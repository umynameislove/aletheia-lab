# Shift-aware label-noise v3.2 technical-recovery protocol

## Status and boundary

This document accompanies the outcome-blind candidate at
`configs/benchmark/p2_label_noise_shift_v3_2_protocol.json`. Its canonical
SHA-256 is
`7cba25f08f4e27007bf17fc837b9f11137123f2f83452378c8ac3db5de3ffe27`;
the raw file SHA-256 is
`fe141be9ea83a1f03d03810fc01d49a112bfbd89390d2a5ac90036b694665122`.

This candidate neither registers nor executes the study. It fits no model,
computes no predictive metric and opens no sealed outcome. Registration must
occur only after this protocol-only change is merged, verified from `main`, and
published as the immutable tag `p2-label-noise-shift-factorial-v3.2`.

## Why a new version is scientifically necessary

The single v3.1 execution terminated during development-only logit calibration.
No atomic result store or scientific disposition was produced. The failure is
preserved in the published receipt whose canonical SHA-256 is
`d9b9b916df472418aeed015db7d5617500607c4b69fac89dc4c02a4d9b71111e`.
The v3.1 execution is permanently retired and must not be rerun.

The root cause was an implementation defect: Newton convergence used a summed
gradient while comparing it with a sample-size-independent tolerance. The
effective convergence criterion therefore changed with development-set size.
The correction uses the mathematically equivalent mean negative log-likelihood,
mean gradient and mean Hessian. It preserves the Newton direction while giving
the registered tolerance a stable per-record interpretation.

Calibration failure now yields a structured dataset-level `ABSTAIN` without
exposing a partial fitted artifact or scoring that dataset. Other numerical,
data, model and provenance defects remain hard failures. Abstention cannot be
converted into admission and the sensitivity model cannot rescue the primary
model.

## Exact permitted delta from v3.1

Only four changes are admitted:

1. calibration sum equations become mean equations;
2. a calibration exception becomes structured fail-closed abstention;
3. v3.1 is permanently retired; and
4. protocol schema, tag and registration identity advance to v3.2.

The protocol machine-checks that these scientific sections are identical to
v3.1: split algorithm, dataset split receipts, preprocessing, intervention,
prior-shift environments, shift estimators, inference and decision rule. Model
classes, model hyperparameters, calibration initialization, clip, tolerance,
iteration limit and line-search minimum step are also unchanged.

No threshold, effect size, seed, dose, grid, dataset, feature, estimand, metric,
bootstrap, hypothesis test or multiplicity rule was selected after the failed
execution. Any such change invalidates this protocol rather than silently
creating a different study.

## Frozen evidence chain

| Evidence | Canonical SHA-256 |
|---|---|
| v3 study design | `1c9c6592112038ae5ee11d0ef91921172dc61873e4d20272902178003171bd25` |
| dataset manifest | `a019a134f6e903fb80a6714237ca59b36adcca3ef1ab69cd9fd664fc5fc63b94` |
| dataset audit receipt | `bc480803505edb7c49f67eb24b8ebbb8ba415c4813278631239f372aca2f60d1` |
| v2 result store | `7e46d0997bc5ad6807409a4aebea39c82c11216f2bdcadd5704724994117504c` |
| v3.1 protocol | `0e9c594a6453dc111def3208582cec85d13518d542a61d86197620f9707ab7b2` |
| v3.1 technical-failure receipt | `d9b9b916df472418aeed015db7d5617500607c4b69fac89dc4c02a4d9b71111e` |
| recovery implementation commit | `c56a184e2bc8f2d3970abaa98910904984719626` |

The two unchanged dataset memberships are:

- Credit primary: `fa0f3fe0cb03b12cecf590f806107206c0bb5c65c332cd5940f9ddca800bbcd8`;
- Online Shoppers external replication: `1f9b1076228a949fd3bc2e3ad48651b27a2046350a51f82cbaaf63619e36574e`.

Target binding, record identity, partition membership and group assignment are
all content-addressed. Exact feature-duplicate groups remain confined to one
partition. Reusing the same frozen data is deliberate: it isolates the declared
technical correction from a change in scientific population or sampling.

## One prospective attempt

After immutable registration, v3.2 permits at most one new execution. Primary
and replication outcomes must be released together. The sealed test may be
opened once for this version. A second execution, another numerical recovery,
or any scientific redesign requires a new disclosed protocol and cannot be
called v3.2.

An outcome-blind failure still counts as an execution attempt. If calibration
abstains, the dataset is not scored and the disposition remains visible. This
rule prevents repeated attempts, optional stopping and silent selection of a
working numerical path.

## Verification and registration sequence

Before merge, verify the candidate and independently recompile the frozen
memberships from the SHA-pinned local archives:

```bash
PYTHONPATH=src python scripts/p2_v3_2_protocol_registration.py verify
PYTHONPATH=src python scripts/p2_v3_2_protocol_registration.py compile-splits
```

Both reports must keep model, metrics, sealed outcomes, registration authority
and execution authority false. The immutable tag and release must not be
created from a feature branch. After merge, repeat both commands from updated
`main`, create and push the annotated tag, then publish its immutable release.
Execution runtime adaptation and the single registered run belong to the next
task, not to this protocol-registration task.
