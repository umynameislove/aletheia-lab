# K-03 auth transient investigation — 2026-11-11

The initial investigation was read-only over two existing J-01 runs. No journey
was rerun, service started/restarted, database queried or credential read.
Production sources matched the received baseline at that pre-edit checkpoint.
A subsequent explicitly approved diagnostic-only producer delta is recorded
separately below. A safe public error reason is not a root cause.

## Baseline and evidence binding

K-02 production-freeze-v2.json SHA256:
408b82b5103318c362f17735b5cfada161a35f8137c878e61db185cdddf4cc47.
Its runtime set is 488b140c055dfd4d64cb0f0358f84e3eee32c736c63266859811b528245d46f3,
recorded 2026-11-10T18:31:32.235307+00:00. It contains 619 source Python/SQL
files plus two hosted example configurations. Both runs' complete 619-path
installed-source manifests match these source hashes, and source-before equals
source-after in each run. At the initial pre-edit checkpoint, current hosted_auth sources and the inspected
runtime pipeline files also matched this baseline. The subsequent diagnostic
delta below supersedes that current-source comparison for its assigned files.
This is not a new whole-repository freeze.

Evidence root: C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3.

| Existing report | SHA256 |
| --- | --- |
| j01-completion-20261110-e/canonical-local-88da4240b21843b8893096526edaf079/report.json | 8fb0161b8f1abc92794bf17940c8d13e7f3f8c30999ddbced0440de357aa5ef8 |
| j01-completion-20261110-e/canonical-local-ff10dccabe8b474b9a8a03b92fefabd3/report.json | f46cbd21970818329876003351b0c3ca0c03646114c6e13fefa6952dd9ae1ce7 |

Both report failed_or_unknown, successful cleanup, no submitted jobs, seven
completed migrations and ready_before_jobs=true. They use the same verified
wheel 2e4ac6f3c0f705fe086919c60f353d410ea5c9a54d8d3fdc6c16f83af13ae879.
Neither is acceptance evidence for a completed login or joint hosted browser flow.

## What was actually observed

| Run | Safe observation | Not recorded |
| --- | --- | --- |
| 88da4240b21843b8893096526edaf079 | Login-start assertion observed HTTP 503. API and worker logs are empty. | Response content-type, Problem code, body category, elapsed time, failing internal stage and an event timestamp/correlation. |
| ff10dccabe8b474b9a8a03b92fefabd3 | Callback trace observed HTTP 303, return_root=false and allowlisted reason idp_unavailable. Assertion was Unexpected post-login return path. API log contains one fixed READINESS_DIAGNOSTIC=admission line; worker log is empty. | Response content-type/body/cookie checks, elapsed time, claim/exchange/commit outcome and correlation/timestamp tying the diagnostic line to this callback. |

The second API log is 32 bytes, SHA256
7ae9747c3d673403d27c89dc46c16a82c27aad168e6c0d43077313cda1d7a773.
No raw callback query, header, token, provider body or reset URL is reproduced.

The runner's current safe_readiness wrapper (run_canonical_local.py:185–195)
prints only a fixed readiness category. Its wait_ready loop also calls
/health/ready before login. Without timestamps/request correlation the single
admission diagnostic could belong to that earlier readiness polling. A later
ready_before_jobs=true does not establish readiness at the failing callback.
The historical driver itself has no recorded source hash in these 619-file
manifests; its current code explains the instrumentation but does not fill
missing historical response fields.

## Frozen pipeline interpretation

- LoginStartApplication catches TimeoutError as plain HTTP 503; it does not add
  a content-type. The enclosing AuthAdmissionApplication can instead return a
  503 application/problem+json service_unavailable on admission error or its
  whole-auth deadline. ProcessApplication can also return a 503 Problem on its
  request deadline. The first record cannot distinguish these producers.
- CallbackApplication maps TimeoutError (including OidcUnavailable and
  RepositoryUnavailable) to the fixed idp_unavailable 303. The same mapping
  applies when its pre/post clock_check predicate returns false or the
  callback/coordinator deadline expires. A 303 with this reason is therefore
  insufficient to attribute an IdP outage, or to determine whether the one-use
  claim, token exchange, account lookup or session commit was reached.
- The runtime predicate called clock is an aggregate readiness check, not just
  clock synchronization: jobs, upload profile, optional follow-up/audit/catalog/
  runtime operations, admission and finally the host/DB clock probe execute
  under an overall min(2, backend_timeout_seconds) deadline.
- The admission readiness category means its readiness call failed/returned
  false at that stage. PostgresAuthAdmission.is_ready can fail on row count,
  policy mismatch, last_seen after DB clock, database error or a query failure.
  It does not itself identify a backward clock or a provider failure.
- A failure of post-completion readiness can occur after the coordinator's
  durable commit. The error redirect alone proves neither session creation nor
  absence of a committed session; no corresponding database snapshot survives
  in these cleaned-up run reports.

Frozen api.toml copies set backend_timeout_seconds=12 and startup_timeout_seconds=30.
The frozen default request timeout is 30 seconds. Admission and the route each
have a 12-second whole-operation budget; the coordinator has its own 12-second
budget, and the aggregate readiness predicate has a two-second bound. These
nested limits are ceilings, not measured request durations or additive reserves.
The currently referenced external auth.toml sets the IdP budget to eight seconds
(SHA256 956ecbe551fdef044894e872961555e7f5b89a6decfa4b5d30225e857f7f71b9);
that policy file is absent from these historical source manifests, so its
current value is not asserted as the exact historical setting.

API final pool counters recorded one queued acquisition and 15 ms total wait
in 88da, one and 14 ms in ff10. They are process-wide final aggregates, including
startup; they are not request timing or a stage attribution. They provide no
recorded multi-second pool wait for either failing request.

## Conclusion and smallest next step

No auth-only correctness defect or concrete production-fix reproduction is
established by these records. Do not weaken state/PKCE, exact identity, epoch/
watermark, admission monotonic-time or readiness checks to turn either result
into a pass. No corrective algorithm/runtime patch was justified by that
investigation. The later authorized observation-only producer change is
recorded separately below.

The smallest useful follow-up is J-01-owned runner instrumentation, followed
only by a separately authorized controlled run: record operation name, status,
content-type media type, an allowlisted Problem code/public reason, monotonic
elapsed time and fixed per-request stage categories. Readiness observations
must distinguish pre/post and health polling. An existing post-failure read-only
clock/admission snapshot may be recorded separately as post-failure evidence;
it cannot prove the earlier failing instant. Keep queries, headers, cookies,
bodies, provider text and secret-bearing navigation out of logs.

If production runtime instrumentation is later necessary, coordinate it with
K-04 as the managed_runtime.py writer against preimage
177dcd7dd7117adcf961b8a2cc889c550ca1377004ee2a72233a126cc09cebd9 and
the frozen upload_runtime.py preimage
960ff6f5cc86c90d4f1d54f2fa36bc36d1da19d679c0a0a1d93d5eb24ebd6347.
Do not add a parallel repository, change budgets on speculation, or bypass
aggregate readiness. CLI, repositories, runtime and shared model writers
remain with their assigned chats.

## Historical pre-diagnostic source preimages and locations

| File / relevant location | SHA256 |
| --- | --- |
| hosted_auth/start_http.py:37–44, 77–84 | 2ad6e530d53fbe72b3fa9f298275e80e0ac69b334752de2a4266d782f84d308b |
| hosted_auth/callback_http.py:66–76, 91–98 | 0c6a040e829b2a9d521793424d732edfc04600fcddbcd067a6d6c62fcee9eb24 |
| hosted_auth/admission_http.py:88–118 | c3d0db5f55400c741379962427525b66d9b8b8469636c3e15a4848ba739cb0fa |
| hosted_auth/postgres_admission.py:34–45 | 2155eace75fdcb230c59500c03ea7cc61b5a6f7c14ba5b07637b9ad27d23418b |
| hosted_auth/login.py:323–330, 352–381 | b05f40998720a6be9b284811ca0f878d06b0d9384eb2ceb43b0d15cb5907793a |
| hosted_auth/clock_guard.py | a15dd7455c3dc762cbd4dd8325140c85eee1a8ea7a1df18a298f0574d676b12c |
| hosted/managed_runtime.py:173–208 | 177dcd7dd7117adcf961b8a2cc889c550ca1377004ee2a72233a126cc09cebd9 |
| hosted/composition.py:77–101 | 0932f7edbc47dbe756304debc049dd3e272c3581c7affbc64dc95377565bffc9 |

Existing offline tests distinguish clock denial/deadline from token exchange:
test_k03_callback_denials.py:226 and :239; readiness category/cancellation tests
are test_j01_readiness_diagnostics.py:141 and :176. No tests or live probes were
run for the initial document-only investigation; subsequent observer tests are
recorded below. No commit/push or Git mutation.

## Subsequent approved fixed-stage diagnostic delta

After the initial read-only findings above, root explicitly assigned K-03 the
three existing auth producer files and a private shared diagnostic helper.
This observation-only delta is later than production-freeze-v2. The historical
source/hash equivalence above applies to the received pre-edit checkpoint and
the two historical runs, not to the new producer hashes below.

The standard logger is aletheia.auth.diagnostics; the only message is
auth_stage_failed with fixed auth_route, auth_stage, auth_outcome and integer
elapsed_ms. Start/callback adapters observe readiness_before, coordinator and
readiness_after. The coordinator observes begin.oidc, begin.intent,
callback.claim, callback.exchange, callback.identity and callback.commit.
TimeoutError subclasses are unavailable; CancelledError is interrupted and
propagates unchanged. An outer adapter deadline therefore records interruption
of its inner stage without asserting a provider outage. Healthy paths and
other exceptions emit no diagnostic failure. A disabled logger or broken
handler cannot replace the original auth exception.

No callback values, headers, body, state/code/cookie, account identity, provider
exception class/message or URL enters these records; no exc_info is used.
There are no new awaits, retries, budgets, configurations or wire models.
All existing clock/current-policy/post-commit guards remain in place.

| Approved producer/test | New SHA256 |
| --- | --- |
| hosted_auth/_diagnostics.py | 3726c29a8a366a5285fac1d004c09c475ae5f168cd57d6532066479b56242366 |
| hosted_auth/start_http.py | 8499be077bfbc1835d6b621697ed5bbb0f3a31ad60abdd7ee0ee05e28726d269 |
| hosted_auth/callback_http.py | a885c596ee5aa41cbffa7e00aad0bdce378d871f4af4ffbfec592ee94565a7c9 |
| hosted_auth/login.py | aac1e443ad944f6de86aff93e2c1272394508fb5bfd1a26b2307c00e6593de09 |
| tests/unit/test_k03_auth_diagnostics.py | 4f7934ffe4144291da50534432f2b5934f41f9d985288a4c20d74288aa42ca60 |

The initial affected login/callback suite passed 138 tests in 22.94s. After an
equivalent suppress(Exception) style correction and an added blocked-logger
case, the final owned diagnostic tests passed 24 in 3.55s. Ruff including C901
passed all five assigned files; strict mypy passed the four producer files.
Counts overlap and are not additive. Tests cover unchanged start 503 and fixed
callback 303 presentation, no content-type/Problem-contract changes, pre/post
stages, six coordinator substages, timeout/cancellation propagation, secret
canaries, invalid dynamic stages, disabled/broken logging and quiet healthy paths.

The existing J-01 child runner disables logging globally at CRITICAL. A future
qualified one-shot must deliberately capture/enable only this fixed diagnostic
namespace while keeping provider/httpx/access/body logging disabled. This work
does not start that journey or certify its future results. Historical ff10's
exact failing stage and the request association of its admission log remain
unknown. CLI, runtime, repository, shared model and service settings were not
edited by this diagnostic delta.
