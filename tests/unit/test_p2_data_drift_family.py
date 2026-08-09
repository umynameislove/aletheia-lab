"""Regression tests for the data-drift candidate package.

The package is where a frozen slot, a resampled batch, its observed rows and a
measurement meet, so it is where a forged one of the four would do the most
damage. Every trust boundary below has its own exploit test.

Two error kinds are expected, and the tests keep them apart deliberately:

* ``ValidationError`` when a single object is malformed;
* a :class:`ContractViolation` subclass when objects disagree with one another.
  The subclass names which layer caught it: ``DataDriftFamilyError`` for the
  package, ``DataDriftError`` for the mechanism underneath.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.binary_evaluation import (
    CLEAN_TEST_SET_SCHEMA_VERSION,
    PREDICTION_VECTOR_SCHEMA_VERSION,
    CleanTestSet,
    PredictionVector,
)
from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import CandidateSlot, TechnicalDispositionEntry
from aletheia_lab.benchmark.p2.data_drift import (
    DRIFT_INTERVENTION_TYPE,
    RESAMPLING_CONTROL_INTERVENTION_TYPE,
    CategoricalDriftSpec,
    DataDriftError,
    DriftEvaluationSource,
    apply_categorical_drift,
    apply_empirical_resampling_control,
    build_drift_observed_evaluation_set,
)
from aletheia_lab.benchmark.p2.data_drift_family import (
    DRIFT_CANDIDATE_PACKAGE_PROTOCOL_VERSION,
    DRIFT_CANDIDATE_PACKAGE_SCHEMA_VERSION,
    DRIFT_ELIGIBILITY_POLICY_VERSION,
    DataDriftFamilyError,
    DriftBenignControlInputs,
    DriftCandidatePackage,
    DriftFaultDirectedInputs,
    DriftPredictionEvidence,
    DriftPredictionRun,
    build_drift_candidate_package,
    build_drift_prediction_run,
    drift_prediction_run_id_for,
    validate_drift_candidate_package,
)
from aletheia_lab.benchmark.p2.identity import (
    IDENTITY_FIELD_NAMES,
    P2_CANDIDATE_PREFIX,
    P2_CANDIDATE_SCHEMA_VERSION,
    P2_FAMILY_PREFIX,
    DataDriftParameters,
    FamilyIdentity,
)
from aletheia_lab.benchmark.p2.mechanism_validation import (
    MechanismValidationError,
    validate_mechanism_candidate,
)
from aletheia_lab.benchmark.p2.validation import ContractViolation

_H = {letter: letter * 64 for letter in "abcdef"}
_HEX_0 = "0" * 64

_SNAPSHOT = "telco_customer_churn@2026-07"
_CATEGORIES = ("Month-to-month", "One year", "Two year")
_SOURCE_SIZE = 120
_OUTPUT_SIZE = 300

#: The frozen M1 grid, restated as the contract's own numbers so a silent change
#: shows up here as a failure rather than as a different experiment.
_M1_GRID: dict[str, tuple[dict[str, float] | None, int, str, str]] = {
    "M1-F1": (
        {"Month-to-month": 0.80, "One year": 0.12, "Two year": 0.08},
        1,
        "fault_directed",
        DRIFT_INTERVENTION_TYPE,
    ),
    "M1-F2": (
        {"Month-to-month": 0.90, "One year": 0.06, "Two year": 0.04},
        3,
        "fault_directed",
        DRIFT_INTERVENTION_TYPE,
    ),
    "M1-S1": (
        {"Month-to-month": 0.60, "One year": 0.20, "Two year": 0.20},
        5,
        "fault_directed",
        DRIFT_INTERVENTION_TYPE,
    ),
    "M1-I1": (
        {"Month-to-month": 0.40, "One year": 0.30, "Two year": 0.30},
        4,
        "fault_directed",
        DRIFT_INTERVENTION_TYPE,
    ),
    "M1-R1": (
        {"Month-to-month": 0.70, "One year": 0.18, "Two year": 0.12},
        2,
        "fault_directed",
        DRIFT_INTERVENTION_TYPE,
    ),
    "M1-B1": (None, 105, "designed_benign_control", RESAMPLING_CONTROL_INTERVENTION_TYPE),
}
_RESERVE_ORDER = {"M1-R1": 1, "M1-R2": 2, "M1-R3": 3}


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _ids(count: int = _SOURCE_SIZE) -> tuple[str, ...]:
    return tuple(f"{index:05d}-EVAL" for index in range(count))


def _values(count: int = _SOURCE_SIZE) -> tuple[str, ...]:
    return tuple(_CATEGORIES[index % len(_CATEGORIES)] for index in range(count))


def _labels(count: int = _SOURCE_SIZE) -> tuple[int, ...]:
    """Eighty negatives then forty positives, so label 1 is the minority."""

    return tuple(1 if index >= (count * 2) // 3 else 0 for index in range(count))


def _source(**overrides: object) -> DriftEvaluationSource:
    payload: dict[str, object] = {
        "schema_version": "p2-drift-evaluation-source/v1",
        "split": "test",
        "dataset_snapshot_id": _SNAPSHOT,
        "dataset_sha256": _H["a"],
        "model_data_split_manifest_sha256": _H["b"],
        "feature": "Contract",
        "record_ids": _ids(),
        "feature_values": _values(),
        "attested_raw_feature_matrix_sha256": _H["e"],
        "attested_raw_target_sha256": _H["f"],
        "attested_model_sha256": _H["c"],
        "attested_preprocessing_specification_sha256": _H["d"],
    }
    payload.update(overrides)
    return DriftEvaluationSource(**payload)  # type: ignore[arg-type]


def _test_set(**overrides: object) -> CleanTestSet:
    payload: dict[str, object] = {
        "schema_version": CLEAN_TEST_SET_SCHEMA_VERSION,
        "split": "test",
        "record_ids": _ids(),
        "attested_true_labels": _labels(),
        "attested_test_feature_matrix_sha256": _H["e"],
        "attested_target_sha256": _H["f"],
        "attested_split_manifest_sha256": _H["b"],
        "attested_model_sha256": _H["c"],
    }
    payload.update(overrides)
    return CleanTestSet(**payload)  # type: ignore[arg-type]


def _parameters(target: dict[str, float], output_size: int) -> DataDriftParameters:
    return DataDriftParameters(
        feature="Contract", target_distribution=target, output_size=output_size
    )


def _target_for(slot_id: str) -> dict[str, float]:
    declared, _, _, _ = _M1_GRID[slot_id]
    return declared if declared is not None else _source().observed_distribution()


def _output_size(slot_id: str) -> int:
    return _SOURCE_SIZE if slot_id == "M1-B1" else _OUTPUT_SIZE


def _identity(slot_id: str, **overrides: object) -> FamilyIdentity:
    _, seed, _, intervention_type = _M1_GRID[slot_id]
    payload: dict[str, object] = {
        "dataset_snapshot_id": _SNAPSHOT,
        "dataset_sha256": _H["a"],
        "model_data_split_manifest_sha256": _H["b"],
        "fault_type": "data_drift",
        "intervention_type": intervention_type,
        "canonical_intervention_parameters": _parameters(
            _target_for(slot_id), _output_size(slot_id)
        ),
        "seed": seed,
        "reference_construction_id": "clean-test-reference/v1",
        "injector_contract_version": "drift/v1",
        "model_specification_sha256": _H["c"],
        "preprocessing_specification_sha256": _H["d"],
        "identity_schema_version": "p2-family-identity/v1",
    }
    payload.update(overrides)
    return FamilyIdentity(**payload)  # type: ignore[arg-type]


def _slot(slot_id: str = "M1-F1", **overrides: object) -> CandidateSlot:
    _, _, role, _ = _M1_GRID[slot_id]
    reserve_order = _RESERVE_ORDER.get(slot_id)
    payload: dict[str, object] = {
        "slot_id": slot_id,
        "fault_type": "data_drift",
        "slot_kind": "reserve" if reserve_order else "primary",
        "role": role,
        "reserve_order": reserve_order,
        "identity": _identity(slot_id),
    }
    payload.update(overrides)
    return CandidateSlot(**payload)  # type: ignore[arg-type]


def _spec(slot_id: str = "M1-F1", **overrides: object) -> CategoricalDriftSpec:
    _, seed, _, _ = _M1_GRID[slot_id]
    payload: dict[str, object] = {
        "injection_id": slot_id,
        "parameters": _parameters(_target_for(slot_id), _output_size(slot_id)),
        "seed": seed,
    }
    payload.update(overrides)
    return CategoricalDriftSpec(**payload)  # type: ignore[arg-type]


def _vector(predictions: tuple[int, ...], *, role: str) -> PredictionVector:
    return PredictionVector(
        schema_version=PREDICTION_VECTOR_SCHEMA_VERSION,
        role=role,  # type: ignore[arg-type]
        predictions=tuple(int(value) for value in predictions),
    )


def _prediction_evidence(
    *,
    test_set: CleanTestSet,
    observed_set: Any,
    clean_predictions: PredictionVector,
    observed_predictions: PredictionVector,
    model_sha256: str = _H["c"],
) -> DriftPredictionEvidence:
    return DriftPredictionEvidence(
        reference_run=build_drift_prediction_run(
            role="reference",
            model_specification_sha256=model_sha256,
            evaluation_source_sha256=test_set.artifact_sha256(),
            predictions=clean_predictions,
        ),
        observed_run=build_drift_prediction_run(
            role="observed",
            model_specification_sha256=model_sha256,
            evaluation_source_sha256=observed_set.artifact_sha256(),
            predictions=observed_predictions,
        ),
    )


def _inputs(slot_id: str = "M1-F1", *, observed_wrong: int = 0, **overrides: object) -> Any:
    """Build one input bundle, with a perfectly predicting model by default."""

    source = _source()
    spec = _spec(slot_id)
    slot = _slot(slot_id)
    apply = apply_empirical_resampling_control if slot_id == "M1-B1" else apply_categorical_drift
    result = apply(source=source, spec=spec, slot=slot)
    test_set = _test_set()
    observed_set = build_drift_observed_evaluation_set(
        result=result,
        source=source,
        test_set=test_set,
        attested_drifted_feature_matrix_sha256=_H["a"],
    )
    observed = list(observed_set.true_labels)
    for index in range(observed_wrong):
        observed[index] = 1 - observed[index]
    payload: dict[str, object] = {
        "source": source,
        "spec": spec,
        "result": result,
        "test_set": test_set,
        "observed_set": observed_set,
        "predictions": _prediction_evidence(
            test_set=test_set,
            observed_set=observed_set,
            clean_predictions=_vector(_labels(), role="reference"),
            observed_predictions=_vector(tuple(observed), role="observed"),
        ),
    }
    payload.update(overrides)
    bundle = DriftBenignControlInputs if slot_id == "M1-B1" else DriftFaultDirectedInputs
    return bundle(**payload)  # type: ignore[arg-type]


def _forge(model: Any, **updates: object) -> Any:
    """Bypass validation the way a careless caller would."""

    return model.model_copy(update=updates)


@pytest.fixture
def fault_inputs() -> Any:
    return _inputs("M1-F1")


@pytest.fixture
def fault_package(fault_inputs: Any) -> DriftCandidatePackage:
    return build_drift_candidate_package(slot=_slot("M1-F1"), inputs=fault_inputs)


@pytest.fixture
def benign_inputs() -> Any:
    return _inputs("M1-B1")


@pytest.fixture
def benign_package(benign_inputs: Any) -> DriftCandidatePackage:
    return build_drift_candidate_package(slot=_slot("M1-B1"), inputs=benign_inputs)


# --------------------------------------------------------------------------- #
# Positive
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("slot_id", ["M1-F1", "M1-F2", "M1-S1", "M1-I1", "M1-R1"])
def test_every_frozen_fault_slot_packages_successfully(slot_id: str) -> None:
    inputs = _inputs(slot_id)
    package = build_drift_candidate_package(slot=_slot(slot_id), inputs=inputs)
    validate_drift_candidate_package(package, slot=_slot(slot_id), inputs=inputs)
    assert package.slot_id == slot_id
    assert package.role == "fault_directed"
    assert package.status == "validity_review_required"
    assert package.disposition.disposition == "technically_valid"


def test_a_reserve_slot_uses_the_same_entry_point() -> None:
    """No second API: the reserve slot goes through the one public builder."""

    inputs = _inputs("M1-R1")
    package = build_drift_candidate_package(slot=_slot("M1-R1"), inputs=inputs)
    validate_drift_candidate_package(package, slot=_slot("M1-R1"), inputs=inputs)
    assert package.execution.slot_kind == "reserve"


def test_the_benign_control_packages_as_technical_equivalence_pass(
    benign_inputs: Any, benign_package: DriftCandidatePackage
) -> None:
    validate_drift_candidate_package(benign_package, slot=_slot("M1-B1"), inputs=benign_inputs)
    assert benign_package.role == "designed_benign_control"
    assert benign_package.status == "equivalence_verified_pending_admission"
    assert benign_package.measurement.distribution_total_variation == 0.0
    assert benign_package.measurement.population_stability_index == 0.0
    assert benign_package.disposition.disposition == "technically_valid"


def test_unified_validator_binds_drift_to_the_shared_lifecycle(
    fault_inputs: Any, fault_package: DriftCandidatePackage
) -> None:
    binding = validate_mechanism_candidate(
        fault_package,
        slot=_slot("M1-F1"),
        inputs=fault_inputs,
        execution=fault_package.execution,
        disposition=fault_package.disposition,
    )
    assert binding.candidate_id == fault_package.candidate_id
    assert binding.fault_type == "data_drift"
    assert binding.artifact_sha256 == fault_package.artifact_package_sha256()


def test_unified_validator_rejects_a_replayed_drift_execution(
    fault_inputs: Any, fault_package: DriftCandidatePackage
) -> None:
    other_inputs = _inputs("M1-F2")
    other_package = build_drift_candidate_package(slot=_slot("M1-F2"), inputs=other_inputs)
    with pytest.raises(MechanismValidationError, match="execution does not match"):
        validate_mechanism_candidate(
            fault_package,
            slot=_slot("M1-F1"),
            inputs=fault_inputs,
            execution=other_package.execution,
            disposition=fault_package.disposition,
        )


def test_the_candidate_identity_matches_an_independently_derived_expectation(
    fault_package: DriftCandidatePackage,
) -> None:
    """Rebuild fingerprint, family ID and candidate ID from the frozen contract."""

    payload = _slot("M1-F1").identity.identity_payload()
    assert set(payload) == set(IDENTITY_FIELD_NAMES)
    fingerprint = canonical_sha256(payload)
    candidate = P2_CANDIDATE_PREFIX + canonical_sha256(
        {
            "candidate_schema_version": P2_CANDIDATE_SCHEMA_VERSION,
            "slot_id": "M1-F1",
            "proposed_family_sha256": fingerprint,
        }
    )
    assert fault_package.proposed_family_sha256 == fingerprint
    assert fault_package.family_id == P2_FAMILY_PREFIX + fingerprint
    assert fault_package.candidate_id == candidate


def test_the_family_fingerprint_does_not_move_when_the_measurement_does() -> None:
    """Identity is twelve frozen fields; a prediction vector is not one of them."""

    clean = build_drift_candidate_package(slot=_slot("M1-F1"), inputs=_inputs("M1-F1"))
    harmed = build_drift_candidate_package(
        slot=_slot("M1-F1"), inputs=_inputs("M1-F1", observed_wrong=90)
    )
    assert clean.proposed_family_sha256 == harmed.proposed_family_sha256
    assert clean.candidate_id == harmed.candidate_id
    assert clean.family_id == harmed.family_id
    assert clean.artifact_package_sha256() != harmed.artifact_package_sha256()


def test_the_same_input_produces_a_byte_identical_package(
    fault_package: DriftCandidatePackage,
) -> None:
    again = build_drift_candidate_package(slot=_slot("M1-F1"), inputs=_inputs("M1-F1"))
    assert again.artifact_package_sha256() == fault_package.artifact_package_sha256()
    assert again.model_dump(mode="json") == fault_package.model_dump(mode="json")


def test_the_caller_inputs_are_not_mutated() -> None:
    inputs = _inputs("M1-F1")
    before = inputs.model_dump(mode="json")
    build_drift_candidate_package(slot=_slot("M1-F1"), inputs=inputs)
    assert inputs.model_dump(mode="json") == before


def test_mapping_order_does_not_change_the_identity() -> None:
    """Two spellings of the same target distribution are one family."""

    reversed_target = dict(reversed(list(_target_for("M1-F1").items())))
    identity = _identity(
        "M1-F1",
        canonical_intervention_parameters=_parameters(reversed_target, _OUTPUT_SIZE),
    )
    slot = _slot("M1-F1", identity=identity)
    spec = _spec("M1-F1", parameters=_parameters(reversed_target, _OUTPUT_SIZE))
    package = build_drift_candidate_package(slot=slot, inputs=_inputs("M1-F1", spec=spec))
    assert package.proposed_family_sha256 == (
        build_drift_candidate_package(
            slot=_slot("M1-F1"), inputs=_inputs("M1-F1")
        ).proposed_family_sha256
    )


def test_the_package_pins_its_protocol_policy_and_schema(
    fault_package: DriftCandidatePackage,
) -> None:
    assert fault_package.schema_version == DRIFT_CANDIDATE_PACKAGE_SCHEMA_VERSION
    assert fault_package.package_protocol_version == DRIFT_CANDIDATE_PACKAGE_PROTOCOL_VERSION
    assert fault_package.eligibility_policy_version == DRIFT_ELIGIBILITY_POLICY_VERSION


def test_the_public_api_exports_the_package() -> None:
    import aletheia_lab.benchmark.p2 as package

    for name in (
        "DriftCandidatePackage",
        "DataDriftFamilyError",
        "DriftFaultDirectedInputs",
        "DriftBenignControlInputs",
        "DriftPredictionRun",
        "build_drift_prediction_run",
        "drift_prediction_run_id_for",
        "build_drift_candidate_package",
        "validate_drift_candidate_package",
        "MechanismValidationError",
        "ValidatedMechanismCandidate",
        "validate_mechanism_candidate",
    ):
        assert name in package.__all__
        assert hasattr(package, name)


# --------------------------------------------------------------------------- #
# 20. Academic and lifecycle boundary
# --------------------------------------------------------------------------- #


def test_no_package_field_can_hold_eligibility_family_or_diagnosis_metadata() -> None:
    names = set(DriftCandidatePackage.model_fields)
    forbidden = (
        "family_class",
        "case_family",
        "admission",
        "census",
        "evidence_condition",
        "context",
        "cause",
        "eligible",
    )
    assert not any(token in name for name in names for token in forbidden)


def test_a_shifted_distribution_alone_does_not_make_an_eligible_failure(
    fault_package: DriftCandidatePackage,
) -> None:
    """PSI proves the marginal moved; it proves nothing about the model."""

    assert fault_package.measurement.population_stability_index > 0.9
    assert fault_package.status == "validity_review_required"
    assert "eligible_failure" not in set(DriftCandidatePackage.model_fields)


def test_a_large_accuracy_drop_still_stops_at_validity_review() -> None:
    package = build_drift_candidate_package(
        slot=_slot("M1-F1"), inputs=_inputs("M1-F1", observed_wrong=250)
    )
    assert package.measurement.comparison.measured_primary_outcome == "regression"
    assert package.status == "validity_review_required"


# --------------------------------------------------------------------------- #
# 1-2. Source, dataset and system binding
# --------------------------------------------------------------------------- #


def test_a_source_from_another_dataset_snapshot_is_refused() -> None:
    with pytest.raises(ContractViolation, match="different dataset snapshot"):
        build_drift_candidate_package(
            slot=_slot("M1-F1"),
            inputs=_inputs("M1-F1", source=_source(dataset_snapshot_id="telco@2099-01")),
        )


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("dataset_sha256", "different dataset"),
        ("model_data_split_manifest_sha256", "different model-data split"),
        ("attested_model_sha256", "different model specification"),
        ("attested_preprocessing_specification_sha256", "different preprocessing"),
    ],
)
def test_a_swapped_system_hash_is_refused(field: str, message: str) -> None:
    with pytest.raises(ContractViolation, match=message):
        build_drift_candidate_package(
            slot=_slot("M1-F1"), inputs=_inputs("M1-F1", source=_source(**{field: _HEX_0}))
        )


# --------------------------------------------------------------------------- #
# 3-5. Spec, result and observed-set provenance
# --------------------------------------------------------------------------- #


def test_a_spec_from_another_slot_is_refused() -> None:
    with pytest.raises(ContractViolation):
        build_drift_candidate_package(
            slot=_slot("M1-F1"), inputs=_inputs("M1-F1", spec=_spec("M1-F2"))
        )


def test_a_drift_result_from_another_family_is_refused() -> None:
    """The batch was drawn for M1-F2; packaging it as M1-F1 must fail."""

    foreign = _inputs("M1-F2")
    with pytest.raises(ContractViolation):
        build_drift_candidate_package(
            slot=_slot("M1-F1"), inputs=_inputs("M1-F1", result=foreign.result)
        )


def test_an_observed_set_from_another_drift_artifact_is_refused() -> None:
    foreign = _inputs("M1-F2")
    with pytest.raises(ContractViolation):
        build_drift_candidate_package(
            slot=_slot("M1-F1"), inputs=_inputs("M1-F1", observed_set=foreign.observed_set)
        )


@pytest.mark.parametrize("field", ["true_labels", "selected_record_ids", "occurrence_ids"])
def test_a_tampered_observed_row_field_is_refused(field: str, fault_inputs: Any) -> None:
    observed_set = fault_inputs.observed_set
    current = getattr(observed_set, field)
    if field == "true_labels":
        replacement: tuple[Any, ...] = (1 - current[0], *current[1:])
    else:
        replacement = (current[1], current[0], *current[2:])
    forged = _forge(observed_set, **{field: replacement})
    with pytest.raises(ContractViolation):
        build_drift_candidate_package(
            slot=_slot("M1-F1"), inputs=_inputs("M1-F1", observed_set=forged)
        )


def test_a_prediction_vector_from_another_observed_batch_is_refused() -> None:
    """A complete run from another batch cannot be replayed as this run."""

    foreign = _inputs("M1-F2")
    with pytest.raises(ContractViolation, match="not bound to this observed evaluation source"):
        build_drift_candidate_package(
            slot=_slot("M1-F1"), inputs=_inputs("M1-F1", predictions=foreign.predictions)
        )


def test_a_prediction_vector_bound_to_another_clean_source_is_refused(
    fault_inputs: Any,
) -> None:
    forged_run = _forge(
        fault_inputs.predictions.reference_run,
        evaluation_source_sha256=_HEX_0,
    )
    forged = _forge(fault_inputs.predictions, reference_run=forged_run)
    with pytest.raises(ValidationError, match="run_id must be derived"):
        build_drift_candidate_package(
            slot=_slot("M1-F1"), inputs=_inputs("M1-F1", predictions=forged)
        )


def test_a_swapped_prediction_role_is_refused(fault_inputs: Any) -> None:
    with pytest.raises(ValidationError, match="vector role must match"):
        DriftPredictionEvidence(
            reference_run=_forge(
                fault_inputs.predictions.reference_run,
                predictions=_vector(_labels(), role="observed"),
            ),
            observed_run=fault_inputs.predictions.observed_run,
        )


def test_synchronized_foreign_vector_with_stale_run_identity_is_refused() -> None:
    """Regression: changing vector and source metadata cannot retain the old run ID."""

    current = _inputs("M1-F1")
    foreign = _inputs("M1-F2", observed_wrong=17)
    forged_run = _forge(
        foreign.predictions.observed_run,
        evaluation_source_sha256=current.observed_set.artifact_sha256(),
    )
    forged = _forge(current.predictions, observed_run=forged_run)
    with pytest.raises(ValidationError, match="run_id must be derived"):
        build_drift_candidate_package(
            slot=_slot("M1-F1"), inputs=_inputs("M1-F1", predictions=forged)
        )


def test_prediction_run_identity_binds_model_source_and_vector() -> None:
    inputs = _inputs("M1-F1")
    run = inputs.predictions.observed_run
    vector = run.predictions
    expected = "p2-drift-run-" + canonical_sha256(
        {
            "schema_version": "p2-drift-prediction-run/v1",
            "run_protocol_version": "attested-binary-prediction-run/v1",
            "role": "observed",
            "model_specification_sha256": _H["c"],
            "evaluation_source_sha256": inputs.observed_set.artifact_sha256(),
            "prediction_vector_sha256": vector.canonical_sha256(),
        }
    )
    assert run.run_id == expected
    assert run.run_id == drift_prediction_run_id_for(
        role="observed",
        model_specification_sha256=_H["c"],
        evaluation_source_sha256=inputs.observed_set.artifact_sha256(),
        predictions=vector,
    )


def test_prediction_run_from_another_model_is_refused(fault_inputs: Any) -> None:
    run = build_drift_prediction_run(
        role="observed",
        model_specification_sha256=_HEX_0,
        evaluation_source_sha256=fault_inputs.observed_set.artifact_sha256(),
        predictions=fault_inputs.predictions.observed_predictions,
    )
    evidence = _forge(fault_inputs.predictions, observed_run=run)
    with pytest.raises(ContractViolation, match="different model specification"):
        build_drift_candidate_package(
            slot=_slot("M1-F1"), inputs=_inputs("M1-F1", predictions=evidence)
        )


def test_prediction_run_rejects_model_construct_bypass(fault_inputs: Any) -> None:
    run = fault_inputs.predictions.observed_run
    forged = DriftPredictionRun.model_construct(
        **{**run.__dict__, "evaluation_source_sha256": _HEX_0}
    )
    evidence = _forge(fault_inputs.predictions, observed_run=forged)
    with pytest.raises(ValidationError, match="run_id must be derived"):
        build_drift_candidate_package(
            slot=_slot("M1-F1"), inputs=_inputs("M1-F1", predictions=evidence)
        )


# --------------------------------------------------------------------------- #
# 8-9. Attestation and measurement replay
# --------------------------------------------------------------------------- #


def test_a_drifted_matrix_attestation_swapped_after_measurement_is_refused(
    fault_inputs: Any, fault_package: DriftCandidatePackage
) -> None:
    forged_set = _forge(fault_inputs.observed_set, attested_drifted_feature_matrix_sha256=_HEX_0)
    with pytest.raises(ContractViolation):
        validate_drift_candidate_package(
            fault_package,
            slot=_slot("M1-F1"),
            inputs=_inputs("M1-F1", observed_set=forged_set),
        )


def test_a_measurement_cannot_be_replayed_between_candidates(
    fault_inputs: Any, fault_package: DriftCandidatePackage
) -> None:
    other = build_drift_candidate_package(slot=_slot("M1-F2"), inputs=_inputs("M1-F2"))
    forged = _forge(fault_package, measurement=other.measurement)
    with pytest.raises((ContractViolation, ValidationError)):
        validate_drift_candidate_package(forged, slot=_slot("M1-F1"), inputs=fault_inputs)


def test_a_package_cannot_be_replayed_across_slots(
    fault_package: DriftCandidatePackage,
) -> None:
    with pytest.raises((ContractViolation, ValidationError)):
        validate_drift_candidate_package(
            fault_package, slot=_slot("M1-F2"), inputs=_inputs("M1-F2")
        )


def test_a_package_mixing_nested_artifacts_from_two_runs_is_refused(
    fault_inputs: Any, fault_package: DriftCandidatePackage
) -> None:
    """The drift artifact of one run with the observed set of another."""

    other = _inputs("M1-F2")
    with pytest.raises((ContractViolation, ValidationError)):
        validate_drift_candidate_package(
            fault_package,
            slot=_slot("M1-F1"),
            inputs=_inputs("M1-F1", result=other.result, observed_set=other.observed_set),
        )


# --------------------------------------------------------------------------- #
# 10-12. Role and control promotion
# --------------------------------------------------------------------------- #


def test_a_fault_directed_bundle_cannot_package_the_benign_slot(fault_inputs: Any) -> None:
    with pytest.raises(DataDriftFamilyError, match="cannot package"):
        build_drift_candidate_package(slot=_slot("M1-B1"), inputs=fault_inputs)


def test_a_benign_bundle_cannot_package_a_fault_slot(benign_inputs: Any) -> None:
    with pytest.raises(DataDriftFamilyError, match="cannot package"):
        build_drift_candidate_package(slot=_slot("M1-F1"), inputs=benign_inputs)


def test_a_fault_directed_candidate_cannot_claim_a_benign_pass(
    fault_inputs: Any, fault_package: DriftCandidatePackage
) -> None:
    forged = _forge(fault_package, status="equivalence_verified_pending_admission")
    with pytest.raises(ValidationError, match="cannot claim equivalence"):
        validate_drift_candidate_package(forged, slot=_slot("M1-F1"), inputs=fault_inputs)


def test_a_benign_equivalence_failure_becomes_a_technical_rejection() -> None:
    inputs = _inputs("M1-B1", observed_wrong=9)
    package = build_drift_candidate_package(slot=_slot("M1-B1"), inputs=inputs)
    validate_drift_candidate_package(package, slot=_slot("M1-B1"), inputs=inputs)
    assert package.measurement.status == "benign_equivalence_failure"
    assert package.status == "technically_rejected"
    assert package.disposition.disposition == "technical_rejected"
    assert package.disposition.rejection_reason == "benign_equivalence_failure"


def test_a_benign_failure_cannot_be_relabelled_as_a_pass() -> None:
    inputs = _inputs("M1-B1", observed_wrong=9)
    package = build_drift_candidate_package(slot=_slot("M1-B1"), inputs=inputs)
    forged = _forge(
        package,
        status="equivalence_verified_pending_admission",
        disposition=TechnicalDispositionEntry(
            candidate_id=package.candidate_id, disposition="technically_valid"
        ),
    )
    with pytest.raises(ValidationError, match="never relabelled stable"):
        validate_drift_candidate_package(forged, slot=_slot("M1-B1"), inputs=inputs)


def test_a_benign_failure_cannot_be_relabelled_as_an_eligible_failure() -> None:
    inputs = _inputs("M1-B1", observed_wrong=9)
    package = build_drift_candidate_package(slot=_slot("M1-B1"), inputs=inputs)
    with pytest.raises(ValidationError, match="Extra inputs"):
        DriftCandidatePackage(
            **{**package.model_dump(), "family_class": "eligible_failure"}  # type: ignore[arg-type]
        )


def test_metric_equivalence_without_distribution_equivalence_is_still_a_failure() -> None:
    """A benign control needs both gates; either one alone is not enough.

    The M1-F1 target shifts the marginal hard, so distribution equivalence
    fails while the predictions stay identical and the metrics agree exactly.
    """

    source = _source()
    spec = _spec("M1-B1", parameters=_parameters(_target_for("M1-F1"), _SOURCE_SIZE))
    slot = _slot(
        "M1-B1",
        identity=_identity(
            "M1-B1",
            canonical_intervention_parameters=_parameters(_target_for("M1-F1"), _SOURCE_SIZE),
        ),
    )
    with pytest.raises(ContractViolation, match="clean empirical distribution"):
        apply_empirical_resampling_control(source=source, spec=spec, slot=slot)


def test_a_benign_control_with_a_shifted_batch_cannot_be_packaged_as_verified(
    benign_inputs: Any, benign_package: DriftCandidatePackage
) -> None:
    forged = _forge(
        benign_package,
        measurement=_forge(benign_package.measurement, distribution_total_variation=0.5),
    )
    with pytest.raises(ValidationError, match="derived from the recomputed metrics"):
        validate_drift_candidate_package(forged, slot=_slot("M1-B1"), inputs=benign_inputs)


# --------------------------------------------------------------------------- #
# 14-18. Identity, digest and schema tamper
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("proposed_family_sha256", _HEX_0),
        ("candidate_id", P2_CANDIDATE_PREFIX + _HEX_0),
        ("family_id", P2_FAMILY_PREFIX + _HEX_0),
        ("slot_sha256", _HEX_0),
        ("drift_artifact_sha256", _HEX_0),
        ("source_artifact_sha256", _HEX_0),
        ("spec_sha256", _HEX_0),
        ("observed_evaluation_source_sha256", _HEX_0),
        ("reference_evaluation_source_sha256", _HEX_0),
        ("reference_prediction_run_sha256", _HEX_0),
        ("observed_prediction_run_sha256", _HEX_0),
        ("dataset_sha256", _HEX_0),
        ("model_specification_sha256", _HEX_0),
    ],
)
def test_a_forged_identity_or_binding_field_is_refused(
    field: str, value: str, fault_inputs: Any, fault_package: DriftCandidatePackage
) -> None:
    forged = _forge(fault_package, **{field: value})
    with pytest.raises((ContractViolation, ValidationError)):
        validate_drift_candidate_package(forged, slot=_slot("M1-F1"), inputs=fault_inputs)


def test_a_deeply_nested_change_moves_the_package_digest(
    fault_package: DriftCandidatePackage,
) -> None:
    """The digest binds nested fields, not just the top level.

    Only one confusion cell three levels down changes here; a shallow digest
    would report the two packages as identical.
    """

    measurement = fault_package.measurement
    deeper = _forge(
        measurement,
        comparison=_forge(
            measurement.comparison,
            observed=_forge(
                measurement.comparison.observed,
                confusion=_forge(measurement.comparison.observed.confusion, true_positive=7),
            ),
        ),
    )
    forged = _forge(fault_package, measurement=deeper)
    assert forged.artifact_package_sha256() != fault_package.artifact_package_sha256()


def test_an_extra_field_is_refused(fault_package: DriftCandidatePackage) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        DriftCandidatePackage(
            **{**fault_package.model_dump(), "diagnosis_context_id": "x"}  # type: ignore[arg-type]
        )


def test_an_extra_nested_field_is_refused(fault_package: DriftCandidatePackage) -> None:
    payload = fault_package.model_dump()
    payload["execution"]["evidence_condition"] = "full"
    with pytest.raises(ValidationError, match="Extra inputs"):
        DriftCandidatePackage(**payload)  # type: ignore[arg-type]


def test_a_wrong_schema_version_is_refused(fault_package: DriftCandidatePackage) -> None:
    with pytest.raises(ValidationError):
        DriftCandidatePackage(
            **{**fault_package.model_dump(), "schema_version": "p2-drift-candidate-package/v2"}  # type: ignore[arg-type]
        )


def test_a_malformed_hash_is_refused(fault_package: DriftCandidatePackage) -> None:
    with pytest.raises(ValidationError):
        DriftCandidatePackage(
            **{**fault_package.model_dump(), "drift_artifact_sha256": "not-a-hash"}  # type: ignore[arg-type]
        )


def test_a_non_finite_metric_cannot_enter_a_package(
    fault_package: DriftCandidatePackage,
) -> None:
    measurement = fault_package.measurement
    forged = _forge(
        fault_package,
        measurement=_forge(
            measurement,
            comparison=_forge(measurement.comparison, accuracy_delta=float("nan")),
        ),
    )
    with pytest.raises(ValidationError):
        type(forged).model_validate(forged.model_dump())


def test_a_package_forged_with_model_copy_is_refused(
    fault_inputs: Any, fault_package: DriftCandidatePackage
) -> None:
    forged = _forge(fault_package, status="technically_rejected")
    with pytest.raises(ValidationError):
        validate_drift_candidate_package(forged, slot=_slot("M1-F1"), inputs=fault_inputs)


def test_a_package_forged_with_model_construct_is_refused(
    fault_inputs: Any, fault_package: DriftCandidatePackage
) -> None:
    forged = DriftCandidatePackage.model_construct(
        **{**fault_package.__dict__, "proposed_family_sha256": _HEX_0}
    )
    with pytest.raises((ContractViolation, ValidationError)):
        validate_drift_candidate_package(forged, slot=_slot("M1-F1"), inputs=fault_inputs)


def test_the_package_cannot_be_mutated(fault_package: DriftCandidatePackage) -> None:
    with pytest.raises(ValidationError):
        fault_package.status = "technically_rejected"
    with pytest.raises(ValidationError):
        fault_package.measurement.status = "benign_equivalence_failure"
    with pytest.raises(ValidationError):
        fault_package.execution.candidate_id = "x"


# --------------------------------------------------------------------------- #
# 19. Phase 1 namespace
# --------------------------------------------------------------------------- #


def test_a_phase_one_snapshot_cannot_seed_a_drift_package() -> None:
    with pytest.raises(ValidationError, match="Phase 1 identifiers"):
        _identity("M1-F1", dataset_snapshot_id="p1-telco@2026-01")


def test_a_phase_one_record_identifier_cannot_enter_a_drift_package() -> None:
    with pytest.raises(ValidationError, match="Phase 1"):
        _source(record_ids=("p1-case-0001", *_ids()[1:]))


def test_no_phase_one_identifier_appears_in_a_built_package(
    fault_package: DriftCandidatePackage,
) -> None:
    serialized = fault_package.model_dump_json()
    assert "p1-" not in serialized.lower()


def test_a_slot_from_another_mechanism_is_refused(fault_inputs: Any) -> None:
    with pytest.raises((ContractViolation, ValidationError)):
        build_drift_candidate_package(
            slot=_slot("M1-F1", fault_type="label_noise"), inputs=fault_inputs
        )


def test_a_drift_error_from_the_mechanism_layer_surfaces_unchanged() -> None:
    """The package does not swallow the mechanism's own rejection."""

    with pytest.raises(DataDriftError):
        build_drift_candidate_package(
            slot=_slot("M1-F1"),
            inputs=_inputs("M1-F1", result=_inputs("M1-S1").result),
        )
