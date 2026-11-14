# K-07 resumable source runtime handoff — 2026-11-10

## Current API-copy checkpoint — macOS, 2026-11-11

HostedConnect now dispatches by the server's advertised resumable/preview-lease
capabilities. Modern managed runtime uses `/uploads` with full-file/per-part
SHA256 and `/imports` with the creator/source/hash/expiry-bound preview lease;
capability-false servers retain the exact legacy flow. Retry keeps the upload ID
and create key, reads committed server progress, skips received parts and
recovers a ready source after a lost completion response. Form/route abort is
checked through file hashing, part/complete requests, submission and output;
aborting the UI does not claim to undo a mutation already accepted by the server.

The final abort-fixed hosted build passed a real native HTTPS browser witness
using a 4,207,407-byte two-part synthetic ZIP. The first part committed with
HTTP200 before its response was deliberately dropped; explicit retry used one
upload/create key, GET status and only the missing second part. Durable leased
preview, v2 confirm, canonical analysis, reload and membership-revoke DOM wipe
passed against native PostgreSQL and a separate OS worker. The snapshot retains
five Unicode runs, two classes and source unit `%`. Actual DB/source/hash/audit
and cleanup receipts are in
`Aletheia-Kien-API/Bang-chung/20261111-completion/native-hosted-browser/`.

This browser witness uses synthetic server-created sessions and an exact leaf
SPKI exception in a fresh Chrome profile (wrong pin rejected); it does not
qualify IdP login, public CA trust, S/M/L browser capacity or joint release.
The earlier 200 MiB/50-part native repository proof and 100k-row isolated-worker
preview/confirm proof remain separate scopes. The 100k snapshot exceeds the
16 MiB analysis/refresh input budget; other worker outputs remain capped at
32 MiB, confirm alone at 64 MiB. No L end-to-end claim or budget expansion.
Owner requested all Docker-dependent runs be skipped and will test real IdP
tonight. See `docs/k03-hosted-browser-preflight.md` for that receiving checklist.

## Historical runtime handoff — 10 November 2026

Historical 10 November snapshot. The current source includes the binary insertion
patch and bounded 60s publication transaction described as unapplied below; source
and staging-part reads now use binary bytea transport. The historical tests and
L failures are preserved. A new native-PG run on 11 November passed exact
200 MiB/50-part transfer, persisted resume and binary source readback with matching
hashes, under the agreed 500 MiB total test budget. This compressible physical-byte
fixture does not certify load capacity, the metric-row axis or browser acceptance.

Checkout: `C:\Users\Trung Kiên\Documents\ChatGPT\Đồ Án\aletheia-j01-api`, branch `feat/product-api`, base `2bbcdc2` plus uncommitted WIP. No stage, commit, push, merge, branch/worktree change or public deployment was performed. Files below extend the existing source authority; they introduce no second job engine or source repository.

## Shared-file freeze

`hosted/postgres_sources.py` was extended before the updated sole-writer ownership notice. It is now frozen; K-06 owns subsequent writes. Frozen SHA256 is `d7dd6c593092210b8bf100c221486015763267e92bc6b33b5c2b147815cbc9f6`. Exact original preimage and SHA256 were preserved under workspace `k07-prepared/source-preimage-*`; installation refused concurrent edits.

Managed/runtime, job HTTP/worker and shared contracts not listed below remain with their existing owners. The full resumable composition must disable the historical one-shot `/sources` HTTP route; its trusted historical `PostgresSources.put` has no aggregate staging quota. Do not advertise the legacy route as resumable or as satisfying L.

## Producer and repository interfaces

Single schema source: `hosted_contract/uploads.py`. `hosted/upload_staging.py` only reexports public schema types, decodes internal rows and defines safe `UploadRejected` outcomes.

`UploadManifest`: `schema_version=product-api/v1-draft`, `source_type=zip_bundle`, `byte_size`, `sha256`, ordered complete `parts[{part_number,byte_size,sha256}]`, optional `parent_source_id`. There is no filesystem-path field. Part numbers are 1-based contiguous, maximum 512; parts maximum 4 MiB, total maximum 200 MiB. Part hashes and full hash are SHA256 lowercase hex. Total manifest length must match exactly. Parent source grants are rechecked when present; new-source uploads bind the authenticated creator and workspace import policy.

`UploadStatus`: version/workspace/upload identity, state `active|ready|cancelled|expired`, sealed byte_size/sha256, timezone-aware expires_at, received_bytes, received_parts, missing_parts, nullable source_id. Ready requires the full verified bytes and a source reference. Progress counts committed bytes only; received=total before complete means transfer complete, not source ready.

`PostgresSources` keeps its original constructor and adds optional `staging_ttl_seconds=86400`, `maximum_workspace_bytes=200MiB*8`, `maximum_actor_uploads=4`, `maximum_workspace_uploads=16`. Existing required maximum_upload/expanded/entry budgets still apply. Full runtime must supply the actual 200 MiB upload, 512 MiB expansion and supported entry policy rather than the historical 10/12 MiB limits.

Async methods:

```text
is_upload_ready() -> bool
create_upload(*, credential_digest:SecretStr, workspace_id:str,
              request_key:SecretStr, manifest:UploadManifest) -> UploadStatus
upload_status(*, credential_digest,workspace_id,upload_id) -> UploadStatus
put_upload_part(*, credential_digest,workspace_id,upload_id,part_number:int,payload:bytes) -> UploadStatus
complete_upload(*, credential_digest,workspace_id,upload_id) -> UploadStatus
cancel_upload(*, credential_digest,workspace_id,upload_id) -> UploadStatus
cleanup_uploads(*,limit:int=100,grace_seconds:int=300) -> int
```

Every user route resolves current server session, workspace membership and import role. All upload reads/writes require their creator; cancel additionally allows current workspace owner/admin. Parent grant scope is current, not a captured client role. Crossactor/crosstenant identifiers receive the same safe not_found policy. Cancel returns an already-ready source unchanged, preserving success-wins-cancel truth.

Workspace advisory locking serializes admission across creators. Declared whole-byte reservations plus existing stored source bytes share the workspace quota. Expired/cancelled reservations remain charged until bounded cleanup after grace. Complete validates every part and full hash, inspects the archive outside its final DB transaction, then atomically creates one existing `j01_sources` row, marks ready and deletes its private parts. Concurrent complete/retry finds the same source. Parser rejection/irreparable checksum failure marks cancelled and retains terminal receipt plus cleanup grace. Transient I/O/DB failure leaves resumable parts for retry/expiry.

## Routes, schema and grants

`UploadStagingApplication(fallback,sources,sessions,tokens,runtime,*,body_limits,part_limits,clock_check)` implements:

| Operation | Method/path | Body | Result |
|---|---|---|---|
| P06 | POST `/v1/workspaces/{workspace}/uploads` | UploadManifest + Idempotency-Key | 201 UploadStatus |
| Resume | GET/HEAD `/v1/workspaces/{workspace}/uploads/{upload}` | none | 200 UploadStatus |
| P29 | PUT `/v1/workspaces/{workspace}/uploads/{upload}/parts/{number}` | bounded application/octet-stream | 200 UploadStatus |
| P30 | POST `/v1/workspaces/{workspace}/uploads/{upload}/complete` | empty JSON object | 200 UploadStatus |
| P31 | DELETE `/v1/workspaces/{workspace}/uploads/{upload}` | empty body | 200 UploadStatus |

Uses existing edge/session/CSRF guards and Problem serializer. No-store, nosniff and request ID headers apply. Part/hash conflict 409, missing parts 409, expired 410, source invalid 422, quota/oversize 413. Detailed input values, source bytes and server paths are never reflected. One per-process completion semaphore bounds simultaneous whole-source joins/parsers. Edge/process request counts remain K-02's responsibility.

`hosted/upload_staging_migration.py`: explicit additive migrate(conn,schema), verify(conn,schema), grant_upload_runtime(conn,schema,role,profile=api|worker). Requires the verified existing K-05 baseline; adds k07_uploads, k07_upload_parts and its separate checksummed/signature ledger. It does not change K-05's published migration bytes. Runtime never installs DDL. Grant helper first reuses K-02's restricted runtime-profile validator; API gets staging DML needed by the routes, worker only ledger SELECT and no part mutation. No runtime migration rights or deletion of j01_sources are granted.

`scripts/k07_uploads_admin.py migrate|verify|cleanup --schema NAME --database-url-env ENV` is an explicit operator tool; default cleanup limit100/grace300 seconds. It deletes only private part rows under this extension, keeps terminal receipts and never deletes accepted sources/snapshots/shared objects. Provisioning runtime roles and scheduling cleanup are operator/runtime-owner integration steps.

## Verification and limitations

Real PostgreSQL fixture: `tests/integration/test_k07_upload_staging_postgres.py`, `_database()` yields `(pool,schema)` with a new `k07_upload_UUID` schema. Environment `K07_TEST_DATABASE_URL` must be set explicitly; no DSN/password is printed. Own fixture seeds analyst/other/owner in workspace a and analyst owner in b. Helpers `_sources`, `_digest`, `_bundle`, `_manifest`, `_create`, `_parts` are available for current K-07 integration tests. Only its generated schema is dropped, with a checked prefix/length.

Initial focused result: 14 passed, 0 skipped, 15.62s (models plus five real-DB cases). This preceded the parser-failure terminal cleanup addition. The subsequent physical-size attempt was 17 passed / 3 failed / 34.03s: two failures were the K-07 test fixture's unstable repeated clock_timestamp values, now corrected to statement_timestamp; L200MiB reached final source INSERT but exceeded run_transaction's default5s deadline. This failed run is retained and is not acceptance evidence.

Prepared shared-source remedy, unapplied while frozen: workspace `k07-prepared/large-complete-postgres-sources.patch` plus `large-complete-patch-manifest.json`. It uses psycopg binary bytea binding for the final200MiB insertion and an explicit bounded60s publication transaction. K-06 must review/apply; runtime HTTP completion deadline must cover the supported L journey. The 200MiB target is unchanged. A new run is required after any code/schema/budget change; do not add overlapping run counts.

`tests/integration/test_k07_upload_sizes_postgres.py` checks physical1/20/200MiB ZIP_STORED upload bytes, chunk ACK/progress, reconstructed-repository resume, full hash and stored source integrity. It does not establish100k metric rows/10k claims, source-to-result analysis, socket/browser behavior, joint acceptance or50k capacity. Current complete/read paths buffer a whole source in process memory. The API's completion slot bounds this per-process expense; it is not a claim of fully streamed completion or bounded memory under arbitrary replica/edge concurrency.

Focused unit tests additionally cover actual CSRF guard wiring, binary framing, schema/path rejection, output scope, no-store, HEAD, method allow-list and concurrent completion admission. Current final rerun and source manifests are recorded separately under workspace `k07-prepared` and must be consulted before marking any gate passed.
