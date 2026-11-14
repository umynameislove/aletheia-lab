# K-03 runtime integration prepared for coordination

2026-11-10: Kiên approved the four integration files below; their planned edits
are implemented. The new session HTTP module's 21 offline tests passed. Further
combined verification and local Keycloak/PostgreSQL evidence are recorded separately.

The user's original scope agreement requires coordinating K-02/J-01 files before
editing them. The prepared changes are limited to these existing API-worktree files:

| File | Concrete change |
| --- | --- |
| src/aletheia_lab/hosted_auth/callback_http.py | Retain the browser login-binding cookie on successful callbacks; remove only its clearing header and now-unused imports. Continue emitting session and CSRF cookies only after verified commit. |
| tests/unit/test_j01_callback_http.py | Update the legacy success assertion from three cookies/cleared login binding to two cookies/no mutation of the login binding. Add K-03 response-order regression tests separately. |
| src/aletheia_lab/hosted/upload_runtime.py | Import and compose SessionMutationApplication for POST /v1/auth/renew and /v1/auth/logout-all, reusing existing settings, sessions, tokens, body limits and clock check. |
| src/aletheia_lab/hosted/managed_runtime.py | Inject one BoundedOidc around the existing initialized RefreshingOidc per runtime; keep pool, workers, TLS trust and storage ownership unchanged. |

New tests and configuration fields remain in the approved K-03 paths. Routing
uses existing Session/Problem models. renewal_conflict/409 is an explicit draft
extension requiring J-01 consumer review; it sets no cookie and must not clear
the winning session in another tab. Shared admission storage is still a K-05
dependency and will not be replaced by a process-local production counter.

The callback fix removes the old-response cookie deletion. Its delayed-callback
test passes. It does not
claim to solve all late session-cookie response ordering or first-cookie races;
those still require the Quân/browser coordination protocol.

Keycloak's password-only reauthentication form is a separate issue. Posting
another username to a form bound to the SSO user does not select that account.
Do not silently change IdP logout/SSO policy or claim a prompt parameter proves
account selection. A later real Keycloak HTTPX smoke passed after explicitly
using its v2 reset-login control; see `k03-runtime-verification-20261110.md`.

## Additional PostgreSQL recovery fix approved and verified

The real PostgreSQL HTTP smoke passed renew/logout-all and denied the revoked
credentials. Its added fresh-login-after-logout-all case then failed at login
start. `PostgresFreshLogin.begin_intent` sees the retained browser binding's old
current_context and rejects it even though that context is revoked.

Kiên explicitly approved the extra file:
`src/aletheia_lab/hosted_auth/postgres_fresh_login.py`.
Within the existing advisory-locked transaction, when the stored browser context
differs from the newly verified previous context, check whether the stored one
is still live with the same authoritative enabled/epoch/expiry conditions used
for a prior credential. Preserve rejection for a different LIVE context. Allow
rebinding if that old context is revoked/expired/disabled or invalidated by epoch.
No schema, migration, lock policy, credential or other chat file change is needed.
`test_k03_postgres_http.py` contains the actual regression. It passed after the
fix for logout-all and expired contexts and still denies a conflicting live
context. The test uses a fresh UUID schema and removes it in finally.


## Continuation: retained stale session cookie and safe callback denials

A new isolated PostgreSQL account-policy HTTP test found a second recovery
failure in the already approved postgres_fresh_login.py: a well-formed expired
or epoch-invalidated SESSION_COOKIE caused begin_intent to return None before
the invalid retained browser context could be rebound. Normal login now treats
a missing prior live credential as no prior authenticated context. A returned
malformed context remains a repository error. Reauthentication still requires
a valid current credential; a different live browser context still rejects a
missing or well-formed unknown credential. No schema/lock policy changed.

The direct DB harness covers the original live-context denial, denial with an
unknown session credential without changing the valid session, expired cookies
without manually removing them, disabled/unverified policy denial, reenable with
new epoch, new subject/reused email, and a watermark exactly equal to the pending
intent's creation time. It exercises a synthetic IdP and a fixture trusted
account writer in its own UUID schema; it is not a real recovery/event bridge.

callback_http.py now separately maps expected OIDC/login rejection to 400 and
account-policy rejection to draft 403 (issuer mismatch remains 400). The existing
generic text body, no-store/no-referrer headers, spent claim and no failure
Set-Cookie/Location behavior are preserved. This does not add or freeze a new
shared Problem code. J-01/Quân must review the public presentation.

See k03-local-completion-20261110.md for the current checks and remaining inputs.
