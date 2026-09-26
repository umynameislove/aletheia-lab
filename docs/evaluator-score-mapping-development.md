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
registered attempt.
