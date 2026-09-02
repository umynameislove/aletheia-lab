# Claim-support development corpus protocol

This document defines the prospective, outcome-blind source contract for the
development corpus used to validate the automatic claim-support instrument. It
freezes the source population, family census, eligible diagnosis outputs,
atomic-claim boundary, automatic-label semantics and failure contingencies
before provider calls or claim materialization.

This is a design and feasibility result. It contains no diagnosis output, no
automatic support label, no human judgment and no main or sealed outcome.

The implementation sequence, task IDs and transition gates are maintained in
[`claim-support-delivery-roadmap.md`](claim-support-delivery-roadmap.md).

## Scientific purpose

The later human study asks whether an automatic four-level support instrument
agrees with independent human judgment. That question is only interpretable if
the claim pool is not selected or expanded after observing labels. The corpus
protocol therefore separates three stages:

1. freeze a complete development request population and its reserve before any
   provider call;
2. freeze the atomic-output schema and automatic relation implementation before
   extracting claims;
3. materialize, label and deterministically sample claims only after every
   feasibility blocker is cleared.

The human-validation thresholds and 200-claim sampling contract remain defined
by `claim_support_validation_protocol.json`; this protocol determines how an
eligible development pool may be created without outcome-dependent selection.

## Bound source artifacts

The protocol content-binds four pre-existing artifacts:

| Artifact | Bound responsibility |
|---|---|
| Claim-support validation protocol | 200-claim target, four labels, family/output caps, blinding and acceptance gates |
| Diagnosis variant-fairness freeze | Exact variant inventory and immutable model, context, tool, evidence and prompt budgets |
| Mechanism-filter manifest | Evidence-accountability eligibility and the prohibition on causal scoring for non-admitted mechanisms |
| Fault-family inventory | The currently declared development family census |

A byte change in any bound artifact invalidates verification. A new scientific
choice requires an explicit protocol revision; it cannot be inherited silently.

## Source and mechanism boundary

Only the `development` partition may contribute claims. Synthetic, padded, main
and sealed claims are forbidden. The corpus evaluates evidence accountability,
not causal-diagnosis accuracy. It includes the three declared mechanism tracks:

- `data_drift` retains its registered `rejected` disposition;
- `preprocessing_mismatch` retains its registered `rejected` disposition;
- `label_noise` retains its registered `assumption_limited` disposition.

Including these mechanisms in the evidence-accountability corpus does not admit
them as causal ground truth. Their dispositions remain visible in evaluator-only
provenance and causal diagnosis scoring for non-admitted mechanisms remains
forbidden.

## Prospective family census

The primary population contains five families per mechanism, for 15 families in
total. Each mechanism also has two ordered reserve families, for six reserves.
Every family is planned under `full`, `missing_key` and `noisy` visible-evidence
conditions. Family identities and the complete primary request census must be
content-bound before a provider call.

The design is derived from the existing sampling caps:

- target: 50 claims for each automatic support label;
- maximum: five claims from one family for one label;
- maximum: two claims from one output for one label;
- minimum: 10 distinct families and 25 eligible outputs per label.

The current inventory declares five data-drift families and no explicit
preprocessing-mismatch or label-noise family settings. It can therefore supply
at most 25 selectable claims per label under the frozen family cap, below the
required 50. The tracked receipt reports this limitation rather than padding the
pool or relaxing diversity.

Reserve families may replace only a mechanism-local family found technically
ineligible before execution. Automatic labels, human judgments, output quality
and observed quota scarcity cannot trigger replacement. After execution, an
insufficient label stratum blocks materialization; it does not authorize extra
families, duplicated claims, changed quotas or threshold revision.

## Eligible diagnosis outputs

The claim pool accepts normalized outputs from `A1`, `A2`, `A3`, `B0`, `B1`,
`B2`, `CodeGraph` and `FULL`. Each requires an adapter to the same atomic
claim/evidence contract. Inclusion in this pool does not change the fairness
freeze's primary-versus-separate reporting rules.

`B3` is excluded because its pinned LogDx-CI native output and metrics are
external-only and explicitly non-pooled. Converting that native output after the
fact would manufacture a comparison contract that the fairness freeze does not
contain.

## Atomic claim boundary

An eligible output may contribute at most five schema-native atomic claims.
Each claim carries one frozen claim type, visible evidence identifiers and the
SHA-256 identity of its source record. Claims may be extracted only from
structured `diagnosis-output/2` atomic fields.

Sentence splitting, punctuation splitting and free-text fallback are forbidden.
Those methods allow the unit of analysis to change after outputs are visible and
can alter both label prevalence and error denominators. A concrete, versioned
`diagnosis-output/2` schema manifest must be frozen before materialization.

## Automatic support semantics

The automatic instrument may read only claim text, claim type and visible
evidence. It cannot read mechanism, evidence condition, variant, hidden ground
truth, human judgment or main outcome. The decision precedence is:

1. `contradicted` when visible evidence conflicts with any material claim part;
2. `unsupported` when visible evidence supports no material claim part;
3. `partially_supported` when some but not all material parts are supported;
4. `fully_supported` when every material part is supported and none conflicts.

The implementation manifest and its test corpus must be frozen before real
claim extraction. Human judgments evaluate the automatic label; they never
rewrite it. A model cannot stand in for either independent human rater.

## Current feasibility result

Run the source-only verifier:

```bash
PYTHONPATH=src python scripts/verify_claim_support_corpus_protocol.py verify
```

The expected status is
`corpus_protocol_frozen_source_expansion_required`. The exact current blockers
are:

- `automatic_instrument_manifest_pending`;
- `diagnosis_output_v2_schema_pending`;
- `insufficient_development_family_census`;
- `label_noise_family_manifest_pending`;
- `preprocessing_family_manifest_pending`;
- `reserve_family_census_pending`.

The stronger materialization gate intentionally returns a blocking exit code:

```bash
PYTHONPATH=src python scripts/verify_claim_support_corpus_protocol.py \
  require-materialization-ready
```

Clearing a blocker requires a separately reviewed, content-addressed artifact.
The future diagnosis-output schema and automatic-instrument manifests must be
self-hashing, must declare that no provider call or claim materialization has
occurred, and must bind this corpus protocol where applicable. Mere file
existence cannot clear either blocker. The receipt is then recomputed from
repository state. A green structural audit
does not itself authorize provider calls, claim extraction, human packets or a
main evaluation.

Expanding the family inventory changes a content-bound scientific input. It
therefore requires a prospective protocol amendment with a new protocol hash
and feasibility receipt while every outcome flag is still false. The current
freeze is retained as the auditable reason for that amendment rather than being
silently rewritten after outputs exist.
