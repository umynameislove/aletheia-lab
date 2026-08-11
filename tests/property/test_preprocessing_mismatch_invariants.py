"""Property-based invariants for inference-time encoder mapping mismatches."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aletheia_lab.benchmark.p2.contracts import CandidateSlot
from aletheia_lab.benchmark.p2.identity import FamilyIdentity, PreprocessingBugParameters
from aletheia_lab.benchmark.p2.preprocessing_intervention import (
    ALPHA_PREPROCESSING_MODE,
    CATEGORY_RANK_RULE,
    CATEGORY_VOCABULARY_SCHEMA_VERSION,
    INFERENCE_SOURCE_SCHEMA_VERSION,
    MISMATCH_INTERVENTION_TYPE,
    CategoryFrequency,
    EncoderMappingMismatchResult,
    EncoderMappingMismatchSpec,
    FrozenCategoryVocabulary,
    InferenceTransformSource,
    PreprocessingInterventionError,
    apply_encoder_mapping_mismatch,
    validate_preprocessing_intervention,
)

_DIGESTS = {letter: letter * 64 for letter in "abcdef"}
_FEATURE = "Contract"
_RANKED = ("Month-to-month", "Two year", "One year")
_COUNTS = {"Month-to-month": 3875, "Two year": 1695, "One year": 1473}
_RAW_CATEGORIES = (
    "Month-to-month",
    "One year",
    "Two year",
    "Month-to-month",
    "Month-to-month",
    "Two year",
    "One year",
    "Month-to-month",
    "Two year",
    "Month-to-month",
    "One year",
    "Month-to-month",
)
_RECORD_IDS = tuple(f"record-{index:02d}" for index in range(len(_RAW_CATEGORIES)))
_FAULT_SLOTS: dict[str, tuple[int, int, int]] = {
    "M3-F1": (3, 2, 301),
    "M3-F2": (2, 1, 302),
    "M3-F3": (1, 3, 303),
    "M3-R1": (3, 1, 306),
    "M3-R2": (2, 3, 307),
    "M3-R3": (1, 2, 308),
}


def _vocabulary() -> FrozenCategoryVocabulary:
    return FrozenCategoryVocabulary(
        schema_version=CATEGORY_VOCABULARY_SCHEMA_VERSION,
        feature=_FEATURE,
        split="train",
        rank_rule=CATEGORY_RANK_RULE,
        frequencies=tuple(
            CategoryFrequency(category=category, count=count)
            for category, count in _COUNTS.items()
        ),
    )


def _source(
    *,
    record_ids: tuple[str, ...] = _RECORD_IDS,
    raw_categories: tuple[str, ...] = _RAW_CATEGORIES,
    matrix_sha256: str = _DIGESTS["a"],
) -> InferenceTransformSource:
    return InferenceTransformSource(
        schema_version=INFERENCE_SOURCE_SCHEMA_VERSION,
        split="test",
        feature=_FEATURE,
        record_ids=record_ids,
        raw_categories=raw_categories,
        vocabulary=_vocabulary(),
        attested_raw_feature_matrix_sha256=matrix_sha256,
        attested_raw_target_sha256=_DIGESTS["b"],
        attested_model_sha256=_DIGESTS["c"],
        attested_fitted_training_transform_sha256=_DIGESTS["d"],
        attested_other_transform_config_sha256=_DIGESTS["e"],
    )


def _parameters(slot_id: str) -> PreprocessingBugParameters:
    source_rank, mapped_rank, _ = _FAULT_SLOTS[slot_id]
    return PreprocessingBugParameters(
        target_feature=_FEATURE,
        source_rank=source_rank,
        mapped_rank=mapped_rank,
        mode=ALPHA_PREPROCESSING_MODE,
        transform_name="one_hot_encoder",
    )


def _spec(slot_id: str) -> EncoderMappingMismatchSpec:
    source_rank, mapped_rank, seed = _FAULT_SLOTS[slot_id]
    return EncoderMappingMismatchSpec(
        injection_id=slot_id,
        parameters=_parameters(slot_id),
        source_category=_RANKED[source_rank - 1],
        mapped_category=_RANKED[mapped_rank - 1],
        seed=seed,
    )


def _slot(slot_id: str) -> CandidateSlot:
    _, _, seed = _FAULT_SLOTS[slot_id]
    is_reserve = slot_id.startswith("M3-R")
    return CandidateSlot(
        slot_id=slot_id,
        fault_type="preprocessing_bug",
        slot_kind="reserve" if is_reserve else "primary",
        role="fault_directed",
        reserve_order=int(slot_id[-1]) if is_reserve else None,
        identity=FamilyIdentity(
            dataset_snapshot_id="telco_customer_churn@2026-07",
            dataset_sha256=_DIGESTS["a"],
            model_data_split_manifest_sha256=_DIGESTS["b"],
            fault_type="preprocessing_bug",
            intervention_type=MISMATCH_INTERVENTION_TYPE,
            canonical_intervention_parameters=_parameters(slot_id),
            seed=seed,
            reference_construction_id="clean-test-reference/v1",
            injector_contract_version="preprocessing-mismatch/v1",
            model_specification_sha256=_DIGESTS["c"],
            preprocessing_specification_sha256=_DIGESTS["d"],
            identity_schema_version="p2-family-identity/v1",
        ),
    )


_SLOT_IDS = st.sampled_from(tuple(_FAULT_SLOTS))
_DISTINCT_SLOT_PAIRS = st.sampled_from(
    tuple(
        (first, second)
        for first in _FAULT_SLOTS
        for second in _FAULT_SLOTS
        if first != second
    )
)


@given(slot_id=_SLOT_IDS)
@settings(max_examples=40)
def test_preprocessing_mismatch_is_deterministic(slot_id: str) -> None:
    source = _source()
    spec = _spec(slot_id)
    slot = _slot(slot_id)

    first = apply_encoder_mapping_mismatch(source=source, spec=spec, slot=slot)
    second = apply_encoder_mapping_mismatch(source=source, spec=spec, slot=slot)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.artifact_sha256() == second.artifact_sha256()


@given(slot_id=_SLOT_IDS, order=st.permutations(tuple(range(len(_RECORD_IDS)))))
@settings(max_examples=40)
def test_preprocessing_mismatch_preserves_permuted_membership_and_order(
    slot_id: str, order: list[int]
) -> None:
    source = _source(
        record_ids=tuple(_RECORD_IDS[index] for index in order),
        raw_categories=tuple(_RAW_CATEGORIES[index] for index in order),
    )
    result = apply_encoder_mapping_mismatch(
        source=source, spec=_spec(slot_id), slot=_slot(slot_id)
    )

    assert result.record_ids == source.record_ids
    assert result.raw_categories == source.raw_categories
    assert set(result.record_ids) == set(_RECORD_IDS)
    assert validate_preprocessing_intervention(
        result, source=source, spec=_spec(slot_id), slot=_slot(slot_id)
    ) == result


@given(slot_id=_SLOT_IDS)
@settings(max_examples=40)
def test_preprocessing_mismatch_changes_only_declared_source_rows(slot_id: str) -> None:
    source = _source()
    spec = _spec(slot_id)
    result = apply_encoder_mapping_mismatch(source=source, spec=spec, slot=_slot(slot_id))
    changed_indices = {
        index
        for index, (raw, transformed) in enumerate(
            zip(result.raw_categories, result.inference_view_categories, strict=True)
        )
        if raw != transformed
    }
    expected_indices = {
        index
        for index, category in enumerate(source.raw_categories)
        if category == spec.source_category
    }

    assert changed_indices == expected_indices
    assert result.affected_record_ids == tuple(
        sorted(source.record_ids[index] for index in expected_indices)
    )
    assert all(
        result.inference_view_categories[index] == spec.mapped_category
        for index in expected_indices
    )


@given(slot_pair=_DISTINCT_SLOT_PAIRS)
@settings(max_examples=40)
def test_preprocessing_slot_change_changes_artifact_identity(
    slot_pair: tuple[str, str]
) -> None:
    first_slot, second_slot = slot_pair
    first = apply_encoder_mapping_mismatch(
        source=_source(), spec=_spec(first_slot), slot=_slot(first_slot)
    )
    second = apply_encoder_mapping_mismatch(
        source=_source(), spec=_spec(second_slot), slot=_slot(second_slot)
    )

    assert first.artifact_sha256() != second.artifact_sha256()


@given(slot_id=_SLOT_IDS)
@settings(max_examples=40)
def test_preprocessing_forged_view_is_rejected(slot_id: str) -> None:
    source = _source()
    spec = _spec(slot_id)
    slot = _slot(slot_id)
    result = apply_encoder_mapping_mismatch(source=source, spec=spec, slot=slot)
    forged = result.model_copy(update={"inference_view_categories": result.raw_categories})

    with pytest.raises(PreprocessingInterventionError, match="inference view"):
        validate_preprocessing_intervention(forged, source=source, spec=spec, slot=slot)


@given(slot_id=_SLOT_IDS)
@settings(max_examples=40)
def test_preprocessing_artifact_cannot_be_replayed_on_another_source(slot_id: str) -> None:
    source = _source()
    spec = _spec(slot_id)
    slot = _slot(slot_id)
    result = apply_encoder_mapping_mismatch(source=source, spec=spec, slot=slot)
    other_source = _source(matrix_sha256="0" * 64)

    with pytest.raises(PreprocessingInterventionError):
        validate_preprocessing_intervention(
            result, source=other_source, spec=spec, slot=slot
        )


def test_preprocessing_artifact_has_no_research_outcome_fields() -> None:
    forbidden = {"passed", "eligible", "verdict", "outcome", "family_class", "cause"}
    assert not (set(EncoderMappingMismatchResult.model_fields) & forbidden)
