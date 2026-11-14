# Canonical upload and P19 — single-writer handoff

The K-05 storage ports below are implemented and tested. Actual HTTP → durable job → worker → canonical publication → API readback is owned by the callers and requires its own integration evidence. Earlier V4 storage checks do not certify this continuation.

## Ownership and authority

- K-05 owns `canonical_results.py`, `upload_publication.py`, `native_upload_records.py`, catalog codecs and appended core migrations. Existing V1–V4 SQL/checksums remain pinned.
- K-06 is the sole current writer of `postgres_jobs.py`: enqueue, worker captured-context policy, fence/terminal state, retries and same-transaction audit. This supersedes the earlier K-04 queue ownership note. No second queue or mutation ledger.
- K-04 owns source/project policy, managed/upload runtime wiring, CatalogAccess/HTTP/result adapters, grants/review/delete. K-02 owns P19 DTO/controller/pure worker and the existing supervisor. K-03 owns session/worker authority; J-01 owns shared contract/fixtures and end-to-end integration.
- For a non-NULL `j01_upload_snapshots.canonical_record_id` or `j01_jobs.canonical_result_id`, canonical records and verified objects are the payload authority. The JSON columns are compatibility projections; linked reads must verify/read canonical data and fail closed on corruption. Existing NULL-link rows retain their old authority until explicit cutover. No automatic backfill or live-schema apply has been performed.

## Native models and reads

`UploadSnapshot` keeps `upload-snapshot-*`, schema `hosted-upload-snapshot/v1`, and Git `not_supplied`. `UploadResultRecord` wraps the unchanged ProductView with workspace/project/snapshot/optional-parent scope, schema `hosted-upload-result/v1`, and `record_id == view.result.id`. Do not coerce an upload into a P3 Git snapshot or local ProductLifecycleRecord.

```text
build_initial_upload_result(snapshot, question, view) -> UploadResultRecord
build_upload_follow_up_result(workspace_id, parent: ProductView,
                              view: ProductView) -> UploadResultRecord
result_view(record: ProductLifecycleRecord | UploadResultRecord) -> ProductView
```

The follow-up factory uses the existing pure lifecycle ID/binding/validation helpers. Derived IDs remain `p6-result-*`; no router prefix change or fake Lifecycle instance. Initial result validation uses the existing initial-upload algorithm.

`PostgresCatalog(pool, objects, *, policy=None)` defaults to current API policy. It returns local or native typed records; `RecordIndex` has trailing `source_id=None` as its ninth field. Logical list kinds `result` and `snapshot` include their local/native kinds in one tenant/grant/tombstone-filtered SQL query before ORDER/LIMIT. Exact ID search is literal. `current(pointer_name=None)` uses declared project provenance: local `project_state`, upload `snapshot`; it does not try alternate stores on failure.

A worker policy adapter must delegate credential checks to the existing captured-context worker authority before and after object I/O, and project `read` to its existing `analyze` grant. A rotated captured digest is not an ordinary API credential. Policy injection does not authorize raw object access or a replacement login.

## Snapshot publication

```text
UploadPublication(objects)
prepare_snapshot(snapshot, *, existing=None) -> PreparedUploadSnapshot
enlist_snapshot(conn, *, authorization, prepared,
                expected_revision=None) -> PublishedUploadSnapshot
```

Prepare validates/hashes/stages outside a database connection. Enlist uses current authorization on the caller's connection, verifies source hash/scope, locks the project, rejects local provenance, installs immutable snapshot/reference/current pointer, and bumps the project revision once. A changed current snapshot requires expected_revision; identical publication does not bump.

Snapshot state ID excludes capture time. If the same ID already has different audit bytes, `SnapshotAlreadyPublished(snapshot_id)` requires rollback, verified catalog read outside the transaction, `prepare_snapshot(new, existing=old)` to retain the first capture, then bounded retry. Never overwrite or repoint its canonical hash. Caller writes the legacy snapshot link and terminal job in the same transaction.

## Result publication

```text
CanonicalResults(objects)
prepare_result(workspace_id, *, parent, record) -> PreparedResult
prepare_initial_result(workspace_id, *, snapshot, record) -> PreparedResult
enlist_result(conn, *, authorization, prepared,
              expected_revision) -> PublishedResult
```

Parents/children are matching local ProductLifecycleRecord or native UploadResultRecord. Initial publication takes the real UploadSnapshot. Prepare performs domain validation, hashing and leased filesystem publication outside the transaction. Prepared data carries validated metadata/relations, not a second mutable payload authority.

Enlist obtains no pool and never commits. The caller must obtain current Authorization on this same connection, then lock project FOR UPDATE before project-policy SHARE checks. Account/session/workspace/member locks precede project locks. It rechecks `analyze`, current grant/tombstone/provenance, pinned parent/snapshot/source/hash/relations and expected revision; inserts ready object + immutable result/references, bumps revision once, and finishes the lease. Identical content at the current revision is a no-op; stale revision still conflicts. Follow-up does not replace current snapshot.

The same transaction MUST include job fence validation, canonical publication, terminal job/link, receipt/effect and audit. If the fenced terminal UPDATE matches zero rows, raise and roll back everything. Returning False after committing a canonical row is invalid. No provider/filesystem/parsing work inside the transaction. Unknown commit outcome is receipt/ledger lookup, not blind replay.

## Queue contract

```text
enqueue_follow_up(*, credential_digest, workspace_id, parent_result_id,
                  expected_revision, request_key, selection_kind,
                  selection_id, question) -> JobReceipt
follow_up_parent(*, claim) -> ProductView
complete_result(*, claim, lease_digest, record) -> bool
```

V5 adds native record kinds/source/provenance and nullable scoped compatibility links. V6 adds operation `follow_up` with exact stored request keys `{project_id,parent_result_id,expected_revision,selection_kind,selection_id,question}` and scoped project/parent FK. No disguised analyze operation. Server resolves the project and exact parent. Replay an existing receipt before checking an advanced revision; new admission checks revision without incrementing it. Two admitted jobs at one revision can have only one publication winner. Retries retain the original intent.

K-04 runtime object root is `storage.object_root/catalog`. Core migration is explicit/operator-only; K-04 project policy and K-07 staging/preview have separate verified ledgers. K-07's known trigger requires strict core-signature integration; unknown triggers must still be rejected.

## Evidence and remaining gate

Evidence root: `C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/k05-verification-20261110/canonical-results`.

- Connector: 12 unit tests passed. PostgreSQL: 10 local + 9 native cases, 19 unique passed in 65.01s; relevant 19-file manifest unchanged.
- Final native rerun: 9 passed in 29.76s, 19-file manifest unchanged. This overlaps the preceding 19 and is not an additional independent total.
- Native tests use a real synthetic ZIP → existing PostgresSources.put → UploadProject.confirm, then canonical snapshot/initial/follow-up, scoped reads and paging. They cover CAS/replay/stale, same-transaction rollback/late fence/cancellation, wrong source/hash/scope/local provenance, first-capture recovery and default current.

These storage tests do not replace caller integration through the actual queue/runtime, browser/consumer acceptance, 200k/2m catalog target, 100k integrity target, soak or restore.
