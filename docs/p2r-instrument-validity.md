# P2R instrument-validity and empty-evidence contract

## Purpose

P2R does not treat an injected change or an alpha regression as sufficient
ground truth. Before data drift or preprocessing mismatch can enter a registered
confirmatory study, the candidate set must pass a separate, outcome-blind
instrument gate.

The frozen machine-readable policy is
[`p2r_instrument_validity_protocol.json`](../configs/benchmark/p2r_instrument_validity_protocol.json).
Verifying this policy cannot fit a model, authorize a confirmatory run or open a
sealed outcome.

## Candidate-to-cause chain

```text
prespecified candidate
  -> outcome-free candidate/seed plan
  -> content-addressed manipulation observation
  -> manipulation-fidelity check
  -> harmful target-effect check
  -> target-versus-nuisance dominance check
  -> cross-seed direction check
  -> dominant-cause-eligible candidate census
  -> zero-evidence negative-control assignments
  -> later registered confirmatory protocol
```

Each arrow is separately auditable. Failure at one arrow keeps the candidate in
the census with structured reasons and prevents it from becoming hidden truth.

## Frozen numerical gates

| Gate | Frozen rule | Failure disposition |
|---|---|---|
| Manipulation fidelity | absolute achieved-versus-declared error <= `max(0.01, 10% × declared)` | `manipulation_fidelity_failed` |
| Target effect | accuracy delta <= `-0.01` | `target_effect_below_threshold` |
| Dominant cause | target effect >= `1.5 × nuisance`, or target-minus-nuisance >= `0.005` | `nuisance_dominance_failed` |
| Independent seeds | at least five unique seeds per mechanism | `insufficient_independent_seeds` |
| Direction stability | at least 80% of seeds move in the harmful direction | `mechanism_direction_unstable` |

The ratio and absolute-margin alternatives are prespecified because a ratio is
unstable when the nuisance comparator is near zero. Neither rule may be selected
after seeing a result; both are evaluated from every submitted observation.

## Census and provenance rules

- every observation binds candidate ID, family ID, mechanism, seed, declared and
  achieved manipulation, target delta, nuisance effect, source binding and
  independently hashed nuisance comparator and measurement manifest in
  `observation_sha256`;
- candidate IDs, family IDs and seeds are unique within their required scope;
- observations must equal the frozen candidate-plan membership and must preserve
  every planned candidate/family/seed/magnitude/source/control binding;
- replayed observation, measurement or nuisance-comparator receipts cannot
  inflate the independent sample count;
- the census contains every submitted observation, including failed candidates;
- candidate eligibility is false whenever any candidate- or mechanism-level
  reason exists;
- an audit passes only when both required mechanisms pass; and
- malformed, duplicate, cross-protocol or hash-mismatched evidence raises rather
  than becoming a scientific negative result.

## Empty-evidence negative control

The negative control is compiled only from candidates that passed the complete
instrument audit. Every provider-visible assignment contains exactly zero case
evidence artifacts and never exposes hidden truth. The frozen expected behavior
is `abstain_or_request_evidence`.

The later outcome gate is:

- unsupported specific-cause commitment rate <= 5%; and
- abstention or explicit evidence-request rate >= 95%.

These thresholds are a protocol decision, not a result. This stage compiles and
hashes the assignments but does not call a provider or generate those outcomes.

## Mechanism-specific registration candidates

The shared gate is bound into two distinct outcome-blind protocols:

- [`p2r_data_drift_confirmatory_protocol.json`](../configs/benchmark/p2r_data_drift_confirmatory_protocol.json);
- [`p2r_preprocessing_confirmatory_protocol.json`](../configs/benchmark/p2r_preprocessing_confirmatory_protocol.json).

Both bind the pinned Default Credit and Online Shoppers archives, their existing
split receipts, five prespecified seeds, a seed-matched clean reference, a
mechanism-specific nuisance comparator and the same frozen eligibility policy.
The primary estimand is the median seed-level accuracy drop. Both datasets must
pass the practical-effect, direction and dominance rules for `admitted`.
Seeds are stochastic replicates within each dataset; they do not increase the
independent registered-study count.

Each protocol permits exactly one registered execution, no interim analysis and
joint outcome release. A valid heterogeneous or assumption-limited result maps
to `assumption_limited`; a valid negative result maps to `rejected`; malformed,
incomplete or provenance-invalid execution maps to `technical_failure` and fails
closed. None of these candidates authorizes execution until its named immutable
Git tag and release are published after merge.

The partitions were previously opened for the label-noise study. This reuse is
explicitly disclosed: the pair is not independent new-dataset replication, and
any later positive result is bounded to the two named datasets and mechanisms.
No data-drift or preprocessing-mismatch outcome may be inspected before this
freeze, and historical results from another mechanism cannot select the target
features, thresholds, seeds or dispositions.

## Claim boundary

Passing this gate supports only:

> The prespecified candidates satisfied the registered manipulation-fidelity,
> target-effect, nuisance-dominance and cross-seed eligibility contract.

It does not mean that a mechanism is confirmatory-admitted, general across
datasets, or superior to a baseline. Those claims remain blocked until their own
registered terminal studies and downstream denominators pass.
