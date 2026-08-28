# P2R v1 archive-readiness recovery

## Terminal result

The registered P2R v1 execution ended in a structured technical failure at
`load_primary`. The terminal store remains immutable and verifies as:

- terminal store SHA-256:
  `28fab91bf7a24994f93a7a145e3786ae200dab8062d06f7501e114df0ce7e28d`;
- terminal artifact SHA-256:
  `074967051b5e5468ff814bddf56ea8fe1671bdaeeba3cabd225bcf4c8ca0128d`;
- partial outcomes published: false;
- model fitted: false; and
- scientific disposition generated: false.

The tracked audit is
[`p2r_v1_technical_failure_audit.json`](../configs/benchmark/provenance/p2r_v1_technical_failure_audit.json).
It binds the registration, sealed marker, environment, terminal manifest and
failure receipt by file hash. P2R v1 is permanently retired and must not be
rerun, deleted, replaced or interpreted as a scientific negative result.

## Root cause

The exception-message digest has the verified preimage
`cannot inspect the bound dataset archive`. Immediately after failure, neither
registered archive existed in the execution worktree. Valid SHA-pinned copies
were present in a different worktree because `data/raw/*` is intentionally
excluded from Git and therefore is not shared between worktree directories.

The defect was not archive corruption, model behavior, an intervention failure
or a failed scientific gate. The defect was that registered preflight validated
Git and immutable releases but did not reproduce local archive readiness before
creating the one-shot sealed-open marker.

## Outcome-free repair boundary

The repair may only:

1. reproduce the existing pinned archive byte count, archive hash, member
   census, member hash, parser, schema and eligibility audit;
2. write a content-addressed readiness receipt before an attempt is consumed;
3. revalidate that receipt immediately before a future marker is created; and
4. require a new protocol, tag, immutable release, registration and single
   prospective execution.

It must not change datasets, split membership, target features, seeds, model,
interventions, nuisance comparators, estimands, thresholds or decision rules.
It must not compile sealed split outcomes, fit a model or generate predictive
metrics while checking archive readiness.

## Recovery sequence

P2R v1.1 is permitted only after all of the following hold:

- the v1 failure audit verifies against the original ignored artifacts;
- both archives reproduce the frozen D4A dataset receipt;
- missing, malformed, wrong-size, wrong-hash, unsafe-member and parser failures
  all stop before registration or marker creation;
- the readiness receipt is content-addressed and cannot be overwritten with
  different evidence;
- tests demonstrate that archive failure consumes zero attempts; and
- the recovery protocol explicitly discloses the v1 failure and preserves all
  scientific sections unchanged.

The later v1.1 result remains bounded to the two named datasets and is a
technical recovery, not an independent new-dataset replication.
