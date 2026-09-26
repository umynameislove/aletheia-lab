# Evaluator score-mapping feasibility — development only

This forward experiment does **not** alter prior registered analyses or their
sealed outcomes. It is not a registered attempt, a mechanism-admission decision,
or evidence that the clean production evaluator contained this fault.

## Source and control

The runner verifies the pinned V3 protocol and UCI archive bytes, reconstructs
the historical split receipt, selects only `train` and `development`, fits the
registered preprocessor on train, and fits the two fixed estimator families on
train. It calibrates on development and evaluates the same development rows.
No model prediction or metric is computed on `sealed_test`. Split reconstruction
necessarily processes source target tokens across the complete public archive;
therefore this is a *development-only prediction/metric boundary*, not a claim
that the sealed source labels were physically unread.

For each dataset/model cell, the fitted estimator is saved in a private
directory and reloaded; the saved preprocessor is likewise replayed. A fresh
independent call through the V3 runtime refits the registered estimator and
checks its calibrated development predictions against the original two-column
source capture. Before intervention, the witness binds archive/protocol/
preprocessor/model bytes, class order, calibration, feature rows, both score
columns, and ordered row-target pairs. The separate verifier recomputes the
row-ID shard selector, score decoding, affected rows, runtime log-loss, and
correction for every dose. The result stays outside the public repository.

Only the evaluator's *assumed* class-to-column order changes on fixed nested
shards: 0, 1, 2, or 4 of 20 nominal shards. Source model, calibration, score
rows, target rows, and scorer remain fixed. The 0-shard control is healthy.

## Complete development observations

Numbers below are changes in reference-prior-standardized log-loss relative
to the same cell's healthy decode. Higher means an apparent performance loss,
not deterioration of the fitted model. Every displayed positive dose changed
exactly the listed number of score rows; independent correction restored the
healthy vector and log-loss exactly. Values are rounded here; private
measurements retain full precision.

| Dataset | Estimator | Development rows | Zero | 1 shard: affected, Δ loss | 2 shards: affected, Δ loss | 4 shards: affected, Δ loss |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| UCI Default of Credit Card Clients | Logistic regression | 6,000 | 0 | 303, +0.020488 | 565, +0.050648 | 1,163, +0.114603 |
| UCI Default of Credit Card Clients | Histogram gradient boosting | 6,000 | 0 | 303, +0.027316 | 565, +0.058953 | 1,163, +0.129468 |
| UCI Online Shoppers | Logistic regression | 2,466 | 0 | 124, +0.035895 | 269, +0.093940 | 484, +0.146550 |
| UCI Online Shoppers | Histogram gradient boosting | 2,466 | 0 | 124, +0.037464 | 269, +0.100158 | 484, +0.170183 |

The full 2 × 2 × 4 grid was retained, including the zero controls. The
development summary is byte-bound by SHA-256
`2074633e1dbae29f4b9178344a752f8ff6b49a3bbc643bb0c206d4302406759a`.
Raw fitted models and row-level material remain private. No provider was called.

## What this does and does not establish

This establishes that the proposed measurement intervention is technically
feasible on both planned sources and both fixed estimator families. Source
prediction and target binding were held stable; the adapter alone created an
apparent metric shift, and independent decoding corrected it. The observed
positive, monotone grid is a development observation, **not** a pre-registered
effect-size estimate or a selected confirmatory dose.

A target-flip rival was built on the same affected row IDs as an adversarial
check. Its development aggregate log-loss did **not** exactly match the
mapping fault in any of the 12 positive-dose cells. The
class-prior-standardized metric changes
weights when target classes change, so a rowwise score/target symmetry does
not imply an identical aggregate. Consequently the `missing_key` views in
these real-data rival pairs were distinguishable by the aggregate symptom;
the synthetic matched-pair 50% shortcut bound does **not** transfer to this
2 × 2 run. We do not claim real-data symptom-matched discriminability.

The development partition is also used to fit calibration, so its losses
cannot be read as held-out model performance. A genuinely symptom-matched
adversarial comparator and prospective holdout would be needed for stronger
discriminability and confirmatory claims. This run does not authorize a
registered attempt. The separate development-only hard-negative construction
below addresses the former at a declared *visible metric resolution*, not the
latter.

## Forward development hard negative

The previous all-affected-row target flip altered class counts, which changed
the runtime scorer's class-prior weights. A new deterministic adversarial
search swaps one source-0 target with one source-1 target at a time under fixed
row IDs. Thus the class counts, model, score rows, calibration and runtime
scorer remain fixed while the target binding changes. The search sees the
development scores and the mapping symptom **by construction**; it is a hard
negative, not an estimate of how often target corruption occurs naturally.
Its target-change footprint is capped at the number of mapping-affected rows.

The diagnosis-visible aggregate loss was fixed at **six decimal places** with
an absolute matching tolerance of `5e-7` before this forward search ran. At
that resolution, the `missing_key` payloads are byte-identical within each
mapping/target-rival pair, while the `full` payload exposes a different
class-order and target-binding witness. The original twelve-decimal payloads
remain distinguishable in all twelve positive-dose pairs. This is therefore
**resolution-matched symptom ambiguity**, not exact equality of raw metrics.

| Dataset / estimator | Mapping Δ loss at 1 / 2 / 4 shards | Rival target rows changed at 1 / 2 / 4 shards |
| --- | --- | --- |
| Credit Card / logistic | +0.020488 / +0.050648 / +0.114603 | 12 / 46 / 130 |
| Credit Card / histogram gradient boosting | +0.027316 / +0.058953 / +0.129468 | 30 / 70 / 166 |
| Online Shoppers / logistic | +0.035895 / +0.093940 / +0.146550 | 8 / 20 / 32 |
| Online Shoppers / histogram gradient boosting | +0.037464 / +0.100158 / +0.170183 | 10 / 26 / 44 |

All twelve pairs matched at the stated visible resolution; the largest raw
absolute loss gap was below `3.7e-8`. The zero-dose controls remained flat.
The smallest candidate dose meeting this development criterion in all four
dataset/model cells is **one of twenty nominal shards**. The cells share
datasets, rows and nested doses; they are not twelve independent replications.
The full private ledger, including every selected row pair, is byte-bound by
SHA-256 `aa891dcec952caefb54b1ccb11470fc22955d738b3e3b49ee961cfe6d4a31d50`.
A second offline replay with independent pair-ledger checks produced the same
summary bytes. The predecessor feasibility summary remained unchanged at
`2074633e1dbae29f4b9178344a752f8ff6b49a3bbc643bb0c206d4302406759a`.

This closes the **development dose/rival study**, not G1–G4 admission. The
target rival is adversarially optimized on development and is not a separately
admitted natural mechanism. A model seeing twelve-decimal metrics or changed
row counts could use those as shortcuts; neither is in the declared
six-decimal `missing_key` projection. The six-decimal pairwise 50% ceiling
applies only to equally weighted, byte-identical pairs under that exact
projection. A prospective evaluator-mapping holdout, precision/failure policy
and separate execution authorization remain necessary before any registered
attempt. No provider was called,
no sealed predictions or metrics were computed, and the earlier P2R/P5
results were not changed.
