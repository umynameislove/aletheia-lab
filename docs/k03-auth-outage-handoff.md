# K-03.06 auth admission and IdP outage handoff

Current update 2026-11-10: shared PostgresAuthAdmission is implemented and
injected through the approved managed/upload runtime edits, with namespace/key,
schema/policy startup checks and readiness. Four-process/HTTP PostgreSQL quota
checks and versioned migration/adoption/policy rollout/rollback, least-privilege
API role and DB restart checks passed locally. SQL is included in wheel/sdist;
the installed CLI passed the dedicated rollout. See
k03-migration-package-verification-20261110.md and
k03-shared-rate-limit-verification-20261110.md for their exact earlier scope.
The latest continuation is in k03-local-completion-20261110.md.

The initial 453-test port-only slice had no production store or route. Later
slices added admission_http.py, shared storage and runtime integration. Those
historical observations must not be read as today's absence of integration.
Production migration/budget/key/log review, browser consumer and joint acceptance
remain open. No production/shared schema is automatically migrated.

## Implemented behavior

`AuthAdmission` requires an explicit `AuthAdmissionStore` and `RatePolicy`; there
is no default in-memory store or guessed production traffic budget. The store
must atomically check both an operation-wide and a subject budget using its own
clock, consuming both only when allowed. Counts are shared across workers and
not refunded after timeouts, invalid credentials or lost responses. The policy
uses fixed windows: adjacent windows can admit twice the limit near a boundary.
Use an explicitly reviewed budget, or agree a different algorithm with K-05 if
the required bound is a rolling interval.

Operations are a closed set: login_start, callback, renew, logout, logout_all.
Subject inputs must be masked 64-character lowercase digests derived by the
server. Never accept a user ID, role, account email, rate-limit key or proxy/IP
header as authority. Browser cookies alone can be replaced by an attacker; the
global budget is mandatory. K-02 must define the trusted edge source and any
keyed digest policy for anonymous admission. K-05 must bound key cardinality,
expiry and counter cleanup without allowing a reset to bypass active quotas.

The adapter bounds storage lookup time and rejects malformed decisions. Store
errors/timeouts fail closed; external cancellation propagates. Denial raises an
internal `AuthRateLimited` with bounded retry-after seconds and a fixed safe
message. It grants no identity and makes no business or IdP call. The implemented draft
HTTP 429/Problem/Retry-After mapping remains a J-01 review item, not an accepted
new wire contract.

`BoundedOidc` preserves the existing OidcExchange protocol and can wrap J-01's
`RefreshingOidc`. Explicit settings bound total call time and simultaneous
provider operations. Excess requests are rejected immediately, with no waiting
queue. It retries neither token exchange nor discovery itself and never sleeps
inside a request. A failed call opens an exponentially increasing cooldown:

`cap = min(maximum_backoff, initial_backoff * 2 ** (failures - 1))`

The actual delay is uniformly between half the cap and the cap. Both are bounded;
a failed jitter source uses the cap. After the delay, only one call probes while
the failed state remains. A successful authorization URL alone cannot clear
token-endpoint failure history; only a successful verified exchange can recover
it. A stale concurrent success cannot erase a newer failure. Token/claim rejection
remains terminal `OidcRejected`, without opening a provider-wide outage.

The concurrency/cooldown state is deliberately per worker/event loop. It limits
local resource use and refresh storms; it is not a distributed rate limit.
Inject one shared wrapper per issuer per worker and put shared `AuthAdmission`
before every auth path. Recreating wrappers per request defeats cooldown. The
provider remains responsible for trusted endpoints, bounded transport and no
hidden retries; the existing Authlib adapter meets those transport obligations.
Do not use this wrapper to authorize a business operation.

## Integration owned by K-02/K-05/J-01

1. PostgresAuthAdmission now supplies the real shared fixed-window store. K-05
   reviews/adopts its migration, privileges, retention and restore policy. The
   separate synthetic store remains test-only. Real multi-process/quota/transaction
   checks have passed in disposable local schemas; DB failover remains open.
2. Admission is now injected into login-start/callback/session-mutation boundaries.
   K-02/operator must approve traffic/resource budgets and deploy the schema/key.
   Validate source/shape
   and bound unauthenticated edge traffic before expensive DB/IdP work. Admission
   does not replace CSRF, session or membership checks. Logout does not depend on
   a healthy IdP and must still revoke server state before reporting success.
3. Wrap `RefreshingOidc` in `BoundedOidc` before injecting it into LoginCoordinator.
   Its total timeout must fit inside the coordinator/runtime backend deadline.
   This is now implemented in the separately approved `managed_runtime.py` edit;
   multi-worker/shared admission and real outage qualification remain open.
4. J-01 agrees public rate-limit/error/recovery semantics and accepted fixtures;
   Quân implements explicit retry UI and stops automatic redirect/poll loops.
5. Safe fixed-field rejection audit is implemented on a named INFO logger.
   K-02 still configures edge/application tracing to exclude cookies, authorization,
   callback queries/codes, nonce/state/verifier, provider response bodies, full
   request URLs and exception details. Existing fixed exceptions alone do not
   establish deployment log redaction. No external audit sink is configured here.

An allowed audit record contains a server request ID, operation, fixed outcome
category, duration, status and bounded retry-after value. Record counter/outage
failures so operations can distinguish rate limiting, provider failure and
storage failure. Avoid identity fields unless a reviewed retention/access policy
requires them. Never use raw provider error strings or `exc_info` as audit data.

## Recovery classification

| Internal outcome | Can the same callback be retried? | Safe recovery |
| --- | --- | --- |
| AuthRateLimited before claim | No automatic replay | Respect bounded wait, then initiate an explicit new login; preserve local rate limits |
| OidcUnavailable, total timeout or cancellation after claim | No; claim stays spent | Wait for cooldown/admission, then create a fresh explicit intent |
| OidcRejected, wrong issuer/nonce/signature or stale/replayed intent | No | End this attempt; show fixed failure and offer a fresh login |
| Unprovisioned/disabled/unverified account | No automatic provisioning or privilege grant | Use K-04 provisioning or the selected IdP's verified-email/account policy |
| Recovery/MFA needed | No app-managed password/reset bypass | Use the selected IdP's recovery flow and revalidate identity/MFA policy afterwards |
| Session/commit reply lost | No blind replay or claim release | Treat credential receipt as unknown; fresh login with agreed UI cleanup |
| Admission/session storage unavailable | No success or demo fallback | Report safe temporary failure; operator restores authority before a fresh operation |

Existing sessions are not converted into IdP tokens or periodically refreshed by
this group. Invalidating cached signing material never authorizes stale-key
fallback. There is no automatic redirect, background token refresh, password
service, public-IdP load test or external traffic in these checks.

## Verification and limits

Tests exercise two gate instances over one synthetic atomic authority, denied
operations with zero effects, malformed decisions, outages, deadlines and
cancellation. Controlled provider schedules cover capacity, bounded jitter/
backoff, one recovery probe, stale success, terminal token errors and spent
coordinator claims without a session. A separate test wraps the actual J-01
RefreshingOidc with mock HTTP and sends a 20-call outage storm: at most the two
already admitted requests reach discovery; subsequent calls during cooldown make
no provider request. No socket or real service is used.

Actual shared-store fairness, key cardinality under hostile traffic, production
budgets, multiple workers, deployed audit redaction and staging/browser recovery
remain unqualified. No K-03/J-02 acceptance tracker was changed.
