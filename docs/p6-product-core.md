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
fixture. The backend fixture is available on the K04 branch; byte-for-byte UI
verification remains pending the UI maintainer's confirmation of the v2 SHA.

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

## K05 evidence visibility bridge

K05 adds a product-owned, payload-free projection over the persisted P3
`ProjectEvidenceBundle`. It does not change the nine `ProductService` method
signatures or lock the complete `p6-product-view/v1` shape ahead of K06.

The internal `p6-evidence-projection/v1` DTO contains:

- `evidence_bundle_id`, `project_id`, and the authorized `visibility`;
- stable evidence `id`, `role`, `source_id`, `source_sha256`, provenance links,
  visibility, and redaction state for each visible item;
- explicit `missing_categories` and `omitted_categories` without hidden item IDs;
- `projection_sha256`, derived from canonical JSON for the complete projection.

The generic projector preserves the P3 lattice
`public < diagnosis < evaluator` for verification, while the persisted product
bridge always selects `diagnosis`. Removing a hidden provenance dependency also
removes every dependent item until the projected graph is closed, so an allowed
item cannot retain a dangling link to an evaluator-only item.

`ProductEvidenceReferences` binds `citation_ids`, `counterevidence_ids`, and
`visible_evidence_ids` to one exact `projection_sha256`. Citation and
counterevidence sets are disjoint, both are subsets of the visible evidence
scope, and every ID must exist in the same authorized projection. Hidden,
dangling, or cross-project IDs fail with one safe product error and are never
echoed back.

The store bridge resolves one exact `project_id` and `snapshot_id`, reloads the
snapshot and evidence records through `ProjectStore`, verifies the evidence
snapshot hash, rejects ambiguous generations, and maps missing/foreign scope or
tampering to stable `ProductError` values. It never returns an evaluator
projection and never exposes the source root or raw store exception.

Security integration uses only synthetic inputs and covers instruction-like
README/log text, a synthetic withheld credential, PII redaction, and a JSON
payload scan. Imported text remains inert data and does not enter the product
evidence DTO. K06 consumes this filtered projection; expanded claim graph
semantics and reports remain owned by K07 and K10 rather than being fabricated
in K05.

## K06 immutable ProductView and deterministic analysis

K06 locks the exact `p6-product-view/v1` DTO used by `demo_view`,
`analyze_mock`, and `view`. The shared fixture is
`tests/fixtures/synthetic_p6_view.json`, with SHA-256
`0f5413427d1adf184639bff08181d5a81d41e25dad7c45bfb71788742d957fd2`.
An identical packaged copy supplies the offline demo; every call validates and
returns a fresh value, so caller mutation cannot change later responses.

The schema is strict and immutable. It rejects missing, extra, or incorrectly
typed fields; duplicate IDs; dangling citations, counterevidence, graph edges,
or graph sources; evaluator-visible evidence; and unsupported causal graph
edges. `independent_families` is explicitly `null`. Runtime metadata states
that analysis is a deterministic mock and made no external call.
Each conversation turn binds its own `result_id` and complete runtime object;
the latest turn must match the current result and top-level runtime. This keeps
the contract correct when K08 adds immutable follow-up results or a later
runtime differs from an earlier turn.

Every claim has one required closed-enum `claim_type`. A technical-failure
result has `disposition=null`, no claims, a zero claim denominator, and an
abstaining turn. It may retain diagnosis-visible input evidence, but its graph
contains only Snapshot/EvidenceItem nodes and `OBSERVED_IN` edges; it cannot
contain a claim, disposition, citation, support conclusion, or causal edge.

The snapshot carries canonical `metric_definitions` and `metric_changes` arrays.
Each change is bound one-to-one to a diagnosis-visible metric-change evidence
ID and to one `(run_id, metric_name, step)` identity. Multiple steps may produce
multiple changes for the same metric name and share its definition. Before and
after observations identify the baseline and analyzed snapshots explicitly;
delta is `after - before`, normalized through decimal text arithmetic. Added or
removed observations have a null missing side, null delta, and
`adverse_status=not_applicable`. The backend supplies adverse status, so the UI
does not recompute threshold semantics.

`analyze_mock` loads one exact project/snapshot diagnosis projection through
the K05 store bridge. Citations, counterevidence, and visible evidence IDs are
resolved against that same projection. Product evidence contains stable IDs,
hashes, safe role descriptions, immutable P3 reproduction references, and explicit
missing/omitted categories; it does not copy raw imported text or host paths.
Each `reproduction_ref` contains only the containing P3 `record_id` and its
record kind. A metric-change reference points to the immutable snapshot
comparison that contains it; the evidence ID and source hash identify the exact
nested change. The former file-looking synthetic `relative_path` is not exposed.
The generated claim is limited to an observation, the result abstains from a
causal conclusion, and causal status remains `unverified`.

Non-demo views are stored as immutable `p6-result-envelope/v1` lifecycle
records in the existing product SQLite store. Result identity includes the
project, snapshot, and canonical envelope while binding the public result ID
back into the view and disposition node. `view` reloads and revalidates the
record hash, envelope, complete ProductView, project scope, snapshot scope,
result ID, visibility, and non-demo state. Foreign-store IDs, mismatched scope,
and tampered rows fail closed with safe `ProductError` values. A saved result
remains byte-stable and readable after restart and after later project refreshes.

Security integration passes instruction-like README/log content, synthetic PII,
and a synthetic credential through import, refresh, evidence projection,
analysis, persistence, and reload. JSON scans confirm that none of those values,
file names, source paths, evaluator labels, or withheld markers enter the
ProductView. K07 owns richer claim/lineage graph construction, K08 owns
follow-up conversation behavior, and K10 owns report rendering.

## K07 deterministic graph projection

K07 projects the persisted P3 lineage through `project_lineage` and
`project_lineage_table` at diagnosis visibility before creating the product
graph. Every visible evidence source must resolve to one typed P3 lineage node;
comparison, metric-change, and regression-event hashes must also match. Snapshot
evidence uses the P3 state hash while its lineage node uses the record hash, so
snapshot provenance reconciles by its typed content-addressed source ID. Foreign,
hidden, duplicated, dangling, or tampered provenance fails closed with a safe
`ProductError`.

The projection also requires the persisted P3 edge topology: project containment
of both snapshots, comparison `compares_before`/`compares_after`, comparison
`reports` for every metric change, comparison `qualifies` the event, and both
metric change and evidence bundle `supports` relations to that event. Evidence
provenance links must describe the same topology. Removing lineage edges while
retaining its nodes therefore fails closed rather than producing an inferred
product graph.

One immutable canonical projection supplies the sorted graph, row-oriented table,
and textual paths. Text paths reuse the exact graph node and edge IDs. Supported
product edges remain `OBSERVED_IN`, `CITES`, and `ASSIGNED_DISPOSITION`; causal or
generic inferred edges are never created. Saved result graphs remain byte-stable
after refresh, and a technical-failure projection contains only Snapshot and
EvidenceItem nodes joined by `OBSERVED_IN`.
`OBSERVED_IN` is emitted only for the after-snapshot evidence itself and evidence
with a direct provenance link to that snapshot; it is not added to every visible
evidence item. Follow-up results preserve this observation scope exactly. Graph
validation requires one node for every in-view source and exact claim citation
and disposition edges, so an empty or partial graph cannot pass validation.

The exact counterfactual marker is
`Counterfactual comparison: not_available`. It may appear only in
`missing_evidence`, at most once per array, and always as the final element after
real evidence requests. Its absence does not mean a pair is available. The demo
fixture emits it once in its turn and claim because it has no valid pair;
technical-failure turns do not emit it. Changing the literal requires a product
view schema-version change.

Metric changes and metric-observation evidence have exact one-to-one ID parity.
For multiple steps, each `(run_id, metric_name, step)` is a separate change whose
before and after observations retain that same run and step; no step is selected
or pooled implicitly.

## K08 deterministic mock and scoped follow-up

`analyze_mock` and `follow_up` are offline deterministic product operations.
Their runtime remains `deterministic_mock` with `external_call=false`; neither
operation calls a provider or treats its output as a model evaluation. Questions
use one shared strict validator, and public failures expose only bounded
`ProductError` messages.

`follow_up` first loads and integrity-checks one immutable parent result, then
resolves an exact `claim` or `node` ID from that view. It never fuzzy-matches a
foreign, hidden, or dangling selection. A claim or AtomicClaim node preserves
the selected claim's citation and counterevidence roles; an EvidenceItem node
selects only that evidence item; other visible node kinds do not silently add
evidence. Conversation history is not evidence.

The derived view retains the parent's project, snapshot, mode, visibility,
runtime, conversation ID, authorized evidence, and complete prior turn/claim
prefix. It appends exactly one turn. A normal result appends one abstaining
uncertainty claim; a technical-failure result appends no claim or disposition
and does not emit the counterfactual marker. The graph is rebuilt from the same
authorized evidence and claims with the K07 canonical builder, so follow-up does
not introduce a causal edge or widen evidence scope.

Derived results are content-addressed immutable lifecycle records whose
`parent_result_id` names the exact parent. Replaying the same parent, selection,
and question returns the same result. Parent bytes remain unchanged across
follow-up, restart, and project refresh; a follow-up from a historical result
stays pinned to that historical snapshot. Stored tamper and foreign selections
fail closed without exposing record IDs, host paths, imported text, or raw
exceptions.

Every derived result retains the exact parent runtime and result status. A normal
parent adds exactly one claim; a technical-failure parent adds none and cannot be
promoted to complete at the persistence boundary. Disposition graph identities
are scoped by the derived turn, so a disposition node from an earlier result is
not selectable in a later result even though both nodes ultimately reference
their own bound result IDs.

## K09 external-send preflight boundary

K09 does not add a send method, provider client, endpoint, credential lookup, or
retry loop. The only external-send surface is the metadata already returned by
`preview_import`. It describes what categories could be considered by a future
authorized integration; it does not construct or transmit an outbound payload.

`outbound_categories` is derived only from P3 decisions whose action is
`include` or `redact`. Excluded, blocked, and locally withheld items do not
contribute a category. The same response reports the canonical P3
`redacted_count` and `withheld_count`, plus structured issue codes and occurrence
counts. It never includes source bytes, imported instruction text, credential
values, PII values, or an absolute source path. Mapping candidates remain the
separate K03 metadata projection and do not grant send authority.

The underlying `ProjectImportPolicy` fixes execution and network modes to
`disabled` and source mutation to `forbidden`. Imported README, log, and config
content is always untrusted data: fields that resemble provider settings,
system instructions, tool requests, result state, visibility, or evidence scope
cannot alter product control flow. Product analysis and follow-up continue to
use `provider=deterministic_mock` with `external_call=false`.

Previewing and then abandoning or reopening the service leaves only the
content-addressed preview and its confirmation lease. It creates no P3 project
record, product-project pointer, confirmation, outbound payload, or `sent`
state. Preview failures are mapped once to a safe `ProductError`; K09 has no
automatic retry. Dedicated unit and integration tests remove common API-key
environment variables, reject Python socket creation, exercise synthetic prompt
injection/PII/credentials, scan serialized payloads, verify restart behavior,
and confirm source bytes, sizes, and mtimes remain unchanged.

## K10 deterministic report export

`export_report(result_id, format)` first resolves the immutable result through
the existing `load_product_result` boundary. That read verifies the lifecycle
record hash, envelope, result/project/snapshot scope, diagnosis visibility, and
complete `ProductView` before any renderer runs. Export never queries an
evaluator record or reconstructs evidence, graph, lineage, or claims from a
second source.

The supported format values are exactly `json`, `markdown`, and `pdf`. Matching
is strict; aliases, whitespace, and case variants fail with a safe
`ProductError`. JSON is the complete authorized ProductView encoded as
canonical sorted UTF-8 JSON. Markdown presents the same project/snapshot/result
scope, runtime, turns, claims, evidence and reproduction references, graph
identities, wording constraints, caveat, and limitations. Null disposition and
`independent_families` are explicit; the latter is labeled
`not_estimated (null)` rather than rendered as zero.

PDF is generated directly from that same stable Markdown semantic projection.
It is a real multi-page PDF with extractable text, fixed page geometry and a
built-in font. The writer emits no creation timestamp, random identifier, host
path, or environment metadata, so identical result bytes produce identical PDF
bytes across replay and service restart. It uses only the Python standard
library; K10 adds no report dependency.

All three formats are returned as bytes and no export record or file is
persisted. Historical result exports therefore remain byte-identical after a
project refresh and after creating a follow-up. A technical-failure result
keeps `disposition=null`, contains no fabricated claim, and remains labeled as
a technical failure in every format. Export performs no provider or network
call and cannot modify the source project. Existing result lookup errors retain
their safe fail-closed codes for missing, foreign, or tampered records.

## K11 reference-safe retention and deletion

`delete_project(project_id)` removes the confirmed project's complete P3
generation and Product lifecycle scope. The owned Product set includes every
project-scoped state/result/turn plus the confirmed preview and its lease and
binding. The P3 set includes bundles, snapshots, comparisons, regression
candidates, evidence, lineage rows, and their record objects. K10 reports are
returned as bytes and are not persisted, so K11 does not invent a report cache
or report store.

P3 owns the content-addressed object index, so reference analysis is performed
there rather than duplicated in Product. A purge plan validates every record
and bundle artifact, then compares the target project's distinct SHA-256 set
with record and artifact references from every surviving project. Only the
difference is removed. The intersection remains byte- and index-verified and
is reported as `retained_shared_count`.

The SQLite phase is one `BEGIN IMMEDIATE` transaction with SQLite secure-delete
enabled: lineage indexes, P3 records, unshared object rows, Product
registrations/bindings/lifecycle rows, and the confirmed preview are removed
together. That transaction writes an immutable `deletion` tombstone containing
an exact pending CAS digest list. After commit, deletion revalidates both the
object index and embedded bundle-artifact references under a write lock before
unlinking only the still-unowned derived CAS paths one by one. It never
recursively removes a directory. A digest that became live concurrently is
retained and the receipt counts are reconciled accordingly.

A completed tombstone replaces the pending one only after all removable files
are absent, both stores confirm that the old project has no records, and the WAL
has been checkpointed and truncated. The completed tombstone retains the safe
digest manifest so every repeated delete rechecks Product, P3, and CAS state
rather than taking an unchecked fast path. Interruption therefore returns no
success receipt. A later call or reopened service resumes the exact pending
list and completes safely.

The first successful receipt uses `status="deleted"`. A repeated call uses
`status="already_deleted"`, returns `deleted_count=0`, and preserves the
original shared-object count. The first `deleted_count` is the sum of logical
P3 records, Product lifecycle/confirmed-preview records, and distinct CAS
objects actually purged; derived indexes and foreign-key rows are not counted
again. The tombstone contains no source path or imported payload and cannot
reconstruct the deleted project.

Deletion never opens the stored source root. Malformed IDs fail as `invalid_id`,
unknown well-formed IDs fail as `record_not_found`, and ownership, object-index,
payload, or reference tampering fails closed as `store_integrity_error` without
including an ID, filesystem path, or raw exception. Old refresh, analysis,
result, follow-up, graph, and export lookups fail after the transaction, while
other projects and shared objects remain fully loadable. Project-scoped writes
also check the deletion tombstone inside their write transaction, preventing a
stale confirmation, refresh, result, or follow-up from resurrecting the scope.

### K11 verification on Windows

K11 was verified on the existing Windows Python 3.11 environment without a
provider, network call, API key, fixture change, or source-project mutation.
The final focused delete/import/lifecycle/persistence/result gate passed all 44
test cases. The complete selected Product plus affected P3 persistence,
lineage, closeout, and persisted regression suites also passed.

The final project profile completed with
`424 passed, 1 skipped, 3930 deselected` in 94.07 seconds. The two slowest K11
integration tests took 3.15 and 1.62 seconds. The final Windows publication
profile completed with `147 passed` in 288.01 seconds. An earlier run used an
unnecessarily long `--basetemp` and
caused nine unrelated diagnosis-development CAS paths to exceed the practical
Windows path limit; rerunning the unchanged checkout with the short unique
basetemp `%TEMP%\k11wp2` passed. No source change was used to mask that
environmental failure.

Ruff lint, Ruff format on every K11 changed source/test file, strict mypy on
the Product package plus P3 persistence, changed-source C901, and
`git diff --check` passed. The maintainability audit still reports the two
pre-existing Product C901 findings and the pre-existing 1419-line P3 importer
against its stale 1231-line exemption. Neither legacy function was changed by
K11. Refactoring the pre-existing P3 `_migrate` helper reduced the project C901
count from 8 to 7, so K11 improves rather than worsens the frozen baseline.

Both repository copies of `synthetic_p6_view.json` remain byte-identical at
SHA-256 `0f5413427d1adf184639bff08181d5a81d41e25dad7c45bfb71788742d957fd2`.
The older handoff copy was not imported or used to alter the locked contract.

## K12 Windows/offline end-to-end delivery

The `aletheia product-demo WORKSPACE` entrypoint is a thin CLI over the packaged
`run_product_demo` orchestration. It accepts only a new empty real directory,
then creates a bounded synthetic Git project with target, metric, and config
files. The harness confirms the initial 0.6 candidate loss, records a second
synthetic commit with candidate loss 0.8, and therefore exercises a real metric
delta rather than inventing a regression label.

The flow calls the nine-method ProductService boundary through preview,
confirmation, changed-snapshot refresh, persisted regression evidence,
deterministic mock analysis, claim-scoped follow-up, immutable reload and graph,
all three report formats, and reference-safe deletion. JSON, Markdown, and PDF
are published with the shared create-only filesystem primitive. The returned
summary contains stable IDs, counts, relative report paths, report hashes, and
the deletion receipt; it contains no absolute workspace path or imported text.
The harness verifies source bytes immediately before and after deletion.

The integration test rejects both Python socket entrypoints, invokes the real
Typer command, checks the adverse metric input and non-empty evidence/graph,
parses JSON, checks PDF magic bytes, verifies every report size and digest,
confirms all P3 and non-tombstone Product records are gone, denies the old result
deep link, and compares the complete source tree after deletion. A separate
case proves that a non-empty workspace is rejected without mutation.

K12 adds a named `product` test profile that discovers every Product unit and
integration test and explicitly includes the persisted P3 import transaction
and snapshot/regression pipeline. The Windows CI job runs this profile as a
blocking step within its existing 35-minute budget. Strict matrix mypy now
includes the complete Product package, and contract tests bind both changes so
future CI edits cannot silently drop them.

### K12 verification status on Windows

The final Product profile passed all 156 tests in 148.06 seconds. The complete
offline E2E took 3.06 seconds, and the slowest shared-object deletion case took
8.77 seconds. The final contract profile passed all 70 tests in 44.49 seconds;
strict mypy passed 23 source files, Ruff lint and format passed, the
maintainability receipt reported `status: pass` with no findings, and
`git diff --check` passed. Focused correction and post-format regression gates
also passed 37 and 41 tests respectively. The previously recorded project and
Windows-publication profiles remain `424 passed, 1 skipped` and `147 passed`.

The repository-wide full profile is not claimed green on this Windows host. Its
last intermediate run reached `4324 passed, 31 skipped` but exposed a deep
research-attempt-store path beyond the host's disabled legacy Windows path
limit (`LongPathsEnabled=0`). A separate frozen Qwen portability test also
compares a Windows `Path` against a POSIX `/tmp` literal. Both files belong to
the research freeze and this Product branch does not amend their bound bytes or
deselect them. The required integrated UI smoke likewise remains a pre-merge
activity led by Quân; this backend branch does not claim that external
confirmation.

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
9. Add the bounded offline Product demo, its Windows profile, and blocking CI
   contracts.
10. Run the final project, publication, contract, full-coverage, quality,
    hygiene, and security gates before integrated UI smoke.

Each item is split into independently reviewable changes. A later step starts
only after the preceding step has been verified.
