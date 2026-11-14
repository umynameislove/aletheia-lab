# K-03 internal session/account ports for K-02, K-04 and K-05

Current R4 session writers use account-before-session row locks; login adds a
schema/browser-scoped lock and sorted target/previous-account locks.
See [account locking and coordinated rollout](k03-account-locking.md).
The historical experimental notes below do not describe R4 deployment status.

Draft on 2026-11-09. The combined synthetic adapter/related regression run supplied
by Kiên passed 228 tests; Ruff and strict mypy passed. This qualifies the stated
offline adapter scope only, not the real K-05 store or shared acceptance.
This is an implementation handoff, not a second public API/schema/fixture
contract. The existing J-01 models remain authoritative for wire shapes.

## Ownership and current insertion point

K-03 owns `hosted_auth/**` and K-03 tests/docs/config. K-02 owns `hosted/**`,
the ASGI router/edge/process/resource lifetime and its test/config/doc paths.
K-05 owns the shared authoritative PostgreSQL schema/migrations/indexes and
real repository, including sessions; K-04 supplies membership/workspace policy.
No migration or in-memory production store is introduced by this handoff.

`ServerSessions(store, policy, timeout_seconds=..., maximum_cookie_bytes=...)`
implements `SessionRepository.is_ready/resolve_cookie`. A future K-02 factory
can create it using process-owned K-05 resources, then inject it into the
existing `ReadOnlyHostedBackend` alongside the real WorkspaceAccessRepository.
Source files for review:

- `src/aletheia_lab/hosted_auth/repositories.py`: draft internal ports/snapshots.
- `src/aletheia_lab/hosted_auth/sessions.py`: read and lifecycle adapter.
- `src/aletheia_lab/hosted_auth/identity.py`: pre-provisioned account lookup.

K-02 router/composition changes have not been made. K-02 must agree them before
integration. No production resource factory or auth-enabled readiness is claimed.

## Authoritative read and credential handling

The adapter accepts only the protected opaque session cookie, rejects duplicate
or malformed protected cookies, and sends a domain-separated SHA-256 digest to
the store. A store never receives a raw session credential through these ports.
Existing SessionRecord/Session/Actor models are reused; identity and roles cannot
be supplied via client headers. No role is present in the account resolver port.

`inspect` must read the credential/session AND current account enabled state and
account epoch consistently. A session has its captured account epoch; unequal
current epoch invalidates it. Lookup is not an idle extension and must not return
a stale cached grant. Retained rotated credentials are tombstones, not grants.
Unknown credentials return None; expiry/revocation is rejected with the existing
J-01 session codes by the adapter. Corrupt/inconsistent answers fail closed.

Effective expiry is the minimum of stored idle/absolute deadlines and the
current policy's last_seen+idle / issued+absolute bounds. Reading does not slide
idle expiry. The adapter currently fails closed on future last-seen timestamps;
K-05/K-02 must agree authoritative clocks/skew before real multi-host tests.

## Rotation and revocation transaction requirements

`rotate_if_current(RotateSession)` must recheck, in one short transaction:

1. Presented digest is the current credential for the exact actor/context.
2. Generation and current/captured account epochs match the expected server facts.
3. Account/session is still enabled and active; effective deadlines remain valid.
4. The configured rotation interval has elapsed at the authoritative clock.

On success, replace the digest, increment generation once and advance last-seen/
rotation time. Preserve actor identity, context, CSRF, original issuance and the
absolute bound. Idle expiry is capped by the original absolute deadline and the
current absolute policy. A failed CAS returns None; the adapter does not return
a newly generated credential to that caller. No hidden retry loop is present.

Keep a bounded old-digest -> exact-context alias for in-flight revocation races.
It cannot authenticate reads, renewals or arbitrary product effects. Its lifetime,
cleanup/indexes and resource budget need an explicit K-05/K-02 policy; the test
double is not a retention implementation. Avoid unlimited rotated-credential
history or accepting aliases after the live family is revoked/expired.

`revoke_presented(..., all_sessions=False)` derives scope from the credential,
revokes the exact context including replacements, and is idempotent. It must
recognize a valid just-rotated alias so renewal winning first cannot bypass an
already pending logout. Do not use a caller-supplied actor or a global browser
"current account" to choose which other account to revoke.

With `all_sessions=True`, a valid current family authorizes revocation of that
actor's sessions, with an atomic account-epoch increment. Serialize it with
rotation and future login commits. A late renewal may fail or its replacement
may already be revoked; it cannot resurrect a session. The adapter requires an
affirmative store result for logout-all. Unknown/stale credentials cannot select
another actor or report a completed logout-all.

The real repository must verify these schedules with two DB connections and
multiple processes, including rollback/cancellation, lost response and restart.
No transaction may contain an IdP call or a client wait. Driver exceptions are
redacted and surfaced as a safe TimeoutError subclass, which current K-02 maps
to 503 `service_unavailable`; external cancellation propagates normally.

## Identity, provisioning and future login commit

`AccountResolver` is downstream of OIDC verification. It requires exact configured
issuer and subject (no trimming/case folding), enforces the proposed verified-email
policy and looks up a current explicit binding. No binding means unprovisioned;
disabled and unverified accounts are denied. No email/domain matching, automatic
account linking, owner grant, membership creation or invitation acceptance occurs.
`ValidatedIdentity` is only an internal type: constructing it does not verify a
JWT or prove issuer/audience/signature/state/nonce/PKCE.

K-04/K-05 must provide the real IdentityDirectory. A returned binding is a
snapshot, not authority to create a session indefinitely. The future login
commit must atomically consume a live browser-bound single-use login transaction,
recheck the current binding/enabled/account epoch and reject a stale auth intent.
If switching accounts, retire the previous browser context under the agreed
switch policy. Generate fresh session/context credentials and prevent issuance
from client-selected identifiers or an epoch captured before logout-all.
The subsequent `login.py` now defines draft login transaction/issuance ports and
a coordinator verified in the 328-test operator run and the final 453-test
offline run on 2026-11-10. See `k03-login-transaction-handoff.md` and
`k03-verification-20261110.md`. J-01 has added experimental PostgreSQL adapters;
a supported production migration and joint real-store qualification remain open.

The current Session DTO expresses only `ready` / `needs_workspace`. Distinct
`IdentityRejected.reason` values and `RenewalConflict` remain INTERNAL drafts;
J-01 must review public status/code/recovery semantics before an HTTP route uses
them. They have not been added to the shared Problem model or fixture exporter.

## HTTP/client integration still required

Update 2026-11-10: SessionMutationApplication now supplies and composes POST
/v1/auth/renew and /v1/auth/logout-all with the existing DTOs and CSRF guard.
Local PostgreSQL HTTP tests passed rotation, early renew, logout-all isolation,
post-revoke/expiry login recovery and rejection of conflicting live browser
rebinding. See `k03-runtime-verification-20261110.md`. The checklist below still
requires J-01 consumer review, deployment qualification and browser work.

- K-02/K-03 must agree login/callback/renew/logout/logout-all routes, methods,
  response serializers, headers, cookie lifetimes and body/deadline budgets.
- Enforce CSRF with the chosen maintained mechanism, Origin/Fetch Metadata and
  content-type/admission policy BEFORE calling lifecycle mutations. These
  methods are now wrapped in opt-in draft CSRF-protected runtime routes; shared
  admission storage and accepted public semantics remain open.
- Return no Set-Cookie for early renewal, CAS loss or rejection. Keep context_id
  stable on renewal; a successful new login/account switch gets a new context.
- Cookie rotation alone cannot prevent late responses from overwriting browser
  cookies or stale UI. Quân must coordinate requests across tabs and fence late
  data/errors/drafts/cache; account switch must also revoke the previous server
  context. No J-02 browser acceptance is established by backend-only tests.
- Rotation can commit before its reply is lost. That caller must not be told it
  received a usable credential; bounded recovery/reauth and logout via the old
  alias need agreed client behavior. Do not repeatedly replay mutations blindly.
- Readiness must reflect real store/directory/membership resources; a synthetic
  ready=True from a test double cannot be used as hosted readiness evidence.

## Evidence boundary and unlock conditions

The tests use a plainly named SyntheticAtomicStore and SyntheticDirectory in
test files only. They check adapter decisions and controlled concurrency schedules
under the port's assumed guarantees. They do not qualify PostgreSQL atomicity,
multiple replicas, revocation propagation time, token validation, CSRF, login
fixation, real provisioning/recovery/MFA, browser cleanup or staging.

Next inputs: K-05 review/implementation of the atomic store and directory; K-02
review of routing/resource/deadline composition; J-01 review of public auth error
semantics; IdP/client/HTTPS decisions and a permitted DB environment. Offline
implementation/testing of independent K-03 pieces can proceed while these inputs
are pending. None of this closes K-03.01–06 or J-02 acceptance by itself.
