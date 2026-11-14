# Hosted request boundary

## macOS follow-up — 2026-11-11

The maintained producer fixtures now pass all **248 local consumer checks**.
The hosted install includes the pinned Psycopg binary extra so the bounded
worker's minimal child environment can load its driver without inherited PATH.
Joint wire sign-off and hosted browser/staging acceptance remain open.

The Windows freeze-v2 receipts below retain their exact historical scope;
the later macOS source is a separate local snapshot.

## Recorded Windows K02 status — 2026-11-11

The backend is implemented on the existing uncommitted API worktree. Freeze v2
identifies **621 runtime/config files**, aggregate SHA-256
`488b140c055dfd4d64cb0f0358f84e3eee32c736c63266859811b528245d46f3`.
The historical holds and statements that adapters/runtime are absent below do
not describe this snapshot. Shared producer models remain J01-owned.

Current code includes the bounded HTTPS/ASGI boundary, separate API and worker
processes, canonical PostgreSQL/object persistence and durable jobs, current
session/membership/project authorization, lossless bounded graph continuations,
expiring durable exports and retained project tombstones. Schema migrations now
number **10**. This is implementation status, not joint acceptance of all routes.

- **24 real PostgreSQL tests passed** on PostgreSQL **16.15**, using one private
  disposable container. All 621 runtime files and nine selected test files were
  stable; the container was removed. [DB receipt][db-final].
- **63 loopback HTTPS/edge tests passed**, with Uvicorn 0.48.0/h11 0.16.0 and
  stable freeze-v2 hashes. [TLS qualification][tls-final].
- Final selected unit qualification: **1125 passed** in **133.74s** across
  **87 files**, with **zero failures, skips or deselections**. Production and
  selected-test Ruff passed; strict mypy passed for **81 hosted files**. All
  **1254 input files** were byte-stable and all 621 runtime files matched freeze
  v2 before/after. [Final unit qualification][unit-final].

The offline unified export describes **92 operations, 53 paths and 83 schemas**,
with 38 original P requirements and five conditional choices
(P22, P32–P34, P36). Schema/handler coverage does not close consumer acceptance.
[OpenAPI verification][openapi-final].

J01 separately records a current installed-wheel private Live run with real
Keycloak, HTTPS, PostgreSQL, restricted API/worker roles, native canonical data
and persistence after clean process restart. This evidence exists; it was not
rerun as part of the K02 DB/TLS checks. [J01 current report][j01-live]. Consumer
verification remains **244 passed / 3 failed, zero skips**. Quân still needs to
confirm the P02/P13/P22/P23/P25 wire alternatives and joint browser journeys.
Public/shared staging ownership and origin/TLS/IdP/DB/replica/budget inputs,
measured SLOs and joint acceptance remain open. No commit/push/deploy is claimed.

[db-final]: <C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/k02-verification-20261111/private-backend-integration-final.postflight.json>
[tls-final]: <C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/k02-verification-20261111/edge-checks-final-qualification.json>
[unit-final]: <C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/k02-verification-20261111/final-unit-rerun2-fa0876a955/summary.json>
[openapi-final]: <C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/k02-verification-20261111/hosted-openapi-final/verification.json>
[j01-live]: <C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/j01-completion-20261110-e/J01-final-kien-20261111.md>

## Historical notes — earlier snapshots retained

Historical unit evidence, superseded and not additive: [1114 passed / 10 failed][unit-prior-failed]; [1ceb checks passed but eight test-import files drifted][unit-prior-drift].

[unit-prior-failed]: <C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/k02-verification-20261111/final-unit-f12f14f768/summary.json>
[unit-prior-drift]: <C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/k02-verification-20261111/final-unit-rerun-1ceb8c12f4/receipt.json>

The following notes retain their original scope, dates and results. Earlier
counts overlap and must not be added; earlier development holds are superseded
by the current status above.

Update on 2026-11-08: Kiên supplied a J01 handoff reporting 118 related
contract/adapter tests and Ruff passing, then authorized K02 to resume work in
`hosted/**`, K02 tests and runtime configuration while shared-contract review
continues. This replaces the earlier blanket hold for K02 development below;
it does not approve a contract change or close joint/hosted acceptance. The
offline preflight work is described in `k02-runtime-config.md`; Kiên supplied
142 passing related tests, Ruff and strict mypy results for that revision.
Subsequent lifecycle/settings/adapter checks passed in a separate 54-test run;
strict mypy passed for seven source files. New read-backend logic is pending
verification. J01 keeps
ownership of shared models, fixtures and scripts. Kiên selected continued
offline work while the actual service environment remains undecided.

Status: request-body helper tests, Ruff and strict mypy passed according to
operator-supplied PowerShell output on 2026-11-06. The subsequently added ASGI
read adapter has not yet been tested and is on hold pending J01 review.
No live hosted runtime is configured. The JSON nesting-budget change below
also passed its focused checks in a later operator-supplied run.

`src/aletheia_lab/hosted/request_boundary.py` is separate from the loopback
dashboard bridge. `read_json_body` reads ASGI events under explicit byte,
event, idle-time and total-time budgets. It checks JSON content type, rejects
compressed input, duplicate Content-Length and conflicting length/transfer
headers, and checks actual length even when Content-Length is absent.

`decode_json_model` rejects invalid UTF-8, malformed or non-object JSON,
duplicate object keys at any level, non-JSON numeric constants and floating
point overflow. It delegates field, version and type rules to the supplied
Pydantic producer model. Tests reuse the draft J01 Session/Capabilities models
and synthetic fixtures. No second Session/Capabilities schema is introduced.
These response models are used to check the helper; they are not new mutation
input DTOs or proof that session data can be supplied by a browser.

`RequestRejected` is an internal exception carrying status, code and a fixed
safe message. It is not a new public error envelope. A future HTTP adapter must
map it through the shared Problem model and validate outgoing responses. The
new transport error codes still need consumer review with the draft contract.
No raw parser/model diagnostics or submitted values appear in its message;
logging and framework exception handling remain to be integrated and tested.

## Integration still required

- Use the existing J01 producer contract as the single schema source. Finish
  consumer review and create fixtures through the actual route serializers.
- Add the hosted ASGI entrypoint and maintained production server, authenticated
  server-session/membership repositories, configuration, health and readiness.
  Readiness must stay unavailable until the required dependencies actually work.
- Keep API and bounded worker processes separate in the same codebase. Route
  handlers must not run CPU, PDF, import or whole-catalog integrity work on the
  event loop. Durable submission/receipts require the job implementation.
- Configure and test the edge/server for TLS, trusted proxy handling, header and
  connection timeouts, HTTP framing, unread rejected bodies, limits, CSP and
  private cache policy. ASGI helper timeouts do not prove slow-client isolation
  or prevent an HTTP server from buffering input before the helper sees it.
- Implement every core operation with tests. Conditional features need an
  explicit disposition and an equivalent tested journey. False flags alone
  do not close a core requirement.
- Run producer and consumer checks in CI. Local parser tests cannot establish
  auth, onboarding, revoke timing, API/worker isolation or staging acceptance.

No changes are made to the existing local bridge, domain, metric analysis,
frontend, shared J01 contract or fixtures. No deployment settings or runtime
limits are claimed as agreed policy.

## Verification evidence

Kiên ran the checks in the API worktree on branch `feat/product-api`, based on
`2bbcdc2a7637c840babd8b71910306c64072bc4b` plus uncommitted files. Import output
resolved `aletheia_lab.hosted.request_boundary` to this worktree's source.

- Pytest for `test_k02_request_boundary.py` and
  `test_j01_session_capabilities_contract.py` completed at 100%, exit code 0.
  The supplied output does not include a numeric test summary.
- Ruff on the hosted package and new request-boundary tests, including C901,
  printed `All checks passed!`, exit code 0.
- Mypy 2.4.1 exited 2 before type checking: its cache setup tried to create
  `NUL\\3.11` and raised `FileNotFoundError` on Windows. The supplied
  `--cache-dir=NUL` invocation must be replaced with a real temporary directory;
  this is neither a successful type check nor a reported source type error.

An earlier invocation from the main checkout could not import or find the new
files. It did not test this worktree. These records describe checks Kiên ran
and supplied in chat; the assistant did not execute terminal commands.

This evidence covers local helper/model tests, not an HTTP endpoint or a
producer-consumer release. All integration requirements above remain open.

- The corrected mypy command used a unique real temporary cache directory and
  printed `Success: no issues found in 2 source files`, exit code 0. This covers
  the request helper and package initializer before adding `application.py`.

## ASGI read adapter draft on hold

Kiên clarified that J01 is waiting for Bu's feedback and dependent K02 work
must wait until J01 is finished. The already-written adapter and its tests are
retained as unverified WIP. Do not run its qualification, integrate it, export
its payloads as an accepted contract, or treat its route semantics as agreed
while that dependency remains open. No shared J01 models or fixtures were
modified by this work.

The independently verified request-body helper remains usable without the
J01 Session/Capabilities decisions. Its generic model argument introduces no
additional product schema. Pure transport/body-budget work and architecture
inventory can continue; Session/Capabilities wire behavior, auth/onboarding
and producer-consumer acceptance wait for J01. Edge deployment also waits for
the actual server/hosting configuration and its permitted test environment.

`hosted/application.py` exposes `HostedApplication(backend, limits=...)` as an
ASGI callable, separate from the local `HTTPServer`. It wires GET/HEAD
`/v1/session` and `/v1/workspaces/{workspace_id}/capabilities` to the existing J01
models. `/health/live` checks the adapter's response path; `/health/ready` calls
the injected backend's dependency probe. No backend means 503 for readiness
and protected routes. There is no default backend implementation or fabricated
session. A runtime and maintained ASGI server still need to be configured.

The backend interface receives cookie bytes for session lookup and the returned
server session plus URL workspace for capability lookup. Caller-supplied role,
identity, forwarded and local-bridge headers do not become identity inputs.
The actual auth adapter must use a maintained auth implementation and enforce
server expiry, rotation, revocation, current membership and resource policy.
The HTTP adapter cannot establish these properties just from a protocol.

Outgoing models are revalidated before serialization, including a check that
capabilities match the requested workspace. Private responses and Problems use
`no-store`; Problem bodies and request IDs are produced by the adapter. Backend
errors use fixed messages, dependency waits have an explicit deadline, and
cancellation propagates. New transport error codes and health paths are local
adapter choices pending joint review. No mutation, CSRF, login, provisioning,
job, worker, persistence implementation or production logging is added here.

`test_k02_hosted_application.py` uses a clearly named synthetic backend and
drives ASGI events in-process. It has not yet been run. These tests do not use a
listening HTTP server, real cookies, IdP, database, worker or staging. Successful
adapter serialization must not be reported as a working authenticated API.

## Independent JSON resource-budget change

The request helper now scans structural nesting before either recursive JSON
decoder runs. A root container counts as depth one; arrays and objects inside
it each add a level. Quoted text, escaped quotes/backslashes and brackets inside
strings do not add structural depth. Syntax, duplicate-key and producer-model
checks still run afterward for input within the configured budgets.

`decode_json_model` takes an optional positive integer `maximum_depth` with a
defensive default of 64. Byte limits are checked first. Excess nesting raises
the internal rejection `json_too_deep` with fixed text. The default is a helper
budget, not a jointly approved product limit or public error-code decision.
The eventual HTTP mapping and deployed limits still need contract review.

`test_k02_json_resource_limits.py` uses its own synthetic transport-only model
and requires no Session/Capabilities, real backend, external calls or server.
It checks deep input under the byte budget, exact depth boundaries, strings,
escaped characters, Unicode and interaction with syntax/byte validation.
The later PowerShell run supplied by Kiên completed both this module and
`test_k02_request_boundary.py`: **62 passed in 1.59s**, exit code 0. Ruff checked
the request helper and both test modules, including C901, with `All checks
passed!` and exit code 0. Strict mypy checked `request_boundary.py` with no
issues in one source file, exit code 0. The held Session/Capabilities adapter
was not part of these checks. No source changes followed this verification;
only documentation and the separate tracker-update utility were prepared.

This verifies the independent JSON helper, including its current depth budget.
It does not close the two-boundary schema/version requirement, HTTP integration,
consumer compatibility, edge behavior or any whole K02 acceptance step.

## Existing acceptance records updated

Kiên ran the tracker-update utility on 2026-11-06. Its supplied output reported
`K02_TRACKERS_VERIFIED=True`, exit code 0, and unchanged unrelated records in
both original assignment CSVs. A subsequent file read confirmed the K02 rows
contain the evidence and dependency notes: K-02, K-02.01, K-02.02, K-02.03 and
K-02.05 are `blocked`; K-02.04 remains `not_run`. No K02 row is `passed`.

The utility retained two pre-write backups in the baseline artifact directory:
`k02-03-Nghiem-thu-before-889e9867209d4301bbd09f36233cdfe8.csv` and
`k02-09-Dau-viec-chi-tiet-before-889e9867209d4301bbd09f36233cdfe8.csv`.
The assignment CSVs remain the authoritative trackers. This document records
implementation scope and supplied evidence, not a replacement tracker.

Dependent adapter/contract work stays on hold until J01 is finished, as Kiên
requested. The next integration step must re-read the accepted J01 contract
and consumer feedback before changing or qualifying the held adapter.
