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

## Reader precision contract

The evidence builder supports the historical twelve-decimal view and an
explicit six-decimal view. It rounds each source loss **once**, then derives
the displayed change from the displayed reference and observed losses. It
does not round through twelve decimals before producing six: double rounding
can move a value to the other side of a rounding boundary. Source metrics and
the historical artifacts are left untouched.

`serialize_development_evidence_view` requires an explicit precision and
condition. It serializes only that view, not the enclosing observation or
private study ledger. Six-decimal payloads declare their precision; the
historical default twelve-decimal projection remains unchanged. Matching now
compares the actual serialized `missing_key` and `full` bytes. A raw gap below
the half-unit tolerance alone is insufficient when the values straddle a
rounding boundary. This is an observation-channel contract for development,
not a guarantee that all deployments or other mechanism pairs are leak-free.

The forward reader audit pins both the score-mapping and independently replayed
target-binding summaries by byte hash. It checks the four zero-dose controls,
all twelve positive-dose correction and lineage witnesses, and every sibling
view. A dedicated helper fixes the **development symptom reader view** at six
decimals. A development-only wrapper now passes exactly that projection into
the gateway's real model-visible context type; an offline fake adapter and a
mock OpenAI SDK client capture the complete model messages supplied to the SDK. The item
ID, title and kind are constant,
while the item and context hashes are derived only from the selected visible
projection, never from the private source witness or case ID. The mock client
uses different case IDs and still sends identical model messages. This closes
the tested envelope-level shortcut: the `missing_key` pair stays identical after the
gateway adds IDs and hashes, while the `full` witness remains different.
The original four-cell source replay still yields the exact historical
summary byte hash `aa891dcec952caefb54b1ccb11470fc22955d738b3e3b49ee961cfe6d4a31d50`.
The twelve-decimal path remains an audit-only counterexample. Across the
balanced finite set of twelve mapping/target pairs, the optimal lookup rule
over **only the serialized `missing_key` payload** has 50% accuracy because
each pair supplies identical bytes with opposite causes. The corresponding
finite-corpus lookup accuracy under `full`, `noisy`, and `misleading` is 100%
because the canonical view hashes separate the causes across this corpus;
the source replay separately verifies paired byte equality for `missing_key`.
All twelve `missing_key`
pairs differ at twelve decimals. A trained shortcut classifier cannot improve
the six-decimal finite-pair bound, but this does **not** prove 50% accuracy on
unseen families, different cause frequencies, raw private summaries, or a
reader that receives other fields. The gateway tests make no network call and
do not freeze an M5 prompt. Zero-dose controls are independently checked by
the pinned replay and now produce reader payloads through a separate healthy
constructor. It reports the class order actually used, not the unused reversed
metadata carried by the zero-dose injector. The mapping-fault constructor
still rejects unchanged scores. The reader-specific validation also rejects
otherwise valid generic contexts with source hashes, cause-bearing IDs/titles,
raw-precision metrics, changed-row counts or extra catalog fields. All four
sibling views pass through the mock client with the same prompt, schema and
sampling settings. Neither this work nor the
replay establishes leakage resistance for a future multi-turn catalog, a new
family, an unbalanced cause prior, or a protected run. No mechanism has been
admitted by this development audit.

This exact paired-payload check is stronger than a non-significant classifier
two-sample test for the *observed pairs*: failure of a finite learned probe
would not prove indistinguishability. It is also narrower. Equal-prior binary
testing is limited by the information in the observation channel, while
adaptive selection on development data cannot certify a new holdout. See
[Duchi's statistical testing notes](https://web.stanford.edu/class/stats311/lecture-notes.pdf),
[Lopez-Paz and Oquab's classifier two-sample test](https://arxiv.org/abs/1610.06545),
and [Dwork et al. on adaptive holdout reuse](https://arxiv.org/abs/1506.02629).

The technical basis is the classifier's documented score-column order and
the metric's label convention ([classifier API](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LogisticRegression.html),
[log-loss API](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.log_loss.html)).
Neither source establishes mechanism admission or the validity of a future
protected experiment.

## Prospective target-row binding validation on new sources

The separate `target_binding_prospective.py` runner freezes two previously
unused source domains: [UCI MAGIC Gamma Telescope](https://archive.ics.uci.edu/dataset/159/magic%2Bgamma%2Btelescope)
and [UCI Spambase](https://archive.ics.uci.edu/dataset/94/spambase). The checked
protocol pins both archive and member bytes, source label encodings and
CC-BY-4.0 provenance. These are **two source clusters**, evaluated with the two
existing fixed estimators (four cells), not four independent datasets. MAGIC
is Monte-Carlo-generated and its hadrons are underrepresented; Spambase's
labels and predictors reflect its historical mail-collection context. Source
hashes prove identity/lineage, not semantic truth of the labels or
representativeness of current deployments.

`prepare` parses numeric source rows and computes membership/class counts;
it fits nothing and produces no final predictions or metrics. It is therefore
**prediction/metric-blind**, not physically blind to source labels. Exact
feature duplicates share a feature-only hash group, with fixed 60/20/20 hash
intervals for train/calibration/final. Labels and fitted scores cannot change
membership. No missing/nonfinite row is silently removed or imputed: a parser
failure blocks preparation. A single-class/empty partition remains an explicit
ineligible cell in the fixed census. See the
[scikit-learn leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html).

`execute` requires the exact plan byte hash and acquires one atomic lease before
model fitting. The loaded package must be the checkout whose sources are
hashed. Standardization and model fitting use train only; logit calibration
uses the separate calibration partition, with fitting clip `1e-12`, 200
iterations and tolerance `1e-9`. Calibration application retains the existing
runtime clip `1e-15`. Final rows are not used to choose model, hyperparameters,
calibration, seed or dose. Code, runtime versions, source bytes, membership,
precision and failure policy are all bound before execution.

For every cell, retain the healthy/unused-metadata sham, zero-dose target
control and fixed one-of-twenty-shard cyclic target-donor intervention. Cyclic
selection does not consult targets/scores; same-label donors and flat or
negative effects are retained. Verify source model/class order, both score
columns, target ledger and correction independently before making a causal
interpretation. Actual target-source restoration must recover healthy loss.

The existing deterministic greedy matcher is then used **once as a
predeclared adversarial construction**, not as a natural-corruption sample.
The mapping dose stays at one shard; opposite-label target swaps preserve
class counts and cannot exceed mapping's affected-row footprint. Match only
at the frozen six-decimal channel, with a raw gap at most `5e-7` **and** exact
complete `missing_key` reader-context equality; `full` must retain a differing
witness. Failure to match, a nonpositive mapping effect, insufficient footprint,
model/calibration failure or interruption never replaces a source, increases
dose/tolerance, or drops a cell. Correction of **target bindings** is evaluated
separately from the reader's "correct score-column decode" field: correcting
columns with wrong targets still leaves the target-rival loss faulty.

The immutable receipt includes all four dispositions, artifact hashes and
match coverage. The offline `verify` operation replays source membership,
stored score/calibration bindings, independent donor joins, runtime metrics
and complete reader payloads, without refitting a model, repeating matching,
or making a provider call. Receipt verification establishes consistency of
the local evidence chain, not authenticity against someone able to rewrite
every file and seal. A verified failure receipt is not a mechanism PASS.

The receipt's mechanical disposition is `incomplete` if any cell is not
structurally verified or the execution fails, `rejected_no_visible_cyclic_effect`
if all four verify but none shows a target-changing cyclic effect at the
declared precision, and `assumption_limited` otherwise. That last status is
**not admission** and does not require all four adversarial pairs to match.
Its claim remains bounded to the observed cells, original-label assumption
and two source clusters; match coverage remains an independent result.

Successful paired ambiguity is conditional on the matched subset. Report
match coverage over **all four cells** separately from structural/correction
checks, and keep both source-cluster identities when discussing generalization.
This is a prospective algorithmic positive-control test, not LLM diagnostic
accuracy, natural error frequency, unrestricted zero-leakage, or automatic
admission of either mechanism. It does not open a historical holdout or change
P2R/P5 results. The development-to-prospective distinction follows
[Dwork et al.'s adaptive holdout analysis](https://arxiv.org/abs/1506.02629).
No paid API is needed for this validation; any later LLM reader experiment is
a separate question with its own final observation contract.

Use one private directory outside the repository, with the two pinned archives
under `sources/`. Run `prepare`, then inspect its exact plan and authorize
`execute --confirm-plan-sha256 ...`; finally run `verify`. Do not publish the
raw archives, fitted models, row-target ledgers or reader traces. Preparation
and synthetic tests alone must not be reported as completed prospective results.
