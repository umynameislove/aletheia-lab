# P2R v1.1 registered recovery protocol

## Purpose

P2R v1.1 is a prospective technical recovery from the registered P2R v1
archive-loading failure. It is not a rerun of v1, an independent replication,
or a scientific redesign. The two mechanism studies remain data drift and
preprocessing mismatch over the same named primary and external-replication
datasets.

The predecessor attempt is permanently retired. Its terminal store SHA-256 is
`28fab91bf7a24994f93a7a145e3786ae200dab8062d06f7501e114df0ce7e28d`.
It produced no scientific disposition, fitted no model, and published no
partial outcome.

## Frozen predecessor chain

The recovery protocols bind the complete v1 scientific protocols rather than
copying selected fields:

| Mechanism | Predecessor protocol SHA-256 | Recovery protocol SHA-256 |
| --- | --- | --- |
| Data drift | `bad097a4298f7925b314f049a762da2f0e4485a24f40860d667ae936b422c289` | `e9d9dd57f3e92a0825631c11dbf2d570b01a993a04757c8e08a503f1c76c0003` |
| Preprocessing mismatch | `4fcca028153fce45098e8547608d16231c33f9a78cdc243ff9931d119eca4904` | `4a166b04da1b801af6d625703a900d542dc66d001c88a486a0a8984c792230f2` |

Both successors also bind:

- failure audit SHA-256
  `b5d8701cbc50f2eab32cfe0a1d880126907510778cb58edea2f1273397caec24`;
- archive-readiness receipt SHA-256
  `528e5d1d25f905c450faeafe6c35c87b7cc09f25f4f9fe77666f85da5c36403c`;
- the two frozen archive SHA-256 identities;
- the readiness implementation file and implementation commit; and
- the exact predecessor protocol bytes and canonical identities.

## Permitted technical delta

Only four changes are permitted:

1. reproduce archive, member, schema, parser and eligibility evidence before a
   registration receipt is written;
2. revalidate the same readiness receipt immediately before the shared sealed
   marker is created;
3. permanently retire the failed v1 attempt; and
4. use new protocol, tag, immutable release, registration and terminal-store
   identities for the single prospective successor.

Datasets, roles, split membership, model, preprocessing, seeds, candidate plan,
interventions, nuisance comparators, estimands, metrics, thresholds, exclusions
and disposition rules are unchanged. No outcome information was used to choose
the repair.

## Outcome-blind verification

Run the structural verifier without compiling local archives:

```bash
PYTHONPATH=src python scripts/p2r_v1_1_protocol_registration.py verify
```

Reproduce archive readiness from the two SHA-pinned local archives before
publishing the registration:

```bash
PYTHONPATH=src python scripts/p2r_v1_1_protocol_registration.py compile-readiness
```

Both commands must report that model fitting, predictive metrics and sealed
outcomes remain false. The second command may inspect encoding and class
eligibility only; it must not compile split membership or open a sealed
partition.

## Registration and execution boundary

After this protocol commit is merged, create annotated tags:

- `p2r-data-drift-confirmatory-v1.1`; and
- `p2r-preprocessing-mismatch-confirmatory-v1.1`.

Each tag must receive an immutable GitHub Release whose protocol identity
matches the tagged file. Merely merging, tagging, publishing a release or
passing CI does not authorize scientific admission.

Execution requires a separate runtime update that understands the v1.1
recovery wrapper, uses new registration/marker/store paths, verifies both
immutable releases, and consumes at most one paired prospective attempt. The
protocol-registration task itself must not execute a model or generate an
outcome.

## Historical execution note

The protocol above remains the immutable record of what was authorized. The
single v1.1 attempt subsequently terminated at `execute_replication` without a
scientific disposition. The post-attempt
[replication-failure audit](p2r-v1-1-replication-failure.md) found a registered
intervention-capacity defect in the online-shoppers `VisitorType` data-drift
direction. This note does not amend v1.1; it links the retired protocol to its
content-addressed terminal evidence and the requirements for a prospective
methodological successor.
