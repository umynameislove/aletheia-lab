# Shared auth rate limit: concrete schema and integration review

2026-11-10. Prepared K-03 implementation uses the existing transaction/pool
protocol. It never creates schema at import, startup or during an HTTP request.
K-05 retains ownership of production migrations, DB privileges and rollout.
Kiên approved the additive schema and the three listed shared-file edits. They
are implemented and verified, with DDL used only in isolated UUID test schemas.
See `k03-shared-rate-limit-verification-20261110.md` for the actual results.

## Proposed additive table

```sql
CREATE TABLE k03_auth_admission (
    namespace varchar(128) NOT NULL,
    operation text NOT NULL CHECK (operation IN ('login_start','callback','renew','logout','logout_all')),
    global_limit integer NOT NULL CHECK (global_limit BETWEEN 1 AND 10000),
    subject_limit integer NOT NULL CHECK (subject_limit BETWEEN 1 AND global_limit),
    window_seconds integer NOT NULL CHECK (window_seconds BETWEEN 1 AND 3600),
    window_number bigint NOT NULL CHECK (window_number >= -1),
    last_seen timestamptz NOT NULL,
    attempts integer NOT NULL CHECK (attempts BETWEEN 0 AND global_limit),
    subjects jsonb NOT NULL CHECK (jsonb_typeof(subjects) = 'object'),
    PRIMARY KEY (namespace, operation)
);
```

Each configured namespace has only five operation rows. Within each fixed
window the subject map can contain at most global_limit source digests, since
only allowed attempts add/update entries and each also consumes a global slot.
On the next window the map is replaced, not appended to an unbounded history.
The map is at most 10,000 entries; operator budgets should usually be much lower.
This deliberately serializes each operation row. Benchmark before approving
high-volume deployment; adding Redis or a second authority is not proposed here.

Both budgets are checked and committed while holding the same row lock, using
DB time sampled AFTER lock acquisition. Denied attempts are not refunded and
cannot create new digest entries. A backwards DB clock or a different policy
for an existing row fails closed; a rolling restart cannot silently reset quota.
Fixed windows allow a boundary burst of up to twice a limit across adjacent
windows. The limits describe per-window attempts, not a rolling-second SLA.

The namespace is an explicit deployment+issuer identifier shared by all API
workers. The source HMAC key must also be shared and stable across workers/
restarts. Changing it changes subject buckets; global quota remains mandatory.
Use an operator-reviewed change procedure at a quiet boundary for policy/key
changes; do not reset rows automatically or expose a reset endpoint to clients.

## Concrete shared-file changes

- `hosted/managed_runtime.py`: construct the PostgreSQL admission store from
  explicit AuthPolicy settings, verify schema/policy compatibility before
  publishing readiness, resolve a dedicated secret reference, then inject the gate.
- `hosted/upload_runtime.py`: place AuthAdmissionApplication inside the existing
  trusted edge and total request deadline, before login/callback/session effects.
  No rate-limit fallback on DB errors; preserve non-auth routes/lifespan.
- `tests/unit/test_j01_managed_runtime.py`: update its injected startup doubles
  for the new required admission dependency, keeping resource-close assertions.

New source/test/config files stay within previously approved K-03 paths. Schema
DDL will be packaged under hosted_auth for isolated tests and operator/K-05
review; it will not be applied to existing J-01 schemas or production automatically.
Real DB tests create/drop only a new k03_rate_test_UUID namespace.
