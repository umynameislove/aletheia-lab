"""Unit tests for P2R manipulation and dominant-cause eligibility."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.instrument_validity import (
    EmptyEvidenceAssignment,
    InstrumentCandidatePlan,
    InstrumentEligibilityError,
    InstrumentValidityError,
    ManipulationObservation,
    PlannedInstrumentCandidate,
    assess_instrument_validity,
    build_manipulation_observation,
    compile_empty_evidence_protocol,
    load_instrument_validity_protocol,
    require_instrument_validity,
    verify_instrument_validity_protocol,
)


def _id(prefix: str, marker: int) -> str:
    return f"{prefix}-{marker:064x}"


def _observation(
    mechanism: str,
    seed: int,
    *,
    achieved: float = 0.205,
    delta: float = -0.03,
    nuisance: float = 0.01,
) -> ManipulationObservation:
    offset = 0 if mechanism == "data_drift" else 100
    marker = offset + seed + 1
    return build_manipulation_observation(
        candidate_id=_id("p2-candidate", marker),
        case_family_id=_id("p2-family", marker),
        fault_type=mechanism,  # type: ignore[arg-type]
        seed=seed,
        declared_manipulation_magnitude=0.2,
        achieved_manipulation_magnitude=achieved,
        target_metric_delta=delta,
        nuisance_effect_magnitude=nuisance,
        source_binding_sha256=f"{offset + 10:064x}",
        nuisance_comparator_sha256=f"{marker + 500:064x}",
        measurement_manifest_sha256=f"{marker + 1000:064x}",
    )


def _passing_observations() -> tuple[ManipulationObservation, ...]:
    return tuple(
        _observation(mechanism, seed)
        for mechanism in ("data_drift", "preprocessing_bug")
        for seed in range(5)
    )


def _plan(
    observations: tuple[ManipulationObservation, ...],
) -> InstrumentCandidatePlan:
    protocol = load_instrument_validity_protocol()
    return InstrumentCandidatePlan(
        protocol_sha256=protocol.canonical_sha256(),
        entries=tuple(
            PlannedInstrumentCandidate(
                candidate_id=item.candidate_id,
                case_family_id=item.case_family_id,
                fault_type=item.fault_type,  # type: ignore[arg-type]
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


def test_frozen_protocol_is_outcome_blind_and_exact() -> None:
    protocol = verify_instrument_validity_protocol(load_instrument_validity_protocol())

    assert protocol.required_mechanisms == ("data_drift", "preprocessing_bug")
    assert protocol.dominant_cause.minimum_independent_seeds == 5
    assert protocol.dominant_cause.minimum_expected_direction_fraction == 0.8
    assert protocol.confirmatory_outcome_generation_authorized is False
    assert protocol.empty_evidence_negative_control.diagnosis_visible_artifact_count == 0


def test_passing_census_reconciles_every_candidate_and_mechanism() -> None:
    protocol = load_instrument_validity_protocol()
    audit = assess_instrument_validity(
        protocol=protocol,
        candidate_plan=_plan(_passing_observations()),
        observations=_passing_observations(),
    )

    assert audit.passed
    assert len(audit.candidate_decisions) == 10
    assert tuple(item.fault_type for item in audit.mechanism_decisions) == (
        "data_drift",
        "preprocessing_bug",
    )
    assert all(item.expected_direction_fraction == 1.0 for item in audit.mechanism_decisions)
    assert all(item.eligible_dominant_cause for item in audit.candidate_decisions)
    assert len(audit.eligible_family_ids) == 10


def test_manipulation_fidelity_failure_is_retained_in_candidate_census() -> None:
    observations = list(_passing_observations())
    observations[0] = _observation("data_drift", 0, achieved=0.25)

    audit = assess_instrument_validity(
        protocol=load_instrument_validity_protocol(),
        candidate_plan=_plan(tuple(observations)),
        observations=tuple(observations),
    )

    decision = next(
        item for item in audit.candidate_decisions if item.candidate_id == observations[0].candidate_id
    )
    assert decision.reason_codes == ("manipulation_fidelity_failed",)
    assert not decision.eligible_dominant_cause
    assert audit.passed  # Other prespecified candidates still establish the mechanism gate.


def test_all_nuisance_dominated_candidates_fail_closed() -> None:
    observations = tuple(
        _observation(
            mechanism,
            seed,
            delta=-0.02,
            nuisance=0.019,
        )
        if mechanism == "data_drift"
        else _observation(mechanism, seed)
        for mechanism in ("data_drift", "preprocessing_bug")
        for seed in range(5)
    )

    audit = assess_instrument_validity(
        protocol=load_instrument_validity_protocol(),
        candidate_plan=_plan(observations),
        observations=observations,
    )

    drift = audit.mechanism_decisions[0]
    assert not drift.passed
    assert drift.reason_codes == ("no_dominant_cause_candidate",)
    assert all(
        "nuisance_dominance_failed" in item.reason_codes
        for item in audit.candidate_decisions
        if item.fault_type == "data_drift"
    )
    with pytest.raises(InstrumentEligibilityError) as error:
        require_instrument_validity(audit)
    assert error.value.audit == audit


def test_direction_instability_excludes_the_whole_mechanism() -> None:
    observations = tuple(
        _observation(mechanism, seed, delta=(-0.03 if seed < 3 else 0.0))
        if mechanism == "data_drift"
        else _observation(mechanism, seed)
        for mechanism in ("data_drift", "preprocessing_bug")
        for seed in range(5)
    )

    audit = assess_instrument_validity(
        protocol=load_instrument_validity_protocol(),
        candidate_plan=_plan(observations),
        observations=observations,
    )

    drift = audit.mechanism_decisions[0]
    assert drift.expected_direction_fraction == 0.6
    assert drift.reason_codes == ("mechanism_direction_unstable",)
    assert not drift.eligible_candidate_ids
    assert all(
        not item.eligible_dominant_cause
        for item in audit.candidate_decisions
        if item.fault_type == "data_drift"
    )


def test_insufficient_independent_seeds_is_not_a_warning() -> None:
    observations = tuple(
        _observation(mechanism, seed)
        for mechanism, count in (("data_drift", 4), ("preprocessing_bug", 5))
        for seed in range(count)
    )
    audit = assess_instrument_validity(
        protocol=load_instrument_validity_protocol(),
        candidate_plan=_plan(observations),
        observations=observations,
    )

    assert audit.mechanism_decisions[0].reason_codes == (
        "insufficient_independent_seeds",
    )
    assert not audit.passed


def test_duplicate_candidate_family_or_seed_cannot_inflate_the_census() -> None:
    protocol = load_instrument_validity_protocol()
    observations = list(_passing_observations())
    observations[-1] = observations[0]
    with pytest.raises(InstrumentValidityError, match="repeat a candidate"):
        assess_instrument_validity(
            protocol=protocol,
            candidate_plan=_plan(_passing_observations()),
            observations=tuple(observations),
        )

    observations = list(_passing_observations())
    observations[-1] = build_manipulation_observation(
        candidate_id=_id("p2-candidate", 999),
        case_family_id=_id("p2-family", 999),
        fault_type="preprocessing_bug",
        seed=3,
        declared_manipulation_magnitude=0.2,
        achieved_manipulation_magnitude=0.2,
        target_metric_delta=-0.03,
        nuisance_effect_magnitude=0.01,
        source_binding_sha256="a" * 64,
        nuisance_comparator_sha256="b" * 64,
        measurement_manifest_sha256="c" * 64,
    )
    with pytest.raises(ValidationError, match="unique seeds"):
        _plan(tuple(observations))


def test_measurement_or_nuisance_receipt_replay_cannot_inflate_independent_n() -> None:
    protocol = load_instrument_validity_protocol()
    observations = list(_passing_observations())
    source = observations[0]
    target = observations[1]
    observations[1] = build_manipulation_observation(
        candidate_id=target.candidate_id,
        case_family_id=target.case_family_id,
        fault_type=target.fault_type,
        seed=target.seed,
        declared_manipulation_magnitude=target.declared_manipulation_magnitude,
        achieved_manipulation_magnitude=target.achieved_manipulation_magnitude,
        target_metric_delta=target.target_metric_delta,
        nuisance_effect_magnitude=target.nuisance_effect_magnitude,
        source_binding_sha256=target.source_binding_sha256,
        nuisance_comparator_sha256=target.nuisance_comparator_sha256,
        measurement_manifest_sha256=source.measurement_manifest_sha256,
    )
    with pytest.raises(InstrumentValidityError, match="measurement manifest"):
        assess_instrument_validity(
            protocol=protocol,
            candidate_plan=_plan(tuple(observations)),
            observations=tuple(observations),
        )

    observations = list(_passing_observations())
    source = observations[0]
    target = observations[1]
    observations[1] = build_manipulation_observation(
        candidate_id=target.candidate_id,
        case_family_id=target.case_family_id,
        fault_type=target.fault_type,
        seed=target.seed,
        declared_manipulation_magnitude=target.declared_manipulation_magnitude,
        achieved_manipulation_magnitude=target.achieved_manipulation_magnitude,
        target_metric_delta=target.target_metric_delta,
        nuisance_effect_magnitude=target.nuisance_effect_magnitude,
        source_binding_sha256=target.source_binding_sha256,
        nuisance_comparator_sha256=source.nuisance_comparator_sha256,
        measurement_manifest_sha256=target.measurement_manifest_sha256,
    )
    with pytest.raises(InstrumentValidityError, match="nuisance comparator"):
        assess_instrument_validity(
            protocol=protocol,
            candidate_plan=_plan(tuple(observations)),
            observations=tuple(observations),
        )


def test_provenance_hash_mismatch_is_technical_invalidity() -> None:
    observation = _passing_observations()[0]
    payload = observation.model_dump(mode="json")
    payload["measurement_manifest_sha256"] = "f" * 64

    with pytest.raises(ValidationError, match="observation_sha256"):
        ManipulationObservation.model_validate_json(json.dumps(payload))


def test_extra_or_missing_mechanism_is_rejected_before_scientific_verdict() -> None:
    only_drift = tuple(_observation("data_drift", seed) for seed in range(5))
    with pytest.raises(InstrumentValidityError, match="frozen candidate-plan membership"):
        assess_instrument_validity(
            protocol=load_instrument_validity_protocol(),
            candidate_plan=_plan(_passing_observations()),
            observations=only_drift,
        )


def test_empty_evidence_protocol_has_zero_visible_artifacts_and_is_hash_bound() -> None:
    protocol = load_instrument_validity_protocol()
    audit = assess_instrument_validity(
        protocol=protocol,
        candidate_plan=_plan(_passing_observations()),
        observations=_passing_observations(),
    )

    negative_control = compile_empty_evidence_protocol(protocol=protocol, audit=audit)

    assert len(negative_control.assignments) == 10
    assert all(not item.visible_evidence_artifact_ids for item in negative_control.assignments)
    assert all(not item.hidden_truth_available_to_provider for item in negative_control.assignments)
    assert negative_control.source_instrument_audit_sha256 == audit.canonical_sha256()
    assert negative_control.confirmatory_execution_authorized is False


def test_empty_evidence_assignment_rejects_even_one_visible_artifact() -> None:
    with pytest.raises(ValidationError, match="zero visible artifacts"):
        EmptyEvidenceAssignment(
            candidate_id=_id("p2-candidate", 1),
            case_family_id=_id("p2-family", 1),
            fault_type="data_drift",
            visible_evidence_artifact_ids=("artifact-1",),
        )


def test_empty_evidence_protocol_cannot_compile_from_failed_or_cross_protocol_audit() -> None:
    protocol = load_instrument_validity_protocol()
    observations = tuple(
        _observation(mechanism, seed, delta=(0.0 if mechanism == "data_drift" else -0.03))
        for mechanism in ("data_drift", "preprocessing_bug")
        for seed in range(5)
    )
    failed = assess_instrument_validity(
        protocol=protocol,
        candidate_plan=_plan(observations),
        observations=observations,
    )
    with pytest.raises(InstrumentEligibilityError):
        compile_empty_evidence_protocol(protocol=protocol, audit=failed)

    passing = assess_instrument_validity(
        protocol=protocol,
        candidate_plan=_plan(_passing_observations()),
        observations=_passing_observations(),
    )
    rebound = passing.model_copy(update={"protocol_sha256": "f" * 64})
    with pytest.raises(InstrumentValidityError, match="another protocol"):
        compile_empty_evidence_protocol(protocol=protocol, audit=rebound)
