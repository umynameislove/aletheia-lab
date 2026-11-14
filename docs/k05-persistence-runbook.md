# K-05 persistence — operator and integration runbook

This is the local qualification of versioned PostgreSQL persistence and a managed filesystem object boundary. It does not deploy a service, schedule a job, change the running API's storage, or qualify production capacity. Keep the original data and all WIP.

## Authority and cutover

Identity/session/workspace membership remain the existing `hosted_identity_bindings` and `j01_*` sources, K-03 transaction ports and K-04 `PostgresAuthorization`. No second identity directory or session engine was added. The explicit baseline migration adopts their actual experimental table shapes and invokes K-04's existing additive migration. K-03 admission remains its own explicit migration; K-05 does not replace it.

P3/P6 local records retain their domain classes, content-derived IDs, distinct canonical-record/byte hashes, exact parent relations and semantic ProductView content. Local ProductService uses its SQLite store. An imported project is explicitly cut over to the hosted catalog; do not dual-write it or fall back to an old SQLite copy after a PostgreSQL failure. The local copy is a recovery/input artifact, not a second authoritative live store. Historical local source paths/preview facts do not grant hosted filesystem access.

The existing J-01 upload flow still uses `PostgresSources`/`PostgresJobs` (BYTEA/JSONB) and upload snapshot provenance. K-04 owns those adapters and runtime integration. This change does not silently redirect that flow into the imported local P3/P6 catalog. Hosted project sharing and public catalog/read/claims/evidence/graph routes must be connected by their owning task on the same schema and authorization ports. See `k05-access-paths.csv`; storage support is not an implemented HTTP operation.

`PostgresCatalog` delegates to K-04's actual `authorize_project` helper and its separate `j01_project_grants` policy extension. It denies tombstoned projects, applies creator/manager/explicit grants in SQL before LIMIT, and rechecks exact membership/session/project policy after filesystem I/O. There is no parallel grant authority. A controlled revoke-during-object-read test covers this integration; general HTTP/browser acceptance still belongs to K-04/J-02.

## Explicit schema migration

Use the approved project environment with the existing hosted dependencies. Supply the database URL through the named environment variable, never an argument, report or query log. The operator creates a new approved non-public schema, or names an existing schema whose backup/legacy shape and ownership have been reviewed. No tool here creates or drops arbitrary schemas automatically.

```text
python scripts/k05_storage_admin.py migrate --schema <approved-schema>
python scripts/k05_storage_admin.py verify --schema <approved-schema>
```

Migration is caller-transaction-owned: success output follows commit. Unknown versions/checksums, installed structure drift, invalid parent/job rows and K-04 ownerless workspaces reject safely; they are not rebuilt or guessed. Inline SQL is packaged with the module, so source/wheel use the same DDL bytes. V1 adopts or creates existing baseline tables; v2 adds catalog/ready objects/scoped parents/current pointers/import checkpoints, purposeful indexes and canonical immutability/reference-retention guards. Appended v3 widens existing jobs with cancelled state and nullable retry_of_job_id under a same-workspace FK; it preserves old rows/receipts and the original v1/v2 checksums. Appended v4 adds the purpose-bound workspace/type/record-ID index for workspace result paging. Verified v2/v3 prefixes upgrade transactionally; core verify rejects it until the current version is complete. The operator CLI then explicitly installs/verifies K-04's separate project-policy extension in the same outer transaction. Core migration does not import that extension or create a circular dependency. Cancel/retry HTTP and job behavior remain K-04/K-09 ownership. FK constraints remain enabled during tests and bulk seeding.

The tool uses a dedicated one-connection pool; connection timeout 5s, lock timeout 1s, statement/idle transaction timeout 30s and overall action budget 300s. A lock timeout rolls the transaction back. On any failed or unknown outcome, verify the ledger and installed shape before retrying. A returned False from `verify` is a failure. Never reset a checksum or run a destructive down migration to make a mismatch disappear. Forward correction or a separate approved destination preserves old data. Compatibility evidence covers verified v2→v3 preservation and existing source/job repository instances before/after adoption; it is not a deployed mixed-version release rehearsal.

Migration/operator owns schema DDL and version ledgers. API/worker roles must have only their required schema USAGE and table DML; no schema CREATE/ALTER or migration-ledger writes. Policy uses row SHARE locks and may require UPDATE privileges even for guarded reads. The runtime-role test uses an actual separate LOGIN role, not schema owner or SET ROLE, and checks catalog reads plus denied DDL/ledger access. It does not establish RLS (none is used), whole-runtime readiness permissions or the final deployment role allocation. Operator/K-02 must apply the role split for the approved topology and total process/pool budget.

## Objects, publication and integrity

See `k05-object-policy.md` for stage/hash/length/schema verification, committed leases, short metadata transactions, cancellation, grace, GC races and recovery. All processes for one authoritative catalog need the same durable object root. Never share that root with another database/schema. The root marker protects against accidental adoption of a nonempty source directory. This is a filesystem backend; no S3/paid service was introduced.

The filesystem and PostgreSQL are not one transaction. A file verified by the trusted stage operation can be damaged later by external storage/OS actors. Every use verifies exact bytes, canonical typed identity and scoped metadata; corruption fails closed. Catalog reads compare parent projections and bundle artifact references/size/media. Reference-retention guards prevent removing an artifact reference while its canonical parent survives the transaction. Background audits distinguish canonical bytes from rebuildable projections; they do not silently replace canonical content.

Local ProjectStore construction and persist no longer hash the entire catalog. `load` verifies its exact object and graph projection. Migration rebuild validates its graph before returning. Explicit `ProjectStore.verify_integrity()` remains the full SQLite/object/lineage audit; closeout and destructive local purge keep their existing full checks. Missing object I/O returns a safe fixed store error. This deliberately changes when unrelated corruption is found: scoped use or explicit scrub, rather than blocking every unrelated click.

```text
python scripts/k05_storage_admin.py scrub --schema <schema> --workspace <workspace> --object-root <marked-root> --limit 100
python scripts/k05_storage_admin.py scrub-catalog --schema <schema> --workspace <workspace> --object-root <marked-root> --limit 100
python scripts/k05_storage_admin.py reconcile --schema <schema> --object-root <marked-root> --limit 100 --grace-seconds 3600
```

Repeat `--after` with the returned next cursor until exhausted. Reconcile has an independent `--metadata-after` / `metadata_next_cursor`; continue both streams and repeat a full expired-lease batch. One batch is not a full audit. Nonempty corruption reports return nonzero: alert the operator, preserve the affected metadata/backup, deny that scope and restore only verified exact bytes in an isolated recovery flow. Healthy unrelated scopes remain readable. Projection repair must derive from verified canonical objects with an explicit maintenance procedure; no automatic canonical rewrite is provided. Directory fsync is platform dependent; Windows process-crash tests do not establish power/storage-loss durability.

Retention/deletion eligibility is K-13/K-18 authority. GC only reconciles unreachable managed bytes after grace and current ref/lease checks; it does not decide that a project should be deleted. Backup must capture a consistent recovery point across DB, objects, permissions, jobs and deletion history; restoring an old backup cannot silently undo a tombstone. Full RPO/RTO/consumer restore acceptance remains open.

## Legacy importer and parity

See `k05-legacy-import-runbook.md` and `scripts/k05_import_local.py`: a permitted checkpointed copy, explicit project/workspace/user mapping and current authenticated import policy are required. The source opens immutable/read-only, with streaming database fingerprinting and bounded objects/project export. Each project atomically commits metadata/pointers/checkpoint after stage verification, then verifies installed parity. Replay of a complete checkpoint rechecks actual bytes/IDs/relations; it does not trust the journal alone. Conflicts, interruption/cancellation and imported tombstones have recovery checks. No original source directory is deleted or assigned inferred tenant rights.

## Query and maintenance qualification

`k05-access-paths.csv` covers all 38 inventory operations plus actual auth/onboarding/worker/operator paths. Implemented catalog list/filter/keyset queries apply workspace/project/type/snapshot before LIMIT; no full catalog/ProductView is loaded and then sliced for paging. Metadata pages are capped at 100 internally (the draft wire proposal's maximum 200 is not an implemented HTTP limit). Public signed opaque cursor/revision semantics remain K-06; a CatalogCursor is an internal value, not a browser token. Claims/citations/graph paging remains K-10 and is not falsely claimed by paging record IDs.

```text
python scripts/k05_query_probe.py --template-copy <synthetic-checkpointed-copy> --project-id <canonical-id> --synthetic-template --copies <count> --maximum-records <budget> --timeout-seconds <budget> --output <new-report.json>
python scripts/k05_storage_admin.py observe --schema <schema>
python scripts/k05_storage_admin.py analyze --schema <schema>
python scripts/k05_storage_admin.py vacuum --schema <schema>
```

The probe validates real domain objects and publishes real verified bytes, then imports scoped metadata into its own UUID schema with constraints enabled. Repeating the same valid template across tenants measures aggregate cardinality with deduplicated physical bytes, not larger per-tenant history, 50k authenticated users or arbitrary S/M/L distributions. The supplied seed_catalog.py logical JSONL is an input/profile reference, not itself a DB benchmark or domain fixture. Cold cache is not run because shared PostgreSQL caches are not flushed. Each measured data query is one SQL statement; auth/HTTP/pool and object costs must be measured separately. Full plans retain rows/loops/filters/buffers; failures/timeouts are not passes.

Observe reports vacuum/analyze/dead tuples/table/index bytes/invalid indexes, WAL, temp, jobs, schema locks, connection wait categories and orphan/lease counts. Database counters include other clients; pg_stat_wal is cluster-wide. Do not attribute shared deltas to this run. ANALYZE/ordinary VACUUM are explicit, bounded maintenance; no VACUUM FULL, automated retention or schedule is installed. The short mixed workload exercises a live reader, 31 writes, 30 revisions, 15 deletes, ordinary VACUUM and reference-safe GC. It does not replace K-17 two-hour mixed soak/fairness/failover/disk-full or K-18 backup/restore tests.
## V5–V7 continuation and upload cutover

The earlier V1–V4 qualification above is historical. V5 adds native upload snapshot/result kinds, required source binding, declared local/upload project provenance and nullable scoped compatibility links; V6 adds the exact six-field follow_up job request and scoped project/parent FK. Existing NULL links are not automatically backfilled. See `k05-follow-up-publication-handoff.md` for the current single-writer APIs and caller transaction rules. K-06 now owns PostgresJobs; K-04 owns sources, policy and runtime/read integration.

A non-NULL canonical snapshot/result link makes verified canonical objects authoritative. JSON projection must not serve as a fallback for missing/corrupt canonical data. Legacy NULL-link adoption must retain the first capture bytes, verify them before the commit connection, then attach the link with scope/hash/projection preconditions in the same fenced publication transaction. Never bulk-repoint old rows or silently change their canonical hashes.

V7 adds `database_created_at timestamptz NOT NULL DEFAULT clock_timestamp()` to fresh-login intents. It expires preexisting pending/claimed attempts under a table lock before historical backfill, preserves committed history, and does not expire fresh attempts on rerun. The deployment must stop/drain auth nodes, apply the explicit migration and deploy all new K-03 adapters before resuming. An old adapter comparing host creation time to a DB revoke watermark remains unsafe. No used schema was migrated during qualification; see `k05-fresh-login-clock-handoff.md`.

K-07 owns independent staging and preview migrations. Operator order is core → K-04 project policy and required extensions → K-07 staging → preview, with every applicable ledger verified by readiness. The preview extension adds a known index and trigger to a core table. Core integration must recognize only its pinned verified version and exact owned objects, preserve existing core ledger/signatures, and reject partial/tampered/unknown additions. It must never refresh a signature merely because another component owns the change. Strict integration evidence is recorded in the final continuation report when available.

Shared source/runtime/job modules can change independently during integration. Report the tested file manifests, and rerun affected checks after any change. The 108-pass V4 report does not qualify V5–V7 or current shared caller code.
