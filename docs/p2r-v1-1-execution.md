# P2R v1.1 recovery execution

## Scope

This runtime implements the prospective execution authorized by the paired P2R
v1.1 technical-recovery protocols. It does not rerun or overwrite v1. The v1
terminal store remains permanently fixed at
`28fab91bf7a24994f93a7a145e3786ae200dab8062d06f7501e114df0ce7e28d`.

The recovery introduces no new scientific choice. Data, dataset roles, split
membership, preprocessing, model, seeds, interventions, nuisance comparators,
estimands, thresholds, exclusion rules and dispositions are read from the
unchanged v1 scientific protocols. The v1.1 releases authorize only the
disclosed archive-readiness repair.

## Two-layer registration

The runtime preserves two different identities rather than conflating them:

1. the original v1 immutable releases register the unchanged scientific design;
2. the v1.1 immutable releases register the technical recovery and bind the v1
   failure audit, predecessor protocols and archive-readiness receipt.

The outer v1.1 terminal store binds both registration layers. Its nested
`scientific-store/` uses the existing P2R decision and evidence contracts, while
the outer manifest additionally binds the recovery protocols, recovery releases,
readiness receipt and predecessor failure audit.

## Required immutable releases

After this runtime PR is merged, the protocol commit already merged in the
previous PR must be available through annotated tags and immutable GitHub
Releases:

- `p2r-data-drift-confirmatory-v1.1`;
- `p2r-preprocessing-mismatch-confirmatory-v1.1`.

The tags must point to the commit containing the exact tracked v1.1 protocol
files. A lightweight tag, mutable release, draft, prerelease, mismatched release
URL or mismatched tagged protocol fails closed before an attempt artifact is
written.

## Artifact separation

The successor uses only new paths:

| Evidence | v1.1 path |
| --- | --- |
| Archive readiness | `experiments/p2/outputs/p2r-v1-1-archive-readiness.json` |
| Recovery registration | `experiments/p2/outputs/p2r-v1-1-registration.json` |
| Shared sealed marker | `experiments/p2/outputs/p2r-v1-1-sealed-open.json` |
| Terminal store | `experiments/p2/outputs/p2r-confirmatory-v1-1/` |

The v1 registration, marker and terminal store are read-only predecessor
evidence. They are never reused as v1.1 output targets.

## Outcome-blind preflight

On clean `main` synchronized with `origin/main`, run:

```bash
PYTHONPATH=src python scripts/p2r_v1_1_confirmatory.py preflight
```

Preflight performs the following before writing the recovery registration:

1. reproduces both protocol files from annotated tags;
2. validates both immutable GitHub Releases;
3. verifies the complete v1 failure audit against preserved local evidence;
4. reproduces both SHA-pinned archive audits and the frozen readiness hash;
5. confirms that no v1.1 marker or terminal store exists; and
6. writes only readiness and recovery-registration receipts.

A successful report must retain `registered_attempts_consumed: 0`,
`sealed_test_opened: false`, `model_fitted: false`, and
`outcomes_generated: false`.

## Single paired execution

Copy the two recovery protocol and registration hashes from preflight exactly:

```bash
caffeinate -dimsu env PYTHONPATH=src \
  python scripts/p2r_v1_1_confirmatory.py execute \
  --confirm-protocol-sha256s <drift-recovery-sha>,<preprocessing-recovery-sha> \
  --confirm-registration-sha256s <drift-registration-sha>,<preprocessing-registration-sha>
```

Immediately before opening the shared marker, execution revalidates the current
archive bytes against the frozen readiness receipt. The marker is created with
exclusive filesystem semantics before any sealed evaluation. It binds both
protocol layers, both registration layers, the v1 terminal failure, the failure
audit and readiness receipt. A marker or output store at the v1.1 path forbids
another attempt.

Both mechanisms and both dataset roles are executed and released together. A
complete terminal store requires the full 20-measurement census, ten paired
instrument observations and one joint closeout. Any exception after the marker
creates a fail-closed technical terminal store without partial measurements or
partial scientific disposition.

## Verification and interpretation

Verify the content-addressed outer and nested stores with:

```bash
PYTHONPATH=src python scripts/p2r_v1_1_confirmatory.py verify
```

Possible scientific dispositions remain `admitted`, `assumption_limited`, or
`rejected` per mechanism and are derived from the unchanged v1 rules. Passing
CI, publishing releases, completing preflight or obtaining a technically valid
store does not by itself constitute scientific admission.
