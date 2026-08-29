# P2R v1.1 replication-failure audit

## Status and scope

The single registered P2R v1.1 attempt is complete and permanently retired as
a technical failure. Its terminal store is content-addressed by
`aafbecaaab43dddad538cf23a66190ca2b71c1a573ed04c7232db14105e12a53`.
It must not be replayed, removed, overwritten, or interpreted as a scientific
admission result.

This audit answers a narrower question: why did the paired execution stop at
`execute_replication`, and what must be frozen before any prospective
successor? It does not fit a model, inspect predictive outcomes, change an
endpoint, or authorize a new execution.

## Evidence chain

Three independently checkable evidence sources reconcile:

1. The terminal failure records `P2RRuntimeError` at
   `execute_replication` with message SHA-256
   `f56917bf211df220637ebdfd2b83cf0e1de68b582b905f7fd838f30f6061826c`.
2. The execution-commit control flow has one matching preimage:
   `registered manipulation cannot achieve its declared row count`.
3. A covariate-only census of the SHA-pinned registered partitions reproduces
   the exact shortfall: the online-shoppers sealed partition contains 2,465
   rows, the registered 20% dose requires 493 rows, and only 343 rows are
   susceptible to the registered `VisitorType` data-drift direction.

The deficit is therefore 150 rows. The preprocessing direction has 2,122
eligible rows and is not the failing direction. The tracked audit also binds
the registration, sealed marker, outer manifest, nested failure, environment,
runtime file, entrypoint file, and feasibility receipt by SHA-256.

## Root-cause classification

The root cause is a **registered intervention-capacity defect**.

It is not:

- a negative scientific result, because no complete two-mechanism/two-dataset
  measurement census or scientific disposition was published;
- an implementation bug in deterministic row selection, because the runtime
  correctly refused to select 493 distinct rows from a 343-row eligible set;
- an archive, parser, split, model-convergence, closeout, or publication
  defect; or
- evidence that the mechanism itself has no effect.

It is a protocol-feasibility defect because the registered target feature and
direction could not physically deliver the registered dose on the registered
sealed population. The v1.1 preflight checked archive readiness but did not
bind an intervention-support census before consuming the attempt.

## Scientific basis: positivity and treatment support

For a deterministic fault intervention, the declared dose is identifiable only
inside the susceptible population. Let `S_m(x)` indicate whether row `x` can
receive mechanism direction `m`, let `n` be sealed-set size, and let `q` be the
declared dose. A necessary pre-execution condition is:

```text
sum_x S_m(x) >= floor(q * n)
```

For a paired study, this condition must hold for both registered directions.
P2R additionally requires a 5% absolute reserve:

```text
min(capacity_data_drift, capacity_preprocessing)
  >= floor(0.20 * n) + ceil(0.05 * n)
```

The reserve is not post-outcome tuning. It is a prospective robustness margin
against a design that sits exactly on a categorical-support boundary. It does
not change the executed 20% dose.

This is the same logical role as positivity/overlap in causal designs: an
effect at a declared treatment level cannot be evaluated where assignment at
that level is structurally impossible. Passing CI or successfully injecting a
smaller dose cannot repair that estimand mismatch.

This analogy is deliberately bounded. Standard positivity concerns support for
treatment assignment conditional on covariates; P2R checks deterministic row
capacity for a registered synthetic intervention. The check is necessary for
the declared fault dose to exist, but it is not sufficient for exchangeability,
dominant cause, construct validity, or cross-dataset generalization. Those
remain separate gates. The methodological basis and claim boundary are linked
from `docs/related-work.md`.

## Complete covariate-only census

The compiler reproduced the already-registered stratified memberships, then
used only categorical covariates for capacity measurement and target
selection. Target labels were not used to rank features, no model was fitted,
and no predictive metric was generated.

The default-credit primary partition has 6,000 sealed rows, a 1,200-row dose,
and a 300-row reserve. All nine categorical candidates satisfy both directions
with reserve, so its frozen `EDUCATION` target is retained.

The online-shoppers replication partition has 2,465 sealed rows, a 493-row
dose, and a 124-row reserve:

| Feature | Drift capacity | Preprocessing capacity | Minimum | 20% + reserve |
| --- | ---: | ---: | ---: | --- |
| `Month` | 1,830 | 635 | 635 | pass |
| `OperatingSystems` | 1,140 | 1,325 | **1,140** | pass |
| `Browser` | 868 | 1,597 | 868 | pass |
| `Region` | 1,525 | 940 | 940 | pass |
| `TrafficType` | 1,705 | 760 | 760 | pass |
| `VisitorType` | **343** | 2,122 | 343 | fail |
| `Weekend` | 574 | 1,891 | 574 | fail reserve |

The outcome-blind selection rule is frozen as follows:

1. retain the registered target if both directions satisfy dose plus reserve;
2. otherwise keep only candidates satisfying both directions plus reserve;
3. maximize the smaller directional capacity; and
4. break any tie by the categorical-feature order in the pinned manifest.

This rule selects `OperatingSystems`. It is preferable to selecting the first
merely feasible feature because it maximizes the worst-direction support and
therefore minimizes proximity to another structural capacity boundary. It is
not evidence that `OperatingSystems` will yield a larger effect.

## Why a successor is methodological, not technical-only

Changing `VisitorType` to `OperatingSystems` changes the intervention target
and therefore the scientific semantics. A successor cannot be described as a
retry of v1.1 or as an archive-only repair. It must be a prospective v1.2
methodological amendment with new protocol hashes, annotated tags, immutable
releases, registrations, attempt marker, and terminal store.

The following remain unchanged unless separately preregistered: named
datasets, dataset roles, split membership, preprocessing, model, seeds,
declared 20% dose, nuisance comparators, endpoints, thresholds, exclusion
rules, paired reduction, and disposition policy. The amendment must bind the
full feasibility receipt, its deterministic selection policy, the selected
feature per dataset, and the retired v1.1 terminal store.

Because the same previously opened named partitions are reused after a
disclosed failure, v1.2 is not an independent new-dataset replication. Any
claim must remain bounded to this recovery design.

## No-v1.3 authorization checklist

No process can guarantee that a future scientific execution will never fail.
The following fail-closed checks remove the presently identifiable technical
and protocol risks before another one-attempt registration:

| Boundary | Required prospective evidence |
| --- | --- |
| Frozen predecessor | v1.1 terminal store and every audit artifact verify byte-for-byte |
| Archives | archive/member/schema/parser/readiness receipt reproduces from SHA-pinned bytes |
| Splits | both registered split and sealed-membership hashes reproduce |
| Intervention support | all candidates censused; selected target has exact 20% capacity plus 5% reserve in both directions |
| Dose contract | structured target count, eligible count, selected feature, direction, and rounding rule bind the protocol |
| Transform contract | target and nuisance frames preserve manifest columns, dtypes, row count, and finite transformed matrices on an outcome-free rehearsal |
| Model contract | registered solver/configuration is exercised on synthetic contract fixtures; real sealed outcomes remain unopened before execution |
| Runtime census | synthetic rehearsal produces exactly 2 mechanisms × 2 datasets × 5 seeds, 10 paired observations, and no replayed identities |
| Decision contract | all admitted, assumption-limited, rejected, and fail-closed branches are built and round-trip verified from synthetic evidence |
| Publication | success and technical-failure stores publish atomically, durably, without partial scientific artifacts |
| Platform | path, timestamp, JSON, and atomic-publication contracts pass on Linux, macOS, and Windows where supported |
| Governance | clean synchronized `main`, immutable releases, explicit hashes, zero prior successor attempts, and exclusive marker/store paths |

The v1.2 preflight must evaluate every outcome-free row above and fail before
creating the attempt marker if any item is absent. The production entrypoint
must also revalidate archive, split, feasibility, release, registration, and
path identities immediately before marker creation to close time-of-check/
time-of-use gaps.

## Interpretation and limitations

The audit strengthens P2R by converting a previously implicit assumption into
a content-addressed instrument-validity condition. It prevents a study from
calling a smaller-than-declared intervention a valid execution and prevents a
technical infeasibility from being misreported as a scientific null.

It does not show that either mechanism will be admitted, that the selected
feature has a large predictive effect, or that the result generalizes beyond
the two named datasets. Those are prospective scientific questions. Until a
successor completes, data drift and preprocessing mismatch remain pending
confirmatory admission.
