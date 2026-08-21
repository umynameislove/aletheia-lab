# V3 internal outcome-blind methods audit

## Decision

**Decision: `PASS_WITH_PRE_EXECUTION_CONFORMANCE_REQUIREMENTS`.**

This audit replaces an external pre-registration review requirement. It does
not claim external or independent validation. The study remains protected by
an outcome-blind protocol, content-addressed inputs, immutable registration,
single sealed-test opening, conjunctive admission, non-rescue rules, and a ban
on outcome-driven grid or seed expansion.

No v3 model, predictive metric, or sealed outcome was available during this
audit. Favorable results are not guaranteed and may not be manufactured by
changing thresholds, estimators, seeds, datasets, or decision rules after
registration.

## Bound candidate

| Item | Value |
|---|---|
| Protocol | `configs/benchmark/p2_label_noise_shift_v3_protocol.json` |
| Protocol SHA-256 | `0e9c594a6453dc111def3208582cec85d13518d542a61d86197620f9707ab7b2` |
| Required release tag | `p2-label-noise-shift-factorial-v3.1` |
| Outcome-free design SHA-256 | `1c9c6592112038ae5ee11d0ef91921172dc61873e4d20272902178003171bd25` |
| Dataset manifest SHA-256 | `a019a134f6e903fb80a6714237ca59b36adcca3ef1ab69cd9fd664fc5fc63b94` |
| Dataset receipt SHA-256 | `bc480803505edb7c49f67eb24b8ebbb8ba415c4813278631239f372aca2f60d1` |
| Frozen v2 result SHA-256 | `7e46d0997bc5ad6807409a4aebea39c82c11216f2bdcadd5704724994117504c` |

The previously published `p2-label-noise-shift-factorial-v3` tag is a frozen
candidate marker. It must not be moved, deleted, reused, or described as the
final registered v3.1 protocol.

## Scientific audit

The registered question is bounded and testable: whether the gate can detect a
net directional training-label corruption effect after controlling for the
first-order training-prior change, while avoiding false label-noise admission
under controlled prior-only shift.

The following components are retained because they directly address the v2
replication failure and reduce outcome-independent alternative explanations:

- two new public binary tabular datasets with frozen archive, schema, target,
  exclusion, identity, and split receipts;
- group-atomic stratified train, development, and sealed-test partitions;
- train-only preprocessing and development-only calibration;
- directional one-way corruption with exact mutation reconciliation;
- a clean-label prior-matched comparator;
- neutral and controlled prior-shift environments;
- a fixed reference-prior proper scoring rule;
- a primary logistic model and a non-rescuing tree sensitivity model;
- two-dataset intersection-union testing and Holm correction across directions;
- explicit technical failure, abstention, no-rescue, and no-optional-stopping
  rules.

The claim remains limited to the registered datasets, models, corruption
mechanism, and controlled shifts. A pass is evidence for this bounded
verification construct, not universal robustness to label noise or arbitrary
distribution shift.

## Mandatory runtime conformance requirements

The execution runtime must resolve implementation details deterministically
without changing the registered estimand or decision rule. These requirements
must be implemented and tested before sealed outcomes are opened.

### Shift estimators

BBSE, MLLS/EM, and RLLS must freeze class ordering, matrix orientation,
calibrated probability inputs, equations, solver, dtype, tolerance,
initialization, feasibility tolerance, normalization, and every abstention
condition. RLLS must define the regularized objective rather than relying only
on `L2=0.01`. Each estimator requires a hand-computable golden fixture.

### MMD diagnostic

The runtime must freeze the finite-sample MMD-squared statistic, compared
representations, bandwidth sample and zero-bandwidth behavior, permutation
unit, seed stream, equality comparison, plus-one p-value, hypothesis ordering,
and Holm family. A golden fixture must independently reproduce the statistic
and adjusted p-values.

### Hash-based sampling

Corruption ranking and environment sampling must use explicit domain
separation, canonical field serialization, integer width and byte order,
counter origin, digest conversion, rejection threshold, and tie-breaking.
Golden vectors must bind sample seeds and identities to expected digests and
indices.

### Calibration

The development-only Newton calibration must freeze its objective, gradient,
Hessian, solve, stopping norm, singular-Hessian behavior, iteration-limit
behavior, and probability-boundary handling. Any convergence ambiguity is an
abstention or technical failure, never an automatic repair.

### Inference

The two-way bootstrap must freeze seed and record populations, independent
multinomial weights, product-weight formulas, estimator recomputation policy,
zero-denominator handling, percentile convention, and output ordering.

The paired sign-flip test must freeze its seed-level contrast, observed
statistic, one-sided alternative, sign stream, equality comparison, and
plus-one correction. A conformance fixture must reproduce dataset p-values,
the intersection-union maximum, and Holm-adjusted direction p-values.

### Categorical encoding

All categorical values must be converted to one canonical token type before
one-hot encoding. The fitted output column names and ordering require a frozen
hash per dataset. Mixed numeric and textual categories, reordered columns, or
an unregistered vocabulary change are technical failures.

## Fail-closed preflight

Before opening the sealed test, the runtime must demonstrate:

1. exact transitive artifact and split-membership hashes;
2. no duplicate analysis-feature group crossing partitions;
3. target, identifier, and excluded columns removed before preprocessing;
4. train-only transformations and development-only calibration;
5. golden-vector conformance for corruption and prior sampling;
6. exact mutation, prevalence, reciprocal-cap, and nuisance-weight
   reconciliation;
7. estimator and diagnostic conformance, including abstention paths;
8. bootstrap, sign-flip, intersection-union, and Holm conformance;
9. strict primary/secondary non-rescue behavior;
10. an atomic single sealed-test opening; and
11. simultaneous release of primary and replication results.

Any mismatch blocks execution. Software-test success is not a positive
scientific result, and a negative or abstained result must be preserved exactly
as generated.

## Registration boundary

The internal audit is complete only for the exact hashes above. The immutable
v3.1 release must contain this audit, the protocol, its bound artifacts, and a
machine-readable verification report. Execution code may be developed only
after that release and may not introduce a new outcome-relevant choice.

## Method references

- Lipton, Wang, and Smola, *Detecting and Correcting for Label Shift*, ICML
  2018: <https://proceedings.mlr.press/v80/lipton18a.html>
- Azizzadenesheli et al., *Regularized Learning for Domain Adaptation under
  Label Shifts*, ICLR 2019: <https://openreview.net/forum?id=rJl0r3R9KX>
- Garg et al., *A Unified View of Label Shift Estimation*, NeurIPS 2020:
  <https://papers.nips.cc/paper/2020/hash/219e052492f4008818b8adb6366c7ed6-Abstract.html>
- Gretton et al., *A Kernel Two-Sample Test*, JMLR 2012:
  <https://jmlr.org/papers/v13/gretton12a.html>
- Owen and Eckles, *Bootstrapping Data Arrays of Arbitrary Order*, Annals of
  Applied Statistics 2012: <https://arxiv.org/abs/1106.2125>
- Phipson and Smyth, *Permutation P-values Should Never Be Zero*, Statistical
  Applications in Genetics and Molecular Biology 2010:
  <https://pubmed.ncbi.nlm.nih.gov/21044043/>
