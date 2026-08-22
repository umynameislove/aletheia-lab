# Shift-aware label-noise v3 execution

This document describes the implementation that executes the immutable v3.1
protocol. It does not change the registered estimand, datasets, splits, model
grid, seeds, inference, thresholds, or admission rule.

## Scientific boundary

The runtime estimates the net effect of directional label corruption after
controlling the nuisance change in class prior. The primary comparison is:

`corrupted training labels - clean labels reweighted to the same induced prior`

Both models are scored on the same sealed records with the registered 50/50
reference-prior standardized log loss. Therefore an observed difference cannot
be explained solely by the altered training prior. A reciprocal mutation keeps
the clean prevalence fixed and provides an additional technical control.

The primary logistic-regression analysis is evaluated for both directions,
three corruption doses and 50 corruption seeds on each dataset. The 30% dose is
co-primary. The histogram-gradient-boosting analysis is sensitivity-only and
cannot rescue the primary analysis.

## Runtime invariants

The implementation fails closed when any of these contracts is violated:

- the protocol hash differs from
  `0e9c594a6453dc111def3208582cec85d13518d542a61d86197620f9707ab7b2`;
- the checked-out protocol differs from the annotated v3.1 tag;
- `main` is not clean and exactly synchronized with `origin/main`;
- the immutable GitHub release is absent, draft, prerelease or mutable;
- either SHA-pinned archive, dataset receipt or split membership differs;
- preprocessing sees unknown or non-finite structure outside its frozen rule;
- mutation counts, mutated-label hashes, induced prior, reciprocal prevalence,
  serialization roundtrip or model provenance do not reconcile;
- a registered seed, cell, environment, estimator, direction or dataset is
  missing, duplicated or replayed;
- calibration, fitting, BBSE, MLLS or RLLS violates its registered numerical
  contract; estimator failures are explicit `ABSTAIN`, never silently clipped;
- the result path or sealed-open marker already exists.

Fitted-model hashes bind targets, weights, preprocessing, learned parameters,
calibration, record identities and raw plus calibrated predictions. Dataset
outcomes bind all per-seed record-level losses and all shift-estimator evidence.

## Cross-dataset inference

Each co-primary direction uses the registered two-way product-weight bootstrap
over corruption seeds and sealed records. Its one-sided paired sign-flip test
uses the registered plus-one Monte Carlo correction. Dataset p-values are joined
with an intersection-union maximum, then Holm-corrected across directions.

Classwise MMD diagnostics are Holm-corrected across the two classes and the two
datasets separately for each registered prior environment. Any assumption
failure produces `ABSTAIN` for the bounded pure-label-shift claim. It is neither
a positive result nor evidence that label shift is absent.

A direction is admitted only if both datasets independently satisfy all of:

- net effect at least 5%;
- 95% bootstrap lower bound above zero;
- cross-dataset IUT followed by Holm below 0.05;
- all technical and orthogonalization controls pass;
- no prior-only condition is misclassified as label-noise evidence;
- the registered shift assumption diagnostics pass.

One dataset, the tree sensitivity model, a secondary estimator, or an oracle
result cannot rescue a failed primary direction.

## One-time workflow

Do not run these commands on the implementation branch. Merge the implementation
with green CI, synchronize local `main`, and preserve the immutable v3.1 release
before preflight.

### 1. Preflight

```bash
git switch main
git pull --ff-only origin main
git status --short --branch
PYTHONPATH=src python scripts/p2_v3_confirmatory.py preflight
```

Preflight may verify archive contents and split memberships, but it fits no
model and emits no predictive metric or scientific outcome. Record the returned
registration SHA-256. A repeated preflight is accepted only when its existing
receipt is byte-semantically identical.

### 2. Execute once

Insert the exact hashes printed by preflight:

```bash
caffeinate -dimsu env PYTHONPATH=src python scripts/p2_v3_confirmatory.py execute \
  --confirm-protocol-sha256 0e9c594a6453dc111def3208582cec85d13518d542a61d86197620f9707ab7b2 \
  --confirm-registration-sha256 <REGISTRATION_SHA256>
```

Execution creates the sealed-open marker before loading outcome-bearing frames.
The marker remains even if execution fails, so an interrupted or failed run
cannot be silently repeated. There is no overwrite or recovery flag. Any future
recovery study requires a new prospective protocol, tag and release.

Primary and replication outcomes remain in memory until both complete. They are
then published together through a staged directory rename. No partial dataset
result is printed or persisted.

### 3. Verify the closed store

```bash
PYTHONPATH=src python scripts/p2_v3_confirmatory.py verify
```

Verification re-hashes every artifact and reconstructs the registration,
environment, dataset-outcome, inference, decision and manifest bindings. A
single changed byte causes failure.

## Result store

The ignored output directory contains:

- `registration.json` — immutable release evidence;
- `environment.json` — interpreter, operating system and package versions;
- `primary-outcome.json` — complete primary dataset census;
- `replication-outcome.json` — complete external replication census;
- `closeout.json` — MMD families, both dataset inferences and final decision;
- `store-manifest.json` — content hashes and the store root.

Large raw outcomes must not be committed directly to Git. After successful
closeout, preserve a content-addressed external copy and commit only a compact
publication receipt and summary that bind the unchanged store root.

## Interpretation

`cross_dataset_admission` supports only the registered bounded claim: under the
two frozen tabular datasets, split policy, directional mechanism, models and
shift environments, at least one direction shows a reproducible corruption
effect beyond the induced-prior nuisance.

`fail_closed` means the conjunctive registered claim was not established. It
does not prove a zero effect. `abstain` means an assumption or validity gate
prevents the pure-label-shift interpretation. Neither outcome authorizes
threshold tuning, new seeds, selective reruns or replacement datasets in this
study.
