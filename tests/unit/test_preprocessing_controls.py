"""Regression tests for the two predeclared preprocessing controls.

The name-bound equivalence claim is checked against the project's real
``ColumnTransformer`` and a real fitted estimator, not against a restated model
of what sklearn is expected to do.

Two error kinds are expected, and the tests keep them apart deliberately:

* ``ValidationError`` when a single object is malformed;
* ``PreprocessingControlError`` when objects disagree with one another.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, recall_score

from aletheia_lab.baseline.model import ModelConfig, build_pipeline
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
    metric_snapshot,
)
from aletheia_lab.benchmark.p2.contracts import CandidateSlot
from aletheia_lab.benchmark.p2.identity import FamilyIdentity, PreprocessingBugParameters
from aletheia_lab.benchmark.p2.preprocessing_controls import (
    BENIGN_EVIDENCE_SCHEMA_VERSION,
    ENCODER_REPAIR_INTERVENTION_TYPE,
    ENCODER_REPAIR_PROTOCOL_VERSION,
    NAMED_FEATURE_TABLE_SCHEMA_VERSION,
    PERMUTATION_INTERVENTION_TYPE,
    PERMUTATION_PROTOCOL_VERSION,
    TRANSFORMED_MATRIX_SCHEMA_VERSION,
    BenignEquivalenceEvidence,
    ColumnPermutationResult,
    ColumnPermutationSpec,
    EncoderMappingRepairResult,
    EncoderMappingRepairSpec,
    NamedFeatureRow,
    NamedFeatureTable,
    PreprocessingControlError,
    RepairControlMeasurement,
    TransformedMatrix,
    apply_column_permutation,
    apply_encoder_mapping_repair,
    benign_equivalence_status,
    measure_repair_control,
    permutation_order,
    validate_column_permutation,
    validate_encoder_mapping_repair,
    validate_repair_control_measurement,
)
from aletheia_lab.benchmark.p2.preprocessing_intervention import (
    CATEGORY_RANK_RULE,
    CATEGORY_VOCABULARY_SCHEMA_VERSION,
    INFERENCE_SOURCE_SCHEMA_VERSION,
    CategoryFrequency,
    FrozenCategoryVocabulary,
    InferenceTransformSource,
)

_HEX_A = "a" * 64
_HEX_B = "b" * 64
_HEX_C = "c" * 64
_HEX_D = "d" * 64
_HEX_E = "e" * 64
_HEX_0 = "0" * 64

_FEATURE = "Contract"
_TRANSFORM = "one_hot_encoder"

#: Ranking is by descending count: rank 1 Month-to-month, rank 2 Two year,
#: rank 3 One year. Deliberately not alphabetical.
_COUNTS: dict[str, int] = {"Month-to-month": 3875, "Two year": 1695, "One year": 1473}
_RANK_1 = "Month-to-month"
_RANK_3 = "One year"

_RAW: tuple[str, ...] = (
    "Month-to-month",
    "One year",
    "Two year",
    "Month-to-month",
    "Month-to-month",
    "Two year",
    "One year",
    "Month-to-month",
)

_REPAIR_SEED = 304
_PERMUTATION_SEED = 305


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _ids(count: int) -> tuple[str, ...]:
    return tuple(f"{index:05d}-SYNTH" for index in range(count))


def _vocabulary(**overrides: object) -> FrozenCategoryVocabulary:
    payload: dict[str, object] = {
        "schema_version": CATEGORY_VOCABULARY_SCHEMA_VERSION,
        "feature": _FEATURE,
        "split": "train",
        "rank_rule": CATEGORY_RANK_RULE,
        "frequencies": tuple(
            CategoryFrequency(category=name, count=count) for name, count in _COUNTS.items()
        ),
    }
    payload.update(overrides)
    return FrozenCategoryVocabulary(**payload)  # type: ignore[arg-type]


def _source(**overrides: object) -> InferenceTransformSource:
    payload: dict[str, object] = {
        "schema_version": INFERENCE_SOURCE_SCHEMA_VERSION,
        "split": "test",
        "feature": _FEATURE,
        "record_ids": _ids(len(_RAW)),
        "raw_categories": _RAW,
        "vocabulary": _vocabulary(),
        "attested_raw_feature_matrix_sha256": _HEX_A,
        "attested_raw_target_sha256": _HEX_B,
        "attested_model_sha256": _HEX_C,
        "attested_fitted_training_transform_sha256": _HEX_D,
        "attested_other_transform_config_sha256": _HEX_E,
    }
    payload.update(overrides)
    return InferenceTransformSource(**payload)  # type: ignore[arg-type]


def _parameters(
    *,
    source_rank: int | None,
    mapped_rank: int | None,
    mode: str = "inference_only",
    target_feature: str = _FEATURE,
    transform_name: str = _TRANSFORM,
) -> PreprocessingBugParameters:
    return PreprocessingBugParameters(
        target_feature=target_feature,
        source_rank=source_rank,
        mapped_rank=mapped_rank,
        mode=mode,  # type: ignore[arg-type]
        transform_name=transform_name,
    )


def _repair_spec(**overrides: object) -> EncoderMappingRepairSpec:
    payload: dict[str, object] = {
        "injection_id": "M3-I1",
        "parameters": _parameters(source_rank=1, mapped_rank=3),
        "source_category": _RANK_1,
        "mapped_category": _RANK_3,
        "seed": _REPAIR_SEED,
    }
    payload.update(overrides)
    return EncoderMappingRepairSpec(**payload)  # type: ignore[arg-type]


def _permutation_spec(**overrides: object) -> ColumnPermutationSpec:
    payload: dict[str, object] = {
        "injection_id": "M3-B1",
        "parameters": _parameters(source_rank=None, mapped_rank=None),
        "seed": _PERMUTATION_SEED,
    }
    payload.update(overrides)
    return ColumnPermutationSpec(**payload)  # type: ignore[arg-type]


def _slot(
    slot_id: str,
    *,
    parameters: PreprocessingBugParameters,
    seed: int,
    role: str,
    intervention_type: str,
    fault_type: str = "preprocessing_bug",
) -> CandidateSlot:
    return CandidateSlot(
        slot_id=slot_id,
        fault_type=fault_type,  # type: ignore[arg-type]
        slot_kind="primary",
        role=role,  # type: ignore[arg-type]
        identity=FamilyIdentity(
            dataset_snapshot_id="telco_customer_churn@2026-07",
            dataset_sha256=_HEX_A,
            model_data_split_manifest_sha256=_HEX_B,
            fault_type=fault_type,  # type: ignore[arg-type]
            intervention_type=intervention_type,
            canonical_intervention_parameters=parameters,
            seed=seed,
            reference_construction_id="clean-test-reference/v1",
            injector_contract_version="preprocessing-control/v1",
            model_specification_sha256=_HEX_C,
            preprocessing_specification_sha256=_HEX_D,
            identity_schema_version="p2-family-identity/v1",
        ),
    )


def _repair_slot(**overrides: object) -> CandidateSlot:
    payload: dict[str, object] = {
        "slot_id": "M3-I1",
        "parameters": _parameters(source_rank=1, mapped_rank=3),
        "seed": _REPAIR_SEED,
        "role": "designed_improvement_control",
        "intervention_type": ENCODER_REPAIR_INTERVENTION_TYPE,
    }
    payload.update(overrides)
    slot_id = str(payload.pop("slot_id"))
    return _slot(slot_id, **payload)  # type: ignore[arg-type]


def _permutation_slot(**overrides: object) -> CandidateSlot:
    payload: dict[str, object] = {
        "slot_id": "M3-B1",
        "parameters": _parameters(source_rank=None, mapped_rank=None),
        "seed": _PERMUTATION_SEED,
        "role": "designed_benign_control",
        "intervention_type": PERMUTATION_INTERVENTION_TYPE,
    }
    payload.update(overrides)
    slot_id = str(payload.pop("slot_id"))
    return _slot(slot_id, **payload)  # type: ignore[arg-type]


def _forge(model: Any, **updates: object) -> Any:
    """Bypass validation the way a careless caller would."""

    return model.model_copy(update=updates)


# --------------------------------------------------------------------------- #
# Synthetic frame that matches the real baseline schema
# --------------------------------------------------------------------------- #

_CATEGORY_POOL: dict[str, tuple[str, ...]] = {
    "Contract": ("Month-to-month", "One year", "Two year"),
    "PaymentMethod": ("Electronic check", "Mailed check", "Bank transfer (automatic)"),
    "InternetService": ("DSL", "Fiber optic", "No"),
}


def _frame(rows: int = 24) -> pd.DataFrame:
    """Build a frame whose columns all differ from one another.

    Giving every numeric column the same values would make a swap between two
    columns invisible, and a test that cannot fail proves nothing. Each column
    is therefore offset by its own position.
    """

    data: dict[str, list[object]] = {}
    for offset, column in enumerate(NUMERIC_FEATURES):
        data[column] = [float((index + offset) % 7) + 0.5 + offset for index in range(rows)]
    for offset, column in enumerate(CATEGORICAL_FEATURES):
        pool = _CATEGORY_POOL.get(column, ("Yes", "No"))
        data[column] = [pool[(index + offset) % len(pool)] for index in range(rows)]
    return pd.DataFrame(data, columns=list(FEATURE_COLUMNS))


def _labels(rows: int = 24) -> list[int]:
    """Sixteen negatives then eight positives, so label 1 is the minority."""

    return [0] * (rows - rows // 3) + [1] * (rows // 3)


def _named_table(frame: pd.DataFrame) -> NamedFeatureTable:
    names = tuple(str(column) for column in frame.columns)
    return NamedFeatureTable(
        schema_version=NAMED_FEATURE_TABLE_SCHEMA_VERSION,
        feature_names=names,
        rows=tuple(
            NamedFeatureRow(
                record_id=f"{index:05d}-SYNTH",
                values=tuple(str(frame.iloc[index][name]) for name in names),
            )
            for index in range(len(frame))
        ),
    )


def _transformed(matrix: Any, columns: tuple[str, ...]) -> TransformedMatrix:
    return TransformedMatrix(
        schema_version=TRANSFORMED_MATRIX_SCHEMA_VERSION,
        column_names=columns,
        rows=tuple(tuple(float(value) for value in row) for row in matrix),
    )


def _clean_test_set(rows: int = 24) -> CleanTestSet:
    return CleanTestSet(
        schema_version=CLEAN_TEST_SET_SCHEMA_VERSION,
        split="test",
        record_ids=_ids(rows),
        attested_true_labels=tuple(_labels(rows)),
        attested_test_feature_matrix_sha256=_HEX_A,
        attested_target_sha256=_HEX_B,
        attested_split_manifest_sha256=_HEX_B,
        attested_model_sha256=_HEX_C,
    )


def _source_for_test_set(test_set: CleanTestSet, **overrides: object) -> InferenceTransformSource:
    """Build the intervention source bound to the exact clean evaluation rows."""

    payload: dict[str, object] = {
        "record_ids": test_set.record_ids,
        "raw_categories": tuple(_RAW[index % len(_RAW)] for index in range(test_set.record_count)),
        "attested_raw_feature_matrix_sha256": test_set.attested_test_feature_matrix_sha256,
        "attested_raw_target_sha256": test_set.attested_target_sha256,
        "attested_model_sha256": test_set.attested_model_sha256,
    }
    payload.update(overrides)
    return _source(**payload)


def _vector(predictions: tuple[int, ...], *, role: str) -> PredictionVector:
    return PredictionVector(
        schema_version=PREDICTION_VECTOR_SCHEMA_VERSION,
        role=role,  # type: ignore[arg-type]
        predictions=predictions,
    )


@pytest.fixture
def source() -> InferenceTransformSource:
    return _source()


@pytest.fixture
def repair(source: InferenceTransformSource) -> EncoderMappingRepairResult:
    return apply_encoder_mapping_repair(source=source, spec=_repair_spec(), slot=_repair_slot())


@pytest.fixture
def table() -> NamedFeatureTable:
    return _named_table(_frame())


@pytest.fixture
def permutation(table: NamedFeatureTable) -> ColumnPermutationResult:
    return apply_column_permutation(table=table, spec=_permutation_spec(), slot=_permutation_slot())


# --------------------------------------------------------------------------- #
# M3-I1 positive
# --------------------------------------------------------------------------- #


def test_the_frozen_repair_slot_is_accepted(repair: EncoderMappingRepairResult) -> None:
    provenance = repair.provenance
    assert provenance.injection_id == "M3-I1"
    assert provenance.seed == _REPAIR_SEED
    assert provenance.source_rank == 1
    assert provenance.mapped_rank == 3
    assert provenance.intervention_type == ENCODER_REPAIR_INTERVENTION_TYPE
    assert provenance.repair_protocol_version == ENCODER_REPAIR_PROTOCOL_VERSION


def test_the_predeclared_reference_differs_from_the_clean_block(
    repair: EncoderMappingRepairResult,
) -> None:
    assert repair.mismatched_reference_block != repair.clean_block
    assert repair.mismatched_reference_view != repair.raw_categories


def test_the_repaired_block_exactly_equals_the_clean_block(
    repair: EncoderMappingRepairResult,
) -> None:
    assert repair.repaired_block == repair.clean_block
    assert repair.provenance.repaired_block_sha256 == repair.provenance.clean_block_sha256


def test_the_repaired_view_restores_the_raw_categories(
    source: InferenceTransformSource, repair: EncoderMappingRepairResult
) -> None:
    assert repair.repaired_view == source.raw_categories
    assert repair.raw_categories == source.raw_categories


def test_the_restored_identifiers_are_exactly_the_source_category_rows(
    source: InferenceTransformSource, repair: EncoderMappingRepairResult
) -> None:
    expected = tuple(
        sorted(
            record_id
            for record_id, category in zip(source.record_ids, source.raw_categories, strict=True)
            if category == _RANK_1
        )
    )
    assert repair.restored_record_ids == expected
    assert repair.provenance.restored_record_count == len(expected)


def test_only_source_category_rows_moved_in_the_reference(
    repair: EncoderMappingRepairResult,
) -> None:
    changed = [
        index
        for index, (raw, view) in enumerate(
            zip(repair.raw_categories, repair.mismatched_reference_view, strict=True)
        )
        if raw != view
    ]
    assert changed
    assert all(repair.raw_categories[index] == _RANK_1 for index in changed)
    assert all(repair.mismatched_reference_view[index] == _RANK_3 for index in changed)


def test_the_repair_preserves_record_order_and_membership(
    source: InferenceTransformSource, repair: EncoderMappingRepairResult
) -> None:
    assert repair.record_ids == source.record_ids
    assert set(repair.record_ids) == set(source.record_ids)


def test_the_same_input_produces_a_byte_equivalent_repair(
    repair: EncoderMappingRepairResult,
) -> None:
    again = apply_encoder_mapping_repair(source=_source(), spec=_repair_spec(), slot=_repair_slot())
    assert again.artifact_sha256() == repair.artifact_sha256()
    assert again.model_dump(mode="json") == repair.model_dump(mode="json")


def test_the_caller_source_object_is_not_mutated() -> None:
    original = _source()
    before = original.model_dump(mode="json")
    apply_encoder_mapping_repair(source=original, spec=_repair_spec(), slot=_repair_slot())
    assert original.model_dump(mode="json") == before


def test_validation_accepts_the_repair_it_produced(
    source: InferenceTransformSource, repair: EncoderMappingRepairResult
) -> None:
    returned = validate_encoder_mapping_repair(
        repair, source=source, spec=_repair_spec(), slot=_repair_slot()
    )
    assert returned.artifact_sha256() == repair.artifact_sha256()


def test_the_repair_result_carries_no_metric_outcome_or_family_class(
    repair: EncoderMappingRepairResult,
) -> None:
    names = set(type(repair).model_fields) | set(type(repair.provenance).model_fields)
    forbidden = ("accuracy", "outcome", "eligib", "family", "improvement", "benign", "cause")
    assert not any(token in name for name in names for token in forbidden)


# --------------------------------------------------------------------------- #
# M3-I1 post-execution measurement
# --------------------------------------------------------------------------- #


def _repair_case(
    reference_correct: int, observed_correct: int
) -> tuple[
    RepairControlMeasurement,
    EncoderMappingRepairResult,
    InferenceTransformSource,
    CleanTestSet,
    PredictionVector,
    PredictionVector,
]:
    test_set = _clean_test_set()
    source = _source_for_test_set(test_set)
    truth = tuple(_labels())
    reference = tuple(
        label if index < reference_correct else 1 - label for index, label in enumerate(truth)
    )
    observed = tuple(
        label if index < observed_correct else 1 - label for index, label in enumerate(truth)
    )
    result = apply_encoder_mapping_repair(source=source, spec=_repair_spec(), slot=_repair_slot())
    reference_vector = _vector(reference, role="reference")
    observed_vector = _vector(observed, role="observed")
    measurement = measure_repair_control(
        result=result,
        source=source,
        spec=_repair_spec(),
        slot=_repair_slot(),
        test_set=test_set,
        mismatched_reference_predictions=reference_vector,
        repaired_predictions=observed_vector,
    )
    return measurement, result, source, test_set, reference_vector, observed_vector


def _repair_measurement(reference_correct: int, observed_correct: int) -> RepairControlMeasurement:
    return _repair_case(reference_correct, observed_correct)[0]


def test_a_repair_that_helps_is_reported_as_improvement() -> None:
    measurement = _repair_measurement(reference_correct=12, observed_correct=24)
    assert measurement.comparison.measured_primary_outcome == "improvement"
    assert measurement.status == "validity_review_required"


def test_a_repair_that_changes_nothing_is_reported_as_stable() -> None:
    measurement = _repair_measurement(reference_correct=20, observed_correct=20)
    assert measurement.comparison.measured_primary_outcome == "stable"
    assert measurement.status == "validity_review_required"


def test_a_repair_that_hurts_is_reported_as_a_control_direction_violation() -> None:
    """The measurement is reported honestly rather than re-run with another seed."""

    measurement = _repair_measurement(reference_correct=24, observed_correct=12)
    assert measurement.comparison.measured_primary_outcome == "regression"
    assert measurement.status == "control_direction_violation"


def test_the_measurement_status_cannot_be_declared_by_a_caller() -> None:
    measurement = _repair_measurement(reference_correct=24, observed_correct=12)
    forged = _forge(measurement, status="validity_review_required")
    with pytest.raises(ValidationError, match="derived from the measured primary outcome"):
        type(forged).model_validate(forged.model_dump())


def test_the_measurement_never_produces_a_family_class() -> None:
    measurement = _repair_measurement(reference_correct=12, observed_correct=24)
    names = set(type(measurement).model_fields)
    assert not any(token in name for name in names for token in ("family", "eligib", "admission"))
    assert measurement.status in ("validity_review_required", "control_direction_violation")


@pytest.mark.parametrize(
    ("test_set_update", "message"),
    [
        ({"record_ids": tuple(f"OTHER-{index:05d}" for index in range(24))}, "same records"),
        ({"attested_test_feature_matrix_sha256": _HEX_0}, "same feature matrix"),
        ({"attested_target_sha256": _HEX_0}, "same target artifact"),
        ({"attested_model_sha256": _HEX_0}, "same fitted model"),
    ],
)
def test_repair_measurement_rejects_cross_source_replay(
    test_set_update: dict[str, object], message: str
) -> None:
    _, result, source, test_set, reference, observed = _repair_case(12, 24)
    replayed_test_set = CleanTestSet.model_validate({**test_set.model_dump(), **test_set_update})
    with pytest.raises(PreprocessingControlError, match=message):
        measure_repair_control(
            result=result,
            source=source,
            spec=_repair_spec(),
            slot=_repair_slot(),
            test_set=replayed_test_set,
            mismatched_reference_predictions=reference,
            repaired_predictions=observed,
        )


def test_repair_measurement_validation_recomputes_every_binding() -> None:
    measurement, result, source, test_set, reference, observed = _repair_case(12, 24)
    returned = validate_repair_control_measurement(
        measurement,
        result=result,
        source=source,
        spec=_repair_spec(),
        slot=_repair_slot(),
        test_set=test_set,
        mismatched_reference_predictions=reference,
        repaired_predictions=observed,
    )
    assert returned.canonical_sha256() == measurement.canonical_sha256()


def test_forged_repair_measurement_is_rejected_at_the_trust_boundary() -> None:
    measurement, result, source, test_set, reference, observed = _repair_case(12, 24)
    forged = _forge(measurement, inference_source_sha256=_HEX_0)
    with pytest.raises(PreprocessingControlError, match="recomputed bound measurement"):
        validate_repair_control_measurement(
            forged,
            result=result,
            source=source,
            spec=_repair_spec(),
            slot=_repair_slot(),
            test_set=test_set,
            mismatched_reference_predictions=reference,
            repaired_predictions=observed,
        )


def test_non_finite_nested_comparison_cannot_enter_a_repair_measurement() -> None:
    measurement = _repair_measurement(12, 24)
    forged_comparison = _forge(measurement.comparison, accuracy_delta=float("nan"))
    with pytest.raises(ValidationError, match="must be finite"):
        RepairControlMeasurement(
            **{
                **measurement.model_dump(exclude={"comparison"}),
                "comparison": forged_comparison,
            }  # type: ignore[arg-type]
        )


def test_the_measurement_binds_the_repair_artifact_and_slot() -> None:
    measurement, repair, _, _, _, _ = _repair_case(12, 24)
    assert measurement.repair_artifact_sha256 == repair.artifact_sha256()
    assert measurement.control_slot_sha256 == repair.provenance.control_slot_sha256


# --------------------------------------------------------------------------- #
# M3-I1 exploits
# --------------------------------------------------------------------------- #


def test_a_repair_with_the_wrong_seed_is_rejected(source: InferenceTransformSource) -> None:
    with pytest.raises(PreprocessingControlError):
        apply_encoder_mapping_repair(
            source=source, spec=_repair_spec(seed=999), slot=_repair_slot(seed=999)
        )


def test_a_repair_with_the_wrong_ranks_is_rejected(source: InferenceTransformSource) -> None:
    parameters = _parameters(source_rank=2, mapped_rank=1)
    spec = _repair_spec(parameters=parameters, source_category="Two year", mapped_category=_RANK_1)
    with pytest.raises(PreprocessingControlError):
        apply_encoder_mapping_repair(
            source=source, spec=spec, slot=_repair_slot(parameters=parameters)
        )


def test_a_repair_naming_categories_the_ranks_do_not_resolve_to_is_rejected(
    source: InferenceTransformSource,
) -> None:
    spec = _repair_spec(source_category="Two year")
    with pytest.raises(PreprocessingControlError, match="ranks do not resolve to"):
        apply_encoder_mapping_repair(source=source, spec=spec, slot=_repair_slot())


def test_a_repair_targeting_another_feature_is_rejected() -> None:
    with pytest.raises(ValidationError, match="targets 'Contract' only"):
        _repair_spec(parameters=_parameters(source_rank=1, mapped_rank=3, target_feature="Tenure"))


def test_a_repair_with_the_wrong_transform_is_rejected() -> None:
    with pytest.raises(ValidationError, match="requires transform"):
        _repair_spec(
            parameters=_parameters(source_rank=1, mapped_rank=3, transform_name="ordinal_encoder")
        )


def test_a_repair_in_training_mode_is_rejected() -> None:
    with pytest.raises(ValidationError, match="inference transform only"):
        _repair_spec(parameters=_parameters(source_rank=1, mapped_rank=3, mode="both"))


def test_a_fault_directed_slot_disguised_as_a_repair_is_rejected(
    source: InferenceTransformSource,
) -> None:
    disguised = _repair_slot(role="fault_directed")
    with pytest.raises(PreprocessingControlError):
        apply_encoder_mapping_repair(source=source, spec=_repair_spec(), slot=disguised)


def test_a_mismatch_intervention_type_is_rejected_by_the_repair_entry_point(
    source: InferenceTransformSource,
) -> None:
    disguised = _repair_slot(intervention_type="inference_encoder_mapping_mismatch")
    with pytest.raises(PreprocessingControlError):
        apply_encoder_mapping_repair(source=source, spec=_repair_spec(), slot=disguised)


def test_an_m3_f3_slot_cannot_be_replayed_as_the_repair_slot(
    source: InferenceTransformSource,
) -> None:
    """M3-F3 shares the rank pair with M3-I1, so only the slot binding separates them."""

    fault_slot = _slot(
        "M3-F3",
        parameters=_parameters(source_rank=1, mapped_rank=3),
        seed=303,
        role="fault_directed",
        intervention_type="inference_encoder_mapping_mismatch",
    )
    with pytest.raises(PreprocessingControlError):
        apply_encoder_mapping_repair(source=source, spec=_repair_spec(), slot=fault_slot)


def test_a_repair_cannot_be_replayed_against_another_source(
    repair: EncoderMappingRepairResult,
) -> None:
    other = _source(attested_model_sha256=_HEX_0)
    with pytest.raises(PreprocessingControlError):
        validate_encoder_mapping_repair(
            repair, source=other, spec=_repair_spec(), slot=_repair_slot()
        )


def test_forged_restored_identifiers_are_rejected(
    source: InferenceTransformSource, repair: EncoderMappingRepairResult
) -> None:
    untouched = next(
        record_id
        for record_id in repair.record_ids
        if record_id not in set(repair.restored_record_ids)
    )
    forged = _forge(
        repair, restored_record_ids=tuple(sorted({*repair.restored_record_ids[1:], untouched}))
    )
    with pytest.raises(PreprocessingControlError, match="restored identifiers"):
        validate_encoder_mapping_repair(
            forged, source=source, spec=_repair_spec(), slot=_repair_slot()
        )


def test_a_forged_repaired_view_is_rejected(
    source: InferenceTransformSource, repair: EncoderMappingRepairResult
) -> None:
    forged = _forge(repair, repaired_view=repair.mismatched_reference_view)
    with pytest.raises(ValidationError, match="restores the raw categories"):
        validate_encoder_mapping_repair(
            forged, source=source, spec=_repair_spec(), slot=_repair_slot()
        )


def test_a_forged_repaired_block_is_rejected(
    source: InferenceTransformSource, repair: EncoderMappingRepairResult
) -> None:
    forged = _forge(repair, repaired_block=repair.mismatched_reference_block)
    with pytest.raises(ValidationError, match="reproduces the clean fitted-transform block"):
        validate_encoder_mapping_repair(
            forged, source=source, spec=_repair_spec(), slot=_repair_slot()
        )


def test_a_reference_identical_to_the_clean_block_is_rejected(
    source: InferenceTransformSource, repair: EncoderMappingRepairResult
) -> None:
    forged = _forge(repair, mismatched_reference_block=repair.clean_block)
    with pytest.raises(ValidationError, match="must differ from the clean block"):
        validate_encoder_mapping_repair(
            forged, source=source, spec=_repair_spec(), slot=_repair_slot()
        )


def test_a_source_without_the_rank_one_category_is_rejected() -> None:
    """With nothing to mismatch there is nothing to repair, so the control is refused."""

    without_rank_one = tuple("Two year" if category == _RANK_1 else category for category in _RAW)
    with pytest.raises(PreprocessingControlError, match="nothing to repair"):
        apply_encoder_mapping_repair(
            source=_source(raw_categories=without_rank_one),
            spec=_repair_spec(),
            slot=_repair_slot(),
        )


def test_a_row_outside_the_declared_mapping_cannot_change(
    source: InferenceTransformSource, repair: EncoderMappingRepairResult
) -> None:
    untouched = next(
        index
        for index, record_id in enumerate(repair.record_ids)
        if record_id not in set(repair.restored_record_ids)
    )
    view = list(repair.mismatched_reference_view)
    view[untouched] = _RANK_1
    forged = _forge(repair, mismatched_reference_view=tuple(view))
    with pytest.raises(PreprocessingControlError):
        validate_encoder_mapping_repair(
            forged, source=source, spec=_repair_spec(), slot=_repair_slot()
        )


@pytest.mark.parametrize(
    "field",
    [
        "vocabulary_sha256",
        "raw_categories_sha256",
        "mismatched_reference_view_sha256",
        "restored_record_ids_sha256",
        "control_slot_sha256",
        "attested_model_sha256",
    ],
)
def test_a_tampered_repair_provenance_digest_is_rejected(
    field: str, source: InferenceTransformSource, repair: EncoderMappingRepairResult
) -> None:
    forged = _forge(repair, provenance=_forge(repair.provenance, **{field: _HEX_0}))
    with pytest.raises((PreprocessingControlError, ValidationError)):
        validate_encoder_mapping_repair(
            forged, source=source, spec=_repair_spec(), slot=_repair_slot()
        )


def test_a_repair_forged_with_model_construct_is_rejected(
    source: InferenceTransformSource, repair: EncoderMappingRepairResult
) -> None:
    forged = EncoderMappingRepairResult.model_construct(
        schema_version=repair.schema_version,
        record_ids=repair.record_ids,
        raw_categories=repair.raw_categories,
        mismatched_reference_view=repair.mismatched_reference_view,
        repaired_view=repair.mismatched_reference_view,
        encoder_column_order=repair.encoder_column_order,
        clean_block=repair.clean_block,
        mismatched_reference_block=repair.mismatched_reference_block,
        repaired_block=repair.repaired_block,
        restored_record_ids=repair.restored_record_ids,
        provenance=repair.provenance,
    )
    with pytest.raises(ValidationError, match="restores the raw categories"):
        validate_encoder_mapping_repair(
            forged, source=source, spec=_repair_spec(), slot=_repair_slot()
        )


# --------------------------------------------------------------------------- #
# M3-B1 positive
# --------------------------------------------------------------------------- #


def test_the_physical_column_order_actually_changes(
    table: NamedFeatureTable, permutation: ColumnPermutationResult
) -> None:
    assert permutation.permuted_table.feature_names != table.feature_names
    assert (
        permutation.provenance.permuted_physical_order_sha256
        != permutation.provenance.original_physical_order_sha256
    )


def test_the_permutation_is_deterministic(table: NamedFeatureTable) -> None:
    first = permutation_order(feature_names=table.feature_names, seed=_PERMUTATION_SEED)
    second = permutation_order(feature_names=table.feature_names, seed=_PERMUTATION_SEED)
    assert first == second
    assert list(first) != list(range(len(table.feature_names)))


def test_a_different_seed_gives_a_different_permutation(table: NamedFeatureTable) -> None:
    assert permutation_order(feature_names=table.feature_names, seed=_PERMUTATION_SEED) != (
        permutation_order(feature_names=table.feature_names, seed=999)
    )


def test_the_permutation_keeps_exactly_the_same_columns(
    table: NamedFeatureTable, permutation: ColumnPermutationResult
) -> None:
    assert sorted(permutation.permuted_table.feature_names) == sorted(table.feature_names)
    assert len(permutation.permuted_table.feature_names) == len(table.feature_names)


def test_the_permutation_keeps_record_order_and_names(
    table: NamedFeatureTable, permutation: ColumnPermutationResult
) -> None:
    assert permutation.permuted_table.record_ids == table.record_ids


def test_the_permutation_keeps_every_name_to_value_mapping(
    table: NamedFeatureTable, permutation: ColumnPermutationResult
) -> None:
    assert permutation.permuted_table.semantic_sha256() == table.semantic_sha256()
    for original_row, permuted_row in zip(table.rows, permutation.permuted_table.rows, strict=True):
        original = dict(zip(table.feature_names, original_row.values, strict=True))
        permuted = dict(
            zip(permutation.permuted_table.feature_names, permuted_row.values, strict=True)
        )
        assert original == permuted


def test_the_permutation_is_byte_equivalent_when_rerun(
    table: NamedFeatureTable, permutation: ColumnPermutationResult
) -> None:
    again = apply_column_permutation(
        table=table, spec=_permutation_spec(), slot=_permutation_slot()
    )
    assert again.artifact_sha256() == permutation.artifact_sha256()


def test_validation_accepts_the_permutation_it_produced(
    table: NamedFeatureTable, permutation: ColumnPermutationResult
) -> None:
    returned = validate_column_permutation(
        permutation, table=table, spec=_permutation_spec(), slot=_permutation_slot()
    )
    assert returned.artifact_sha256() == permutation.artifact_sha256()


def test_the_permutation_pins_its_protocol_and_intervention_type(
    permutation: ColumnPermutationResult,
) -> None:
    provenance = permutation.provenance
    assert provenance.permutation_protocol_version == PERMUTATION_PROTOCOL_VERSION
    assert provenance.intervention_type == PERMUTATION_INTERVENTION_TYPE
    assert provenance.seed == _PERMUTATION_SEED
    assert provenance.injection_id == "M3-B1"


# --------------------------------------------------------------------------- #
# M3-B1 against the real name-bound transformer
# --------------------------------------------------------------------------- #


def _fitted_transform_pair(rows: int = 24) -> tuple[Any, Any, tuple[str, ...]]:
    """Fit the project's real preprocessor and transform both physical layouts."""

    frame = _frame(rows)
    preprocessor = build_preprocessor()
    preprocessor.fit(frame)
    order = permutation_order(
        feature_names=tuple(str(column) for column in frame.columns), seed=_PERMUTATION_SEED
    )
    permuted_frame = frame.iloc[:, list(order)]
    assert list(permuted_frame.columns) != list(frame.columns)
    columns = tuple(str(name) for name in preprocessor.get_feature_names_out())
    return preprocessor.transform(frame), preprocessor.transform(permuted_frame), columns


def test_the_real_column_transformer_is_indifferent_to_physical_order() -> None:
    """The project's own ColumnTransformer selects by name, so layout cannot matter."""

    original, permuted, columns = _fitted_transform_pair()
    assert _transformed(original, columns).canonical_sha256() == (
        _transformed(permuted, columns).canonical_sha256()
    )


def test_the_real_pipeline_predicts_identically_under_both_layouts() -> None:
    frame = _frame()
    labels = _labels()
    pipeline = build_pipeline(ModelConfig())
    pipeline.fit(frame, labels)
    order = permutation_order(
        feature_names=tuple(str(column) for column in frame.columns), seed=_PERMUTATION_SEED
    )
    permuted_frame = frame.iloc[:, list(order)]
    original_predictions = tuple(int(value) for value in pipeline.predict(frame))
    permuted_predictions = tuple(int(value) for value in pipeline.predict(permuted_frame))
    assert original_predictions == permuted_predictions


def test_every_metric_is_identical_under_both_layouts() -> None:
    frame = _frame()
    labels = _labels()
    pipeline = build_pipeline(ModelConfig())
    pipeline.fit(frame, labels)
    order = permutation_order(
        feature_names=tuple(str(column) for column in frame.columns), seed=_PERMUTATION_SEED
    )
    original_predictions = tuple(int(value) for value in pipeline.predict(frame))
    permuted_predictions = tuple(
        int(value) for value in pipeline.predict(frame.iloc[:, list(order)])
    )
    test_set = _clean_test_set()
    reference = metric_snapshot(
        test_set=test_set, predictions=_vector(original_predictions, role="reference")
    )
    observed = metric_snapshot(
        test_set=test_set, predictions=_vector(permuted_predictions, role="observed")
    )
    assert reference.canonical_sha256() == observed.canonical_sha256()
    assert reference.accuracy == pytest.approx(accuracy_score(labels, list(original_predictions)))
    assert reference.macro_f1 == pytest.approx(
        f1_score(labels, list(original_predictions), average="macro", zero_division=0)
    )
    assert reference.minority_recall == pytest.approx(
        recall_score(labels, list(original_predictions), pos_label=1, zero_division=0)
    )
    expected = confusion_matrix(labels, list(original_predictions), labels=[0, 1])
    assert reference.confusion.true_negative == int(expected[0][0])
    assert reference.confusion.true_positive == int(expected[1][1])


def _benign_evidence(
    permutation: ColumnPermutationResult,
    *,
    table: NamedFeatureTable | None = None,
    test_set: CleanTestSet | None = None,
    slot: CandidateSlot | None = None,
    diverge_predictions: int = 0,
    diverge_matrix: bool = False,
) -> BenignEquivalenceEvidence:
    table = table or _named_table(_frame())
    test_set = test_set or _clean_test_set()
    slot = slot or _permutation_slot()
    original, permuted, columns = _fitted_transform_pair()
    original_matrix = _transformed(original, columns)
    permuted_matrix = _transformed(permuted, columns)
    if diverge_matrix:
        rows = list(permuted_matrix.rows)
        rows[0] = (rows[0][0] + 1.0, *rows[0][1:])
        permuted_matrix = TransformedMatrix(
            schema_version=TRANSFORMED_MATRIX_SCHEMA_VERSION,
            column_names=permuted_matrix.column_names,
            rows=tuple(rows),
        )
    base = tuple(_labels())
    observed = list(base)
    for index in range(diverge_predictions):
        observed[index] = 1 - observed[index]
    return BenignEquivalenceEvidence(
        schema_version=BENIGN_EVIDENCE_SCHEMA_VERSION,
        permutation_artifact_sha256=permutation.artifact_sha256(),
        source_table_sha256=table.artifact_sha256(),
        record_ids_sha256=table.record_ids_sha256(),
        evaluation_source_sha256=test_set.artifact_sha256(),
        attested_model_sha256=test_set.attested_model_sha256,
        preprocessing_specification_sha256=slot.identity.preprocessing_specification_sha256,
        original_transformed=original_matrix,
        permuted_transformed=permuted_matrix,
        reference_predictions=_vector(base, role="reference"),
        observed_predictions=_vector(tuple(observed), role="observed"),
    )


def test_without_post_execution_evidence_the_control_is_pending(
    table: NamedFeatureTable, permutation: ColumnPermutationResult
) -> None:
    status = benign_equivalence_status(
        result=permutation, table=table, spec=_permutation_spec(), slot=_permutation_slot()
    )
    assert status == "pending_post_execution_equivalence"


def test_with_full_equivalence_the_control_stops_short_of_admission(
    table: NamedFeatureTable, permutation: ColumnPermutationResult
) -> None:
    status = benign_equivalence_status(
        result=permutation,
        table=table,
        spec=_permutation_spec(),
        slot=_permutation_slot(),
        test_set=_clean_test_set(),
        evidence=_benign_evidence(permutation),
    )
    assert status == "equivalence_verified_pending_admission"
    assert status != "benign_control"


# --------------------------------------------------------------------------- #
# M3-B1 exploits
# --------------------------------------------------------------------------- #


def test_a_duplicate_column_name_is_rejected(table: NamedFeatureTable) -> None:
    names = (table.feature_names[0], *table.feature_names[1:-1], table.feature_names[0])
    with pytest.raises(ValidationError, match="feature names must be unique"):
        NamedFeatureTable(
            schema_version=NAMED_FEATURE_TABLE_SCHEMA_VERSION,
            feature_names=names,
            rows=table.rows,
        )


def test_a_missing_column_value_is_rejected(table: NamedFeatureTable) -> None:
    shortened = tuple(
        NamedFeatureRow(record_id=row.record_id, values=row.values[:-1]) for row in table.rows
    )
    with pytest.raises(ValidationError, match="one value per feature name"):
        NamedFeatureTable(
            schema_version=NAMED_FEATURE_TABLE_SCHEMA_VERSION,
            feature_names=table.feature_names,
            rows=shortened,
        )


def test_the_identity_permutation_is_refused(
    table: NamedFeatureTable, permutation: ColumnPermutationResult
) -> None:
    identity = tuple(range(len(table.feature_names)))
    forged = _forge(permutation, permutation=identity, permuted_table=table)
    with pytest.raises(ValidationError, match="identity permutation"):
        type(forged).model_validate(forged.model_dump())


def test_swapping_two_values_between_names_is_detected(table: NamedFeatureTable) -> None:
    """Comparing name sets would miss this; the semantic digest does not."""

    first, second = 0, 1
    swapped_rows = []
    for row in table.rows:
        values = list(row.values)
        values[first], values[second] = values[second], values[first]
        swapped_rows.append(NamedFeatureRow(record_id=row.record_id, values=tuple(values)))
    swapped = NamedFeatureTable(
        schema_version=NAMED_FEATURE_TABLE_SCHEMA_VERSION,
        feature_names=table.feature_names,
        rows=tuple(swapped_rows),
    )
    assert swapped.semantic_sha256() != table.semantic_sha256()
    assert swapped.physical_order_sha256() == table.physical_order_sha256()


def test_a_permutation_that_changed_a_named_value_is_rejected(
    table: NamedFeatureTable, permutation: ColumnPermutationResult
) -> None:
    rows = list(permutation.permuted_table.rows)
    values = list(rows[0].values)
    values[0] = values[0] + "-EDITED"
    rows[0] = NamedFeatureRow(record_id=rows[0].record_id, values=tuple(values))
    tampered = NamedFeatureTable(
        schema_version=NAMED_FEATURE_TABLE_SCHEMA_VERSION,
        feature_names=permutation.permuted_table.feature_names,
        rows=tuple(rows),
    )
    forged = _forge(permutation, permuted_table=tampered)
    with pytest.raises(ValidationError, match="must not change any name-to-value mapping"):
        type(forged).model_validate(forged.model_dump())


def test_a_permutation_that_reordered_records_is_rejected(
    permutation: ColumnPermutationResult,
) -> None:
    reversed_rows = tuple(reversed(permutation.permuted_table.rows))
    tampered = NamedFeatureTable(
        schema_version=NAMED_FEATURE_TABLE_SCHEMA_VERSION,
        feature_names=permutation.permuted_table.feature_names,
        rows=reversed_rows,
    )
    forged = _forge(permutation, permuted_table=tampered)
    with pytest.raises(ValidationError, match="must not reorder or rename records"):
        type(forged).model_validate(forged.model_dump())


def test_a_permutation_with_the_wrong_seed_is_rejected(table: NamedFeatureTable) -> None:
    with pytest.raises(PreprocessingControlError):
        apply_column_permutation(
            table=table, spec=_permutation_spec(seed=999), slot=_permutation_slot(seed=999)
        )


def test_a_permutation_with_the_wrong_intervention_type_is_rejected(
    table: NamedFeatureTable,
) -> None:
    disguised = _permutation_slot(intervention_type="inference_encoder_mapping_mismatch")
    with pytest.raises(PreprocessingControlError):
        apply_column_permutation(table=table, spec=_permutation_spec(), slot=disguised)


def test_a_permutation_spec_with_ranks_is_rejected() -> None:
    with pytest.raises(ValidationError, match="maps no category"):
        _permutation_spec(parameters=_parameters(source_rank=1, mapped_rank=3))


def test_a_fault_directed_slot_is_refused_by_the_benign_entry_point(
    table: NamedFeatureTable,
) -> None:
    with pytest.raises(PreprocessingControlError):
        apply_column_permutation(
            table=table, spec=_permutation_spec(), slot=_permutation_slot(role="fault_directed")
        )


def test_a_permutation_cannot_be_replayed_against_another_table(
    permutation: ColumnPermutationResult,
) -> None:
    other = _named_table(_frame(rows=12))
    with pytest.raises(PreprocessingControlError):
        validate_column_permutation(
            permutation, table=other, spec=_permutation_spec(), slot=_permutation_slot()
        )


def test_a_tampered_permutation_digest_is_rejected(
    table: NamedFeatureTable, permutation: ColumnPermutationResult
) -> None:
    forged = _forge(
        permutation, provenance=_forge(permutation.provenance, control_slot_sha256=_HEX_0)
    )
    with pytest.raises(PreprocessingControlError):
        validate_column_permutation(
            forged, table=table, spec=_permutation_spec(), slot=_permutation_slot()
        )


def test_a_non_finite_transformed_value_is_rejected() -> None:
    with pytest.raises(ValidationError, match="not finite"):
        TransformedMatrix(
            schema_version=TRANSFORMED_MATRIX_SCHEMA_VERSION,
            column_names=("a", "b"),
            rows=((1.0, float("inf")),),
        )


def test_a_nan_transformed_value_is_rejected() -> None:
    with pytest.raises(ValidationError, match="not finite"):
        TransformedMatrix(
            schema_version=TRANSFORMED_MATRIX_SCHEMA_VERSION,
            column_names=("a", "b"),
            rows=((1.0, float("nan")),),
        )


def test_a_differing_transformed_matrix_is_an_equivalence_failure(
    table: NamedFeatureTable, permutation: ColumnPermutationResult
) -> None:
    status = benign_equivalence_status(
        result=permutation,
        table=table,
        spec=_permutation_spec(),
        slot=_permutation_slot(),
        test_set=_clean_test_set(),
        evidence=_benign_evidence(permutation, diverge_matrix=True),
    )
    assert status == "benign_equivalence_failure"


def test_differing_predictions_are_an_equivalence_failure_not_a_stable_result(
    table: NamedFeatureTable, permutation: ColumnPermutationResult
) -> None:
    status = benign_equivalence_status(
        result=permutation,
        table=table,
        spec=_permutation_spec(),
        slot=_permutation_slot(),
        test_set=_clean_test_set(),
        evidence=_benign_evidence(permutation, diverge_predictions=3),
    )
    assert status == "benign_equivalence_failure"
    assert status not in ("stable", "eligible_failure", "equivalence_verified_pending_admission")


def test_evidence_bound_to_another_artifact_is_rejected(
    table: NamedFeatureTable, permutation: ColumnPermutationResult
) -> None:
    forged = _forge(_benign_evidence(permutation), permutation_artifact_sha256=_HEX_0)
    with pytest.raises(PreprocessingControlError, match="not bound to this permutation"):
        benign_equivalence_status(
            result=permutation,
            table=table,
            spec=_permutation_spec(),
            slot=_permutation_slot(),
            test_set=_clean_test_set(),
            evidence=forged,
        )


def test_evidence_bound_to_another_test_set_is_rejected(
    table: NamedFeatureTable, permutation: ColumnPermutationResult
) -> None:
    forged = _forge(_benign_evidence(permutation), evaluation_source_sha256=_HEX_0)
    with pytest.raises(PreprocessingControlError, match="not bound to this clean test set"):
        benign_equivalence_status(
            result=permutation,
            table=table,
            spec=_permutation_spec(),
            slot=_permutation_slot(),
            test_set=_clean_test_set(),
            evidence=forged,
        )


def test_benign_evidence_cannot_be_replayed_to_different_records_of_the_same_size(
    table: NamedFeatureTable, permutation: ColumnPermutationResult
) -> None:
    other_test_set = CleanTestSet.model_validate(
        {
            **_clean_test_set().model_dump(),
            "record_ids": tuple(f"OTHER-{index:05d}" for index in range(24)),
        }
    )
    rebound = _benign_evidence(permutation, table=table, test_set=other_test_set)
    with pytest.raises(PreprocessingControlError, match="identical ordered records"):
        benign_equivalence_status(
            result=permutation,
            table=table,
            spec=_permutation_spec(),
            slot=_permutation_slot(),
            test_set=other_test_set,
            evidence=rebound,
        )


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("source_table_sha256", "source feature table"),
        ("record_ids_sha256", "record sequence"),
        ("attested_model_sha256", "fitted model"),
        ("preprocessing_specification_sha256", "preprocessing specification"),
    ],
)
def test_benign_evidence_binding_fields_are_recomputed(
    field: str,
    message: str,
    table: NamedFeatureTable,
    permutation: ColumnPermutationResult,
) -> None:
    evidence = _forge(_benign_evidence(permutation, table=table), **{field: _HEX_0})
    with pytest.raises(PreprocessingControlError, match=message):
        benign_equivalence_status(
            result=permutation,
            table=table,
            spec=_permutation_spec(),
            slot=_permutation_slot(),
            test_set=_clean_test_set(),
            evidence=evidence,
        )


def test_a_caller_cannot_attach_an_equivalence_boolean(
    permutation: ColumnPermutationResult,
) -> None:
    evidence = _benign_evidence(permutation)
    with pytest.raises(ValidationError, match="Extra inputs"):
        BenignEquivalenceEvidence(
            **{**evidence.model_dump(), "equivalence_passed": True}  # type: ignore[arg-type]
        )


def test_no_control_model_owns_a_pass_flag_or_family_class() -> None:
    for model in (BenignEquivalenceEvidence, ColumnPermutationResult, EncoderMappingRepairResult):
        names = set(model.model_fields)
        assert not any(
            token in name
            for name in names
            for token in ("passed", "benign", "family_class", "eligib", "admission")
        )


def test_a_permutation_forged_with_model_construct_is_rejected(
    table: NamedFeatureTable, permutation: ColumnPermutationResult
) -> None:
    forged = ColumnPermutationResult.model_construct(
        schema_version=permutation.schema_version,
        original_table=permutation.original_table,
        permuted_table=permutation.original_table,
        permutation=permutation.permutation,
        provenance=permutation.provenance,
    )
    with pytest.raises(ValidationError, match="different physical column order"):
        validate_column_permutation(
            forged, table=table, spec=_permutation_spec(), slot=_permutation_slot()
        )


# --------------------------------------------------------------------------- #
# Immutability, proven by attempted mutation
# --------------------------------------------------------------------------- #


def test_the_repair_result_and_its_provenance_cannot_be_mutated(
    repair: EncoderMappingRepairResult,
) -> None:
    with pytest.raises(ValidationError):
        repair.record_ids = ()
    with pytest.raises(ValidationError):
        repair.provenance.seed = 0
    with pytest.raises(TypeError):
        repair.clean_block[0][0] = 1  # type: ignore[index]


def test_the_table_and_its_rows_cannot_be_mutated(table: NamedFeatureTable) -> None:
    with pytest.raises(ValidationError):
        table.feature_names = ()
    with pytest.raises(ValidationError):
        table.rows[0].record_id = "x"
    with pytest.raises(TypeError):
        table.rows[0].values[0] = "x"  # type: ignore[index]


def test_the_permutation_result_cannot_be_mutated(permutation: ColumnPermutationResult) -> None:
    with pytest.raises(ValidationError):
        permutation.permutation = (0, 1)
    with pytest.raises(ValidationError):
        permutation.permuted_table.feature_names = ()
    with pytest.raises(ValidationError):
        permutation.provenance.seed = 0


def test_a_transformed_matrix_and_evidence_cannot_be_mutated(
    permutation: ColumnPermutationResult,
) -> None:
    evidence = _benign_evidence(permutation)
    with pytest.raises(ValidationError):
        evidence.original_transformed.column_names = ()
    with pytest.raises(TypeError):
        evidence.original_transformed.rows[0] = ()  # type: ignore[index]
    with pytest.raises(ValidationError):
        evidence.reference_predictions.predictions = ()
