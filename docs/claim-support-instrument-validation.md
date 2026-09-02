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
[`claim-support-corpus-protocol.md`](claim-support-corpus-protocol.md). That
contract freezes 15 primary and six reserve families, the eight comparable
diagnosis variants, schema-native atomic extraction, automatic-label semantics
and non-adaptive contingency rules before provider calls. Its current receipt
correctly blocks materialization because only five of the required family
settings and neither required implementation manifest are present.

## Blinding and adjudication

Two independent human raters receive the same randomly ordered blind claims and
visible evidence. They do not receive the automatic label, original IDs, family,
condition, variant, mechanism or hidden ground truth. A separate evaluator-only
mapping is opened after both ratings are locked.

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
