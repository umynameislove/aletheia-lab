# Proposed native metric-observation v3 — decision required before implementation

Status: proposal for the product/model owner and Quân's consumer. No v3 runtime is advertised or enabled. Existing v1/v2 serializers, identities, limits and fixtures remain unchanged. `primary_dataset_qualified=false` and `--require-primary` still fail before writes.

The current v2 observes at most64series. The seed's1/8/32series produce2/16/64claims; adding source rows or duplicate claims cannot meet100/1000/10000 source-grounded claims. A large model must define a new unit of observation, not only a larger array limit.

## Proposed meaning

Model ID: `upload-snapshot-windowed-metric-observation/v3`.
View schema: `p6-product-view/v3`; canonical upload-result schema must get its own explicit v3 discriminator. Request/capability negotiation must reject v3 on a v1/v2-only worker or reader. Result identity includes model ID, aggregation-policy version and canonical selection; v1/v2 identity bytes stay stable.

One claim describes one numeric metric over one disjoint source-row window in one mapped run/context/class/unit. It states count, minimum, maximum and exact arithmetic mean over finite validated numeric values. It does not assert a cause, rank a model or infer data outside the selected window. Preserve metric direction only as source metadata; no causal/regression verdict follows from a standalone window. Unknown/missing/nonfinite values are accounted separately; an empty numeric window produces an explicit abstention, never an invented numeric claim.

Suggested fixed primary fixture:1000/10000/100000 physically distinct source rows divided into100/1000/10000 windows of10rows, respectively. Metric data must actually contain those numeric observations, source run/context/class/unit must reconcile and each window must have a distinct row range. A repeated value is allowed if present in the source; duplicating a claim or citing the same row window only to reach the target is not allowed. Report actual claims produced; do not label a fixture primary when missing/invalid data reduces the count.

Each claim has one source-bound evidence item with immutable snapshot ID, source payload SHA256, project-item/file identity, record-index range and a canonical SHA256 over the selected normalized rows. Hashes identify actual fixture bytes. All evidence must be accessible under the current source grant. A claim/evidence pair is unique by `(snapshot, item, run, context, class, unit, metric, start, stop, policy_version)`.

Counterevidence is absent when no contradictory observed claim is being made; use an empty array with the caveat that independent causal verification is unavailable. Do not synthesize counterevidence, copy research evaluator truth or call a paid provider. Each claim is `observed`/`fully_supported`/`max_strength=observation` only after its source rows and arithmetic reconcile. Overall causal status remains `unverified`, and a causal question yields abstention.

Graph: one Snapshot node, one EvidenceItem per window, one AtomicClaim per claim and one Disposition node, with existing `OBSERVED_IN`, `CITES`, `ASSIGNED_DISPOSITION` semantics.10000claims therefore naturally imply20002nodes and30000edges, exceeding the guide's minimum10000nodes through actual facts, not padding. Quân must confirm the guide's10000node target is a minimum rather than an exact fixed shape.

## Proposed finite limits

| Boundary | Proposed limit | Failure behavior |
|---|---:|---|
| Validated source numeric rows per analysis |100000| reject before materialization |
| Claims/evidence per result |10000 each| reject beyond cap; never silently truncate canonical result |
| Graph nodes/edges |20002/30000| validated exact source closure |
| Canonical JSON result |32MiB| reject before publication if encoded output exceeds cap |
| Header | existing bounded header budget | counts and model/version only |
| Claim page | existing50default/100max | opaque scoped cursor |
| Graph page | existing finite node/edge budget | endpoint-node repetition and continuation retain current rules |
| Download | existing32MiB bound and Range/current authority | no partial file counted as complete |
| Worker slots/deadlines | existing budgets | no enlargement to hide a failing primary fixture |

Producer must stream source-window aggregation and stage at most one result under existing byte/process admission. Paging projections must be staged from the validated canonical output and keep exact counts/source hashes. PDF for10000claims must have an explicit product policy: either a complete bounded export that meets current budgets, or a negotiated unavailable format with a truthful reason; a clipped PDF cannot count as the complete result.

## Concrete synthetic example

For records0–9 of a fixture `metrics.csv`, run `synthetic_run_0`, metric `loss`, unit `ratio`, values `[0.10,0.12,0.14,0.16,0.18,0.20,0.22,0.24,0.26,0.28]`, one claim states: "These10source observations have loss minimum0.10, maximum0.28 and mean0.19." Its evidence names exactly those rows and their computed digest. Source/snapshot/claim IDs are generated from these fixture bytes by the producer; this proposal supplies no invented live hashes or tokens.

## Decisions needed from product owner and Quân

1. Approve one source-row window as a meaningful product claim; confirm disjoint10row windows and the fixed mean/min/max/count policy, or supply a different source-grounded claim definition.
2. Confirm metric source semantics: existing `metrics.csv` ingestion must retain100kactual numeric observations, including class/unit/context and stable source record indexes. Which mapped data shape and source-file authority should the v3 reader expose?
3. Approve `p6-product-view/v3` plus canonical result discriminator and model/capability negotiation. Confirm the exact UI claims/evidence/header/search/graph/export fields needed before producer wiring.
4. Confirm graph target means at least10000nodes; approve natural20002node/30000edge closure for10000claims rather than suppressing evidence nodes.
5. Decide the complete export/PDF policy under existing32MiB and worker deadlines; do not assume all formats can be truthfully advertised for every v3result.
6. Sign the versioned primary fixture schema, expected rows/claims/graph/encoded bytes and correctness oracles before seeding or running the paid/large campaign.

After the decision: implement producer + parse/serializer/projections/worker/capabilities + consumer/export together; preserve v1/v2 fixtures byte-for-byte; add arithmetic/source/duplicate/overflow/cross-scope tests; then generate a measured physical fixture. This proposal itself closes no primary dataset,50kcapacity or joint release gate.
