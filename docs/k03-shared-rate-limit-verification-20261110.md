# K-03 shared PostgreSQL rate limit: verification — 2026-11-10

This is the earlier 786-test store/integration record. Subsequent migration v1,
legacy adoption, least-privilege role, policy rollout/rollback and actual DB
restart checks are in `k03-admission-migration-rollout-20261110.md`, with a later
813-test no-skip regression. Existing environments are still not migrated here.

Kiên requested completion of feasible K-03 work with priority on shared rate
limits and authorized code/tests, preserving WIP with no commit/push. Kiên then
explicitly approved the additive admission schema and edits to managed_runtime,
upload_runtime and the J-01 managed-runtime test double, coordinating other chats.

## Implementation and impact

`PostgresAuthAdmission` implements the existing AuthAdmissionStore protocol over
the existing pool/transaction interface. Global and source counters share one
row lock per deployment/issuer namespace and operation. Time is sampled from
PostgreSQL after acquiring that lock. An allowance is returned only after commit.
No process-local counter, cached grant or rate-limit refund is introduced.

The table has five rows per configured namespace. Each fixed-window JSON map is
bounded by the global allowance, capped at 10,000 source digests. New windows
replace the map; rejected requests cannot create new entries. Policy mismatch,
backward DB time, corrupt answers, missing table and storage failures fail closed.
The fixed-window boundary burst and row/JSON serialization limits are documented
in `k03-shared-rate-limit-runbook.md`; no production capacity claim is made.

The approved managed runtime now requires explicit admission_namespace and a
dedicated shared secret reference. It prepares all five policies transactionally
before creating the worker/IdP resources or publishing readiness, without
spending quota. Its existing readiness/clock predicate also checks the current
table/policy. Schema/policy loss after startup denies readiness and auth; quota
transactions independently check the authority on every operation.

The existing upload composition requires an explicit AuthAdmission and key,
inside EdgeGuard and before auth effects. The HTTP adapter enforces one total
deadline for counter lookup plus inner auth work. It returns safe draft 429/
Retry-After or 503, with no body read/effect on denial. Standard INFO audit records
contain only server request ID, fixed operation/outcome/status/retry-after. A new
test checks actual log fields against injected cookie/token/driver secrets.

DDL is packaged in `hosted_auth/admission_schema.sql`. The application does not
execute CREATE/ALTER/DROP. This work applied it only in isolated test schemas,
not any existing J-01 or production schema. Existing local server configs were
not changed; they need explicit settings/key plus the reviewed migration before
starting the updated managed runtime. No plaintext credential was requested.

## Final offline checks

| Check | Result |
| --- | --- |
| All test_k03_*, test_j01_* and test_k02_* unit files | **786 passed in 58.26s**, exit 0; no skip |
| Ruff/C901 on 15 auth source files and all K-03 test files | All checks passed, exit 0 |
| Ruff on managed_runtime.py, upload_runtime.py, test_j01_managed_runtime.py | All checks passed, exit 0 |
| Strict mypy on 15 auth source files | No issues, exit 0 |

The typed files are __init__, abuse, admission_http, boundary, callback_http,
csrf, identity, login, oidc, postgres_admission, postgres_fresh_login,
repositories, session_http, sessions and settings. This does not claim strict
typing of all existing hosted/dashboard code.

The final 786-test run includes audit privacy, required namespace/key, startup
failure/cleanup, DB readiness propagation, composed route admission, no effect on
denial, safe deadlines, invalid storage facts, lost commit and cancellation.
Earlier scopes overlap: 95 passed initially; a later focused run had 111 passed
and one timing-sensitive deadline test failure. That test combined a 10ms store
delay with a 20ms response-start deadline, so Windows scheduling could expire
before headers were sent. The post-header case now uses an immediate authority
and its own bounded deadline, preserving the no-second-response assertion. The
production timeout behavior was not weakened. The earlier 781-pass run had four
schema skips; the subsequent 785-pass pre-audit run and final 786-pass run have none.
Counts must not be added.

The four historical skips were resolved using exactly the repo's dev pin,
jsonschema[format]==4.25.1, installed as wheels in a fresh TEMP target:

```text
C:\Users\TRUNGK~1\AppData\Local\Temp\aletheia-k03-schema-deps-53f15a92e1a944b2a14d39cf5ece72e8
```

Pip's installation-report.json records resolved artifacts. The target was appended
after existing sys.path entries for the full run, preserving installed dependency
precedence. Neither shared venv nor pyproject was changed. This is a test target,
not a production dependency lock.

Successful pytest used the confirmed K-02 Python 3.12 interpreter, -B,
PYTHONDONTWRITEBYTECODE, no pytest cache, fresh GUID basetemp and -o addopts=''.
Ruff used no-cache without fixes. Mypy used a fresh TEMP cache. The manual CLI
does not run during pytest. The final full run predates only the additional
natural-window manual check and documentation; no logic changed afterwards.

## Real PostgreSQL and independent processes

Manual CLI: `tests/unit/test_k03_rate_limit_live.py`, using PostgreSQL local
127.0.0.1:15432 and the existing isolated psycopg dependency directory. The
password was read from the existing current-user DPAPI file, passed only in the
child environment, never printed, then removed. Every run created a new
k03_rate_test_UUID schema and dropped only that validated/quoted schema in finally.

Two schedules each launched four independent Python processes, with separate
pools and 12 requests per process. One admitted exactly nine requests across
distinct sources (global limit nine); the other admitted exactly three requests
for one shared source (subject limit three). After all workers exited, a new
pool/instance still denied attempts at the exhausted limit. A different operation
had its own budget. Stored map cardinality matched (nine/nine and three/one).

Additional checks used the actual PostgreSQL HTTP gate under EdgeGuard: quotas
produced 429/Retry-After with exactly four total business/body reads, and denied
requests added no effect. Temporarily renaming only the test table produced
false readiness and safe 503 with no extra effect; it was restored in finally.

A held DB row lock forced a quota timeout; cancellation rolled back without
spending a slot. Policy drift failed rather than resetting quota. A deliberately
future last_seen rejected backwards time, preserving the counter. An explicitly
historical test bucket was replaced with one fresh map. A separate two-second
policy then reached the next window through REAL DB elapsed time, with no SQL
reset or clock substitution, and admitted the source again.

Final manual output:

```text
K03_RATE_DB_FOUR_PROCESSES_GLOBAL_AND_SUBJECT=PASS
K03_RATE_DB_NEW_PROCESS_POOL_PRESERVES_QUOTA=PASS
K03_RATE_DB_HTTP_DENIAL_ZERO_EFFECT=PASS
K03_RATE_DB_HTTP_MISSING_TABLE_FAILS_CLOSED=PASS
K03_RATE_DB_CANCEL_POLICY_CLOCK_AND_WINDOW_RESET=PASS
K03_RATE_DB_NATURAL_WINDOW_ROLLOVER=PASS
K03_RATE_DB_ROW_AND_SOURCE_CARDINALITY_BOUNDED=PASS
K03_RATE_DB_OWN_SCHEMA_REMOVED=PASS
```

There were only 25 rows for the five test namespaces, not a row per attempted
source/window. No existing schema was modified; no IdP/public service was called.
This tests repository sharing across processes, not a deployed multi-worker
ASGI topology, database crash/restart/failover, migration rollout, production
load, real proxy/NAT sizing or external logging pipeline.

## Remaining acceptance

- K-05 review/adoption of the additive table, least-privilege role, migration,
  restore/retention and coordinated policy changes.
- Operator-reviewed namespace, shared key, per-source/global budgets and load
  evidence. Fixed windows, NAT fairness and per-operation serialization are real
  tradeoffs, not hidden completed capacity work.
- J-01 acceptance of draft 429/Retry-After and renewal/account-error semantics;
  Quân's rate-limited/recovery/browser multi-tab behavior.
- IdP recovery/MFA/assurance and app revocation integration, production TLS/key
  operations, deployed audit redaction and the joint J-02 journey.

The earlier Keycloak/PostgreSQL A-to-B smoke is recorded separately in
`k03-runtime-verification-20261110.md`; it was not rerun or broadened by the rate
tests. No authoritative external acceptance tracker is marked complete here.

## Final workspace check

Branch/HEAD stayed feat/product-api at
2bbcdc2a7637c840babd8b71910306c64072bc4b, with no staged paths. The initial snapshot
covered 336 WIP paths. Final comparison found 13 intended existing-path edits and
eight new K-03 files; the other 323 hashes matched, including pyproject, dashboard
WIP and the J-01 progress report. The only shared-file edits in this continuation
were the three explicitly approved runtime/test paths. No WIP was copied,
reverted, committed or pushed.

Import assertions confirmed the new auth source in this API checkout and
jsonschema 4.25.1 in the dedicated schema target; they passed. Final Ruff/C901
also passed after the additional natural-boundary smoke helper. The DDL/test
schemas were isolated and every observed manual DB run reported removal of its
own schema. Existing J-01/operator runtime configs were not edited or migrated.
