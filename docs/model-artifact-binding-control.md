# Model artifact binding as a positive control (M4)

This documents **development** controls, fixed new-source protocols and their
execution/replay workflows. Neither implementation nor an execution receipt
automatically admits a causal mechanism. It defines one fault: the evaluation process declares a specific
fitted model artifact but loads a different, compatible fitted model artifact.
The intended and loaded artifacts must be separately fitted and have distinct
recorded recipes and byte identities; a separate process is not required. A
worse prediction from a legitimately selected model is not
this fault; the discrepancy between the declared binding and actual load is
the intervention.

**Current methodological scope:** preserve each historical control, protocol and
run below. New forward work prioritizes source-faithful evidence admission into
a fixed resolver, with an adapted deterministic parser as the matched comparator.
The dated offline-readiness sections describe preparation checkpoints; they are
not instructions to repeat a consumed execution. Numeric private outcomes and
operator task tracking are not published by this documentation update.

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

### Completed direct-witness pilot and compositional follow-up

`V6-M4-LIN-DEV-01` is complete at its development scope: 72/72 unique requests
were parsed and replayed offline. Both policies resolved all 48 sufficient
view slots and returned the warranted binding check on all 16 missing slots.
All 36 matched contexts had identical parsed decisions. The observed frozen-rate
cost was $0.149212; aliases and reused view slots are not independent sources.
This is a genuine ceiling/tie, not evidence of A4 superiority or deep causal
reasoning. The old prompt supplied a direct witness and its comparison rule.
The completed pilot and all source/code bindings remain unchanged.

`V6-A4-DEV-02` therefore asks a new, explicitly **exposed development** question:
can the policies compose scoped lineage facts and respond appropriately when
those facts identify, underdetermine, or contradict the requested/loaded byte
identity? It is not a confirmatory rescue of the old result. The code lives in
`compositional_lineage.py`, `compositional_lineage_cases.py`, and
`compositional_lineage_pilot.py`; the existing paid caller and immutable private
IO are reused without modifying the completed pilot.

#### Reference model and scope

The requested endpoint is the artifact in the **pinned immutable snapshot**.
The consumed endpoint is obtained from the target attempt's execution, its
actual consumed buffer, and that buffer's byte identity. This is not a temporal
latest-manifest task. Declarations and symptom reports are not consumption
measurements. Requested and loaded endpoints can match even when a declaration
does not; a wrong buffer can differ even when the declaration matches.

There are eight independent binary variables: request snapshot, request
execution, two snapshot/artifact mappings, two execution/buffer mappings, and
two buffer/artifact mappings. Every mapping is total but need not be injective.
Unobserved variables may take either admitted domain value; no default is
inferred. The 256 complete worlds are fixed before outputs. Seven primitive
record kinds are admitted by the task contract, outside record prose. Only the
exact request **and** attempt scope constrains this target. All admitted facts
in that scope must be consistent, including unselected branches. This global
integrity convention is intentional; it is not a claim that such sources are
authenticated by a self-reported kind or `trusted=true` string in deployment.

For visible records V, let W(V) be all worlds satisfying admitted constraints,
and C(V) their requested-versus-loaded statuses. A singleton C warrants a
commitment. Both statuses warrant a nonanswer or measurement. Empty W means
conflicting admitted evidence, not an answer certified by vacuous entailment.
The independent reference uses domain intersections and reachable endpoints;
it is equivalent here because the requested and loaded variable dependencies
are disjoint. Shared-variable extensions would require a new oracle.

Three finite-model properties are tested: indistinguishable fault/match worlds
cannot warrant a unique answer; adding consistent admitted facts only restricts
the fixed world set; and removing an unnecessary fact can preserve a singleton.
These apply established diagnosability/consistency ideas, not a novel general
theory of diagnosis. [Reiter](https://www.cs.ru.nl/P.Lucas/teaching/KeR/reiter.pdf)
motivates consistency and discriminating measurements;
[Rintanen](https://www.ijcai.org/Proceedings/07/Papers/085.pdf) supplies the
observable-equivalence foundation. Minimal diagnoses can change under new
measurements: our refinement property is about a **fixed complete world set**,
not an evolving list of minimal causal explanations.

[W3C PROV-DM](https://www.w3.org/TR/prov-dm/) motivates explicit entity/activity
joins; [PROV-AQ](https://www.w3.org/TR/prov-aq/#interpretation) distinguishes
provenance from authority. [ML Metadata's schema](https://raw.githubusercontent.com/google/ml-metadata/master/ml_metadata/proto/metadata_store.proto)
distinguishes declared inputs from artifacts actually read. We consequently do
not promote declaration prose, recency or record position into a binding fact.

#### Census, actions and falsification

Twelve authored motifs cover complete fault and legitimate matching consumption,
pinned-old snapshots, declaration/consumption disagreement, off-attempt and
reported decoys, missing request/execution/manifest facts, redundant missing
proof, both unknown endpoints, and admitted-record conflict. Two artifact alias
orders and two record orders give **48 dependent view slots: 28 identified,
16 ambiguous, 4 conflicting**. There is **one retained Online Shoppers
development source**. Event envelopes and symptom reports are authored
constructions anchored to retained A/B identities, not measured deployment logs
or new independent families. Two list orders are not two representations;
equivalent table/log decoding is tested offline, not claimed as a paid format
replication. No fitting, deserialization or new-source final outcome is used.

The flat response has `decision`, `basis`, `next_check`, and `cited_records`.
For a commitment, the cited subset must itself entail the claimed status; merely
citing existing IDs is insufficient. Conflict citations must themselves be
inconsistent. Missing-proof nonanswers and conflicts have distinct reasons.
The available queries measure requested, loaded, or both endpoints, with costs
1, 1, and 2. A query guarantees resolution only when every feasible result
leaves one status. All least-cost qualifying queries are accepted; there is no
fabricated prior or expected-information-gain claim. `reconcile_records` flags
the integrity problem; it is **not** scored as a guaranteed endpoint repair.

The primary development endpoint is justified action success over all planned
views: resolution where identifiable, a least-cost guaranteed query where
ambiguous, and an evidenced conflict flag where inconsistent. Bounded abstention
is separately credited on ambiguous views but does not earn query-utility credit.
The constant abstention baseline uses a valid nonanswer grammar. A symptom-only
shortcut is included alongside the visible resolver. Report raw resolution,
boundedness, conflict handling, unsupported commitment, citation proof and
resources separately, then A4-minus-A3 paired transitions over the **12 authored
motifs** with their four dependent variants. No population CI or AURC is implied.

Both policy arms receive identical contexts, primitive semantics, response
schema, model, sampling, input/output budgets and failure rules. Only the
policy instruction differs; A3 is not weakened or forced to guess. Model answers
are never used to choose a motif, change the oracle, repair semantic errors, or
drop a planned denominator. Tests enumerate all 6,561 partial primitive
assignments against an independently authored integer-world reference, plus
complete-world/direct-endpoint combinations, removals, contradictions,
counterpart worlds, alias/order changes, citation subsets and wire privacy.

[EviScope](https://arxiv.org/html/2609.17081v1) motivates paired action diagnostics
but explicitly leaves authority, recency and multi-document synthesis outside
its current scope; it does not supply our trust/lineage oracle. The
[benchmark-saturation study](https://proceedings.mlr.press/v306/akhtar26a.html)
supports investigating headroom, not assuming that a harder benchmark will
produce the preferred ranking. Any later live difference is a finite matched
prompt-policy result; it does not admit a second mechanism or change P5.

#### Operator boundary for the new pilot

`scripts/compositional_lineage_pilot.py` supports `prepare`, `preflight`,
`execute`, and `verify`, using a **new private directory**. Preparation requires
the completed predecessor receipt to replay and checks retained source/code
bindings. It seals corpus, independent oracle audit, complete SDK-interface
capture, baselines, prompts, schema, runtime, code, and finite budget. Verification
rebuilds the results without a network client. Use the prepared directory's
current plan digest; do not rerun either completed predecessor.

After owner approval only, at most **96 unique calls**, zero retries, use
`gpt-4.1-2025-04-14` at `https://api.openai.com/v1/chat/completions`. The worst-case
frozen-rate reservation is **$2.359296**, ceiling **$2.50**, not a bill estimate.
Only task instructions, constructed visible records and the decision schema are
sent. Source/receipt hashes, paths, oracle/worlds, case/motif IDs, raw rows and
human labels are withheld. Exact SDK arguments are captured offline; this does
not establish live HTTP correctness. Strict structured output is still locally
parsed and semantically assessed. [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
does not guarantee factual or evidential correctness.

Three consecutive technical/schema failures stop execution; semantic mistakes
remain measured outcomes. Every uncalled or failed request remains in planned
denominators. Interrupted runs are not silently replayed. If both policies
still reach the ceiling, close headroom for this frame rather than automatically
expanding calls to manufacture a difference. Live execution and replay remain
required before this task can report measured development performance or be
marked Done; they would not establish population policy efficacy.

### Proof-aware cached development guard

`V6-A4-DEV-03` adds a local post-generation guard after the compositional live
pilot. The original prompts, corpus, decisions, oracle, analysis and receipt
remain unchanged. This is a **post-observation development intervention**, not
a revised score for the old pilot or evidence of transfer to new data.

The architecture is an LLM proposal followed by visible-evidence checking.
[LINC](https://aclanthology.org/2023.emnlp-main.313/) and
[Logic-LM](https://aclanthology.org/2023.findings-emnlp.248/) already establish
LLM/symbolic-solver decompositions; unlike their natural-language formalization
stage, this prototype starts with typed constructed facts. It therefore does
not test a semantic parser, real-log authentication, or general causal diagnosis.
[ChopChop](https://arxiv.org/abs/2509.00360) motivates semantic rather than merely
syntactic constraints, but our guard checks **after** generation: it is not
constrained token decoding or an implementation of ChopChop's realizability
algorithm. The research opportunity is the task-specific interaction of scoped
lineage, independently checked citations, nonanswer and measurement, with
explicit attribution of tool assistance; novelty beyond that requires further
related-work and new-data evaluation.

#### Conditional correctness and action boundaries

For admitted visible facts V and cited subset S, a status c is accepted only
when W(V) is nonempty, every world in W(V) has status c, and S itself entails c
with nonempty W(S). The **whole** admitted context is checked first; a convenient
consistent citation subset cannot hide conflicting facts elsewhere. Duplicate,
unknown, nonconstraint or off-request/attempt citations cannot certify a claim.
Empty W(V) warrants an evidenced conflict flag, never vacuous certainty.

The guard uses domain intersections and endpoint reachability; the evaluation
reference independently enumerates complete worlds. Their equivalence relies
on this finite model's disjoint endpoint dependencies. Extending to shared
variables, other artifacts or untrusted natural-language records requires a
new model/checker validation. Correctness is conditional on the admitted facts
and their semantics; an artifact match is not global health and a mismatch does
not establish that it caused the measured loss.

Four stages use the **same cached proposals in both arms**:

| Stage | Allowed intervention |
|---|---|
| `raw` | Reproduce the unchanged original assessment. |
| `action_canonicalized` | Interpret an explicit endpoint check accompanying `abstain` and `underdetermined` as `check_evidence`; change no basis, endpoint or citation. This is an action-contract intervention, not format-only repair. |
| `reject_only` | Preserve a warranted normalized proposal; otherwise return a refusal without granting resolution/query utility. |
| `proof_guarded` | Preserve warranted proposals; add a visible certificate for an already-correct status; generate a resolving query or evidenced conflict flag when appropriate; refuse an incorrect identified status. |

Proof reconstruction never replaces an identified wrong answer with the correct
resolver answer. Certificates are deterministically **inclusion-minimal**, not
minimum-cardinality. Queries are cheapest among those guaranteed to resolve all
feasible endpoint outcomes under fixed costs 1/1/2, not estimated information
gain. Missing or unparseable proposals stay unavailable in all stages and retain
their planned denominator; they cannot acquire successful synthetic safe outputs.

Provenance separates `model`, `proof_reconstructed`, `resolver_query`,
`resolver_conflict`, `rejected` and `invalid_proposal`. Canonicalization is also
counted separately. Tool-generated proof/query/conflict actions are system
capabilities, not additional unaided LLM reasoning. An always-abstain and a
visible-resolver-only baseline remain in the comparison. Report both risk and
coverage: rejection can improve conditional correctness by reducing commitments,
as studied in [selective classification](https://www.jmlr.org/papers/v11/el-yaniv10a.html).
Do not infer a population risk guarantee or AURC from this fixed finite frame.

#### Replay and verification

`scripts/analyze_compositional_lineage_guard.py` is read-only with respect to the
completed pilot and has **no paid execute entrypoint**. It verifies the original
receipt before and after analysis, hashes the source tree, reproduces the frozen
raw arm/motif summaries, and retains the original paid resource accounting.
New calls and provider cost are zero; checker latency is explicitly unmeasured.
An optional output is aggregate-only and immutable, outside Git checkouts and
outside the original pilot directory. It contains no raw proposals or records.

From a checkout, reproduce the aggregate in stdout with an existing interpreter:

```sh
PYTHONPATH=src "$ALETHEIA_PYTHON" scripts/analyze_compositional_lineage_guard.py \
  --root . --memory-root "$ALETHEIA_MEMORY" \
  --pilot-dir "$ALETHEIA_MEMORY/compositional-lineage-policy-development-v1"
```

Here `ALETHEIA_PYTHON` is the existing project Python executable and
`ALETHEIA_MEMORY` the operator's private memory root. Set them once; do not place
private paths, inputs or results in the repository. `--output` is optional and
must name a new private aggregate file, not a location inside the source run.

Tests independently check complete worlds, partial constraints, citations,
minimal proofs, redundant missing facts, conflict precedence, authority/scope
decoys, aliases/order, the 24-record bound, mutated proposals and refusal
idempotence. Replay tests retain full and stopped-prefix denominators, reject
identity/status/resource tampering, detect source mutation, and prohibit network
client construction. The new tests also enter the Windows evaluation profile;
short parametrization IDs avoid the Windows environment-variable limit.

The next question is **transfer**, not another replay until a preferred policy
wins: freeze this guard, then evaluate genuinely new motifs/representations with
the same evidence and tool access for both arms, retaining raw/reject/guard and
resolver-only comparators. Separate logical correctness from parser/authority
errors, and measure checker resources. Any new paid call still needs its own
payload, destination and cost approval. This development repair does not admit a
mechanism, reopen M4/M5/P5, or authorize U2/U5/S2.

### Frozen proof-aware transfer on new motifs and formats

The transfer runner tests a **fixed** guard on a prospectively sealed, authored
development frame. It does not modify the earlier raw pilot or cached repair.
The guard's byte identity is pinned before preparation; an edit blocks both
execution and replay. No protected M4/M5/target-binding prediction is used.

There are **24 new motifs**: eight identified, eight ambiguous and eight
conflicting. Each has two model-visible encodings, a list of record objects and
a column/row table, giving 48 dependent views per policy and at most 96 calls.
The formats have the same records, order, scope, authority challenges and
semantics. The provider receives the selected encoding unchanged; decoding
equivalence is checked offline, not used to erase the format manipulation.
Both policies get the same format instructions and evidence, model, schema,
sampling and tool access. Execution interleaves reference states and balances
first-policy order within format and first-format order within state.

Novelty is tested against the old frame under all 16 renamings of the four
typed binary domains, preserving requested/loaded roles. IDs, record order,
reports, other scopes and duplicate constraints cannot create novelty. The
new frame has no old constraint signature or nonconflicting compatible-world
set overlap. All conflicts have an empty world set, so that set alone cannot
establish novelty: eight distinct inclusion-minimal conflict-core topologies
are checked separately, with no old conflict-core overlap. Identified proof
cores are also checked. These cores are not minimum-cardinality proofs.

The new frame includes direct endpoint measurements, already declared in the
old grammar but unused in its authored corpus. It therefore tests new primitive
exposure **and** new compositions, not a pure compound split with matched atom
distributions, a new real-source replication, or unstructured log parsing.
[CFQ](https://arxiv.org/abs/1912.09713) motivates separating atomic and compound
coverage; [Shaw et al.](https://aclanthology.org/2021.acl-long.75/) distinguish
compositional generalization from natural-language variation. Our records/table
contrast is narrower than either broad natural-language generalization claim.
[CheckList](https://aclanthology.org/2020.acl-main.442/) motivates testing named
behaviors and invariances rather than treating aggregate accuracy as sufficient.
[SATQuest](https://aclanthology.org/2026.acl-long.96/) already combines verifiable
logical tasks with instance, task and format dimensions. Verifier-backed
cross-format evaluation is therefore prior art, not our novelty claim.

The primary system endpoint is within-arm `proof_guarded` minus `raw` justified
action success over **all 48 planned views**. Raw A4 versus A3-derived safety,
status correctness, proof, useful-query proposal and coverage are reported
separately. The same cached output is assessed in raw, action-canonicalized,
reject-only and proof-guarded regimes; these are not four independent model
experiments. Visible-resolver-only, always-abstain and symptom-only baselines
remain visible. Report transitions at the 24 authored-motif unit, joint success
in both formats, format discordance, provider resources and measured local
codec/guard duration. Retained duration measurements are validated and summed
on replay, not claimed to have been remeasured identically.

An important limitation of the frozen guard is explicit: a valid simple
abstention on ambiguous evidence remains an abstention, whereas an invalid
proposal can trigger a solver-generated useful query. Hence assisted query
success need not increase monotonically with raw proposal quality. Report
preserved safe abstentions, raw boundedness, risk with coverage, and success
origin (`model`, proof reconstruction or resolver actions). Do not rank prompts
from guarded action success alone. A useful query is a certified proposal under
the finite costs 1/1/2; the pilot does not execute that measurement or observe a
subsequently resolved status. Solver correctness remains conditional on the
typed attested facts and fixed finite model, not real-log authentication.

Zero guarded commitment violations are partly enforced by the checker, not
independent evidence of improved model reasoning. The format `same_decision`
diagnostic requires exact Decisions including citation order; it is not semantic
equivalence of proof sets. Joint justified action and discordance are reported
separately so an alternative valid proof is not mistaken for task failure.

`scripts/proof_aware_lineage_transfer.py` provides `prepare`, `preflight`,
`execute` and `verify`. A new private directory stores only three preparation
files: plan, cases and combined audit. Execution adds an immutable lease,
private responses, analysis and receipt. Replay verifies the earlier pilot and
cached guard aggregate without modifying them. Offline complete SDK-interface
capture checks all 96 actual argument sets; it is not a live HTTP test.

Only after exact owner authorization, execution uses `gpt-4.1-2025-04-14` at
`https://api.openai.com/v1/chat/completions`, zero retries and serial calls.
Worst-case frozen-rate reservation is **$2.359296**, ceiling **$2.50**; this is
not the expected bill. Payloads contain common typed-task/policy/format
instructions, constructed visible records or rows, and the decision schema.
Source rows, human labels, paths, hashes, reference states, oracle worlds and
case/motif metadata are withheld; `store=false` is not a promise of zero
provider retention. Three consecutive technical/schema failures stop the run;
semantic errors and missing calls retain planned denominators. No automatic
calls, prompt changes, guard tuning or mechanism admission follow the result.
The frame is finite authored development, with dependent format views and no
population confidence interval or new independent-source count.

### Offline transfer diagnostics

`scripts/analyze_proof_aware_lineage_transfer.py` replays a verified transfer
without provider calls. It separates mutually exclusive state/action outcomes
from overlapping reasoning, citation and action-contract flags. Identified
refusal and safe ambiguous abstention have different meanings; neither is a
technical failure. It records whether a successful guarded action came from
the model, proof reconstruction, resolver conflict handling or resolver query
selection. Assisted success must not be attributed entirely to model reasoning.

The records/table comparison separates semantic status/basis/action differences,
citation membership and citation-order-only changes. A single completion per
format cannot isolate a causal representation effect from model variability.
The diagnostic also checks a case-only counterfactual for the secondary
symptom-only baseline. A case-sensitive phrase lookup can create a spurious
abstention ceiling; such a finding belongs in a separate post-hoc diagnostic,
not a silent rewrite of the frozen baseline or primary analysis.

Diagnostics may produce an aggregate-only file outside the immutable run. They
verify source integrity before and after replay and never export raw responses,
source contexts or private paths. The frozen guard, reference and runner remain
unchanged.

### Forward method: source-faithful admission before resolution

The next research question is whether evidence extraction/admission or actual
evidence acquisition adds value beyond strong deterministic baselines. This is
a **design direction**, not an implemented semantic extractor or a performance
claim. More prompts on fully typed facts do not by themselves establish useful
LLM headroom.

Keep six boundaries separate: intervention/reference validity, visible source
bytes, extraction/admission, finite-world reasoning and certificates, executed
actions with newly observed evidence, and rendered claim warrant. The existing
eight-variable/256-world model does not establish temporal, shared-variable or
open-world correctness. A binding mismatch is not proof of loss causation;
`no_binding_fault` is not global pipeline health.

Source admission must check producer/request/attempt/time scope, permitted
authority, source-span/field meaning and omission/completeness limits. The LLM
can propose interpretations and spans, but cannot authenticate its own evidence
or promote a manifest/report to a runtime consumed-buffer witness. Hash and span
resolution do not establish semantic entailment or upstream authenticity.
Check the full admitted context for consistency before validating a cited subset.
An inclusion-minimal proof/core is not necessarily minimum-cardinality.

For multiple admissible interpretations, preserve the union of feasible worlds
instead of intersecting alternatives into false certainty. An interpretation
with no feasible worlds must remain an explicit conflict possibility, not be
discarded when forming that union. Commit only if all admissible readings are
consistent and imply the same conclusion, under a justified true-reading
coverage assumption. Mixed consistent/conflicting readings require clarification;
all inconsistent readings support only model-relative conflict. An LLM list of
alternatives is not an exhaustive-coverage proof: retain an unknown branch or
declare the producer/schema scope. These are proposed conservative semantics,
not guarantees supplied by the current typed guard.

Use a producer/schema-adapted deterministic parser and an LLM extractor with the
**same raw evidence, allowed metadata, tools, budget and fixed resolver**. A
gold-fact resolver has privileged facts and is a reference ceiling, not the
matched comparator. Report source/graph fidelity, omissions, false authority,
downstream status/certificate warrant, failures, latency and cost separately.
If the adapted parser solves the task, keep that solution; do not weaken it or
manufacture natural-language difficulty merely to require an LLM.

Acquisition is optional. If ambiguity can be reduced by a permitted measurement,
execute the read-only tool, retain unavailable/error results, record newly
observed source evidence and reassess the decision. Compare with an exact or
greedy cost-aware deterministic planner under the same initial evidence, tool
availability, budget and stop rules. A certified query proposal is not observed
post-query utility. No repair/write authority follows from this research plan.

Select the method on exposed development, then fix a separate producer/data
frame before new model outcomes. Distinguish untouched unknown-schema validation
from documentation-informed transfer: the latter adapts a strong parser and
shared source semantics using disjoint offline fixtures, not evaluation outcomes.
The current SQLite study tests that narrower transfer claim. Formats, rows,
calls and extra estimators do not increase independent
source count. Extraction-only validation need not require an active agent or two
admitted causal mechanisms; a causal contrastive study still needs its own
mechanism-admission contract. New paid calls require separate exact approval.

This architecture builds on semantic parsing plus theorem proving in
[LINC (EMNLP 2023)](https://aclanthology.org/2023.emnlp-main.313/), interactive
fragment/alternative formalization in
[nl2spec (CAV 2023)](https://cs.stanford.edu/~trippel/pubs/cosler_CAV23.pdf),
evidence compilation and verification in
[EviGuard](https://doi.org/10.3390/app16188925), and separated extraction/reasoning
in the [EviRCA preprint](https://arxiv.org/html/2609.19825v1).
[Sequential model-based diagnosis](https://www.ijcai.org/Abstract/16/181) already
studies measurement selection. Candidate novelty therefore concerns controlled
ML binding errors, source-faithful admission, attribution and executed acquisition
under matched controls—not the first neurosymbolic, proof-aware or active
diagnosis method. Incremental value and independent transfer remain to be shown.

### Retained development source admission and headroom

The development source audit implements an offline, explicit-allowlist audit of three
already-exposed local M4 development stores. It does not search the entire
workspace, open prospective/final studies, fit or deserialize a model, or call
a provider. The inventory distinguishes five legacy aggregate path records
from 36 consumed-buffer loader events, three container layouts from two endpoint
parser contracts, and dependent records from one producer family/source cluster.
These are not independent incidents or naturally sampled pipeline failures.

The parser receives a **blinded JSON field projection**, not native free-text
logs: declared artifact hash, actually loaded hash, and a reported-manifest
distractor when present. Control names, gold labels, metrics, dose/iteration
recipes and native pointers that reveal the control are withheld. Exact source
field pointers are restored on the audit branch after parsing. The producer and
receipt-local control slot are caller-bound, not inferred from document prose.
Neither native request/attempt IDs nor timestamps exist in these records;
solver aliases do not manufacture them. The dataset's license does not establish
permission to publish the derived local traces.

The strong baseline is a producer/schema-adapted deterministic parser followed
by the existing finite resolver. It admits declared and actual endpoints, not
report text as proof of what was loaded. Missing endpoints preserve ambiguity;
malformed recognized fields or unsupported producer/schema remain unresolved;
same-scope contradictions are preserved, and unrelated scope supplies no facts.
Duplicate keys and nonfinite JSON numbers are rejected. More than two artifact
identities stay outside this adapter's declared binary domain. Full admitted
context consistency is checked, not only the subset convenient for a conclusion.

The reference uses a **separate fixed control schedule and retained artifact-byte
hashes**, not the parser's endpoint comparison or an LLM answer. Historical code,
receipt/plan bindings and the exact control census are checked before replay;
source hashes are compared before and after. This is independent replay logic
within a trusted local producer, not independent host/request-service
attestation. An adversary controlling the caller can forge producer metadata;
dropping an entire document cannot be detected from the remaining bytes alone.
The native census check limits that risk for this retained corpus, not for all
runtime evidence. Correct resolution remains conditional on admitted facts and
the finite model; binding status does not prove loss causation or overall health.

Synthetic falsifiers cover missing endpoints, conflicting documents, invalid
authority/schema, out-of-scope evidence, reported-manifest and prose distractors,
duplicate/nonfinite values, alias/order changes, and 81 combinations of two
documents with missing/A/B endpoints. These are behavioral tests, not extra
empirical source samples. A swapped-fact regression verifies that a correct
fault/no-fault status alone cannot hide extraction of the wrong endpoint facts.
Both exact-fact fidelity and resolution must pass before declaring no measured
gap. The audit is descriptive and does not supply population intervals.

Run the read-only audit with:

```sh
PYTHONPATH=src python scripts/audit_source_evidence_headroom.py \
  --root . --memory-root ../memory
```

Optional `--output` creates one new private JSON directly in the memory root;
it cannot overwrite an existing file, enter a study directory, or publish raw
traces in the repository. The source-bound result stays private. A
`no_demonstrated_semantic_headroom` disposition means **keep the deterministic
adapter for this structured scope**. It does not mean LLMs cannot help elsewhere.
Do not launch an LLM comparison on this corpus merely to obtain model calls: first identify
authorized producer evidence with a useful semantic/admission question and an
independently checkable reference. Do not invent AI paraphrases or weaken the
parser to manufacture that gap.

This boundary follows established ideas, not a new parsing-plus-proving
architecture: [LINC](https://aclanthology.org/2023.emnlp-main.313/) separates
semantic translation from proving; [EviRCA](https://arxiv.org/html/2609.19825v1)
already uses deterministic extraction and declarative adapters. The
[W3C PROV data model](https://www.w3.org/TR/prov-dm/) motivates explicit
entity/activity/agent provenance, but does not authenticate these local records.
No novelty or LLM superiority follows from implementing the baseline.

### Offline extraction assessment and executed acquisition replay

The next implementation reuses the producer adapter and the unchanged finite
resolver. `source_evidence_extraction.py` assesses candidate fact proposals on
the **same document bytes and caller-bound metadata** as the deterministic
comparator. It separates byte/field grounding, semantic role, omitted eligible
facts and downstream resolution. A grounded manifest digest is not an actual
loaded-buffer witness. Swapped endpoint roles cannot pass merely because the
fault status remains the same. Omitting a contrary same-scope witness cannot
obtain a certificate over the convenient remaining subset.

For these known schemas, only an exact complete eligible fact frame is admitted.
This conservative engineering contract delegates semantic warrant to the strong
producer parser; it is not independent natural-language reference annotation or
measured LLM extraction. Completeness means the supplied eligible document frame,
not all runtime logs. Requiring exact facts may reject harmless omissions too.
Unknown producer/schema, malformed recognized evidence or an unverified caller
cannot be repaired by the extractor's declaration of trust.

Alternative readings retain ambiguity, conflict and unknown coverage explicitly.
One consistent singleton plus one conflicting reading requires reconciliation;
the empty conflicting world set must not disappear in a union. No commitment is
permitted with unverified true-reading coverage. Listed-reading statuses are
reported separately from an exhaustive compatible answer. Conditional soundness
requires the true reading to be covered, the true world to be represented and
satisfy that reading, and all admissible readings to be consistent and entail the
same status. The code does not prove these upstream premises. This applies the
possible-world/uncertainty boundary, not a new universal theorem.

`source_evidence_acquisition.py` adds one-attempt, read-only endpoint acquisition
from the two already-exposed native runtime containers. The caller pins store,
compiled JSON pointer, container identity and receipt-local scope. Initial and
new evidence must share the same record/snapshot capability. Audit-to-read drift,
cross-record joins and symlink paths fail rather than silently changing the
source. The query returns only permitted endpoint fields, with native pointer,
container and projection hashes recorded separately. Control labels, metrics,
recipes and raw container contents never enter the planner or resolver. Actual
read bytes are retained even when hash/JSON validation fails. Unavailable/read
errors remain charged outcomes, never negative endpoint facts; no retries,
provider calls, model loads, repair commands or write capabilities are present.

The exact one-step planner uses the initial evidence, available query catalogue
and the existing abstract endpoint costs **1/1/2**. A combined query is a valid
fallback when the required single-endpoint capability is unavailable. Re-reading
a known loaded endpoint can have zero resolution gain; acquiring the missing
intended endpoint can distinguish an A/B fault from a legitimate B/B load. Full
context conflicts remain conflicts after a measurement. The reported cost ratio
is resolution gain per abstract endpoint-cost unit, not USD or physical IO.
Single- and both-endpoint reads consume the same underlying container bytes;
there is no claim of native-file-cost optimality or sophisticated planner value.

The offline native replay has **36 runtime records × 4 authored views = 144
dependent view slots**, excluding the five legacy aggregate records. Thirty-six
full views resolve without queries. The 108 masked views are initially ambiguous;
108 executed scoped reads resolve all 108 and match the independently replayed
control/artifact reference. All 144 post-query statuses match. Queries consume
144 abstract units and read 444,441,402 native-container bytes in total. One
producer and one old source cluster remain one, not 144 independent observations.
These masks are authored visibility interventions on fully retained records,
**not naturally missing production logs**, and no LLM planner/extractor was run.
Source trees are checked before and after; historical outputs remain unchanged.

Run the bounded replay locally:

```sh
PYTHONPATH=src python scripts/replay_source_evidence_acquisition.py \
  --root . --memory-root ../memory
```

Stdout contains aggregate results only. Optional `--output` creates one new
private JSON directly in the memory root, with source/code identities and scoped
row ledgers; it cannot overwrite an existing file or enter the public repository.
The same source/bytes can be replayed without authorizing a provider operation.

### Independent producer/schema transfer: offline implementation

The selected development cited-fact interface now has an implemented SQLite
transfer runner. It uses native SELECT traces, a nested row-declaration grammar,
caller pins and exact returned-BLOB witnesses; it does not reuse Joblib traces or
evaluation artifact values. The deterministic parser remains the matched
within-scope comparator and both arms share the unchanged finite resolver.
The complete producer contract, reference, lifecycle and paid boundary are in
[the existing extraction document](native-cache-evidence-extraction.md#independent-sqlite-producer-and-nested-schema-transfer).
**Offline readiness is not a live LLM transfer result.**

An eligible source needs authorized authentic bytes, independently justified
producer and operational scope, a real semantic admission question, and reference
facts/roles traceable to a producer/runtime witness independent of the candidate
extractor. For this study, the SQLite adapter and shared semantics are developed
from official documentation and disjoint fixture bytes before evaluation bytes
and new model outputs. This explicitly narrows the earlier untouched-schema
ambition to documentation-informed transfer with adaptation; it is not a claim
that no SQLite documentation influenced the adapter. More estimator cells,
JSON layouts, authored masks or LLM paraphrases do not provide independent
producer evidence.

Before new outcomes, bind source identities and units, development/final split,
selected variant and code, strongest matched comparator, permitted input/tools,
resource limits, failure handling, metrics/precision and inference rule. Primary
engineering endpoints are exact fact-role fidelity and unwarranted downstream
commitment; omissions, conflict loss, syntax, failure and costs are reported
separately. Gold-fact resolution is a privileged ceiling, not a matched arm.
Inference groups by independent producer/source, not claim/view/call count. With
only one source the result remains descriptive. Acquisition claims additionally
require observed tool returns; proposed queries are not post-query utility.
The selected frame is a finite descriptive census: five controls with two views,
ten dependent execution slots and six distinct complete provider inputs from one
additional controlled producer. No population interval or non-inferiority margin
is estimated from these slots. The exact cited-frame comparison, authority and
omission diagnostics, failure policy and resource ceilings are fixed before
model outputs. A parser ceiling can support a falsifiable fidelity/false-authority
question, not superiority. Paid execution still requires separate payload,
destination and cost approval; no protected source or prior study is reopened.

The method follows the formalization/prover limitation in
[LINC](https://aclanthology.org/2023.emnlp-main.313.pdf), fragment interpretation in
[nl2spec](https://cs.stanford.edu/~trippel/pubs/cosler_CAV23.pdf), and observed
measurement updates with an explicitly correct oracle in
[sequential model-based fault localization](https://www.ijcai.org/Proceedings/16/Papers/181.pdf).
The current contribution is a checked source/admission/acquisition boundary.
Incremental LLM value, authentic semantic-source transfer, causal admission and
population reliability remain unmeasured.
