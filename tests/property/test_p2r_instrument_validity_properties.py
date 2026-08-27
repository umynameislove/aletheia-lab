"""Generative invariants for the P2R instrument-validity gate."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from aletheia_lab.benchmark.p2.instrument_validity import (
    InstrumentCandidatePlan,
    PlannedInstrumentCandidate,
    assess_instrument_validity,
    build_manipulation_observation,
    load_instrument_validity_protocol,
)


def _observation(mechanism: str, seed: int, *, achieved: float = 0.2):  # type: ignore[no-untyped-def]
    offset = 0 if mechanism == "data_drift" else 100
    marker = offset + seed + 1
    return build_manipulation_observation(
        candidate_id=f"p2-candidate-{marker:064x}",
        case_family_id=f"p2-family-{marker:064x}",
        fault_type=mechanism,  # type: ignore[arg-type]
        seed=seed,
        declared_manipulation_magnitude=0.2,
        achieved_manipulation_magnitude=achieved,
        target_metric_delta=-0.03,
        nuisance_effect_magnitude=0.01,
        source_binding_sha256=f"{offset + 10:064x}",
        nuisance_comparator_sha256=f"{marker + 500:064x}",
        measurement_manifest_sha256=f"{marker + 1000:064x}",
    )


def _observations(*, first_achieved: float = 0.2):  # type: ignore[no-untyped-def]
    return tuple(
        _observation(
            mechanism,
            seed,
            achieved=(first_achieved if mechanism == "data_drift" and seed == 0 else 0.2),
        )
        for mechanism in ("data_drift", "preprocessing_bug")
        for seed in range(5)
    )


def _plan(observations) -> InstrumentCandidatePlan:  # type: ignore[no-untyped-def]
    protocol = load_instrument_validity_protocol()
    return InstrumentCandidatePlan(
        protocol_sha256=protocol.canonical_sha256(),
        entries=tuple(
            PlannedInstrumentCandidate(
                candidate_id=item.candidate_id,
                case_family_id=item.case_family_id,
                fault_type=item.fault_type,
                candidate_role=item.candidate_role,
                seed=item.seed,
                declared_manipulation_magnitude=item.declared_manipulation_magnitude,
                source_binding_sha256=item.source_binding_sha256,
                nuisance_comparator_sha256=item.nuisance_comparator_sha256,
                measurement_manifest_sha256=item.measurement_manifest_sha256,
            )
            for item in sorted(observations, key=lambda value: value.candidate_id)
        ),
        frozen_before_outcomes=True,
        model_fitted=False,
        predictive_metrics_generated=False,
    )


@given(st.permutations(_observations()))
def test_observation_order_cannot_change_audit_identity(permuted) -> None:  # type: ignore[no-untyped-def]
    protocol = load_instrument_validity_protocol()
    observations = _observations()
    plan = _plan(observations)
    reference = assess_instrument_validity(
        protocol=protocol,
        candidate_plan=plan,
        observations=observations,
    )
    actual = assess_instrument_validity(
        protocol=protocol,
        candidate_plan=plan,
        observations=tuple(permuted),
    )
    assert actual == reference
    assert actual.canonical_sha256() == reference.canonical_sha256()


@given(
    st.floats(min_value=0.18, max_value=0.22, allow_nan=False, allow_infinity=False),
)
def test_manipulation_tolerance_is_derived_not_rounded(achieved: float) -> None:
    protocol = load_instrument_validity_protocol()
    observations = _observations(first_achieved=achieved)
    audit = assess_instrument_validity(
        protocol=protocol,
        candidate_plan=_plan(observations),
        observations=observations,
    )
    decision = next(
        item
        for item in audit.candidate_decisions
        if item.fault_type == "data_drift" and item.seed == 0
    )
    expected = abs(achieved - 0.2) <= 0.02 + 1e-12
    assert decision.eligible_dominant_cause is expected


@given(st.integers(min_value=0, max_value=2**31 - 1))
def test_content_hash_changes_when_seed_changes(seed: int) -> None:
    first = _observation("data_drift", seed)
    second = _observation("data_drift", seed + 1)
    assert first.observation_sha256 != second.observation_sha256
