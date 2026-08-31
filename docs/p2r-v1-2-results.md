# Instrument-validity study v1.2: terminal results and preservation

## Terminal status

The instrument-validity study v1.2 completed its single registered paired execution. This is a valid
scientific negative result, not a technical failure. The atomic terminal store
has SHA-256
`7b920ef15cc5683965652a3dc02cef06bf514d8c85e1954c3094c1c441919956`,
and rerunning the study is forbidden.

The complete registered census contains two mechanisms, two named dataset
roles, five seeds per mechanism and dataset, 20 dataset-seed measurements, and
10 paired cross-dataset observations. Both mechanism outcomes were released
together.

## Results

| Mechanism | Manipulation fidelity | Target-effect gate | Direction gate | Paired instrument | Disposition |
|---|---:|---:|---:|---:|---|
| Data drift | passed at the registered 0.20 dose | failed on both datasets | failed on both datasets | failed | `rejected` |
| Preprocessing mismatch | passed at the registered 0.20 dose | failed on both datasets | failed on both datasets | failed | `rejected` |

The interventions changed the registered feature at exactly the declared
magnitude. They did not produce a practically sufficient and directionally
stable target-metric effect under the prespecified gates. Therefore the result
does not support either mechanism as dominant-cause diagnostic ground truth.

The result supports a narrower construct-validity finding: implementation
success and intervention fidelity are insufficient for scientific admission.
An injected change may be real while the intended causal contrast remains too
weak or unstable to ground diagnosis scoring.

## Claim boundary

Allowed claims are limited to the following:

- the registered paired study completed without a technical failure;
- both registered interventions achieved the declared 0.20 magnitude;
- neither mechanism passed the target-effect, direction, and paired-instrument
  admission chain;
- both mechanisms were correctly rejected by the prospective fail-closed gate;
- the rejection is bounded to the two named, previously opened partitions and
  is not an independent new-dataset replication.

The result does **not** support a cross-dataset mechanism-admission claim, a
causal-diagnosis ground-truth claim, a two-of-two admission rate, or a claim
that the mechanisms are universally ineffective. A future attempt to strengthen
either mechanism requires a new independent protocol, new evidence, and a new
versioned status artifact. It may not reinterpret or rerun v1.2.

## Compact publication artifact

The tracked summary is
`configs/benchmark/provenance/p2r_v1_2_publication_summary.json`. It reproduces
every terminal disposition, decision hash, registered protocol, evidence
census, and bounded metric needed for publication without committing the local
terminal store into Git. Its canonical SHA-256 is
`e8595547c26f73f81d20fd53d00e5e876bb3a3c4f4044027bf8c9c6205fedd2e`.

Verify it against the frozen local store with:

```bash
PYTHONPATH=src python scripts/p2r_v1_2_closeout.py verify
```

## External content-addressed preservation

The ignored terminal store is copied once to:

```text
../preserved-artifacts/p2r-confirmatory-v1.2/
  sha256-7b920ef15cc5683965652a3dc02cef06bf514d8c85e1954c3094c1c441919956/
```

The destination contains the byte-identical result store and a preservation
receipt. The verified tree is made read-only. Existing content is never
overwritten, the source result store is never modified, and any missing,
unexpected, replayed, or hash-mismatched artifact fails closed.

The completed preservation receipt has canonical SHA-256
`78c506edb1e2ae313a89e1dccb1552dcd4eff8a5d623a42fc1d756b6e4e3ff4f`.
It binds eight manifest-counted result-store artifacts totaling 57,774 bytes,
confirms byte identity, and records that the source store was not modified.

```bash
PYTHONPATH=src python scripts/p2r_v1_2_closeout.py preserve
```

## Downstream consequence

The superseding disposition policy has three non-interchangeable terminal
tracks:

- primary admitted mechanisms: empty (`n_admitted = 0`);
- assumption-limited mechanisms: label noise;
- rejected instruments: data drift and preprocessing mismatch.

All three mechanisms remain eligible for evidence-accountability and validity-
behavior evaluation. None is eligible for positive causal-diagnosis scoring.
Downstream diagnosis and statistical evaluation must publish the empty primary result rather than substitute another
denominator or pool tracks.
