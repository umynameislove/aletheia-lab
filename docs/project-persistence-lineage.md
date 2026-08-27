# Project persistence and typed lineage

P3 stores validated project state in two coordinated layers:

- SQLite indexes immutable records and their relationships.
- A content-addressed object directory stores canonical JSON and artifact bytes.

This boundary persists existing contracts. It does not authorize a project root,
parse new source material, establish scientific admission, or infer causality.

## Store layout

```text
<store>/
  project-store.sqlite3
  objects/sha256/<first-two-hex>/<remaining-hex>
```

Object filenames are derived only from lowercase SHA-256. The database never
stores an absolute source path. Each record binds its project ID, record type,
schema version, canonical model hash and object hash. Supported record types are:

- project bundle;
- snapshot;
- snapshot comparison;
- regression candidate;
- project evidence bundle; and
- lineage graph.

Loading a record rechecks the filesystem confinement, byte count, object hash,
Pydantic contract, canonical model hash, record type and stable record ID.

## Transaction and crash semantics

`ProjectStore.persist()` writes and verifies content-addressed objects before it
starts `BEGIN IMMEDIATE`. Metadata, records, lineage nodes and lineage edges then
become visible in one SQLite transaction. Any exception rolls the database back.

A process failure can leave an unindexed content-addressed object. It cannot expose
a partial record generation. Such an object is harmless because readers can only
resolve objects through validated database records. Existing objects are byte
compared and can never be replaced with different content under the same digest.

SQLite foreign keys, `synchronous=FULL`, integrity checks and migration-history
hashes are enabled. Nested transactions and stores created by a newer schema are
rejected. Reopening the store validates all indexed objects before use.

## Migration contract

Migrations are ordered, append-only and SHA-256 bound in `migration_history`.
Schema v1 introduces immutable objects and project records. Schema v2 introduces
typed lineage nodes and edges with composite foreign keys. Startup fails closed if
the applied history differs from the implementation or `user_version` is newer
than the supported schema.

No migration may reinterpret an existing canonical object. A future migration
that changes a contract must write a new versioned record and retain the prior
object for audit.

## Typed lineage semantics

Every node and edge has a content-derived ID, project ID, source hash and explicit
visibility. Allowed relationships are intentionally non-causal:

- `contains` — project/snapshot membership;
- `observes` — an observation recorded in a snapshot;
- `compares_before` and `compares_after` — comparison operands;
- `reports` — an observed item or metric change;
- `qualifies` — comparison to regression-candidate qualification; and
- `supports` — evidence/provenance support.

There is no `causes` relationship. A P3 regression event remains
`causal_status="unverified"`; temporal order, Git proximity and a metric delta are
not causal proof.

Graphs reject duplicate members, self-edges, foreign-project members, dangling
endpoints, forged hashes and any edge whose visibility is less restrictive than
either endpoint.

## Visibility projections

The ordered visibility lattice is:

```text
public < diagnosis < evaluator
```

Project-item visibility maps as follows:

| Project item | Lineage visibility |
|---|---|
| `outbound` | `public` |
| `diagnosis` | `diagnosis` |
| `local_only` | `evaluator` |

A projection includes only nodes allowed at the requested level. It then includes
an edge only if the edge and both endpoints are visible. This prevents an allowed
edge from revealing the existence or ID of a withheld endpoint. Table exports omit
per-member visibility fields and contain only already-projected rows.

## Determinism and publication boundary

Node, edge, graph and table members are sorted by stable IDs before hashing.
SQLite row order is never trusted; publication indexes use explicit ordering and
canonical JSON. An unchanged generation therefore yields identical graph IDs,
table hashes and exported index bytes across restarts.

The exported store index contains IDs and hashes, not object payloads or local
paths. It is a publication aid, not evidence admission. A later integration may
add a version-pinned, read-only external adapter without changing this persistence
or lineage contract.

## Required verification

The persistence and lineage gate covers:

- canonical round-trip and restart recovery;
- idempotent insertion and conflicting-ID refusal;
- all-or-nothing record visibility under injected failure;
- artifact and record tamper detection;
- migration ordering, future-schema refusal and history tamper detection;
- deterministic graph/table identity under input permutation;
- dangling, cross-project and visibility-downgrade rejection; and
- end-to-end snapshot, comparison, event, evidence and lineage persistence.

## Phase closeout

Persistence is necessary but does not close P3 by itself. The final closeout gate
uses `build_project_closeout()` to reload and reconcile the two mapped bundles,
two snapshots, comparison, regression candidate, evidence bundle and lineage
graph selected for one generation. It binds the current migration history,
content-addressed record metadata, byte-stable exported index and all three
visibility projections in a self-hashed, payload-free receipt.

See [`p3-closeout.md`](p3-closeout.md) for the operational and scientific
boundary and [`p3-closeout-acceptance.csv`](p3-closeout-acceptance.csv) for the
blocking acceptance matrix.
