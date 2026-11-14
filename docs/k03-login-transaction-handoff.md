# K-03 login transaction coordinator: K-05/K-02/J-01 review draft

Update 2026-11-10: the final 453-test combined offline run also covers CSRF and
bounded abuse/outage adapters; see `k03-verification-20261110.md`. J-01 has since
added experimental HTTP/runtime/DB code, which does not close joint acceptance.
The callback login-cookie retention discrepancy recorded in that note was
subsequently fixed with Kiên's approval. Runtime routes, PostgreSQL browser-binding
recovery and newer checks are in `k03-runtime-verification-20261110.md`.

2026-11-09: Kiên supplied a combined offline run including the coordinator:
**328 passed in 14.29s**, pytest exit 0, Ruff including C901 exit 0, and strict
mypy with no issues in eight source files, exit 0. This is an internal
implementation port, not a new public API/schema or evidence of DB atomicity.
The run includes all six K-03 test files and three selected K-02/J-01 regression
files; its count overlaps previous runs. The subsequently added CSRF module/tests
are not covered by this result.

## Ownership and integration

`hosted_auth/login.py` contains the coordinator and immutable internal intent/
command types. K-03 owns policy/orchestration; K-05 owns the real authoritative
shared identity/session/intent data, schema/migrations/indexes and transactions.
K-02 owns routing, resource lifetime/deadlines, readiness and runtime composition.
J-01 owns public route/error/response semantics and shared DTO/fixture/schema review.

The new port must share authority with AtomicSessionStore and IdentityDirectory.
It cannot be a second process-local session database. No SQL, production in-memory
store, K-02/J-01 edit, migration or public auth endpoint is added by this group.

## Start and browser binding

Login initiation must pass same-origin/content/admission policy before calling
`begin`. The coordinator validates return-to against the exact configured path
allowlist. It reads the protected login cookie, or generates a new random browser
binding, plus an optional prior session credential. Neither cookie carries actor,
role or workspace data. Browser binding alone is never authenticated identity.

The maintained OIDC client receives fresh state/nonce/verifier. The store receives
domain-separated browser/state digests, protected nonce/verifier, exact issuer/
client/redirect, timestamps, return-to and reauth intent. Raw browser credential,
state and authorization code are not repository inputs. Nonce/verifier must be
encrypted/protected at rest by the real adapter and removed under bounded expiry.

`begin_intent` atomically derives a valid prior context from authoritative browser/
session facts, increments the browser intent epoch, supersedes older pending OR
claimed intents and persists before any start result is returned. The response
cookie must be emitted only after successful persistence. Do not trust a caller's
user/context/role IDs. An arbitrary client session cookie cannot select the newly
issued credential/context or auto-provision identity.

The browser binding cookie is retained across callback completion to coordinate
subsequent intents. Its exact lifetime/row retention must be agreed with K-02/K-05,
bounded by the auth policy; intent expiry remains a separate, shorter deadline.
Do not clear it unconditionally on an old callback response, which can arrive
after a newer login intent. Initial tabs with no binding cookie and browser
response ordering still require client coordination and real browser tests.

## Claim before exchange

`complete` parses the bounded callback query and requires its browser cookie.
It creates a unique internal claim digest. In one short transaction,
`claim_intent` matches browser + state, current intent epoch, pending phase and
store-clock deadline, then makes the transition to claimed. There is one winner.
A wrong browser cannot consume the rightful browser's intent. Replayed, expired,
superseded or already claimed callbacks return no claim and make no IdP request.

The coordinator checks returned intent metadata, reconstructs the trusted OIDC
attempt and exchanges the code OUTSIDE DB transactions. Authlib/JOSE validates
token/claims using pinned material. Only then does the current AccountResolver
perform exact issuer/subject lookup. Unprovisioned/disabled/unverified identity
does not reach session commit. Return-to and deadline are checked again after I/O.

Claim is never released for callback retry on provider denial, invalid token,
outage, cancellation or process loss. Recovery starts a fresh explicit intent.
TTL/quotas/cleanup are required; claimed records cannot accumulate without bounds.

## Atomic session commit and account-switch policy proposal

`commit_login` must serialize with renew/logout/revoke and new login intents:

1. Recheck intent/claim digest, browser/state/epoch, current/latest intent,
   claimed phase and authoritative expiry.
2. Recheck exact issuer/subject -> account binding, enabled/verified status and
   expected account epoch. A previously returned AccountBinding is a snapshot.
3. Deny if the account's logout-all/credential-change watermark is at or after
   intent creation. This conservative policy also fences logout-all BEFORE
   lookup, when lookup might otherwise capture the new epoch and appear current.
   K-05 must review the representation, precision and authoritative clock.
4. Recheck any captured prior browser context remains live/current. A prior logout
   must not be undone by a callback that was waiting at the IdP or commit queue.
5. Retire only that prior browser context (including its rotated credentials),
   then insert the fresh server-generated credential digest/context with current
   safe account/onboarding facts. Mark intent committed in the SAME transaction.

Account-wide revocation has its separate logout-all authority; account switching
does not revoke every other browser. No email/domain linking, workspace/owner
grant or client-selected context/credential is performed. The exact switch and
reauthentication UX/public semantics still require J-01/Quân review.

Context/digest uniqueness, account/intent constraints, expiry cleanup, locking/
CAS, rollback and restart/restore behavior belong in the actual K-05 repository.
Nonce/verifier/client secrets must not enter logs. No external call is permitted
inside a transaction or while holding its DB connection/lock for user interaction.

## Outputs, CSRF and ambiguous outcomes

The coordinator requires a CSRF token factory bound to the newly generated context.
There is no insecure production default; coordinator tests deliberately use a
synthetic factory. The subsequent, offline-verified `csrf.py` supplies `CsrfTokens.issue`
for injection and a current-context guard; see `k03-csrf-handoff.md`. Real store
and HTTP integration remain open. Internal methods are not exposed as HTTP routes.

Commit output is revalidated for scope, digest/context, enabled/verified account,
epoch, fresh generation/timestamps, CSRF and policy-bounded expiry before the raw
credential can be returned. This is a fail-closed output guard, not rollback of
an already committed broken repository. Real constraints/tests remain mandatory.
`LoginCompletion` is an INTERNAL result for cookie/redirect wiring; the shared
Session endpoint continues to use the existing K-02/J-01 models and current facts.

The coordinator's overall deadline fits K-02's configured backend deadline and
leaves room for its smaller IdP deadline. A commit can succeed before the reply
is lost: the caller must not be told it received a valid credential, and replay
must not create another session. Fresh-login recovery and classification of
unknown outcomes need agreed public semantics. Do not blindly replay a callback.

No route should clear or set cookies on a stale/failed callback as if it were a
successful new login. Late cookie responses, cache/draft/toast cleanup and tab
epochs still need Quân browser qualification; server guards do not settle UI state.

## Written tests and remaining acceptance

SyntheticLoginAuthority is a test double in the test file only. Controlled
schedules cover replay/concurrent claim, binding, superseded pending/claimed
intents, expiry, failed token validation, disabled/unprovisioned accounts,
logout-all before/after lookup, prior logout, account switch/fixation inputs,
lost commit reply, corrupt store outputs and cancellation. Additional cases use
the actual Authlib/JOSE adapter and generated signatures over mock HTTP.

These tests passed in the operator-supplied combined run above. They do not
qualify real DB transactions, multiple processes, HTTPS, real IdP/provisioning/
recovery/MFA, the subsequent CSRF group, shared rate
limits or revoke <=5 seconds. DB/IdP/browser acceptance and all complete K-03/J-02
journeys remain open; existing assignment CSVs are unchanged.
