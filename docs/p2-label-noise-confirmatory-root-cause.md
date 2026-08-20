# Bank confirmatory replication root-cause audit

## Status and scope

This is an outcome-aware explanatory audit of the frozen Bank replication. It
does not amend the preregistration, rerun the confirmatory study, replace any
artifact, change any threshold, or reverse the registered decision.

The Bank replication remains failed. The study disposition remains
`primary_dataset_bounded_admission`; a cross-dataset claim remains prohibited.

The audit is bound to:

- protocol SHA-256
  `1a7340d0897fcbbde02bb0a3ffe0a50cccd1cebd695ebf2c293c6c260bb02d4e`;
- result-store SHA-256
  `7e46d0997bc5ad6807409a4aebea39c82c11216f2bdcadd5704724994117504c`;
- replication-outcome SHA-256
  `8f895bb9234fe851927340838f0fc5f86a1326f547bae93c56be65f887a196b7`.

The compact machine-readable findings are in
`configs/benchmark/provenance/p2_label_noise_confirmatory_v2_bank_root_cause_summary.json`.
The complete per-seed audit is reproduced from the frozen store by the command
at the end of this document and is intentionally not checked in as a second
copy of the large outcome.

## Question

The frozen `no_to_yes` Bank result improved log loss instead of degrading it.
The first question was therefore validity, not optimization:

1. Was the result caused by reversed label encoding, a reversed intervention,
   mutation-census error, leakage through `duration`, non-convergence, or an
   inference implementation error?
2. If those defects are absent, is the observed behavior consistent with a
   temporal class-prior mismatch between training and sealed test data?

The second explanation is evaluated as a supported explanation, not as proof
that class-prior shift is the only form of temporal distribution shift.

## Audit method

The audit performs the following checks without generating a new outcome:

1. Verify the complete result store against its frozen root hash.
2. Reload the byte-pinned Bank snapshot and archive and match the registered
   dataset receipt.
3. Rebuild the source-order temporal split and compare it exactly with the
   stored split.
4. Verify `no -> 0`, `yes -> 1`, `yes_to_no: 1 -> 0`, and
   `no_to_yes: 0 -> 1`.
5. Reproduce all 180 mutation artifacts from their registered cell and seed,
   retaining a mutation hash, count, and achieved rate for every seed.
6. Confirm that `duration` is declared excluded and absent from the loaded
   model frame.
7. Refit the clean model and all 180 corrupted models; compare all 181 complete
   probability vectors and model artifacts with the frozen values while
   capturing convergence warnings.
8. Recompute the registered two-way product-weight bootstrap, paired sign-flip
   tests, and Holm adjustment from frozen per-record losses and seed effects.
9. Measure temporal class prevalence, predicted positive rate, calibration
   intercept, and class-conditional log loss.

Any structural, mutation, model, convergence, control, or inference mismatch
dominates the outcome and forces `implementation_defect_detected`. Prior-shift
support is allowed only after every validity gate passes.

## Validity findings

| Check | Result |
|---|---:|
| Temporal split reproduced | exact |
| Target encoding | `no = 0`, `yes = 1` |
| Direction encoding | `yes_to_no = 1 -> 0`, `no_to_yes = 0 -> 1` |
| Mutation artifacts reproduced | 180 / 180 exact |
| Registered seed census | 30 seeds in each of 6 cells |
| `duration` excluded from model frame | yes |
| Probability/model vectors refit | 181 / 181 exact |
| Convergence warnings | 0 |
| Technical controls | all pass |
| Inference analysis hash | exact match |
| Implementation defects detected | 0 |

The stored and recomputed inference analyses both hash to
`7911433696c8c7d44ed5169f3a307740bd2fe794c8b364c79878fd0d45ddf9d9`.
These results rule out the audited implementation-defect classes as an
explanation of the Bank failure.

## Temporal prevalence and calibration

| Partition | Records | Positive | Positive prevalence |
|---|---:|---:|---:|
| Train | 24,712 | 1,188 | 4.8074% |
| Development | 8,237 | 912 | 11.0720% |
| Sealed test | 8,239 | 2,540 | 30.8290% |

The sealed-test prevalence is 26.02 percentage points above training. The clean
model predicts a mean positive probability of only 0.001066%, has a calibration
intercept of `+11.4011`, and has sealed-test log loss `4.06458`. Its positive
class log loss is `13.18427`, whereas its negative class log loss is
`0.00000956`. This is severe underprediction of the temporally later positive
class, not merely a small calibration deviation.

## Mutation reconciliation

Mutation count and achieved conditional rate are constant across all 30 seeds
within a cell; the selected records differ by seed and every selection hash is
reproduced exactly.

| Direction | Dose | Source count | Mutations/seed | Achieved rate | Mutated train positive rate | Test-prior gap after |
|---|---:|---:|---:|---:|---:|---:|
| yes-to-no | 10% | 1,188 | 119 | 10.0168% | 4.3258% | 26.5032 pp |
| yes-to-no | 20% | 1,188 | 238 | 20.0337% | 3.8443% | 26.9847 pp |
| yes-to-no | 30% | 1,188 | 356 | 29.9663% | 3.3668% | 27.4622 pp |
| no-to-yes | 10% | 23,524 | 2,352 | 9.9983% | 14.3250% | 16.5040 pp |
| no-to-yes | 20% | 23,524 | 4,705 | 20.0009% | 23.8467% | 6.9823 pp |
| no-to-yes | 30% | 23,524 | 7,057 | 29.9991% | 33.3644% | 2.5354 pp |

Thus `no_to_yes` moves the training label prior toward the later test prior,
whereas `yes_to_no` moves it farther away. The high-dose `no_to_yes` cell nearly
matches the test prevalence by construction, even though its operation is
nominally label corruption.

## Seed distribution and registered inference

| Direction/dose | Mean relative log-loss change | Seed range | Improving seeds |
|---|---:|---:|---:|
| yes-to-no 10% | +1.9036% | -10.6676% to +22.1076% | 12 / 30 |
| yes-to-no 20% | +3.4700% | -26.3017% to +25.7266% | 13 / 30 |
| yes-to-no 30% | +5.3267% | -12.8755% to +47.2551% | 14 / 30 |
| no-to-yes 10% | -54.4397% | -74.7456% to -29.5156% | 30 / 30 |
| no-to-yes 20% | -68.3998% | -82.8853% to -51.4025% | 30 / 30 |
| no-to-yes 30% | -77.1681% | -84.3186% to -62.2700% | 30 / 30 |

For `no_to_yes`, every one of the 90 seed effects is negative. The behavior is
therefore not driven by one favorable seed.

The registered co-primary calculations reproduce as follows:

| Direction | Point estimate | 95% crossed-bootstrap CI | Raw one-sided sign-flip p | Holm p | Pass |
|---|---:|---:|---:|---:|---:|
| yes-to-no | +0.053267 | [0.003322, 0.107784] | 0.028890 | 0.057779 | no |
| no-to-yes | -0.771681 | [-0.792294, -0.748986] | 1.000000 | 1.000000 | no |

The first direction misses the registered family-wise threshold after Holm
correction. The second direction fails the sign, interval, and practical-effect
requirements. Neither direction passes the frozen Bank decision rule.

## Root-cause disposition

`temporal_prior_shift_supported`

No audited implementation defect was found. The coherent combination of:

- a train-to-test positive prevalence increase from 4.81% to 30.83%;
- extreme clean underprediction on the sealed test;
- exact movement of the corrupted training prior toward the test prior;
- dose-ordered calibration improvement; and
- negative log-loss effects in all 90 `no_to_yes` seeds

supports temporal class-prior shift as the dominant observed explanation for
the apparent benefit of `no_to_yes` corruption.

This interpretation agrees with established prior-probability/label-shift
theory: posterior probabilities trained under one class prior can become
invalid under another, and correcting for the changed prior can improve
probabilistic prediction. See Saerens, Latinne, and Decaestecker (2002),
<https://doi.org/10.1162/089976602753284446>, and Lipton, Wang, and Smola
(2018), <https://proceedings.mlr.press/v80/lipton18a.html>.

## Limits and next study

The audit does not establish pure label shift because it does not prove that
`P(X | Y)` stayed constant across time. Covariate shift, class-conditional
feature shift, and concept shift may coexist. It also does not show that label
corruption is a valid deployment correction. The corruption intervention has
instead exposed a construct confound: under a severe temporal prior mismatch,
changing training labels can act partly like prior correction.

A future preregistered study should separate these constructs by including:

1. an unchanged-label clean baseline with explicit prior correction;
2. a prevalence-matched evaluation or reweighting sensitivity analysis;
3. diagnostics or tests for changes in `P(X | Y)`, not only `P(Y)`;
4. the same corruption grid evaluated under both prevalence-stable and
   temporally shifted conditions; and
5. a new frozen protocol and independent outcome store.

Those analyses may explain or extend the result, but they must not be used to
rewrite the v2 registered outcome.

## Reproduction

From a checkout containing the preserved frozen result store:

```bash
python scripts/p2_confirmatory_root_cause.py \
  --expected-store-sha256 \
  7e46d0997bc5ad6807409a4aebea39c82c11216f2bdcadd5704724994117504c
```

Omitting `--skip-convergence-refits` is required for the reported disposition.
Skipping refits deliberately limits the audit to `inconclusive` unless a defect
is already found.
