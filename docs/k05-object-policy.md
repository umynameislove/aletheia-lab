# K-05 object publication, reconcile and scoped integrity

Implemented boundary: `hosted/object_store.py` and `hosted/postgres_objects.py`.
This backend is a managed, durable local/shared filesystem, not an S3 service.
All API/worker processes using the catalog must see the same filesystem and
the same PostgreSQL schema. One object root belongs to one authoritative catalog;
do not share a physical root between unrelated databases or test schemas.
SQL is installed explicitly by `hosted/persistence_migration.py`.

## Publication and authority

1. Authorization belongs to the existing K-03/K-04 session, membership and object
   policy. The storage API is internal and does not accept client authority.
2. Validate the domain model first. `stage(workspace_id, payload, media_type=...,
   schema_version=...)` commits a bounded lease before filesystem I/O.
3. Publish exact bytes using the existing fsynced stage + hard-link primitive,
   then verify the final file's SHA-256, byte length and canonical JSON envelope.
   Directory entries for the marker, managed root chain, SHA-256 directory and
   newly created bucket are synced where the platform supports directory fsync.
   The existing helper is a no-op for directory fsync on Windows; process-crash
   checks do not prove filesystem/machine power-loss durability on that platform.
   JSON record bytes reuse P3's sorted compact JSON plus one newline. The object
   digest therefore differs from the canonical record digest, which excludes
   that newline. Typed P3/P6 model validation belongs to the catalog/importer.
4. Start a short PostgreSQL transaction. `ready(conn, workspace_id, staged)`
   takes the per-digest advisory lock, checks the lease using `clock_timestamp()`
   and inserts/confirms immutable ready metadata. Insert canonical record and
   artifact references in this same transaction, then call `finish` before commit.
5. A lost/expired lease rejects metadata publication. Retry stages/verifies the
   same bytes again. A failed metadata commit leaves an object/lease to reconcile;
   it does not expose a partly committed canonical record.

`k05_objects.schema_version` is a nullable validation hint, so the same exact
JSON bytes may be used both as a canonical record and as a generic artifact.
An absent hint is compatible with a known hint; two conflicting known schemas
are rejected. Byte length and media type must still match exactly. Every record
keeps its authoritative exact domain schema in `k05_records`, and typed record
reads validate it even if the shared object's optional hint is absent.

There is no transaction spanning PostgreSQL and the filesystem. Ready metadata
means the managed immutable file was verified by the trusted staging operation.
An OS actor, storage failure or manual filesystem edit can corrupt/remove it
later, including between verification and metadata commit. Every read still
checks hash/length/schema and fails closed; metadata is never proof that arbitrary
external filesystem changes are impossible. Concurrent nonidentical publication
at an existing digest fails instead of overwriting bytes.

## Reads and bounded integrity

The catalog first validates the exact workspace/project/record/parent scope with
the existing authorization helper. It releases its database connection before
`PostgresObjects.read(workspace_id, descriptor)`. That method obtains a short
lease, reads one bounded object outside any transaction, and verifies exact
hash/length/schema. Missing/corrupt bytes raise `ObjectIntegrityError`; callers
must return the existing safe integrity error, never a partial payload.

The default per-object budget is 64 MiB, configurable at backend construction.
The default lease is 300 seconds, bounded to 1–3600 seconds. Deployments must
choose a lease longer than their allowed filesystem I/O duration. Cancellation
retains the lease until expiry because the filesystem thread may still be active.
No global catalog hash/scan occurs at construction, scoped load or scoped read.

## Reconcile, leases and retention

`reconcile` defaults to a 3600-second grace period (minimum 60 seconds), with at
most 1000 filesystem candidates and 1000 metadata digests by default (maximum
10000). It only removes files below a root carrying the K-05 managed-store marker;
initialization rejects a nonempty directory without that marker. It never adopts
or removes a user's source directory. Bucket/path/symlink/reparse checks fail closed.

Every ready/reference writer must use the same digest advisory lock, namespaced
by `current_schema()` so tenants sharing this catalog coordinate while unrelated
catalogs/object roots do not block each other. Switch all library writers/GC to
the same lock convention together; mixed old/new lock-key versions are unsafe. GC also
locks matching object rows before its final FK/reachability checks. A single
unlink is performed under that lock; hashing, parsing and provider calls are not.
Final files are retained if *any* workspace has a canonical record, artifact
reference or live lease. Sharing bytes across two tenants cannot let deletion in
one tenant remove the other tenant's object. Abandoned stage siblings can be
deleted after grace when no live lease exists, even if their final digest is
referenced. Expired leases, including leases with no file, are pruned in batches.

If physical unlink succeeds and the metadata transaction rolls back, a later
metadata pass removes the missing object's metadata only after grace and only
when no canonical/artifact reference or live lease exists. Missing objects that
are still referenced retain their metadata for investigation and restore.
This is recovery of an orphan, not automatic repair of canonical content.

Pagination is explicit: `next_cursor` is the filesystem path cursor, while
`metadata_next_cursor` is the independent metadata digest cursor passed back as
`metadata_after`. A bounded batch is not a full scrub/reconcile. Continue both
streams to exhaustion; repeat expired-lease pruning if its batch reaches the limit.
The reconciler does not implement project tombstones or retention eligibility:
K-13/K-18 must remove only eligible references through their authorized lifecycle.

## Scrub, alert and recovery

`scrub(workspace_id, limit=100, after=...)` scans at most 1000 object descriptors
per batch, verifies each through a lease and reports only affected SHA-256 values,
verified byte count and a continuation cursor. The catalog's record scrub adds
typed model, canonical/index identity and relation checks. Iterate all batches for
a full audit; invoking one batch does not establish full-store health.

Treat any nonempty corruption report as an operator alert and preserve the
affected canonical metadata and trusted backups. Deny reads of the damaged exact
object/scope; healthy unrelated scopes continue without scanning that object.
Restore exact bytes from a verified backup to an isolated recovery location,
check hash/length/schema and domain identity, then use an approved maintenance
procedure to repair storage and rerun scoped reads plus scrub. Do not silently
rewrite canonical records, synthesize a different hash, or delete evidence.
Projection/index drift may be rebuilt only from verified canonical records; an
object scrub alone does not prove projection parity. Backup/PITR orchestration
and deployment alerts remain K-18/K-16 responsibilities.

No recurring schedule, external service or deployment is created by this code.

## Verification scope, 2026-11-10

`test_k05_object_store.py` covers real filesystem publication/retry, partial-write
failure, abrupt child-process crash after stage fsync, missing/truncated/changed
bytes, canonical/schema mismatch, source preservation and one-object reads with
100 unrelated objects (including unrelated corruption).
`test_k05_objects_postgres.py` uses owned random schemas, real PostgreSQL pools
and controlled thread barriers for duplicate publication, rollback/expired-lease
recovery, shared-tenant references, reader/GC races, cancellation, scoped scrub,
stale-stage/no-file-lease cleanup and unlink-before-metadata-rollback recovery.
These tests do not prove remote object-store semantics, shared staging deployment,
consumer/browser migration/restore parity or K-17 capacity/soak targets.
