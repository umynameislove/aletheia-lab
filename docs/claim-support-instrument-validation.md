# Claim-support measurement validation protocol

This document freezes the preparation and analysis contract for validating the
automatic atomic-claim support instrument against independent human judgment.
It prepares a study; it does not report human agreement or scientific validity.

Repository documentation intentionally separates this scientific protocol from
internal scheduling and personnel assignments. A real study remains unauthorized
until an immutable development-corpus closeout and private-rater readiness review
both pass.

## Registered sample

The source is a development-only claim pool produced after the automatic system
is frozen and before main outcomes are opened. The target is exactly 200 atomic
claims, within the accepted 150–250 range. The deterministic sampler selects 50
claims from each automatic label and round-robins across claim type, evidence
condition and system variant. Within each automatic label, no family contributes
more than five claims and no output contributes more than two.

The four ordinal labels, from least to most supported, are:

1. `contradicted`: visible evidence conflicts with a material part of the claim;
2. `unsupported`: visible evidence does not justify the claim;
3. `partially_supported`: visible evidence supports only a bounded part;
4. `fully_supported`: all material parts are supported by visible evidence.

Claims cannot be synthesized or duplicated to fill a quota. If the development
pool cannot supply the frozen target and caps, preparation fails closed.

The prospective source population is governed separately by
[`claim-support-corpus-protocol.md`](claim-support-corpus-protocol.md). The
forward-linked materialization closeout now binds 15 primary and six reserve
families, the eight comparable diagnosis variants, schema-native atomic
extraction, automatic-label semantics and non-adaptive contingency rules before
provider calls. The earlier five-family blocked receipt remains immutable
historical evidence and is not rewritten.

## Blinding and adjudication

Two independent human raters receive the same randomly ordered blind claims and
visible evidence. They do not receive the automatic label, original IDs, family,
condition, variant, mechanism or hidden ground truth. A separate evaluator-only
mapping is opened after both ratings are locked. The hash-bound human workflow
validates exact claim coverage, packet identity, rater-slot separation,
visible-evidence citations and human independence attestations before accepting
either completed packet.

One adjudicator resolves every disagreement and every case where either rater
marks `contradicted`. A model or model-as-judge cannot replace either independent
human rater. The tracked repository contains only the outcome-blind protocol and
preparation receipt; it contains no fabricated or prefilled judgment.

## Prespecified endpoints

The study passes only if all four gates pass:

- quadratic-weighted inter-rater Cohen's kappa is at least 0.70;
- automatic-label versus adjudicated-label four-class macro-F1 is at least 0.80;
- false-supported rate is at most 10%, with automatic-supported predictions as
  the denominator;
- contradicted-to-supported rate is at most 5%, with adjudicated contradicted
  claims as the denominator.

Uncertainty uses 2,000 deterministic bootstrap replicates clustered by
`case_family_id`. The report must also include the complete four-by-four
automatic-versus-adjudicated confusion matrix and adjudicated label prevalence.
An empty risk denominator, incomplete sample, duplicate identity or insufficient
valid bootstrap census blocks the report.

## Claim boundary and next gate

First verify the outcome-blind corpus source contract:

```bash
PYTHONPATH=src python scripts/verify_claim_support_corpus_protocol.py verify
```

`corpus_protocol_frozen_source_expansion_required` is the correct current
status. It is not a scientific failure: it proves that missing family diversity
cannot be hidden by padding, adaptive execution or a changed sampling cap.

Run the outcome-blind verification with:

```bash
PYTHONPATH=src python scripts/prepare_claim_support_validation.py verify
```

The current valid status is
`outcome_blind_preparation_complete_human_validation_pending`. It means the
protocol, deterministic sampler, packet schemas and analysis gates are ready.
It does **not** mean that the corpus is materialization-ready or that agreement,
macro-F1 or either error-rate gate passed.
Those statements require a real development claim pool, two completed human
packets, adjudication and a frozen validation report. Failure of any gate blocks
confirmatory support-rate claims; thresholds cannot be changed after results.

The original onboarding workflow remains immutable historical qualification
evidence. Its rubric left the precedence between material conflict and partial
support ambiguous in three synthetic controls. It is retained and verifiable,
but no new rater qualification uses it. Version 2 clarifies that direct conflict
with any material claim part takes precedence over partial support and uses a
fresh 20-case fixture so feedback from the earlier exercise cannot leak into a
new qualification attempt.

Verify the active version 2 human boundary without creating an annotation:

```bash
PYTHONPATH=src python scripts/claim_support_human_workflow.py verify \
  --workflow configs/evaluation/claim_support_human_workflow_v2.json
PYTHONPATH=src python scripts/claim_support_human_workflow.py dry-run \
  --workflow configs/evaluation/claim_support_human_workflow_v2.json
```

The dry-run prepares the balanced 20-case synthetic onboarding material in
memory and writes no artifact. Real onboarding delivery requires an explicit
destination outside the repository. The command creates separate rater
directories and a coordinator-only answer key; the directories must never be
combined when sent to raters:

```bash
PYTHONPATH=src python scripts/claim_support_human_workflow.py \
  prepare-onboarding \
  --workflow configs/evaluation/claim_support_human_workflow_v2.json \
  --output-dir /private/path/claim-support-onboarding-v2
```

After a rater fills only the submission template, run the outcome-blind
submission precheck. It validates packet binding, exact coverage, order,
citations, rationales and attestations without opening the answer key or writing
an artifact:

```bash
PYTHONPATH=src python scripts/claim_support_human_workflow.py \
  validate-submission \
  --workflow configs/evaluation/claim_support_human_workflow_v2.json \
  --packet /private/path/claim-support-onboarding-v2/rater-1/blind-packet.json \
  --submission /private/path/rater-1-completed-submission.json
```

The coordinator then locks the unchanged submission. The output must also stay
outside the repository:

```bash
PYTHONPATH=src python scripts/claim_support_human_workflow.py lock-submission \
  --workflow configs/evaluation/claim_support_human_workflow_v2.json \
  --packet /private/path/claim-support-onboarding-v2/rater-1/blind-packet.json \
  --submission /private/path/rater-1-completed-submission.json \
  --output /private/path/locked-v2/rater-1-completed.json
```

Incomplete coverage, reordered IDs, a wrong rater slot, a foreign evidence ID,
an inaccurate attestation, altered packet binding or a non-identical replay is
rejected before a completed artifact exists. The evaluator mapping stays closed
until both rater packets are locked.

Onboarding results are qualification evidence only and are excluded from every
scientific denominator. Passing onboarding does not materialize the 200-claim
sample, open the evaluator mapping or authorize main evaluation.

The retired version 1 boundary remains reproducible with an explicit workflow
path:

```bash
PYTHONPATH=src python scripts/claim_support_human_workflow.py verify \
  --workflow configs/evaluation/claim_support_human_workflow.json
```
