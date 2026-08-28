# P2R registered confirmatory execution

## Scope

This runtime executes the two frozen lightweight studies for `data_drift` and
`preprocessing_bug`. It does not change their datasets, partitions, target
features, seeds, interventions, nuisance comparators, estimands, thresholds or
decision rules. The runtime is infrastructure only until it is merged, both
immutable releases exist, preflight passes on synchronized `main`, and the user
explicitly starts the single registered execution.

No registered P2R outcome was generated while this runtime was developed or
tested. Integration tests use synthetic in-memory data and never open the
pinned sealed partitions.

## Evidence flow

```text
two immutable protocol releases
  -> paired registration receipt
  -> one exclusive sealed-open marker
  -> 2 mechanisms x 2 datasets x 5 seeds
  -> 20 content-addressed dataset measurements
  -> 10 conservative paired observations
  -> frozen instrument-validity audit
  -> dataset decisions and mechanism dispositions
  -> one atomic terminal store
```

The 20 measurements preserve dataset-level results. The instrument audit uses
10 paired observations because a seed appearing on two datasets is one paired
unit, not two independent observations. For each mechanism and seed, the audit
uses the weaker achieved manipulation, weaker target effect, larger nuisance
effect and a hash binding both dataset receipts. This prevents dataset count
from inflating the independent denominator.

## Fixed runtime

- one deterministic logistic-regression implementation;
- no hyperparameter search, calibration fitting or outcome-dependent fallback;
- five registered seeds per mechanism and dataset;
- data drift changes the frozen 20% row subset toward the training-feature mode;
- the data-drift nuisance comparator is a seeded same-size empirical resample;
- preprocessing mismatch changes the frozen 20% row subset from the training
  mode to the second training mode;
- its nuisance comparator performs a name-bound permutation round trip that
  should preserve values; and
- every fitted model, source, manipulation and comparator receipt is hashed.
- the execution commit, interpreter, operating system and exact scientific
  package versions are captured in a hashed environment receipt.

The source-binding hash ties each measurement to its protocol, dataset, split
membership and sealed membership. A complete census rejects missing cells,
duplicates, replays, cross-protocol evidence, dataset-binding mismatches and
content-hash mismatches.

## Prespecified decisions

Each dataset passes only when all four frozen gates pass:

1. achieved manipulation is within the registered tolerance;
2. median seed-level accuracy drop reaches the practical threshold;
3. at least 80% of seeds have the harmful direction; and
4. target effect dominates the nuisance comparator by the frozen rule.

Mechanism disposition is then derived without manual interpretation:

| Disposition | Required evidence |
|---|---|
| `admitted` | both named datasets and the paired instrument audit pass |
| `assumption_limited` | exactly one named dataset passes |
| `rejected` | neither named dataset passes, or the paired audit blocks admission |
| `technical_failure` | execution or evidence assembly is invalid |

Only `admitted` enters the admitted-mechanism denominator. Even an admitted
result remains bounded to the two named datasets and is not independent
new-dataset replication because the sealed partitions were used previously.

## Fail-closed publication

Preflight validates clean synchronized `main`, annotated tags, tagged protocol
contents, both immutable GitHub releases and their exact release identities
before writing the registration receipt. A transient release API failure does
not create the sealed-open marker or consume the attempt.

Execution creates that marker exclusively before loading sealed partitions.
Afterward, any exception produces a structured technical-failure store and
forbids rerun. Partial measurements or partial mechanism outcomes are never
published. Success publishes both mechanisms together by atomic directory
rename with a manifest that binds every byte and validates the terminal
artifact semantically.

## Commands after merge and immutable release publication

Run these only from a clean `main` synchronized with `origin/main`:

```bash
PYTHONPATH=src python scripts/p2r_confirmatory.py preflight
```

Preflight prints the two protocol and registration hashes. Execution requires
both comma-separated hash sequences exactly as printed:

```bash
caffeinate -dimsu env PYTHONPATH=src \
  python scripts/p2r_confirmatory.py execute \
  --confirm-protocol-sha256s <data-drift-hash>,<preprocessing-hash> \
  --confirm-registration-sha256s <data-drift-registration>,<preprocessing-registration>
```

The terminal store can be checked without fitting a model or opening data:

```bash
PYTHONPATH=src python scripts/p2r_confirmatory.py verify
```

Do not execute from a feature branch and do not delete, edit or recreate the
registration, marker or terminal store to obtain another outcome.
