# P6 Product Core implementation notes

## Starting point

- Repository: `https://github.com/umynameislove/aletheia-lab.git`
- Working branch: `feat/p6-product-core-kien`
- Base revision (`origin/main` and initial `HEAD`):
  `ec781efcf533f626548d58c25e7ffc4372f01aa3`
- Development environment: Windows PowerShell, Python 3.11.9, local `.venv`
- Baseline command:
  `.\.venv\Scripts\python.exe -m pytest tests/integration/test_project_import_transaction.py -q`
- Baseline result: `3 passed`
- Dependency check:
  `.\.venv\Scripts\python.exe -m pip check`
- Dependency result: `No broken requirements found.`

These facts were captured before the first P6 source change. The handoff package is
kept outside this repository and is not copied into the product source tree.

## Scope boundary

P6 adds a product-facing service and a stable product projection around the
existing P3 project pipeline. Existing P3 security, import, mapping, snapshot,
regression, evidence, lineage, and persistence behavior remains the trusted
foundation. P6-specific lifecycle and presentation behavior belongs in a new
product layer; it must not weaken P3 validation or expose evaluator-only data.

The public surface is `ProductService(store_root: Path)` with these methods:

1. `demo_view()`
2. `preview_import(root)`
3. `confirm_import(preview_id, mapping)`
4. `refresh(project_id)`
5. `analyze_mock(project_id, snapshot_id, question)`
6. `follow_up(result_id, selection_kind, selection_id, question)`
7. `view(result_id)`
8. `export_report(result_id, format)`
9. `delete_project(project_id)`

Public failures use a safe `ProductError(code, safe_message)`. Public views and
errors must not expose filesystem paths, raw exceptions, secrets, imported text,
raw dataset rows, or hidden/evaluator truth.

## Reuse and gap map

| Product method | Existing P3 capability to reuse | P6 adapter or real gap |
| --- | --- | --- |
| `demo_view` | Existing public/diagnosis visibility conventions | Add a deterministic product fixture and `ProductView` projection. |
| `preview_import` | Root grant, secure collection, import policy, and P3 validation | Add a staged preview lifecycle. A preview must not persist a valid project or snapshot. |
| `confirm_import` | Mapping validation/binding, snapshot construction, and transactional `ProjectStore` writes | Revalidate the staged source at confirmation time and atomically persist the new product identity plus P3 records. |
| `refresh` | Import, mapping, snapshot, regression, evidence, lineage, and store APIs | Add durable, safe refresh authorization/source metadata without exposing an absolute source path. Preserve the previous snapshot if refresh fails. |
| `analyze_mock` | Diagnosis-visible evidence and lineage projections | Add deterministic offline analysis, immutable result storage, product claims, conversation state, and graph projection. No provider call is allowed. |
| `follow_up` | Existing visibility rules can constrain referenced evidence | Add deterministic selection validation, conversation turns, and immutable derived results. |
| `view` | `ProjectStore` record loading and P3 projection helpers | Add the stable `ProductView` schema and result lookup/scope checks. |
| `export_report` | Stored result/evidence/lineage data | Add JSON, Markdown, and PDF renderers over the same saved product projection. |
| `delete_project` | No P3 purge API exists | Add a reference-safe, idempotent product deletion transaction covering all product and owned P3 records. |

## Current architectural constraints

- `ProjectStore` currently persists P3 bundles, snapshots, comparisons, events,
  evidence, and lineage records; it does not implement the P6 result,
  conversation, preview, project-lifecycle, export, or deletion contracts.
- P3 source paths are transient and are not stored. Refresh after reopening a
  service therefore needs an explicit product-layer design rather than an
  assumption that a path is already durable.
- P3 mapping remains backward compatible without product metric definitions.
  The P6 confirmation boundary additionally requires explicit, canonical
  `metric_definitions` containing the metric name, direction, and regression
  threshold; these definitions participate in the mapping and snapshot hashes.
- Snapshot identity is content/state based while capture time is record
  metadata. P6 refresh and idempotency behavior must preserve this distinction.
- P3 causal status is unverified. P6 may present diagnosis-visible hypotheses,
  but must not introduce `CAUSES` or `RELATED_TO` graph edges or claim hidden
  causal truth.
- `independent_families` is unknown when unavailable and must be represented as
  `null`, never fabricated as zero.

K02 keeps one physical SQLite store: product lifecycle records use dedicated
`product_*` tables in the P3 `project-store.sqlite3` database. They do not
duplicate P3 objects, importer logic, records, or graph storage. Lifecycle IDs
include their project/snapshot/parent scope in the canonical digest and every
read revalidates both content identity and the caller's expected scope.

K03 keeps staged previews content-addressed and gives each preview a renewable
30-minute confirmation lease stored separately from the immutable preview
record. Re-previewing identical source state preserves the same `preview_id`
and renews that lease. Confirmation at or after expiry fails with
`preview_expired`, requires a fresh preview, and persists no project or
snapshot.

The current shared UI/backend preview contract fixture is
`tests/fixtures/p6_preview_import_v2.json`, with SHA-256
`8e04f2e3b6fb854c8a22e79b0f47fef1663191df146730337b1a6b8434aad4c6`.
It contains only synthetic values and covers resolved JSON, CSV, ambiguous
manual-entry, root-array, and sensitive-withheld candidate behavior. Version 2
adds stable `metric_name_candidates`; an unresolved source returns an empty
list, while a sensitive metric name disables the candidate with a safe label,
no path, and empty structural arrays. The UI type and tests are ready for this
fixture; byte-for-byte UI verification is pending the K04 branch push.

The older `synthetic_p6_view.json` fixture is not the K03 preview contract and
remains only a temporary UI fallback. A shared `p6-product-view/v1` fixture will
be locked in K06 after the backend view, per-turn `result_id`/`runtime`, and
technical-failure invariants exist; K03 does not pre-commit that later shape.

## K04 snapshot and refresh invariants

- `confirm_import` persists the bound bundle, initial immutable snapshot, and
  current product-project pointer in one SQLite transaction.
- `refresh` compares the private source-state seal first. An identical source
  returns the existing snapshot and does not call persistence, change the
  exported index, or mint timestamp-only state.
- A changed source is imported and mapped again. Mapping choices are rebound
  only to the same validated relative paths and compatible source types. A
  missing source, invalid metric payload, or source race creates no visible
  generation.
- A valid change atomically persists the new bundle, snapshot, comparison, and
  current pointer. An adverse metric change additionally persists the P3
  regression event, evidence bundle, and non-causal lineage graph. Reopening a
  refreshed project revalidates these relations through P3 closeout.
- P6 requires one metric definition for every observed metric name. Directions
  are `higher_is_better` or `lower_is_better`; thresholds are finite and
  non-negative. Inclusive adverse comparisons use decimal arithmetic over the
  persisted numeric representation, including the boundary case
  `0.82 -> 0.74` at threshold `0.08`.
- Metric deltas, Git state, and configuration adjacency remain observations.
  Regression events retain `causal_status=unverified`, and lineage never adds a
  `causes` edge.
- Previous snapshots and lifecycle results remain immutable and scoped to their
  original snapshot after refresh. K04 verifies this with a lifecycle pinning
  probe; K06 repeats it with the complete immutable ProductView result schema.
- Transaction-write failure, unavailable roots, missing mapping sources,
  integrity mismatch, and concurrent source changes fail closed with safe
  `ProductError` values that do not retain raw internal exceptions.

## Implementation order

1. Define the product contracts, safe errors, deterministic identifiers, and
   projection validation.
2. Implement persistent product metadata and staged preview/confirmation.
3. Implement refresh with transactional failure safety and idempotency.
4. Implement deterministic mock analysis, saved results, view, and follow-up.
5. Implement claim/evidence/graph consistency checks.
6. Implement JSON, Markdown, and PDF exports from one saved projection.
7. Implement reference-safe idempotent deletion.
8. Add contract, security, persistence/restart, determinism, export-parity, and
   deletion tests; then run the repository quality gates.

Each item is split into independently reviewable changes. A later step starts
only after the preceding step has been verified.
