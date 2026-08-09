"""Regression tests for deterministic training-label corruption.

Two error kinds are expected, and the tests keep them apart deliberately:

* ``ValidationError`` when a single object is malformed, because model
  validators report through Pydantic;
* ``LabelNoiseError`` when objects disagree with one another, because those
  checks run outside any model.

Every invariant is covered twice: once by a positive case showing the intended
behaviour, and once by a forged artifact showing that the validator rejects a
plausible-looking fake.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2 import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import (
    CandidateSlot,
    ExecutedCandidate,
    TechnicalDispositionEntry,
)
from aletheia_lab.benchmark.p2.identity import (
    FamilyIdentity,
    LabelNoiseParameters,
    candidate_id_for,
    proposed_family_sha256,
)
from aletheia_lab.benchmark.p2.label_noise import (
    AUDIT_INTERVAL_METHOD,
    LABEL_MUTATION_SCHEMA_VERSION,
    LABEL_SOURCE_SCHEMA_VERSION,
    TARGET_QUALITY_AUDIT_SCHEMA_VERSION,
    LabelCorruptionResult,
    LabelCorruptionSpec,
    LabelNoiseError,
    LabelNoiseSource,
    MutationEntry,
    MutationMap,
    TargetQualityAudit,
    apply_label_corruption,
    diagnosis_projection,
    mutation_count,
    select_record_ids,
    selection_digest,
    validate_label_corruption,
    wilson_interval,
)
from aletheia_lab.benchmark.p2.mechanism_validation import (
    LabelFaultDirectedInputs,
    MechanismValidationError,
    validate_mechanism_candidate,
)

_HEX_F = "f" * 64
_HEX_D = "d" * 64
_HEX_C = "c" * 64


def _ids(count: int) -> tuple[str, ...]:
    return tuple(f"{index:05d}-SYNTH" for index in range(count))


def _alternating(count: int) -> tuple[int, ...]:
    return tuple(index % 2 for index in range(count))


def _source(count: int = 1000, **overrides: object) -> LabelNoiseSource:
    payload: dict[str, object] = {
        "schema_version": LABEL_SOURCE_SCHEMA_VERSION,
        "split": "train",
        "record_ids": _ids(count),
        "targets": _alternating(count),
        "attested_feature_matrix_sha256": _HEX_F,
        "attested_preprocessing_specification_sha256": _HEX_D,
        "attested_model_specification_sha256": _HEX_C,
    }
    payload.update(overrides)
    return LabelNoiseSource(**payload)  # type: ignore[arg-type]


def _spec(flip_rate: float = 0.05, seed: int = 202) -> LabelCorruptionSpec:
    return LabelCorruptionSpec(
        parameters=LabelNoiseParameters(
            flip_rate=flip_rate,
            flip_direction="symmetric",
            selection_policy="seeded_record_hash",
            scope="train",
        ),
        seed=seed,
    )


def _fault_slot() -> CandidateSlot:
    return CandidateSlot(
        slot_id="M2-F2",
        fault_type="label_noise",
        slot_kind="primary",
        role="fault_directed",
        identity=FamilyIdentity(
            dataset_snapshot_id="telco_customer_churn@2026-07",
            dataset_sha256="a" * 64,
            model_data_split_manifest_sha256="b" * 64,
            fault_type="label_noise",
            intervention_type="training_target_label_corruption",
            canonical_intervention_parameters=LabelNoiseParameters(
                flip_rate=0.05,
                flip_direction="symmetric",
                selection_policy="seeded_record_hash",
                scope="train",
            ),
            seed=202,
            reference_construction_id="clean-test-reference/v1",
            injector_contract_version="label-noise/v1",
            model_specification_sha256=_HEX_C,
            preprocessing_specification_sha256=_HEX_D,
            identity_schema_version="p2-family-identity/v1",
        ),
    )


def _execution(slot: CandidateSlot) -> ExecutedCandidate:
    fingerprint = proposed_family_sha256(slot.identity)
    return ExecutedCandidate(
        candidate_id=candidate_id_for(
            slot_id=slot.slot_id,
            family_fingerprint=fingerprint,
        ),
        slot_id=slot.slot_id,
        fault_type=slot.fault_type,
        role=slot.role,
        slot_kind=slot.slot_kind,
        proposed_family_sha256=fingerprint,
        dataset_sha256=slot.identity.dataset_sha256,
        model_data_split_manifest_sha256=slot.identity.model_data_split_manifest_sha256,
    )


def _forge(result: LabelCorruptionResult, **updates: object) -> LabelCorruptionResult:
    """Bypass validation the way a careless caller would."""

    return result.model_copy(update=updates)


@pytest.fixture
def source() -> LabelNoiseSource:
    return _source()


@pytest.fixture
def spec() -> LabelCorruptionSpec:
    return _spec()


# --------------------------------------------------------------------------- #
# Mutation count
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("flip_rate", "expected"),
    [(0.01, 10), (0.05, 50), (0.20, 200), (0.025, 25), (0.10, 100), (0.30, 300)],
)
def test_mutation_count_matches_the_frozen_alpha_rates(flip_rate: float, expected: int) -> None:
    assert mutation_count(flip_rate=flip_rate, record_count=1000) == expected


def test_mutation_count_rounds_half_up_not_to_even() -> None:
    """``round()`` would give 0 here; the declared rate asks for 1."""

    assert mutation_count(flip_rate=0.01, record_count=50) == 1
    assert round(0.01 * 50) == 0


def test_mutation_count_avoids_binary_float_truncation() -> None:
    """``int(0.29 * 100)`` is 28 because 0.29 has no exact binary form."""

    assert mutation_count(flip_rate=0.29, record_count=100) == 29
    assert int(0.29 * 100) == 28


def test_mutation_count_is_derived_independently_from_the_published_rule() -> None:
    """Build the expectation from the contract, not from the implementation."""

    for flip_rate, record_count in ((0.01, 733), (0.05, 137), (0.20, 41), (0.30, 17)):
        expected = int(
            (Decimal(str(flip_rate)) * Decimal(record_count)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        assert mutation_count(flip_rate=flip_rate, record_count=record_count) == expected


def test_mutation_count_rejects_invalid_inputs() -> None:
    with pytest.raises(LabelNoiseError, match="at least one record"):
        mutation_count(flip_rate=0.05, record_count=0)
    with pytest.raises(LabelNoiseError, match=r"\[0, 0.5\]"):
        mutation_count(flip_rate=0.9, record_count=100)
    with pytest.raises(LabelNoiseError, match=r"\[0, 0.5\]"):
        mutation_count(flip_rate=-0.01, record_count=100)


# --------------------------------------------------------------------------- #
# Confidence interval
# --------------------------------------------------------------------------- #


def test_wilson_interval_brackets_the_observed_proportion() -> None:
    lower, upper = wilson_interval(successes=50, trials=1000)
    assert lower < 0.05 < upper


def test_wilson_interval_stays_inside_the_unit_range() -> None:
    lower_zero, upper_zero = wilson_interval(successes=0, trials=100)
    assert 0.0 <= lower_zero < 1e-12
    assert 0.0 < upper_zero < 1.0

    lower_all, upper_all = wilson_interval(successes=100, trials=100)
    assert 0.0 < lower_all < 1.0
    assert upper_all == pytest.approx(1.0)


def test_wilson_interval_is_computed_independently_from_the_formula() -> None:
    """Rebuild the published Wilson formula rather than trusting the function."""

    z = 1.959963984540054
    successes, trials = 37, 811
    proportion = successes / trials
    denominator = 1.0 + z * z / trials
    centre = (proportion + z * z / (2.0 * trials)) / denominator
    spread = (
        z
        / denominator
        * math.sqrt(proportion * (1.0 - proportion) / trials + z * z / (4.0 * trials * trials))
    )
    lower, upper = wilson_interval(successes=successes, trials=trials)
    assert lower == pytest.approx(centre - spread)
    assert upper == pytest.approx(centre + spread)


def test_wilson_interval_narrows_as_the_sample_grows() -> None:
    small = wilson_interval(successes=5, trials=100)
    large = wilson_interval(successes=50, trials=1000)
    assert (large[1] - large[0]) < (small[1] - small[0])


def test_wilson_interval_rejects_invalid_inputs() -> None:
    with pytest.raises(LabelNoiseError, match="at least one trial"):
        wilson_interval(successes=0, trials=0)
    with pytest.raises(LabelNoiseError, match="successes must lie"):
        wilson_interval(successes=11, trials=10)


# --------------------------------------------------------------------------- #
# Deterministic selection
# --------------------------------------------------------------------------- #


def test_selection_is_identical_for_the_same_seed(source: LabelNoiseSource) -> None:
    assert select_record_ids(source=source, seed=201, count=25) == select_record_ids(
        source=source, seed=201, count=25
    )


def test_selection_ignores_row_order(source: LabelNoiseSource) -> None:
    reversed_source = _source(
        record_ids=tuple(reversed(source.record_ids)),
        targets=tuple(reversed(source.targets)),
    )
    assert set(select_record_ids(source=source, seed=201, count=25)) == set(
        select_record_ids(source=reversed_source, seed=201, count=25)
    )


def test_selection_changes_with_the_seed(source: LabelNoiseSource) -> None:
    assert select_record_ids(source=source, seed=201, count=25) != select_record_ids(
        source=source, seed=202, count=25
    )


def test_selection_digest_depends_only_on_seed_and_identifier() -> None:
    baseline = selection_digest(seed=201, record_id="00001-SYNTH")
    assert selection_digest(seed=201, record_id="00001-SYNTH") == baseline
    assert selection_digest(seed=202, record_id="00001-SYNTH") != baseline
    assert selection_digest(seed=201, record_id="00002-SYNTH") != baseline


def test_selection_rejects_impossible_counts(source: LabelNoiseSource) -> None:
    with pytest.raises(LabelNoiseError, match="cannot select"):
        select_record_ids(source=source, seed=201, count=source.record_count + 1)
    with pytest.raises(LabelNoiseError, match="must not be negative"):
        select_record_ids(source=source, seed=201, count=-1)


# --------------------------------------------------------------------------- #
# Corruption
# --------------------------------------------------------------------------- #


def test_corruption_changes_exactly_the_selected_labels(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    result = apply_label_corruption(source=source, spec=spec)
    changed = {
        record_id
        for record_id, before, after in zip(
            source.record_ids, source.targets, result.mutated_targets, strict=True
        )
        if before != after
    }
    assert changed == result.mutation_map.record_ids()
    assert len(changed) == 50


def test_every_change_is_a_binary_flip(source: LabelNoiseSource, spec: LabelCorruptionSpec) -> None:
    result = apply_label_corruption(source=source, spec=spec)
    for entry in result.mutation_map.entries:
        assert entry.mutated_label == 1 - entry.original_label


def test_corruption_preserves_identifiers_and_their_order(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    result = apply_label_corruption(source=source, spec=spec)
    assert result.record_ids == source.record_ids
    assert len(result.mutated_targets) == len(source.targets)


def test_corruption_does_not_mutate_its_source(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    apply_label_corruption(source=source, spec=spec)
    assert source.targets == _alternating(1000)


def test_corruption_is_byte_identical_for_the_same_inputs(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    first = apply_label_corruption(source=source, spec=spec)
    second = apply_label_corruption(source=source, spec=spec)
    assert first.artifact_sha256() == second.artifact_sha256()
    assert first.semantic_sha256() == second.semantic_sha256()
    assert canonical_sha256(first.model_dump(mode="json")) == canonical_sha256(
        second.model_dump(mode="json")
    )


def test_semantic_identity_is_invariant_but_artifact_binds_source_row_order(
    spec: LabelCorruptionSpec,
) -> None:
    """Equivalent interventions share identity without erasing artifact order."""

    ordered = _source()
    shuffled = _source(
        record_ids=tuple(reversed(ordered.record_ids)),
        targets=tuple(reversed(ordered.targets)),
    )
    first = apply_label_corruption(source=ordered, spec=spec)
    second = apply_label_corruption(source=shuffled, spec=spec)

    assert first.semantic_sha256() == second.semantic_sha256()
    assert first.artifact_sha256() != second.artifact_sha256()
    assert first.provenance.mutation_map_sha256 == second.provenance.mutation_map_sha256
    assert first.provenance.source_membership_sha256 == second.provenance.source_membership_sha256
    # The listing order itself is still visible, and still differs.
    assert first.record_ids != second.record_ids
    assert first.provenance.source_record_ids_sha256 != (second.provenance.source_record_ids_sha256)


def test_semantic_digest_binds_seed_and_attested_inputs() -> None:
    source = _source()
    baseline = apply_label_corruption(source=source, spec=_spec(seed=202))
    other_seed = apply_label_corruption(source=source, spec=_spec(seed=203))
    other_features = apply_label_corruption(
        source=_source(attested_feature_matrix_sha256="a" * 64),
        spec=_spec(seed=202),
    )

    assert baseline.semantic_sha256() != other_seed.semantic_sha256()
    assert baseline.semantic_sha256() != other_features.semantic_sha256()


def test_semantic_identity_does_not_replace_artifact_integrity_validation(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    """The semantic digest may ignore order metadata; validation never does."""

    result = apply_label_corruption(source=source, spec=spec)
    forged = _forge(
        result,
        provenance=result.provenance.model_copy(update={"source_record_ids_sha256": "a" * 64}),
    )

    assert forged.semantic_sha256() == result.semantic_sha256()
    assert forged.artifact_sha256() != result.artifact_sha256()
    with pytest.raises(LabelNoiseError, match="record-ID digest"):
        validate_label_corruption(forged, source=source, spec=spec)


def test_achieved_rate_is_derived_from_the_counts(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    provenance = apply_label_corruption(source=source, spec=spec).provenance
    assert provenance.achieved_flip_rate == provenance.mutation_count / provenance.record_count


def test_provenance_records_recomputable_and_attested_digests(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    provenance = apply_label_corruption(source=source, spec=spec).provenance
    assert provenance.source_record_ids_sha256 == source.record_ids_sha256()
    assert provenance.source_membership_sha256 == source.membership_sha256()
    assert provenance.source_targets_sha256 == source.targets_sha256()
    assert provenance.attested_feature_matrix_sha256 == source.attested_feature_matrix_sha256
    assert provenance.mutated_targets_sha256 != provenance.source_targets_sha256


def test_membership_digest_is_recomputed_not_supplied(source: LabelNoiseSource) -> None:
    """Membership follows from the identifiers, so nobody may assert it."""

    assert source.membership_sha256() == source.membership_sha256()
    assert "membership_sha256" not in LabelNoiseSource.model_fields
    fewer = _source(count=999, record_ids=_ids(999), targets=_alternating(999))
    assert fewer.membership_sha256() != source.membership_sha256()


def test_corruption_refuses_a_rate_that_mutates_nothing() -> None:
    with pytest.raises(LabelNoiseError, match="no effective intervention"):
        apply_label_corruption(source=_source(count=10), spec=_spec(flip_rate=0.01))


def test_corruption_refuses_to_erase_a_class() -> None:
    """With one record per class and a half rate, any selection erases a class."""

    two_records = _source(count=2, record_ids=("aaa", "bbb"), targets=(1, 0))
    assert mutation_count(flip_rate=0.5, record_count=2) == 1
    with pytest.raises(LabelNoiseError, match="erased a class"):
        apply_label_corruption(source=two_records, spec=_spec(flip_rate=0.5, seed=7))


def test_corruption_requires_the_source_split_to_match_the_scope() -> None:
    with pytest.raises(ValidationError):
        _source(split="test")


# --------------------------------------------------------------------------- #
# Specification guards — malformed object, so ValidationError
# --------------------------------------------------------------------------- #


def test_spec_rejects_a_zero_rate_corruption() -> None:
    """Zero belongs to the serialization control, not to corruption."""

    with pytest.raises(ValidationError, match="positive flip rate"):
        _spec(flip_rate=0.0)


@pytest.mark.parametrize("flip_rate", [-0.01, 0.51, 1.0])
def test_parameters_reject_rates_outside_the_contract(flip_rate: float) -> None:
    with pytest.raises(ValidationError):
        LabelNoiseParameters(
            flip_rate=flip_rate,
            flip_direction="symmetric",
            selection_policy="seeded_record_hash",
            scope="train",
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_parameters_reject_non_finite_rates(value: float) -> None:
    with pytest.raises(ValidationError):
        LabelNoiseParameters(
            flip_rate=value,
            flip_direction="symmetric",
            selection_policy="seeded_record_hash",
            scope="train",
        )


def test_spec_rejects_directions_outside_the_alpha_slice() -> None:
    with pytest.raises(ValidationError, match="symmetric"):
        LabelCorruptionSpec(
            parameters=LabelNoiseParameters(
                flip_rate=0.05,
                flip_direction="yes_to_no",
                selection_policy="seeded_record_hash",
                scope="train",
            ),
            seed=1,
        )


# --------------------------------------------------------------------------- #
# Source guards — malformed object, so ValidationError
# --------------------------------------------------------------------------- #


def test_source_rejects_duplicate_identifiers() -> None:
    with pytest.raises(ValidationError, match="unique"):
        _source(count=3, record_ids=("a", "b", "b"), targets=(0, 1, 0))


def test_source_rejects_blank_or_untrimmed_identifiers() -> None:
    with pytest.raises(ValidationError, match="non-blank and trimmed"):
        _source(count=2, record_ids=("a", " b"), targets=(0, 1))


def test_source_rejects_misaligned_identifiers_and_targets() -> None:
    with pytest.raises(ValidationError, match="must align"):
        _source(count=3, record_ids=("a", "b", "c"), targets=(0, 1))


@pytest.mark.parametrize("bad_target", [2, -1])
def test_source_rejects_non_binary_targets(bad_target: int) -> None:
    with pytest.raises(ValidationError, match="binary"):
        _source(count=2, record_ids=("a", "b"), targets=(0, bad_target))


def test_source_rejects_an_empty_split() -> None:
    with pytest.raises(ValidationError, match="at least one training record"):
        _source(count=0, record_ids=(), targets=())


def test_source_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        _source(unexpected="x")


# --------------------------------------------------------------------------- #
# Authoritative validation
# --------------------------------------------------------------------------- #


def test_validator_accepts_and_returns_an_honest_result(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    result = apply_label_corruption(source=source, spec=spec)
    validated = validate_label_corruption(result, source=source, spec=spec)
    assert validated.artifact_sha256() == result.artifact_sha256()
    assert validated.semantic_sha256() == result.semantic_sha256()


def test_unified_validator_binds_label_corruption_to_the_shared_lifecycle(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    slot = _fault_slot()
    execution = _execution(slot)
    result = apply_label_corruption(source=source, spec=spec)
    disposition = TechnicalDispositionEntry(
        candidate_id=execution.candidate_id,
        disposition="technically_valid",
    )
    binding = validate_mechanism_candidate(
        result,
        slot=slot,
        inputs=LabelFaultDirectedInputs(source=source, spec=spec),
        execution=execution,
        disposition=disposition,
    )
    assert binding.candidate_id == execution.candidate_id
    assert binding.fault_type == "label_noise"
    assert binding.artifact_sha256 == result.artifact_sha256()


def test_unified_validator_rejects_label_artifact_reuse_under_another_family(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    slot = _fault_slot()
    execution = _execution(slot)
    result = apply_label_corruption(source=source, spec=spec)
    forged_slot = slot.model_copy(
        update={
            "identity": slot.identity.model_copy(update={"dataset_sha256": "9" * 64})
        }
    )
    with pytest.raises(MechanismValidationError, match="execution does not match"):
        validate_mechanism_candidate(
            result,
            slot=forged_slot,
            inputs=LabelFaultDirectedInputs(source=source, spec=spec),
            execution=execution,
            disposition=TechnicalDispositionEntry(
                candidate_id=execution.candidate_id,
                disposition="technically_valid",
            ),
        )


def test_validator_rejects_a_tampered_mutation_map_with_a_matching_digest(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    """The classic forgery: change the map and update its self-declared hash."""

    result = apply_label_corruption(source=source, spec=spec)
    untouched = next(
        record_id
        for record_id in source.record_ids
        if record_id not in result.mutation_map.record_ids()
    )
    original_by_id = dict(zip(source.record_ids, source.targets, strict=True))
    forged_map = MutationMap(
        schema_version=LABEL_MUTATION_SCHEMA_VERSION,
        entries=(
            *result.mutation_map.entries[1:],
            MutationEntry(
                record_id=untouched,
                original_label=original_by_id[untouched],
                mutated_label=1 - original_by_id[untouched],
            ),
        ),
    )
    forged = _forge(
        result,
        mutation_map=forged_map,
        provenance=result.provenance.model_copy(
            update={"mutation_map_sha256": forged_map.canonical_sha256()}
        ),
    )
    with pytest.raises(LabelNoiseError, match="deterministic selection"):
        validate_label_corruption(forged, source=source, spec=spec)


def test_validator_rejects_an_inflated_mutation_count(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    result = apply_label_corruption(source=source, spec=spec)
    forged = _forge(result, provenance=result.provenance.model_copy(update={"mutation_count": 999}))
    with pytest.raises(ValidationError, match="mutation_count"):
        validate_label_corruption(forged, source=source, spec=spec)


def test_validator_rejects_a_forged_achieved_rate(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    result = apply_label_corruption(source=source, spec=spec)
    forged = _forge(
        result, provenance=result.provenance.model_copy(update={"achieved_flip_rate": 0.5})
    )
    with pytest.raises(ValidationError, match="achieved_flip_rate"):
        validate_label_corruption(forged, source=source, spec=spec)


def test_validator_rejects_a_stale_source_digest(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    result = apply_label_corruption(source=source, spec=spec)
    forged = _forge(
        result,
        provenance=result.provenance.model_copy(update={"source_targets_sha256": "0" * 64}),
    )
    with pytest.raises(LabelNoiseError, match="target digest"):
        validate_label_corruption(forged, source=source, spec=spec)


def test_validator_rejects_a_forged_membership_digest(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    """Membership is recomputed, so a fabricated digest cannot survive."""

    result = apply_label_corruption(source=source, spec=spec)
    forged = _forge(
        result,
        provenance=result.provenance.model_copy(update={"source_membership_sha256": "1" * 64}),
    )
    with pytest.raises(LabelNoiseError, match="membership digest"):
        validate_label_corruption(forged, source=source, spec=spec)


def test_validator_rejects_a_changed_attested_feature_digest(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    result = apply_label_corruption(source=source, spec=spec)
    forged = _forge(
        result,
        provenance=result.provenance.model_copy(
            update={"attested_feature_matrix_sha256": "1" * 64}
        ),
    )
    with pytest.raises(LabelNoiseError, match="attested_feature_matrix_sha256"):
        validate_label_corruption(forged, source=source, spec=spec)


def test_validator_rejects_reordered_records(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    result = apply_label_corruption(source=source, spec=spec)
    forged = _forge(
        result,
        record_ids=tuple(reversed(result.record_ids)),
        mutated_targets=tuple(reversed(result.mutated_targets)),
    )
    with pytest.raises(LabelNoiseError, match="reorder"):
        validate_label_corruption(forged, source=source, spec=spec)


def test_validator_rejects_extra_changed_labels(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    result = apply_label_corruption(source=source, spec=spec)
    selected = result.mutation_map.record_ids()
    position = next(
        index for index, record_id in enumerate(result.record_ids) if record_id not in selected
    )
    smuggled = list(result.mutated_targets)
    smuggled[position] = 1 - smuggled[position]
    forged = _forge(result, mutated_targets=tuple(smuggled))
    with pytest.raises(LabelNoiseError, match="changed labels"):
        validate_label_corruption(forged, source=source, spec=spec)


def test_validator_rejects_a_spec_that_did_not_produce_the_result(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    result = apply_label_corruption(source=source, spec=spec)
    with pytest.raises(LabelNoiseError, match="seed"):
        validate_label_corruption(result, source=source, spec=_spec(seed=spec.seed + 1))


def test_validator_rejects_a_different_source(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    result = apply_label_corruption(source=source, spec=spec)
    other = _source(targets=tuple(1 - value for value in source.targets))
    with pytest.raises(LabelNoiseError, match="digest"):
        validate_label_corruption(result, source=other, spec=spec)


def test_validator_rejects_model_construct_bypass(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    """``model_construct`` skips validation entirely; the boundary must not."""

    result = apply_label_corruption(source=source, spec=spec)
    unvalidated = LabelCorruptionResult.model_construct(
        **{**result.__dict__, "mutated_targets": source.targets}
    )
    with pytest.raises((LabelNoiseError, ValidationError)):
        validate_label_corruption(unvalidated, source=source, spec=spec)


def test_mutation_entry_rejects_a_non_change() -> None:
    with pytest.raises(ValidationError, match="actual change"):
        MutationEntry(record_id="a", original_label=1, mutated_label=1)


def test_mutation_map_rejects_a_repeated_record() -> None:
    entry = MutationEntry(record_id="a", original_label=0, mutated_label=1)
    with pytest.raises(ValidationError, match="at most once"):
        MutationMap(schema_version=LABEL_MUTATION_SCHEMA_VERSION, entries=(entry, entry))


def test_mutation_map_digest_ignores_entry_order(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    result = apply_label_corruption(source=source, spec=spec)
    reversed_map = MutationMap(
        schema_version=LABEL_MUTATION_SCHEMA_VERSION,
        entries=tuple(reversed(result.mutation_map.entries)),
    )
    assert reversed_map.canonical_sha256() == result.mutation_map.canonical_sha256()


# --------------------------------------------------------------------------- #
# Diagnosis boundary
# --------------------------------------------------------------------------- #


def test_projection_carries_aggregate_evidence_including_an_interval(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    result = apply_label_corruption(source=source, spec=spec)
    projection = diagnosis_projection(result, source=source, spec=spec)
    audit = projection.target_quality_audit

    assert projection.sample_size == source.record_count
    assert audit.disagreeing_record_count == 50
    assert audit.disagreement_rate == pytest.approx(0.05)
    assert audit.interval_method == AUDIT_INTERVAL_METHOD
    assert audit.disagreement_rate_lower_bound < 0.05 < audit.disagreement_rate_upper_bound
    comparison = projection.target_distribution_comparison
    assert comparison.reference_positive_count + comparison.reference_negative_count == 1000


def test_projection_export_requires_a_validated_parent(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    """A forged projection must not escape through the export helper."""

    result = apply_label_corruption(source=source, spec=spec)
    forged_audit = result.projection.target_quality_audit.model_copy(
        update={
            "disagreeing_record_count": 1,
            "disagreement_rate": 0.001,
            "disagreement_rate_lower_bound": 0.0,
            "disagreement_rate_upper_bound": 0.006,
        }
    )
    forged = _forge(
        result,
        projection=result.projection.model_copy(update={"target_quality_audit": forged_audit}),
    )
    with pytest.raises((LabelNoiseError, ValidationError)):
        diagnosis_projection(forged, source=source, spec=spec)


def test_projection_cannot_carry_record_identifiers_or_labels(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    """The boundary is the type: there is no field for this data."""

    result = apply_label_corruption(source=source, spec=spec)
    payload = str(diagnosis_projection(result, source=source, spec=spec).model_dump(mode="json"))
    for record_id in sorted(result.mutation_map.record_ids())[:5]:
        assert record_id not in payload
    assert "seed" not in payload
    assert "mutation" not in payload


def test_projection_has_no_free_text_field() -> None:
    """Every audit field is a number or a pinned identifier."""

    annotations = TargetQualityAudit.model_fields
    assert set(annotations) == {
        "schema_version",
        "audited_record_count",
        "disagreeing_record_count",
        "disagreement_rate",
        "disagreement_rate_lower_bound",
        "disagreement_rate_upper_bound",
        "interval_method",
        "protocol_version",
    }
    with pytest.raises(ValidationError):
        TargetQualityAudit(
            schema_version=TARGET_QUALITY_AUDIT_SCHEMA_VERSION,
            audited_record_count=10,
            disagreeing_record_count=1,
            disagreement_rate=0.1,
            disagreement_rate_lower_bound=0.0,
            disagreement_rate_upper_bound=1.0,
            interval_method=AUDIT_INTERVAL_METHOD,
            protocol_version="00042-SYNTH",  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "leaky",
    [
        {"flip rate": 0.05},
        {"FLIP-audit": 1},
        {"Flip.Audit": 1},
        {"label noise": "v1"},
        {"note": "label noise/v1"},
        {"note": "mutation  map"},
        {"note": "seed=5"},
        {"note": "record 00042"},
        {"nested": {"inner": "ground-truth"}},
        {"items": ["flip"]},
    ],
)
def test_projection_scan_catches_separator_and_case_variants(leaky: dict[str, object]) -> None:
    """Compatibility folding means punctuation cannot evade the scan."""

    from aletheia_lab.benchmark.p2.label_noise import _assert_projection_is_diagnosis_safe

    with pytest.raises(LabelNoiseError):
        _assert_projection_is_diagnosis_safe(leaky)


def test_projection_scan_accepts_the_pinned_protocol_identifier() -> None:
    from aletheia_lab.benchmark.p2.label_noise import _assert_projection_is_diagnosis_safe

    _assert_projection_is_diagnosis_safe(
        {"protocol_version": "target-quality-audit/v1", "audited_record_count": 1000}
    )


def test_validator_rejects_a_projection_that_misreports_the_audit(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    result = apply_label_corruption(source=source, spec=spec)
    lower, upper = wilson_interval(successes=1, trials=1000)
    forged_audit = result.projection.target_quality_audit.model_copy(
        update={
            "disagreeing_record_count": 1,
            "disagreement_rate": 0.001,
            "disagreement_rate_lower_bound": lower,
            "disagreement_rate_upper_bound": upper,
        }
    )
    forged = _forge(
        result,
        projection=result.projection.model_copy(update={"target_quality_audit": forged_audit}),
    )
    with pytest.raises(LabelNoiseError, match="disagreement count"):
        validate_label_corruption(forged, source=source, spec=spec)


def test_audit_rate_and_interval_must_be_derived_from_their_counts() -> None:
    with pytest.raises(ValidationError, match="derived"):
        TargetQualityAudit(
            schema_version=TARGET_QUALITY_AUDIT_SCHEMA_VERSION,
            audited_record_count=1000,
            disagreeing_record_count=50,
            disagreement_rate=0.9,
            disagreement_rate_lower_bound=0.0,
            disagreement_rate_upper_bound=1.0,
            interval_method=AUDIT_INTERVAL_METHOD,
            protocol_version="target-quality-audit/v1",
        )
    with pytest.raises(ValidationError, match="Wilson"):
        TargetQualityAudit(
            schema_version=TARGET_QUALITY_AUDIT_SCHEMA_VERSION,
            audited_record_count=1000,
            disagreeing_record_count=50,
            disagreement_rate=0.05,
            disagreement_rate_lower_bound=0.0,
            disagreement_rate_upper_bound=1.0,
            interval_method=AUDIT_INTERVAL_METHOD,
            protocol_version="target-quality-audit/v1",
        )


# --------------------------------------------------------------------------- #
# The injector stays inside its layer
# --------------------------------------------------------------------------- #


def test_result_carries_no_outcome_or_eligibility_field(
    source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> None:
    """Deciding whether this harmed the model belongs to a later stage."""

    result = apply_label_corruption(source=source, spec=spec)
    forbidden = {
        "measured_outcome",
        "family_class",
        "eligibility",
        "eligible_failure",
        "disposition",
        "expected_diagnosis_behavior",
        "cause_label",
        "evidence_condition",
    }
    payload = str(result.model_dump(mode="json"))
    for field in forbidden:
        assert field not in payload
    assert forbidden.isdisjoint(LabelCorruptionResult.model_fields)
