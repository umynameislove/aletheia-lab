# Phase 2 label-noise alpha recovery

## Decision

The primary alpha run does not contain an eligible label-noise failure family.
The mechanism-coverage gate must therefore fail. This is a research result, not
an implementation exception and not permission to relax the eligibility rule.

The label corruption implementation was technically valid and achieved its
declared mutation counts. All three primary fault-directed candidates remained
inside the frozen accuracy band of `-0.01 < delta < 0.01`:

| Slot | Declared rate | Achieved rate | Accuracy delta | Classification |
|---|---:|---:|---:|---|
| M2-F1 | 0.010 | 0.009939 | -0.001892 | stable |
| M2-F2 | 0.050 | 0.050101 | -0.005676 | stable |
| M2-F3 | 0.200 | 0.200000 | -0.008515 | stable |

This evidence rules out a silent no-op and supports the bounded diagnosis
`effective_intervention_below_frozen_primary_threshold`. It does not prove that
label noise is generally harmless. The result is specific to the frozen Telco
split, preprocessing, model, symmetric training-label intervention, seeds and
accuracy eligibility policy.

## Outcome-blind amendment

The recovery protocol uses only the three reserves already present in the
frozen candidate plan. It makes the following decisions before observing any
reserve outcome:

1. execute M2-R1, M2-R2 and M2-R3 in full;
2. retain M2-R1 and M2-R2 as sensitivity probes, excluded regardless of outcome;
3. promote only M2-R3, the prespecified 30% high-dose candidate;
4. supersede M2-F3 so the admitted alpha remains capped at 15 families;
5. preserve the 0.01 threshold, dataset, split, model, preprocessing and all
   primary measurements;
6. forbid a second recovery round.

Running every reserve prevents optional stopping. Fixing the promoted slot before
execution prevents selecting whichever reserve happens to cross the threshold.
The authorization is stored inside `candidate-execution.json` and binds the
source store, candidate plan, candidate census, coverage audit and primary
observations by SHA-256.

## Recovery result

The complete reserve run also failed to create an eligible family:

| Slot | Declared rate | Accuracy delta | Classification | Admission role |
|---|---:|---:|---|---|
| M2-R1 | 0.025 | +0.001892 | stable | excluded sensitivity probe |
| M2-R2 | 0.100 | -0.000946 | stable | excluded sensitivity probe |
| M2-R3 | 0.300 | -0.006623 | stable | promoted candidate |

The reconciled recovery run contains 18 executions, 15 admitted families and
three protocol-amendment exclusions. Its final status remains:

- `mechanism_coverage_passed = false`;
- `gate_status = fail`;
- `label_noise = no_eligible_failure`.

Consequently, human validity review and benchmark scale-up must not treat the
current label-noise mechanism as covered.

## Reproduction

From the repository root with the development environment active:

```bash
python scripts/p2_alpha_recovery.py \
  --source-output experiments/p2/runs/alpha-primary-coverage-audit \
  --recovery-output experiments/p2/runs/alpha-label-noise-recovery
```

The command writes two immutable stores and exits with status 2 when mechanism
coverage remains incomplete. Re-running against identical inputs may reuse an
identical store; a non-identical overwrite is refused.

## Permitted next research decisions

No further seed, threshold or candidate tuning is permitted under this alpha
protocol. A subsequent study must choose and document one of these options
before observing new outcomes:

- preregister a new label-noise construct with a dataset/model combination that
  has a justified sensitivity analysis;
- preregister a different primary performance endpoint, with construct validity
  independent of the observed alpha values; or
- narrow the benchmark claim and remove label-noise mechanism coverage.

Until one option is approved as a new protocol version, the correct scientific
state is an informative failed alpha rather than a fabricated complete sample.
