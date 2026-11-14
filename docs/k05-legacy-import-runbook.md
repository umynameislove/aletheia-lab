# K-05 legacy import v1 — operator runbook

This is a storage importer, not a hosted HTTP endpoint or an authorization/provisioning service. Keep the original local data and the checkpointed source copy. Local ProductService continues to use SQLite; after cutover the imported project uses the hosted catalog only. Do not dual-write the two stores. Stored local source paths and preview leases are historical content; they grant no hosted filesystem access and do not enable hosted refresh/confirm on that path.

## Inputs and exact scope

- A permitted, **checkpointed backup copy** containing `project-store.sqlite3` and its `objects/sha256/xx/...` tree. Use SQLite's backup API on an authorized source, then copy its immutable objects with the writer paused or with a consistent backup boundary. Copying the `.sqlite3` main file while a writer has uncheckpointed WAL is unsafe. The importer rejects a nonempty `-wal`, unknown/missing migration versions, checksum drift, links/reparse paths and missing/corrupt bytes. It opens the supplied copy with `mode=ro&immutable=1` and checks that its main database SHA-256 did not change. It never constructs a mutating ProjectStore/ProductLifecycleStore on the source.
- A schema already created/adopted by `hosted.persistence_migration.migrate`, including K-04's existing workspace migration and its additive project-policy extension. The CLI requires both core persistence and project-policy verification to return true; it does not run DDL automatically.
- Existing enabled, verified identity and active import-capable membership, established through the current identity/workspace administration. An authenticated current session credential digest is required; the mapped user must equal the current server actor. Mapping cannot provision an owner or grant membership.
- An empty managed object root, or an existing K-05 object root with the expected format marker. The filesystem backend requires durable shared storage across API/worker processes; no remote object-store deployment has been certified.

Mapping file (replace all IDs with explicit existing facts):

```json
{
  "schema_version": "k05-local-import/v1",
  "projects": [
    {
      "project_id": "p3-project-0000000000000000000000000000000000000000000000000000000000000000",
      "workspace_id": "provisioned-workspace",
      "user_id": "provisioned-user"
    }
  ],
  "preview_projects": {}
}
```

Every project must be mapped exactly once. Confirmed previews retain the project relation already recorded in SQLite. Each unconfirmed preview needs an explicit `preview_projects` entry from its exact preview ID to a mapped canonical project ID; no workspace is inferred from a path, email or source payload. One CLI execution uses one current actor, so run separate mappings/source copies for actors requiring different credentials.

## Invocation

Use the project's Python environment and installed hosted dependencies. Set `ALETHEIA_HOSTED_DATABASE_URL` and `ALETHEIA_IMPORT_CREDENTIAL_DIGEST` through the permitted secret mechanism. Do not put either value into command arguments, logs or this file.

```text
python scripts/k05_import_local.py --source-copy <checkpointed-copy-root> --mapping <mapping.json> --object-root <managed-object-root> --schema <explicit-hosted-schema> --batch-id <stable-operator-batch-id>
```

Pool size is two, pool wait five seconds, statement timeout thirty seconds and lock timeout two seconds. `public` and system schemas are rejected. The CLI prints only a versioned report of fingerprints, counts and disposition, or a fixed safe error. Keep the report with the exact code/schema/config identities. No source data, credential, DSN or private path is printed.

## Commit, parity and recovery

Each project is a checkpoint. Decode and validate the copied SQLite records first; stage/hash/schema-verify objects under committed leases with no PostgreSQL connection held during filesystem I/O. Recheck the existing K-04 import policy. In one short PostgreSQL transaction, lock the project, reject older content over a hosted tombstone, enlist ready objects, insert immutable records and tenant/parent relations, attach artifact references, preserve confirmations/preview lease facts/current pointers, complete the import journal only after rechecking the authoritative current session clock again, and release this attempt's object leases. There is no cross-system transaction.

The source fingerprint covers typed P3/P6 semantic exports, identities, all artifact hashes/sizes/media, pointers, confirmations and leases. The mapping has a separate fingerprint. After commit, and again on a completed-checkpoint replay, compare installed IDs/counts/canonical and byte hashes, parent projections, pointers/confirmations/leases and artifact references, then read and verify every referenced hosted object. A journal row alone is insufficient to return a parity success. P6 results use the same ProductLifecycleRecord and ProductView validators as local consumers; snapshots, history and follow-up turns retain their IDs/content.

If killed before metadata commit, that project's PostgreSQL changes roll back; verified objects can remain as lease-protected orphans. Rerun the same batch against the same immutable copy and mapping. Committed projects return `identical` after parity verification. Other projects can continue from their own checkpoints. Expired leases/orphans are reconciled after the configured grace period, preserving all live/shared references and active reader/job leases. Do not delete original source directories to recover space.

If the batch ID is reused with different source/mapping, or an immutable ID/current pointer/confirmation differs, the importer stops with a conflict. Do not overwrite the pointer, reset the journal or perform a down migration. Retain the source copy, reports and hosted metadata, inspect the permitted namespace with redacted tooling, and resolve the explicit mapping/version/cutover decision. Forward correction or a new isolated destination is safer than destructive rollback. A parity failure after commit requires investigation; the source remains available and no destructive reversal is claimed.

## Boundaries still requiring acceptance

The importer limits each object to 64 MiB and one atomic project export to 256 MiB, failing explicitly rather than truncating. Larger projects need streamed checkpoints. Unreferenced source orphan objects are not canonical project content and are not imported. Source roots are not read after cutover merely because their historical paths were preserved. This does not implement K-06 public mutation/CAS/idempotency, K-09 durable analysis lifecycle, K-13 retention/deletion or K-18 backup/restore.

Synthetic tests cover the old local facade, complete typed hosted read facade, semantic ProductView equality, pointers, paging, tenant separation, exception/cancellation rollback and connection reuse, interruption/replay/conflict, current policy revoke at a deterministic barrier, corruption and imported tombstones. This is not browser/Quân consumer acceptance, clean-machine release rehearsal, a full restore/RPO/RTO measurement, 200k/2m import or a 50k-user load test. Those gates remain with J-03/J-08, K-17 and K-18 and must use the same release/schema/config and real consumer IDs/hashes.
