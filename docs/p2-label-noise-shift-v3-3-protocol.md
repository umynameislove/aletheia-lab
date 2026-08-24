# P2 label-noise shift closeout-recovery protocol v3.3

## Purpose and boundary

V3.3 is a single prospective technical recovery from the immutable v3.2
`build_closeout` failure. It is not a new scientific design and is not an
independent new-dataset replication. The protocol preserves the v3.2 datasets,
partitions, preprocessing, models, calibration, interventions, seeds,
estimands, metrics, inference procedures, thresholds, and decision rules.

The only semantic correction is to represent two already-registered abstention
states without contradiction:

- calibration abstention contains no scored outcome or partial inference;
- scientific assumption abstention contains both complete dataset inferences,
  all three registered MMD families, and the bound study decision, while still
  prohibiting a cross-dataset claim.

Mixed or incomplete representations fail closed. The correction cannot turn an
abstention or failure into an admission, modify a p-value, change an assumption
threshold, or rescue a direction with a secondary model.

## Predecessor disclosure

The v3.2 terminal store remains immutable at
`1ce2b827d027cdb0685ad22c520d1ff11b6fcb45e2af5894ad2f4f964c97d029`.
It contains a registration, environment receipt, and technical-failure receipt;
it contains no dataset attempts, numerical outcome artifacts, or scientific
disposition. Its tracked outcome-blind audit has canonical SHA-256
`2f18d52c682a86ba6ab638a94b6163cfb0a5459453083d31319f088235246da4`.

The v3.2 sealed partitions were opened by the failed execution. Reusing those
same pinned partitions is explicitly disclosed, so v3.3 must be described as a
technical recovery rather than an independent confirmatory replication. The
recovery design used the failure receipt, reachable code path, and synthetic
reproduction only; unavailable numerical outcomes were not reconstructed or
used for tuning.

## Immutable scientific identity

The v3.3 candidate has canonical protocol SHA-256
`5fa057e17203b00fa78fc86d58ce5b324bb30cebf916ebb94a3d6b389f30b456`.
Its recovery implementation is commit
`b79568c3c60ffcf0a972b00c20fd9807754d8c9d`. Automated verification compares
every scientific section with v3.2 and rejects any changed model, calibration,
dataset binding, split, intervention, seed, inference setting, threshold, or
decision rule.

Before registration, verify the candidate without opening outcomes:

```bash
PYTHONPATH=src python scripts/p2_v3_3_protocol_registration.py verify
PYTHONPATH=src python scripts/p2_v3_3_protocol_registration.py compile-splits
```

The second command only recompiles deterministic split membership from pinned
archives. It must not fit a model or generate predictive metrics.

## Registration and execution governance

The required annotated tag is `p2-label-noise-shift-factorial-v3.3`. The tag
must point to the merged protocol-only commit. An immutable GitHub Release must
exist before any execution registration or sealed access. The release should
publish the protocol hash, predecessor terminal-store hash, failure-audit hash,
and recovery implementation commit.

This candidate grants neither registration nor execution authority. Execution
requires a later runtime implementation, a release-bound registration receipt,
a new one-use marker, explicit protocol and registration hash confirmations,
and atomic joint release of both dataset attempts. V3.3 permits one registered
execution attempt. Any further recovery requires a new disclosed protocol.

## Interpretation

`cross_dataset_admission`, `fail_closed`, and scientific `abstain` are valid
scientific terminal states. Calibration abstention is a valid fail-closed
technical/statistical terminal state without outcome scoring. A non-calibration
exception remains `technical_failure`. Regardless of disposition, the v3.1 and
v3.2 failures remain in the publication record.
