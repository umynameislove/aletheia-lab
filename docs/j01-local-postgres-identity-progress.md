# J-01: PostgreSQL identity lookup progress

Local-only progress, not J-01 acceptance or a deployed staging service.

- `PostgresIdentityDirectory` resolves an explicitly provisioned binding by the
  exact issuer and subject using SQL parameters. It never provisions accounts.
- On Windows, a synthetic smoke test connected to the isolated PostgreSQL
  container and exercised a connection-local temporary table: matching binding,
  unknown subject, and wrong issuer behaved as expected. The table was scoped
  to that connection; this was not a durable hosted schema or storage test.
- Two unit tests and Ruff passed for the new adapter. The Windows async psycopg
  smoke test requires a Selector event loop; the default Proactor loop failed.
- Keycloak's independently verified PKCE/OIDC exchange has not been wired to
  this directory in a running HTTP login flow. Transactional login/session
  repositories, a supported migration, account provisioning, authorization,
  hosted source/result persistence, and shared staging acceptance remain open.
- Identity lookup alone is not an authorization decision. A login commit must
  recheck the account and its epoch transactionally with session creation.

## Experimental login transaction components

- `PostgresLoginClaims` is a standalone primitive, not an implementation of
  `LoginTransactionStore`. Its schema is an isolated test proposal in
  `j01-experimental-login-claims.sql`, not an installed migration.
- A PostgreSQL local smoke test with synthetic rows checked supersession,
  wrong-browser rejection, twelve concurrent claim attempts with one winner,
  and rollback when an expired begin is rejected. A unique test schema was
  created for this check and removed afterwards. These tests are not proof of
  cross-process failure recovery, account revocation, or a complete login.
- Windows and PostgreSQL container clocks differed by several seconds during
  testing; the expiry test uses the database clock. Clock synchronization and
  the coordinator's host-clock assumptions still require a shared policy.
- `LoginProofCipher` encrypts synthetic nonce and PKCE verifier values using
  AES-256-GCM with intent/browser/state binding. It is now used by the
  experimental claim primitive. PostgreSQL tests checked ciphertext storage,
  successful decrypt and a spent claim even when decrypt fails. Key provisioning
  and rotation, durable migrations, expiry cleanup and deployment remain open.
- A committed synthetic claim was recovered after closing and reopening the
  connection pool. This does not test PostgreSQL restart or disaster recovery.
- On Windows, 17 focused unit tests passed across these components; Ruff and
  mypy passed on the new claims and proof-encryption modules. Dependency used
  for the proof test came from the existing isolated K-03 auth target.

## Combined Keycloak and PostgreSQL smoke test

In a local synthetic run, a real Keycloak authorization code was exchanged by
the application's verified OIDC adapter using nonce and verifier recovered from
the encrypted PostgreSQL claim. A repeated claim was denied. The proof was
written to the experimental database after the test browser obtained the code;
this is not a complete HTTP initiation/callback flow or evidence of a session.
The isolated test schema was removed afterwards. The source-to-result hosted
flow and joint staging acceptance remain unimplemented and untested.

## Experimental PostgreSQL session store

`PostgresSessionStore` and `j01-experimental-session-schema.sql` implement a
local experiment for reading, rotating and revoking stored sessions. In a
random temporary PostgreSQL schema, synthetic checks observed one winner among
12 competing rotations, revocation through the immediately previous credential,
logout-all confined to one account, and no resurrection in one concurrent
rotation/logout schedule. A seeded synthetic session was also read through the
existing ASGI `/v1/session` adapter and returned `session_revoked` after logout.
The test schema was removed afterwards. Ruff and mypy passed for the store.

This is not a production migration or a real login/session issuance path. The
synthetic rows were inserted directly for tests. Login commit, account changes,
membership and session operations must share the account lock and authoritative
clock; their cross-process transactional behavior and PostgreSQL restart have
not been qualified. The callback has not been wired to this store or the runtime.
No hosted source/result flow, shared staging or J-01 acceptance is established.

The session smoke test later failed once during synthetic row setup because
multiple `clock_timestamp()` calls differed by a microsecond and violated the
timestamp ordering constraint. The test fixture was corrected to use a single
statement timestamp; the session smoke passed again. This was a fixture error,
and is separate from the unexplained callback HTTP 500 described below.

## Fresh login experiment across callback and PostgreSQL

`PostgresFreshLogin` experimentally commits a fresh login into the session schema;
the account must be pre-provisioned. It deliberately rejects reauthentication,
previous session cookies and browser account switching. Login/session writers
use a shared database advisory transaction lock in this experiment; throughput,
other account/membership writers and production lock discipline are untested.

`check_postgres_fresh_login.py` used a synthetic IdP, the real coordinator,
callback ASGI adapter and isolated PostgreSQL to create a session, read it via
`/v1/session`, and reject a replay. Five consecutive reruns passed. The first
attempt returned HTTP 500 for an unknown reason; its cause was not captured and
has not been eliminated. The test deliberately controls the coordinator clock to
bridge observed database/host clock differences. It does not establish a real
Keycloak-to-session HTTP flow or real-time clock correctness. The test schema
was removed after each run. The runtime remains unwired and source/result,
revocation latency and joint acceptance remain open.

## Further local diagnosis

With the local Keycloak issuer and a pre-provisioned synthetic account, the
experimental ASGI callback created a PostgreSQL session and `/v1/session`
returned it; a subsequent local revocation denied the next request. Some runs
passed, but repeated runs also observed HTTP 503. Instrumentation classified a
503 as an OIDC exchange deadline (`TimeoutError` during TLS read), not a
successful login. Extending only the test timeout did not establish stability.
The callback unit test now verifies an IdP timeout sends no cookie and leaves
the claimed callback spent; a fresh login is required.

A later HTTP 500 was traced to `LoginCoordinator._committed` rejecting session
timestamp ordering while the test used an artificial clock. This does not prove
that the first 500 had the same cause. Windows and container DB clocks differed
by roughly six seconds when sampled. No production clock policy or fix has
been qualified, and the original 500 remains unresolved. A separate initial
connection error occurred while Keycloak was still starting after Docker restart.
These are local diagnostics, not staging acceptance or a measured SLO.

## Clock-readiness experiment

`hosted_auth/clock_guard.py` implements a bounded, fail-closed comparison of a
PostgreSQL timestamp with both ends of a host-clock round trip. It rejects
excessive latency, wall-clock jumps, naive timestamps and invalid bounds. Five
unit tests, Ruff and mypy passed. A local database probe returned false for a
one-second bound and true for a ten-second bound; neither is an approved
deployment threshold. The probe does not synchronize clocks or resolve the
login coordinator's clock policy.

An opt-in `ClockGuardedBackend` now gates readiness and checks before and after
session/capability reads. Unit tests plus the existing lifecycle suite: 20 passed;
Ruff and mypy passed. In a synthetic `ProcessApplication` using a real local
PostgreSQL clock probe with a one-second test threshold, lifespan startup failed
closed as expected. Three host-minus-database samples over roughly two seconds
were +5.339, +5.731 and +5.630 seconds. This short sample cannot establish drift
or clock health over time. The threshold is a test value, not an agreed SLO.
This wrapper is not installed in production composition and does not protect
callback, mutations or workers. Synchronization, authoritative time design,
key rotation and complete transaction/runtime wiring remain open.

## Bounded timestamp policy and source inspection

The fresh-login experiment now accepts an explicitly configured database clock
skew of 0–60 seconds (default zero). The local Keycloak test uses the existing
AuthPolicy 30-second bound and a real host clock, with a PostgreSQL clock probe
before and after the callback coordinator. One local run passed fresh login,
callback/session read, replay rejection and revocation. It used pre-provisioned
synthetic identity and an ASGI in-process transport, not a browser/socket server.
Login/session timestamp comparisons accept only the configured skew; expiry
checks do not add skew. The session corruption test now checks 31 seconds into
the future to exceed the default 30-second policy. This does not synchronize
the host or container clocks or establish an agreed production threshold.

`hosted/source_bundle.py` adds bounded ZIP inspection without extraction. It
hashes actual contents, checks entry count and expanded byte budgets, rejects
unsafe paths, links, case-colliding names and names normalized by zipfile on
Windows. The caller must bound bytes before buffering. It has no HTTP route,
workspace authorization, immutable storage, receipt or analysis job integration.
101 selected login/session/callback/source tests passed and Ruff passed for the
new source and changed callback/fresh-login files. Source-to-result acceptance
is still open. Callback clock checking is injectable; production composition
has not been installed and must supply it for any deployment.

## Scoped source persistence experiment

`PostgresSources` and the isolated `j01-experimental-source-schema.sql` store
accepted ZIP bytes, content SHA-256 and entry manifest in PostgreSQL. The
repository checks the current active credential/session/account and workspace
membership before inspection and again before committing. Owners/admins/analysts
can write; active viewers can read. A request key is hashed and scoped by actor
and workspace; identical retries return the same receipt and changed content
with the same key raises a conflict. Queries use parameters and source reads
check the stored content hash.

Local synthetic database tests passed upload/read, same-key replay, different
content conflict, cross-workspace denial, revoked membership denial, viewer
write denial, and reading committed bytes from a separate pool. Session tests
also passed in that run. The schema was removed afterwards; Ruff/mypy passed.
This does not prove crash recovery or immutability against a database admin.
The experiment shares the coarse advisory auth lock and has no migration,
HTTP/CSRF upload wiring, quota/admission service, bounded process worker,
preview/confirm mapping, job receipt, result publication or client integration.
Source validation uses a thread and does not establish CPU cancellation bounds.
It is not a complete hosted source-to-result flow and J-01 remains open.

## Opt-in source upload HTTP experiment

`SourceUploadApplication` adds `POST /v1/workspaces/{workspace_id}/sources` for
ZIP bytes. Its constructor wires Host validation and the existing real CSRF
middleware with current session binding, and requires a clock-check function.
It bounds body bytes/events/idle/total time, validates one Idempotency-Key, calls
the scoped repository and returns a draft 201 receipt after commit. Error bodies
use safe Problem responses; private responses use no-store. This route is not
installed in the process factory or enabled through capabilities.

A local isolated PostgreSQL run tested the complete in-process ASGI upload with
real CSRF validation and a seeded synthetic session, matching receipt on replay,
readback of bytes and CSRF rejection. Earlier repository scope/revocation tests
also passed in that run. 138 selected upload/ZIP/body/CSRF unit tests passed;
Ruff and mypy passed for HTTP/body code. This is not a socket/browser test, job
receipt, worker run or source-to-result acceptance. Shared runtime composition,
production migrations/admission, preview/confirm, analysis and result remain open.

## Snapshot integration finding

The current local confirm path requires an exact Git worktree and a mandatory
Git-state snapshot binding. Raw hosted ZIP is not compatible with that input.
The required upload-provenance boundary is recorded in
`j01-upload-snapshot-boundary.md`; it is not implemented. ZIP inspection now
rejects `.git` components and file/directory path collisions; all 14 source ZIP
unit tests passed. No Git history was fabricated and no hosted confirmation or
analysis result was created in this step.

## Upload snapshot envelope and repository publication

`UploadSnapshot` now implements an internal `hosted-upload-snapshot/v1`
envelope, explicitly carrying `git_provenance=not_supplied`. It binds workspace,
source ID/hash, P3 mapping configuration and valid mapping result. It checks
mapping/result scope and observation identities and seals both state and the
captured record. Capture time changes the record seal but not semantic snapshot
identity. Models reject tampered IDs/source hashes/scope. The existing local
snapshot and `p6-product-view/v1` schemas remain unchanged.

`PostgresSources.publish_snapshot` is a trusted-worker-only operation, not an
HTTP payload handler. It checks current writer access and source hash before
storing the envelope; repeat publication preserves the first record. Reads
recheck membership and workspace scope and validate both seals. Local synthetic
tests computed real P3 mapping from fixture files, uploaded those files as ZIP,
published/reloaded the snapshot, checked repeat identity and wrong-workspace
denial. 13 selected snapshot/mapping tests, Ruff and mypy passed. The temporary
schema and fixture directory were removed after the DB test.

Important remaining proof: the future worker must itself reload exact stored
source bytes and derive mapping facts. The publication model alone cannot prove
that relationship and is not exposed to clients. No hosted preview/confirm
route, supervised analysis job or ProductView result exists for this envelope
yet; no source-to-result or J-01 acceptance is claimed.

## ZIP-to-P3 preview and confirmation

`UploadProject` reuses P3 inspection, mapping candidates, confirmation mapping
validation and observation extraction on verified uploaded bytes. It stages
validated file names beneath a private, server-supplied content-addressed root,
checks bytes/hashes and rejects changed staging files. It never reads an uploaded
Git directory or executes source code. These synchronous methods are intended
for a worker process and must not be called on an API event loop.

16 selected ZIP/upload-snapshot/preview-confirm tests, Ruff and mypy passed. The
local PostgreSQL harness now reloads source bytes from the scoped repository,
passes those exact bytes to preview and confirm, then publishes and reads the
resulting snapshot through repository authorization. The test passed, including
existing HTTP upload/CSRF and session/source scope checks. Its preview/confirm
calls used test-only thread offloading; a durable process worker is not wired.

Known limitations: existing P3 identifiers depend on the private staging root;
cross-worker-host or staging relocation reproducibility is not established.
Interrupted staging directories are retained and subsequently rejected if their
contents are incomplete; quotas and owned cleanup are not implemented. Source
inspection and P3 candidate counts bound this experiment, not production CPU or
memory. No preview/confirm HTTP job API or ProductView result has been produced;
J-01 remains open. The reusable legacy P3/Git domain was not modified here.

## Ordered login smoke test

`check_oidc_intent_before_login.py` in the local staging directory subsequently
tested the correct ordering: create and commit an encrypted intent in isolated
PostgreSQL before initiating authorization; sign in as the synthetic local
Keycloak user; reject a wrong browser; claim the intent once; recover its proof;
and exchange and verify the code using `AuthlibOidcClient`. Callback replay was
denied. The test obtained the redirect itself and did not send it to an app
HTTP callback. It did not resolve an account, create a session, test Keycloak
revocation, or run any hosted source/result operation. It deletes only its own
randomly named test schema after completion.

## Callback HTTP adapter, synthetic only

`hosted_auth/callback_http.py` adds an opt-in ASGI adapter around an injected
login coordinator. It allows only HTTPS GET callbacks, bounds cookies and query,
revalidates the returned active session and credential digest, emits a relative
allowlisted 303 and Secure/HttpOnly session and CSRF cookies, and uses fixed
errors without reflecting the callback. Unit tests exercise both a synthetic
coordinator and the actual `LoginCoordinator` with a synthetic store/IdP;
131 selected callback/login boundary regression tests passed on Windows.

This adapter is not wired into the process runtime: it relies on an outer Host
guard and a real coordinator backed by atomic login and session repositories.
It has not been exercised with a real HTTP Keycloak callback. If session commit
succeeds but response delivery fails, recovery needs a defined policy. J-01
remains open and the hosted source/result service is still not implemented.

## One-shot upload worker

`hosted.upload_worker` runs preview or confirm in a separate Python process
with bounded JSON/ZIP input and output, a server-supplied private root, and a
fixed failure message. A subprocess test ran real P3 preview and confirm in
separate processes using the same synthetic ZIP and rejected malformed base64;
the existing direct preview/confirm test also passed (2 tests). Ruff and mypy
passed. The test supplies a limited environment; deployment must enforce the
same and must not pass its root from a user request. This was not connected to
a process supervisor or durable queue, had no HTTP preview/confirm route or
analysis result, and does not establish CPU/RSS limits or production isolation.
The provisional worker ZIP budget is 12 MiB and differs from the draft 200 MiB
capability; streaming and a consistent deployed limit remain required.

`UploadWorkerSupervisor` now reuses the report supervisor's bounded slots,
timeout, child termination and stripped environment. Nine upload-worker and
existing report-supervisor tests passed; Ruff and mypy passed. This supervisor
is not wired into runtime or a durable queue and has no OS memory isolation;
its output still needs authorization and source revalidation before publication.

`UploadOperations` now reads authorized source bytes, invokes the supervised
worker, re-reads the source under current authorization, checks worker scope and
source SHA, and publishes a validated confirmation snapshot under a fresh
write-authorization check. Preview also checks the returned source SHA. A local
synthetic PostgreSQL/ASGI harness passed upload → process preview/confirm →
stored snapshot, but needed a test-only thread with a Proactor event loop because
Windows psycopg uses a Selector event loop. Four focused orchestration/worker/
snapshot tests, Ruff and mypy passed. Production event-loop composition remains
unresolved on Windows. This coordinator has no durable queue, preview/confirm
HTTP route or analysis/result publication, and makes no J-01 acceptance claim.

## Experimental job persistence

`PostgresJobs` and the local-only job schema implement durable enqueue with
actor/workspace/operation/key-scoped idempotency, bounded request/result JSON,
lease claims and a write-access recheck before marking a job succeeded. A local
PostgreSQL synthetic test verified receipt replay, conflicting request denial,
one winner among eight competing claims, stale lease fencing after reclaim and
denial of publication after membership revocation. Ruff and mypy passed.

This is not a deployed queue: there is no worker loop, HTTP job/status endpoint,
retention/lease cleanup, cancel/retry flow, integration with snapshot/result
transactions or restart/failure qualification. Session credential rotation can
invalidate an enqueued job; its recovery policy is not decided. Job result JSON
in the experiment is synthetic and is not a ProductView or evidence of analysis.
The coarse shared auth lock and production workload remain unmeasured.

An experimental `run_one_upload_job` now claims only preview/confirm jobs and
calls the authorized process supervisor. Confirm can publish the snapshot and
mark its job successful in one PostgreSQL transaction after checking the lease,
source and current membership; stale leases do not publish. The local synthetic
PostgreSQL test passed a supervised preview job and fenced atomic confirmation,
alongside the prior lease/revocation checks. Ruff and mypy passed. The harness
uses a Windows-specific test bridge between Selector and Proactor event loops.
There is still no runtime scheduler, job HTTP/status endpoint, failure retention,
analysis worker, or ProductView result. A continuously running worker and crash
recovery have not been verified; this is not J-01 acceptance.

`run_upload_scheduler` now polls preview/confirm jobs with bounded concurrency,
an idle interval, an execution deadline shorter than the lease, and an explicit
stop event. Worker/storage errors are surfaced; cancellation leaves an unfinished
job for lease recovery. Twelve focused scheduler/worker tests passed, along with
Ruff and mypy. An isolated PostgreSQL harness enqueued a synthetic preview and
observed the scheduler process it and persist its output. The harness used the
test-only Windows Selector/Proactor bridge; runtime process startup, forced crash
recovery, OS resource limits and analysis/ProductView remain unverified.

`JobApplication` now provides opt-in POST job and GET/HEAD status for preview
and confirm. Submission uses the existing Host/CSRF/session checks, strict JSON,
body budgets, idempotency key and clock predicate; enqueue verifies the source
belongs to the workspace. The stored status is visible only to the creator
with current workspace access. A synthetic PostgreSQL/ASGI test passed 202,
status lookup, retry preserving receipt, cross-workspace denial and CSRF denial;
Ruff and mypy passed. Analysis submission is intentionally unavailable.
These routes are not wired to the runtime or a continuously running worker;
status does not yet expose preview output or ProductView. The 24-hour retention
proposal, restart/recovery and per-job role policy remain to be qualified.

The experimental job route now exposes completed preview/confirm output via
`GET /v1/workspaces/{workspace}/jobs/{job}/output`, scoped to the creator with
current membership. A local ASGI/PostgreSQL test checked pending output denial,
successful output and cross-workspace denial. A worker exception records only a
fixed failed state under its still-valid lease; a synthetic invalid job tested
this without persisting private output. `ProcessApplication` can accept injected
routes, enabled only while its backend is active; this is an opt-in composition
hook, not a configured deployment. The existing runtime/CSRF lifecycle suite
passed 98 tests and Ruff/mypy passed. One initial collection run failed because
the relative dependency path was not resolved; rerunning with absolute paths
passed. Durable scheduler, production route configuration, ProductView analysis
and shared staging acceptance remain open.
# 2026-11-10: upload analysis and interrupted worker check

- The experimental upload job boundary accepts an initial-snapshot analysis and
  returns a `p6-product-view/v1` through its authorized job output. This is an
  observation of one snapshot: it makes no comparison or causal claim.
- In isolated local PostgreSQL, an ASGI job request, subprocess analysis,
  durable output and retrieval passed. A separate process claimed a queued job
  and was killed; after its lease expired another claimant completed the job,
  while the old lease was rejected. The test uses synthetic inputs and a
  temporary schema, not shared staging.
- `ProcessApplication` can start and stop an explicitly supplied scheduler
  during ASGI lifespan, and refuses subsequent requests if that task stops.
  The scheduler is not yet wired to a deployed resource factory: this checkout
  does not contain a finished deployment entry point. Windows test code uses
  a test-only thread bridge for psycopg and subprocess event-loop compatibility.
- No claim is made that hosted end-to-end or J-01 joint acceptance has passed.
  Deployment wiring, shared staging, actual metric comparison and a joint UI
  run remain unverified. The scheduler recovery check covers a killed claimant,
  not every interruption point of the full deployment.

## Local composition follow-up

- The experimental PostgreSQL workspace adapter reads current account, session,
  membership and workspace in one query. The local database check passed the
  capabilities read and membership-revocation denial.
- `compose_upload_runtime` joins callback, session/capability reads, upload and
  job routes with the scheduler and clock guard. In the local isolated schema,
  an ASGI lifespan test submitted preview, confirm and analysis through the
  composed runtime and retrieved the resulting ProductView. It used a synthetic
  session, an unused callback stub and a Windows test-only worker bridge.
- This composition is injected into a test harness, not an executable staging
  deployment. It does not provide a login-start route, production resource
  provisioning, or an integrated Keycloak-to-ProductView browser run. The
  upload limit is provisional and the displayed capabilities are not yet wired
  to expose these operations. Shared staging and joint acceptance remain open.

## Local Keycloak through result follow-up

- An experimental same-origin login-start HTTP boundary now calls the durable
  login coordinator and is included in the composed runtime. The local synthetic
  check reached the real Keycloak instance, verified callback and persisted a
  PostgreSQL session, then used that session for capabilities, source upload,
  preview, confirm and initial ProductView analysis through ASGI HTTP calls.
  The test output `KEYCLOAK_LOGIN_TO_HOSTED_SOURCE_RESULT=PASS` is recorded by
  `../j01-local-staging-20261109/check_keycloak_fresh_session.py`.
- The test used HTTPX rather than a browser, a pre-provisioned synthetic account
  and workspace, a Windows test-only worker bridge, and temporary DB schema.
  It did not test account switching or a deployed service. Login-start is
  admission-limited to same-origin Fetch Metadata and still needs real-server
  browser verification and production admission/rate limiting. The limitations
  in the previous section otherwise remain, except that login-start and this
  local HTTPX combined flow now exist.

## Local logout and upload limit follow-up

- The composed experimental runtime exposes CSRF-protected single-session
  logout. The Keycloak/DB synthetic flow checked a bad CSRF token is denied,
  a valid logout succeeds and the former session is then rejected.
- Runtime capabilities now return the lower of the configured upload limit,
  repository limit and a provisional 10 MiB worker-safe limit, and cap question
  length at 2000. A local HTTP check verified the repository's smaller test
  limit and rejected an oversized upload. Actions remain disabled in the
  capability response until the hosted consumer and deployment are qualified.
- These tests do not establish account switching, IdP logout propagation,
  shared staging, a production object store or joint browser acceptance.

## Account-switch experiment

- The isolated PostgreSQL login schema records the previous session context.
  A switch verifies the prior session and atomically revokes it when the new
  account session is committed. A pending switch cannot recreate a session if
  logout revokes the old context first. `/v1/auth/start` accepts `mode=switch`
  and requests reauthentication through the coordinator.
- Synthetic IdP plus PostgreSQL tests passed for switching between two
  pre-provisioned accounts, rejecting the old session, and the logout/switch
  race. Two simultaneous switches from one prior session were also checked:
  exactly one succeeded. The local script is
  `../j01-local-staging-20261109/check_postgres_fresh_login.py`; its identity
  provider is synthetic and its clock is controlled. 52 related unit tests
  passed. A separate single-account Keycloak login
  and source-to-result regression passed after the schema change.
- This is an experimental schema change, not a migration for an existing
  database. Two-account switching with real Keycloak and a browser, logout at
  the IdP, shared staging, and production rollout remain unverified.

## OIDC discovery refresh experiment

- `RefreshingOidc` fetches bounded discovery and JWKS from the configured
  issuer origin, verifies the issuer and endpoint origins, and refreshes the
  signing material after its short cache period. A failed refresh does not
  fall back to expired keys.
- The local Keycloak-to-ProductView synthetic flow passed using this loader.
  The related 56-test run also covered cache reuse, expiry refresh, and
  rejection of a cross-origin JWKS URL. Key rotation under concurrent real
  traffic and operator-managed deployment remain unverified.

## Managed runtime preparation

- An experimental ASGI factory now reads explicit runtime and auth-policy
  configuration, obtains secrets from environment variables, opens its pool,
  initializes OIDC material and starts the composed API and scheduler. A server
  entry point requires explicit HTTPS certificate and key paths. The runtime
  closes the worker and pool before reporting shutdown complete.
- On Windows, a dedicated Proactor thread runs subprocess workers while the API
  and psycopg use a Selector loop. The Windows worker subprocess test passed;
  isolated tests cover startup failure and resource cleanup. Server tests use
  doubles and have not established a real deployed server or TLS/browser flow.
- This remains experimental: dependencies and schema migration are not packaged
  as a release; no shared staging exists in the evidence; two real Keycloak
  accounts and the browser journey have not been exercised together.

## Real HTTPS and two-account local check

- The managed factory started a real HTTPS Uvicorn service on loopback with a
  separate PostgreSQL schema and a separate Keycloak 26.8.2 realm. HTTPX used
  certificate verification against the test CA. The disposable test's import
  file was sanitized after realm import. The HTTPS service and test container
  were stopped after testing; the test schema and container were retained.
- Both synthetic accounts authenticated separately. The attempted in-session
  switch **failed**: Keycloak displayed a password-only reauthentication form
  for account A, and the resulting session was again account A. The old app
  session was revoked. This contradicts the desired switch to B; it must not be
  accepted as a successful two-account switch. The check script is
  `../j01-browser-two-20261110/check_https_accounts.py`.
- Browser automation reported local browser use disabled in Work settings, so
  the real-browser case remains not run. No certificate warning was bypassed.
  IdP logout/return-to behavior needs a reviewed design and end-to-end check.
- The `hosted` optional dependency set records direct versions used in this
  test; a complete transitive lock, distributable migration, fresh install and
  release qualification remain open.

## Opt-in IdP logout work in progress

- Discovery validates the `end_session_endpoint` against the configured issuer
  origin. When `ALETHEIA_HOSTED_IDP_LOGOUT=enabled`, the experimental logout
  returns a JSON `logout_url` after revoking the local session; the caller must
  navigate to it and handle any Keycloak confirmation. The post-logout URI is
  fixed to the configured public origin plus `/#/signed-out`, not browser input.
  Default is disabled pending realm-owner and consumer agreement.
- If discovery is unavailable after local revocation, the endpoint clears local
  cookies and returns `logout_url: null`: this explicitly means IdP logout has
  not been completed. The client must not report full sign-out in that case.
- The Keycloak client must register the exact post-logout URI. Its registration,
  signed-out UI, switch navigation, provider-side confirmation, back-channel
  logout and end-to-end two-account flow are still unverified. A real Keycloak
  rerun was attempted but the Docker engine was unavailable. These changes are
  a proposed contract, not a jointly accepted release.

## Local IdP logout follow-up

- An isolated Keycloak 26.8.2 realm with a test post-logout wildcard and local
  HTTPS server was checked through HTTPX with certificate verification. The
  flow authenticated A, revoked its app session, confirmed logout at Keycloak,
  received the fixed signed-out redirect and authenticated B with return_to=/.
  The former A cookie was rejected and B's context differed from A. Direct
  switch was refused when IdP logout was enabled. This is not a browser run.
- A Keycloak client without a registered post-logout URI returned 400. With the
  test wildcard, Keycloak displayed a confirmation form. The wildcard is test
  configuration only. Login-start/callback intermittently returned 503 during
  this local check; the cause is not established and retry from scratch passed.
- Backend/consumer handoff and unresolved behavior are listed in
  `j01-logout-handover-draft.md`. Client UI, real two-tab interaction,
  back-channel logout, job behavior after revocation, and joint staging remain
  open. No production realm has been changed.

