# K-03 CSRF and source guard: integration draft

2026-11-10: 83 focused CSRF tests passed, followed by the final 453-test combined
K-03/selected regression run. Ruff and strict mypy passed for ten K-03 source
files. See `k03-verification-20261110.md`. The previous 328-test run predates
this group. This K-03 group creates no HTTP route, production signing key,
server bootstrap repository or shared fixture.
K-03.04 and the complete K-03/J-02 journeys remain open.

## Libraries and scope

Proposed direct verification pins, checked against primary sources:

| Distribution | Pin | Use |
| --- | --- | --- |
| asgi-csrf | 0.11 | ASGI signed-cookie/header validation |
| itsdangerous | 2.2.0 | Same library serializer for server-context token issuance |
| python-multipart | 0.0.33 | Required by asgi-csrf import; body form fallback is blocked here |

References: [asgi-csrf release](https://pypi.org/project/asgi-csrf/0.11/),
[versioned implementation](https://github.com/simonw/asgi-csrf/blob/0.11/asgi_csrf.py),
[itsdangerous release](https://pypi.org/project/itsdangerous/2.2.0/),
[python-multipart release](https://pypi.org/project/python-multipart/0.0.33/).
Direct pins are not an approved transitive lock or a deployed dependency set.
After Kiên authorized assistant-run commands, they were installed into a separate
GUID-named TEMP target, with pip's report retained; its path is recorded in the
verification note. Existing OIDC target, shared venv and pyproject are untouched.

The library's no-cookie and Bearer exemptions are unsuitable for these
cookie-auth routes. The wrapper requires proof before calling the library,
uses exact always-protected paths and removes Authorization from its canonical
cookie-auth scope. It never supplies the business body reader to form fallback.
No skip hook or automatic library cookie generation is exposed to the router.

## Proof and current authority

`CsrfTokens.issue(context_id)` signs the server-selected context plus a fresh
random nonce. A stable explicit secret is required, referenced only through
`AuthPolicy.csrf_secret_env`; there is no per-process random fallback or .env
lookup. Production key generation/distribution is operator work: use sufficient
cryptographic entropy (at least 32 random bytes), the same key across workers,
and keep its value out of config files, diagnostics and logs. The length guard
is not an entropy estimator. No real secret is needed for synthetic tests.

For each unsafe request, the following must agree:

1. Draft `X-CSRF-Token` header and the protected CSRF cookie.
2. The current server repository's CSRF proof.
3. The signature's context and the current server context.

`SessionBindingLoader` adapts the existing K-02 SessionRepository. It requires a
current active/enabled server session; the middleware also rechecks binding
shape and expiry under a bounded lookup deadline. It does not take identity,
role or context from caller headers. No binding means denial. The read adapter
does not replace business authorization: account/membership/revocation must be
checked again within the mutation's authoritative transaction to fence races.

The proposed `__Host-aletheia_csrf` cookie uses Secure, HttpOnly, SameSite=Lax,
Path=/ and no Domain. JavaScript receives the token through the existing Session
response, not document.cookie or persistent localStorage. The additional cookie
name and bootstrap behavior need K-02/J-01/Quân review; no shared wire header,
model, schema or accepted fixture was changed. Proofs are masked internal values.

## Request policy

Unsafe methods are POST/PUT/PATCH/DELETE. They require HTTPS ASGI scope, exact
configured public Origin (scheme/host/effective port), current proof and default
application/json media. A single UTF-8 charset parameter is allowed. Unexpected
content encoding, duplicate security headers/cookies and method overrides are
denied. Request header/cookie sizes are bounded before store lookup.

When Fetch Metadata is present it must say same-origin, cors/same-origin and
empty destination. Missing Fetch Metadata still requires exact Origin and all
proof checks. Same-site is insufficient. The wrapper never trusts Forwarded or
X-Forwarded-Proto directly; trusted proxy/TLS normalization and Host validation
belong to K-02's edge. Do not enable permissive CORS for credentialed requests.

GET/HEAD/OPTIONS pass to the router; they must not expose business mutations.
The OIDC GET callback uses the separate browser-bound one-use state/nonce/PKCE
flow. This is not a prefix exemption for unsafe callback requests. TRACE,
CONNECT and other methods are denied; WebSocket upgrade is closed here until an
explicit protocol is designed. Upload/media routes need an explicit media
allowlist with the same mandatory header proof and bounded business body reader;
adding a media type never enables the library's body-token fallback.

Denials return the existing Problem model, no-store, safe fixed detail and a
server-generated request ID, without setting cookies. Invalid proof/source is
csrf_invalid/403; current session rejection preserves existing 401 semantics.
Invalid repository data is service_failure/500; unavailable/timeout lookup is
service_unavailable/503. This draft mapping requires J-01 review. External
cancellation propagates. Business exceptions after effects remain the router's
responsibility and are not relabeled as CSRF denials.

## K-02/K-05/J-01 integration and remaining qualification

- J-01's current experimental ManagedRuntime already injects `tokens.issue` as
  LoginCoordinator's csrf_factory. Qualify persistence of its context-bound
  output in the actual shared session transaction and migration.
- Set session and CSRF cookies only after successful commit, with current
  bounded lifetime and no-store responses. Renewal preserves context/proof;
  repair a missing/stale CSRF cookie only from an authenticated Session GET.
  Agree response ordering with Quân so a late response cannot overwrite newer
  context cookies or revive old UI state. Logout clears matching cookie scopes.
- The current J-01 LoginStartApplication uses same-origin Fetch Metadata and a
  GET redirect. Its cookie/intent response ordering, source policy, admission and
  CSRF strategy need joint review; no claim of protected anonymous mutation
  follows from the session guard. An alternative anonymous bootstrap needs a
  bounded, server-authoritative
  preauth binding/bootstrap flow, Origin/admission policy and expiry/quotas. The
  interface permits a dedicated BindingLoader; the tests demonstrate only a
  synthetic one. SessionBindingLoader deliberately cannot bootstrap anonymous
  identity. Do not exempt missing cookies or silently turn login-start into an
  unprotected mutation. Its route/DTO/cookie handshake is a coordination item.
- Put this guard inside K-02's total request deadline and outside business
  effects. Add current-authority checks at the mutation transaction. Provide
  real K-05 session/preauth adapters; no process-local production store is added.
- Configure a shared signing key through the declared environment reference.
  Key changes currently fail closed for old proofs and may require explicit
  reauthentication; a key ring/rotation deployment policy is not implemented.
- Review header/cookie/Problem semantics and add accepted shared fixtures in
  J-01-owned files. K-03 tests construct existing models rather than publishing
  a competing schema. Real HTTPS/browser/multi-tab and shared-DB tests follow
  integration; no five-second propagation claim is made by this offline guard.

## Supplied verification

`test_k03_csrf_boundary.py` imports the real libraries, uses synthetic server
bindings and ASGI messages, and counts body reads and business effects. It covers
valid unsafe methods, source/media/duplicate/bypass rejection, context/signature
binding, stale/revoked/disabled state, corrupt store data, no-store Problems,
timeouts/cancellation and no implicit anonymous exemption. Rejection cases assert
zero business calls/effects and zero request body reads. It makes no network
calls, starts no service and does not qualify a real browser or database.

The focused 83-case run and final 453-case combined run passed outside the
sandbox after sandbox temp-directory failures; lint and typing passed in the
recorded source scope. This is offline qualification, not browser/DB acceptance.
The final code's dependency targets and exact scope are recorded in
`k03-verification-20261110.md`.
