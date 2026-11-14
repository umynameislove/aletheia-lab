# K-03 local implementation and coordination

Current 2026-11-11: K03 resumed to finish independent auth/session/OIDC/CSRF/
shared-admission and local recovery/J02 work. The extended real A/B retained-OTP
recovery, actual trusted operator CLI and private-PG J02 compute/rotation/revoke/
replay proofs now passed with unchanged run inputs and verified own-resource
cleanup. Production IdP/lost-factor policy and Quân hosted browser/joint acceptance
remain external conditions. No UI/consumer or shared Git state was changed.

Use [k03-completion-20261111.md](k03-completion-20261111.md) and
[k03-coverage-20261111.md](k03-coverage-20261111.md) for the current manifest,
test/package receipts, honest transient status and remaining receiver work.
K03 is sole auth/test/config/docs writer; K04 owns runtime integration and the
canonical admin CLI. The latest K04 readiness caller request is handled only
in K03's test fixture, leaving production ownership unchanged. Shared tracker
changes are provided as an exact seven-row proposal for the coordinator.

## Historical checkpoints below

The earlier freeze/ownership constraints were superseded by the explicit
K03/K04 resume instruction. Historical test counts refer only to their recorded
snapshots and are not added together or relabelled as a new complete-WIP pass.


Historical 2026-11-11 coordination checkpoint: **K03 auth/V7/captured-context
handoff for J01 is frozen**. 529 focused offline tests, Ruff/C901 and strict
mypy27 passed. K02 owns shared worker/runtime callers; K04–K07 remain paused.
Long recovery/J02 work is preserved without a pass claim. Exact hashes,
dependency status and remaining caller work:
[k03-j01-auth-freeze-20261111.md](k03-j01-auth-freeze-20261111.md).

Final coordinated checkpoint: **1587 selected unit tests passed**, real PG HTTP/
J02 worker harnesses passed on an unchanged used-source scope, Ruff/C901 and
strict mypy27 passed. Durable receipts and the one out-of-scope K06 inventory
change are recorded in the recovery report. K02 takes the next P19 worker hook;
later shared WIP is not covered by this checkpoint. No Git mutation performed.

Latest recovery/MFA checkpoint: **retained-OTP reset passed with real isolated
Keycloak, SMTP/Mailpit, Authlib and PostgreSQL**, unchanged imported-source hashes
and resource cleanup. Kiên approved this local fixture policy; production/factor-
loss policy, trusted IdP→app epoch bridge and hosted browser acceptance remain open.
See [k03-recovery-mfa-verification-20261110.md](k03-recovery-mfa-verification-20261110.md).
Typed callback outcomes and worker startup binding are coordinated with J01/K02/K04.
Earlier checkpoints below retain their original scopes.

Latest approved worker fix: **normal rotation no longer aborts valid-context
jobs**. K04 repository/RBAC remains canonical; worker locks per account and API
cookies stay strict. **1214 unit +16 real-DB K04 caller integration passed**;
Ruff/C901 and strict mypy passed. See
[k03-j02-rotation-verification-20261110.md](k03-j02-rotation-verification-20261110.md)
for exact WIP manifests/limits. Earlier checkpoints below are historical.


Previous K-03/J-02 checkpoint: **945 unit tests passed**, real PostgreSQL role/
scope/revocation harness passed, and two-tab Chrome demo component checks passed.
A real job/session-rotation incompatibility was reproduced; the worker-only fix
requires the requested shared-file/policy approval. No hosted browser/MFA/joint
acceptance is claimed. See
[k03-j02-execution-20261110.md](k03-j02-execution-20261110.md) for exact evidence
and the J-02/A01–A32 matrix. The earlier K-03-only state follows.


Previous K-03-only continuation, 2026-11-10: **independent work with available inputs is
implemented and checked locally**. Callback policy/token rejection mapping,
optional exact signed ACR enforcement, retained-stale-cookie fresh-login recovery
and DB account-policy/epoch checks are complete in the approved paths.
Final regression: **912 passed in 70.40s, no skip** across 62 current K-03/J-01/K-02
unit files; Ruff+C901 and strict mypy passed on 17 auth source files.
Real PostgreSQL HTTP smoke passed and removed its UUID schema; final local
logout-all/denial measurement was 0.063s. OIDC was synthetic in that DB run.

See [k03-local-completion-20261110.md](k03-local-completion-20261110.md) for exact
code/test evidence, file hashes and the six-step dependency/acceptance matrix.
Kiên confirmed no new approved IdP/MFA/recovery policy or hosted frontend input.
MFA/recovery, browser/multitab, deployment and joint K-03/J-02 acceptance remain
open. External assignment trackers were not edited.

Earlier shared admission, four-process quota, migration/adoption/policy
rollout/rollback, API grants and DB restart evidence remains in
k03-shared-rate-limit-verification-20261110.md and
k03-admission-migration-rollout-20261110.md. The SQL package-data change and
wheel/sdist/installed CLI results are in
k03-migration-package-verification-20261110.md. Those are previous scoped checks,
not a released build of today's complete WIP. No migration/quota code changed
in this continuation; no shared schema was automatically migrated.

The sections below retain historical implementation slices and original input
states. Use the linked current matrix for today's state and remaining inputs.

## Local source and ownership

Operator-supplied Git output identifies the existing API worktree as
`aletheia-j01-api`, branch `feat/product-api`, HEAD
`2bbcdc2a7637c840babd8b71910306c64072bc4b`, plus local K-02/J-01 WIP.
Its Git common directory belongs to `aletheia-kien-code`, whose `main` is at
`6d1ba1469b5231971095b63d4f8069f542cc7993` with two untracked K-01 reports.
Neither worktree had staged changes in that supplied output. These are observed
local states, not a claim about current GitHub branches.

Kiên approved adding only these K-03 paths before any edits:

- `src/aletheia_lab/hosted_auth/**`
- `tests/unit/test_k03_*.py`
- `docs/k03-*.md`
- `configs/hosted/k03-auth.example.toml`

Existing K-02/J-01 work outside the explicitly approved integration edits and
`pyproject.toml` is preserved. Auth integration changes to shared files must first
be itemized and coordinated. No clone, branch switch,
new worktree, WIP copy, commit, push or deployment was performed. The original
operator-run and later authorized assistant-run isolated dependency installations
are recorded separately; the shared venv was not modified.
Before 2026-11-10, Kiên ran commands and supplied output. On 2026-11-10, Kiên
explicitly authorized the assistant to run commands and code, with no commit or
push. The command evidence below that predates this change remains historical.
A hash snapshot of 143 existing API WIP files was retained in tool memory
before K-03 edits for content comparisons, not as a file backup. That tool-memory
snapshot is no longer available; no fresh hash comparison is claimed here.
For 2026-11-10, a separate 322-path starting snapshot and final comparison are
recorded in `k03-verification-20261110.md`. They do not compare against the lost
143-path snapshot or claim that concurrent chats stopped changing their files.
Kiên subsequently approved callback_http.py, test_j01_callback_http.py,
hosted/upload_runtime.py and hosted/managed_runtime.py for the listed integration,
then separately approved postgres_fresh_login.py for the demonstrated recovery
bug. Those exact changes are recorded in `k03-runtime-integration-review.md`.
At that integration stage pyproject and the J-01 report were untouched. A later explicit approval added only the SQL package-data rule to pyproject; the J-01 report remains outside K-03 edits.
For shared rate limiting, Kiên later approved the additive admission schema,
managed_runtime.py/upload_runtime.py and test_j01_managed_runtime.py. The schema
was packaged for K-05 review and applied only in UUID test schemas; pyproject,
existing external configs and J-01 progress report were preserved.

## First slice

- `settings.py` loads explicit bounded policy TOML and imports the existing K-02
  `RuntimeSettings`. Issuer, client, callback and database settings have one
  source; no second runtime identity config is introduced.
- Return-to is an exact allowlist of literal app paths. Absolute/network URLs,
  encoded paths, dot segments, controls, query/fragment and prefix matches are
  rejected. Real frontend destinations must be agreed with Quân.
- Callback parsing rejects malformed/duplicate/oversized query fields, missing
  state, conflicting code/error and wrong response issuer. The selected IdP must
  support the proposed required authorization-response issuer parameter, or the
  policy and mix-up defenses need explicit review. ID Token issuer/signature
  validation is a separate mandatory step.
- Callback codes and state use masked values in object representations. Provider
  error text and URLs are discarded. Exceptions contain fixed safe messages.
  This does not qualify deployment access logs or telemetry redaction.
- Opaque cookie helpers use 256-bit random values, `__Host-` names, `Secure`,
  `HttpOnly`, `SameSite=Lax`, `Path=/`, no Domain, bounded lifetime and the same
  scope when cleared. Duplicate/ambiguous protected request cookies are rejected.
  Cookie headers themselves are credentials and must never be logged.
- The proposed flow is Authorization Code + PKCE S256 with query-mode GET
  callback. This slice requires HTTPS for real auth even in local mode. There
  is no insecure cookie fallback. In-process synthetic tests need no HTTPS.

These helpers are not wired into HTTP routes. They do not verify OIDC tokens,
consume state, prevent callback replay, create/resolve server sessions, enforce
CSRF, log out/revoke a session, provision identities or prove a timing target.
No accepted J-01 schema, fixture or public error-code table was changed.

## First operator-supplied verification

Kiên ran the two first-slice test files with the confirmed Python 3.12.15
interpreter. The import check resolved both auth modules to this API worktree.
Results supplied on 2026-11-09:

- Pytest: **101 passed, 1 failed in 4.63s**, exit 1.
- Ruff including C901: `All checks passed!`, exit 0.
- Strict mypy: no issues in three source files, exit 0.

The failure was the oversized-policy-file branch: it raised the fixed safe
message but did not explicitly suppress exception chaining, unlike the parser
and validation error paths. In that run no sensitive context was shown.
The branch now uses `raise AuthConfigurationRejected() from None`.
A further regression test creates an actual sensitive prior exception and
checks that formatting the resulting rejection does not expose it or policy
content. The limit and rejection behavior remain unchanged.

Kiên then supplied a separate rerun after the fix: **103 passed in 2.01s**,
pytest exit 0, Ruff including C901 exit 0, and strict mypy with no issues in
three source files, exit 0. This replaces the earlier pending verification
for that first slice. Counts overlap and are not additive. No result here
establishes OIDC, production server sessions, browser behavior or shared acceptance.

## Second slice: session/account adapters and supplied offline verification

`repositories.py` defines internal immutable snapshots and draft ports only.
`ServerSessions` implements the existing K-02 `SessionRepository` structurally;
it can be injected into `ReadOnlyHostedBackend` without changing either file.
It also exposes internal renew/logout/logout-all calls over the required atomic
store. `AccountResolver` resolves only a fully validated OIDC identity to a
current pre-provisioned account using exact issuer/subject lookup.

The code supplies bounded deadlines, safe storage outages (compatible with the
existing K-02 `service_unavailable` mapping), current account/epoch checks,
effective idle/absolute expiry clamping and rotation result checks. GET reads
never slide idle expiry. Early renewal does not rotate, extend expiry or return
a cookie. Successful renewal preserves context/CSRF/issuance/absolute bound and
returns one new opaque credential; a CAS loser receives no new credential.
`RenewalConflict` and `IdentityRejected.reason` are internal outcomes whose
HTTP semantics still require J-01 review. No new shared error table is published.

No default store/directory, SQL/migration, HTTP mutation route, automatic
provisioning, invitation, IdP call, CSRF enforcement or session issuance is
introduced. The `ValidatedIdentity` type is not proof of authentication; only
the future maintained-library OIDC adapter may construct it for real requests.
Prior account lookup must be rechecked at the future atomic login commit.

New tests use a synthetic atomic store/directory, controlled race schedules and
in-process K-02 wiring. They cover bad/unknown credentials, expired/revoked/
rotated sessions, account disable/epoch change, duplicate renewal, both logout
race orders, another account staying valid, lost rotation reply, corrupt storage
answers, exact identity binding, disabled/unprovisioned accounts, outages and
cancellation. Kiên supplied their combined run with the first-slice tests,
K-02 read/application tests and J-01 Session/Capabilities model tests:
**228 passed in 7.31s**, pytest exit 0; Ruff including C901 exit 0; strict mypy
reported no issues in six source files, exit 0. These counts overlap the earlier
103-test run and must not be added together. They do not establish DB atomicity,
fixation protection at login, real shared state, measured five-second propagation,
browser tab cleanup, recovery/MFA or the complete J-02 journey.

See `k03-session-store-handoff.md` for the explicit K-02/K-04/K-05 integration
requirements. No existing K-02/J-01 file or shared fixture was edited.

## Third slice: maintained-library OIDC adapter, supplied offline verification

`oidc.py` now uses Authlib for Authorization Code + PKCE S256 and CodeIDToken
claims validation, joserfc for signatures, and HTTPX2 for bounded token exchange.
It accepts explicitly trusted provider endpoints/public keys with short expiry;
there is no client-controlled key source, discovery URL or redirect URI. Generated
state/nonce/verifier are masked in object representations; token/access/refresh
credentials are not returned as local identity/session data or retained in a store.

The new synthetic-library tests require those actual libraries (no import skip).
They generate ephemeral RSA keys and use MockTransport only; no IdP/public network
traffic is scheduled by the tests. Kiên supplied successful pip `--target`
installation output, exit 0, for Authlib 1.9.5, joserfc 1.8.0 and httpx2 2.16.0
plus their resolved dependencies in a unique temporary directory. Details are
recorded in `k03-oidc-verification.md`. Kiên then supplied verified import paths
for the worktree's OIDC module and the target's Authlib/JOSE/HTTPX2/cryptography.
The first combined run reported **276 passed, 2 failed in 9.82s**, pytest exit 1;
Ruff exit 0; mypy reported two errors in OIDC auth_time comparisons, exit 1.
Both failing tests reached a state mismatch while generating an authorization
URL. The fix passes the saved state explicitly to Authlib's URL builder and
narrows auth_time using an explicit numeric guard that still rejects booleans.
New signed-token cases cover wrong auth_time types and future timestamps.
Kiên then supplied a focused OIDC rerun after those edits: **54 passed in 2.32s**,
pytest exit 0; Ruff including C901 exit 0; strict mypy reported no issues in
seven source files, exit 0. This qualifies the corrected library adapter with
synthetic signatures/mock HTTP. It does not turn the earlier failed combined run
into a passing combined run. Test scopes overlap; do not add their counts.
Existing K-02 venv/package files and `pyproject.toml` remain untouched.

This is a protocol/crypto adapter, not a completed login route. Qualification of
the browser-bound single-use transaction claim, atomic issuance/account-switch commit,
trusted discovery/key-refresh loader, real IdP/client setup and HTTP/CSRF/admission
integration are still required. The adapter cannot prevent replay of a trusted
attempt by itself, and it is not exposed as an endpoint. See
`k03-oidc-verification.md` for dependencies, verification scope and open inputs.

## Fourth slice: browser-bound login coordinator, supplied offline verification

`login.py` adds LoginCoordinator and internal BeginLogin/CommitLogin/
LoginTransactionStore ports. It persists browser/state digests with server-side
nonce/verifier and pinned issuer/client/redirect metadata, requires an atomic
one-use claim before exchange, resolves only provisioned identity, and requires
an atomic current-account/intent check before returning a fresh credential.
There is no process-local intent cache or production store implementation.

The proposed switch policy retires only the previous browser context. An intent
epoch fences superseded callbacks. Commit must recheck account enabled/verified
status and epoch, the prior context and an account revocation watermark, including
logout-all occurring before the identity lookup observes a new epoch. These are
draft K-05 transaction requirements, not implemented database guarantees or a
change to the shared wire contract.

CSRF token creation is an explicit required dependency; coordinator tests supply
a plainly synthetic factory. The subsequent CSRF slice supplies a signed factory
for integration, but is not covered by the coordinator verification below.
The coordinator has an overall deadline bounded by existing K-02 settings and
does not retry a spent callback after timeout/error/cancellation or lost commit
reply. Storage output is checked before credentials are returned. Actual unknown
commit outcomes need a fresh explicit login, not blind callback replay.

New controlled-store tests cover binding/replay/concurrent callbacks, expiry
before/after exchange, superseded intents, lookup/commit races, account switch,
fixation inputs, lost commit reply, corrupt repository answers and cancellation.
Additional cases combine the coordinator with real Authlib/JOSE and mock HTTP.
Kiên supplied the combined six K-03 test files plus selected K-02 read/backend
application and J-01 Session/Capabilities regression files: **328 passed in
14.29s**, pytest exit 0; Ruff including C901 exit 0; strict mypy reported no
issues in eight source files, exit 0. Counts overlap previous runs and are not
additive. This verifies synthetic authority schedules and real crypto over mock
HTTP, not production storage or HTTP/browser integration.

K-05 still owns the actual schema/transactions/directory; K-02 owns HTTP/resource
integration. See `k03-login-transaction-handoff.md`. Real DB/IdP/HTTPS, CSRF,
admission, browser/multi-tab, recovery/MFA and measured revoke acceptance remain
open. No existing K-02/J-01 file or authoritative assignment tracker was edited.

## Fifth slice: library CSRF and request source guard, offline verification

`csrf.py` supplies a signed, context-bound proof factory and ASGI guard over
asgi-csrf/itsdangerous. It preserves draft X-CSRF-Token and existing Problem
models, adds an internal host-only CSRF cookie proposal, validates HTTPS Origin/
Fetch Metadata and JSON media, and requires current server binding before
business effects. Missing cookie, forged Bearer, duplicate headers/cookies,
method override, invalid context/signature and form-body fallback cannot exempt
cookie-auth mutation checks. No request body is consumed by CSRF verification.

`test_k03_csrf_boundary.py` uses real library imports and synthetic authority/
ASGI I/O, with effect/body-read counters and timeout/cancellation cases. These
tests and the policy passed the subsequent checks: 83 focused CSRF tests and
the final 453-test combined regression. Ruff and strict mypy passed in the
recorded K-03 scope. The previous 328-test result predates this group.
This K-03 group added no auth route, production bootstrap/binding repository,
shared fixture/schema or existing K-02/J-01 file was changed. See
`k03-csrf-handoff.md` for direct pins, composition and outstanding decisions.

## Sixth slice: bounded auth abuse/outage adapters, offline verification

`abuse.py` adds explicit shared-store admission over atomic global/subject
budgets and an OIDC wrapper with bounded concurrency, total deadlines,
exponential backoff/jitter and one recovery probe. It never retries a spent
callback, queues requests, generates success on failure or uses stale identity.
Only a verified exchange clears provider failure history; local URL generation
cannot prove token-endpoint recovery. Counter storage remains a K-05 port,
with no process-local production rate-limit fallback.

The final combined run passed **453 tests in 7.69s**, exit 0, covering all eight
K-03 test files and eight selected K-02/J-01 regression files. Ruff including
C901 passed; strict mypy reported no issues in ten K-03-owned source files.
Tests include a mock-HTTP outage storm over J-01's actual discovery component,
two admission instances over a synthetic authority and zero session creation
after a failed, spent callback. There is no real service traffic in this run.
See `k03-auth-outage-handoff.md` for retry/recovery and integration requirements.

The current J-01 tree has grown experimental PostgreSQL, discovery, callback,
login-start, logout and managed runtime code. Its progress report describes local
Keycloak/DB smoke results; those were read, not rerun here. The previous lack of
local environment was a historical observation, not a claim about today's tree.
No approved shared staging, production migration or browser acceptance follows
from these additions. Callback login-cookie retention also needs J-01/Quân
coordination, as recorded in `k03-verification-20261110.md`.
The concurrent J-01 report also records a real loopback HTTPS two-account check:
fresh logins worked, but in-session A-to-B switching failed and returned A.
That reported failure was an explicit K-03.05 integration blocker in the earlier
run. The subsequent real-Keycloak smoke passed by explicitly choosing the other
account through its reset-login control; full browser/DB integration remains open.
The callback binding deletion and PostgreSQL post-logout recovery were also fixed
and checked later. See `k03-runtime-verification-20261110.md`.

## Historical six-step implementation and evidence plan

The following table records earlier starting points. The current matrix and
remaining conditions are in k03-local-completion-20261110.md.

| Step | Local starting point | Implementation/output still needed | Required checks | Dependencies |
| --- | --- | --- | --- | --- |
| K-03.01 | Callback/input, Authlib adapter and login coordinator qualified offline; J-01 has experimental HTTP/discovery/DB code | Coordinate and qualify real shared transaction/runtime integration and deployment | Invalid/replayed/expired callbacks and signed tokens, issuer/audience/nonce/skew, no session on rejection | Approved IdP/client/environment, K-05 store/migration, HTTPS |
| K-03.02 | AccountResolver and synthetic login commit qualified offline; no provisioner | Coordinate K-04 provisioning/invitation and IdP recovery guidance | No email-based linking/auto-owner; disabled/recovery/MFA scenarios | Provisioning decision, K-04/invitation ownership, IdP recovery policy |
| K-03.03 | Renew/logout-all runtime routes and local PostgreSQL HTTP smoke pass; earlier synthetic race tests pass | Supported K-05 migration, multi-worker/restart and deployment qualification | Fixation/reuse, concurrent renew/logout-all, persistence/failure tests | K-05 schema/repository and process topology |
| K-03.04 | Draft X-CSRF-Token retained; library CSRF/source guard qualified offline | Coordinate real binding/bootstrap, cookie and HTTP/browser integration | Cross-site/missing/wrong proof and bypass schedules produce zero effect | Cookie/route policy, J-01 consumer and browser review |
| K-03.05 | Real Keycloak + PostgreSQL A-to-B switch passes over ASGI; local PostgreSQL session denial measured at 0.046s | Actual browser/HTTPS API, client handoff and membership/job/download propagation | Tab races, stale response cleanup, deployed five-second target | Quân browser cleanup/epoch; K-04/K-15/job/download integration |
| K-03.06 | Gate/store wired; local migration/adoption, installed-wheel policy rollout/rollback, API role and DB restart pass; four-process quota/audit checks pass | K-05 production adoption and release/fresh-install validation, operator key/budget/load/log review and accepted client semantics | Real deployment/load/failover and browser recovery beyond isolated local evidence | K-05/operator production rollout and J-01/Quân acceptance |

All six joint acceptance states remain **open**; offline evidence is recorded
above, without editing authoritative external trackers. Existing K-01/K-02/J-01 test
results cover their recorded versions and scopes only. A parser test cannot close
K-03.01, a cookie helper cannot close K-03.03, and backend-only tests cannot close
the J-02 browser journey or establish revoke propagation within five seconds.

## Architecture proposal, not an approved IdP choice

Use Authlib for OIDC over the existing ASGI architecture, and a shared server
session store. The maintained Authlib client supports Authorization Code with
PKCE; do not replace it with handwritten OAuth or password handling.

The official Authlib changelog currently documents 1.9.5 and the move of the
async integration to `httpx2`, with a deprecated `httpx` fallback. Check the actual
K-02 interpreter and installed packages before selecting/pinning the hosted auth
dependencies. Existing application dependencies must not be upgraded implicitly.
The initial proposal preceded the operator-run isolated installation and offline
qualification recorded above. No shared-venv upgrade or production lock was made.

References checked on 2026-11-09:

- [Authlib OAuth HTTP clients](https://docs.authlib.org/en/stable/oauth2/client/http/index.html)
- [Authlib changelog](https://docs.authlib.org/en/stable/upgrades/changelog.html)
- [Starlette middleware](https://starlette.dev/middleware/): the built-in session
  middleware uses signed client cookies; it does not satisfy the shared server
  session requirement by itself.

Keycloak self-hosting remains a proposal, not a decision or a configured service.
IdP selection/client registration/OIDC/recovery policy belong to K-03. K-02
provides runtime/API/resource-lifecycle/settings integration; HTTPS/DB/staging
also involve the data/deployment work and the operator. Missing environment does
not transfer the OIDC implementation to K-02 or prevent independent offline work.

## K-02 handoff received on 2026-11-09

Kiên supplied K-02's read-only handoff. K-02 performed no commands, connection
checks or edits for that handoff. It identifies the interpreter used in prior
successful K-02 checks as:

```text
C:\Users\Trung Kiên\Documents\ChatGPT\Đồ Án\aletheia-k01-baseline-6d887dfb1fbb4aaba69613a8d61889fc\metric-review-venv\Scripts\python.exe
```

Kiên then supplied a successful metadata-only run, exit 0, using that executable:
Python 3.12.15 (Windows AMD64), Pydantic 2.14.2, pytest 9.2.0, Ruff 0.17.3,
mypy 2.4.1 and httpx 0.28.1. The queried distributions jsonschema, uvicorn,
psycopg, psycopg-pool, authlib, joserfc, cryptography, httpx2 and starlette were
not installed in this interpreter at that time. This is distribution metadata,
not a dependency compatibility check or a K-03 test result. It also does not
contradict historical J-01 checks using a separate validator dependency directory.

The first metadata attempt failed with SyntaxError before inspecting packages
because PowerShell changed quoting in the Python `-c` argument. Passing the
script through stdin corrected that invocation; no package change was involved.
Prior passing K-02 checks are not a K-03 run. Subsequent operator-supplied K-03 results are recorded above.

The adapter insertion point is K-02 `SessionRepository.resolve_cookie(cookie)`
returning `SessionRecord`. It is injected into `ReadOnlyHostedBackend`, whose
resource lifetime is supplied by `ResourceFactory`. There is no production
factory yet. Existing routing is read-only; login/callback/renew/logout routing,
readiness and cleanup need coordinated integration owned by K-02.

Ownership confirmed in that handoff:

- K-03: OIDC/client policy, account policy, session lifecycle/adapter, auth route
  requirements, cookie/CSRF/outage behavior and test doubles in the agreed paths.
- K-02: `hosted/**` and K-02 tests/config/docs; runtime composition, routing
  integration, edge settings, real resource readiness and shutdown.
- K-05: the authoritative shared PostgreSQL schema/migrations/indexes and real
  repository, including sessions. K-03 supplies atomic operation/invariant
  requirements rather than introducing another production session schema/store.
- K-04: membership/workspace/authorization source and its consistent lookup.
- J-01: shared models, wire/error/route review, schema/exporters and fixtures.
- K-18/operator: permitted service environment, deployment/migration strategy,
  TLS/secrets, topology, backup/restore and operational evidence.

Pool wrapper tests used injected doubles. Real Psycopg/DB/TLS/transactions,
multi-process sessions and propagation timing remain unverified. No live IdP,
registered client, HTTPS endpoint, DB or staging was established by this handoff.
Template process/pool numbers are proposals, not observed deployed topology.

## Remaining coordination

Verify the reported interpreter's current packages, agree concrete auth routing
and resource integration, supply K-05 atomic session/identity requirements, and
confirm TLS/proxy/public-origin plans, deployment process topology and who can
supply an approved local/staging service.
Only report non-secret configuration and availability. No credential or network
probe is needed to answer the source/ownership questions.

Keep `hosted/**`, K-02 tests/config/docs and J-01 models/fixtures/exporters under
their existing ownership. Prefer an additive K-03 wrapper/repository adapter and
list the exact composition changes needed before touching existing files.

J-01 still needs review of new auth/onboarding error semantics and any added
routes. The existing shared Session/Capabilities/Problem models remain the single
wire-model source. K-03 synthetic tests will construct values through those
models instead of creating a competing fixture/schema bundle.

## Historical operator command for the first slice

Use the exact interpreter identified in the K-02 handoff after checking its
current package metadata. Pydantic, pytest, Ruff and mypy must be available.
Do not install or change packages just to run this block.

Run in PowerShell from the existing API worktree:

```powershell
Set-Location -LiteralPath 'C:\Users\Trung Kiên\Documents\ChatGPT\Đồ Án\aletheia-j01-api'
$K03Python = 'C:\Users\Trung Kiên\Documents\ChatGPT\Đồ Án\aletheia-k01-baseline-6d887dfb1fbb4aaba69613a8d61889fc\metric-review-venv\Scripts\python.exe'
if (-not $K03Python -or -not (Test-Path -LiteralPath $K03Python)) {
  throw 'Set K03Python to the confirmed existing project interpreter.'
}

$env:PYTHONDONTWRITEBYTECODE = '1'
& $K03Python -B -m pytest tests/unit/test_k03_auth_policy.py tests/unit/test_k03_auth_boundary.py -o addopts='' -q -p no:cacheprovider
Write-Output "PYTEST_EXIT=$LASTEXITCODE"

& $K03Python -B -m ruff check src/aletheia_lab/hosted_auth tests/unit/test_k03_auth_policy.py tests/unit/test_k03_auth_boundary.py --extend-select C901
Write-Output "RUFF_EXIT=$LASTEXITCODE"

$K03TypeCache = Join-Path $env:TEMP ('aletheia-k03-mypy-' + [guid]::NewGuid().ToString('N'))
& $K03Python -B -m mypy --strict --follow-imports=silent --cache-dir $K03TypeCache src/aletheia_lab/hosted_auth
Write-Output "MYPY_EXIT=$LASTEXITCODE"
```

Pytest uses synthetic inputs and temporary policy files, no services or network.
Ruff does not use `--fix`. Mypy writes its cache to the named new temporary
directory. None of these commands change Git/staging or existing product data.
This block records the original operator-run phase. It is superseded for current
verification by Kiên's 2026-11-10 command authorization and the exact results in
`k03-verification-20261110.md`; no prior result is retroactively broadened.
