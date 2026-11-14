# K10 bounded catalog and result reads

The existing `catalog.read` feature retains the full `ResultView` endpoint. New
clients use `catalog.paged_read` for bounded claims, evidence and graph reads.
This feature is advertised only when the installed projection relations are
readable. An older result may still need the explicit operator backfill below;
a missing supported projection returns HTTP 503 `service_unavailable`, without
opening a full canonical object as a fallback.

| Read | Bound and continuation |
| --- | --- |
| `GET /v1/workspaces/{w}/projects` | `limit=1..100`, `cursor`, exact literal ID `search` |
| `GET /v1/workspaces/{w}/results` | `project_id`, `snapshot_id`, `limit=1..100`, `cursor`, exact literal ID `search` |
| `GET /v1/workspaces/{w}/results/{r}/header` | Bounded project/runtime/result summary, snapshot/conversation IDs and exact counts; no metric arrays or full conversation |
| `GET /v1/workspaces/{w}/results/{r}/claims` | `limit=1..100`, `cursor`, optional text `search` of at most 128 characters |
| `GET /v1/workspaces/{w}/results/{r}/evidence` | Same page query; new `EvidencePage` envelope with project/result/snapshot IDs |
| `GET /v1/workspaces/{w}/results/{r}/evidence/{e}` | Optional `claim_id`; verifies the stored citation/counterevidence relation |
| `GET /v1/workspaces/{w}/results/{r}/graph` | `limit=1..100` bounds nodes; up to 1000 edges with their endpoint nodes; opaque continuation visits all nodes and edges |

Claims/evidence text search uses PostgreSQL `simple` full text search and a GIN
index. It performs word search, not a substring or a semantic/provider search.
Each claims/evidence page also stops at 256 KiB of canonical item JSON; a producer
item is bounded to 64 KiB. A byte-limited page can contain fewer than `limit`
items. Clients continue until `next_cursor` is null. Unfiltered claim/evidence
totals are exact manifest counts. Filtered pages and catalog pages state
`total_kind: "unknown"` and `total: null`.

The existing full-result and canonical-record endpoints keep their existing wire
schemas and response size limits. An oversized canonical response returns HTTP
413 `response_too_large`, without a partial JSON payload. Public full
canonical/result GETs first apply a conservative
bound to the authorized canonical object's stored byte size, before opening it;
the final serialized wire response is also bounded. A stored lifecycle envelope
can be larger than its projected view, so use the header/pages for large records.
Internal worker and export callers retain an unbounded-by-HTTP default, preserving
their separate validated object/worker budgets. These endpoints are not a substitute
for bounded reads on large results. Raw metric observations remain in the pinned
canonical snapshots and source hashes for operator inspection; this release does
not advertise raw observation HTTP paging or a report containing every raw row.
The graph is a series of subgraphs: every returned edge includes
its actual endpoints, and nodes can repeat as edge context. Clients deduplicate
by ID and retain the canonical edge kind/source/target. A non-null graph cursor
always has `truncated: true`; `truncated` describes the current segment, so the
last segment can also be truncated while its cursor is null.

Signed cursors bind workspace, actor, session context, membership revision,
query, limit and catalog generation. Result cursors additionally bind immutable
result/snapshot IDs, projection version and checksum. A publication, logical
delete, current-pointer change or an authorized grant mutation changes the
catalog generation. HTTP 400 `cursor_invalid` or 410 `cursor_expired` requires
discarding accumulated page state and requesting the first page again with the
current query. Do not splice a newly restarted query onto an old page sequence.

Projection version 1 is derived from the validated canonical result and published
in the same transaction as the immutable record. The manifest contains its
canonical-record checksum, sorted item/relation checksum and exact counts. Each
item has its own checksum. SQL reads select only a bounded set of projection
rows; they never decode the entire ProductView or open its canonical object.
Authorization is checked before index access and again during HTTP delivery.

There is no additional private payload or authorization cache. Responses retain
`Cache-Control: no-store`; PostgreSQL's own cache does not replace permission
checks. A result ID or projection checksum is never treated as authorization.
No Redis dependency is needed. Cold OS-cache capacity and concurrent-user load
are separate qualifications from the query-plan evidence.

## Operator backfill and repair

Apply and verify the maintained persistence and project-policy migrations before
starting the new runtime. K10 appends its projection migration after the PDF and
durable-attempt migrations. The explicit canonical/project-policy migrators
install two verified actor read indexes once both prerequisites exist, supporting
either prerequisite install order. Existing K-04 DDL bytes/checksums are unchanged.
Only those two precisely verified extra indexes are excluded from the historical
schema fingerprints; partial indexes/extensions or changed definitions reject
verification. The separate `k10_read_indexes` ledger binds their expected version.
The `read-indexes --schema ... --database-env ...` admin action can also perform
this explicit index installation. Startup and ordinary reads never perform DDL.
`catalog.paged_read` remains false until both indexes and projections are readable.
Old immutable canonical objects and result IDs are unchanged.
Projection tables can be empty immediately after the migration: startup and GET
requests never scan all existing canonical objects to fill them.

Use the existing storage administration command with a database **schema-owner**
credential held in the named environment variable and the existing marked object
root. Runtime API/worker roles cannot execute the owner-only rebuild. For example:

```sh
python -B scripts/k05_storage_admin.py rebuild-views \
  --schema OWNED_SCHEMA --database-env OWNER_DATABASE_URL \
  --object-root /absolute/existing/object-root --workspace WORKSPACE_ID --limit 10
```

Repeat with `--after` equal to the returned `next_after` until it is null. The
operator continuation is a literal result ID, not a public HTTP cursor. Each
result is decoded and its immutable identity/checksum checked against canonical
storage. Existing parent relations and applicable scope projections are verified before rebuilding;
history drift fails closed instead of publishing a new read model from it.
Complete decode/validation/hashing runs outside a database transaction and off the
event loop; cancelled CPU work is drained before its caller releases its slot.
A complete manifest is published last. An interrupted result transaction
rolls back all of its new index rows; earlier completed results remain reusable.
For a detected corrupted projection, use `--replace` to repair the current
projection version atomically from the same canonical records. It does not
rewrite canonical objects, move current pointers or remove history.

A future projection algorithm must increment `PROJECTION_VERSION`. Keep old
version rows while older readers are supported; do not serve a partially built
version. Reverting application code does not require destructive down-migration
of immutable history. Missing supported indexes fail closed with a bounded error.
Direct owner maintenance that changes grants outside the authorized writers must
also call `stamp_catalog_revision` in that transaction; payload delivery still
rechecks live policy even if an operator forgets that pagination invalidation.

## Consumer receiving scope

Quân's existing hosted client currently opens a complete ProductView and uses it
for claim/graph selections. The API changes do not alter that frontend. Receiving
work is to dispatch on `catalog.paged_read`, maintain cursor/query state, merge
graph segments by ID, use the evidence relation endpoint for selected claims and
restart cleanly on cursor errors. Do not infer a total from page length or infer
membership from a graph node. Existing complete `ResultView` clients retain their
wire contract; large-view support requires adopting the bounded endpoints.

Local checks cover SQL page bounds, exact relation membership, projection
checksums, cursor invalidation, publication/rebuild rollback and live revocation.
Catalog scale probes separately record actual row counts and plans; their shared
synthetic template scope does not imply millions of distinct analyses, a large
single tenant, a browser witness, remote CI or a joint acceptance signature.
