# K13 — deletion, retention and recovery fences

Deleting a project commits a tombstone and immediately hides it from ordinary
project reads. The existing `DeletedProject` receipt still says `tombstoned` and
`retained`. It does not claim that private bytes or backups have been erased.
The separate deletion status reports `retained`, `purging` or `purged`, the
eligibility time, actual completion time, removed canonical rows, pending object
hashes and retained shared objects. Current workspace/project read permission
is required, including after deletion.

## Operator policy and bounded work

The owner policy defaults to 30 days for deleted projects, 7 days for terminal
job request/result/attempt payloads, and 3600 seconds of managed-object grace.
A policy change affects future deletion eligibility only. A historical deletion
that predates the K13 ledger starts its conservative retention clock when the
migration first sees it; its status says `migration_first_seen`. No historical
deletion date is invented. Retention is eligibility, not a guarantee of instant
purge at that time. An operator must schedule maintenance.

Run the explicit owner maintenance tool every five minutes with a batch limit
of 100. It first lists due projects and makes at most one bounded cleanup step
per requested project. Repeat until `purged`; a wait result is resumable. Use
`expire` to remove expired previews and eligible terminal jobs. Read-only status
and ordinary request paths never scan all projects or purge files. Runtime API
and worker roles cannot configure or run purge. Never use the owner credential
in the application process.

Purge keeps the minimal project row, canonical deletion record, audit action IDs
and immutable deletion ledger. It removes private comments, exported handle
rows, derived projections, pointers, confirmations, parent edges and canonical
project records in dependency order. Old terminal jobs are removed leaf-first
so their retry foreign keys remain valid. Active/non-expired jobs hold cleanup;
expired jobs use the normal fenced lifecycle transition before removal. No
job completion can publish back into a tombstoned project.

Expired export handles remain immutable replay receipts while their project is
retained: the same request key must continue returning an expired response and
must not mint a fresh TTL. Their canonical artifact bytes remain referenced
until project purge. The expiry report explicitly counts retained receipts; it
does not pretend that an expired URL alone freed storage. Append-only action
IDs are retained; process log rotation is a separate operator policy.

## Reference and file safety

Before canonical rows or artifact references disappear, their object hashes
are recorded durably for resumable collection. Each digest uses the existing
object lock and checks all workspaces' records, artifact references and active
leases. A shared digest remains available to its other workspace/project.
Leases, grace periods, bounded worker admission and an active backup defer file
removal. Every physical deletion takes the shared backup barrier; the backup
operator holds its exclusive counterpart through database/object capture.
A crash after unlink is safe to resume because absent files are rechecked.

`purged` means eligible private project rows and unshared eligible managed
objects have been handled. It explicitly does not mean all copies everywhere
are gone. The status states `workspace_sources_retained` and
`operator_managed_not_erased_here`: uploaded workspace ZIP bytes, user source
folders, shared objects, offline backups and external copies are not erased by
project purge. No raw source directory is traversed or modified by this tool.
Backup expiry/secure erasure must be documented by the deployment operator.

## Restore without resurrection

A backup alone cannot know about a project deleted after the backup. Keep the
latest authenticated deletion/authority checkpoint outside older backup sets,
and pin its manifest hash in the trusted operator inventory. K18 owns the HMAC,
schema/deployment binding, complete ordered page chain and latest-inventory
gate. A restored database remains offline and without runtime grants until the
latest checkpoint has been applied, restored sessions revoked and current
membership/source/project grants reconciled.

`hosted-deletion-checkpoint/v1` contains typed canonical deletion records and
minimal immutable retention facts. It accepts no SQL or filesystem path. Export
all pages on one repeatable-read connection. Replay is idempotent and owner-only;
it verifies canonical bytes and exact scope. A separate no-FK restore fence also
covers a project or workspace created and deleted after the old backup, so
later import/confirmation cannot recreate that missing project. Conflicting or
stale history fails closed. Checkpoint replay does not by itself enable service
readiness or issue any runtime grant.

## Local evidence and limits

The K13 focused native PostgreSQL suite verifies truthful status, immutable
expiry replay, bounded private-comment/canonical purge, shared workspace data,
reader leases, the live backup barrier, owner-only maintenance, secure trigger
paths, denied counterfeit TEMP triggers, checkpoint replay and absent-project
fences. Separate review migration tests verify preserved historical checksums
and additive retention permission. This local evidence does not qualify a
50,000-project load, production backup expiry, real IdP or joint frontend work.

## Explicit owner commands

Set `ALETHEIA_HOSTED_DATABASE_URL` through the deployment secret mechanism to
the migration/maintenance owner, and substitute the actual owned schema and
existing marked object-store path. Never paste a database URL into logs. Apply
both canonical and review migrations before running retention maintenance.
These commands do not create a store, migrate a schema or issue runtime grants.

```text
python scripts/k13_retention_admin.py due --schema APP_SCHEMA --object-root MANAGED_OBJECT_ROOT --limit 100
python scripts/k13_retention_admin.py expire --schema APP_SCHEMA --object-root MANAGED_OBJECT_ROOT --limit 100
python scripts/k13_retention_admin.py purge --schema APP_SCHEMA --object-root MANAGED_OBJECT_ROOT --workspace WORKSPACE_ID --project PROJECT_ID --limit 100
```

`due` is a bounded inventory page, not proof that all due projects were listed.
Schedule repeated inventory/purge batches, rotate across returned projects and
record each action. A blocked project must not prevent later due projects from
being visited. Do not tight-loop on `retention_wait`, `active_job_wait`,
`job_retention_or_retry_wait`, `objects_or_lease_wait`, `resource_wait` or
`backup_wait`: retry at the next scheduled interval. `complete`/`purged` is the
actual finish signal. The expire command is separate and bounded per category.

To change operator policy explicitly, provide all three values (this does not
change the fixed eligibility of existing deletion receipts):

```text
python scripts/k13_retention_admin.py configure --schema APP_SCHEMA --object-root MANAGED_OBJECT_ROOT --project-days 30 --terminal-job-days 7 --object-grace-seconds 3600
```

Keep backups and checkpoint inventory under their independent operator expiry
policy. Deleting native test clusters/data frees their allocated files after
processes close them; production PostgreSQL `DELETE` generally makes pages
reusable internally and does not immediately shrink its files on disk. This
maintenance tool makes no disk-space reclamation promise.

A full due inventory uses keyset pagination: pass each returned `next_cursor`
JSON array as the next command's `--after` value, continuing until it is null.
At the next scheduled sweep start again without `--after`, so blocked and newly
eligible projects are revisited. Preserve the JSON cursor as one shell argument.
