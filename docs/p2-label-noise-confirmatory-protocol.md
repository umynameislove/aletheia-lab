# Label-noise confirmatory protocol

> Status: internally frozen design; no confirmatory outcome has been generated.
> Machine-readable contract: `configs/benchmark/p2_label_noise_confirmatory_protocol.json`
> Contract SHA-256: `09dd4124eaf54a11c7f4b30d23c4e1369ebca77e3335ffbefc4bd3034b3d53a1`

Validate the contract and its local predecessor bindings without running an
experiment:

```bash
python scripts/p2_label_noise_confirmatory_protocol.py
```

After this protocol-only change is merged, it must be registered before any
outcome execution: create the immutable Git tag
`p2-label-noise-confirmatory-v1` and archive the tagged artifact in a GitHub
release or an external timestamped registry. Any later design change requires a
new protocol version and new registration; the existing hash must not be edited.

## 1. Decision and scientific purpose

The failed alpha and reserve recovery remain immutable negative results. This
protocol does not lower the alpha threshold, search new alpha seeds, reinterpret
stable candidates or reuse an observed reserve as a new failure. It starts a
separate confirmatory study with a new construct, endpoint, split and seed
namespace fixed before execution.

The confirmatory question is:

> Does class-conditional training-label corruption cause a reproducible and
> practically meaningful degradation in clean-label probabilistic prediction
> quality?

The study can fail. If it fails on the primary dataset, external replication,
secondary metrics or an attractive individual seed cannot rescue mechanism
coverage. The correct action is to retain the fail-closed result and narrow the
benchmark claim.

## 2. Root cause of the alpha failure

The alpha used symmetric binary label flips and clean-test accuracy as its only
eligibility endpoint. For symmetric flip probability `rho`, the corrupted class
posterior is

```text
P(Y_tilde = 1 | X=x) = rho + (1 - 2*rho) P(Y = 1 | X=x).
```

When `rho < 0.5`, this affine transformation preserves which side of `0.5` the
clean posterior occupies. The Bayes decision boundary under zero-one loss can
therefore remain unchanged even while probability estimates and training labels
are materially corrupted. Oyen et al. likewise show that uniform noise can leave
classification robust until a high tipping point, while class- or feature-shaped
noise can move the boundary much earlier.

This explains the observed alpha pattern without asserting that label noise is
harmless. The alpha tested a real intervention with an endpoint that is
theoretically insensitive to that particular noise shape. It did not test the
new confirmatory hypotheses below.

## 3. Construct correction

### 3.1 Noise shape

The fault-directed construct changes from symmetric corruption to binary
class-conditional corruption. Both directions are included so a favorable
direction cannot be selected after outcomes:

- `yes_to_no`: a fixed fraction of source-positive training labels becomes negative;
- `no_to_yes`: a fixed fraction of source-negative training labels becomes positive.

Rates are conditional on the eligible source class, not the complete training
set. The complete `direction x rate` grid is `2 x {0.10, 0.20, 0.30}`. The two
30% cells are co-primary; lower doses are secondary dose-response evidence only.

Class-conditional corruption is established in noisy-label theory and does not
require the injector to access features. The one-factor invariant is preserved:
only training targets may change, while membership, order, features,
preprocessing, model specification and clean evaluation labels remain bound.

### 3.2 Endpoint

The primary endpoint is log loss on an untouched clean-label test split. Log loss
is a strictly proper scoring rule: in expectation it uniquely rewards reporting
the correct predictive distribution. It can detect degradation in probability
quality before the hard class decision changes. Accuracy remains a locked legacy
comparator and cannot determine or rescue confirmatory eligibility.

The primary effect for each direction and dataset is

```text
mean over corruption seeds of
  (log_loss(noisy_model) - log_loss(paired_clean_model))
  / log_loss(paired_clean_model).
```

A direction is scientifically eligible only if all of the following hold:

1. the relative increase is at least 5%;
2. the two-way bootstrap lower confidence bound is above zero;
3. its one-sided paired sign-flip test survives Holm correction across the two
   co-primary directions at family-wise alpha `0.05`;
4. every technical, provenance and control gate passes.

The 5% smallest effect size of interest prevents a negligible but statistically
detectable change from becoming a benchmark failure. It is a prospective design
choice, not an estimate derived from the alpha outcomes.

Secondary metrics are Brier score, ROC AUC, balanced accuracy, accuracy and both
class recalls. They describe mechanism behavior and operational trade-offs but
cannot overturn the primary decision.

## 4. Datasets and model

### 4.1 Primary dataset

The primary dataset remains the pinned Telco Customer Churn snapshot:

- dataset SHA-256: `7dd93bbff704f59a3044237c6695f4cfb2ee1b6faff6c21265b2e4044195cbd8`;
- target: `Churn`; positive label: `Yes`;
- new stratified `60/20/20` train/development/sealed-test split;
- split seed: `314159`;
- the sealed test is opened once after implementation, fixture and synthetic
  tests pass and the contract hash is recorded.

The new split prevents direct reuse of alpha measurements. It does not create a
new population, so primary claims remain bounded to this dataset and protocol.

### 4.2 External replication

UCI Bank Marketing `bank-additional-full.csv` is fixed as an external replication:

- DOI: `10.24432/C5K306`; license: CC BY 4.0;
- official archive SHA-256:
  `e0bf5f5de5b846e2f18e9d90606637267d46dfa260e0f17bb12e605db5efbeb4`;
- CSV SHA-256:
  `74adfc578bf77a7ff4bb1ba4a9f8709d9e3c6907342959c2c8416847e0afb4d8`;
- target: `y`; positive label: `yes`;
- source-order `60/20/20` temporal partition;
- `duration` is excluded because it is only known after a call and would create
  an unrealistic information path.

Replication cannot rescue a failed Telco primary result. A cross-dataset claim
requires the same class-conditional direction to satisfy the full decision rule
on both datasets. A Telco-only pass permits only a Telco-bounded claim.

### 4.3 Model

The primary model remains logistic regression to isolate the changed construct:

- `C=1.0`, `max_iter=1000`, `solver=lbfgs`, `class_weight=None`;
- training seed `42`;
- the preprocessing fit uses training data only;
- hyperparameter search and model-family selection are forbidden.

This is not a label-noise-robust learner competition. Alternative learners may
be a later, separately preregistered transfer study and cannot alter this gate.

## 5. Replication, power and uncertainty

Each intervention cell has 30 corruption-seed replicates. Telco uses the complete
seed set `4101..4130`; Bank uses `5101..5130`. The same seeds are reused across
doses within a dataset as common random numbers, enabling paired dose contrasts.
Seeds are replicates nested inside a `dataset x model x direction x rate` family;
they are not reported as 30 independent datasets or 30 independent mechanisms.

The design targets approximately 80% power for a standardized paired effect of
`0.53` under a one-sided per-comparison alpha near `0.025`. This normal-approximation
budget is a planning guarantee for moderate seed-level effects, not proof of
power for arbitrary small effects or population generalization.

Uncertainty is estimated using 10,000 product-weight bootstrap resamples across
both evaluation records and corruption seeds, with seed `271828`. This accounts
for the crossed sources of variation rather than treating every record-seed pair
as independent. One-sided paired sign-flip tests use 100,000 Monte Carlo draws,
seed `161803`, followed by Holm correction across the two co-primary directions.

All 30 replicates and all six cells run. There is no interim inspection, early
stopping, best-seed reporting or grid extension after outcomes. Primary and
external-replication outputs are generated as one locked batch and released
together, so the primary result cannot influence replication execution.

## 6. Controls and causal contrast

Every intervention is matched to the same dataset, split, preprocessing, model
and seed path. Four controls are mandatory:

| Control | Purpose | Admission rule |
|---|---|---|
| Clean reference | Paired counterfactual without target corruption | Reference only |
| Serialization roundtrip | Detect pipeline changes that do not alter labels | Must reproduce clean artifacts |
| Symmetric matched-count corruption | Separate noise amount from noise shape | Sensitivity control; never substitutes for a failure |
| Label repair | Restore the class-conditional mutation map | Must reproduce the clean target/model artifact within frozen tolerance |

The primary causal contrast is class-conditional noisy training versus its paired
clean reference. The symmetric matched-count contrast tests the theoretically
motivated shape explanation. The repair and no-op controls detect implementation
or pipeline defects that could masquerade as a label-noise effect.

## 7. Baseline comparison and allowed superiority claim

The legacy baseline is the frozen `accuracy-regression/v1` gate with a one-point
accuracy threshold. The proposed gate uses a strictly proper probability score,
paired uncertainty and a practical-effect requirement.

No superiority claim is currently allowed because no confirmatory result exists.
After execution, the narrow claim “the construct-aware gate detected a
preregistered probabilistic degradation missed by the accuracy-only gate” is
allowed only if:

1. a co-primary cell passes the complete log-loss decision rule;
2. the legacy accuracy gate classifies the same cell as stable;
3. no-op and repair controls pass;
4. the result is reported with raw effects, intervals and all cells;
5. wording is limited to the studied dataset/model/noise shape.

This would establish greater sensitivity to the specified probabilistic failure,
not universal superiority over accuracy, other benchmarks or competing diagnosis
systems. Diagnostic-method superiority remains a later comparison of B0/B1/B2
against A1/A2/A3/FULL under the locked main evaluation.

## 8. Leakage and provenance requirements

The implementation must preserve these boundaries:

- corruption selection sees record IDs and clean training targets only;
- feature matrices and clean development/test targets are immutable inputs;
- mutation maps, directions, declared rates and seeds are evaluator-only;
- diagnosis-facing evidence contains aggregate target-quality observations, not
  intervention names, seed values, source labels or mutated record IDs;
- the source archive, normalized dataset, split, model, preprocessing, mutation
  map, predictions, per-record losses, analysis code and outputs receive
  canonical hashes;
- the protocol hash is embedded in every run and report;
- a result with a mismatched protocol, dataset, split, seed set, endpoint or
  analysis hash fails closed.

The implementation may be developed against synthetic fixtures and the
development partition. The sealed test result cannot be inspected until the
full execution command is ready to produce the complete immutable store once.

## 9. Decision table

| Primary Telco | Bank replication | Decision |
|---|---|---|
| Fail | Any result | Mechanism remains uncovered; narrow claim |
| Pass | Fail | Telco-bounded mechanism coverage; no transfer claim |
| Pass | Same direction passes | Telco coverage plus bounded cross-dataset replication |
| Technical/control failure | Any result | Invalid run; fix defect and replay exact frozen protocol |

A technical replay is allowed only when the failure occurred before outcome
inspection and the reason is recorded. A scientific failure does not authorize
new rates, seeds, endpoints, models or datasets under this protocol.

## 10. Execution requirements

The next implementation must, in order:

1. validate the machine-readable protocol and print its canonical hash;
2. verify the required tag and immutable release or external timestamp exists;
3. implement conditional source-class selection without feature access;
4. create and hash both frozen split manifests;
5. implement per-record probability metrics and the complete control set;
6. implement the two-way bootstrap, sign-flip test and Holm correction;
7. test forged hashes, wrong seeds, partial grids, early stopping, metric rescue,
   control failure and external-replication rescue attempts;
8. run fixture/property/integration gates before opening sealed test outcomes;
9. execute every cell once and persist all artifacts, including failed results;
10. issue a machine-readable GO/NO-GO decision from the frozen rule.

Human blind review begins only for families admitted by this decision. Reviewers
do not select endpoints, thresholds, seeds or families.

## 11. Threats and limits

- A synthetic class-conditional flip is a controlled dominant cause, not a full
  model of human annotation error.
- Thirty corruption seeds quantify intervention randomness, not broad dataset or
  deployment variability.
- One linear model limits model-class generalization.
- Telco's new split uses the same underlying population as alpha.
- Bank replication differs in domain, prevalence and temporal structure; it is
  evidence of transfer only if the same direction passes prospectively.
- The 5% practical threshold is a defensible prospective convention, not a
  universal definition of operational harm.
- A pass validates benchmark eligibility under this contract; it does not by
  itself show that an AI diagnosis is correct, faithful or better than baselines.

## 12. Scientific basis

- Oyen, Kucer, Hengartner and Singh (2022), *Robustness to Label Noise Depends
  on the Shape of the Noise Distribution*, NeurIPS 35.
  <https://proceedings.neurips.cc/paper_files/paper/2022/hash/e7a217c3389b323fe156046ed3aa1e0e-Abstract-Conference.html>
- Natarajan, Dhillon, Ravikumar and Tewari (2013), *Learning with Noisy Labels*,
  NeurIPS 26. <https://proceedings.neurips.cc/paper_files/paper/2013/hash/3871bd64012152bfb53fdf04b401193f-Abstract.html>
- Scott, Blanchard and Handy (2013), *Classification with Asymmetric Label
  Noise: Consistency and Maximal Denoising*, COLT.
  <https://proceedings.mlr.press/v30/Scott13.html>
- Gneiting and Raftery (2007), *Strictly Proper Scoring Rules, Prediction, and
  Estimation*, JASA 102(477). <https://doi.org/10.1198/016214506000001437>
- Owen and Eckles (2012), *Bootstrapping Data Arrays of Arbitrary Order*, Annals
  of Applied Statistics 6(3). <https://doi.org/10.1214/12-AOAS547>
- Demsar (2006), *Statistical Comparisons of Classifiers over Multiple Data
  Sets*, JMLR 7. <https://www.jmlr.org/papers/v7/demsar06a.html>
- Goswami et al. (2023), *AQuA: A Benchmarking Tool for Label Quality
  Assessment*, NeurIPS Datasets and Benchmarks.
  <https://openreview.net/forum?id=dhJ8VbcEtX>
- UCI Bank Marketing dataset, DOI `10.24432/C5K306`.
  <https://archive.ics.uci.edu/dataset/222/bank%2Bmarket>
