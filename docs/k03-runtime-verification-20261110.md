# K-03 runtime and local service verification — 2026-11-10

This is the earlier 725-test/runtime integration record. Shared PostgreSQL rate
admission, four-process tests and the later 786-test no-skip run are recorded in
`k03-shared-rate-limit-verification-20261110.md`. Its previously missing shared
store/runtime injection is now implemented; deployment/acceptance stays open.

This continues `k03-verification-20261110.md`; the earlier 453-test result remains
historical. No clone, branch/worktree switch, commit, push or deployment occurred.
Kiên authorized assistant-run commands and approved the four shared integration
files, then separately approved `hosted_auth/postgres_fresh_login.py` after the
real PostgreSQL recovery regression was demonstrated.

## Implemented changes

- Added SessionMutationApplication and composed POST `/v1/auth/renew` and
  `/v1/auth/logout-all` into the existing experimental upload runtime. Existing
  Session/Problem models are reused. Empty JSON, bounded body/deadline and real
  library CSRF/source guards precede mutations. Early renew and CAS loss emit no
  cookie. Successful rotation preserves context/CSRF. Logout-all derives the actor
  from the server session and clears app cookies after authoritative revocation.
- Callback success now emits session/CSRF cookies only, retaining its browser
  login binding. The legacy J-01 test was updated with approval. A delayed old
  callback versus newer start regression confirms it cannot delete that binding.
- ManagedRuntime now initializes discovery under a total IdP deadline and
  injects one BoundedOidc per runtime. Explicit bounded inflight/backoff values
  are in AuthPolicy and its sample. These per-worker limits do not replace a
  distributed rate counter.
- Added AuthAdmissionApplication over the existing required shared-store gate.
  It HMACs only the trusted server peer address, normalizes IPv4-mapped IPv6,
  ignores identity/cookie/proxy headers, and denies before body/effects. It uses
  draft rate_limited/429 plus bounded Retry-After. It is prepared for injection;
  no production shared store or runtime default is invented.
- Fixed PostgresFreshLogin browser rebinding inside the existing advisory-locked
  transaction. A different live context still blocks rebinding; an old revoked,
  expired, disabled or epoch-invalid context no longer blocks fresh login.
  No SQL schema/migration or lock policy was changed.

Route names, renewal_conflict/409, rate-limit mapping and account-state UI still
require J-01/Quân contract review. Shared fixture/schema files were not changed.

## Offline checks on final code

| Check | Observed result |
| --- | --- |
| All unit test files named test_k03_*, test_j01_* and test_k02_* | **725 passed, 4 skipped in 35.38s**, exit 0 |
| Ruff including C901 on 14 auth files and all K-03 tests | All checks passed, exit 0 |
| Ruff on approved managed_runtime.py/upload_runtime.py and J-01 callback test | All checks passed, exit 0 |
| Strict mypy on those 14 auth source files | No issues, exit 0 |

The four skips are existing `test_k02_contract_schema.py` cases because this
interpreter/isolated target has no jsonschema. A separate `-rs` run confirmed the
reason. They are not K-03 passes. No new library or shared-venv change was made
in this continuation. Counts overlap earlier focused/combined runs; do not add.

The typed auth files are __init__, abuse, admission_http, boundary, callback_http,
csrf, identity, login, oidc, postgres_fresh_login, repositories, session_http,
sessions and settings. This is not strict typing of all existing hosted modules.

Pytest runs used a fresh GUID-named TEMP basetemp, -B, no cache provider and
PYTHONDONTWRITEBYTECODE. Ruff used no-cache without fixes; mypy used a separate
TEMP cache. Successful pytest ran outside the sandbox to avoid its previously
observed temp-directory errors. Tests do not invoke the manual smoke CLIs.

## Real Keycloak, synthetic app storage

Manual CLI: `tests/unit/test_k03_keycloak_account_switch.py`. It reused only the
isolated two-user container `aletheia-j01-two-users-20261110`, Keycloak 26.8.2,
realm j01-two-review at https://localhost:18445. Its initial stopped state was
restored after each check. Other containers/realms were not changed.

Synthetic user password was read from its existing current-user DPAPI file into
the child process environment, then removed; it was never printed. TLS used the
existing test CA with verification enabled. No certificate warning was bypassed.
The app used the actual start/callback/coordinator/Authlib/discovery/bounded-IdP
adapters with a clearly synthetic session/directory authority and in-process ASGI.
There was no API network listener, PostgreSQL storage or real browser in this run.

Initial diagnostics failed to find reset-login because Keycloak's v2 theme uses
a button with a literal location.href action, while the first parser looked for
an anchor. The harness now parses that exact literal pattern without executing
JavaScript and still pins destination scheme/host/port/realm. Earlier failed
attempts are not counted as passing evidence. The final manual run returned:

```text
K03_IDP_EXPLICIT_ACCOUNT_SELECTION=PASS
K03_REAL_KEYCLOAK_ASGI_SWITCH_A_TO_B=PASS
K03_OLD_APP_CONTEXT_REJECTED=PASS
K03_STORAGE_SCOPE=SYNTHETIC_AUTHORITY_NO_DATABASE
K03_LIVE_EXIT=0
```

This explains the previous J-01 report: prompt=login bound a password-only form
to A; posting username B was not account selection. Explicit use of the IdP's
restart control allows selection of B. Product code does not scrape IdP pages
or silently force IdP logout; its SSO effect and browser UX still require review.

The first DPAPI attempt mistakenly used the text SecureString format; that
invocation failed before authentication and was corrected to binary
ProtectedData.Unprotect. Failure diagnostics print only exception type and a line
number, never exception/provider text, forms, callback URLs, cookies or tokens.

## Combined real Keycloak and PostgreSQL follow-up

The same pinned local Keycloak CLI now supports an explicit database mode when
K03_DB_PASSWORD is supplied. This mode creates another validated UUID schema,
loads only the existing experimental DDL, provisions both synthetic fixture
subjects, and injects the real PostgresFreshLogin, encrypted proof,
PostgresIdentityDirectory and PostgresSessionStore into the real coordinator/
start/callback/session ASGI flow. A random ephemeral proof key stays in memory.
It uses real host/DB clocks after a bounded skew probe, verified TLS to the IdP,
and explicit account selection on the IdP. No application network listener or
real browser is used. The final combined run passed with these outputs:

```text
K03_IDP_EXPLICIT_ACCOUNT_SELECTION=PASS
K03_REAL_KEYCLOAK_ASGI_SWITCH_A_TO_B=PASS
K03_OLD_APP_CONTEXT_REJECTED=PASS
K03_STORAGE_SCOPE=REAL_POSTGRES_ISOLATED_SCHEMA
K03_REAL_IDP_AND_DB_SINGLE_ACTIVE_CONTEXT=PASS
K03_COMBINED_DB_OWN_SCHEMA_REMOVED=PASS
K03_LIVE_EXIT=0
```

The schema was removed in finally and the isolated two-user Keycloak container
was returned to its original stopped state. The 11 offline parser/URL/schema
guard tests passed again after this CLI extension, and Ruff/C901 passed for both
manual-smoke files. The 725-test run predates only this test-harness extension
and documentation; production code was unchanged afterwards.

This closes the demonstrated local HTTPX A-to-B path across the actual provider
and database in the stated scope. It does not close real browser/cookie policy,
multi-tab state, a deployed HTTPS API listener, multi-worker/restart, production
migrations, recovery/MFA or J-02 acceptance.

## Real PostgreSQL, synthetic IdP

Manual CLI: `tests/unit/test_k03_postgres_http.py`. It used existing local
PostgreSQL on 127.0.0.1:15432, its existing isolated psycopg target, and a unique
schema `k03_http_test_<UUID>`. The existing experimental session/fresh-login DDL
was installed only there. Cleanup validates that exact name and uses a quoted
SQL Identifier in finally. Every observed run removed its own schema; no
existing J-01 schema was targeted. DB password came from current-user DPAPI,
was never printed and was removed from the child environment afterwards.

This run used the actual PostgresFreshLogin, encrypted login proof,
PostgresIdentityDirectory, PostgresSessionStore, coordinator and HTTP adapters.
The IdP was explicitly synthetic. Host/DB clocks were real, checked within the
30-second local test bound; the test did not substitute an artificial clock.
One session was deliberately aged via test-only SQL to reach the rotation window.
Additional account sessions were seeded directly for isolation checks.

The added fresh-login-after-logout-all case first failed at login start. That
proved the retained browser binding could not recover from its revoked context.
After the separately approved fix, the final manual run returned:

```text
K03_DB_HTTP_RENEW_AND_EARLY_RENEW=PASS
K03_DB_HTTP_LOGOUT_ALL_ACCOUNT_ISOLATION=PASS
K03_DB_HTTP_LOCAL_REVOKE_SECONDS=0.046
K03_DB_CLOCK=REAL_HOST_AND_DATABASE_NO_OVERRIDE
K03_DB_FRESH_LOGIN_AFTER_LOGOUT_ALL=PASS
K03_DB_FRESH_LOGIN_AFTER_EXPIRED_CONTEXT=PASS
K03_DB_LIVE_CONTEXT_REBIND_STILL_REJECTED=PASS
K03_DB_OWN_SCHEMA_REMOVED=PASS
K03_DB_EXIT=0
```

The 0.046 seconds measures one local logout-all request plus sequential denials
for the initial rotated alias, current credential and another session of the
same account on `/v1/session`. Another account's session remained valid. It is
not evidence for deployed multi-process propagation, membership/jobs/downloads,
browser cleanup or the joint five-second target across the product.

## Remaining work

- Real shared admission store/expiry/cardinality/namespace and operator-reviewed
  budgets, then inject AuthAdmissionApplication under the trusted edge and total
  request deadline. Current runtime only enforces per-worker BoundedOidc.
- Supported K-05 migrations, multi-worker/restart/race qualification, and an
  actual HTTPS API server + browser switch journey. Combined real Keycloak and
  PostgreSQL over in-process ASGI now passes as recorded above.
- J-01-approved route/error/fixture semantics, especially renewal CAS loss,
  unprovisioned/disabled/unverified identity and rate limiting. The experimental
  callback still reports account rejections generically.
- Quân's tab/context epochs, cookie/response ordering, cache/draft cleanup and
  browser renewal/logout/recovery tests; membership/job/download invalidation.
- IdP provisioning/recovery/MFA/acr/amr policy and trusted revocation linkage to
  app account epochs, signing/proof-key operations and deployed safe audit.

See `k03-account-recovery-runbook.md` and `k03-auth-outage-handoff.md`. Authoritative
external trackers are untouched. This does not mark all K-03 or J-02 accepted.

## Final local state

Branch/HEAD remained feat/product-api at
2bbcdc2a7637c840babd8b71910306c64072bc4b, with no staged paths. A comparison against
the 326-path WIP snapshot taken before this continuation found 13 intended
existing-path edits and 10 new K-03 files. All other 313 baseline hashes matched,
including pyproject, dashboard WIP and the J-01 progress report. The five shared
files edited were exactly the four approved integration files plus the separately
approved PostgreSQL recovery adapter. No WIP was copied or reverted.

The final Docker status check confirmed the isolated two-user Keycloak container
was exited. All manually created UUID PostgreSQL schemas reported removal. These
are point-in-time observations, not a claim that other chats cannot edit later.
