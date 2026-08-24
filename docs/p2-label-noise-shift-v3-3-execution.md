# P2 v3.3 registered execution and closeout

## Scope

Version 3.3 is the single registered technical recovery from the disclosed v3.2
closeout-contract failure. It reuses the same pinned datasets, split memberships,
models, calibration rules, interventions, seeds, estimands, inference procedures,
thresholds, and decision rules. It is not an independent new-dataset replication.

The only permitted behavioral change is the preregistered distinction between:

- calibration abstention, which contains no predictive metrics or scientific
  inference; and
- scientific assumption abstention, which retains both complete dataset
  inferences, all three assumption families, and the derived abstention decision.

The immutable protocol identity is
`5fa057e17203b00fa78fc86d58ce5b324bb30cebf916ebb94a3d6b389f30b456`
under annotated tag `p2-label-noise-shift-factorial-v3.3`.

## Safety contract

The runtime fails closed unless all of the following hold:

1. The worktree is clean, on `main`, and synchronized with `origin/main`.
2. The annotated v3.3 tag is an ancestor of the execution commit.
3. The tagged protocol has the registered canonical hash.
4. The public GitHub release is immutable, non-draft, non-prerelease, and bound
   to the exact v3.3 tag.
5. Both dataset archives and compiled split receipts match the frozen hashes.
6. Neither the v3.3 sealed-open marker nor a v3.3 terminal store exists.
7. The operator explicitly confirms both protocol and registration hashes.

Creating the sealed-open marker consumes the only v3.3 execution attempt. A
failure after this point is terminal and must not be rerun under v3.3.

## Preflight after implementation merge

Do not run preflight from a feature branch. After the implementation is merged,
synchronize `main`, activate the project environment, and run:

```bash
PYTHONPATH=src python scripts/p2_v3_3_confirmatory.py preflight
```

Preflight recompiles the two frozen partitions without fitting a model or opening
the sealed test partitions. Preserve the emitted protocol and registration hashes.

## Single registered execution

Run exactly once, substituting the registration hash emitted by preflight:

```bash
caffeinate -dimsu env PYTHONPATH=src \
  python scripts/p2_v3_3_confirmatory.py execute \
  --confirm-protocol-sha256 5fa057e17203b00fa78fc86d58ce5b324bb30cebf916ebb94a3d6b389f30b456 \
  --confirm-registration-sha256 <REGISTRATION_SHA256>
```

Do not delete a marker, terminal store, or failure receipt to obtain another run.

## Terminal outcomes

- `cross_dataset_admission`: both complete dataset inferences satisfy the
  registered admission rule; the cross-dataset claim is allowed within the two
  registered datasets.
- `fail_closed`: both analyses are complete, but the registered scientific
  decision does not admit the mechanism.
- `abstain`: either calibration could not support a complete outcome, or the
  registered shift-assumption checks require scientific abstention. The closeout
  representation distinguishes these two cases without inventing evidence.
- `technical_failure`: a non-calibration implementation or infrastructure failure
  prevented a scientific closeout. Only a hashed diagnostic is published and no
  partial dataset outcome is released.

All successful scientific outcomes are released together in one content-addressed
terminal store. Technical failure stores contain only registration, environment,
and failure evidence.

## Verification

Verify the immutable store without rerunning any model:

```bash
PYTHONPATH=src python scripts/p2_v3_3_confirmatory.py verify
```

Verification reconstructs the artifact census, hashes every file, validates the
terminal model, and reconciles registration, environment, attempts, closeout, and
store-root identities.

## Interpretation limits

Even a cross-dataset admission is bounded to the two registered UCI datasets and
the frozen protocol. Because v3.3 reuses partitions previously opened during a
disclosed technical failure, it strengthens recoverable P2 evidence but does not
replace future independent replication on new data.
