# K-03 auth interface and ID-token logout-hint review

WIP review in the existing `aletheia-j01-api` checkout. J-01 owns start/callback/logout
presentation, public codes and fixed app destinations. This document records
internal producer meanings and a retention assessment; it does not create a
second HTTP contract, Session DTO, repository or migration.

## Typed outcome implementation

The small change stays in the existing `boundary.py` and `login.py`.

- `LoginRejectionReason = Literal["invalid_attempt", "provider_cancelled"]`.
  `LoginRejected(reason="invalid_attempt")` retains its existing exception base,
  fixed message and zero-argument constructor.
- `CallbackInput` retains its first three fields, including `provider_denied`.
  A defaulted `provider_disposition` contains only `cancelled`, `unavailable`,
  `rejected` or None. Provider text and recovery URLs remain discarded.
- `access_denied` maps to `cancelled`; `server_error` and
  `temporarily_unavailable` map to `unavailable`; every other nonempty bounded
  error maps to `rejected`. A provider access denial does not establish that
  the human personally clicked Cancel.
- The coordinator parses issuer/input, atomically claims the exact browser/state,
  validates the intent, and only then classifies the provider response.
  Cancellation raises `LoginRejected("provider_cancelled")`; reported outage
  raises existing `OidcUnavailable`; unknown error raises generic LoginRejected.
  No token exchange/account lookup/session commit occurs on these paths.
- This classification runs outside `BoundedOidc.exchange`. Browser-supplied
  provider error parameters cannot trigger process-wide provider backoff.
  Real transport/discovery failures retain the existing outage guard.
- `IdentityRejected.reason` already carries typed disabled/unprovisioned/
  email_unverified/issuer_mismatch meanings. `OidcUnavailable` already has its
  own type and safe fixed message. No duplicate exception hierarchy is added.
- `commit_login` returning None can mean epoch, expiry, supersession, prior
  context or account races. It remains `invalid_attempt`; do not infer disabled
  or any other specific account state from that generic result.

J-01 may map these existing typed outcomes to its agreed wire response. An invalid
state/issuer/browser, a spent callback or malformed input must remain generic.
A replay of a cancellation/outage callback is an invalid attempt, not another
cancellation/outage signal. Provider errors never carry a browser-controlled
return destination.

Domain tests in `test_k03_callback_denials.py` cover typed outcomes, wrong state,
forged issuer, replay, no partial session and no global outage cooldown.
The HTTP presentation tests now consume J-01's coordinated wire handoff below.

## Current logout state and retention boundary

The verified raw ID token is currently discarded by the OIDC adapter.
`ValidatedIdentity`, `SessionSnapshot`, the session table and store ports have
no logout-token slot. `RefreshingOidc.end_session_url()` has no session argument;
it uses configured client_id and the fixed registered signed-out destination.

An ID-token hint is recommended by RP-Initiated Logout. The current client_id
request can use the provider's confirmation flow. A hint may allow fewer prompts,
but its relationship to the current provider session still matters; it is not an
unconditional guarantee of silent logout. [OIDC RP-Initiated Logout](https://openid.net/specs/openid-connect-rpinitiated-1_0.html).

Keycloak 26.8.2 verifies hint signature/client and can require confirmation when
the hinted session differs from the browser session. Its existing registered
post-logout URI validation still applies. [Keycloak logout endpoint](https://raw.githubusercontent.com/keycloak/keycloak/26.8.2/services/src/main/java/org/keycloak/protocol/oidc/endpoints/LogoutEndpoint.java).

Safe retention needs a separate coordinated implementation:

1. Carry a bounded SecretStr ID token only after full maintained-library
   signature, issuer/audience, nonce, time and assurance validation. Retain only
   when the configured logout feature requires it; never accept a hint from
   request bodies, query parameters or browser-declared actor/context IDs.
2. Persist encrypted data in the existing atomic login commit, linked immutably
   to the generated server context and account epoch. Use authenticated
   encryption with a separate protocol domain and AAD covering context, issuer,
   client, actor and epoch; define key ID/rotation and bounded retention.
3. K-05 supplies the canonical session extension/migration and least-privilege
   grants. Current session schema has no safe spare column. CSRF data and
   `sealed_proof` are unsuitable: the proof cipher accepts only nonce/verifier,
   binds to an intent, and has a 512-byte envelope bound. Mutable browser binding
   is not an immutable session-to-token association.
4. Read/consume the exact context's hint while deriving context from the presented
   credential and atomically revoking local session authority. A later login,
   another account, rotated/expired context, swapped ciphertext or stale epoch
   must never redirect with another context's hint. Preserve successful local
   logout when hint decryption/provider discovery fails.
5. J-01 changes its context-aware logout hook and browser flow after the store
   contract exists. Keep hint material out of Session/capability DTOs, logs,
   telemetry, exceptions and general-purpose domain records. Treat any logout
   URL containing a hint as secret-bearing navigation.
6. Test signed hint/client matching, context/account swaps, encryption tamper,
   rotation, revocation, lost replies, key changes, expiry/cleanup and provider
   confirmation/outage with the supported store and actual IdP.

No token-retention source/store/schema/wire changes are implemented by this review.
The typed-error work does not depend on hint retention, and confirmation-based
provider logout remains an explicit existing behavior.

## Source evidence

All listed auth sources were existing untracked local WIP when inspected;
their contents were read before the guarded K-03 edit. No Git mutation, reset,
clone, worktree or WIP copy was used. Implementation hashes below identify this
historical domain-only snapshot rather than a release. The later J-01 wire
handoff and its owned-test hash are recorded separately below.

| File | SHA-256 |
| --- | --- |
| `src/aletheia_lab/hosted_auth/boundary.py` | `64c0661e7b9ff564cc4f9f21b18652e6dbb11f8650b6bbe45c639a8d801a8aa7` |
| `src/aletheia_lab/hosted_auth/login.py` | `b05f40998720a6be9b284811ca0f878d06b0d9384eb2ceb43b0d15cb5907793a` |
| `src/aletheia_lab/hosted_auth/identity.py` | `50c9f2d1d9a92e679b5c6204e2ced7d09e5f685269b023510a5b99161bc9403b` |
| `src/aletheia_lab/hosted_auth/oidc.py` | `d09fcaf308d005b6e04add912ff38d40f9d99265856c2f7bb8bb2735a4dc5fe3` |
| `src/aletheia_lab/hosted_auth/oidc_discovery.py` | `5dff8dd20c538836005631630dd43bed3aca73dfa21f709a20f3328613301c8a` |
| `src/aletheia_lab/hosted_auth/repositories.py` | `6419cb5e44a57888c6fb512f39082f38b13d61bd314bffa2ce99efeb9413b1af` |
| `src/aletheia_lab/hosted_auth/postgres_fresh_login.py` | `e1de7dbfeb4776cd650ea86ed6725dc54b22c9ed86a9f6aaa630c3946fa56ba0` |
| `src/aletheia_lab/hosted_auth/postgres_sessions.py` | `96b8b732f9fc76c600182f8806cf42dc30a8479dcf60ecd4a891097e5454ecf1` |
| `src/aletheia_lab/hosted_auth/login_proof_cipher.py` | `e271cb97f370147da4bc6d7ea2d09ce60cd30ade09549d7e0765942e15fa05b7` |
| `src/aletheia_lab/hosted_auth/logout_http.py` | `94342681291a174ef67bcd2670222bb8f0bec9058996cda087ada5d34cb1173c` |
| `tests/unit/test_k03_callback_denials.py` | `1921bbb267a1612b0e58b37b0793a45bb3df25effc9334e15cb3d12932997818` |

This review does not qualify a deployed browser flow or a trusted IdP credential-
change event bridge. The local recovery harness labels its manual epoch writer
separately.

## Scoped verification

After the domain-only change, the six focused K-03 files (callback denials,
auth boundary, login coordinator, maintained OIDC library, abuse guard and
identity adapter) passed: **246 passed in 9.70s**, exit 0. Ruff including C901
passed on the two changed source files and the owned denial tests; strict mypy
passed on the two changed source files.

The callback presentation tests in that earlier run exercised the pre-handoff
generic HTTP responses. The coordinated caller update and fresh focused result
for J-01's fixed redirect presentation are recorded below. The earlier 246-test
result applies to its domain-only snapshot. No route, Session DTO, store/schema,
token retention, deployment, commit or push was changed by that work.


## J-01 wire handoff verification

After J-01 completed its callback presentation, K-03 changed only its owned
`test_k03_callback_denials.py` assertions and this review. The callback source
remains J-01-owned. HTTPS GET failures now have J-01's exact fixed empty 303
response, no Set-Cookie, no-store, no-referrer, nosniff and Content-Length 0:

| Trusted outcome | Fixed signed-out reason |
| --- | --- |
| Claimed provider access denial | `login_cancelled` |
| Verified identity rejected as disabled | `account_disabled` |
| OIDC/repository unavailable, TimeoutError, clock failure or deadline | `idp_unavailable` |
| Invalid attempt, replay, untrusted provider error or internal defect | `login_failed` |

All destinations are `/#/signed-out?reason=` plus one of those fixed reason
codes. Provider description, recovery URL and exception text cannot supply the
destination or response body. Callback method/scheme rejection stays 405/400
without a redirect, cookie, claim or exchange. External cancellation propagates.

The owned tests also check the accepted provider response is spent once, replay
is generic, outage/cancellation never exchanges tokens or starts global
backoff, failure cannot partially commit a session, clock failure has no claim,
and a timeout after the claim still prevents exchange completion/publication.

| Handoff file | SHA-256 |
| --- | --- |
| `src/aletheia_lab/hosted_auth/callback_http.py` (J-01 producer) | `0c6a040e829b2a9d521793424d732edfc04600fcddbcd067a6d6c62fcee9eb24` |
| `tests/unit/test_k03_callback_denials.py` (K-03 consumer) | `581eb89aa8b410c577799593bcb3eb1d080ca2dda4d54561106f4dfdca9973e2` |

Focused verification against that source: owned callback denial tests plus
J-01's `test_j01_callback_http.py`: **60 passed in 1.91s**, exit 0. Ruff including
C901 passed on the owned test. These are synthetic coordinator/ASGI tests;
this focused run does not certify an external IdP, deployed browser or shared
PostgreSQL service. The separate real-provider evidence remains in its own
harness and report. No source route, store/schema, credential retention,
deployment, commit or push was changed during this consumer update.
