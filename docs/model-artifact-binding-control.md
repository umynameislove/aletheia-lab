# Model artifact binding as a positive control (M4)

This contains **development** controls and an unexecuted new-source design, not
an admitted mechanism or protected experiment. It defines one fault: the evaluation process declares a specific
fitted model artifact but loads a different, compatible fitted model artifact.
The intended and loaded artifacts must be separately fitted and have distinct
recorded recipes and byte identities; a separate process is not required. A
worse prediction from a legitimately selected model is not
this fault; the discrepancy between the declared binding and actual load is
the intervention.

## Intervention and causal boundary

For a fixed source, split, ordered rows and targets, transformed feature
matrix, class convention, calibration application, score-column adapter and
scorer, record the intended model's immutable byte digest and run/config
lineage **before** the loader is redirected. The healthy path loads that
artifact. The M4 path redirects only the fitted-model load to another
hash-checked, compatible artifact from a separately recorded fit on the same
training population. Keep the estimator family, feature schema, fitted
preprocessing state and runtime versions fixed. Predeclare exactly how B was
produced, including any seed or *single* fitting-recipe difference needed to
obtain distinct saved scores; do not choose B by searching evaluation loss.
The intervention changes only the loader binding, not a live training/config
setting. Its effect is the operational consequence of loading B where A was
declared, **not** an estimate of that recipe difference or B's intrinsic
quality. The correction restores the intended artifact through the same
loader; it does not replace output numbers or change the evaluator. The actual
loaded digest must be measured at the load boundary, not copied from the
intended manifest or reported by the injected path alone.

The source pair is eligible only if the fitted preprocessing state and ordered
feature schema are identical across both paths. A bundled pipeline swap that
also changes preprocessing is ineligible. The first implementation should
reuse the existing development source/model loader and independent score
witness rather than create a new artifact framework. Calibration parameters,
if used, must be fitted on the healthy training/calibration path before the
swap and then held fixed; do not recalibrate on evaluation targets after
injection. Report both raw and post-calibration score changes. With a fixed
A-calibrator applied to B, the observed loss is the effect of this *operational
wrong-load path*, potentially including model–calibrator incompatibility;
do not claim a pure model-quality or calibration effect. Reject B as
incompatible if the fixed calibration cannot be applied. A live
hyperparameter, feature-set or configuration mutation without a wrong load is
a separate rival construct, not another M4 intervention hidden in this cell.
Never load an untrusted serialized artifact merely to make a control.

The independent verifier should reproduce, from the saved source artifacts:

1. exact intended and actual model digests and their run/config lineage,
   anchored outside the faulting loader and measured on the object it loads;
2. equality of row IDs, target bindings, transformed feature bytes, fitted
   preprocessing state, calibration application, adapter and scorer;
3. the healthy and faulty raw and post-calibration, pre-adapter score vectors,
   their declared metric values, and the exact changed-row footprint;
4. restoration of the healthy score vector and metric after loading the
   intended model again, under an explicit numerical tolerance; and
5. a sham/zero-dose traversal of the same loader that leaves scores and
   metric unchanged.

A hash proves the identity of saved bytes, not the semantic correctness of a
model, label or training run. The verifier must trace the training population
and fitting recipe independently of the injected loader. A changed aggregate
metric without a demonstrated load mismatch is insufficient. A failed or
opposite-direction effect, incompatible feature schema, untrusted artifact,
or correction that does not restore healthy scores remains a visible failed
cell, not a replacement candidate selected after seeing results.

## Rivals and evidence views

| Candidate | Intended vs loaded model | Pre-adapter scores | Row–target binding | Adapter mapping |
| --- | --- | --- | --- | --- |
| Healthy / sham | same | healthy | unchanged | unchanged |
| M4 artifact binding | different | may change | unchanged | unchanged |
| M5 column mapping | same | unchanged | unchanged | changed |
| Target-row binding | same | unchanged | changed | unchanged |
| Preprocessing mismatch | same intended model, but input path changes | may change | unchanged | unchanged |
| Calibration/scorer drift | same | raw scores unchanged | unchanged | unchanged |

The last four are independently constructed hard negatives, not alternative
names for M4. A manifest text edit without an actual wrong load is another
negative control. A legitimate deployment of the alternative model with its
declared manifest updated to match is **not** an M4 fault, even if its loss is
high. The `full` view needs a verified intended-versus-loaded lineage witness
and pre-adapter score evidence that distinguish M4 from these rivals. The
`missing_key` view removes the decisive lineage witness only; it cannot be
called ambiguous unless the **entire serialized model-visible input** is
checked for score magnitude, precision, IDs, order, hashes, metadata, and
context shortcuts. `noisy` adds authentic irrelevant material;
`misleading` adds authentic competing clues, never invented false provenance.
For a later contrastive test, pair M4 and M5 cases with comparable visible
symptoms; different fault loci alone do not establish that a reader can tell
their causes apart. If no such pair survives the shortcut audit, do not
claim G2 distinguishability. No claim about LLM diagnosis follows from
constructing these views.

## Development decision and next implementation

For artifact-binding development, first build one small, reproducible source-model cell and an
independent source-bound verifier. Measure the fault, correction, sham and
rivals on development data, retaining all attempts and failures. Only then
consider more cells or dose tuning. A plausible M4 cell requires a verified
binding intervention, changed pre-adapter scores, a declared nontrivial metric
effect, exact/tolerance-bounded correction, stable competing loci and a
distinguishing `full` witness. If any decisive element fails, record
`rejected` or `assumption_limited` at that scope. This design does not open
U4, justify a protected M4 holdout, or count M4 as an admitted second cause.

### First cell outcome, 29 September 2026

The fixed Online Shoppers/HGB cell used artifact A with 100 boosting iterations
and B with 25, on the same V3 training rows, features and preprocessing. The
loader declared A but deserialized B from its saved, hash-checked bytes; the
independent verifier refitted both models and replayed the saved objects. On
2,466 development rows, 2,466 raw and calibrated scores changed. Re-loading A
restored the A scores and metric exactly, and sham A→A changed nothing. A
legitimate B/B load had the same scores as the wrong A/B load but no binding
violation. No provider, protected outcome or registered attempt was used.

The prespecified metric went from 0.7876888849 (A/A) to 0.7924045065 (A/B),
an increase of **0.0047156216**, below the declared **0.01** minimum. The raw
un-calibrated loss actually improved (0.8397526872 → 0.8376372146); the
observed post-calibration change therefore includes an A-calibrator/B-model
compatibility effect and is not a pure claim about B's quality. Calibration
was fitted and evaluated on the same development partition, so neither
number is an out-of-sample performance estimate. This first cell is
`development_effect_insufficient`: the loader fault is real, but this selected
A/B pair is **not** an admitted positive control. Do not lower the threshold,
replace B post hoc, infer G2 from the candidate views, or unlock U4 on this
result. The private receipt and artifacts are outside the repo; code and
synthetic tests here disclose no source-row predictions.

### Forward early-budget development cell

The next cell is a **new development design**, not a replacement of the first
result. Before any new fit or prediction, `prepare-forward` records the fixed
100/1 HGB recipes, numerical runtime and code, original source/split bindings,
ordered memberships and all predecessor file digests. B is a separately fitted
**early-budget artifact**, not a literal saved checkpoint from A's training.
Both fit the identical original training rows with the original train-fitted
preprocessor. Early stopping remains disabled, learning rate 0.1 and seed 43;
the actual fitted iterations must be 100 and 1. The runner also checks B's raw
scores against A's first `staged_predict_proba` output. B is not chosen by
searching the new measurement loss.

Existing development members are stratified by their labels and ranked with a
fixed seed (`20260930`) and opaque row identities. Half of each class, rounded
down, fits only A's calibrator; the other members measure every path. Training,
calibration and measurement are disjoint by row identity. The original sealed
partition is never predicted. These development data have prior exposure, so
this split removes calibration/measurement reuse but **does not create a pristine
confirmatory holdout**. Duplicate entities, unmeasured dependence and external
generalization are not established by disjoint IDs alone.

The forward primary endpoint is **raw reference-prior-standardized log loss**:
half the mean loss among class 0 plus half the mean among class 1, with strict
finite probabilities in `(0,1)` and no raw-score clipping. Prespecified success
requires changed raw scores, a faulty-minus-healthy increase of at least `0.01`,
and healthy raw loss below the constant predictor using the **training** prior.
This is not the first cell's calibrated-primary endpoint and cannot repair its
failed result. Ordinary empirical log loss and loss after the fixed A-calibrator
are secondary diagnostics; no endpoint is selected after observing its sign.
Class reweighting changes the target distribution, so this standardized loss is
not evidence that the original-population probabilities are better calibrated.
The fixed A-calibrator/B-model combination remains an operational compatibility
effect, not a pure calibration or model-quality effect.

All six paths traverse the same hash-before-deserialization boundary: A/A,
A/B, sham A/A, correction A/A, legitimate B/B, and an untrusted manifest-text
edit with the trusted intended/actual A/A load unchanged. The measured digest
comes from the actual buffer deserialized, not the declared identity. Ordered
raw/calibrated scores, targets and boundary events stay in private files. Sham,
correction and the manifest-only control must reproduce healthy scores exactly;
legitimate B/B must reproduce faulty scores with **no binding violation**.
Separate adapter-reversal and two-row target-swap rivals retain A/A and the
healthy pre-adapter scores. They establish different fault loci, not matched
aggregate symptoms or a validated ambiguous `missing_key` message.

The verifier checks the retained artifact byte identities, independently refits
from the source to check the boundary score vectors (it **never loads supplied
pickles**), and recalculates class-conditional loss directly from row-aligned
vectors. Fresh-fit serialization need not be byte-identical to an earlier file;
byte identity and functional score replay are intentionally separate checks.
It does not call the injector, runner metric helper or runner decision helper.
Code/runtime drift, tampering, score changes in a sham/correction, or an invalid
load fails verification. The load record is instrumentation evidence; it is not
tamper-proof attestation against a malicious process with access to every file.
All weak, opposite or failed cells remain development evidence. No provider,
protected execution, scientific admission, G2 input-equivalence claim or U4
permission follows from this one cell.

The design applies the intervention-boundary distinction in
[Pearl (1995)](https://bayes.cs.ucla.edu/R218-B.pdf), artifact/execution lineage
from [ML Metadata](https://www.tensorflow.org/tfx/guide/mlmd), the iteration and
staged-score contracts of [HGB](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingClassifier.html),
and separate calibration/evaluation motivated by the
[calibration guide](https://scikit-learn.org/stable/modules/calibration.html)
and [Kapoor and Narayanan (2022)](https://arxiv.org/abs/2207.07048).
These are methodological foundations, not claims that artifact identity proves
scientific correctness or that this cell is a novel general diagnosis method.

Use the existing `scripts/model_artifact_binding_development.py` entrypoint:
`prepare-forward`, then `execute-forward --confirm-plan-sha256 <observed hash>`,
then `verify-forward`. Forward operations require `--predecessor-output` for
the unchanged first cell. An existing lease/output is never overwritten;
another exploratory attempt needs a separately identified development cell.

### Retained-cell reader and complete-input audit

`scripts/audit_model_artifact_binding_input.py` reads the already independently
verified forward cell. Supply `--root`, `--cell-dir`, and
`--confirm-receipt-sha256` with the receipt identity from that verification.
It checks retained file/code bindings, load events, raw row-aligned scores,
metrics, sham/correction and rivals without loading a pickle, fitting a model,
predicting again, or rerunning the intervention. It emits an aggregate report
to stdout and leaves the cell unchanged. Its new code hashes are separate from
the historical cell seal; none of the previously sealed implementation changes.

Every view uses the same cause-blind layout and a shared historical A
performance benchmark. The legitimate B/B control retains **its own B
intention**, not A's intention. Local artifact byte identities become two
consistent visible aliases. Private source hashes, case identities, recipes
and intervention labels are not serialized into the evidence.

| View | Visible evidence |
| --- | --- |
| `full` | Raw standardized-loss comparison, trusted intended/loaded artifact binding, pre-adapter class/column probes and source/scoring target probes |
| `missing_key` | Exactly `full` minus the trusted artifact-load binding; the other measurements and witnesses remain |
| `noisy` | `full` plus authentic feature, training and calibration dimensions |
| `misleading` | `full` plus authentic reported manifest text, explicitly distinguished from trusted load instrumentation |

The raw-primary endpoint uses raw score probes, not calibrated probes. The
same first class-0/class-1 measurement rows are used in every path. These
probes witness the retained two-row target swap; they do not establish target
integrity for arbitrary rows or label faults. Numerical projections use six
decimals, with raw metric equality and twelve-decimal projections reported
separately so rounding cannot be silently called exact equality.

The audit passes each context through the existing recovery adapter's common
frozen A2 prompt/schema with a non-network SDK substitute. It compares the
complete captured SDK arguments: messages, response schema, model and sampling
settings. Only the checked opaque request-ID header is excluded. Unknown SDK
fields, extra telemetry or extra invocations fail the audit. Identifiers and
content digests exposed to the model derive solely from the selected view;
changing a private source identity or internal case ID must not change the
input used for equality. This is an SDK-interface test, **not** a live HTTP
test, frozen M4 diagnosis protocol, or LLM diagnosis experiment.

The genuine A/B fault and legitimate B/B control can have identical missing-key
inputs because they load the same B scores. The full load witness distinguishes
their binding status. On a finite pair with equal priors, identical inputs and
no side channel limit expected payload-only binary classification accuracy to
one half. A lookup ceiling on distinct inputs is only potential separability;
neither value is measured LLM accuracy. Legitimate B is a no-binding-fault
control, **not a second causal mechanism**. Adapter reversal and target swap
remain separate rivals; their input/metric differences are retained rather
than erased to manufacture cross-fault ambiguity. Missing-key equivalence with
those rivals must be demonstrated separately before any matched-cause study.

This approach uses artifact/execution lineage from
[ML Metadata](https://www.tensorflow.org/tfx/guide/mlmd) and checks alternative
input shortcuts rather than inferring explanation quality from performance
alone, following the caution in [Lapuschkin et al. (2019)](https://arxiv.org/abs/1902.10178).
Construct verification and diagnostic inference remain separate: recent
[OpenRCA 2.0](https://arxiv.org/html/2606.27154v2) also distinguishes verifying
behavior from inferring its cause. These motivate this development audit; they
do not establish novel theory, mechanism admission, external generalization
or permission for protected execution.

## Development dose sweep and prospective M4 design

The dose sweep retains the forward cell's exact A artifact bytes, train-fitted
preprocessor, A-calibrator and calibration/measurement memberships. Its fixed
grid independently fits B with **1, 5, 10, 25 and 50 iterations**, changing no
other fitting parameter. Dose describes the alternate artifact's provenance;
the fault still redirects only the evaluation loader. Zero-dose/sham and
correction load the same retained A bytes, rather than a separately fitted
B100 whose serialization might differ. Each B is compared functionally with
the corresponding staged predictions from A; a stage match does not establish
literal checkpoint provenance.

All five budgets, six loader paths and two rival constructs are retained. The
raw standardized effect, empirical raw effect and fixed-A calibrated effect
are reported separately, without selecting a best dose or endpoint. The
verifier refits from source and recomputes class-conditional losses without
loading supplied pickles, invoking the injector, or using the runner's metric
and decision helpers. It also replays all 160 non-network SDK-interface
captures. The input audit retains the class-column and two-row target witnesses:
changing budget cannot justify removing these witnesses to force a cross-fault
match. Legitimate B/B remains a no-binding-fault control, not a second cause.

For the **new** prospective design, a positive control additionally requires
healthy raw standardized loss strictly below `ln(2)` (the uniform predictor)
and positive empirical wrong-load loss change, as well as the `0.01` raw
standardized effect and unchanged-locus/control checks. The historical forward
cell's training-prior adequacy rule is not rewritten. A calibrated improvement
cannot rescue a failed raw-primary gate. These checks distinguish a worsening
wrong-load path from a comparison whose intended baseline is already weak for
the specified reference distribution. They do not establish estimator
optimality, calibration quality under natural prevalence, or LLM accuracy.

The separate prospective protocol is
[`model_artifact_binding_new_source_protocol.json`](../configs/benchmark/model_artifact_binding_new_source_protocol.json).
It selects [Banknote Authentication](https://archive.ics.uci.edu/dataset/267/banknote%2Bauthentication)
and [Wisconsin Diagnostic Breast Cancer](https://archive.ics.uci.edu/dataset/17/breast%2Bcancer%2Bwisconsin%2Bdiagnostic)
by numeric/binary schema, license and distinct source provenance, **not fitted
performance**. Archive/member size and SHA-256, target encodings, ID exclusion,
split, fixed A100/B1 recipe, calibration, endpoint, reader precision and failure
policy are pinned. B1 is the minimum compatible early-budget control, not the
winner selected from the development loss curve. A100/B1 are fitted only on
train; the A-calibrator fits only calibration; final is a separate partition.

Union components join repeated feature vectors and repeated available subject
IDs before a target-blind 60/20/20 hash split. Class-support checks reject an
ineligible split rather than reroll it. This prevents observed groups from
crossing partitions, but cannot prove independence of unrecorded physical
subjects or public benchmark novelty to an LLM. Two sources are **two source
clusters**, not independent replicates for each budget, row or evidence view.
The final census retains both sources, including weak, opposite, unmatched,
incompatible and technical-failure cells; it has no replacement search or
population-confidence-interval claim.

`scripts/model_artifact_binding_dose.py` supplies `prepare`, `execute` and
`verify` for exposed development only. These require the source archive,
verified predecessor directory and receipt digest, and a private output;
execution additionally requires the prepared plan digest. `inventory` checks
the pinned new-source archives and grouped memberships without fitting a model
or computing final predictions. Its optional output is immutable and private.
**No operation in this entrypoint executes the new-source final study.** That
requires a later runner, clean committed code/runtime/inventory execution seal
and an explicit protected-execution decision. No provider call or mechanism
admission is authorized by this design lock.

The intervention boundary follows [Pearl (1995)](https://bayes.cs.ucla.edu/R218-B.pdf);
development/final separation follows the caution about adaptive evaluation in
[Dwork et al. (2015)](https://arxiv.org/abs/1506.02629). Dataset-level replication,
rather than counting repeated fits as independent samples, follows
[Demšar (2006)](https://jmlr.org/papers/volume7/demsar06a/demsar06a.pdf).
These support a bounded, reproducible control study, not a new general causal
identification theorem or a diagnosis of the entire system architecture.

### New-source M4 runner: offline readiness

The entry point is `scripts/model_artifact_binding_new_source_study.py`.
The Banknote/WDBC protocol bytes and original prediction-blind inventory
remain unchanged. This checkpoint implements and tests the runner; it does
not report real new-source model effects or authorize final execution.

| Operation | Scope |
| --- | --- |
| `preflight` | Reads pinned archives, original inventory, target-blind memberships, code/runtime and synthetic reader captures. Writes nothing and fits no estimator or scaler. |
| `prepare` | Seals `execution-plan.json` in the existing private study directory, from the committed clean checkout. Still no model fitting or final prediction. |
| `execute` | Requires the exact plan hash and explicit scoped final-execution authorization. Acquires one immutable lease and retains both source cells, including failures. |
| `verify` | Rechecks the seal and file census, independently refits completed cells and recomputes their metrics, controls, rival ledgers and decisions. Never loads supplied model pickles. |

Preparation binds all source code, the entry point, protocol, package
configuration, reader contracts, Git commit, runtime versions, single-thread
policy, original inventory and exact synthetic SDK-interface captures. Drift
fails before a lease or fit. Interruption after the lease retains both census
dispositions and blocks repeat execution. A runtime-failure record can have
verified integrity without a successful numerical replay; it is not counted
as an effect-valid or structurally verified cell.

Execution fits one train-only StandardScaler and two separately fitted HGB
artifacts, A100 and B1, on the same training rows. Calibration fits A only on
the separate calibration partition, with fit clipping `1e-12`; every loader
path uses that same calibrator with application clipping `1e-15`. The six
controls retain raw and calibrated scores, actual deserialization-buffer
hashes and unchanged preprocessor/features/targets/calibration/adapter/scorer
bindings. Column reversal and two-row target swap retain A at the loader and
change their respective downstream locus only.

`G1` requires changed raw scores, standardized raw loss increase at least
`0.01`, healthy raw loss strictly below `ln(2)`, positive empirical raw loss
increase and exact controls. Calibrated improvement cannot rescue a raw
failure. `G2` separately reports whether either different-locus rival has
the same complete missing-key SDK input and a distinguishing full input.
Legitimate B/B is a no-binding-fault control, not a second fault mechanism.
Both source cells stay in each denominator; there is no outcome-dependent
source, budget or threshold replacement.

Synthetic tests cover preparation without fit/prediction/network access,
train-only preprocessing, separate calibration, boundary decisions, exact
controls, signed-result tampering, drift, failure retention and lease races.
The real-archive preflight remains prediction-blind: 1,941 source rows and
385 final rows are metadata counts, not scored outcomes. The current checkout
must be committed before an execution seal can be prepared.

The control equalities are an application of
[metamorphic testing](https://arxiv.org/abs/2002.12543), not a substitute for
the independent numerical replay. Train-only preprocessing follows the
[scikit-learn leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html).
Avoiding supplied pickle loading follows its
[model persistence security guidance](https://scikit-learn.org/stable/model_persistence.html).
Refit replay checks reproducibility under the bound environment; the saved
loader trace does not attest against a malicious process rewriting all
evidence. Two source clusters support descriptive finite-control findings,
not source-population confidence intervals or LLM diagnosis accuracy.

## Separate M5 new-source readiness

M5 development has demonstrated a six-decimal observation boundary, not an
unrestricted zero-leakage property. Its 12 constructed pairs differ at twelve
decimals. The later MAGIC/Spambase target-binding test has already exposed
those sources to the M5 rival-matching method; they are not untouched final
M5 sources. The two selected independent source families are
[HTRU2](https://archive.ics.uci.edu/dataset/372/htru2) and
[Rice (Cammeo and Osmancik)](https://archive.ics.uci.edu/dataset/545/rice%2Bcammeo%2Band%2Bosmancik).
Their official metadata lists binary labels, numeric features and a reusable
license. The prospective **design** selects these sources in
[`score_mapping_new_source_protocol.json`](../configs/benchmark/score_mapping_new_source_protocol.json).
The offline inventory verified exact archive/member bytes, 17,898 HTRU2 and
3,810 Rice rows, target encodings, all 21,708 deterministic split memberships,
and nonempty binary classes in train/calibration/final. No exact duplicate
feature rows were present within either source. Different domains and feature
schemas support source-level independence from earlier development/TB data,
but do not prove absence of every possible data-origin overlap. Two sources
are **two independent clusters**, not four independent results because each
source has two estimators. The current protocol SHA-256 is
`428ee882d1857967511f32deb5336588f4aa0d7c8ca2e26e2d01dfbb5866d10f`;
the private prediction-blind inventory binds the archive and split hashes.
The following decisions are fixed before new-source model scores or metrics
are computed:

- independent source/family acquisition and eligibility, byte identity,
  duplicate/version-overlap audit, grouped split/membership and exact
  row–target lineage; multiple estimators or folds on one source are not
  independent source replicates;
- estimator, preprocessor, calibration, code/runtime and scorer identity;
  dose/selector, rival construction, sham, correction and fixed failure rule;
- the actual complete model-visible payload for each view, including numeric
  precision, IDs and metadata; separately audit raw/high-precision values and
  SDK fields that are **not** model-visible;
- family/source as the analysis units, all eligible cells and unmatched
  rivals in the denominator, exact admission/falsification criteria and
  multiplicity/uncertainty handling; and
- a prospective holdout boundary, no retuning on its outcome, a feasible P6
  reserve and a separate U3 execution decision.

**Runner readiness, 29 September 2026:** the new-source runner implements
prediction-blind preflight/sealing, fixed-dose execution and independent
verification. End-to-end tests use synthetic CSV/ARFF sources with both fixed
estimators. Preflight on the pinned public archives checks the existing
inventory and all split memberships without fitting either source. The
historical design hash is unchanged. This is a locally verified runner, **not
an executed new-source study**. The execution plan must still be sealed from
the committed checkout and separately authorized before final outcomes are
computed. No real new-source estimator has been fitted at this checkpoint.

### Entry point and retained artifacts

Use `scripts/score_mapping_new_source_study.py` with four operations:

| Operation | Reads or writes | Models / provider |
| --- | --- | --- |
| `preflight` | Checks pinned archives, original inventory, split, code/runtime and synthetic input audit; writes nothing | No new-source fit; no provider |
| `prepare` | Creates immutable `execution-plan.json` in the existing private source directory, from a clean committed checkout | No new-source fit; no provider |
| `execute` | Requires the exact plan hash and separate `--authorize-final-execution`; creates one immutable lease and retains all four cells | Fits train only, calibrates calibration only, measures final; no provider |
| `verify` | Checks the seal and retained files, independently regenerates source scores/calibration and replays metric/donor ledgers | Deterministic source replay; no new intervention search or provider |

The plan binds all source code, the entrypoint, prospective protocol, package
configuration, common input contracts, commit, numerical package versions,
platform and single-thread policy. It also binds the original private
inventory; the source directory is reused rather than replaced by another
roadmap or results folder. A code/runtime/source drift fails before fitting.
An existing lease blocks repeat execution even after interruption. Failures
and unmatched rivals stay in the denominator of four cells; two estimators
on a source do not become independent source replications. `G1` measures the
mapping effect, unchanged upstream scores, sham, correction and full witness.
`G2` separately measures exact six-decimal missing-key input equality and a
distinguishing full witness. Neither gate is inferred from the other.

Source verification first checks retained hashes, then rebuilds the trusted
train-fitted estimator and calibration-only calibrator and compares the exact
saved scores. The verifier never deserializes a supplied model pickle. It
recomputes the mapping footprint from row IDs and evaluates the saved donor
ledger with the runtime scorer, without rerunning the injector or greedy
matcher. Verification establishes deterministic reproducibility under the
sealed environment; it is not another selected experimental attempt.

### Input-channel scope

The offline audit sends each view through the production adapter into a
fake SDK client. It captures every SDK-interface field: both messages,
response schema, model snapshot, sampling, output ceiling and timeout. Only
the verified opaque request-ID header is classified separately from model
inputs. Equality means exact UTF-8 JSON bytes of these captured interface
objects, including the exact message strings. This does **not** claim to run
the OpenAI SDK's HTTP serializer or measure a live provider. The common frozen
A2 prompt/schema is an input-channel witness, not an LLM efficacy study. A
future study with a different prompt, adapter or side channel needs its own
actual-input audit. See the official [Chat Completions contract](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create)
and [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

The audit reports raw and twelve-decimal equality as counterfactual results;
it does not require those channels to differ in every pair. A metric gap
below `5e-7` alone cannot establish six-decimal message equality: values on
opposite sides of a rounding boundary can satisfy that gap and still produce
different inputs. Empty/unmatched rivals remain failures of the specified
greedy procedure, not proof that all possible target-binding rivals fail.

### Why limited or negative effects do not diagnose the architecture

For binary cross-entropy, `loss(y, 1-p) = loss(1-y, p)` at each row. However,
the registered metric averages class-conditional losses with coefficients
`1/(2*n_y)`. With original labels held fixed, the mapping effect on selected
rows is `sum((2*y_i-1)*logit(p_i)/(2*n_yi))`. Thus the effect depends on score
confidence and correctness; a real mapping error need not increase loss.
At `p=0.5`, inversion is unobservable. Target swaps change row-specific class
weights on imbalanced sources, even when the total class counts are retained.
The subset BCE identity therefore does not establish equality of the actual
weighted metric. A synthetic regression test gives mapping loss 1.4361511173
and target-flip loss 1.6094379124 despite identical unweighted BCE and retained
class counts. Global flipping is a separate sufficient symmetry.

The source-class contract is the estimator's actual `classes_`, not a guessed
positive column. [Logistic Regression](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LogisticRegression.html)
and [histogram gradient boosting](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingClassifier.html)
provide two distinct fixed estimator families for testing the same evaluator
boundary. They are not two LLM architectures or a universal architecture
benchmark. No model, dose or source is added after seeing final results.

Separating train, calibration and final avoids using measured labels to fit
the post-hoc calibration; [Guo et al.](https://proceedings.mlr.press/v70/guo17a.html)
motivate the fixed-predictor/validation-calibration distinction, not this
project's exact calibrator. The two-source descriptive analysis follows the
independent-dataset concern in [Demšar](https://jmlr.org/papers/v7/demsar06a.html);
four cells cannot support source-population inference. If the final cells
are limited, first classify whether the binding failed, the effect was too
small/nonpositive, or the frozen rival failed to match. Those are different
findings, none of which alone proves an estimator architecture defect.

Source selection, dose and acceptance rules must be frozen before any final
source outcomes are inspected; a source used to tune these rules becomes
development, not the final independent test. Existing development sources
and the opened MAGIC/Spambase result can inform design but cannot become the
untouched final M5 test. Offline checks may prepare the protocol; no protected
data, provider call or one-shot attempt is
authorized here. A successful local intervention alone would establish a
finite constructed control, not natural fault prevalence or LLM accuracy.

This design follows the distinction between ML artifact lineage and output
behavior in [ML Metadata](https://www.tensorflow.org/tfx/guide/mlmd) and
[MLflow model provenance](https://mlflow.org/docs/latest/ml/model-registry/),
the deployment-dependency risks described by
[Sculley et al.](https://papers.neurips.cc/paper/5656-hidden-technical-debt-in-machine-learning-systems.pdf),
and [scikit-learn's warnings](https://scikit-learn.org/stable/model_persistence.html)
on version compatibility and loading untrusted serialized models. The need
to keep an adaptive development set separate from a final test is supported
by [Dwork et al.](https://arxiv.org/abs/1506.02629). These sources motivate
the construct and safeguards; none proves that M4 has passed in this project.

## Development pilot: loader status with and without artifact lineage

The follow-on question is narrower than cause-of-loss diagnosis: **does the
fitted-model artifact loaded match the artifact requested?** `binding_fault`
means a loader mismatch. `no_binding_fault` means this loader binding matches,
not that all other pipeline loci are healthy or that the model performs well.
The retained wrong A/B and legitimate B/B paths share scores and loss. Their
complete `missing_key` inputs are equal even without rounding; `full` contains
different trusted intended/loaded bindings. This fault–legitimate pair does
not create a second fault mechanism or repair cross-fault matching results.

`artifact_lineage_policy_pilot.py` reads the already independently verified
Online Shoppers development cell. Preparation hashes supplied model artifacts
but never loads them, fits models, predicts again, or reads new-source final
outcomes. The census retains all six loader controls and both column/target
rivals. A bijective alias swap counterbalances the two local artifact names.
Eight observations × two alias orders × four views give 64 planned views,
**one exposed source cluster**, not 64 independent families. Both cross-locus
rivals have intact loader bindings, so their loader answer is `no_binding_fault`
even though another fault is present.

The exact visible-context census has 36 distinct inputs. Each policy receives
one completion per distinct input, then that response is joined to all retained
worlds with the same input. The matched A3-derived and A4 instructions share
the task, evidence, response grammar and runtime; A4 adds consideration of the
two loader-status alternatives. A3-derived is not the historical P5 A3 arm.
All actual SDK-interface arguments are captured with a non-network client at
preflight. Private truth, references, case/source/condition/alias-order IDs,
receipt hashes and paths are outside messages and options. An unknown transport
field or changed request prevents the input audit from passing. These are SDK
captures, not live HTTP or LLM results.

The output is a flat decision (`binding_fault`, `no_binding_fault`, `abstain`,
`check_binding`), its basis, and cited visible field IDs. Duplicate or unknown
JSON fields are rejected. Wrong decisions, wrong non-answer reasons and missing
citations are assessable scientific errors, separate from technical/parser
failures. There is no free-prose field or second hidden cause field. The visible
trusted binding proves a mismatch, **not that it caused the loss change**;
correction experiments are not included as visible causal evidence here.

The main development endpoint requires both worlds' full views resolved with
the binding witness **and** their missing-lineage views bounded. Report full
fault recall, legitimate-B false positives, sufficient-view nonresolution,
missing-view unique commitments, next-check correctness, citation compliance,
failures, unexecuted requests, latency, tokens and cost separately. Selective
unwarranted risk uses committed decisions; it is `null`, not zero, when coverage
is zero. Report a single risk/coverage operating point, not AURC or a population
confidence interval. Alias/view permutations are shortcut checks, not source
replication. Deterministic trusted-binding, always-abstain and metric-only
controls make extraction ceilings and symptom shortcuts explicit.

This extends paired evidence diagnostics, not the invention of evidence-first
reasoning. [EviScope](https://arxiv.org/html/2609.17081v1) shows why full/missing
joint success and wrong non-answer actions matter and why an explicit gate must
not be assumed to outperform a simple prompt. [TraceBench](https://arxiv.org/html/2608.27182)
studies ambiguous intervention/configuration observations; here the ambiguous
loader-status pair is retained for warrant testing rather than turned into an
answerable hidden-label task. [ML Metadata](https://www.tensorflow.org/tfx/guide/mlmd)
motivates the execution–artifact lineage boundary, not causal attribution of
loss. [Franc et al.](https://jmlr.org/papers/v24/21-0048.html) motivate reporting
resolution/coverage with reject-option risk. These are design motivations, not
evidence that A4 improves on A3-derived in this pilot.

### Local workflow and paid boundary

Preparation and preflight verification need no key or network access:

```sh
PYTHONPATH=src python scripts/artifact_lineage_policy_pilot.py prepare \
  --root . --memory-root ../memory --pilot-dir ../memory/artifact-lineage-policy-development-v1
PYTHONPATH=src python scripts/artifact_lineage_policy_pilot.py preflight \
  --root . --memory-root ../memory --pilot-dir ../memory/artifact-lineage-policy-development-v1
```

After explicit approval of the current plan digest, destination and payload,
`execute --confirm-plan-sha256 <current-plan-digest>` calls only
`https://api.openai.com/v1/chat/completions`, snapshot `gpt-4.1-2025-04-14`.
At most **72 calls**, zero retries, temperature 0, seed 731, timeout 90 seconds,
8,192 input and 1,024 output tokens per call. The frozen-rate reservation is
**$1.769472** under a **$2.00 ceiling**; this is not a predicted bill.
`store=false` does not assert zero provider-side retention. The existing caller
rejects destination, organization, project and proxy overrides. No human
judgments or raw source rows are sent. All replies and raw records stay private.

Execution retains every attempt, stops after three consecutive invalid/provider
responses and does not silently replay charged calls. `verify` rechecks the
source/code/runtime, order, stop rule, resources, results and analysis offline.
API keys and exception text are not printed or persisted. Do not execute this
development pilot or infer policy superiority from preparation/tests alone.
