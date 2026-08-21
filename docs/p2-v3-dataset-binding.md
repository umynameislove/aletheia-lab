# Prospective v3 dataset binding

## Status and boundary

This artifact binds the two datasets selected by the outcome-free v3 design.
It is not a preregistration and cannot authorize execution.

The binding operation inspected only:

- official source bytes and archive structure;
- source schema and target encoding;
- class counts required by the prospective eligibility floor;
- missing or blank cells;
- identifier uniqueness and exact-feature duplicate groups; and
- columns that must never reach the analysis frame.

No split was materialized, no model was fitted, no predictive metric was
calculated and no sealed experimental outcome was generated.

The machine-readable manifest is
`configs/benchmark/p2_label_noise_shift_v3_dataset_bindings.json`, canonical
SHA-256
`a019a134f6e903fb80a6714237ca59b36adcca3ef1ab69cd9fd664fc5fc63b94`.
The deterministic audit receipt has canonical SHA-256
`bc480803505edb7c49f67eb24b8ebbb8ba415c4813278631239f372aca2f60d1`.

## Source census

| Role | Dataset | Raw rows | Negative | Positive | Analysis features |
|---|---|---:|---:|---:|---:|
| Primary | UCI Default of Credit Card Clients | 30,000 | 23,364 | 6,636 | 23 |
| External replication | UCI Online Shoppers Purchasing Intention | 12,330 | 10,422 | 1,908 | 16 |

Both datasets exceed the prospectively fixed minimum of 1,000 records in each
class. Both official UCI pages state a CC BY 4.0 license.

### Primary archive

- UCI ID: `350`
- DOI: `10.24432/C55S3H`
- archive SHA-256:
  `56c885f84457f6680f8438f02bfcdac9579323d8a94465ee5f26e32baa727602`
- member: `default of credit card clients.xls`
- member SHA-256:
  `30c6be3abd8dcfd3e6096c828bad8c2f011238620f5369220bd60cfc82700933`
- parser: sheet `Data`, header row `1`, engine `xlrd`
- target: `default payment next month`; positive `1`, negative `0`
- identifier excluded from model frame: `ID`

The target represents default payment in the following month. The 23 retained
features describe credit limit, demographics, prior repayment state, bills and
payments. No post-target feature is present in the bound source schema.

The source contains undocumented category values in `EDUCATION` and
`MARRIAGE`. They are not treated as missing and cannot be relabelled after an
outcome is observed. The future training-only preprocessor must retain them as
an explicit other/unknown category.

### External replication archive

- UCI ID: `468`
- DOI: `10.24432/C5F88Q`
- archive SHA-256:
  `2972e6184d3ad7beaaa831d9fc2b059dc3ee29df69d1ec593c466a5cd8485d14`
- member: `online_shoppers_intention.csv`
- member SHA-256:
  `b3055ee355f59134d851d32641183cb4a8b45def7124d2f50442a042f358e0d9`
- parser: comma-separated CSV, header row `0`, pandas C engine
- target: `Revenue`; positive `true`, negative `false`

`PageValues` is excluded prospectively. UCI describes it as the average value
of a page visited before completion of an e-commerce transaction. Although it
is commonly used as a predictor, its target-proximal construction creates an
avoidable leakage challenge for a study whose contribution is mechanism
verification rather than maximal purchase prediction. Removing it before
registration is the conservative choice. It cannot be restored after outcomes.

## Duplicate leakage control

Exact analysis-feature duplicates are retained, but they must never cross
train, development and sealed-test partitions.

| Dataset | Exact-feature groups | Rows in groups | Groups with multiple target values |
|---|---:|---:|---:|
| Default of Credit Card Clients | 52 | 108 | 21 |
| Online Shoppers | 76 | 201 | 0 |

Dropping duplicates would make an outcome-sensitive choice when identical
features carry different targets, as occurs in the primary dataset. Randomly
splitting individual rows would permit duplicate-feature leakage. The frozen
policy therefore retains every row and assigns each exact-feature group wholly
to one partition while approximating the registered stratified `60/20/20`
ratios with seeds `2718` and `3141`.

The exact deterministic group-assignment algorithm and its achieved partition
counts must be frozen in the protocol-only registration. Failure to keep a
group within one partition is a technical failure, not permission to rerun with
a favorable seed.

## Missing data and record identity

Both bound snapshots contain zero missing or blank cells. The registered policy
is fail-closed: a future source with any missing or blank cell differs from this
binding and cannot be silently imputed.

The primary record identity uses the unique `ID` field. Online Shoppers has no
source identifier, so identity is the source-order row ordinal bound to the
member SHA-256. Reordering the source changes the target/record binding and must
fail verification.

## Reproduction

Archive bytes remain under `data/raw/p2-v3/` and are excluded from Git. Acquire
or verify them with:

```bash
python scripts/p2_v3_dataset_binding.py acquire
python scripts/p2_v3_dataset_binding.py verify
```

`verify` rechecks archive and member hashes, schema, target encoding, counts,
exclusions, duplicate census and every tracked receipt hash. Its report must
retain:

```text
model_fitted = false
predictive_metrics_generated = false
sealed_outcomes_generated = false
registration_authorized = false
execution_authorized = false
```

## Next scientific gate

The next implementation step is a protocol-only registration that freezes:

1. grouped stratified split assignment and exact membership receipts;
2. training-only feature preprocessing and unknown-category handling;
3. prior-matched and prevalence-preserving controls;
4. estimator abstention and assumption diagnostics;
5. inference and cross-dataset decision algebra; and
6. an immutable commit, tag and public release before any real execution.

This binding does not by itself satisfy the structured internal outcome-blind
audit required by the governance contract. That audit and the immutable
registration release must be completed before execution.

## Authoritative sources

- UCI Default of Credit Card Clients:
  <https://archive.ics.uci.edu/dataset/350/default+of+credit+card+clients>
- UCI Online Shoppers Purchasing Intention:
  <https://archive.ics.uci.edu/dataset/468/online+shoppers+purchasing+intention+dataset>
- CC BY 4.0 legal code:
  <https://creativecommons.org/licenses/by/4.0/legalcode>
