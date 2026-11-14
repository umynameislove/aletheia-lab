# Hosted runtime configuration

## macOS follow-up — 2026-11-11

Install `.[hosted]` to include `psycopg[binary]==3.3.8`. The worker intentionally
starts with a minimal environment; resolving a system libpq only through the
parent's PATH did not survive that boundary on macOS. The original split-process
PostgreSQL journey passed after installing this declared dependency.

Current local producer/consumer checks pass **248/248**. Shared wire choices,
browser journeys and environment-owner deployment decisions still require their
own acceptance. The retained Windows freeze below describes its recorded source,
not the later macOS snapshot.

## Recorded Windows runtime status — 2026-11-11

Runtime code and private verification environments now exist. The frozen source
is **621 Python/SQL/config files**, freeze-v2 aggregate
`488b140c055dfd4d64cb0f0358f84e3eee32c736c63266859811b528245d46f3`;
the canonical persistence ledger contains **10 migrations**. HEAD remains
`2bbcdc2a7637c840babd8b71910306c64072bc4b` plus WIP on `feat/product-api`.
[Source freeze][runtime-freeze].

The API server (`hosted.server`) and worker entrypoint (`hosted.managed_worker`)
run separate OS processes with separate role-bound pools over the intended same
schema/object authority. The server command admits one API process per invocation;
this does not choose the shared deployment's replica count. Startup/readiness
checks cover trusted runtime binding, required schemas, clock/dependency state
and matching upload/preview profiles. Blank example configuration is still a
fail-closed template, not a deployable staging configuration.

Use the maintained [J01 backend runbook][runtime-runbook] for launch and
configuration commands. The header timer starts **after the TLS handshake**;
asyncio's native TLS handshake timeout is **60 seconds**. Public-ingress
handshake policy remains an environment-owner decision. Export TTL limits
**download availability**; it does not purge retained canonical artifacts.

Current evidence on this source:

- **24 PostgreSQL integration cases passed** on **PostgreSQL 16.15** in a private
  container: API/worker separation, persistence, restricted-role exports,
  lifecycle/refresh/settings and quota/delete race regressions. All **630 input
  hashes** (621 runtime plus nine test files) were stable; the owned container
  was removed and independently confirmed absent. [DB receipt][runtime-db].
- **63 loopback HTTPS/settings/header cases passed**, Uvicorn **0.48.0** and h11
  **0.16.0**, with no runtime drift or owned listener/process left running.
  This qualifies the recorded local edge cases. [TLS receipt][runtime-tls].
- Final selected unit qualification: **1125 passed** in **133.74s** across
  **87 files**, with **zero failures, skips or deselections**. Production and
  selected-test Ruff passed, as did strict mypy for **81 hosted files**. All
  **1254 input files** were byte-stable; all 621 runtime files matched freeze v2
  before/after. [Final unit qualification][runtime-unit].

J01's separate current installed-wheel Live report records real Keycloak/HTTPS/
PostgreSQL, distinct restricted API/worker roles, three durable jobs, native
canonical/audit records, restart persistence and six cross-account denials;
619 installed Python/SQL files stayed unchanged and cleanup passed.
[J01 installed-live report][runtime-j01-live]. This is HTTPX/private evidence,
not shared browser/staging or SLO acceptance; clean restart does not prove
arbitrary crash recovery.

The unified offline OpenAPI snapshot has **92 operations / 53 paths / 83 schemas**
and dispositions for **38 original requirements**, including five conditional
choices. J01 owns the shared disposition/contract documents. Consumer checks
remain **244 passed / 3 failed, zero skips**; Quân's wire deltas and joint S01–S05
browser runs remain open. The environment owner still needs to confirm public
origin/TLS/proxy, IdP registration, PostgreSQL/object authority, replicas,
operator and budget/test window. Neither local TLS nor an example TOML settles
those deployment decisions.

[runtime-freeze]: <C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/k02-verification-20261111/production-freeze-v2.json>
[runtime-db]: <C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/k02-verification-20261111/private-backend-integration-final.postflight.json>
[runtime-tls]: <C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/k02-verification-20261111/edge-checks-final-qualification.json>
[runtime-unit]: <C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/k02-verification-20261111/final-unit-rerun2-fa0876a955/summary.json>
[runtime-j01-live]: <C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/j01-completion-20261110-e/canonical-local-666d5d82f93e4ef7b6515bd81b7f8c57/report.json>
[runtime-runbook]: <C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/j01-completion-20261110-e/J01-backend-runbook.md>

## Historical notes — earlier snapshots retained

Historical unit evidence, superseded and not additive: [1114 passed / 10 failed][runtime-unit-prior-failed]; [1ceb checks passed but eight test-import files drifted][runtime-unit-prior-drift].

[runtime-unit-prior-failed]: <C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/k02-verification-20261111/final-unit-f12f14f768/summary.json>
[runtime-unit-prior-drift]: <C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/k02-verification-20261111/final-unit-rerun-1ceb8c12f4/receipt.json>

The dated handoffs below are preserved as historical evidence. Their earlier
absence/hold statements and limits do not override the current section, and
passing counts from overlapping selections must not be added.

## Later J01 current-model caller handoff — 2026-11-10

After the transport handoff below, J01 supplied an additive Session producer field
`can_create_workspace: bool = False` for legacy input compatibility. K02 changed
only its own `test_k02_contract_schema.py` and this evidence note. K02 did not
change the producer, runtime workspace-creation policy or frozen v7 artifacts.
The earlier Bu reply and transport snapshots remain historical evidence; this
later caller test does not establish Bảo's product approval or staging readiness.

Four live HTTP cases now use the current Session/Capabilities/Problem model JSON
schemas with the independent Draft202012Validator and format checking. Status,
revoked-session and revoked-membership codes remain asserted. Unknown producer
fields must still fail validation; Session must explicitly emit a boolean
can_create_workspace. Current Problem consumer validation is retained.
Four separate historical cases keep v7 Session/Capabilities and both relevant
Problem fixtures validated against their frozen producer/consumer schemas.

Focused result: **77 passed in 5.06s**, no skips, including the updated K02 schema,
HTTP/backend tests and J01 Session contract tests. Ruff/C901 passed. Snapshot
contains 597 files with zero changes; all 22 frozen v7 JSON files retained
their hashes. This is a focused caller check, not a new full K02 regression.

- Test SHA-256: `6e544c549464b33bbccca131332a3c84798036d0272b844ccabf7cc31344e880`
- Producer SHA-256: `1cd5363933336330b49e3e40f19edabfac06b0c91f89b6223fdafcba3c7c80fa`
- Manifest: `Đồ Án 3/k02-verification-20261110/current-schema-manifest-876965048ab44d7d96cbecd33420c144.json`
- Manifest SHA-256: `5cf0c91236d7dc9b3e7924f2d367687ccd9403f36d7e988ad8b2c1d38e3fa23c`
- Log: `Đồ Án 3/k02-verification-20261110/current-schema-callers.log`
- Receipt: `Đồ Án 3/k02-verification-20261110/current-schema-final-receipt.json`

Paths are beneath `C:/Users/Trung Kiên/Documents/ChatGPT/`. HEAD remains
`2bbcdc2a7637c840babd8b71910306c64072bc4b` plus WIP; no stage, commit, push or merge.

## Transport/settings handoff and Bu feedback — 2026-11-10

This continuation changed only K02-owned composition, settings, staging template,
K02 tests and this document. K04 keeps the canonical runtime binding checker and
API composition; K03 keeps its worker caller; J01 keeps package-data edits.
No shared contract, repository, auth store or K03/K04/K05 production file was copied
or edited by K02. The existing worktree remains `feat/product-api`, HEAD
`2bbcdc2a7637c840babd8b71910306c64072bc4b` plus WIP; staged paths were empty.

`api.maximum_header_bytes` (default 32768) and `maximum_header_count` (default 100)
are checked by ProcessApplication before routing/body reads. Each parsed ASGI
field counts independently, including duplicates; bytes are name + value + 4 per
field. Exact limits are accepted; excess uses the existing shared Problem
`431 headers_too_large`, no-store, a generated request ID and no reflected header
values. HEAD has no body. Rejection writes retain the bounded timeout and cannot
send another response after headers begin. The cookie limit remains independent.
These operator defaults are not measured capacity. This does not bound raw TLS,
request lines or slow headers before ASGI; h11's existing incomplete-event limit
may reject earlier, and the intended ingress still needs its own budgets.

Storage now declares trusted `expected_schema` and `expected_database_role`,
both-or-neither. Both use lowercase ASCII `[a-z][a-z0-9_]{0,62}` without PostgreSQL
identifier truncation; application schema also rejects public, information_schema
and pg_* namespaces. `load_runtime_settings` requires the pair for staging and
reports only a safe storage error. Explicit local legacy and direct synthetic
RuntimeSettings models may retain neither. The template intentionally leaves
required deployment values empty. Each API/worker process using a different DB
role needs its matching trusted config and DB URL environment value, while sharing
the intended application schema and total connection budget.

Configuration assertions do not prove PostgreSQL binding or least privilege.
K04 supplied `verify_runtime_binding(pool, storage, strict=environment == "staging")`
and managed_runtime integration; K03 supplied the same guard in managed_worker.
API checks immediately after pool acquisition, before auth admission; worker
checks before schema readiness/clock/supervisor. Both retain startup deadlines.
K02 adapted only its synthetic lifecycle fixtures: exact checker arguments,
binding denial before downstream effects, cancellation propagation and pool cleanup.
The helper's DB-fact cases use test doubles; the real-DB split test uses explicit
local legacy settings without a bound role. This does not qualify the intended
non-owner runtime DB profile or staging. K04/K05 own that real-profile check.

Handed-off production hashes used by the final runs:

- settings: `3e5207421c6e1632b08c00ac2c09f6046896a6485c3e2e396161be36dda2a99f`
- runtime_binding: `54f36cf8e9fbc22650f6cb6979f12da3a7967ae83ad31eada3c4f3e595ca527f`
- managed_runtime: `9d796e81d29a4304d792326e2198cc9745a5eb3c219b283f790c5e64ac490bf0`
- managed_worker: `3b1296289efa23c07e38f6539222c7e9419d3e3c1eb8518d2654087f5f528919`

Final combined execution: **324 passed in 35.14s**, no skips: all K02 unit files,
K03 worker binding/session tests, K04 binding-helper tests and the existing HTTPS
and PostgreSQL process integration tests. The HTTPS test now exercises
byte/count excess through real TLS/h11, privacy and a subsequent healthy request,
alongside Host/forwarding/JSON/slow-body/deadline checks. The backend is synthetic;
the PostgreSQL test uses its own disposable schema, actual session rotation and
separate worker lifetimes. Afterwards the API lifecycle test was extended with a
negative binding case: **2 passed in 4.81s**, covering denial and cancellation.
These counts overlap; do not add them. Ruff/C901 passed for both changed source
files and all five changed test files; strict mypy passed for composition/settings.
The blank staging template returned expected exit 2.

The final before/after manifest covers **671 files**, zero changed paths:
`Đồ Án 3/k02-verification-20261110/binding-caller-manifest-d9d748b3e5574d1ea64e21e9ba606a29.json`,
SHA-256 `bdc279fe79d5c2879366d799c64afb374d58afb3d7ac15b0a47f6a386c3dba5f`.
Log: `Đồ Án 3/k02-verification-20261110/transport-binding-final.log`.
The subsequent API test manifest covers 669 files, also zero changes:
`Đồ Án 3/k02-verification-20261110/binding-caller-manifest-603c05a7586448799fd737fba01ea928.json`,
SHA-256 `85397e7aa6b91eebac80b31f2b8f2fea70678a42f6e4d94110ce2fe9a8041da7`;
log `Đồ Án 3/k02-verification-20261110/api-binding-final.log`.
Paths are beneath `C:/Users/Trung Kiên/Documents/ChatGPT/`. Earlier passing runs
observed concurrent K05 catalog and K04 review/workspace edits; those were recorded
without claiming a stable whole snapshot. Both final runs above had zero changes.
No stage, commit, push, merge, dependency install or deploy occurred.

Bu's supplied ZIP and direct reply were reviewed and forwarded to K04/J01:
create-workspace UX is acceptable pending Bảo's product confirmation;
can_create_workspace is withdrawn; invite uses pasted code, never a URL token.
Target-user lookup, pending invitations, distinct invite errors, workspace/grant
reads, member display names/pagination, the >200-member case and DELETE-body
semantics remain K04/J01 review items, not K02 implementations in this patch.
Bu did not name the three missing error codes; request their exact names/mappings
before changing the shared table. Bảo still needs to designate the environment
owner and supply the agreed origin/TLS/proxy/DB/replica inputs without credentials
in chat. Local test TLS does not establish that deployment.

## K04 integration handoff received — 2026-11-10

K04 handed off the canonical workspace/authorization integration and its review
package. K02 verified the received manifest checksum and all **32 owned-file
hashes** against the current worktree; no mismatch was found. Manifest:
`Đồ Án 3/k04-verification-20261110/k04-verified-files.json`, SHA-256
`9edf94403a31861e515296360951a3033d1dae546a6ec5b9a6ff11a23b97b956`.
Review ZIP: `Đồ Án 3/k04-verification-20261110/k04-review-20261110.zip`, SHA-256
`3e4331c82bb4a4534ac5d9075f9d1dbf3e40207504d72033ceef6e0ad0aac64b`.
Both paths are beneath `C:/Users/Trung Kiên/Documents/ChatGPT/`.

The earlier statement that first-owner/invite had no implementation is now
historical. K04 supplies eight HTTP operations and additive models/schema for
workspace creation, membership, ownership transfer, invitations and source
grants. Explicit workspace creation by an enabled, verified, pre-provisioned
account without active membership creates that workspace's owner atomically;
login alone does not grant global ownership. Invitations target a provisioned
verified user_id, not a guessed email-domain membership. Review remains needed
for these choices, the onboarding UI, role/action/object scope and error handling.
No K02 copy of the repository, auth store or contract was created.

K04 reports 1201 unit passes, 244 caller passes, 17 PostgreSQL/HTTP passes and two
split-worker/HTTPS passes, on their individually recorded snapshots. These counts
overlap and are **peer-supplied evidence**, not new K02 executions. Its earlier
split-worker test hash differs from the later K02 rotation-extended test; that
owner update is explicitly recorded in K04's manifest. K02's own latest focused
caller result remains 75 passes on the 651-file unchanged snapshot below.

Current capabilities in K04's composition advertise only its implemented upload
slice under current RBAC. The older notes that all capabilities are disabled do
not describe that newer composition. This does not establish shared deployment
or accepted consumer behavior. See `k04-ownership-handoff.md`,
`k04-role-action-scope.md`, `k04-workspace-runbook.md` and
`k04-verification-20261110.md` for actual policy, limits and evidence.
K02 did not edit K04 production files or rerun K04's full test suites here.

## Worker rotation caller handoff — 2026-11-10

Ownership was confirmed before this continuation: K03 owns the worker-only
session adapter and `managed_worker.py`; K04 owns the active canonical RBAC,
repository and route integration. K02 changed only its separate-process caller
test and this evidence document, leaving their production code unchanged.

K03 handed off these verified SHA-256 values:

- `hosted_auth/worker_sessions.py`:
  `91566a7b4db859ae60b1a2161b2718ed9b126fd27aedc5fa1e65fd04bcae9058`
- `hosted/managed_worker.py`:
  `4c75819cf8818bad96f480a9e7f067b642d763971fca59964464253c2f3f21b5`

The K02 PostgreSQL process test now renews the synthetic session through the
actual CSRF-protected HTTP renewal adapter after enqueue and before worker
startup. On both worker lifetimes, the queued job completes using its captured
context while every earlier cookie receives HTTP 401 from the API. Current
credentials retrieve the persisted output. The existing K04 migration is adopted
only inside that test's UUID schema, with a valid owner and analyst fixture.
The test still uses seeded identity, in-process API HTTP and real child workers;
this is not browser/IdP, arbitrary crash or global invalidation acceptance.

Final focused caller run: **75 passed in 10.59s**, exit 0, no skips; Ruff including
C901 passed for the updated K02 integration test. This includes K02 worker and
managed lifecycle tests, K03 worker-adapter tests, J01 readiness tests and the
real PostgreSQL K02 caller test. It is not a new full K02/K03/K04 regression and
is not additive to earlier counts.

Tested identity: HEAD `2bbcdc2a7637c840babd8b71910306c64072bc4b` plus WIP. The
before/after manifest contains **651 files**, with **zero changed paths** in the
final run. The caller test SHA-256 was
`5c29e5918dd5e8f1df84b55f411be32b9d00caa45a32de539d1116556f1d5f7d`.
Log: `Đồ Án 3/k02-verification-20261110/worker-callers-rotation-final.log`.
Manifest: `Đồ Án 3/k02-verification-20261110/worker-caller-manifest-73833343ecc54883b379ecf2d82863e1.json`,
SHA-256 `005cd8bd920bb4a98067068be45d9d862f2366018067527764ba4289436a30c2`.
These paths are beneath `C:/Users/Trung Kiên/Documents/ChatGPT/`.

An earlier passing focused run observed concurrent changes in K05 legacy-import
files; those were recorded separately and not claimed as covered. The final
run above had no such changes. No stage, commit, push or merge was performed.

## Latest boundary qualification — 2026-11-10

The API worktree remains at its existing location under `Đồ Án`; the newly
selected `Đồ Án 3` workspace contains verification artifacts only. No checkout
was cloned or copied. Scoped patches were checked against the existing WIP
before application. No commit, push, staging deployment or external message was
performed.

`ProcessApplication` now applies `api.request_timeout_seconds` (provisional
operator default 30 seconds) around the complete HTTP guard/route/response-send
path. Before response headers, expiry produces a fixed shared Problem 503 with
no-store and a generated request ID; HEAD suppresses its body. Error-response
writes have a separate bounded backend-timeout budget. After headers have begun,
timeout propagates so the server closes the incomplete response rather than
emitting another status. External cancellation propagates. This is a cooperative
ASGI execution deadline; it does not preempt blocking CPU, bound TLS/header
handling before ASGI, or undo a database effect already committed. Retrying a
job after an unknown response outcome must preserve the same idempotency key.

The job HTTP boundary now revalidates outgoing JobReceipt and Job through the
existing producer models, checks the requested workspace/job scope, checks the
existing output envelope and bounds all response bodies. It honors the configured
JSON nesting limit before enqueue and returns the appropriate Allow header for
unsupported methods. No shared model, fixture, route or public code was added.
Terminal-job result/error semantics still await J01 review; these guards do not
approve v8 or completely validate every operation-specific result payload.

Final checks for this continuation:

- All current K02/J01/K03 unit files: **912 passed in 69.92s**, no skip.
- Real PostgreSQL separate-worker test and verified loopback HTTPS test:
  **2 passed in 11.95s**. The HTTPS test also exercised the outer deadline with
  a stalled synthetic route; the DB test removed its own UUID schema.
- Thirty new deadline/job-boundary cases are included in the unit result, not
  additional passes. They use synthetic authority and the actual CSRF library.
- Ruff including C901 and strict mypy with `--follow-imports=silent` passed for
  composition.py, settings.py and job_http.py. New tests passed Ruff/C901 too.

Logs are in
`C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/k02-verification-20261110/`:
`hosted-unit-final.log` and `postgres-https-final.log`. Older results below
retain their original scopes and are not additive.

Remaining acceptance is not solely blocked by Quân. Quân supplies v7 consumer
results/client SHA and agrees browser route/error/redirect/logout behavior;
K03/K04 coordinate account, first-owner and invitation rules/implementation;
Bảo or the designated operator confirms permitted hosting, real edge/header
policy, static CSP/cache, supervision and joint schedule. GitHub CI has not run
because publication remains unauthorized. Existing acceptance trackers remain
open; no K02 task has been marked passed by these local checks.

## Current API/worker split — 2026-11-10

Earlier split regression: **830 passed in 103.60s**, including all current
K-02/J-01/K-03 unit tests, the real PostgreSQL separate-worker test and the real
HTTPS loopback test. No test in this run was skipped. Ruff passed for the changed
files; focused strict mypy passed for the worker, pool and managed API modules
with `--follow-imports=silent`. Workflow YAML parsed successfully; this is
not GitHub Actions execution. These results overlap all earlier test counts.

This section supersedes earlier implementation-stage notes below. The managed
API now opens only its API pool and auth/HTTP resources. `compose_upload_runtime`
no longer accepts `operations` or starts a scheduler. The worker command owns its
worker-role pool, bounded scheduler and subprocess supervisor. No job execution
or worker thread is started by the API factory.

The worker checks job/source schema and the database clock before starting its
scheduler. It handles SIGTERM (and SIGBREAK on Windows); Ctrl-C also cancels its
resource lifetime. A stop allows in-flight work the configured worker grace
period, then cancels it for lease recovery and closes supervisor before pool.
Failure or grace expiry returns a nonzero worker exit. A broken driver or OS
can still defeat cleanup; this is not an OS resource-isolation guarantee.

Local PostgreSQL verification passed with a unique temporary schema: the API
enqueued a preview job while no worker was running, a different interpreter
process consumed it, and the API retrieved the stored output. Stopping that
worker and starting a new process completed another job. The schema was removed
in the test's cleanup. This used a seeded synthetic session and ASGI HTTP calls;
the child test harness invokes `run_worker` with a stdin stop controller rather
than sending OS signals to the production CLI. The production CLI's signal and
error handling have separate unit coverage. It does not qualify forced crash,
host loss, a real browser login, shared staging or a production migration.

An actual Uvicorn HTTPS loopback test also passed with a generated temporary
certificate and client certificate verification. It checks Host rejection,
discarded untrusted proxy headers, no-store, duplicate JSON rejection and body
timeout while a concurrent health request succeeds. It uses synthetic routes;
reverse proxy behavior, slow headers/whole-request edge deadlines, static asset
CSP, external certificates and browser behavior remain unqualified.

The schema-response check now requires jsonschema explicitly; a missing validator
fails collection rather than silently skipping contract evidence. The new
`.github/workflows/hosted.yml` installs the declared hosted/dev dependencies,
validates v7 fixtures and runs backend tests with a disposable PostgreSQL service
and local HTTPS. The existing full-test CI install also includes the hosted
extra needed to collect these tests. These workflow definitions have not run on
GitHub because this work remains uncommitted/unpushed. Consumer execution using
Bu's `J01_FIXTURES` support still awaits the reviewed client commit and v7 result.

### Operating the two processes

Use the same approved runtime configuration and database/schema for API and
worker, and a stable private staging root for worker processes. No example file
is deployable unchanged. Use an environment containing installed project and
hosted dependencies; the local temporary dependency directories used in checks
are not a distributable runtime or transitive lock.

API process (paths below stand for actual approved files):

```powershell
& $Python -B -m aletheia_lab.hosted.server --config $RuntimeConfig `
    --auth-policy $AuthPolicy --tls-cert $TlsCertificate --tls-key $TlsKey
```

Worker process, in a separate terminal or managed service:

```powershell
$env:ALETHEIA_HOSTED_CONFIG = $RuntimeConfig
$env:ALETHEIA_HOSTED_AUTH_POLICY = $AuthPolicy
& $Python -B -m aletheia_lab.hosted.managed_worker
```

The worker needs the declared DB environment variable; it does not need OIDC,
CSRF, admission-signing or login-proof secrets. Provision its environment with
only the required settings rather than copying the API's secrets. Windows DB
I/O uses a Selector loop; only the worker process owns the Proactor bridge for
its CPU subprocesses. Each worker command starts one scheduler process; operators
must keep the number launched consistent with `worker.processes` and the total
DB budget. The server command starts one API process per invocation.

API readiness probes its own repositories/admission/schema/clock. It does not
prove that an external worker is alive, has spare capacity, or will finish an
accepted job. External worker supervision and queue-age/availability monitoring
remain operational work (K-09/K-14/K-18); capabilities remain disabled as before.
Worker hosts with different staging roots and production object storage are not
qualified by the same-host process test. Earlier J01 local experiment scripts
that pass `operations` to the composer require migration to the separate worker;
the new integration test is the current reproducible split-process check.

### Remaining K-02 acceptance

| Step | Current evidence | Still required |
| --- | --- | --- |
| K-02.01 | API-only factory, worker command, lifecycle tests and a real local DB job across processes | Approved topology, worker supervision and deployment/failure qualification; no overall acceptance claim |
| K-02.02 | Strict body/model checks and selected serialized responses validated against v7 | Bu's v7 consumer result, full operation/error review and accepted contract |
| K-02.03 | K03 auth/session plus K04 workspace/first-owner/invite implementation with their scoped local evidence | Quân/J01 review of onboarding/account/invitation semantics and the shared UI journey; no joint acceptance yet |
| K-02.04 | Real loopback HTTPS and slow-body/concurrent health checks | Actual edge/TLS/static policy, header and total-request deadlines, trusted-proxy and browser checks |
| K-02.05 | Backend/schema/DB/HTTPS CI definition prepared | GitHub CI execution after authorized publication and Bu's consumer commit/test on the same fixture version |

Existing assignment tracker acceptance states remain open; this document is
evidence, not a replacement tracker. No commit, push, deployment or external
message was performed.

## Historical implementation and supplied checks

Prepared on 2026-11-08. The preflight passed its first focused checks as
recorded below. Process-resource lifecycle checks also passed in a separate
run. Read-backend tests and strict mypy passed; Ruff passed after an
import-formatting fix in its test module.
There is no deployed runtime or working identity/membership/
storage implementation. Kien selected continued code and offline checks
because the actual service environment is not yet decided.

## Scope and ownership

K02 owns `hosted/**`, its tests and this runtime configuration. The J01 shared
models, fixtures, schema/export scripts and J01 documents are unchanged.
`pyproject.toml` is also unchanged: the existing J01 jsonschema dependency WIP
is preserved. No additional package was installed or declared in this step.

`python -m aletheia_lab.hosted check-config --config PATH` reads at most 64 KiB
of TOML and validates only configuration and declared secret-variable values.
It does not read `.env` implicitly, contact an IdP, connect to PostgreSQL,
create object directories or start API/worker processes. Success explicitly
reports `dependencies_probed=false`, `runtime_started=false` and
`hosted_ready=false`. Do not use it as a readiness probe.

## Configuration boundaries

`configs/hosted/staging.example.toml` deliberately has blank required origin,
IdP/client/callback and storage-root fields. It must fail preflight until the
operator supplies actual registered settings. URLs and budgets in tests are
synthetic and make no network requests. The defaults are operational starting
points, not measured sizing, a product limit decision or a 50k capacity claim.

The TOML contains secret VARIABLE names only. The DB DSN comes from
`ALETHEIA_HOSTED_DATABASE_URL`; a confidential client declares its own
`ALETHEIA_HOSTED_*` secret reference. No fallback uses the local bridge's
`DATABASE_URL` or an unrelated provider key. Runtime secret values use masked
Pydantic `SecretStr`; preflight output excludes URLs, credentials and paths.
Configuration failures report safe known section names, never raw TOML,
validation input, DSNs, IdP responses or exception diagnostics.

Staging origins, callback and issuer must use HTTPS; plain HTTP is allowed
only in explicit local mode with loopback URL hosts. Callback origin must
match the configured app origin. This checks URL shape only: issuer ownership,
discovery/JWKS, client registration, scopes, PKCE/state/nonce, cookie/CSRF and
provider authorization must be implemented and verified with the actual IdP.

PostgreSQL DSN validation checks syntax and a database name, not credentials,
TLS, connection availability or schema. The absolute object root is a proposed
retained filesystem root; it is not an implemented object store, not proof of
durability and not permission to read arbitrary hosted paths from a browser.
No persistence backend is created or migrated by these settings.

The planned connection count includes every configured API process, every
separate worker process and the admin reserve. There is no hidden overflow
pool in this plan. Actual pools and their limits still need to be implemented
and measured. Worker process/job-slot settings are a plan, not a job consumer.

## API server options plan

`api_server_options` returns explicit Uvicorn options without importing or
starting Uvicorn: loopback bind by default, asyncio/h11/ASGI3, no reload,
separate configured API process count, bounded concurrency/backlog, keep-alive
and graceful shutdown values. Multi-process serving will need an importable
factory that creates real process-local repositories and closes them safely.
No runtime composition or supervisor is provided by this options dictionary.

Proxy-header processing is explicitly disabled and forwarded trust is empty;
ambient `FORWARDED_ALLOW_IPS`/`WEB_CONCURRENCY` do not determine the plan. A real
edge and trusted-proxy policy must be agreed and tested before changing that.
Access logging is disabled in the plan to avoid URLs with OIDC codes entering
default access logs. Redacted application/audit logging still needs work.

The h11 incomplete-event buffer, Uvicorn concurrency/backlog and keep-alive
settings do not establish complete header/body/whole-request edge deadlines.
The JSON body helper budgets still need to be wired into mutation routes;
TLS, static asset CSP/cache, slow clients and proxy spoofing require actual
server/edge tests. No K02.04 acceptance is implied.

Uvicorn options were checked against the official
[settings documentation](https://uvicorn.dev/settings/). Pydantic's separate
[settings extension](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
is not required: this step uses the already-installed BaseModel and an
explicit bounded TOML/environment boundary.

## Next integration work

After the focused offline checks: select and pin the maintained server/auth/DB
runtime in an isolated environment; implement repository-backed session and
membership enforcement; compose and close real pools; then run HTTP checks.
OIDC/auth/onboarding and durable job/persistence work are still missing.
Before any staging call, obtain the actual origin/issuer/client/redirect,
DB/storage namespace, operator, cost/traffic permission and stop conditions.
J01 consumer review remains open and shared contract changes need coordination.

## Supplied preflight verification

Kiên supplied PowerShell output for the six related J01/K02 test modules:
**142 passed in 3.06s**, exit code 0. Ruff including C901 passed, exit code 0.
Strict mypy reported no issues in six source files, exit code 0. The checked-in
blank staging template printed `Invalid hosted configuration: identity,
public_origin, storage`, exit code 2, matching its intended rejection.

These results precede the lifecycle work below and the addition of separate
startup/resource-close timeout fields. They do not verify those changes and
do not prove connectivity, hosted sessions, persistence, API/worker process
separation under a real server or staging acceptance.

## Process-resource lifecycle and offline verification

`hosted/composition.py` adds `ProcessApplication(settings, resources)`, an ASGI
wrapper requiring an explicit async resource-context factory. It invokes that
factory in the process's startup event loop, probes the backend within the
configured startup deadline and only then publishes it to the HTTP adapter.
Before startup, after failure or after shutdown, the adapter has no backend
and cannot report successful readiness.

The factory must use proper async context managers to release partially
acquired resources, including acquisition cancellation. The wrapper uses
`AsyncExitStack` for entered contexts, clears the backend before cleanup,
bounds resource closing, and emits fixed lifespan failure messages. External
cancellation propagates after cleanup is attempted. Cleanup failure/timeout
is a failure, not a guarantee that a broken driver released all resources;
runtime supervision and real driver tests remain necessary.

Each process/event loop needs a new wrapper and fresh factory-created pools.
Instances cannot restart their resource lifetime or use an active backend on
a different event loop. No global DB pool is created at import/construction.
API startup neither starts a domain worker nor imports a synthetic backend;
worker supervision/execution is still unimplemented. The exposed body-budget
configuration still needs to be wired into future mutation handlers.

The new lifecycle tests inject synthetic resource contexts and run in-process
without a server, DB, IdP or network. They test startup rejection, partial
cleanup, cancellation, shutdown failure, send failure and one-shot ownership.
They do not establish real readiness, auth, membership or persisted sessions.
The CLI still supports offline `check-config` only, not a production server.

The lifecycle follows the official
[ASGI lifespan protocol](https://asgi.readthedocs.io/en/latest/specs/lifespan.html)
and uses Python's
[AsyncExitStack](https://docs.python.org/3/library/contextlib.html#contextlib.AsyncExitStack).

Kiên then supplied a distinct run for lifecycle/settings/adapter regression:
**54 passed in 2.33s**, exit code 0; Ruff including C901 passed, exit code 0;
strict mypy reported no issues in seven source files, exit code 0. These tests
overlap the earlier 142-test run and the totals must not be added together.
Only the synthetic in-process lifecycle is established by this evidence.

## Read-backend logic prepared for offline checks

`hosted/read_backend.py` implements Session and Capabilities read decisions
over two explicitly injected repository interfaces. It does not implement
those repositories or choose a driver. A real auth/session adapter using a
maintained auth implementation must verify cookies, enforce effective idle/
absolute/rotation policy and return current server facts. No cookie is parsed,
signed, created or stored by this module, and no fallback produces identity.

The read backend rejects absent, expired or revoked sessions and disabled
accounts, then serializes using the unchanged J01 producer models. Capability
reads require a current same-scope session/membership snapshot, check actor,
context and workspace, and reject revoked membership. Real repositories must
provide the documented consistent scoped read; the interface alone does not
implement transactions, persistence or a revoke propagation guarantee.

All product writes, analysis, sources and exports remain unimplemented here.
Capability actions and supported-mode lists are empty; feature keys are absent
(the draft client semantics treat missing features as off). Even an owner role
does not enable an unimplemented operation. This is a read-only implementation
stage, not disposition/acceptance of the mandatory core product journeys.
Limit values must be supplied explicitly; this step does not settle 2000/4000.

The new tests use synthetic repositories and actor/limit examples from J01 v6.
They keep fixture files unchanged and supply their own deterministic future
session expiry instead of treating the dated fixture as a live credential.
Tests cover current state, scope mismatch, role changes and revocation between
reads, plus safe HTTP error mapping. Kiên supplied a distinct run with 41 tests
passed in 3.43s and mypy passing for eight source files. Ruff reported one I001
import-formatting issue in the new test file; that import block was subsequently
reformatted. Kiên reran Ruff including C901 and received `All checks passed!`,
exit code 0. No IdP,
DB, pool, server, onboarding, CSRF mutation or 5-second revoke measurement is
claimed. Selection and integration of the real repositories remain open.

## Independent transport concurrency checks

`test_k02_transport_concurrency.py` adds offline tests of concurrent calls to
the existing ASGI body reader: a stalled receive yields to another reader on
the same event loop, and cancelling one reader does not cancel another. Kiên
supplied output: **2 passed in 2.02s**, pytest exit 0 and Ruff exit 0. These
tests do not cover a listening HTTP server,
proxy buffering, connection limits or slow headers; they cannot close K-02.04.

## Host and forwarding boundary pending verification

`hosted/edge.py` and `ProcessApplication` now check a single Host header
against the configured public origin and discard `Forwarded`, `X-Forwarded-*`
and `X-Real-IP` before passing requests to the application. Rejected hosts
receive a fixed 400 with no-store; this is an edge rejection, not an agreed J01
Problem payload. Health checks must use the configured Host too.

The ASGI server must disable proxy-header processing before the scope is built;
middleware cannot recover the original client/scheme after a server has already
trusted a forged header. Trusted proxies, HTTPS termination and any health-check
host policy still require actual infrastructure configuration. The new tests
were run in-process as recorded below; they do not establish deployed edge
security or full K-02.04 acceptance.

Kiên supplied the edge/lifecycle/settings/adapter run: **72 passed in 8.76s**,
pytest exit 0; Ruff including C901 passed, exit 0; mypy reported no issues in
nine source files, exit 0. These in-process results overlap previous runs and
do not qualify a real proxy, TLS or server deployment.

## One-shot report worker prepared for verification

`hosted/worker.py` is a separate-process entrypoint for the existing validated
`ProductView` and domain report renderer. It accepts trusted bytes on stdin and
emits JSON or Markdown on stdout, with provisional byte limits and fixed safe
errors on stderr. It does not read a browser-supplied path, call an external
provider, accept a job durably or expose an HTTP route. PDF is deliberately not
enabled here because Vietnamese PDF correctness is still open under K-12.

The caller must already have authorized and loaded the exact immutable result.
It must enforce process admission/deadlines, discard any partial/nonzero-exit
output, verify bytes and recheck authorization before publication. The output
limit is checked after rendering and does not cap peak renderer memory. A broken
pipe can receive partial bytes even when the worker reports failure. A bounded
internal subprocess supervisor was subsequently added below. A durable queue,
job cancellation/fencing, persistence and API/worker deployment remain
unimplemented. No feature flag or core export journey is claimed.

`test_k02_worker.py` starts local child Python processes with synthetic data to
compare the output with the existing domain renderer and checks failure/budget
behavior. Kiên supplied a run with **6 passed in 4.80s**, Ruff pass and strict
mypy pass for 10 source files. There was no DB/IdP/edge call.

`worker_supervisor.py` has since been added. Kiên supplied a run where its
worker/supervisor tests passed (**12 passed in 16.27s**) and strict mypy passed
for 12 source files. Ruff found one SIM105 issue; the equivalent cleanup syntax
was updated afterward. A targeted rerun then passed **6 tests in 8.39s**, Ruff
including C901, and strict mypy for 12 source files, all exit 0. These runs
overlap and are not additive. The supervisor
starts only the fixed internal report-worker module, limits concurrent admitted
children and input bytes, applies an execution timeout, and reaps children on
timeout/cancellation. A slot stays reserved until its child task finishes.
The child receives a minimal OS environment rather than inherited DB/IdP/API
credentials. Process creation itself is shielded during cleanup; if the OS
never completes spawn or reap, cleanup can still wait and the slot remains
reserved. OS resource limits and external supervision remain necessary.

The supervisor returns in-memory bytes only: it is not a durable queue, job
receipt, object publication, authorization or an enabled hosted capability.
The future caller must perform the scope checks and publication/integrity
workflow described above. Offline supervisor tests start local subprocesses
with synthetic data; they do not exercise a durable queue or real storage.

Kiên subsequently ran all 10 K02 test modules together with the two existing
J01 model-test modules: **209 passed in 13.94s**, exit code 0. This combined run
includes earlier tests; counts are not additive. It is local regression evidence,
not producer-consumer acceptance or a hosted release.
After that run, a new supervisor heartbeat test was added. Kiên then supplied
output: **7 passed in 10.17s**, pytest exit 0 and
Ruff exit 0. It checks event-loop schedulability while a local child renders a
bounded synthetic view; it is not a latency SLO, load test or HTTP/edge check.

## PostgreSQL pool boundary prepared for offline verification

`hosted/postgres_pool.py` prepares one process-local Psycopg async pool using
explicit API/worker size, bounded waiters, a separate pool wait timeout, and
explicit open/close. It reads only the DB secret reference, so a worker does not
need the IdP client secret. In staging mode the proposed pool boundary requires
an explicit `sslmode=verify-full`; local mode can use a local test database.
This is an implementation safety choice for review, not evidence that a server,
certificate, CA or staging network has been configured.

The independent `SELECT 1` probe checks only the supplied pool connection;
it does not check schema, identity, membership, migrations or full readiness.
Tests use injected synthetic pool objects. Kiên ran the pool, runtime settings
and lifecycle tests together: **53 passed in 6.77s**, Ruff passed, and strict
mypy reported no issues in 13 source files (all exit 0). This run overlaps
earlier settings and lifecycle checks and its count is not additive. Psycopg/its
pool package is not installed or pinned here; the real API, transactions,
driver logging/redaction, TLS and PostgreSQL behavior need separate testing.
In particular, libpq can also read `PG*` environment variables; the eventual
process launcher must constrain its environment before claiming isolation.
No new production service, schema, repository or capability is enabled.

On 2026-11-10, an additional in-process test exercised this pool boundary inside
`ProcessApplication`: a successful synthetic probe permits startup, while a
failed probe rejects startup, and both paths close the pool. The pool and
lifecycle test files passed together: **31 passed in 1.45s**; Ruff passed for
the pool test file. The first sandboxed attempt could not create pytest's temp
directory, so these checks were rerun outside the sandbox. This synthetic test
is not a production resource factory or evidence of this pool against a real DB.
The subsequent regression run of all 11 `test_k02_*.py` modules passed:
**191 passed in 19.22s**, exit 0. This count includes the new test and earlier
K-02 tests; it is not additive to prior runs or J01/K03 acceptance evidence.

The worktree also contains later experimental J01 runtime integrations and
separately reported local PostgreSQL/identity experiments. See
`j01-local-postgres-identity-progress.md` for their actual scope and unresolved
clock, login, and deployment issues. Earlier statements in this document describe
the revision at the time of their checks, not the entire current worktree.

An additional lifecycle test checks a scheduler failure after startup: readiness
fails, newly dispatched experimental routes are no longer used, shutdown reports
failure, and resources close. A K-02/J-01 focused run passed **19 tests with one
deselected**, and Ruff passed for the lifecycle test. The initial unfiltered run
had **19 passed, one failed** because the shared K-02 interpreter lacks
`itsdangerous`, needed by the deselected J-01 job/CSRF import. No dependency was
installed. This in-process check does not stop requests already in flight or prove
the behavior of a deployed worker, server, or real storage.

The existing isolated J01 CSRF dependency directory was subsequently located;
using it explicitly with the K-02 interpreter, the runtime lifecycle, J01 routes
and scheduler, and K03 CSRF test files passed together: **103 passed in 4.93s**,
exit 0, including the previously failing test. No package was installed. This
is an in-process integration check, not an actual edge, browser or staging run.
The experimental `compose_upload_runtime` currently starts its scheduler in the
API process; this does not satisfy the K-02.01 requirement for separate API and
worker processes and must be addressed before acceptance.

The K-02 HTTP adapter was also checked against the existing J01 v7 JSON Schema
with synthetic repositories. A combined run of the new schema-response tests,
read-backend and HTTP adapter tests passed **45 tests in 2.73s**; Ruff passed for
the new test. The tested responses were session, capabilities, revoked session
and revoked membership, with Problem responses checked against both producer and
consumer schemas. The run used the existing isolated JSON Schema dependency.
This does not cover every error or operation, Bu's consumer implementation, CI,
or an accepted contract. The test skips when jsonschema is unavailable; a future
CI job must install the dependency and treat a skip as missing evidence.

After integration work added an optional scheduler, the runtime was corrected
to report failed shutdown when the scheduler had already exited, including a
normal early return. Scheduler futures now accept the declared Awaitable type.
The lifecycle, J01 routes and scheduler tests passed together: **21 passed in
2.10s**, Ruff passed, and strict mypy passed for `composition.py`. This tests
in-process lifecycle only; it does not provide separate API/worker processes or
prove recovery of real jobs.

The experimental `ManagedRuntime` now also attempts to close already acquired
resources when startup is cancelled, then propagates cancellation. A synthetic
test cancels startup while admission preparation waits after pool acquisition;
the pool context closes and no startup success is sent. Together with existing
managed/lifecycle tests, **17 passed in 4.38s**; Ruff passed for the new test.
This does not prove cleanup if a driver ignores cancellation or fails to close.

`hosted/managed_worker.py` now supplies an independently invoked worker resource
lifetime, with a worker-role pool, clock check, scheduler and subprocess
supervisor. Synthetic success/failure checks verify supervisor closes before
the pool; together with scheduler and supervisor tests, **15 passed in 10.66s**
and Ruff passed on the new files. This is not yet a process entrypoint or a
deployed worker. The existing API composition still starts its own scheduler;
do not start both against the same queue as if the separation were complete.
Readiness/heartbeat, launch, independent process shutdown and real DB job
recovery still need design and verification before K-02.01 can be accepted.

The pool boundary follows the official
[Psycopg pool API](https://www.psycopg.org/psycopg3/docs/api/pool.html).

## Source audit: domain, repository and process boundaries

This audit reads the API worktree based on `2bbcdc2` plus uncommitted K02/J01
work. The target placement is a design direction, not implemented execution,
profiling evidence or a claim that these operations already support hosting.

| Existing entry point | Observed local behavior | Hosted boundary to implement |
| --- | --- | --- |
| `product/service.py:51` | `ProductService` is synchronous and constructed around a local store root; its methods call local product functions directly. | Do not call it wholesale from the ASGI event loop or treat the root/header as actor identity. Reuse domain invariants through hosted adapters. |
| `product/imports.py:134` | Preview obtains a local root grant, inspects that root and stores its canonical path in a local preview record. | Hosted upload/staging must supply scoped object references; parsing and inspection run in bounded worker processes. Do not accept a browser-supplied server path. |
| `product/imports.py:397` | Confirm reads local preview/lifecycle state and creates local project artifacts. | Preserve preview/version checks and atomic hosted publication; durable job ACK and real repositories are still required. |
| `product/refresh.py:505` | Refresh reads local state, reopens its local source and returns unchanged when the source hash matches. | Keep no-op and immutable-history invariants while replacing local-root access with authorized hosted source identity and worker execution. |
| `product/metric_analysis.py:44` | Metric analysis uses local snapshot/evidence, produces `local_evidence`, and persists through the local store; it does not call a provider. | Reuse calculation/invariants without claiming a hosted capability until persistence, authorization, execution and consumer checks exist. |
| `product/reports.py:19` and `:262` | Report rendering is synchronous; PDF converts non-ASCII text with `backslashreplace`. | Render heavy exports in bounded workers, recheck download authorization, and leave Vietnamese PDF correctness open under K-12. |
| `product/deletion.py:430` | Deletion coordinates local lifecycle/tombstone state and SQLite-backed scope checks. | Hosted deletion needs scoped repository transactions, durable fencing and retention policy; do not call the local store as a multi-tenant implementation. |
| `project/persistence.py:209` | `ProjectStore` opens SQLite, runs migrations and calls `verify_integrity()` in its constructor. | Hosted PostgreSQL/object storage needs scoped repositories and integrity verification at the right boundary; keep the local facade separate and do not block the API event loop on a whole-store scan. |

API code may validate bounded inputs and perform genuinely asynchronous I/O;
upload inspection, parsing, hashing, analysis and PDF work require an explicitly
bounded worker execution path. This placement must be verified with an actual
server, real storage and worker processes; the current process lifecycle helper
does not execute domain jobs, provide durable receipts or enforce fencing.

## Existing acceptance record refresh

Kiên supplied output for the November 8 update of the existing assignment
CSVs: `K02_TRACKERS_VERIFIED=True`, exit 0, and unrelated records unchanged.
Pre-write backups were retained as
`k02-03-Nghiem-thu-before-f16a44b289cf46cba1ae471b28b641fd.csv` and
`k02-09-Dau-viec-chi-tiet-before-f16a44b289cf46cba1ae471b28b641fd.csv` in the
baseline artifact directory. The CSVs retain blocked/not-run acceptance states;
their notes now permit independent offline K02 implementation while J01 review
continues. The CSVs remain the authoritative status records.
