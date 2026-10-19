# Current product handoff acceptance

The current bridge checks the deterministic `p6-product-view/v1` contract used
by `ProductService`. It preserves the historical research acceptance module and
fixture, which describe a different synthetic projection. There is no conversion
of current evidence into the former `relative_path` field.

## Acceptance boundaries

| Boundary | Evidence and supported conclusion |
| --- | --- |
| Historical research | `accept_model_load_research.py` retains pinned identity and exact-replay checks. A pinned summary is not an exact replay. No research receipts are ingested into product results by this bridge. |
| Current demo | The committed current fixture is byte-pinned and compared with `demo_view()`. This is demo compatibility, not a live result or UI run. |
| Local backend lifecycle | A synthetic CSV/P3 source oracle checks selected semantics before accepting the output. The real service executes import, refresh, analysis, follow-up, persistence, reopening, reports and deletion. Git collection alone is substituted with a valid synthetic state. |
| Saved projection and JSON export | After source checks pass, the accepted view is a consistency reference for reopening, later refresh and JSON export. Full projection equality is not a second independent observation channel. |
| Markdown/PDF | Existing report tests check selected semantic parity; the local lifecycle test also checks extracted claim and limitation text. This is not visual-layout or arbitrary-format equivalence. |
| HTTP API and dashboard | Not executed by these checks. An actual consumer, graph/table rendering and interaction flow must still be tested before accepting the integrated product. |

The original scientific dispositions and frozen evidence are unchanged. Mock
project observations are not scientific study results, causal verification,
model-quality scores, independent-family estimates or a new research method.

## Read-only projection check

`check_product_handoff(trusted_expected, candidate)` reuses `ProductView` for
strict structure, authorized scope, reference closure and graph/citation parity.
It additionally checks the selected deterministic mock contract:

- Every field of the serialized contract is explicit; schema defaults cannot
  authorize an incomplete projection, including an omitted `step: null`.
- Complete results retain causal abstention. Technical failure has no successful
  diagnosis or disposition.
- Observation claims may be `fully_supported` as observations; that does not
  establish a cause. Follow-up uncertainty claims remain abstained/not-estimated.
- Counterfactual limitations and the visible claim denominator are preserved.
  Follow-up output counts are not inferred from the number of conversation turns.
- Metric classification is recomputed from before/after values, declared
  direction and the inclusive decimal threshold.
- The full raw JSON projection is compared: result wording, missingness,
  per-turn result/runtime bindings, reproduction references, metrics and graph
  contents cannot disappear behind matching IDs, hashes or counts. Object-key
  order and whitespace are irrelevant; JSON array order is preserved, even if a
  schema normalizer would reorder an array.

The expected view must come from an authorized fixture or separately checked
source expectations. Computing its pin from the unchecked candidate does not
create a trustworthy reference. Scope rules catch some equal-but-invalid pairs;
they are not a universal semantic checker for arbitrary free text.

The CLI pins the **reference file bytes**. Its returned `reference_sha256` is the
separate canonical JSON digest; the two identities need not be equal.

```bash
PYTHONPATH=src python3 scripts/accept_product_handoff.py \
  --reference tests/fixtures/synthetic_p6_view.json \
  --expected-reference-sha256 0f5413427d1adf184639bff08181d5a81d41e25dad7c45bfb71788742d957fd2 \
  --candidate /absolute/path/to/consumer-view.json
```

For this demo reference the candidate must also be the demo projection. Live
results instead require a trusted reference for their actual project, snapshot,
result and turn scope; relabeling the demo as live is not sufficient.

Inputs are read-only bounded JSON (1 MiB each). Duplicate members, nonfinite
numbers, symbolic links, changed input bytes, unsupported fields/types and
over-limit documents fail closed. Failures return a generic status without the
input payload or private path. The report explicitly states that backend
execution is not attested by a projection check and API/dashboard integration
has not run. Referenced P3 records are not loaded by this CLI.

## Tested source oracle

`tests/integration/test_product_handoff_local.py` fixes the metric recipe before
execution: candidate loss changes `0.6 -> 0.8`, with lower-is-better direction and
threshold `0.08`; the expected delta is `0.2` and classification is adverse.
Before requesting analysis, the test reads the corresponding P3 operands and
evidence references. It checks those source identities/hashes, reproduction
record kinds, citation scope, observation-edge provenance, mock runtime,
denominators, abstention and declared result/turn/claim wording independently of
the returned ProductView. It does not duplicate the entire output builder or
independently validate P3's own collection algorithms.

Valid, rehashed mutations to claim text, answer, summary, observation edges or
reproduction IDs still load through ordinary persistence. The selected source
oracle rejects them. Only then does projection comparison verify that the
accepted content survives reopen, export and a later source refresh. Deletion
preserves the source files and invalidates the saved product link.

```bash
python3 -m pytest \
  tests/unit/test_product_handoff_acceptance.py \
  tests/integration/test_product_handoff_local.py
```

Both files belong to the product and evaluation profiles. Existing historical
acceptance and product export tests remain in place. Native Windows, remote CI,
real Git collection and HTTP/dashboard execution are separate checks; a local
pass does not substitute for them.

## API/dashboard acceptance boundary

The consumer must use the current [product contract](p6-product-core.md), including
`reproduction_ref`, per-turn `result_id`/`runtime`, claim type, metric definitions
and changes. Preview mapping uses its separate versioned fixture; it is not a
ProductView. No new DTO, scientific checker or HTTP route is introduced here.

When the actual consumer is available, acceptance must retain `null` rather than
zero for unavailable family counts; show failure, abstention and missing evidence;
preserve originating turn/snapshot scope and graph/citation identities; and use
the same saved result for JSON export. Graph/table rendering and follow-up
selection must be exercised in that consumer, not inferred from fixture parity
or a screenshot. This local bridge does not attest HTTP routes, dashboard
rendering or interaction behavior.

A separate local acceptance on 19 October 2026 exercised the built Live app in
Chromium through the actual HTTP bridge and `ProductService`, using synthetic
Git/CSV projects. Eighteen lifecycle checks passed: three-run import, initial
analysis, unchanged and adverse refresh, claim follow-up, reload/back-forward,
stale-response handling, canonical result links, exact JSON/Markdown/PDF
downloads, deletion, and connecting another project without a document reload.
The browser made no non-loopback requests and raised no page exceptions.
One settled 390px Investigation view was also inspected; this is not a complete
accessibility certification or a new benchmark measurement.

That execution used `deterministic_mock`, not a live model provider. It does not
replace native Windows or remote CI qualification, authorize a release, validate
causal claims, or extend the guarantees of the projection checker described above.
