# Phase 1 Feasibility Report

> Status: complete; final decision is **GO to Phase 2**.
> Frozen result-lock: `8050de78966315be6f5df5c60bb9b1fcff163513b3621a78f5d78c41fa20c815`

## Purpose

Phase 1 tests whether Aletheia Lab can execute one honest end-to-end slice from a controlled ML change to evidence-bounded diagnosis and reproducible evaluation. It is a feasibility study, not a statistically powered comparison and not evidence of broad generalization.

The slice asks whether a diagnosis can:

1. use only evidence visible to the model;
2. cite evidence that actually supports its claims;
3. avoid promoting an observation to a strong causal conclusion;
4. reduce or withhold its conclusion when decisive evidence is missing;
5. remain robust to a neutral secondary comparison; and
6. leave an immutable, auditable artifact chain.

## Experimental design

| Property | Frozen value |
|---|---|
| Dataset | Telco Customer Churn |
| Mechanism | categorical distribution shift on `Contract` |
| Independent unit | 5 `case_family_id` values |
| Evidence conditions | `full`, `missing_key`, `noisy` |
| Contexts | 15 |
| Variants | A3 evidence contract and B1 plain |
| Model | OpenAI `gpt-4.1-2025-04-14` |
| Requests | 30 matched requests |
| Decoding | temperature 0.0, top-p 1.0, seed 17 |
| Maximum attempts | 2 |

The family, rather than an individual context or model output, is the independent experimental unit. The three conditions within a family are paired siblings.

## Method

### Deterministic data and baseline

The dataset and split are bound by checksums. The baseline pipeline is seeded and tested for repeated-run equality. Phase 1 does not select a fault because it produces a desired result.

### Controlled intervention and honest outcome semantics

Five candidate distribution shifts are generated deterministically. The injected change and the measured metric outcome are recorded separately. Only a measured regression is eligible to be called a failure; stable and improvement outcomes remain visible controls.

The five families contain:

- 3 eligible regressions;
- 1 improvement control; and
- 1 stable control.

### Evidence contract

Each context is represented by a strict, versioned `EvidenceBundle`. Diagnosis-visible evidence is separated from evaluator-only ground truth. Unknown fields, invalid distributions, stale checksums, mismatched provenance and leakage-bearing projections fail closed.

The diagnosis-facing noisy item is named neutrally. Evaluator metadata may record its experimental role, but the model cannot see that an item was designed as a distractor.

Claim strength is bounded:

- `full` and `noisy` may support a bounded causal hypothesis, not a causal conclusion;
- `missing_key` permits observations, comparisons and explicitly uncertain hypotheses;
- missing decisive evidence must be stated or requested rather than silently inferred.

### Matched diagnosis

A3 and B1 receive the same observable facts and matched execution settings. A3 adds the evidence-ID, citation and abstention contract; B1 is the plain matched comparison. Raw response, parsed response, request identity, model snapshot, prompt/config hash, usage, latency and retry state are persisted.

### Three-layer evaluation

Evaluation separates:

1. citation existence and visibility;
2. atomic claim support and correctness/behavior; and
3. paired evidence sensitivity across family siblings.

Rule-based checks run against the frozen artifact chain. Machine labels remain separate from later human judgments.

## Frozen machine-scored result

### Execution and operations

| Metric | Result |
|---|---:|
| Successful parses | 30/30 |
| Retries | 0 |
| Unresolved outputs | 0 |
| Input tokens | 19,566 |
| Output tokens | 4,123 |
| Estimated actual cost | USD 0.072116 |
| Aggregate latency | 51,165.3645 ms |

### Diagnostic evaluation

| Metric | Result |
|---|---:|
| Correct | 23/30 |
| Incorrect | 1/30 |
| Cause not asserted | 6/30 |
| Fully supported | 30/30 |
| Behavior/evidence aligned | 29/30 |
| Missing-key sensitive pairs | 10 of 10 |
| Noisy-robust pairs | 8 of 10 |

Variant-level results:

| Variant | Correctness | Support | Alignment | Missing sensitivity | Noisy robustness |
|---|---|---:|---:|---:|---:|
| A3 evidence contract | 12 correct; 3 not asserted | 15/15 | 15/15 | 5/5 | 4/5 |
| B1 plain | 11 correct; 1 incorrect; 3 not asserted | 15/15 | 14/15 | 5/5 | 4/5 |

These counts are descriptive pilot results over five independent families. They do not establish statistical superiority.

## Machine and human reconciliation

The frozen evaluator identifies:

- one B1 noisy output that promoted a non-failure control to a candidate failure cause;
- two failed full-to-noisy robustness comparisons, one for A3 and one for B1, within the same control family; and
- no structural citation or claim-support failure in the 30 parsed outputs.

The machine result remains frozen. Two independent, pseudonymized review
records were subsequently completed:

- Evidence review: 15/15 entries and 5/5 paired families passed, with no
  answer-revealing, design-revealing or unsupported-causal cue found.
- Diagnosis review: 24 correct, 6 cause-not-asserted and 30/30
  behavior-compliant by human judgment. The review agreed with the machine on
  29/30 entry judgments.
- The disclosed entry-level disagreement concerns one B1 noisy output in an
  improvement control. Human review read the output as explicitly
  rejecting degradation causality; the rule-based evaluator flagged its claim
  form. Neither label overwrites the other.
- Machine noisy robustness remains 8 of 10. Human semantic noisy robustness is
  separately reported as 10 of 10 because both disputed pairs preserved the
  non-degradation meaning despite changing claim strength.

The first blind diagnosis-review round retains 18 PASS and 12 UNCERTAIN. Those uncertain
screening judgments were not silently promoted to PASS.

## Reproduction

The published closeout package is reproducible offline from the repository root:

```bash
PYTHONPATH=src .venv/bin/python -m aletheia_lab benchmark validate-p1-final \
  --record reports/p1/p1-final-closeout.json \
  --machine-result reports/p1/p1-machine-result.json \
  --result-lock reports/p1/p1-result-lock.json \
  --evidence-review reports/p1/human-review/evidence-review.json \
  --evidence-blind-packet reports/p1/human-review/evidence-blind-packet.json \
  --evidence-mapping-packet reports/p1/human-review/evidence-mapping-packet.json \
  --diagnosis-review reports/p1/human-review/diagnosis-review.json \
  --diagnosis-blind-packet reports/p1/human-review/diagnosis-blind-packet.json \
  --diagnosis-mapping-packet reports/p1/human-review/diagnosis-mapping-packet.json
```

The validator does not read an API key, contact a provider or regenerate model
outputs.

## Completion decision

Phase 1 now contains:

- deterministic data/baseline;
- controlled cases and honest controls;
- paired family contract;
- evidence schema, collection, storage, leakage audit and validation;
- matched external execution;
- evaluator and immutable result lock;
- canonical table, cost/latency report, machine-first error analysis; and
- two completed, pseudonymized human reviews;
- explicit machine–human reconciliation; and
- fail-closed offline validation of the final decision.

**Decision: GO to Phase 2.** P1 establishes end-to-end technical feasibility and
a useful directional pilot signal. It does not establish comparative
performance, production readiness or broad external validity; those remain
questions for the larger multi-mechanism P2 design.
