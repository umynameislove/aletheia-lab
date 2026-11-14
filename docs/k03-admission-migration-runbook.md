# K-03 admission migration v1 and local rollout procedure

2026-11-10. This is an explicit operator tool, tested on disposable local
PostgreSQL 16 containers. K-05/operator still approves production schema/roles,
backup/failover and release packaging. No existing J-01 database was migrated.

## Artifact and contract

`hosted_auth/admission_migration.py` implements only admission v1 and a bounded
policy compare-and-set. `admission_migrate_cli.py` runs it with a single explicit
schema and transaction. There is no generic migration discovery, HTTP migration,
startup DDL, destructive down command or automatic quota reset.

The existing admission_schema.sql is the immutable v1 DDL input; its normalized
text SHA-256 is recorded in `k03_auth_admission_migrations` with version/applied
time. Do not edit that v1 file after adoption. A future schema change needs an
explicit reviewed version; an unknown ledger version/checksum is rejected.
The SQL is now explicit package-data after Kiên approved that narrow pyproject
change. The local wheel/sdist/rebuilt-wheel and installed migration CLI were
verified; see `k03-migration-package-verification-20261110.md`. A complete fresh
dependency installation and accepted production release are still unqualified.

The target must be an existing, explicit lowercase named schema. The CLI refuses
public, information_schema, pg_* schemas and ambiguous identifiers. It does not
create a schema or silently use search_path to choose a database target.
Credentials are read only from ALETHEIA_HOSTED_MIGRATION_DATABASE_URL; never put
the connection string in command arguments, TOML, evidence or chat output.

## What migration does

1. Validate schema/transaction and acquire a schema-specific PostgreSQL advisory
   transaction lock. Set a bounded connection, statement, lock and total CLI
   deadline. Concurrent migration invocations serialize.
2. Create the operator-owned version ledger if absent. Verify an existing ledger
   before accepting its version/checksum.
3. Create the admission table if both table and applied version are absent, or
   adopt the exact original unversioned table. If a version exists but the table
   is missing, reject it; recreating it would erase allowance history.
4. Briefly lock the table against writers and compare its columns/types/defaults,
   constraints, indexes, RLS/trigger/rule behavior against a temporary reference
   created from the canonical v1 DDL on that PostgreSQL server. Constraint names
   do not matter; unexpected behavior or extra structure does. No source table
   is rewritten or dropped.
5. Check existing counter coherence, digest/count maps and window timestamps.
   Register v1 only after validation. Existing quotas and rows remain identical.
6. Commit before printing CREATED, ADOPTED or UNCHANGED. The temporary reference
   objects are dropped and are also transaction scoped.

Any error requires rollback for library callers. The CLI owns that connection
context and performs rollback on failure. FAILED_OR_UNKNOWN never means success;
connection loss near commit needs inspection. A migration rerun verifies state
without clearing quotas. Do not blindly repeat a policy change after an unknown
commit outcome: inspect the current policy first.

The migration role needs schema/table ownership or equivalent reviewed DDL/lock
rights plus temporary-table capability. The API role needs USAGE on the trusted
schema and SELECT/INSERT/UPDATE on k03_auth_admission only. It does not need
CREATE/ALTER/DROP or ledger read/write. API startup/readiness checks the compatible
counter table/policy, not the operator-only ledger; the v1 table layout is unchanged
from the existing unversioned local implementation.

## Operator invocation

After provisioning the migration connection environment through the reviewed
secret channel and selecting the schema, run from the API checkout with the
confirmed interpreter and hosted dependencies available. Examples use a clearly
illustrative schema/namespace; substitute the reviewed local values, never a
credential on the command line:

```powershell
& $K03Python -B -m aletheia_lab.hosted_auth.admission_migrate_cli --schema k03_local_review migrate
```

The command reports only a fixed outcome. It does not dump SQL, counters, provider
responses, DSNs or driver tracebacks. A failed command may have rolled back or
have an unknown reply; preserve the operator change record and inspect safely.

## Safe policy rollout and rollback

All five operation rows for the namespace must already exist and match the
expected old policy. The compare-and-set checks them in one transaction. A
different expected policy or a still-active admitted window is rejected without
resetting counters. A no-op change preserves state even in an active window.

1. Drain/stop old auth writers. The tool cannot prove the deployment is drained.
2. Wait for admitted old windows to finish according to DB time. Do not change
   the namespace or delete rows to evade a live quota.
3. Apply the exact reviewed old/new values (global, source, seconds):

```powershell
& $K03Python -B -m aletheia_lab.hosted_auth.admission_migrate_cli --schema k03_local_review change-policy --namespace local-review --expected 600 30 60 --replacement 900 45 60
```

4. Start all workers with the new values and the same shared source key. Old
   workers become unready and their quota requests fail closed; they cannot
   overwrite the new policy. This is not a seamless mixed-policy rolling update.
5. To roll back application settings, drain again and use the same compare-and-set
   with reversed expected/replacement values after the NEW admitted windows end.
   A live new window blocks rollback. Never drop the ledger/table as rollback.

After quiet-window acceptance, only expired maps are cleared, counters are set
to the new policy's initial window and last_seen retains the DB transition time.
The table/ledger/schema stay in place. Namespace/key rotation is a different
operator action: changing the key changes source digests and is not automated
by this policy tool. Per-source continuity and NAT fairness still need review.

For a v1 software rollout without a policy change, run migration idempotently,
verify the same policy/key across workers and restart the app. Adoption preserves
active counters. The local tests prove repository/policy behavior and DB restart,
not production load or a live multi-worker API deployment.

## Local-only evidence and limits

The dedicated rollout smoke creates a uniquely named PostgreSQL container from
the already available postgres:16 image, publishes only loopback with an ephemeral
port, generates a temporary password without printing it, then removes exactly
that container and its anonymous volume in finally. Its fixture API role/schema
exist only inside that container. No shared J-01 container/schema/config changes.

Tests cover fresh install, concurrent rerun, legacy counter preservation,
checksum/future-version/missing-table/counter/schema drift, unexpected trigger/
RLS/index, transaction rollback, blocked active-window changes, old-policy worker
denial, safe policy rollback, API least privilege and actual DB restart with
preserved ledger/quota/grants. Docker can reassign the ephemeral host port after
restart; the harness rediscovers only the same container's loopback binding.

Remaining production work: K-05 migration adoption and backup/restore policy,
reviewed deployment budgets/key operations, release/fresh-install validation, performance and real
server/browser/multi-tab/MFA/J-02 acceptance. See the rollout verification report
for actual outcomes; this runbook alone does not mark those items accepted.
