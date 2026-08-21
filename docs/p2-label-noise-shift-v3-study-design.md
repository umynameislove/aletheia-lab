# Shift-aware label-noise study design

## Status

This document defines an outcome-free design candidate. It is ready for
independent methods review, but it is not a preregistration and cannot authorize
execution. Dataset bytes, target encodings, excluded columns and source hashes
must be bound in a later protocol-only registration.

The machine-readable design is
`configs/benchmark/p2_label_noise_shift_v3_design.json`, canonical SHA-256
`0cbbdc83961658cd2b767800ea6dbf736239a268cad96461ef96bd59d9c1f7cc`.

No v3 outcome has been generated. The v2 result store remains immutable at
SHA-256
`7e46d0997bc5ad6807409a4aebea39c82c11216f2bdcadd5704724994117504c`.

## Motivation from the frozen predecessor

The registered v2 study passed on Telco and failed on Bank. The outcome-aware
Bank audit found no encoding, mutation, feature-leakage, convergence, control or
inference defect. It instead found a severe temporal prevalence change:

- training positive prevalence: 4.81%;
- sealed-test positive prevalence: 30.83%;
- clean mean predicted positive probability: 0.0011%;
- `no_to_yes` at 30% moved the training label prevalence to 33.36%;
- all 30 high-dose `no_to_yes` seeds improved, rather than degraded, log loss.

The intervention therefore changed two scientific quantities at once: label
integrity and the effective training class prior. Under temporal prior shift,
the second change can improve target calibration and mask the harm caused by
the first. This is a construct confound, not an implementation defect.

V3 asks a stronger question:

> Can a shift-aware verification gate distinguish training-label corruption
> from class-prior shift while preserving cross-dataset sensitivity to
> corruption?

## Scientific contribution

The proposed contribution is not another label-shift estimator. It is a
verification design that combines three ideas:

1. a nuisance-matched counterfactual that holds effective class prior constant;
2. a prevalence-standardized proper scoring endpoint that holds the evaluation
   reference population constant; and
3. fail-closed comparison with established label-shift estimators and explicit
   assumption diagnostics.

The central claim is discriminability, not universal robustness: evidence for
label corruption should survive removal of the prior component, while a
prior-only condition should not be admitted as label noise.

## Independent confirmatory datasets

The Telco and Bank v2 partitions have already been opened and are prohibited
from v3 confirmation. They remain historical development evidence only.

The design fixes two new public, licensed datasets:

| Role | Dataset | Records | DOI | License |
|---|---|---:|---|---|
| Primary | UCI Default of Credit Card Clients | 30,000 | `10.24432/C55S3H` | CC BY 4.0 |
| External replication | UCI Online Shoppers Purchasing Intention | 12,330 | `10.24432/C5F88Q` | CC BY 4.0 |

Both use a prospective stratified `60/20/20` split. The primary seed is `2718`
and the replication seed is `3141`. Before registration, the official bytes,
archive/file hashes, exact target encoding, missing-value policy and any
post-outcome feature exclusions must be committed. Failure of either dataset to
meet the registered minimum of 1,000 records per class is a design failure, not
permission to choose a favorable replacement.

## Models and preprocessing

Logistic regression remains the primary model to preserve comparability with
v2 and isolate the changed construct. Its fixed parameters are `C=1.0`,
`solver=lbfgs`, `max_iter=1000` and `random_state=42`.

HistGradientBoosting is a prespecified secondary model-class sensitivity check
with learning rate `0.1`, 100 iterations, 31 leaves, zero L2 regularization,
disabled early stopping and seed `43`. It uses the same cells, controls and
evaluation policy, but cannot rescue a failed logistic-regression primary gate.

Preprocessing is fit on training data only. Calibration is fit on development
data only. Sealed labels cannot select a model, feature, calibration method or
hyperparameter, and hyperparameter search is forbidden.

## Factorial construct

### Corruption factor

Both class-conditional directions and all rates are retained:

```text
direction in {yes_to_no, no_to_yes}
conditional rate in {0.10, 0.20, 0.30}
```

The 30% cells are co-primary. Lower doses are dose-response evidence. Selection
is feature-blind, targets are the only mutated field, and seeds `6101..6150`
form the complete 50-replicate census.

### Effective-prior nuisance control

For every direction and rate, let `pi_clean` be the clean training positive
prevalence and `pi_corrupted` the prevalence after the registered flips. The
clean-label nuisance control keeps every label and feature unchanged and uses
class sample weights:

```text
w_positive = pi_corrupted / pi_clean
w_negative = (1 - pi_corrupted) / (1 - pi_clean)
```

The weighted clean training set therefore has exactly the same effective class
prior as the corrupted training set. The primary contrast is:

```text
corrupted model - prior-matched clean-label model
```

This subtracts the model change attributable to prior weighting. What remains
is the net corruption effect under the matched nuisance condition.

A reciprocal matched-pair flip is retained as a second control. It changes
labels in both directions while preserving the aggregate label prevalence.
Clean, serialization and label-repair controls remain mandatory. No control
may rescue a failed primary contrast.

### Prior-shift environments

The sealed clean evaluation pool is resampled within label using odds
multipliers:

```text
{0.25, 1.0, 4.0}
```

The grid is symmetric on the log-odds scale. It changes `P(Y)` while preserving
the empirical `P(X | Y)` by construction. Seeds `7101..7150` are independent of
the corruption seeds. Target labels are accessible only to the isolated
environment generator and final scorer; estimators receive unlabeled target
features or predictions.

The historical Bank temporal split is not promoted to a pure-label-shift
environment because its `P(X | Y)` invariance was never established.

## Primary endpoint and estimand

Raw target-population log loss changes when class prevalence changes. V3 uses a
fixed reference population for construct admission:

```text
L_ref = 0.5 * mean(log loss | Y=1)
      + 0.5 * mean(log loss | Y=0)
```

This is logarithmic loss evaluated under a fixed 50/50 reference prior. It is a
strictly proper score for that reference distribution and is invariant to
duplicating one evaluation class while its class-conditional losses remain
unchanged.

The primary estimand for each dataset and corruption seed is:

```text
(L_ref(corrupted) - L_ref(prior-matched clean))
/ L_ref(prior-matched clean)
```

The smallest effect of practical interest remains a prospectively fixed 5%.
Raw target-prior log loss, Brier score, calibration intercept and slope, ROC
AUC, balanced accuracy and classwise log loss are secondary. Accuracy v1 and
raw-log-loss v2 are historical comparators. Secondary metrics cannot rescue the
primary decision.

## Shift-estimation baselines

The deployment-oriented secondary analysis compares:

1. unadjusted v2 probabilities;
2. oracle prior-ratio adjustment, reported only as a non-deployable upper bound;
3. Black Box Shift Estimation (BBSE);
4. maximum-likelihood/EM label-shift estimation (MLLS);
5. Regularized Learning under Label Shift (RLLS).

Probability calibration uses only the development partition through a frozen
logit intercept-and-slope model. Sealed labels cannot calibrate or select an
estimator. Singular confusion matrices, invalid weights or failed optimization
produce `ABSTAIN`; weights cannot be silently clipped until a favorable answer
appears.

Classwise MMD permutation tests audit the label-shift assumption. Failure is not
evidence for pure label shift and forces abstention from that attribution. The
controlled environments are primary because their class-conditional invariance
is known by construction; natural-shift diagnostics are secondary.

## Hypotheses and decision rule

For a corruption direction to support the v3 cross-dataset claim, every
condition below must hold:

1. the mean net corruption effect is at least 5% on both new datasets;
2. the crossed-bootstrap lower bound is above zero on both datasets;
3. the direction-level cross-dataset p-value is the maximum of the two dataset
   p-values, implementing an intersection-union test;
4. the two direction-level p-values survive Holm correction at family-wise
   alpha `0.05`;
5. every mutation, prior-match, serialization, repair and provenance control
   passes; and
6. no prior-only negative control is admitted as label noise.

The conjunction prevents one dataset from rescuing another. The estimator
comparison is secondary and cannot modify construct admission.

## Replication and power

There are 50 corruption seeds per cell. Under a prospective paired-normal
planning approximation with standardized effect `0.50` and worst-case
one-sided alpha `0.025`, planned power is `0.9424`, above the target `0.90`.
This is a planning calculation, not a guaranteed empirical power claim.

Primary uncertainty uses a two-way product-weight bootstrap over evaluation
records and corruption seeds with 10,000 resamples. Paired seed-level sign-flip
tests use 100,000 draws. New seeds are `271829` and `161804`; they do not reuse
the v2 inference namespace. All cells and baselines must run with no interim
inspection or early stopping.

## Comparison with earlier designs

| Property | v1 alpha | v2 confirmatory | v3 design |
|---|---|---|---|
| Primary score | accuracy | raw clean-test log loss | reference-prior standardized log loss |
| Noise | symmetric | directional class-conditional | directional plus orthogonal controls |
| Prior confounding controlled | no | no | yes, by nuisance matching |
| Label-shift baselines | none | none | oracle, BBSE, MLLS, RLLS |
| Assumption failure | not represented | not represented | fail-closed abstention |
| Model scope | logistic | logistic | logistic primary plus fixed tree sensitivity |
| Independent unseen datasets | no | Bank only | two new datasets |
| Cross-dataset rule | none | same direction | IUT plus Holm, same direction |

## Allowed future claim

No new superiority claim is currently allowed. If the future registered study
passes, the strongest defensible statement is:

> Under two prospectively registered tabular datasets and the frozen model,
> corruption and shift conditions, the shift-aware nuisance-matched gate
> detected a cross-dataset net label-corruption effect while rejecting
> prior-only negative controls; accuracy-only and unadjusted-log-loss baselines
> did not provide the same construct discrimination.

This would not establish universal superiority over all datasets, models,
annotation processes or domain-adaptation methods.

## Threats to validity

- Sample weighting is a controlled proxy for an effective training prior, not
  proof that every one-way flip decomposes causally into prior and corruption.
- Controlled prior resampling establishes a pure shift environment but is less
  ecologically rich than unconstrained temporal drift.
- The primary claim remains logistic-regression bounded; the fixed tree model
  is sensitivity evidence, not a rescue path.
- Corruption seeds quantify intervention variation, not independent datasets.
- Public datasets are reproducible but cannot be treated as private clinical or
  production populations.
- Dataset and column checksums are intentionally absent until the protocol-only
  registration stage; execution before that stage is forbidden.

## Required next gate

Before a v3 protocol can be registered:

1. independently review the design without any v3 outcomes;
2. download only the two fixed official datasets;
3. bind source bytes, licenses, targets, exclusions and preprocessing receipts;
4. verify class-count eligibility without fitting outcome models;
5. run synthetic/property tests for prior matching, shift generation,
   estimator abstention and the complete decision algebra;
6. publish a protocol-only commit and immutable tag
   `p2-label-noise-shift-factorial-v3`; and
7. implement execution only after registration.

Run the current non-executing readiness check with:

```bash
python scripts/p2_v3_design_readiness.py
```

The report must keep both `registration_authorized` and
`execution_authorized` false.

## Scientific basis

- Gneiting and Raftery (2007), *Strictly Proper Scoring Rules, Prediction, and
  Estimation*. <https://doi.org/10.1198/016214506000001437>
- Natarajan et al. (2013), *Learning with Noisy Labels*.
  <https://proceedings.neurips.cc/paper/2013/hash/3871bd64012152bfb53fdf04b401193f-Abstract.html>
- Scott, Blanchard and Handy (2013), *Classification with Asymmetric Label
  Noise: Consistency and Maximal Denoising*.
  <https://proceedings.mlr.press/v30/Scott13.html>
- Saerens, Latinne and Decaestecker (2002), *Adjusting the Outputs of a
  Classifier to New A Priori Probabilities*.
  <https://doi.org/10.1162/089976602753284446>
- Lipton, Wang and Smola (2018), *Detecting and Correcting for Label Shift with
  Black Box Predictors*. <https://proceedings.mlr.press/v80/lipton18a.html>
- Azizzadenesheli et al. (2019), *Regularized Learning for Domain Adaptation
  under Label Shifts*. <https://openreview.net/forum?id=rJl0r3R9KX>
- Garg et al. (2020), *A Unified View of Label Shift Estimation*.
  <https://proceedings.neurips.cc/paper/2020/hash/219e052492f4008818b8adb6366c7ed6-Abstract.html>
- Gretton et al. (2012), *A Kernel Two-Sample Test*.
  <https://www.jmlr.org/papers/v13/gretton12a.html>
- UCI Default of Credit Card Clients, DOI `10.24432/C55S3H`.
  <https://archive.ics.uci.edu/dataset/350/default+of+credit+card+clients>
- UCI Online Shoppers Purchasing Intention, DOI `10.24432/C5F88Q`.
  <https://archive.ics.uci.edu/dataset/468/online+shoppers+purchasing+intention+dataset>
