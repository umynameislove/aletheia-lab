# K-03 shared rate limit: configuration and operational boundaries

2026-11-10. The PostgreSQL store, auth HTTP gate and experimental ManagedRuntime
integration are implemented. The additive DDL was tested only in disposable UUID
schemas. K-05/operator still owns production migration/role/rollout review.
An explicit versioned migration/CLI and quiet-window policy change are now
implemented and verified on a separate disposable PostgreSQL container. Use
`k03-admission-migration-runbook.md` for the preferred installation/adoption
procedure; existing unversioned counters are preserved. The original SQL file
is now the frozen v1 checksum input, not a file to edit for policy changes.

## Required setup

1. Review `src/aletheia_lab/hosted_auth/admission_schema.sql` and apply it through
   the explicit migration CLI after K-05 review, in the API role's trusted named
   schema/search_path. The application
   never executes CREATE/ALTER/DROP during import/startup/HTTP. Do not point tests
   at a shared existing schema. The API role needs SELECT, INSERT and UPDATE on
   this table, with schema CREATE/DDL reserved to the migration role.
2. Set one stable `admission_namespace` for this deployment/issuer in AuthPolicy.
   All API workers must use the same namespace, budgets and source key. Choose
   namespaces through trusted operator configuration, never through a request.
3. Supply a separate strong shared HMAC key through the variable named by
   `admission_secret_env` (default ALETHEIA_HOSTED_ADMISSION_SECRET). Generate it
   with at least 32 random bytes and store through the operator's secret channel.
   Never paste its value into TOML, chat, a command argument, log or fixture.
4. Review `admission_global_attempts`, `admission_subject_attempts` and
   `admission_window_seconds`. The example's 600/30/60 is a proposal, not a
   capacity measurement or an accepted user policy. Each of the five auth
   operations has its own global and source window. NAT/shared proxy addresses
   share a source quota. The source limit must not be mistaken for an account
   limit or a brute-force mechanism replacing the IdP's own protection.
5. Deploy through the normal resource/TLS lifecycle. The managed runtime refuses
   missing namespace/key, absent table or policy mismatch; it does not silently
   run auth without admission. Older local auth TOMLs need these explicit inputs
   before they can start the updated ManagedRuntime. None were edited here.

## Enforcement

The trusted EdgeGuard runs first, rejects untrusted Host and removes proxy hints.
The ASGI server must keep proxy-header processing disabled unless K-02 configures
and verifies a real trusted-proxy boundary. Admission uses only the server peer
IP, canonicalizes mapped IPv4/IPv6, and stores its HMAC digest. Raw peer address,
cookies, email, identity headers and forwarded IPs are not repository inputs.

Admission wraps login start, callback, renew, logout and logout-all before any
auth body/business/IdP effect. It bounds the full request (counter lookup plus
inner auth work) by the configured backend deadline. Other routes and lifespan
pass through. An unknown trusted peer fails closed; it is not replaced with an
untrusted forwarded header.

PostgresAuthAdmission locks the same namespace/operation row for global and
subject decisions, samples DB time after lock acquisition, then commits before
returning an allowance. Startup prepare verifies all five policies without
spending quota. Runtime readiness includes a non-mutating table/policy/clock
check; loss of that dependency also makes the existing shared readiness predicate
false. Every quota transaction still revalidates policy and time independently.

A denial returns the existing Problem shape with draft rate_limited/429,
no-store and bounded Retry-After. Storage/clock/policy errors return safe 503;
there is no process-local fallback, refund after a callback failure or automatic
retry. J-01 still needs to accept the new route/error/fixture semantics. A client
must not turn rate limiting into a redirect loop or blindly replay a spent code.

## Storage bounds and clock behavior

There are five rows per configured namespace. A fixed window stores at most
global_attempts digests, capped at 10,000 by policy/DDL. Denied requests never add
source keys. The first accepted request in a later window replaces the old map;
there is no row per historical bucket/source. An idle namespace can retain its
last bounded map until another request or explicit operator retention cleanup.
Namespace retirement/retention belongs to K-05; no unbounded background cleanup
or automatic namespace creation from untrusted values is introduced.

Fixed windows permit up to twice the limit across an adjacent-window boundary.
The counter row serializes an operation and JSONB updates copy bounded data;
benchmark the actual topology before raising quotas for high traffic. This is a
resource limit, not a claim of constant-time throughput under hostile load.

Backward DB time fails closed against last_seen. Restore of an older database
backup, database clock repair and active failover can affect counter history and
must be included in operational recovery policy. Four-process/new-pool tests do
not qualify DB crash/restart/failover/disaster recovery.

## Changing budgets, namespaces or keys

Existing rows persist their original budgets. A worker configured differently
fails startup/readiness/requests rather than resetting everybody's allowance.
Prepare applies all five operation policies in one transaction, so a mismatch
cannot leave a partially initialized namespace.

For a planned policy change, stop/drain the affected auth writers, wait through
the old window, and have K-05 apply the reviewed counter/policy migration before
starting all workers with the same values. Do not mix old/new policies in a
rolling deployment or switch namespaces just to bypass an active quota. The
application supplies no public or automatic reset endpoint.
The new operator-only `change-policy` CLI enforces expected values and expired
admitted windows atomically; its local rollout/rollback checks are recorded in
`k03-admission-migration-rollout-20261110.md`. It cannot prove writers are drained.

HMAC key rotation creates different subject digests. The mandatory global quota
continues under the same namespace, but per-source continuity requires a reviewed
quiet-window/key rollout. Different HMAC keys across workers are an operator
misconfiguration; the table verifies policy values, not key equality. No key-ring
rotation scheme is claimed by this implementation.

## Diagnosis and safe audit

`aletheia.auth.admission` emits an INFO `auth_admission_rejected` record with only
a server-generated request_id, closed-set operation/outcome, HTTP status and
bounded retry-after. Its request_id matches the public Problem. It omits IP,
digests, cookies, authorization, callback URL/query, driver/provider text and
tracebacks. Unit tests inspect the actual log record for leakage.

Enable this named logger in the operator's reviewed logging configuration if
needed; there is no global DEBUG logging or new log file/sink configured here.
The deployment must bound log retention/volume and avoid adding sensitive request
context through its handlers/access/proxy logs. Application redaction tests do
not qualify that external logging pipeline.

- 429: inspect fixed outcome/operation/request_id and respect Retry-After; review
  legitimate NAT traffic before increasing a budget.
- 503 on auth/readiness: check table migration, DB availability/permissions,
  policy consistency, stable namespace/key configuration and authoritative time.
  Restore the shared authority before retrying; do not enable memory fallback.
- 500 service_failure: investigate invalid adapter answers/corrupt authority in
  the isolated diagnostic environment, without dumping rows or secrets to clients.
- Callback already claimed before timeout/outage: start a fresh explicit login.
  Rate-limit admission never releases a spent claim or repeats IdP token exchange.

See `k03-shared-rate-limit-design.md` for DDL and scope approval and the dedicated
verification report for actual test evidence. No external acceptance tracker or
production migration was changed by this work.
