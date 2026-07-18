# Diagnosis evaluation protocol

This document defines how Aletheia Lab evaluates a matched diagnosis pilot. The
protocol deliberately separates four questions that can disagree:

1. Did the diagnosis identify the controlled cause?
2. Do all citations resolve to evidence the diagnoser was allowed to see?
3. Do the cited evidence roles support the stated claim strength?
4. Did the diagnosis qualify or abstain as required by the evidence condition?

No single aggregate score replaces these four outputs.

## Inputs and trust boundaries

The evaluator accepts a pilot store, the immutable evidence store, and the
source benchmark cases. It first recomputes file hashes, parses every persisted
raw and structured response, verifies matched requests against diagnosis-safe
evidence projections, and binds each opaque context to evaluator-only condition
and ground-truth metadata.

These hashes establish internal integrity and reproducible source binding, not
third-party authenticity. A party with permission to rewrite an entire local
store and all of its manifests can fabricate a new internally consistent store;
provider-signed receipts or an external transparency log would be required to
defend against that stronger threat model.

External smoke outputs additionally require the original OpenAI config and
preflight artifact. The evaluator reruns external authorization validation; the
presence of an authorization marker without those inputs fails closed.

## Per-diagnosis layers

### Cause correctness

The first implementation is a locked, deterministic P1 lexical baseline. For
eligible failures it tests the controlled distribution-shift concept and the
affected feature against hidden ground truth. For stable or improvement controls
it rejects promotion to a bounded failure cause. Results are `correct`,
`partial`, `incorrect`, `not_asserted`, or `not_evaluable`.

Every row is marked as requiring human semantic review. This baseline is useful
for reproducible screening and regression tests; it is not claimed to understand
arbitrary paraphrases or to replace a blinded human judgment.

### Citation validity

All cited identifiers must resolve to the diagnosis-visible projection. A
non-abstaining diagnosis must cite at least one supporting item. Evaluator-only
and unknown identifiers are invalid even if their text would support the answer.

### Evidence-role support

The scorer uses evidence roles rather than filenames or condition labels:

- observations require cited observable evidence;
- comparisons require at least one cited comparison role;
- bounded hypotheses require all diagnosis-visible decisive roles;
- when decisive roles were intentionally withheld, a bounded hypothesis is only
  fully supported when visible roles are covered and missing evidence is
  explicitly requested.

Support is reported as `fully_supported`, `partially_supported`, `unsupported`,
or `not_evaluable`. The current diagnosis schema contains one root hypothesis,
so this is root-claim support rather than a sentence-level entailment judgment.

### Abstention and overclaim

The behavior gate flags strong causal language, an unqualified claim under
missing decisive evidence, blanket abstention when bounded evidence is present,
use of the neutral secondary comparison as causal support, and promotion of a
stable or improvement control to a failure cause. A row is evidence-aligned only
when citations are valid, role support is full, and the behavior gate passes.

Correctness and evidence alignment form a two-axis divergence label. This makes
correct-but-unsupported guesses visible rather than counting them as faithful.

## Paired-condition sensitivity

For each injection family and prompt variant, the evaluator compares the full,
missing-evidence, and noisy siblings:

- missing-evidence sensitivity is satisfied by a strict reduction in claim
  strength; when claim strength is unchanged, it instead requires an observable
  qualification: abstention, lower confidence, or a larger missing-evidence
  request; a stronger missing-evidence claim always fails;
- noisy robustness requires the same claim-strength level and correctness label
  as the full sibling, without selecting the secondary comparison as support.

Incomplete families are reported explicitly. An eight-request smoke run can test
the full-versus-missing comparison for two families, but it cannot establish the
complete three-condition result; the full 30-request run is required for that.

## Reporting boundary

Mock-adapter results validate infrastructure only. External smoke results are a
small operational check, not a final performance claim. Final empirical claims
require the complete matched matrix, an attested human semantic review, reported
unresolved runs, and uncertainty appropriate to the sample size.
