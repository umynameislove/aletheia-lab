"""Property-based invariants for categorical data-drift artifacts."""

from __future__ import annotations

from collections import Counter

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aletheia_lab.benchmark.p2.contracts import CandidateSlot
from aletheia_lab.benchmark.p2.data_drift import (
    DRIFT_INTERVENTION_TYPE,
    DRIFT_SOURCE_SCHEMA_VERSION,
    CategoricalDriftResult,
    CategoricalDriftSpec,
    DataDriftError,
    DriftEvaluationSource,
    apply_categorical_drift,
    validate_categorical_drift,
)
from aletheia_lab.benchmark.p2.identity import DataDriftParameters, FamilyIdentity

_DIGESTS = {letter: letter * 64 for letter in "abcdef"}
_CATEGORIES = ("Month-to-month", "One year", "Two year")
_RECORD_IDS = tuple(f"record-{index:02d}" for index in range(12))
_FEATURE_VALUES = tuple(_CATEGORIES[index % 3] for index in range(12))
_SLOTS: dict[str, tuple[dict[str, float], int]] = {
    "M1-F1": ({"Month-to-month": 0.80, "One year": 0.12, "Two year": 0.08}, 1),
    "M1-F2": ({"Month-to-month": 0.90, "One year": 0.06, "Two year": 0.04}, 3),
    "M1-S1": ({"Month-to-month": 0.60, "One year": 0.20, "Two year": 0.20}, 5),
    "M1-I1": ({"Month-to-month": 0.40, "One year": 0.30, "Two year": 0.30}, 4),
    "M1-R1": ({"Month-to-month": 0.70, "One year": 0.18, "Two year": 0.12}, 2),
}


def _source(
    *,
    record_ids: tuple[str, ...] = _RECORD_IDS,
    feature_values: tuple[str, ...] = _FEATURE_VALUES,
    matrix_sha256: str = _DIGESTS["e"],
) -> DriftEvaluationSource:
    return DriftEvaluationSource(
        schema_version=DRIFT_SOURCE_SCHEMA_VERSION,
        split="test",
        dataset_snapshot_id="telco_customer_churn@2026-07",
        dataset_sha256=_DIGESTS["a"],
        model_data_split_manifest_sha256=_DIGESTS["b"],
        feature="Contract",
        record_ids=record_ids,
        feature_values=feature_values,
        attested_raw_feature_matrix_sha256=matrix_sha256,
        attested_raw_target_sha256=_DIGESTS["f"],
        attested_model_sha256=_DIGESTS["c"],
        attested_preprocessing_specification_sha256=_DIGESTS["d"],
    )


def _parameters(slot_id: str) -> DataDriftParameters:
    target, _ = _SLOTS[slot_id]
    return DataDriftParameters(
        feature="Contract", target_distribution=target, output_size=300
    )


def _spec(slot_id: str) -> CategoricalDriftSpec:
    _, seed = _SLOTS[slot_id]
    return CategoricalDriftSpec(
        injection_id=slot_id,
        parameters=_parameters(slot_id),
        seed=seed,
    )


def _slot(slot_id: str) -> CandidateSlot:
    _, seed = _SLOTS[slot_id]
    return CandidateSlot(
        slot_id=slot_id,
        fault_type="data_drift",
        slot_kind="reserve" if slot_id == "M1-R1" else "primary",
        role="fault_directed",
        reserve_order=1 if slot_id == "M1-R1" else None,
        identity=FamilyIdentity(
            dataset_snapshot_id="telco_customer_churn@2026-07",
            dataset_sha256=_DIGESTS["a"],
            model_data_split_manifest_sha256=_DIGESTS["b"],
            fault_type="data_drift",
            intervention_type=DRIFT_INTERVENTION_TYPE,
            canonical_intervention_parameters=_parameters(slot_id),
            seed=seed,
            reference_construction_id="clean-test-reference/v1",
            injector_contract_version="drift/v1",
            model_specification_sha256=_DIGESTS["c"],
            preprocessing_specification_sha256=_DIGESTS["d"],
            identity_schema_version="p2-family-identity/v1",
        ),
    )


_SLOT_IDS = st.sampled_from(tuple(_SLOTS))
_DISTINCT_SLOT_PAIRS = st.sampled_from(
    tuple((first, second) for first in _SLOTS for second in _SLOTS if first != second)
)


@given(slot_id=_SLOT_IDS)
@settings(max_examples=40)
def test_drift_construction_is_deterministic(slot_id: str) -> None:
    source = _source()
    spec = _spec(slot_id)
    slot = _slot(slot_id)

    first = apply_categorical_drift(source=source, spec=spec, slot=slot)
    second = apply_categorical_drift(source=source, spec=spec, slot=slot)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.artifact_sha256() == second.artifact_sha256()


@given(slot_id=_SLOT_IDS, order=st.permutations(tuple(range(len(_RECORD_IDS)))))
@settings(max_examples=40)
def test_drift_selection_is_independent_of_source_row_order(
    slot_id: str, order: list[int]
) -> None:
    permuted_source = _source(
        record_ids=tuple(_RECORD_IDS[index] for index in order),
        feature_values=tuple(_FEATURE_VALUES[index] for index in order),
    )
    original = apply_categorical_drift(
        source=_source(), spec=_spec(slot_id), slot=_slot(slot_id)
    )
    permuted = apply_categorical_drift(
        source=permuted_source, spec=_spec(slot_id), slot=_slot(slot_id)
    )

    assert permuted.selected_record_ids == original.selected_record_ids
    assert permuted.selected_feature_values == original.selected_feature_values
    assert validate_categorical_drift(
        permuted,
        source=permuted_source,
        spec=_spec(slot_id),
        slot=_slot(slot_id),
    ) == permuted


@given(slot_id=_SLOT_IDS)
@settings(max_examples=40)
def test_drift_reported_counts_match_selected_rows(slot_id: str) -> None:
    result = apply_categorical_drift(
        source=_source(), spec=_spec(slot_id), slot=_slot(slot_id)
    )
    independent_counts = Counter(result.selected_feature_values)

    assert sum(independent_counts.values()) == 300
    assert dict(result.category_counts) == dict(sorted(independent_counts.items()))
    source_values = dict(zip(_RECORD_IDS, _FEATURE_VALUES, strict=True))
    assert all(
        source_values[record_id] == category
        for record_id, category in zip(
            result.selected_record_ids, result.selected_feature_values, strict=True
        )
    )


@given(slot_pair=_DISTINCT_SLOT_PAIRS)
@settings(max_examples=40)
def test_drift_slot_change_changes_artifact_identity(slot_pair: tuple[str, str]) -> None:
    first_slot, second_slot = slot_pair
    first = apply_categorical_drift(
        source=_source(), spec=_spec(first_slot), slot=_slot(first_slot)
    )
    second = apply_categorical_drift(
        source=_source(), spec=_spec(second_slot), slot=_slot(second_slot)
    )

    assert first.artifact_sha256() != second.artifact_sha256()


@given(slot_id=_SLOT_IDS)
@settings(max_examples=40)
def test_drift_forged_selection_is_rejected(slot_id: str) -> None:
    source = _source()
    spec = _spec(slot_id)
    slot = _slot(slot_id)
    result = apply_categorical_drift(source=source, spec=spec, slot=slot)
    forged = result.model_copy(
        update={"selected_record_ids": tuple(reversed(result.selected_record_ids))}
    )

    with pytest.raises(DataDriftError, match="drawn rows"):
        validate_categorical_drift(forged, source=source, spec=spec, slot=slot)


@given(slot_id=_SLOT_IDS)
@settings(max_examples=40)
def test_drift_artifact_cannot_be_replayed_on_another_source(slot_id: str) -> None:
    source = _source()
    spec = _spec(slot_id)
    slot = _slot(slot_id)
    result = apply_categorical_drift(source=source, spec=spec, slot=slot)
    other_source = _source(matrix_sha256="0" * 64)

    with pytest.raises(DataDriftError):
        validate_categorical_drift(result, source=other_source, spec=spec, slot=slot)


def test_drift_artifact_has_no_research_outcome_fields() -> None:
    forbidden = {"passed", "eligible", "verdict", "outcome", "family_class", "cause"}
    assert not (set(CategoricalDriftResult.model_fields) & forbidden)
