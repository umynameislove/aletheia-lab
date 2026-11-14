# K-03 admission migration and isolated rollout evidence — 2026-11-10

Later update: wheel/sdist package-data and installed CLI PostgreSQL rollout
passed after Kiên approved the narrow pyproject change. See
`k03-migration-package-verification-20261110.md` for the 821-test regression and
artifact evidence. Earlier packaging limits below describe this prior slice.

Kiên requested migration/rollout checks in a separate local environment and
authorized code/tests without commit/push. This continuation adds only K-03
migration source, tests and documentation. No shared runtime or J-01 schema/config
was edited by this work. Production migration ownership remains with K-05.

## Implemented artifacts

- admission_migration.py: one explicit v1 migration, canonical DDL checksum and
  operator-owned version ledger; strict schema/data/behavior checks; idempotent
  creation/adoption; transactional quiet-window compare-and-set for all five
  operation policies.
- admission_migrate_cli.py: explicit schema, credential from a named environment
  variable, bounded connection/statement/lock/total timeouts, commit before success
  output and fixed FAILED_OR_UNKNOWN on errors. No HTTP/startup DDL or down command.
- test_k03_admission_migration.py: offline boundary and CLI privacy/commit tests.
- test_k03_admission_rollout_live.py: opt-in local PostgreSQL container exercise;
  pytest only runs its guard against restarting a non-owned container.

The original admission_schema.sql was not changed. The migration verifies
columns/defaults/identity/generated/collation, constraints, indexes/operator
classes, RLS/policies, triggers/rules/inheritance and coherent stored counters.
Unknown versions/checksums, incompatible legacy behavior and missing data tables
after an applied version are rejected rather than rebuilt/reset.

The API table layout is unchanged, so a valid unversioned table can be adopted
without clearing active quotas. The ledger is not readable/writable by the API
role in the tested grant model. Runtime readiness continues checking the counter
table and policy, while the explicit operator tool checks the version ledger.

## Final offline results

| Check | Observed result |
| --- | --- |
| All current unit test files named test_k03_*, test_j01_* and test_k02_* | **813 passed in 45.58s**, exit 0; no skip |
| Ruff including C901 on 17 auth source files and all K-03 tests | All checks passed, exit 0 |
| Strict mypy on 17 auth source files | No issues, exit 0 |

The source scope is the previous fifteen auth files plus admission_migration and
admission_migrate_cli. Mypy additionally used the existing isolated psycopg target
to resolve the CLI's lazy driver import. No library, pyproject or shared venv was
changed. The same isolated jsonschema 4.25.1 target was appended to sys.path for
the full pytest run, so the earlier four schema skips remain resolved.

Runs used -B/PYTHONDONTWRITEBYTECODE, no pytest cache, fresh GUID TEMP basetemp,
Ruff no-cache without fixes and a separate mypy TEMP cache. Manual CLI checks
do not run during pytest. Counts overlap the prior 786-test slice and earlier
focused/full runs; they must not be added. The current combined selection also
includes concurrent K-02/J-01 unit additions, not just this migration's tests.

## Dedicated local environment

Each manual attempt created a new container named aletheia-k03-rollout-UUID,
using cached image postgres:16 with
--pull=never, a new anonymous data volume and an ephemeral loopback-only host
port. It did not use the J-01 PostgreSQL container, port 15432, schema or password.
The fixture database/admin user was k03_rollout. A random temporary password was
passed through environment, never printed or placed in a command argument/file.

The Python harness permits restart only for the exact task UUID container name.
The PowerShell finally block validates the owned name and removes that container
with its anonymous volume, whether a test passes or fails. API fixture roles and
schemas exist only in that disposable container. Final Docker inventory showed
no remaining container with the K-03 rollout label. No IdP/public service was used.

## Passing real-DB cases

1. Fresh migration reports CREATED; two concurrent CLI invocations subsequently
   report UNCHANGED. The advisory transaction lock prevents conflicting installs.
2. The original unversioned DDL is seeded with real admission counters, then
   adopted. Every row matches the pre-migration snapshot before and after rerun.
3. Altered checksum, future ledger version, missing versioned table, inconsistent
   counter map, RLS, an extra index, inheritance and an update-suppressing trigger
   are rejected; each test transaction rolls back to the original snapshot.
4. An injected exception after fresh migration rolls back the schema/objects;
   an incompatible legacy constraint is rejected without leaving a ledger.
5. A policy change is blocked while an admitted old window is active. After
   real DB time reaches the boundary, all five policies change atomically.
   The old-policy admission adapter becomes unready and fails safely; it cannot
   overwrite the new values. A rollback is blocked while a new window is active,
   then succeeds at a later quiet boundary. This is a local adapter/configuration
   test, not a deployed mixed-version ASGI worker rollout.
6. A separate LOGIN role gets only schema USAGE and table SELECT/INSERT/UPDATE.
   It prepares and spends quota; CREATE TABLE, ALTER TABLE and ledger SELECT
   are denied by PostgreSQL. This uses actual role credentials, not SET ROLE on
   an administrator connection.
7. All client pools/connections close, the dedicated DB container actually
   restarts, and a new pool/CLI reconnects. Migration stays UNCHANGED; exhausted
   quota remains exhausted, readiness succeeds and the API grants still work.

Final manual output on the completed code:

```text
K03_MIGRATION_FRESH_CONCURRENT_RERUN_AND_LEGACY_PRESERVE=PASS
K03_MIGRATION_TAMPER_FUTURE_VERSION_AND_MISSING_TABLE_REJECTED=PASS
K03_MIGRATION_ATOMIC_ROLLBACK_AND_SCHEMA_DRIFT=PASS
K03_ROLLOUT_ACTIVE_WINDOW_BLOCKED_OLD_WORKER_DENIED_AND_SAFE_ROLLBACK=PASS
K03_ROLLOUT_API_ROLE_DML_WITHOUT_SCHEMA_OR_LEDGER_CONTROL=PASS
K03_ROLLOUT_EPHEMERAL_PORT_CHANGED_AFTER_RESTART=TRUE
K03_ROLLOUT_DATABASE_RESTART_PRESERVES_LEDGER_QUOTA_AND_API_GRANTS=PASS
K03_MIGRATION_AND_LOCAL_ROLLOUT=PASS
K03_OWN_ROLLOUT_CONTAINER_AND_VOLUME_REMOVED=PASS
K03_ROLLOUT_EXIT=0
```

The first attempt failed reconnecting after restart. The next attempt confirmed
Docker reassigned the automatically published host port. The harness now reads
the same owned container's current binding, accepts only 127.0.0.1 and continues
against its private fixture database; it never falls back to port 15432 or a
J-01 target. Subsequent final checks passed. Failed attempts were also cleaned
up and are not counted as passing restart evidence.

## Remaining work and evidence boundary

This qualifies local v1 migration/adoption, policy change/rollback, API grant model
and restart persistence in the stated fixture. It does not qualify production
restore/failover, load/capacity, a live mixed-version API deployment, packaging of
the SQL in a distributable wheel, operator key rotation, MFA, browser tabs or J-02.
The single-file v1 implementation intentionally refuses unknown future versions;
K-05 must review any next migration or adoption into its release workflow.

See k03-admission-migration-runbook.md for exact operator commands, drain/window
requirements, rollback limits and unknown-commit handling. External authoritative
trackers and existing local/runtime configs were not changed.

## Concurrent work

During this continuation other chats changed hosted composition/managed/upload
runtime, logout/discovery and their tests/reports, and added K-02 lifecycle and
J-01 IdP-logout tests. Those changes were read and preserved, not authored or
reverted here. Their separate live IdP/logout claims are not established by these
migration checks. The test result is a point-in-time observation of shared local
WIP, not a claim that other chats stopped editing it.
