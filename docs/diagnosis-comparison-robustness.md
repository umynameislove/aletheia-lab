# Measurement validity of cached diagnosis comparisons

The frozen outcome is **evidence warrant in the supplied context**, not hidden
causal correctness. A claim can be well supported by the visible evidence without
identifying the true intervention site. Controlled ground-truth validation and
human warrant judgement protect different links in this measurement chain.

## Keep the estimand and measurement unit explicit

The preserved loss averages atomic claim harm within an output, conditions within
a family, then paired family contrasts. Positive B1−A3 favors A3. Technical
failures, abstentions and zero-claim outputs keep their registered penalties.
They are not silently discarded or assigned human labels for unobserved claims.

For a scoreable core claim i, with k_i claims in its output and F families,

    human contrast − automatic contrast
      = Σ_i c_i [g_w(human_i) − g_w(automatic_i)],
    c_i = +1/(3 F k_i) for B1, −1/(3 F k_i) for A3.

Other claims have coefficient zero. Here full support has harm 0, partial
support has harm w, and unsupported/contradicted has harm 1. The registered
primary w=0 and sensitivity w=0.5 stay unchanged. Global exact-label agreement
does not determine this signed, weighted correction: the same confusion counts
can yield opposite contrasts when errors occur in different arms or outputs.

## Existing references serve different designs

| Reference | Estimator or valid use | What it does not establish |
| --- | --- | --- |
| Stratified probability claim audit | Original design-weighted correction | Complete paired-output coverage or causal truth |
| Enriched claims | Known reference values for conditional regions | Probability-sample prevalence |
| Whole-family pilot | Fixed automatic census contrast plus sampled family residual mean | Infallible adjudication or a confirmatory small-sample interval |
| Historical reference union | Conditional per-claim label-domain region, retaining both conflicting labels | A new combined probability sample or point estimator |

With n families sampled without replacement from F, the difference estimator is
`automatic census mean + sampled mean(human − automatic)`; its estimated design
variance is `(1 − n/F) s_residual²/n`. This is established survey/PPI methodology,
not a new estimator. [Prediction-Powered Inference, Appendix B](https://arxiv.org/html/2301.09633v4)
requires an appropriate correction interval; a pilot planning normal interval
does not acquire a confirmatory guarantee simply by using the same formula.

## Secondary analyses on the unchanged cache

`evaluation/diagnosis_comparison_robustness.py` implements:

- Affine partial-weight sensitivity over [0,1], without choosing a replacement
  endpoint or interpolating historical bootstrap intervals.
- Sharp extrema under a declared product label domain: unseen claims may have
  any rubric label; overlaps allow all returned labels. Single reference values
  are conditional assumptions, not authenticated truth. These are feasible
  regions, not confidence intervals.
- Original two-rater pilot scenarios, distinct from the final adjudicated point
  and from family-sampling uncertainty. No new adjudication occurs.
- Minimum unit-cost automatic-label perturbations for fixed output sizes,
  statuses and weights. Sorted attainable gains give the exact minimum under
  this hypothetical adversary; they do not estimate error rates or choose
  an audit sample.
- A supplementary `any harmful claim` output indicator. Adding supported claims
  can dilute mean harm without removing a harmful claim. Mean fraction and
  any-harm incidence measure different constructs; neither automatically
  replaces the frozen metric or establishes diagnosis accuracy.

## Executed bounded result

The recovery contains 32 families, 1,024 requests and 3,181 emitted claims. The
core B1/A3 contrast has 192 slots, 168 scoreable outputs and 636 scored claims.
The original failure-dominated run remains separate.

The automatic primary contrast is −0.007465278; the existing family-pilot
human-reference point is +0.000868056. At w=0.5 they are −0.064236111 and
−0.056597222. None of these points proves superiority or equivalence. The final
pilot point curve crosses zero at w≈0.007553 and never reaches +0.05 over [0,1].

The two historical references cover 198 unique core claims; 438 remain unseen.
Their conditional primary region is [−0.605729, +0.603819], so the sign is not
identified under this domain without further measurement assumptions. Three
overlap conflicts are all full/partial; primary harm does not change, while
the half-weight sensitivity does. References are not pooled as a probability
sample. Under the hypothetical all-core label adversary, 3 changes can reverse
the primary automatic sign, and 17 can reach +0.05; these are not observed errors.

The supplementary automatic any-harm contrast is 0 rather than the mean-harm
contrast −0.007465278. B1 has 4 harmful scoreable outputs and 1 technical failure;
A3 has 3 and 2 respectively. Each has 5 positive-loss slots out of 96 under this
supplementary rule. Status handling is unchanged, and no equivalence follows.

## Reproduction and privacy

With the repository dependencies and `PYTHONPATH=src`, run from the repository
root (replace placeholders with already-authorized local files):

```text
python scripts/diagnosis_comparison_robustness.py \
  --memory-root <private-existing-memory-directory> \
  --first-submission <locked-first-pilot-submission.json> \
  --second-submission <locked-second-pilot-submission.json> \
  --output <private-directory>/<new-summary.json>
```

The bridge reuses the historical frame, audit and pilot analyzers, checks receipt
byte identities, binds selected outputs and cached losses to the source frame,
recomputes original and recovery separately, and verifies input bytes afterward.
Duplicate JSON fields, changed source/selection/labels, incomplete outputs and
invalid weights fail closed. Hashes record identity, not genuine cognitive
blindness or the completeness of a human rating process.

It writes a new aggregate-only file outside the repository, using exclusive
creation and restrictive permissions where supported. It cannot overwrite an
existing file. No source IDs, claim text, evidence, rationales, rater identity or
sampling keys enter the aggregate. The actual input labels are private and are
not distributed with the public tests. No provider call or native experiment is
performed; tests use synthetic data. The affected checks are registered in the
evaluation test profile.

## Boundary of the conclusion

This is an exploratory measurement audit and implementation, not new judge
calibration, validation of all 32 families by humans, or an estimator novelty
claim. Pooled confusion correction is not identified by the pilot's absent
human harmful classes; its transportability across arms, conditions and output
sizes is unproven. [Noisy LLM-as-a-Judge Evaluation, sections 2.2–2.4](https://arxiv.org/html/2601.05420v1)
compares classification-channel correction with residual-based inference under
different explicit assumptions; that literature does not supply missing labels
for this cache. Broader causal or deployment claims still need their own evidence.
