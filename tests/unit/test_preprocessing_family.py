"""Regression tests for the preprocessing candidate package.

The package is the point where a frozen slot, an artifact and a measurement meet,
so it is the point where a forged one of any of the three would do the most
damage. Every invariant below is covered twice: once by a positive case showing
the intended behaviour, and once by a forged artifact showing that the
authoritative validator rejects a plausible-looking fake.

Two error kinds are expected, and the tests keep them apart deliberately:

* ``ValidationError`` when a single object is malformed;
* ``PreprocessingFamilyError`` when objects disagree with one another.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError
from sklearn.metrics import accuracy_score

from aletheia_lab.baseline.preprocess import build_preprocessor
from aletheia_lab.baseline.schema import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    NUMERIC_FEATURES,
)
from aletheia_lab.benchmark.p2.binary_evaluation import (
    CLEAN_TEST_SET_SCHEMA_VERSION,
    PREDICTION_VECTOR_SCHEMA_VERSION,
    CleanTestSet,
    PredictionVector,
)
from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import CandidateSlot, TechnicalDispositionEntry
from aletheia_lab.benchmark.p2.identity import (
    IDENTITY_FIELD_NAMES,
    P2_CANDIDATE_PREFIX,
    P2_CANDIDATE_SCHEMA_VERSION,
    FamilyIdentity,
    PreprocessingBugParameters,
)
from aletheia_lab.benchmark.p2.preprocessing_controls import (
    BENIGN_EVIDENCE_SCHEMA_VERSION,
    ENCODER_REPAIR_INTERVENTION_TYPE,
    NAMED_FEATURE_TABLE_SCHEMA_VERSION,
    PERMUTATION_INTERVENTION_TYPE,
    TRANSFORMED_MATRIX_SCHEMA_VERSION,
    BenignEquivalenceEvidence,
    ColumnPermutationSpec,
    EncoderMappingRepairSpec,
    NamedFeatureRow,
    NamedFeatureTable,
    TransformedMatrix,
    apply_column_permutation,
    apply_encoder_mapping_repair,
    permutation_order,
)
from aletheia_lab.benchmark.p2.preprocessing_family import (
    CANDIDATE_PACKAGE_PROTOCOL_VERSION,
    CANDIDATE_PACKAGE_SCHEMA_VERSION,
    ELIGIBILITY_POLICY_VERSION,
    BenignControlInputs,
    FaultDirectedInputs,
    PreprocessingCandidatePackage,
    PreprocessingFamilyError,
    RepairControlInputs,
    build_preprocessing_candidate_package,
    validate_preprocessing_candidate_package,
)
from aletheia_lab.benchmark.p2.preprocessing_intervention import (
    CATEGORY_RANK_RULE,
    CATEGORY_VOCABULARY_SCHEMA_VERSION,
    INFERENCE_SOURCE_SCHEMA_VERSION,
    MISMATCH_INTERVENTION_TYPE,
    CategoryFrequency,
    EncoderMappingMismatchSpec,
    FrozenCategoryVocabulary,
    InferenceTransformSource,
    apply_encoder_mapping_mismatch,
)
from aletheia_lab.benchmark.p2.validation import ContractViolation

_H = {letter: letter * 64 for letter in "abcdef"}
_HEX_0 = "0" * 64

_COUNTS: dict[str, int] = {"Month-to-month": 3875, "Two year": 1695, "One year": 1473}
_RANK = {1: "Month-to-month", 2: "Two year", 3: "One year"}

#: Twelve evaluation rows covering all three categories, with eight negatives and
#: four positives so label 1 is the minority the alpha protocol requires.
_RAW: tuple[str, ...] = (
    "Month-to-month",
    "One year",
    "Two year",
    "Month-to-month",
    "Two year",
    "One year",
    "Month-to-month",
    "One year",
    "Two year",
    "Month-to-month",
    "One year",
    "Two year",
)
_IDS = tuple(f"{index:05d}-TEST" for index in range(len(_RAW)))
_LABELS: tuple[int, ...] = (0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1)

#: The frozen M3 grid, restated as the contract's own numbers so a silent change
#: shows up here as a failure rather than as a different experiment.
_GRID: dict[str, tuple[int | None, int | None, int, str, str]] = {
    "M3-F1": (3, 2, 301, "fault_directed", MISMATCH_INTERVENTION_TYPE),
    "M3-F2": (2, 1, 302, "fault_directed", MISMATCH_INTERVENTION_TYPE),
    "M3-F3": (1, 3, 303, "fault_directed", MISMATCH_INTERVENTION_TYPE),
    "M3-I1": (1, 3, 304, "designed_improvement_control", ENCODER_REPAIR_INTERVENTION_TYPE),
    "M3-B1": (None, None, 305, "designed_benign_control", PERMUTATION_INTERVENTION_TYPE),
    "M3-R1": (3, 1, 306, "fault_directed", MISMATCH_INTERVENTION_TYPE),
}
_RESERVE_ORDER: dict[str, int] = {"M3-R1": 1, "M3-R2": 2, "M3-R3": 3}


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _vocabulary() -> FrozenCategoryVocabulary:
    return FrozenCategoryVocabulary(
        schema_version=CATEGORY_VOCABULARY_SCHEMA_VERSION,
        feature="Contract",
        split="train",
        rank_rule=CATEGORY_RANK_RULE,
        frequencies=tuple(
            CategoryFrequency(category=name, count=count) for name, count in _COUNTS.items()
        ),
    )


def _source(**overrides: object) -> InferenceTransformSource:
    payload: dict[str, object] = {
        "schema_version": INFERENCE_SOURCE_SCHEMA_VERSION,
        "split": "test",
        "feature": "Contract",
        "record_ids": _IDS,
        "raw_categories": _RAW,
        "vocabulary": _vocabulary(),
        "attested_raw_feature_matrix_sha256": _H["a"],
        "attested_raw_target_sha256": _H["b"],
        "attested_model_sha256": _H["c"],
        "attested_fitted_training_transform_sha256": _H["d"],
        "attested_other_transform_config_sha256": _H["e"],
    }
    payload.update(overrides)
    return InferenceTransformSource(**payload)  # type: ignore[arg-type]


def _test_set(**overrides: object) -> CleanTestSet:
    payload: dict[str, object] = {
        "schema_version": CLEAN_TEST_SET_SCHEMA_VERSION,
        "split": "test",
        "record_ids": _IDS,
        "attested_true_labels": _LABELS,
        "attested_test_feature_matrix_sha256": _H["a"],
        "attested_target_sha256": _H["b"],
        "attested_split_manifest_sha256": _H["f"],
        "attested_model_sha256": _H["c"],
    }
    payload.update(overrides)
    return CleanTestSet(**payload)  # type: ignore[arg-type]


def _parameters(source_rank: int | None, mapped_rank: int | None) -> PreprocessingBugParameters:
    return PreprocessingBugParameters(
        target_feature="Contract",
        source_rank=source_rank,
        mapped_rank=mapped_rank,
        mode="inference_only",
        transform_name="one_hot_encoder",
    )


def _identity(
    parameters: PreprocessingBugParameters, seed: int, intervention_type: str, **overrides: object
) -> FamilyIdentity:
    payload: dict[str, object] = {
        "dataset_snapshot_id": "telco_customer_churn@2026-07",
        "dataset_sha256": _H["a"],
        "model_data_split_manifest_sha256": _H["b"],
        "fault_type": "preprocessing_bug",
        "intervention_type": intervention_type,
        "canonical_intervention_parameters": parameters,
        "seed": seed,
        "reference_construction_id": "clean-test-reference/v1",
        "injector_contract_version": "preprocessing/v1",
        "model_specification_sha256": _H["c"],
        "preprocessing_specification_sha256": _H["d"],
        "identity_schema_version": "p2-family-identity/v1",
    }
    payload.update(overrides)
    return FamilyIdentity(**payload)  # type: ignore[arg-type]


def _slot(slot_id: str, **overrides: object) -> CandidateSlot:
    source_rank, mapped_rank, seed, role, intervention_type = _GRID[slot_id]
    reserve_order = _RESERVE_ORDER.get(slot_id)
    payload: dict[str, object] = {
        "slot_id": slot_id,
        "fault_type": "preprocessing_bug",
        "slot_kind": "reserve" if reserve_order else "primary",
        "role": role,
        "reserve_order": reserve_order,
        "identity": _identity(_parameters(source_rank, mapped_rank), seed, intervention_type),
    }
    payload.update(overrides)
    return CandidateSlot(**payload)  # type: ignore[arg-type]


def _vector(predictions: tuple[int, ...], *, role: str) -> PredictionVector:
    return PredictionVector(
        schema_version=PREDICTION_VECTOR_SCHEMA_VERSION,
        role=role,  # type: ignore[arg-type]
        predictions=predictions,
    )


def _wrong_by(count: int) -> tuple[int, ...]:
    """Return a vector that is wrong on the first ``count`` records."""

    return tuple(1 - label if index < count else label for index, label in enumerate(_LABELS))


def _fault_inputs(slot_id: str = "M3-F1", *, wrong: int = 6, **overrides: object) -> Any:
    source_rank, mapped_rank, seed, _, _ = _GRID[slot_id]
    assert source_rank is not None and mapped_rank is not None
    spec = EncoderMappingMismatchSpec(
        injection_id=slot_id,
        parameters=_parameters(source_rank, mapped_rank),
        source_category=_RANK[source_rank],
        mapped_category=_RANK[mapped_rank],
        seed=seed,
    )
    source = _source()
    payload: dict[str, object] = {
        "source": source,
        "spec": spec,
        "result": apply_encoder_mapping_mismatch(source=source, spec=spec, slot=_slot(slot_id)),
        "test_set": _test_set(),
        "clean_reference_predictions": _vector(_LABELS, role="reference"),
        "mismatched_predictions": _vector(_wrong_by(wrong), role="observed"),
    }
    payload.update(overrides)
    return FaultDirectedInputs(**payload)  # type: ignore[arg-type]


def _repair_inputs(
    *, reference_wrong: int = 6, observed_wrong: int = 0, **overrides: object
) -> Any:
    spec = EncoderMappingRepairSpec(
        injection_id="M3-I1",
        parameters=_parameters(1, 3),
        source_category=_RANK[1],
        mapped_category=_RANK[3],
        seed=304,
    )
    source = _source()
    payload: dict[str, object] = {
        "source": source,
        "spec": spec,
        "result": apply_encoder_mapping_repair(source=source, spec=spec, slot=_slot("M3-I1")),
        "test_set": _test_set(),
        "mismatched_reference_predictions": _vector(_wrong_by(reference_wrong), role="reference"),
        "repaired_predictions": _vector(_wrong_by(observed_wrong), role="observed"),
    }
    payload.update(overrides)
    return RepairControlInputs(**payload)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# A real frame that matches the baseline schema, for the benign control
# --------------------------------------------------------------------------- #

_CATEGORY_POOL: dict[str, tuple[str, ...]] = {
    "Contract": ("Month-to-month", "One year", "Two year"),
    "PaymentMethod": ("Electronic check", "Mailed check", "Bank transfer (automatic)"),
    "InternetService": ("DSL", "Fiber optic", "No"),
}


def _frame(rows: int = len(_RAW)) -> pd.DataFrame:
    """Build a frame whose columns all differ, so a column swap is detectable."""

    data: dict[str, list[object]] = {}
    for offset, column in enumerate(NUMERIC_FEATURES):
        data[column] = [float((index + offset) % 7) + 0.5 + offset for index in range(rows)]
    for offset, column in enumerate(CATEGORICAL_FEATURES):
        pool = _CATEGORY_POOL.get(column, ("Yes", "No"))
        data[column] = [pool[(index + offset) % len(pool)] for index in range(rows)]
    return pd.DataFrame(data, columns=list(FEATURE_COLUMNS))


def _named_table(frame: pd.DataFrame | None = None) -> NamedFeatureTable:
    frame = _frame() if frame is None else frame
    names = tuple(str(column) for column in frame.columns)
    return NamedFeatureTable(
        schema_version=NAMED_FEATURE_TABLE_SCHEMA_VERSION,
        feature_names=names,
        rows=tuple(
            NamedFeatureRow(
                record_id=_IDS[index],
                values=tuple(str(frame.iloc[index][name]) for name in names),
            )
            for index in range(len(frame))
        ),
    )


def _transformed_pair() -> tuple[TransformedMatrix, TransformedMatrix]:
    """Transform both physical layouts with the project's real preprocessor."""

    frame = _frame()
    preprocessor = build_preprocessor()
    preprocessor.fit(frame)
    order = permutation_order(
        feature_names=tuple(str(column) for column in frame.columns), seed=305
    )
    permuted = frame.iloc[:, list(order)]
    columns = tuple(str(name) for name in preprocessor.get_feature_names_out())

    def wrap(matrix: Any) -> TransformedMatrix:
        return TransformedMatrix(
            schema_version=TRANSFORMED_MATRIX_SCHEMA_VERSION,
            column_names=columns,
            rows=tuple(tuple(float(value) for value in row) for row in matrix),
        )

    return wrap(preprocessor.transform(frame)), wrap(preprocessor.transform(permuted))


def _benign_inputs(
    *, with_evidence: bool = True, diverge_predictions: int = 0, **overrides: object
) -> Any:
    table = _named_table()
    spec = ColumnPermutationSpec(injection_id="M3-B1", parameters=_parameters(None, None), seed=305)
    result = apply_column_permutation(table=table, spec=spec, slot=_slot("M3-B1"))
    payload: dict[str, object] = {"table": table, "spec": spec, "result": result}
    if with_evidence:
        original, permuted = _transformed_pair()
        observed = list(_LABELS)
        for index in range(diverge_predictions):
            observed[index] = 1 - observed[index]
        payload["test_set"] = _test_set()
        payload["evidence"] = BenignEquivalenceEvidence(
            schema_version=BENIGN_EVIDENCE_SCHEMA_VERSION,
            permutation_artifact_sha256=result.artifact_sha256(),
            source_table_sha256=table.artifact_sha256(),
            record_ids_sha256=table.record_ids_sha256(),
            evaluation_source_sha256=_test_set().artifact_sha256(),
            attested_model_sha256=_H["c"],
            preprocessing_specification_sha256=_H["d"],
            original_transformed=original,
            permuted_transformed=permuted,
            reference_predictions=_vector(_LABELS, role="reference"),
            observed_predictions=_vector(tuple(observed), role="observed"),
        )
    payload.update(overrides)
    return BenignControlInputs(**payload)  # type: ignore[arg-type]


def _forge(model: Any, **updates: object) -> Any:
    """Bypass validation the way a careless caller would."""

    return model.model_copy(update=updates)


@pytest.fixture
def fault_inputs() -> Any:
    return _fault_inputs()


@pytest.fixture
def fault_package(fault_inputs: Any) -> PreprocessingCandidatePackage:
    return build_preprocessing_candidate_package(slot=_slot("M3-F1"), inputs=fault_inputs)


@pytest.fixture
def repair_inputs() -> Any:
    return _repair_inputs()


@pytest.fixture
def repair_package(repair_inputs: Any) -> PreprocessingCandidatePackage:
    return build_preprocessing_candidate_package(slot=_slot("M3-I1"), inputs=repair_inputs)


@pytest.fixture
def benign_inputs() -> Any:
    return _benign_inputs()


@pytest.fixture
def benign_package(benign_inputs: Any) -> PreprocessingCandidatePackage:
    return build_preprocessing_candidate_package(slot=_slot("M3-B1"), inputs=benign_inputs)


# --------------------------------------------------------------------------- #
# A. Positive
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("slot_id", ["M3-F1", "M3-F2", "M3-F3"])
def test_each_primary_fault_slot_packages_successfully(slot_id: str) -> None:
    inputs = _fault_inputs(slot_id)
    package = build_preprocessing_candidate_package(slot=_slot(slot_id), inputs=inputs)
    validate_preprocessing_candidate_package(package, slot=_slot(slot_id), inputs=inputs)
    assert package.slot_id == slot_id
    assert package.role == "fault_directed"
    assert package.status == "validity_review_required"


def test_a_reserve_slot_uses_the_same_entry_point() -> None:
    """No second API: the reserve slot goes through the one public builder."""

    inputs = _fault_inputs("M3-R1")
    package = build_preprocessing_candidate_package(slot=_slot("M3-R1"), inputs=inputs)
    validate_preprocessing_candidate_package(package, slot=_slot("M3-R1"), inputs=inputs)
    assert package.execution.slot_kind == "reserve"


def test_the_repair_control_packages_successfully(
    repair_inputs: Any, repair_package: PreprocessingCandidatePackage
) -> None:
    validate_preprocessing_candidate_package(
        repair_package, slot=_slot("M3-I1"), inputs=repair_inputs
    )
    assert repair_package.role == "designed_improvement_control"
    assert repair_package.repair_measurement is not None
    assert repair_package.status == "validity_review_required"


def test_the_benign_control_packages_successfully(
    benign_inputs: Any, benign_package: PreprocessingCandidatePackage
) -> None:
    validate_preprocessing_candidate_package(
        benign_package, slot=_slot("M3-B1"), inputs=benign_inputs
    )
    assert benign_package.role == "designed_benign_control"
    assert benign_package.equivalence_status == "equivalence_verified_pending_admission"
    assert benign_package.status == "equivalence_verified_pending_admission"
    assert benign_package.disposition is not None
    assert benign_package.disposition.disposition == "technically_valid"


def test_a_benign_control_without_evidence_is_pending_not_passing() -> None:
    inputs = _benign_inputs(with_evidence=False)
    package = build_preprocessing_candidate_package(slot=_slot("M3-B1"), inputs=inputs)
    validate_preprocessing_candidate_package(package, slot=_slot("M3-B1"), inputs=inputs)
    assert package.status == "pending_post_execution_equivalence"
    assert package.disposition is None
    assert package.metric_comparison is None


def test_the_candidate_identity_matches_an_independently_derived_expectation(
    fault_package: PreprocessingCandidatePackage,
) -> None:
    """Rebuild the fingerprint and the candidate ID from the frozen contract."""

    identity = _slot("M3-F1").identity
    payload = identity.identity_payload()
    assert set(payload) == set(IDENTITY_FIELD_NAMES)
    expected_fingerprint = canonical_sha256(payload)
    expected_candidate = P2_CANDIDATE_PREFIX + canonical_sha256(
        {
            "candidate_schema_version": P2_CANDIDATE_SCHEMA_VERSION,
            "slot_id": "M3-F1",
            "proposed_family_sha256": expected_fingerprint,
        }
    )
    assert fault_package.proposed_family_sha256 == expected_fingerprint
    assert fault_package.candidate_id == expected_candidate


def test_the_family_fingerprint_does_not_move_when_the_outcome_does() -> None:
    """Identity is twelve frozen fields; a measurement is not one of them."""

    regressed = build_preprocessing_candidate_package(
        slot=_slot("M3-F1"), inputs=_fault_inputs(wrong=8)
    )
    stable = build_preprocessing_candidate_package(
        slot=_slot("M3-F1"), inputs=_fault_inputs(wrong=0)
    )
    assert regressed.measured_primary_outcome == "regression"
    assert stable.measured_primary_outcome == "stable"
    assert regressed.proposed_family_sha256 == stable.proposed_family_sha256
    assert regressed.candidate_id == stable.candidate_id
    assert regressed.artifact_package_sha256() != stable.artifact_package_sha256()


def test_the_same_input_produces_a_byte_identical_package(
    fault_package: PreprocessingCandidatePackage,
) -> None:
    again = build_preprocessing_candidate_package(slot=_slot("M3-F1"), inputs=_fault_inputs())
    assert again.artifact_package_sha256() == fault_package.artifact_package_sha256()
    assert again.model_dump(mode="json") == fault_package.model_dump(mode="json")


def test_record_order_and_membership_survive_packaging(
    fault_inputs: Any, fault_package: PreprocessingCandidatePackage
) -> None:
    assert fault_inputs.result.record_ids == _IDS
    assert fault_package.source_binding_sha256 != _HEX_0
    assert fault_package.evaluation_source_sha256 == _test_set().artifact_sha256()


def test_the_metrics_match_an_independent_sklearn_computation(
    fault_package: PreprocessingCandidatePackage,
) -> None:
    comparison = fault_package.metric_comparison
    assert comparison is not None
    assert comparison.reference.accuracy == pytest.approx(
        accuracy_score(list(_LABELS), list(_LABELS))
    )
    assert comparison.observed.accuracy == pytest.approx(
        accuracy_score(list(_LABELS), list(_wrong_by(6)))
    )


def test_the_package_pins_its_protocol_policy_and_schema(
    fault_package: PreprocessingCandidatePackage,
) -> None:
    assert fault_package.schema_version == CANDIDATE_PACKAGE_SCHEMA_VERSION
    assert fault_package.package_protocol_version == CANDIDATE_PACKAGE_PROTOCOL_VERSION
    assert fault_package.eligibility_policy_version == ELIGIBILITY_POLICY_VERSION


def test_the_package_is_strict_and_frozen(fault_package: PreprocessingCandidatePackage) -> None:
    with pytest.raises(ValidationError):
        fault_package.status = "technically_rejected"
    with pytest.raises(ValidationError):
        fault_package.execution.candidate_id = "x"
    with pytest.raises(ValidationError, match="Extra inputs"):
        PreprocessingCandidatePackage(
            **{**fault_package.model_dump(), "family_class": "eligible_failure"}  # type: ignore[arg-type]
        )


def test_the_public_api_exports_the_package() -> None:
    import aletheia_lab.benchmark.p2 as package

    for name in (
        "PreprocessingCandidatePackage",
        "PreprocessingFamilyError",
        "FaultDirectedInputs",
        "RepairControlInputs",
        "BenignControlInputs",
        "build_preprocessing_candidate_package",
        "validate_inference_evaluation_binding",
        "validate_preprocessing_candidate_package",
    ):
        assert name in package.__all__
        assert hasattr(package, name)


# --------------------------------------------------------------------------- #
# B. Outcome and control semantics
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("wrong", "expected"),
    [(8, "regression"), (0, "stable")],
)
def test_a_fault_directed_outcome_is_measured_not_assumed(wrong: int, expected: str) -> None:
    """A slot named M3-F1 is not a regression because of its name."""

    package = build_preprocessing_candidate_package(
        slot=_slot("M3-F1"), inputs=_fault_inputs(wrong=wrong)
    )
    assert package.measured_primary_outcome == expected


def test_a_fault_directed_candidate_can_measure_an_improvement() -> None:
    package = build_preprocessing_candidate_package(
        slot=_slot("M3-F1"),
        inputs=_fault_inputs(
            wrong=0, clean_reference_predictions=_vector(_wrong_by(8), role="reference")
        ),
    )
    assert package.measured_primary_outcome == "improvement"


@pytest.mark.parametrize(
    ("reference_wrong", "observed_wrong", "outcome", "measurement_status"),
    [
        (8, 0, "improvement", "validity_review_required"),
        (4, 4, "stable", "validity_review_required"),
        (0, 8, "regression", "control_direction_violation"),
    ],
)
def test_the_repair_control_reports_its_direction_honestly(
    reference_wrong: int, observed_wrong: int, outcome: str, measurement_status: str
) -> None:
    inputs = _repair_inputs(reference_wrong=reference_wrong, observed_wrong=observed_wrong)
    package = build_preprocessing_candidate_package(slot=_slot("M3-I1"), inputs=inputs)
    assert package.measured_primary_outcome == outcome
    assert package.repair_measurement is not None
    assert package.repair_measurement.status == measurement_status


def test_a_regressed_repair_control_cannot_become_an_eligible_failure() -> None:
    inputs = _repair_inputs(reference_wrong=0, observed_wrong=8)
    package = build_preprocessing_candidate_package(slot=_slot("M3-I1"), inputs=inputs)
    assert package.measured_primary_outcome == "regression"
    assert package.role == "designed_improvement_control"
    assert "family_class" not in type(package).model_fields
    assert package.status == "validity_review_required"


def test_a_benign_equivalence_failure_is_a_technical_rejection() -> None:
    inputs = _benign_inputs(diverge_predictions=3)
    package = build_preprocessing_candidate_package(slot=_slot("M3-B1"), inputs=inputs)
    validate_preprocessing_candidate_package(package, slot=_slot("M3-B1"), inputs=inputs)
    assert package.equivalence_status == "benign_equivalence_failure"
    assert package.status == "technically_rejected"
    assert package.disposition is not None
    assert package.disposition.disposition == "technical_rejected"
    assert package.disposition.rejection_reason == "benign_equivalence_failure"


def test_a_rejected_benign_candidate_carries_no_outcome_or_measurement() -> None:
    package = build_preprocessing_candidate_package(
        slot=_slot("M3-B1"), inputs=_benign_inputs(diverge_predictions=3)
    )
    assert package.measured_primary_outcome is None
    assert package.metric_comparison is None
    assert package.evaluation_source_sha256 is None


def test_a_benign_control_cannot_carry_a_primary_metric_outcome(
    benign_package: PreprocessingCandidatePackage,
) -> None:
    """Recording one would let a benign control be relabelled stable."""

    forged = _forge(benign_package, measured_primary_outcome="stable")
    with pytest.raises(ValidationError, match="decided by equivalence"):
        type(forged).model_validate(forged.model_dump())


def test_guardrail_data_is_persisted_without_deciding_eligibility(
    fault_package: PreprocessingCandidatePackage,
) -> None:
    comparison = fault_package.metric_comparison
    assert comparison is not None
    assert comparison.macro_f1_delta is not None
    assert comparison.minority_recall_delta is not None
    assert comparison.observed.confusion.total == len(_LABELS)
    assert fault_package.status == "validity_review_required"


def test_no_package_field_can_hold_an_admission_family_or_cause() -> None:
    names = set(PreprocessingCandidatePackage.model_fields)
    forbidden = ("family_class", "case_family", "admission", "census", "context", "cause")
    assert not any(token in name for name in names for token in forbidden)


# --------------------------------------------------------------------------- #
# C. Exploits
# --------------------------------------------------------------------------- #


def test_a_caller_cannot_declare_an_outcome(
    fault_inputs: Any, fault_package: PreprocessingCandidatePackage
) -> None:
    forged = _forge(fault_package, measured_primary_outcome="improvement")
    with pytest.raises(ValidationError, match="must be the outcome the comparison derived"):
        validate_preprocessing_candidate_package(forged, slot=_slot("M3-F1"), inputs=fault_inputs)


def test_a_forged_candidate_identifier_is_rejected(
    fault_inputs: Any, fault_package: PreprocessingCandidatePackage
) -> None:
    forged = _forge(fault_package, candidate_id=P2_CANDIDATE_PREFIX + _HEX_0)
    with pytest.raises(ValidationError, match="derived from the slot and the fingerprint"):
        validate_preprocessing_candidate_package(forged, slot=_slot("M3-F1"), inputs=fault_inputs)


def test_a_forged_family_fingerprint_is_rejected(
    fault_inputs: Any, fault_package: PreprocessingCandidatePackage
) -> None:
    forged = _forge(fault_package, proposed_family_sha256=_HEX_0)
    with pytest.raises(ValidationError):
        validate_preprocessing_candidate_package(forged, slot=_slot("M3-F1"), inputs=fault_inputs)


def test_a_forged_slot_identifier_is_rejected(
    fault_inputs: Any, fault_package: PreprocessingCandidatePackage
) -> None:
    forged = _forge(fault_package, slot_id="M3-F2")
    with pytest.raises(ValidationError):
        validate_preprocessing_candidate_package(forged, slot=_slot("M3-F1"), inputs=fault_inputs)


def test_a_forged_role_is_rejected(
    fault_inputs: Any, fault_package: PreprocessingCandidatePackage
) -> None:
    forged = _forge(fault_package, role="designed_benign_control")
    with pytest.raises(ValidationError):
        validate_preprocessing_candidate_package(forged, slot=_slot("M3-F1"), inputs=fault_inputs)


def test_a_slot_with_a_forged_seed_is_rejected(fault_inputs: Any) -> None:
    tampered = _slot(
        "M3-F1", identity=_identity(_parameters(3, 2), 999, MISMATCH_INTERVENTION_TYPE)
    )
    with pytest.raises(PreprocessingFamilyError):
        build_preprocessing_candidate_package(slot=tampered, inputs=fault_inputs)


def test_a_slot_with_a_forged_intervention_type_is_rejected(fault_inputs: Any) -> None:
    tampered = _slot(
        "M3-F1",
        identity=_identity(_parameters(3, 2), 301, ENCODER_REPAIR_INTERVENTION_TYPE),
    )
    with pytest.raises(PreprocessingFamilyError):
        build_preprocessing_candidate_package(slot=tampered, inputs=fault_inputs)


def test_a_slot_and_artifact_forged_together_are_still_rejected() -> None:
    """Making both sides agree does not help: the grid is the third opinion."""

    tampered = _slot(
        "M3-F1", identity=_identity(_parameters(2, 1), 301, MISMATCH_INTERVENTION_TYPE)
    )
    spec = EncoderMappingMismatchSpec(
        injection_id="M3-F1",
        parameters=_parameters(2, 1),
        source_category=_RANK[2],
        mapped_category=_RANK[1],
        seed=301,
    )
    source = _source()
    with pytest.raises(ContractViolation):
        build_preprocessing_candidate_package(
            slot=tampered,
            inputs=FaultDirectedInputs(
                source=source,
                spec=spec,
                result=apply_encoder_mapping_mismatch(
                    source=source, spec=spec, slot=_slot("M3-F2")
                ),
                test_set=_test_set(),
                clean_reference_predictions=_vector(_LABELS, role="reference"),
                mismatched_predictions=_vector(_wrong_by(6), role="observed"),
            ),
        )


def test_a_package_cannot_be_replayed_across_slots(
    fault_inputs: Any, fault_package: PreprocessingCandidatePackage
) -> None:
    with pytest.raises((ContractViolation, ValidationError)):
        validate_preprocessing_candidate_package(
            fault_package, slot=_slot("M3-F2"), inputs=_fault_inputs("M3-F2")
        )


def test_a_package_cannot_be_replayed_across_sources(
    fault_package: PreprocessingCandidatePackage,
) -> None:
    other = _fault_inputs(source=_source(attested_model_sha256=_HEX_0))
    with pytest.raises(ContractViolation):
        validate_preprocessing_candidate_package(fault_package, slot=_slot("M3-F1"), inputs=other)


@pytest.mark.parametrize(
    "field",
    [
        "dataset_sha256",
        "model_data_split_manifest_sha256",
        "model_specification_sha256",
        "preprocessing_specification_sha256",
    ],
)
def test_a_replayed_dataset_or_system_hash_is_rejected(
    field: str, fault_inputs: Any, fault_package: PreprocessingCandidatePackage
) -> None:
    forged = _forge(fault_package, **{field: _HEX_0})
    with pytest.raises((ContractViolation, ValidationError)):
        validate_preprocessing_candidate_package(forged, slot=_slot("M3-F1"), inputs=fault_inputs)


def test_a_tampered_artifact_digest_is_rejected(
    fault_inputs: Any, fault_package: PreprocessingCandidatePackage
) -> None:
    forged = _forge(fault_package, artifact_sha256=_HEX_0)
    with pytest.raises(PreprocessingFamilyError, match="not bound to this artifact"):
        validate_preprocessing_candidate_package(forged, slot=_slot("M3-F1"), inputs=fault_inputs)


def test_a_tampered_slot_digest_is_rejected(
    fault_inputs: Any, fault_package: PreprocessingCandidatePackage
) -> None:
    forged = _forge(fault_package, slot_sha256=_HEX_0)
    with pytest.raises(PreprocessingFamilyError, match="not bound to this frozen slot"):
        validate_preprocessing_candidate_package(forged, slot=_slot("M3-F1"), inputs=fault_inputs)


def test_a_surface_consistent_forged_comparison_is_rejected(
    fault_inputs: Any, fault_package: PreprocessingCandidatePackage
) -> None:
    """A forged comparison that agrees with itself still disagrees with the vectors."""

    other = build_preprocessing_candidate_package(
        slot=_slot("M3-F1"), inputs=_fault_inputs(wrong=0)
    )
    forged = _forge(
        fault_package,
        metric_comparison=other.metric_comparison,
        measured_primary_outcome=other.measured_primary_outcome,
    )
    with pytest.raises(PreprocessingFamilyError):
        validate_preprocessing_candidate_package(forged, slot=_slot("M3-F1"), inputs=fault_inputs)


def test_a_prediction_vector_shorter_than_the_test_set_is_rejected() -> None:
    with pytest.raises(ContractViolation):
        build_preprocessing_candidate_package(
            slot=_slot("M3-F1"),
            inputs=_fault_inputs(
                mismatched_predictions=_vector(_wrong_by(6)[:-1], role="observed")
            ),
        )


def test_a_fault_candidate_cannot_join_a_source_to_different_evaluation_records() -> None:
    """Catch the internally-valid cross-artifact exploit, not only package replay."""

    unrelated_test_set = _test_set(record_ids=("99999-TEST", *_IDS[1:]))
    inputs = _fault_inputs(test_set=unrelated_test_set)
    with pytest.raises(ContractViolation, match="same records in the same order"):
        build_preprocessing_candidate_package(slot=_slot("M3-F1"), inputs=inputs)


@pytest.mark.parametrize(
    ("source_field", "message"),
    [
        ("attested_raw_feature_matrix_sha256", "same feature matrix"),
        ("attested_raw_target_sha256", "same target artifact"),
        ("attested_model_sha256", "same fitted model"),
    ],
)
def test_a_fault_candidate_cannot_join_source_and_evaluation_attestations(
    source_field: str, message: str
) -> None:
    """Individually valid sources from different experiments must not compose."""

    source = _source(**{source_field: _HEX_0})
    baseline = _fault_inputs()
    inputs = FaultDirectedInputs(
        source=source,
        spec=baseline.spec,
        result=apply_encoder_mapping_mismatch(
            source=source,
            spec=baseline.spec,
            slot=_slot("M3-F1"),
        ),
        test_set=baseline.test_set,
        clean_reference_predictions=baseline.clean_reference_predictions,
        mismatched_predictions=baseline.mismatched_predictions,
    )
    with pytest.raises(ContractViolation, match=message):
        build_preprocessing_candidate_package(slot=_slot("M3-F1"), inputs=inputs)


def test_a_swapped_prediction_role_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must declare the reference role"):
        _fault_inputs(clean_reference_predictions=_vector(_LABELS, role="observed"))


def test_a_changed_record_membership_is_rejected(
    fault_package: PreprocessingCandidatePackage,
) -> None:
    renamed = ("99999-TEST", *_IDS[1:])
    other = _fault_inputs(source=_source(record_ids=renamed))
    with pytest.raises((ContractViolation, ValidationError)):
        validate_preprocessing_candidate_package(fault_package, slot=_slot("M3-F1"), inputs=other)


def test_a_changed_record_order_is_rejected(
    fault_package: PreprocessingCandidatePackage,
) -> None:
    reversed_source = _source(
        record_ids=tuple(reversed(_IDS)), raw_categories=tuple(reversed(_RAW))
    )
    other = _fault_inputs(source=reversed_source)
    with pytest.raises((ContractViolation, ValidationError)):
        validate_preprocessing_candidate_package(fault_package, slot=_slot("M3-F1"), inputs=other)


def test_a_transformed_block_changed_outside_the_target_feature_is_rejected(
    fault_inputs: Any, fault_package: PreprocessingCandidatePackage
) -> None:
    """Widening the encoded block means a column outside Contract moved."""

    widened = tuple((*row, 0) for row in fault_inputs.result.mismatched_block)
    forged_result = _forge(fault_inputs.result, mismatched_block=widened)
    with pytest.raises(ValidationError):
        FaultDirectedInputs(
            source=fault_inputs.source,
            spec=fault_inputs.spec,
            result=forged_result,
            test_set=fault_inputs.test_set,
            clean_reference_predictions=fault_inputs.clean_reference_predictions,
            mismatched_predictions=fault_inputs.mismatched_predictions,
        )


def test_a_non_finite_metric_cannot_enter_a_package(
    fault_package: PreprocessingCandidatePackage,
) -> None:
    comparison = fault_package.metric_comparison
    assert comparison is not None
    forged = _forge(
        fault_package, metric_comparison=_forge(comparison, accuracy_delta=float("nan"))
    )
    with pytest.raises(ValidationError):
        type(forged).model_validate(forged.model_dump())


def test_a_package_forged_with_model_copy_is_rejected(
    fault_inputs: Any, fault_package: PreprocessingCandidatePackage
) -> None:
    forged = _forge(fault_package, status="technically_rejected")
    with pytest.raises(ValidationError, match="must carry a technical rejection"):
        validate_preprocessing_candidate_package(forged, slot=_slot("M3-F1"), inputs=fault_inputs)


def test_a_package_forged_with_model_construct_is_rejected(
    fault_inputs: Any, fault_package: PreprocessingCandidatePackage
) -> None:
    forged = PreprocessingCandidatePackage.model_construct(
        **{**fault_package.__dict__, "measured_primary_outcome": "improvement"}
    )
    with pytest.raises(ValidationError, match="must be the outcome the comparison derived"):
        validate_preprocessing_candidate_package(forged, slot=_slot("M3-F1"), inputs=fault_inputs)


def test_an_unknown_top_level_field_is_refused(
    fault_package: PreprocessingCandidatePackage,
) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        PreprocessingCandidatePackage(
            **{**fault_package.model_dump(), "admission_record": {}}  # type: ignore[arg-type]
        )


def test_an_unknown_nested_field_is_refused(
    fault_package: PreprocessingCandidatePackage,
) -> None:
    payload = fault_package.model_dump()
    payload["execution"]["evidence_condition"] = "full"
    with pytest.raises(ValidationError, match="Extra inputs"):
        PreprocessingCandidatePackage(**payload)  # type: ignore[arg-type]


def test_a_caller_cannot_attach_an_equivalence_boolean() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        BenignControlInputs(
            **{  # type: ignore[arg-type]
                **_benign_inputs(with_evidence=False).model_dump(),
                "equivalence_passed": True,
            }
        )


def test_pending_equivalence_cannot_carry_a_technical_pass() -> None:
    inputs = _benign_inputs(with_evidence=False)
    package = build_preprocessing_candidate_package(slot=_slot("M3-B1"), inputs=inputs)
    forged = _forge(
        package,
        disposition=TechnicalDispositionEntry(
            candidate_id=package.candidate_id,
            disposition="technically_valid",
        ),
    )
    with pytest.raises(ValidationError, match="pending equivalence must not carry"):
        validate_preprocessing_candidate_package(
            forged,
            slot=_slot("M3-B1"),
            inputs=inputs,
        )


def test_a_rejected_candidate_cannot_also_be_valid(
    benign_inputs: Any, benign_package: PreprocessingCandidatePackage
) -> None:
    forged = _forge(
        benign_package,
        status="technically_rejected",
        disposition=TechnicalDispositionEntry(
            candidate_id=benign_package.candidate_id, disposition="technically_valid"
        ),
    )
    with pytest.raises(ValidationError, match="must carry a technical rejection"):
        validate_preprocessing_candidate_package(forged, slot=_slot("M3-B1"), inputs=benign_inputs)


def test_a_technical_rejection_must_name_a_reason(
    benign_package: PreprocessingCandidatePackage,
) -> None:
    with pytest.raises(ValidationError):
        _forge(
            benign_package,
            status="technically_rejected",
            disposition=TechnicalDispositionEntry(
                candidate_id=benign_package.candidate_id, disposition="technical_rejected"
            ),
        )


def test_an_input_bundle_cannot_package_the_wrong_role(fault_inputs: Any) -> None:
    with pytest.raises(PreprocessingFamilyError, match="cannot package"):
        build_preprocessing_candidate_package(slot=_slot("M3-I1"), inputs=fault_inputs)


def test_a_deeply_nested_change_moves_the_package_digest(
    fault_package: PreprocessingCandidatePackage,
) -> None:
    """The digest binds nested fields, not just the top level.

    This test fails if the package digest is computed over a shallow view: the
    only thing that changes here is one confusion cell three levels down.
    """

    comparison = fault_package.metric_comparison
    assert comparison is not None
    deeper = _forge(
        comparison,
        observed=_forge(
            comparison.observed,
            confusion=_forge(comparison.observed.confusion, true_positive=99),
        ),
    )
    forged = _forge(fault_package, metric_comparison=deeper)
    assert forged.artifact_package_sha256() != fault_package.artifact_package_sha256()


@pytest.mark.parametrize(
    "field", ["slot_sha256", "artifact_sha256", "source_binding_sha256", "candidate_id"]
)
def test_every_bound_identity_field_moves_the_package_digest(
    field: str, fault_package: PreprocessingCandidatePackage
) -> None:
    value: object = P2_CANDIDATE_PREFIX + _HEX_0 if field == "candidate_id" else _HEX_0
    forged = _forge(fault_package, **{field: value})
    assert forged.artifact_package_sha256() != fault_package.artifact_package_sha256()
