"""Regression tests for the deterministic inference encoder mapping mismatch.

Two error kinds are expected, and the tests keep them apart deliberately:

* ``ValidationError`` when a single object is malformed, because model
  validators report through Pydantic;
* ``PreprocessingInterventionError`` when objects disagree with one another,
  because those checks run outside any model.

Every invariant is covered twice: once by a positive case showing the intended
behaviour, and once by a forged artifact showing that the authoritative
validator rejects a plausible-looking fake.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError
from sklearn.preprocessing import OneHotEncoder

from aletheia_lab.benchmark.p2.contracts import CandidateSlot
from aletheia_lab.benchmark.p2.identity import FamilyIdentity, PreprocessingBugParameters
from aletheia_lab.benchmark.p2.preprocessing_intervention import (
    ALPHA_PREPROCESSING_MODE,
    ALPHA_RANK_COUNT,
    CATEGORY_RANK_RULE,
    CATEGORY_VOCABULARY_SCHEMA_VERSION,
    INFERENCE_SOURCE_SCHEMA_VERSION,
    MISMATCH_INTERVENTION_TYPE,
    MISMATCH_PROTOCOL_VERSION,
    CategoryFrequency,
    EncoderMappingMismatchResult,
    EncoderMappingMismatchSpec,
    FrozenCategoryVocabulary,
    InferenceTransformSource,
    PreprocessingInterventionError,
    apply_encoder_mapping_mismatch,
    validate_preprocessing_intervention,
)
from aletheia_lab.benchmark.p2.validation import ContractViolation, validate_frozen_alpha_slot

_HEX_A = "a" * 64
_HEX_B = "b" * 64
_HEX_C = "c" * 64
_HEX_D = "d" * 64
_HEX_E = "e" * 64
_HEX_F = "f" * 64
_HEX_0 = "0" * 64

_FEATURE = "Contract"
_TRANSFORM = "one_hot_encoder"

#: Telco churn contract categories with their real training shape. Ranking is by
#: descending count, so rank 1 is Month-to-month, rank 2 is Two year and rank 3
#: is One year — deliberately *not* alphabetical, so a test that confuses rank
#: order with column order fails.
_COUNTS: dict[str, int] = {"Month-to-month": 3875, "Two year": 1695, "One year": 1473}
_RANKED = ("Month-to-month", "Two year", "One year")
_COLUMNS = ("Month-to-month", "One year", "Two year")

#: The frozen alpha fault slots, restated here as the contract's own numbers so a
#: silent change shows up as a test failure rather than as a different experiment.
_FAULT_SLOTS: dict[str, tuple[int, int, int]] = {
    "M3-F1": (3, 2, 301),
    "M3-F2": (2, 1, 302),
    "M3-F3": (1, 3, 303),
    "M3-R1": (3, 1, 306),
    "M3-R2": (2, 3, 307),
    "M3-R3": (1, 2, 308),
}

#: Field names that must never appear in an intervention artifact, at any depth.
_FORBIDDEN_FIELD_TOKENS = (
    "accuracy",
    "f1",
    "recall",
    "confusion",
    "metric",
    "outcome",
    "eligib",
    "family_class",
    "improvement",
    "benign",
    "cause",
    "passed",
    "expected_behavior",
)


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


#: Twelve evaluation rows covering every category, with the rare ones present so
#: that any rank can be used as a source without the run becoming a no-op.
_RAW = (
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


def _ids(count: int = len(_RAW)) -> tuple[str, ...]:
    return tuple(f"{index:05d}-SYNTH" for index in range(count))


def _source(**overrides: object) -> InferenceTransformSource:
    payload: dict[str, object] = {
        "schema_version": INFERENCE_SOURCE_SCHEMA_VERSION,
        "split": "test",
        "feature": _FEATURE,
        "record_ids": _ids(),
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
    mode: str = ALPHA_PREPROCESSING_MODE,
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


def _spec(slot_id: str = "M3-F1", **overrides: object) -> EncoderMappingMismatchSpec:
    source_rank, mapped_rank, seed = _FAULT_SLOTS[slot_id]
    payload: dict[str, object] = {
        "injection_id": slot_id,
        "parameters": _parameters(source_rank=source_rank, mapped_rank=mapped_rank),
        "source_category": _RANKED[source_rank - 1],
        "mapped_category": _RANKED[mapped_rank - 1],
        "seed": seed,
    }
    payload.update(overrides)
    return EncoderMappingMismatchSpec(**payload)  # type: ignore[arg-type]


def _slot(
    slot_id: str = "M3-F1",
    *,
    parameters: PreprocessingBugParameters | None = None,
    seed: int | None = None,
    role: str = "fault_directed",
    intervention_type: str = MISMATCH_INTERVENTION_TYPE,
    fault_type: str = "preprocessing_bug",
) -> CandidateSlot:
    default_source, default_mapped, default_seed = _FAULT_SLOTS.get(slot_id, (1, 3, 304))
    resolved = parameters or _parameters(source_rank=default_source, mapped_rank=default_mapped)
    is_reserve = slot_id.startswith("M3-R")
    return CandidateSlot(
        slot_id=slot_id,
        fault_type=fault_type,  # type: ignore[arg-type]
        slot_kind="reserve" if is_reserve else "primary",
        role=role,  # type: ignore[arg-type]
        reserve_order=int(slot_id[-1]) if is_reserve else None,
        identity=FamilyIdentity(
            dataset_snapshot_id="telco_customer_churn@2026-07",
            dataset_sha256=_HEX_A,
            model_data_split_manifest_sha256=_HEX_B,
            fault_type=fault_type,  # type: ignore[arg-type]
            intervention_type=intervention_type,
            canonical_intervention_parameters=resolved,
            seed=default_seed if seed is None else seed,
            reference_construction_id="clean-test-reference/v1",
            injector_contract_version="preprocessing-mismatch/v1",
            model_specification_sha256=_HEX_C,
            preprocessing_specification_sha256=_HEX_D,
            identity_schema_version="p2-family-identity/v1",
        ),
    )


def _forge(model: Any, **updates: object) -> Any:
    """Bypass validation the way a careless caller would."""

    return model.model_copy(update=updates)


def _forge_provenance(result: Any, **updates: object) -> Any:
    return _forge(result, provenance=_forge(result.provenance, **updates))


def _field_tokens(model: type[BaseModel], seen: set[type[BaseModel]] | None = None) -> set[str]:
    """Collect every field name reachable from a model, including nested ones."""

    seen = seen if seen is not None else set()
    if model in seen:
        return set()
    seen.add(model)
    names: set[str] = set()
    for name, field in model.model_fields.items():
        names.add(name)
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            names |= _field_tokens(annotation, seen)
    return names


@pytest.fixture
def source() -> InferenceTransformSource:
    return _source()


@pytest.fixture
def spec() -> EncoderMappingMismatchSpec:
    return _spec()


@pytest.fixture
def slot() -> CandidateSlot:
    return _slot()


@pytest.fixture
def result(
    source: InferenceTransformSource, spec: EncoderMappingMismatchSpec, slot: CandidateSlot
) -> EncoderMappingMismatchResult:
    return apply_encoder_mapping_mismatch(source=source, spec=spec, slot=slot)


# --------------------------------------------------------------------------- #
# Positive
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("slot_id", ["M3-F1", "M3-F2", "M3-F3"])
def test_each_frozen_fault_slot_produces_a_valid_intervention(slot_id: str) -> None:
    source_rank, mapped_rank, _ = _FAULT_SLOTS[slot_id]
    produced = apply_encoder_mapping_mismatch(
        source=_source(), spec=_spec(slot_id), slot=_slot(slot_id)
    )
    validated = validate_preprocessing_intervention(
        produced, source=_source(), spec=_spec(slot_id), slot=_slot(slot_id)
    )
    assert validated.provenance.source_rank == source_rank
    assert validated.provenance.mapped_rank == mapped_rank
    assert validated.provenance.injection_id == slot_id
    assert validated.provenance.source_category == _RANKED[source_rank - 1]
    assert validated.provenance.mapped_category == _RANKED[mapped_rank - 1]
    assert validated.inference_view_categories != validated.raw_categories


def test_ranking_follows_descending_count(source: InferenceTransformSource) -> None:
    assert source.vocabulary.ranked_categories() == _RANKED
    assert source.vocabulary.category_for_rank(1) == "Month-to-month"
    assert source.vocabulary.category_for_rank(3) == "One year"
    assert source.vocabulary.rank_for_category("Two year") == 2


def test_ties_are_broken_by_canonical_lexical_order() -> None:
    tied = _vocabulary(
        frequencies=(
            CategoryFrequency(category="Two year", count=100),
            CategoryFrequency(category="Month-to-month", count=100),
            CategoryFrequency(category="One year", count=100),
        )
    )
    assert tied.ranked_categories() == ("Month-to-month", "One year", "Two year")


def test_rank_order_is_not_column_order(source: InferenceTransformSource) -> None:
    """Column layout follows the fitted encoder, which sorts; rank does not."""

    assert source.vocabulary.encoder_column_order() == _COLUMNS
    assert source.vocabulary.ranked_categories() != _COLUMNS


def test_the_same_input_produces_a_byte_equivalent_artifact(
    result: EncoderMappingMismatchResult,
) -> None:
    again = apply_encoder_mapping_mismatch(source=_source(), spec=_spec(), slot=_slot())
    assert again.artifact_sha256() == result.artifact_sha256()
    assert again.model_dump(mode="json") == result.model_dump(mode="json")


def test_record_order_is_preserved(
    source: InferenceTransformSource, result: EncoderMappingMismatchResult
) -> None:
    assert result.record_ids == source.record_ids


def test_membership_is_preserved(
    source: InferenceTransformSource, result: EncoderMappingMismatchResult
) -> None:
    assert set(result.record_ids) == set(source.record_ids)
    assert len(result.record_ids) == len(source.record_ids)


def test_the_raw_column_is_never_rewritten(
    source: InferenceTransformSource, result: EncoderMappingMismatchResult
) -> None:
    assert result.raw_categories == source.raw_categories
    assert result.provenance.raw_categories_sha256 == source.raw_categories_sha256()


def test_the_caller_source_object_is_not_mutated() -> None:
    original = _source()
    before = original.model_dump(mode="json")
    apply_encoder_mapping_mismatch(source=original, spec=_spec(), slot=_slot())
    assert original.model_dump(mode="json") == before


def test_only_source_category_rows_change_in_the_inference_view(
    source: InferenceTransformSource, result: EncoderMappingMismatchResult
) -> None:
    source_category = source.vocabulary.category_for_rank(_FAULT_SLOTS["M3-F1"][0])
    mapped_category = source.vocabulary.category_for_rank(_FAULT_SLOTS["M3-F1"][1])
    changed = [
        index
        for index, (raw, view) in enumerate(
            zip(result.raw_categories, result.inference_view_categories, strict=True)
        )
        if raw != view
    ]
    assert changed
    assert all(result.raw_categories[index] == source_category for index in changed)
    assert all(result.inference_view_categories[index] == mapped_category for index in changed)


def test_only_the_target_feature_block_differs_between_the_two_encodings(
    result: EncoderMappingMismatchResult,
) -> None:
    """Both blocks describe the target feature only, and differ on affected rows."""

    assert len(result.encoder_column_order) == len(_COLUMNS)
    affected = set(result.affected_record_ids)
    for record_id, reference, mismatched in zip(
        result.record_ids, result.reference_block, result.mismatched_block, strict=True
    ):
        if record_id in affected:
            assert reference != mismatched
        else:
            assert reference == mismatched


def test_output_shape_is_unchanged(result: EncoderMappingMismatchResult) -> None:
    width = len(result.encoder_column_order)
    assert all(len(row) == width for row in result.reference_block)
    assert all(len(row) == width for row in result.mismatched_block)
    assert len(result.reference_block) == len(result.mismatched_block) == len(result.record_ids)


def test_blocks_match_the_real_fitted_one_hot_encoder(
    result: EncoderMappingMismatchResult,
) -> None:
    """Ground the stdlib kernel in the encoder used by the baseline pipeline."""

    training_column = [[entry.category] for entry in _vocabulary().frequencies]
    encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    encoder.fit(training_column)

    reference = encoder.transform([[value] for value in result.raw_categories]).astype(int)
    mismatched = encoder.transform([[value] for value in result.inference_view_categories]).astype(
        int
    )

    assert tuple(str(value) for value in encoder.categories_[0]) == result.encoder_column_order
    assert tuple(tuple(int(value) for value in row) for row in reference) == result.reference_block
    assert (
        tuple(tuple(int(value) for value in row) for row in mismatched) == result.mismatched_block
    )


def test_the_result_cannot_carry_a_metric_outcome_or_cause() -> None:
    names = _field_tokens(EncoderMappingMismatchResult)
    offending = {name for name in names for token in _FORBIDDEN_FIELD_TOKENS if token in name}
    assert offending == set()


def test_the_result_pins_its_protocol_mode_and_intervention_type(
    result: EncoderMappingMismatchResult,
) -> None:
    provenance = result.provenance
    assert provenance.mismatch_protocol_version == MISMATCH_PROTOCOL_VERSION
    assert provenance.intervention_type == MISMATCH_INTERVENTION_TYPE
    assert provenance.mode == ALPHA_PREPROCESSING_MODE
    assert provenance.rank_rule == CATEGORY_RANK_RULE


def test_the_public_api_exports_the_intervention() -> None:
    import aletheia_lab.benchmark.p2 as package

    for name in (
        "EncoderMappingMismatchResult",
        "EncoderMappingMismatchSpec",
        "FrozenCategoryVocabulary",
        "InferenceTransformSource",
        "PreprocessingInterventionError",
        "apply_encoder_mapping_mismatch",
        "validate_frozen_alpha_slot",
        "validate_preprocessing_intervention",
    ):
        assert name in package.__all__
        assert hasattr(package, name)


# --------------------------------------------------------------------------- #
# Invalid input
# --------------------------------------------------------------------------- #


def test_a_specification_for_another_feature_is_rejected() -> None:
    parameters = _parameters(source_rank=3, mapped_rank=2, target_feature="PaymentMethod")
    with pytest.raises(ValidationError, match="targets 'Contract' only"):
        _spec(parameters=parameters)


def test_a_raw_category_outside_the_vocabulary_is_rejected() -> None:
    with pytest.raises(ValidationError, match="outside the frozen training vocabulary"):
        _source(raw_categories=("Weekly", *_RAW[1:]))


def test_a_duplicate_category_in_the_vocabulary_is_rejected() -> None:
    with pytest.raises(ValidationError, match="categories must be unique"):
        _vocabulary(
            frequencies=(
                CategoryFrequency(category="One year", count=10),
                CategoryFrequency(category="One year", count=20),
                CategoryFrequency(category="Two year", count=30),
            )
        )


def test_a_vocabulary_too_small_for_the_alpha_ranks_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least 3 categories"):
        _vocabulary(
            frequencies=(
                CategoryFrequency(category="One year", count=10),
                CategoryFrequency(category="Two year", count=30),
            )
        )


def test_a_zero_or_negative_category_count_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CategoryFrequency(category="One year", count=0)


def test_equal_source_and_mapped_ranks_are_rejected() -> None:
    with pytest.raises(ValidationError, match="must differ"):
        _parameters(source_rank=2, mapped_rank=2)


def test_a_rank_outside_the_alpha_range_is_rejected() -> None:
    with pytest.raises(ValidationError, match=f"first {ALPHA_RANK_COUNT} ranks"):
        _spec(parameters=_parameters(source_rank=1, mapped_rank=4))


def test_absent_ranks_are_rejected_for_a_mismatch() -> None:
    with pytest.raises(ValidationError, match="both a source rank and a mapped rank"):
        _spec(parameters=_parameters(source_rank=None, mapped_rank=None))


def test_a_seed_that_differs_from_the_slot_is_rejected(spec: EncoderMappingMismatchSpec) -> None:
    with pytest.raises(PreprocessingInterventionError, match="role/seed contract"):
        apply_encoder_mapping_mismatch(source=_source(), spec=spec, slot=_slot(seed=999))


def test_jointly_forged_slot_and_spec_cannot_bypass_the_frozen_grid() -> None:
    """Agreement between two forged inputs is not evidence of the frozen plan."""

    parameters = _parameters(source_rank=1, mapped_rank=2)
    forged_spec = _spec(
        parameters=parameters,
        source_category=_RANKED[0],
        mapped_category=_RANKED[1],
        seed=999,
    )
    forged_slot = _slot("M3-F1", parameters=parameters, seed=999)
    with pytest.raises(PreprocessingInterventionError, match="role/seed contract"):
        apply_encoder_mapping_mismatch(source=_source(), spec=forged_spec, slot=forged_slot)


def test_an_unplanned_but_well_formed_slot_identifier_is_rejected() -> None:
    parameters = _parameters(source_rank=1, mapped_rank=2)
    forged_spec = _spec(
        injection_id="M3-F9",
        parameters=parameters,
        source_category=_RANKED[0],
        mapped_category=_RANKED[1],
        seed=999,
    )
    forged_slot = _slot("M3-F9", parameters=parameters, seed=999)
    with pytest.raises(PreprocessingInterventionError, match="not part of the frozen alpha grid"):
        apply_encoder_mapping_mismatch(source=_source(), spec=forged_spec, slot=forged_slot)


def test_injection_id_must_name_the_supplied_frozen_slot() -> None:
    forged_spec = _spec(injection_id="M3-F2")
    with pytest.raises(PreprocessingInterventionError, match="injection_id differs"):
        apply_encoder_mapping_mismatch(source=_source(), spec=forged_spec, slot=_slot("M3-F1"))


def test_declared_categories_must_be_the_categories_at_the_frozen_ranks() -> None:
    forged_spec = _spec(source_category="Month-to-month")
    with pytest.raises(PreprocessingInterventionError, match="category pair differs"):
        apply_encoder_mapping_mismatch(source=_source(), spec=forged_spec, slot=_slot("M3-F1"))


def test_a_transform_name_that_differs_from_the_slot_is_rejected() -> None:
    with pytest.raises(ValidationError, match="requires transform 'one_hot_encoder'"):
        _spec(
            parameters=_parameters(source_rank=3, mapped_rank=2, transform_name="ordinal_encoder")
        )


def test_the_authoritative_grid_also_rejects_a_forged_transform_name() -> None:
    forged_parameters = _parameters(
        source_rank=3,
        mapped_rank=2,
        transform_name="ordinal_encoder",
    )
    with pytest.raises(ContractViolation, match="target/mode/transform differs"):
        validate_frozen_alpha_slot(_slot("M3-F1", parameters=forged_parameters))


def test_training_mode_is_refused_by_the_specification() -> None:
    with pytest.raises(ValidationError, match="inference transform only"):
        _spec(parameters=_parameters(source_rank=3, mapped_rank=2, mode="both"))


def test_duplicate_record_identifiers_are_rejected() -> None:
    ids = _ids()
    with pytest.raises(ValidationError, match="record IDs must be unique"):
        _source(record_ids=(ids[0], *ids[1:-1], ids[0]))


def test_a_length_mismatch_between_identifiers_and_values_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must align"):
        _source(raw_categories=_RAW[:-1])


@pytest.mark.parametrize("category", [" One year", "One year ", "One\tyear", ""])
def test_a_noncanonical_category_string_is_rejected(category: str) -> None:
    with pytest.raises(ValidationError):
        CategoryFrequency(category=category, count=5)


def test_a_source_feature_that_disagrees_with_the_vocabulary_is_rejected() -> None:
    with pytest.raises(ValidationError, match="targets 'Contract' only"):
        _source(feature="PaymentMethod")


def test_a_jointly_forged_source_and_vocabulary_cannot_change_the_alpha_target() -> None:
    vocabulary = _vocabulary(feature="PaymentMethod")
    with pytest.raises(ValidationError, match="targets 'Contract' only"):
        _source(feature="PaymentMethod", vocabulary=vocabulary)


def test_a_source_category_absent_from_the_evaluation_rows_is_rejected() -> None:
    """Rank 3 exists in training, but if no evaluation row carries it there is no effect."""

    without_rare = tuple(
        "Month-to-month" if category == "One year" else category for category in _RAW
    )
    with pytest.raises(PreprocessingInterventionError, match="would have no effect"):
        apply_encoder_mapping_mismatch(
            source=_source(raw_categories=without_rare), spec=_spec("M3-F1"), slot=_slot("M3-F1")
        )


def test_a_non_integer_indicator_is_rejected(result: EncoderMappingMismatchResult) -> None:
    """A float, including NaN, cannot enter an indicator block."""

    broken = ((float("nan"), 0.0, 0.0), *result.mismatched_block[1:])
    with pytest.raises(ValidationError):
        EncoderMappingMismatchResult(
            record_ids=result.record_ids,
            raw_categories=result.raw_categories,
            inference_view_categories=result.inference_view_categories,
            encoder_column_order=result.encoder_column_order,
            reference_block=result.reference_block,
            mismatched_block=broken,  # type: ignore[arg-type]
            affected_record_ids=result.affected_record_ids,
            provenance=result.provenance,
        )


def test_an_indicator_row_with_two_hot_columns_is_rejected(
    result: EncoderMappingMismatchResult,
) -> None:
    broken = ((1, 1, 0), *result.mismatched_block[1:])
    with pytest.raises(ValidationError, match="exactly one indicator"):
        EncoderMappingMismatchResult(
            record_ids=result.record_ids,
            raw_categories=result.raw_categories,
            inference_view_categories=result.inference_view_categories,
            encoder_column_order=result.encoder_column_order,
            reference_block=result.reference_block,
            mismatched_block=broken,
            affected_record_ids=result.affected_record_ids,
            provenance=result.provenance,
        )


# --------------------------------------------------------------------------- #
# One-factor violations
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "field",
    [
        "attested_raw_feature_matrix_sha256",
        "attested_raw_target_sha256",
        "attested_model_sha256",
        "attested_fitted_training_transform_sha256",
        "attested_other_transform_config_sha256",
    ],
)
def test_a_changed_attestation_breaks_validation(
    field: str,
    spec: EncoderMappingMismatchSpec,
    slot: CandidateSlot,
    result: EncoderMappingMismatchResult,
) -> None:
    forged = _forge_provenance(result, **{field: _HEX_0})
    with pytest.raises(PreprocessingInterventionError, match="is not the value the source"):
        validate_preprocessing_intervention(forged, source=_source(), spec=spec, slot=slot)


def test_a_changed_membership_breaks_validation(
    spec: EncoderMappingMismatchSpec, slot: CandidateSlot, result: EncoderMappingMismatchResult
) -> None:
    renamed = ("99999-SYNTH", *result.record_ids[1:])
    forged = _forge(result, record_ids=renamed)
    with pytest.raises(PreprocessingInterventionError, match="order included"):
        validate_preprocessing_intervention(forged, source=_source(), spec=spec, slot=slot)


def test_a_changed_row_order_breaks_validation(
    spec: EncoderMappingMismatchSpec, slot: CandidateSlot, result: EncoderMappingMismatchResult
) -> None:
    forged = _forge(
        result,
        record_ids=tuple(reversed(result.record_ids)),
        raw_categories=tuple(reversed(result.raw_categories)),
        inference_view_categories=tuple(reversed(result.inference_view_categories)),
        reference_block=tuple(reversed(result.reference_block)),
        mismatched_block=tuple(reversed(result.mismatched_block)),
    )
    with pytest.raises(PreprocessingInterventionError, match="order included"):
        validate_preprocessing_intervention(forged, source=_source(), spec=spec, slot=slot)


def test_a_rewritten_raw_column_breaks_validation(
    spec: EncoderMappingMismatchSpec, slot: CandidateSlot, result: EncoderMappingMismatchResult
) -> None:
    """Turning the raw value into the mapped value would hide the intervention."""

    forged = _forge(result, raw_categories=result.inference_view_categories)
    with pytest.raises(PreprocessingInterventionError, match="must survive the intervention"):
        validate_preprocessing_intervention(forged, source=_source(), spec=spec, slot=slot)


def test_a_row_outside_the_source_category_cannot_be_changed(
    spec: EncoderMappingMismatchSpec, slot: CandidateSlot, result: EncoderMappingMismatchResult
) -> None:
    untouched = next(
        index
        for index, record_id in enumerate(result.record_ids)
        if record_id not in set(result.affected_record_ids)
    )
    view = list(result.inference_view_categories)
    view[untouched] = "Two year" if view[untouched] != "Two year" else "One year"
    forged = _forge(result, inference_view_categories=tuple(view))
    with pytest.raises(PreprocessingInterventionError, match="does not match the declared rank"):
        validate_preprocessing_intervention(forged, source=_source(), spec=spec, slot=slot)


def test_a_changed_encoder_column_order_breaks_validation(
    spec: EncoderMappingMismatchSpec, slot: CandidateSlot, result: EncoderMappingMismatchResult
) -> None:
    forged = _forge(result, encoder_column_order=tuple(reversed(result.encoder_column_order)))
    with pytest.raises(ValidationError, match="canonical sorted order"):
        validate_preprocessing_intervention(forged, source=_source(), spec=spec, slot=slot)


def test_a_changed_output_width_breaks_validation(
    spec: EncoderMappingMismatchSpec, slot: CandidateSlot, result: EncoderMappingMismatchResult
) -> None:
    narrowed = tuple(row[:-1] for row in result.mismatched_block)
    forged = _forge(result, mismatched_block=narrowed)
    with pytest.raises(ValidationError, match="one column per category"):
        validate_preprocessing_intervention(forged, source=_source(), spec=spec, slot=slot)


def test_a_changed_row_count_breaks_validation(
    spec: EncoderMappingMismatchSpec, slot: CandidateSlot, result: EncoderMappingMismatchResult
) -> None:
    forged = _forge(result, mismatched_block=result.mismatched_block[:-1])
    with pytest.raises(ValidationError, match="must align"):
        validate_preprocessing_intervention(forged, source=_source(), spec=spec, slot=slot)


def test_an_intervention_without_an_effective_change_is_rejected(
    result: EncoderMappingMismatchResult,
) -> None:
    forged = _forge_provenance(
        result, mismatched_block_sha256=result.provenance.reference_block_sha256
    )
    with pytest.raises(ValidationError, match="must change the encoded block"):
        type(forged.provenance).model_validate(forged.provenance.model_dump())


# --------------------------------------------------------------------------- #
# Forgery and tamper
# --------------------------------------------------------------------------- #


def test_a_forged_inference_view_built_with_model_copy_is_rejected(
    spec: EncoderMappingMismatchSpec, slot: CandidateSlot, result: EncoderMappingMismatchResult
) -> None:
    forged = _forge(result, inference_view_categories=result.raw_categories)
    with pytest.raises(PreprocessingInterventionError, match="does not match the declared rank"):
        validate_preprocessing_intervention(forged, source=_source(), spec=spec, slot=slot)


def test_a_forged_result_built_with_model_construct_is_rejected(
    spec: EncoderMappingMismatchSpec, slot: CandidateSlot, result: EncoderMappingMismatchResult
) -> None:
    forged = EncoderMappingMismatchResult.model_construct(
        schema_version=result.schema_version,
        record_ids=result.record_ids,
        raw_categories=result.raw_categories,
        inference_view_categories=result.inference_view_categories,
        encoder_column_order=result.encoder_column_order,
        reference_block=result.reference_block,
        mismatched_block=result.mismatched_block,
        affected_record_ids=result.affected_record_ids[:-1],
        provenance=result.provenance,
    )
    with pytest.raises(ValidationError, match="affected identifier count"):
        validate_preprocessing_intervention(forged, source=_source(), spec=spec, slot=slot)


def test_forged_affected_identifiers_are_rejected(
    spec: EncoderMappingMismatchSpec, slot: CandidateSlot, result: EncoderMappingMismatchResult
) -> None:
    untouched = next(
        record_id
        for record_id in result.record_ids
        if record_id not in set(result.affected_record_ids)
    )
    swapped = tuple(sorted({*result.affected_record_ids[1:], untouched}))
    forged = _forge(result, affected_record_ids=swapped)
    with pytest.raises(PreprocessingInterventionError, match="do not match the rows carrying"):
        validate_preprocessing_intervention(forged, source=_source(), spec=spec, slot=slot)


def test_a_forged_mismatched_block_is_rejected(
    spec: EncoderMappingMismatchSpec, slot: CandidateSlot, result: EncoderMappingMismatchResult
) -> None:
    """Recomputation catches this before the effectiveness check ever runs."""

    forged = _forge(result, mismatched_block=result.reference_block)
    with pytest.raises(PreprocessingInterventionError, match="mismatched block does not match"):
        validate_preprocessing_intervention(forged, source=_source(), spec=spec, slot=slot)


def test_a_forged_reference_block_is_rejected(
    spec: EncoderMappingMismatchSpec, slot: CandidateSlot, result: EncoderMappingMismatchResult
) -> None:
    swapped = (result.reference_block[1], result.reference_block[0], *result.reference_block[2:])
    forged = _forge(result, reference_block=swapped)
    with pytest.raises(PreprocessingInterventionError, match="reference block does not match"):
        validate_preprocessing_intervention(forged, source=_source(), spec=spec, slot=slot)


@pytest.mark.parametrize("field", ["source_rank", "mapped_rank"])
def test_forged_provenance_ranks_are_rejected(
    field: str,
    spec: EncoderMappingMismatchSpec,
    slot: CandidateSlot,
    result: EncoderMappingMismatchResult,
) -> None:
    current = getattr(result.provenance, field)
    replacement = 1 if current != 1 else 2
    forged = _forge_provenance(result, **{field: replacement})
    with pytest.raises(PreprocessingInterventionError, match="ranks differ"):
        validate_preprocessing_intervention(forged, source=_source(), spec=spec, slot=slot)


@pytest.mark.parametrize(
    "field",
    [
        "vocabulary_sha256",
        "encoder_column_order_sha256",
        "source_record_ids_sha256",
        "source_membership_sha256",
        "raw_categories_sha256",
        "inference_view_categories_sha256",
        "reference_block_sha256",
        "affected_record_ids_sha256",
        "fault_slot_sha256",
        "declared_mapping_sha256",
    ],
)
def test_a_tampered_provenance_digest_is_rejected(
    field: str,
    spec: EncoderMappingMismatchSpec,
    slot: CandidateSlot,
    result: EncoderMappingMismatchResult,
) -> None:
    forged = _forge_provenance(result, **{field: _HEX_0})
    with pytest.raises((PreprocessingInterventionError, ValidationError)):
        validate_preprocessing_intervention(forged, source=_source(), spec=spec, slot=slot)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("injection_id", "M3-F2"),
        ("source_category", "Month-to-month"),
        ("mapped_category", "Month-to-month"),
    ],
)
def test_tampered_explicit_mapping_provenance_is_rejected(
    field: str,
    value: str,
    spec: EncoderMappingMismatchSpec,
    slot: CandidateSlot,
    result: EncoderMappingMismatchResult,
) -> None:
    forged = _forge_provenance(result, **{field: value})
    with pytest.raises((PreprocessingInterventionError, ValidationError)):
        validate_preprocessing_intervention(forged, source=_source(), spec=spec, slot=slot)


def test_a_result_cannot_be_replayed_against_a_different_source(
    spec: EncoderMappingMismatchSpec, slot: CandidateSlot, result: EncoderMappingMismatchResult
) -> None:
    other = _source(attested_model_sha256=_HEX_F)
    with pytest.raises(PreprocessingInterventionError):
        validate_preprocessing_intervention(result, source=other, spec=spec, slot=slot)


def test_a_result_cannot_be_replayed_against_a_different_specification(
    result: EncoderMappingMismatchResult,
) -> None:
    with pytest.raises(PreprocessingInterventionError):
        validate_preprocessing_intervention(
            result, source=_source(), spec=_spec("M3-F2"), slot=_slot("M3-F2")
        )


def test_a_result_cannot_be_replayed_against_a_different_slot(
    spec: EncoderMappingMismatchSpec, result: EncoderMappingMismatchResult
) -> None:
    other_slot = _slot("M3-R1")
    with pytest.raises(PreprocessingInterventionError, match="injection_id differs"):
        validate_preprocessing_intervention(result, source=_source(), spec=spec, slot=other_slot)


def test_the_artifact_digest_is_not_a_field_a_caller_can_supply(
    result: EncoderMappingMismatchResult,
) -> None:
    """The digest is computed on demand, so there is nothing to forge."""

    assert "artifact_sha256" not in type(result).model_fields
    assert result.artifact_sha256() == result.artifact_sha256()


@pytest.mark.parametrize(
    "field",
    [
        "seed",
        "source_rank",
        "vocabulary_sha256",
        "fault_slot_sha256",
        "declared_mapping_sha256",
        "attested_model_sha256",
        "attested_fitted_training_transform_sha256",
    ],
)
def test_every_bound_provenance_field_changes_the_artifact_digest(
    field: str, result: EncoderMappingMismatchResult
) -> None:
    baseline = result.artifact_sha256()
    value: object = _HEX_0 if field.endswith("sha256") else 2
    assert _forge_provenance(result, **{field: value}).artifact_sha256() != baseline


def test_the_artifact_digest_is_sensitive_to_row_and_column_order(
    result: EncoderMappingMismatchResult,
) -> None:
    rows = _forge(result, record_ids=tuple(reversed(result.record_ids)))
    columns = _forge(result, encoder_column_order=tuple(reversed(result.encoder_column_order)))
    assert rows.artifact_sha256() != result.artifact_sha256()
    assert columns.artifact_sha256() != result.artifact_sha256()


def test_no_semantic_digest_is_published() -> None:
    """No consumer needs one, and an unused digest is a claim nobody checks."""

    assert not hasattr(EncoderMappingMismatchResult, "semantic_sha256")


# --------------------------------------------------------------------------- #
# Scope boundary
# --------------------------------------------------------------------------- #


def test_the_improvement_control_slot_is_refused(spec: EncoderMappingMismatchSpec) -> None:
    control = _slot(
        "M3-I1",
        parameters=_parameters(source_rank=1, mapped_rank=3),
        seed=304,
        role="designed_improvement_control",
        intervention_type="inference_encoder_mapping_repair",
    )
    with pytest.raises(PreprocessingInterventionError, match="fault-directed slots only"):
        apply_encoder_mapping_mismatch(source=_source(), spec=spec, slot=control)


def test_the_benign_control_slot_is_refused(spec: EncoderMappingMismatchSpec) -> None:
    control = _slot(
        "M3-B1",
        parameters=_parameters(source_rank=None, mapped_rank=None),
        seed=305,
        role="designed_benign_control",
        intervention_type="name_bound_column_order_permutation",
    )
    with pytest.raises(PreprocessingInterventionError, match="fault-directed slots only"):
        apply_encoder_mapping_mismatch(source=_source(), spec=spec, slot=control)


def test_a_repair_intervention_type_is_refused_even_with_a_fault_role(
    spec: EncoderMappingMismatchSpec,
) -> None:
    """Role and intervention type are checked independently, not as one rule."""

    disguised = _slot(
        "M3-I1",
        parameters=spec.parameters,
        seed=spec.seed,
        intervention_type="inference_encoder_mapping_repair",
    )
    with pytest.raises(PreprocessingInterventionError, match="role/seed contract"):
        apply_encoder_mapping_mismatch(source=_source(), spec=spec, slot=disguised)


def test_a_slot_from_another_mechanism_is_refused(spec: EncoderMappingMismatchSpec) -> None:
    with pytest.raises(ValidationError):
        _slot("M2-F1", parameters=spec.parameters, seed=spec.seed, fault_type="label_noise")


def test_the_caller_cannot_attach_an_outcome_to_any_model(
    result: EncoderMappingMismatchResult,
) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        EncoderMappingMismatchResult(
            **result.model_dump(),
            measured_outcome="regression",  # type: ignore[call-arg]
        )


def test_the_module_exposes_no_control_or_reserve_activation() -> None:
    from aletheia_lab.benchmark.p2 import preprocessing_intervention as module

    names = dir(module)
    assert not any("reserve" in name.lower() for name in names)
    assert not any("activat" in name.lower() for name in names)
    assert not any(name.lower().startswith("evaluate") for name in names)


# --------------------------------------------------------------------------- #
# Immutability, proven by attempted mutation
# --------------------------------------------------------------------------- #


def test_the_result_cannot_be_mutated(result: EncoderMappingMismatchResult) -> None:
    with pytest.raises(ValidationError):
        result.record_ids = ()


def test_the_nested_provenance_cannot_be_mutated(result: EncoderMappingMismatchResult) -> None:
    with pytest.raises(ValidationError):
        result.provenance.seed = 999


def test_the_nested_vocabulary_cannot_be_mutated(source: InferenceTransformSource) -> None:
    with pytest.raises(ValidationError):
        source.vocabulary.feature = "PaymentMethod"


def test_a_nested_category_frequency_cannot_be_mutated(source: InferenceTransformSource) -> None:
    with pytest.raises(ValidationError):
        source.vocabulary.frequencies[0].count = 1


def test_the_indicator_blocks_cannot_be_mutated(result: EncoderMappingMismatchResult) -> None:
    with pytest.raises(TypeError):
        result.reference_block[0][0] = 1  # type: ignore[index]
    with pytest.raises(TypeError):
        result.mismatched_block[0] = (0, 0, 1)  # type: ignore[index]


def test_the_specification_cannot_be_mutated(spec: EncoderMappingMismatchSpec) -> None:
    with pytest.raises(ValidationError):
        spec.seed = 999
    with pytest.raises(ValidationError):
        spec.parameters.source_rank = 1
