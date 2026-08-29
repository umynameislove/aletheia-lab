# P2R v1.2 outcome-blind methodological amendment

## Status and purpose

P2R v1.2 is a prospective, outcome-blind methodological amendment for the
paired data-drift and preprocessing-mismatch confirmatory studies. It is not a
rerun of v1.1, a technical-only repair, an independent new-dataset replication,
or evidence that either mechanism is scientifically admitted.

The retired v1.1 attempt stopped during external-replication execution because
the registered online-shoppers target, `VisitorType`, could supply only 343
eligible data-drift rows. The frozen protocol required 493 rows. This is a
protocol-feasibility defect, not a scientific negative result. No scientific
disposition was generated, no partial outcome was published, and the v1.1
terminal store remains permanently bound by SHA-256:

`aafbecaaab43dddad538cf23a66190ca2b71c1a573ed04c7232db14105e12a53`.

## Evidence chain

The amendment verifies every input below as a regular file and binds both the
file bytes and canonical model identity where applicable.

| Evidence | Canonical SHA-256 |
| --- | --- |
| v1.1 replication-failure audit | `43ac081372120a62b949b3dd732c38a50bf21e2ee0e9ba8086bd7bd4438f5c13` |
| outcome-blind feasibility receipt | `2234d0c7bf9b1a35e971792a34134c3917ce35b8ff6410afec8f64625f673c13` |
| data-drift scientific protocol v1 | `bad097a4298f7925b314f049a762da2f0e4485a24f40860d667ae936b422c289` |
| preprocessing scientific protocol v1 | `4fcca028153fce45098e8547608d16231c33f9a78cdc243ff9931d119eca4904` |
| data-drift recovery wrapper v1.1 | `e9d9dd57f3e92a0825631c11dbf2d570b01a993a04757c8e08a503f1c76c0003` |
| preprocessing recovery wrapper v1.1 | `4a166b04da1b801af6d625703a900d542dc66d001c88a486a0a8984c792230f2` |

The feasibility implementation is frozen at commit
`0b275d1f120ff9b982fc938055fba12fa186a315`. The verifier requires that commit
to exist and be an ancestor of the protocol candidate being checked.

## Prespecified selection rule

Feature support is measured without target values, predictions, fitted models,
effect estimates, or scientific outcomes. For each named sealed partition and
categorical feature, the census computes capacity in both intervention
directions. Selection follows this fixed rule:

1. retain the predecessor target if both mechanisms can deliver the declared
   dose plus reserve;
2. otherwise retain only features satisfying both directional capacities;
3. maximize the smaller of the two capacities; and
4. break a tie by the categorical-feature order in the pinned manifest.

This is a maximin support rule. It reduces sensitivity to the weakest
intervention direction without using predictive performance to select a
feature. It does not assert that the selected feature has a larger causal or
predictive effect.

## Frozen amendment

The registered dose remains 20% of the sealed partition and the prospective
capacity reserve remains 5%. Counts use the prespecified floor rule for dose
and ceiling rule for reserve.

| Dataset role | Predecessor target | v1.2 target | Sealed n | Dose | Reserve | Drift capacity | Preprocessing capacity | Minimum |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Default Credit, primary | `EDUCATION` | `EDUCATION` | 6,000 | 1,200 | 300 | 3,205 | 2,795 | 2,795 |
| Online Shoppers, external replication | `VisitorType` | `OperatingSystems` | 2,465 | 493 | 124 | 1,140 | 1,325 | 1,140 |

Only four changes are allowed:

1. select the target feature by the frozen outcome-blind rule;
2. bind structured bidirectional dose and reserve evidence;
3. permanently retire the v1.1 attempt; and
4. use new schema, tag, release, registration, marker, and terminal-store
   identities.

The change from `VisitorType` to `OperatingSystems` changes scientific
semantics and is disclosed as such. It is therefore a methodological amendment
rather than a technical retry.

## Invariants

The v1.2 wrapper inherits the following sections from v1 by canonical hash:
artifacts, model, endpoint, execution, exclusions, dispositions, and
governance. The verifier also rejects changes to:

- dataset identities, roles, split membership, or sealed membership;
- preprocessing, model configuration, seeds, or candidate count;
- 20% intervention dose or 5% reserve;
- nuisance-comparator semantics;
- estimands, metrics, thresholds, exclusion rules, or disposition rules; and
- any selection or tuning based on predictive outcomes.

Both mechanism protocols must bind one ordered dataset census, one failure
audit, one feasibility receipt, and the same selected target per dataset. A
missing mechanism, duplicate mechanism, divergent target, replayed evidence,
hash mismatch, or additional scientific change fails closed.

## Verification boundary

Verify the tracked candidates without model fitting:

```bash
PYTHONPATH=src python scripts/p2r_v1_2_protocol_registration.py verify
```

Recompile the frozen feasibility census from the two local SHA-pinned archives:

```bash
PYTHONPATH=src python scripts/p2r_v1_2_protocol_registration.py compile-feasibility
```

Both commands must report all of the following as false: model fitted,
predictive metrics generated, sealed outcomes generated, registration
authorized, and execution authorized. This task creates no attempt marker and
consumes no registered execution attempt.

## Registration boundary

After the protocol-only change is merged and verified from synchronized
`main`, create annotated tags and immutable GitHub Releases:

- `p2r-data-drift-confirmatory-v1.2`;
- `p2r-preprocessing-mismatch-confirmatory-v1.2`.

The immutable release metadata must be converted into new registration
receipts by a separate execution-runtime change. Exactly one paired v1.2
attempt may then be authorized, with one exclusive sealed-open marker and both
mechanism outcomes released atomically. Passing CI, merging, tagging, or
publishing a release alone does not constitute scientific admission.

## Claim scope and limitations

The strongest claim supported by this amendment is procedural: the two named
mechanism studies now have a prospective, content-addressed intervention-
feasibility condition that prevents under-dosed executions from being
misreported as scientific evidence.

The amendment does not establish mechanism validity, diagnostic performance,
cross-dataset admission, causal identification, or generalization beyond the
two named reused partitions. Because the partitions were opened during the
disclosed v1.1 failure, v1.2 is explicitly bounded as a named-dataset
methodological amendment and not an independent replication. Scientific status
can change only after a valid registered v1.2 execution and fail-closed
closeout.
