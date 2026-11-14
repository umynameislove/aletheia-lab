# K03 account and browser locking — R4

Login, renewal, logout and trusted operator revocation no longer share one
database-wide advisory lock. A blocked account must not stop authentication
for a different account in another browser.

The transaction lock order is:

1. Login begin/claim/commit acquire a transaction advisory lock derived from the
   current application schema and the browser digest. It also covers creation
   of a browser row that does not yet exist. The browser binding is not identity.
2. Login commit locks its target account and, when switching, the previous
   session's account, ordered by user ID. Membership writers use the same order.
   Renewal/logout/operator revocation lock only their own account row.
3. Session and credential locks follow account locks. After waiting for accounts,
   commit rereads the exact claim/browser binding and expiry, refreshes the
   issuance clock, and rechecks enabled/email policy, account epoch, logout
   watermark and the previous live session before publishing a new session.

Only begin/claim/commit take browser locks. Account/session writers never acquire
a browser lock after an account lock. No provider call takes place inside these
transactions. Existing one-use claims, browser epochs, database-created intent
timestamps and logout-all watermarks remain authoritative. An account lookup
that observes the new epoch still cannot publish a pending intent created before
logout/recovery. Normal rotation preserves its context and the existing worker
authorization protocol.

The existing schema, grants, cookie policy, request/response contract and timeout
budgets are unchanged. Hash collisions in the browser advisory key can serialize
unrelated browsers; they cannot grant authority or bypass row-level checks.

## Deployment

Drain and stop **all old API authentication writers** before starting R4 API
processes. Keep readiness unavailable while changing this protocol. This is a
coordinated API restart, not a mixed R3/R4 auth rolling deployment: R3 does not
take the new browser lock, so mixing writers would not preserve same-browser
serialization. The unchanged workers may continue with retained PostgreSQL and
object storage. Jobs already acknowledged remain in their existing durable queue.

To roll back, drain and stop all R4 API writers before starting R3 writers.
No reverse schema migration is required. Retain sessions, pending intents,
receipts, tombstones and the original secrets; do not reset epochs or clear
storage to make deployment succeed. Check readiness and the same-release login,
switch, renewal, logout and current-object authorization journeys after restart.

## Verification

`tests/integration/test_k03_auth_isolation_postgres.py` uses independent native
PostgreSQL backends and UUID-owned schemas/roles. It checks unrelated-account
progress while A is visibly blocked, same-browser latest-intent claiming,
logout/operator fencing of pending login, opposite account switches and a
previous-account revoke committed while switch waits, same-browser begin during
a waiting commit and a claim that expires during its account lock wait. Existing
`test_k03_session_races_postgres.py` retains renewal-CAS and both logout-all
race orders. These tests require an explicitly supplied disposable
`K03_TEST_DATABASE_URL`; they do not contact an IdP or certify browser/50k SLOs.
