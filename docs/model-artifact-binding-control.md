# Model artifact binding as a positive control (M4)

This is a development-stage design and one executed **development** cell, not an
admitted mechanism or protected experiment. It defines one fault: the evaluation process declares a specific
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

For `V5-M4-01`, first build one small, reproducible source-model cell and an
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
