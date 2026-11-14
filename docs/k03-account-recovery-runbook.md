# K-03 account lifecycle and recovery: integration runbook

## Current local verification — 2026-11-11

The extended A/B fixture now passed with the current auth sources: actual
Keycloak 26.8.2, TLS verification, Mailpit, Authlib/joserfc, private PostgreSQL
16.15 and the existing canonical operator CLI. The 146-second run had unchanged
input hashes and verified removal of its own schema/containers/bridge/TLS/PG
anonymous volume. Evidence: k03-verification-20261111/
private-acceptance-64228102b8a447f9b37ed05858d0bdf9/result.json and safe-outcomes.log
under the shared evidence workspace; see k03-completion-20261111.md.

Passed: real expired/used reset links, old password denial, original OTP retained
and required after reset, exact subject continuity, disabled provider login,
A-link-in-B context with no app/factor transfer, real signed new subject reusing
A's email denied without provisioning, and actual operator revoke of A while B
remains authenticated. The cross-context reset action was not applied in this
fixture; this is not a general policy that every valid bearer reset link must fail.

### Trusted recovery/compromise operation

Use the existing scripts/k04_workspace_admin.py revoke-sessions operation.
The bounded private identity file contains exactly issuer and subject; supply
the expected issuer separately and the database connection through the named
environment variable, never a URL/subject on argv or in logs. The CLI's --help
is canonical; K04 owns that CLI and its provisioning/grant paths. The operation
commits the shared epoch/watermark/session revocation transaction before reporting
a boolean result. Unknown identity changes nothing; it does not provision,
re-enable an account or reset MFA. A failed/lost command reply is not proof of
no effect. Do not silently retry or infer IdP SSO logout.

This is a trusted manual operator path, not an automatic IdP event listener.
Any propagation measurement begins after the trusted database commit; no
five-second guarantee is claimed from an external reset/disable event that the
application has not received. Keep original MFA on password reset. Lost-factor
recovery and production IdP/assurance choices remain with their owners.

The sections below describe the implemented boundaries and deployment/browser
conditions. Earlier real-provider results retain their own historical hashes.


2026-11-10. This describes implemented server policy and remaining decisions.
Keycloak 26.8.2 is the local test provider, not an approved production choice.
No application password, password-reset or automatic provisioning service is added.

## Account policy implemented

The OIDC adapter must first verify signature, exact issuer, client audience,
nonce, expiry/authentication time and the saved one-use state/PKCE flow. Only its
ValidatedIdentity may enter AccountResolver. Resolve an explicitly provisioned
account by exact `(issuer, subject)`, never email or domain. A new IdP subject
with the same email does not inherit an existing account or membership.

Default policy requires a verified email both in the validated identity and the
current local binding. Disabled/unprovisioned/unverified accounts cannot create
a session. The commit transaction must recheck binding, enabled status, epoch
and revocation watermark; a previous lookup is not a lasting grant. Runtime
session reads continue checking current account state/epoch/expiry.

IdentityRejected.reason distinguishes unprovisioned, disabled, email_unverified
and issuer_mismatch internally. J01 owns the fixed HTTPS GET callback failure
303 presentation using its four allowlisted signed-out reasons; method/scheme
guards remain HTTP errors. Typed cancellation/outage is classified only after
browser/state claim. See k03-auth-wire-interface-review.md and canonical J01
source/fixtures. No provider text/token/arbitrary return URL is exposed;
external cancellation still propagates.

K-04 owns provisioning/invitation/workspace membership and the first-owner rule.
The existing Session response exposes ready/needs_workspace after authenticated
provisioning; it is not an invitation acceptance or a grant to create an owner.
K-05 owns supported schema/migration and transactional binding updates. The
current J-01 PostgreSQL adapters/schema are local experiments, not a rollout.

## Recovery and MFA

The approved local retained-OTP fixture now passed real provider/SMTP/Authlib/PG:
reset, expired/replayed links, fresh MFA/subject continuity and disabled IdP login.
See k03-recovery-mfa-verification-20261110.md for exact versions/limits.
Factor-loss policy, production assurance and the trusted epoch bridge remain
separate; this HTTPX check does not establish hosted browser acceptance.

Use only the selected IdP's account/recovery/MFA UI on its configured trusted
HTTPS origin. The application does not collect a password, backup code, reset
token or MFA credential. Do not accept callback or browser parameters as proof
that MFA/recovery succeeded. Run the normal verified OIDC flow afterwards.

The adapter validates fresh auth_time for requested reauthentication. An explicit
optional required_acr now requests that exact class via acr_values and rejects
a missing/different/non-string signed ID Token acr before identity/session
creation. It applies to both normal login and reauthentication; amr labels and
fresh auth_time cannot substitute for it. The issuer remains pinned by the
existing runtime config; no second provider map or identity config is added.

required_acr is unset by default for compatibility with existing password-only
local fixtures. Set it only after the issuer owner defines and verifies the
class's real MFA/recovery flow. Enabling or changing this policy also requires
the trusted account writer to invalidate existing app session epochs and pending
login intents before admitting the new policy: stored sessions have no persisted
assurance class and are not retroactively reverified against the IdP. All API
workers must use the same reviewed policy. Missing/unsupported claims fail closed
at the next login, without retries.

Requesting acr_values alone does not require the provider to meet it; the RP's
signed-claim enforcement is mandatory. See [OpenID Connect Core section 3.1.2.1
and 5.5.1.1](https://openid.net/specs/openid-connect-core-1_0.html).
Synthetic signed-claim tests demonstrate claim enforcement. The linked local
real flow also proves its mandatory original factor. Other required methods,
fallback, production mapping and alternate recovery paths remain operator/
IdP-owner decisions.

On password reset, compromised account, disabled account or administrative
revoke, the IdP/K-04/K-05 integration needs to advance the application's account
revocation epoch/watermark and invalidate old app sessions. IdP logout or password
reset alone is not proven to revoke Aletheia's independent server sessions.
Select a trusted event/admin mechanism and verify its propagation target;
never trust an arbitrary HTTP identity/revocation header.

## Account switching and reauthentication

The app requests prompt=login/max_age=0 and validates fresh authentication time.
Keycloak can display a password-only form already bound to its current SSO user.
Posting a different username to that form does not choose a different account.

For the local Keycloak v2 theme, the user must use its reset-login control to
restart account selection, then enter the other account. A real-provider HTTPX
smoke passed A-to-B through this action, verified the signed token, created a new
app context and denied the old one. The app side used a synthetic authority in
that check. The CLI follows a strictly parsed control URL in the exact localhost
test realm; product code does not scrape IdP HTML or execute this workaround.

The reset action can log out the existing IdP SSO session. Its effect on other
IdP clients is therefore part of the required UX/SSO policy review. Do not call
the IdP restart/logout endpoint silently or guess an unregistered return URL.
The real browser/Quân journey remains open. A subsequent combined Keycloak plus
PostgreSQL A-to-B HTTPX/ASGI check passed with actual repositories and an isolated
UUID schema; evidence is in `k03-runtime-verification-20261110.md`. It still does
not establish browser or deployed API behavior. Separate fresh logins alone
would not establish account switching.

Primary sources checked for the local provider:

- [Keycloak 26.8.2 cookie authentication](https://github.com/keycloak/keycloak/blob/26.8.2/services/src/main/java/org/keycloak/authentication/authenticators/browser/CookieAuthenticator.java)
- [Keycloak v2 reset-login control](https://github.com/keycloak/keycloak/blob/26.8.2/themes/src/main/resources/theme/keycloak.v2/login/template.ftl)
- [Keycloak restart flow and SSO-session handling](https://github.com/keycloak/keycloak/blob/26.8.2/services/src/main/java/org/keycloak/services/resources/LoginActionsService.java)

## App session recovery

- POST `/v1/auth/renew` accepts an empty JSON object, exact Origin, current
  session/CSRF cookies and X-CSRF-Token. It returns the existing Session model.
  Early renewal changes no expiry/cookie. A successful rotation preserves context
  and CSRF; a competing loser receives draft renewal_conflict/409 with no cookie.
- POST `/v1/auth/logout-all` accepts the same empty-body/proof boundary, derives
  the actor from current server state, revokes all app sessions for that account
  and clears the current app session/CSRF cookies only after success. It does not
  assert that IdP SSO or every other IdP client is logged out.
- A retained browser login-binding cookie is not authentication. The callback
  no longer clears it, avoiding an old callback deleting a newer intent's binding.
  PostgreSQL begin can now rebind it after old context revocation/expiry, while
  rejecting a conflicting context that is still live. Cookie expiry, first-cookie
  races and other late response overwrites still need browser coordination.
- Lost rotation/commit replies can have changed server state without delivering
  a credential. Do not repeatedly replay a callback or treat missing credentials
  as success. Start a fresh explicit login and clear/fence old client data.
- After session_expired/session_revoked, stop renewal/poll loops and discard the
  old context's cache/draft/UI according to Quân's policy. Old in-flight responses
  must not repopulate another account's state. Server revocation does not erase
  bytes that the browser already received legitimately.
- Provider/storage outage and rate limit are distinct from denied identity.
  Respect bounded backoff/Retry-After and offer an explicit fresh attempt; no demo
  identity, automatic redirect loop, stale-key fallback or background token retry.

See `k03-auth-outage-handoff.md` for remaining shared admission storage, audit and
public Problem/retry semantics. Never paste passwords, cookies, callback queries,
reset/MFA tokens or raw provider responses into diagnostic evidence.

## Deployment and browser evidence still required for acceptance

1. Reviewed IdP/client/HTTPS environment, recovery/MFA and acr/amr policy.
2. K-04 provisioning/invitation and disabled/verified-email transitions, with
   explicit consumer states and accepted J-01 fixtures.
3. Qualify the approved deployment's recovery/MFA and account-policy settings.
   The real local retained-OTP/A-B/new-subject cases now passed as recorded above;
   that fixture does not choose or configure the production provider.
4. Old app sessions invalid after recovery/disable/epoch change; real multi-worker
   and membership/job/download paths measured against the five-second target.
5. Browser tab A/B, pending request, concurrent renewal, logout-all and account
   switch, including context-cookie response order and UI/cache cleanup.

These are open acceptance items, not missing passwords to send in chat. No
authoritative external tracker is marked passed by this runbook.
