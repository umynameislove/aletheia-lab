# Shift-aware label-noise v3 protocol candidate

## Status and scientific boundary

This document accompanies the frozen protocol candidate at
`configs/benchmark/p2_label_noise_shift_v3_protocol.json`. Its canonical
SHA-256 is
`9b9db59c9555ecb41ef0ead5af3bafecf30189d69511e0b39711ebf1ff1220bf`.

The candidate is complete enough for outcome-free verification, but this file
does not register the study or authorize execution. Registration requires the
protocol-only commit, independent methods review and an immutable release at
the tag `p2-label-noise-shift-factorial-v3` before execution code is merged.

No predictive model was fitted, no predictive metric was calculated and no
sealed outcome was generated while compiling this protocol. Labels were read
only to create the prospectively stratified partition membership.

## Transitive evidence binding

The protocol binds all earlier evidence by canonical hash:

| Artifact | Canonical SHA-256 |
|---|---|
| Outcome-free v3 design | `0cbbdc83961658cd2b767800ea6dbf736239a268cad96461ef96bd59d9c1f7cc` |
| Dataset manifest | `67599382e1e114cf76e4f35d1e01c92477c8dd65f9e4e7eff1a94957bf3658fa` |
| Dataset audit receipt | `05b2703381f81f10979f916ef6eb657ed34c5728152e9c052d9dfa67fc66c684` |
| Frozen v2 result store | `7e46d0997bc5ad6807409a4aebea39c82c11216f2bdcadd5704724994117504c` |

Changing a source archive, target order, record identity, feature role, design
decision or predecessor result invalidates this chain rather than silently
creating another study.

## Frozen group-aware partitions

Exact analysis-feature groups are atomic. They are ordered by group size,
class mass and a seed-bound SHA-256 tiebreaker, then greedily assigned against
integer class and total-count targets. The objective first minimizes normalized
overflow and then normalized squared class and total deficits. It does not use
source row order or a library-specific random split implementation.

| Dataset | Train | Development | Sealed test | Cross-partition duplicate groups |
|---|---:|---:|---:|---:|
| Default of Credit Card Clients | 18,000 | 6,000 | 6,000 | 0 |
| Online Shoppers | 7,399 | 2,466 | 2,465 | 0 |

Class counts are also exact:

| Dataset/partition | Negative | Positive |
|---|---:|---:|
| Credit train | 14,018 | 3,982 |
| Credit development | 4,673 | 1,327 |
| Credit sealed test | 4,673 | 1,327 |
| Shoppers train | 6,254 | 1,145 |
| Shoppers development | 2,084 | 382 |
| Shoppers sealed test | 2,084 | 381 |

The complete membership is represented by content hashes, not committed row
lists. The primary membership hash is
`fa0f3fe0cb03b12cecf590f806107206c0bb5c65c332cd5940f9ddca800bbcd8`;
the replication membership hash is
`1f9b1076228a949fd3bc2e3ad48651b27a2046350a51f82cbaaf63619e36574e`.

## Preprocessing and models

The target, identifiers and excluded features are removed before fitting.
Missing values fail closed; post-registration imputation is not allowed.
Numeric features use float64 standardization learned from training records.
Categorical features use a training-only one-hot vocabulary, deterministic
column ordering and an all-zero representation for unseen evaluation tokens.
Undocumented Credit values are normalized prospectively: education codes
`0`, `5`, `6` and marriage code `0` map to an explicit `other` token.

The primary model is logistic regression with `C=1.0`, `lbfgs`, 1,000 maximum
iterations and seed `42`. HistGradientBoosting with its frozen parameters is a
model-class sensitivity analysis only. A convergence warning or non-finite
probability is a technical failure. Hyperparameter search is forbidden.

Logit intercept-and-slope calibration is fit only on development predictions.
Newton optimization starts at intercept `0` and slope `1`, has no penalty,
clips input probabilities only at `1e-15`, uses tolerance `1e-8` and stops at
100 iterations. Failure is `ABSTAIN`; sealed labels cannot select, calibrate or
repair a model.

## Corruption and orthogonal controls

For each direction, rate and seed, candidate source-class records are ranked
by SHA-256 over seed and canonical record identity. Exactly
`floor(rate * source_class_count)` records are changed without replacement.
Only training targets may change.

The primary comparator retains clean labels and uses class weights whose
effective positive prior exactly matches the corrupted training prevalence.
Weights are normalized to mean one. The primary estimand subtracts this
prior-matched clean control from the corrupted model under a fixed 50/50
reference-prior logarithmic score.

The reciprocal control flips equal numbers in opposite directions without
replacement. Its feasible pair count is capped by the minority class. This is
scientifically necessary: at high `no_to_yes` doses, matching the one-way
mutation count can exceed the number of positive records. The protocol reports
that cap explicitly and never claims equal mutation count when it is binding.
This control cannot rescue the primary comparison.

Serialization roundtrip and repair must reproduce the registered clean hashes.
Every mutation count, achieved prevalence and nuisance weight must reconcile
exactly or the cell is a technical failure.

## Controlled prior environments and estimators

The sealed evaluation pool is evaluated at odds multipliers `0.25`, `1.0` and
`4.0`. The neutral condition is the exact sealed membership. Non-neutral
conditions retain the sealed sample size and sample within each class using a
SHA-256 counter stream with rejection sampling before the pool-index mapping;
sampling is with replacement. This avoids modulo bias, changes empirical class
prior and preserves the source class-conditional pools by construction.

Only the isolated generator and scorer may read target labels. Shift estimators
receive predictions or unlabeled features. The ordered comparison is:

1. unadjusted v2 probabilities;
2. an oracle prior-ratio upper bound that is not deployable;
3. BBSE;
4. MLLS/EM; and
5. RLLS with fixed L2 regularization `0.01`.

BBSE abstains above condition number `1e8`. MLLS uses the development source
prior, tolerance `1e-8` and at most 1,000 iterations. Invalid priors, negative
weights, singular systems or non-convergence produce `ABSTAIN`; silent clipping
is forbidden. Classwise RBF-MMD diagnostics use a median bandwidth, 2,000
permutations, seed `314160` and Holm correction across classes and datasets.

## Inference and admission

The primary cell is the 30% corruption rate in the neutral environment. Each
direction has 50 corruption seeds. The uncertainty interval is an equal-tailed
95% two-way multinomial product-weight bootstrap over evaluation records and
seeds, using 10,000 resamples and seed `271829`. The one-sided paired sign-flip
test uses 100,000 Monte Carlo draws, seed `161804` and the plus-one correction.

For each direction, the cross-dataset p-value is the maximum of the two dataset
p-values, implementing an intersection-union test. Holm correction then covers
the two directions. A direction passes only when both datasets have at least a
5% net effect, both bootstrap lower bounds exceed zero, the adjusted p-value is
below `0.05`, every technical/control gate passes and prior-only conditions
produce zero label-noise admissions.

One dataset cannot rescue the other. Secondary metrics, the tree model and
shift-estimator comparisons cannot rescue a failed primary gate. Assumption
failure yields `ABSTAIN`, not pass and not evidence that pure label shift is
absent. At least one same direction must pass both datasets for the bounded
cross-dataset claim.

## Reproduction and next gate

Verify tracked contracts without source archives:

```bash
python scripts/p2_v3_protocol_registration.py verify
```

Recompile exact split receipts from the ignored SHA-pinned archives:

```bash
python scripts/p2_v3_protocol_registration.py compile-splits
```

Both commands must report `model_fitted`, `predictive_metrics_generated`,
`sealed_outcomes_generated`, `registration_authorized` and
`execution_authorized` as false.

After this candidate is independently reviewed, merge it as a protocol-only
commit and publish the immutable tag and release. Only the following task may
implement the execution runtime, and that runtime must reproduce every frozen
membership and decision contract before the sealed test is opened once.
