"""Regression tests for the two predeclared label controls.

Two error kinds are expected, and the tests keep them apart deliberately:

* ``ValidationError`` when a single object is malformed, because model
  validators report through Pydantic;
* ``LabelControlError`` when objects disagree with one another, and
  ``LabelNoiseError`` when the disagreement is inside the corrupted reference
  the repair control depends on, because those checks run outside any model.

Every invariant is covered twice: once by a positive case showing the intended
behaviour, and once by a forged artifact showing that the validator rejects a
plausible-looking fake.
"""

from __future__ import annotations

from inspect import signature
from typing import Any, get_args

import pytest
from pydantic import BaseModel, ValidationError

from aletheia_lab.baseline.schema import NEGATIVE_LABEL, POSITIVE_LABEL
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
from aletheia_lab.benchmark.p2.label_controls import (
    CODE_TO_SEMANTIC,
    LABEL_REPAIR_SCHEMA_VERSION,
    PREDICTION_EVALUATION_SOURCE_SCHEMA_VERSION,
    PREDICTION_EVIDENCE_SCHEMA_VERSION,
    PREDICTION_REPORT_SCHEMA_VERSION,
    REPAIR_PROTOCOL_VERSION,
    SEMANTIC_TARGET_SOURCE_SCHEMA_VERSION,
    SEMANTIC_TO_CODE,
    SERIALIZATION_PROTOCOL_VERSION,
    TARGET_CODEC_VERSION,
    BenignControlReadiness,
    LabelControlError,
    LabelRepairResult,
    PredictionEquivalenceEvidence,
    PredictionEquivalenceReport,
    PredictionEvaluationSource,
    SemanticTargetSource,
    SerializationControlSpec,
    SerializationRoundTripResult,
    codec_sha256,
    decode_target_code,
    encode_target_label,
)
from aletheia_lab.benchmark.p2.label_controls import (
    apply_label_repair as _apply_label_repair,
)
from aletheia_lab.benchmark.p2.label_controls import (
    apply_serialization_roundtrip as _apply_serialization_roundtrip,
)
from aletheia_lab.benchmark.p2.label_controls import (
    benign_control_readiness as _benign_control_readiness,
)
from aletheia_lab.benchmark.p2.label_controls import (
    validate_label_repair as _validate_label_repair,
)
from aletheia_lab.benchmark.p2.label_controls import (
    validate_serialization_roundtrip as _validate_serialization_roundtrip,
)
from aletheia_lab.benchmark.p2.label_controls import (
    verify_prediction_equivalence as _verify_prediction_equivalence,
)
from aletheia_lab.benchmark.p2.label_noise import (
    LABEL_MUTATION_SCHEMA_VERSION,
    LABEL_SOURCE_SCHEMA_VERSION,
    LabelCorruptionResult,
    LabelCorruptionSpec,
    LabelNoiseError,
    LabelNoiseSource,
    MutationEntry,
    MutationMap,
    apply_label_corruption,
)
from aletheia_lab.benchmark.p2.mechanism_validation import (
    LabelBenignControlInputs,
    LabelRepairControlInputs,
    validate_mechanism_candidate,
)

_HEX_F = "f" * 64
_HEX_D = "d" * 64
_HEX_C = "c" * 64
_HEX_0 = "0" * 64
_HEX_A = "a" * 64
_HEX_B = "b" * 64

#: The frozen alpha plan pins these two slots. The tests restate the numbers so
#: a silent change to the contract shows up here as a failure rather than as a
#: quietly different experiment.
_REPAIR_SEED = 204
_REPAIR_REFERENCE_RATE = 0.20
_SERIALIZATION_SEED = 205

#: Field names that must never appear in a control artifact, at any depth.
_FORBIDDEN_FIELD_TOKENS = (
    "measured_outcome",
    "outcome",
    "family_class",
    "eligibility",
    "eligible",
    "improvement",
    "stable",
    "benign",
    "passed",
    "cause",
)


def _ids(count: int) -> tuple[str, ...]:
    return tuple(f"{index:05d}-SYNTH" for index in range(count))


def _binary(count: int) -> tuple[int, ...]:
    return tuple(index % 2 for index in range(count))


def _semantic(count: int) -> tuple[str, ...]:
    return tuple(POSITIVE_LABEL if index % 2 else NEGATIVE_LABEL for index in range(count))


def _clean_source(count: int = 1000, **overrides: object) -> LabelNoiseSource:
    payload: dict[str, object] = {
        "schema_version": LABEL_SOURCE_SCHEMA_VERSION,
        "split": "train",
        "record_ids": _ids(count),
        "targets": _binary(count),
        "attested_feature_matrix_sha256": _HEX_F,
        "attested_preprocessing_specification_sha256": _HEX_D,
        "attested_model_specification_sha256": _HEX_C,
    }
    payload.update(overrides)
    return LabelNoiseSource(**payload)  # type: ignore[arg-type]


def _corruption_spec(
    flip_rate: float = _REPAIR_REFERENCE_RATE, seed: int = _REPAIR_SEED
) -> LabelCorruptionSpec:
    return LabelCorruptionSpec(
        parameters=LabelNoiseParameters(
            flip_rate=flip_rate,
            flip_direction="symmetric",
            selection_policy="seeded_record_hash",
            scope="train",
        ),
        seed=seed,
    )


def _semantic_source(count: int = 200, **overrides: object) -> SemanticTargetSource:
    payload: dict[str, object] = {
        "schema_version": SEMANTIC_TARGET_SOURCE_SCHEMA_VERSION,
        "split": "train",
        "record_ids": _ids(count),
        "targets": _semantic(count),
        "attested_feature_matrix_sha256": _HEX_F,
        "attested_preprocessing_specification_sha256": _HEX_D,
        "attested_model_specification_sha256": _HEX_C,
    }
    payload.update(overrides)
    return SemanticTargetSource(**payload)  # type: ignore[arg-type]


def _serialization_spec(
    flip_rate: float = 0.0, seed: int = _SERIALIZATION_SEED, scope: str = "train"
) -> SerializationControlSpec:
    return SerializationControlSpec(
        parameters=LabelNoiseParameters(
            flip_rate=flip_rate,
            flip_direction="symmetric",
            selection_policy="seeded_record_hash",
            scope=scope,  # type: ignore[arg-type]
        ),
        seed=seed,
    )


def _control_slot(kind: str) -> CandidateSlot:
    repair = kind == "repair"
    seed = _REPAIR_SEED if repair else _SERIALIZATION_SEED
    rate = _REPAIR_REFERENCE_RATE if repair else 0.0
    intervention_type = (
        "training_target_label_repair" if repair else "target_label_serialization_roundtrip"
    )
    role = "designed_improvement_control" if repair else "designed_benign_control"
    identity = FamilyIdentity(
        dataset_snapshot_id="telco_customer_churn@2026-07",
        dataset_sha256=_HEX_A,
        model_data_split_manifest_sha256=_HEX_B,
        fault_type="label_noise",
        intervention_type=intervention_type,
        canonical_intervention_parameters=LabelNoiseParameters(
            flip_rate=rate,
            flip_direction="symmetric",
            selection_policy="seeded_record_hash",
            scope="train",
        ),
        seed=seed,
        reference_construction_id="clean-test-reference/v1",
        injector_contract_version="label-control/v1",
        model_specification_sha256=_HEX_C,
        preprocessing_specification_sha256=_HEX_D,
        identity_schema_version="p2-family-identity/v1",
    )
    return CandidateSlot(
        slot_id="M2-I1" if repair else "M2-B1",
        fault_type="label_noise",
        slot_kind="primary",
        role=role,  # type: ignore[arg-type]
        identity=identity,
    )


def _prediction_source(
    count: int = 200,
    *,
    record_ids: tuple[str, ...] | None = None,
    true_labels: tuple[int, ...] | None = None,
) -> PredictionEvaluationSource:
    return PredictionEvaluationSource(
        schema_version=PREDICTION_EVALUATION_SOURCE_SCHEMA_VERSION,
        split="test",
        record_ids=record_ids or tuple(f"test-{item}" for item in _ids(count)),
        true_labels=true_labels or _binary(count),
        attested_test_feature_matrix_sha256=_HEX_A,
        attested_split_manifest_sha256=_HEX_B,
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


def apply_label_repair(
    *,
    source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    spec: LabelCorruptionSpec,
    slot: CandidateSlot | None = None,
) -> LabelRepairResult:
    return _apply_label_repair(
        source=source, corrupted=corrupted, spec=spec, slot=slot or _control_slot("repair")
    )


def validate_label_repair(
    result: LabelRepairResult,
    *,
    source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    spec: LabelCorruptionSpec,
    slot: CandidateSlot | None = None,
) -> LabelRepairResult:
    return _validate_label_repair(
        result,
        source=source,
        corrupted=corrupted,
        spec=spec,
        slot=slot or _control_slot("repair"),
    )


def apply_serialization_roundtrip(
    *,
    source: SemanticTargetSource,
    spec: SerializationControlSpec,
    slot: CandidateSlot | None = None,
) -> SerializationRoundTripResult:
    return _apply_serialization_roundtrip(
        source=source, spec=spec, slot=slot or _control_slot("serialization")
    )


def validate_serialization_roundtrip(
    result: SerializationRoundTripResult,
    *,
    source: SemanticTargetSource,
    spec: SerializationControlSpec,
    slot: CandidateSlot | None = None,
) -> SerializationRoundTripResult:
    return _validate_serialization_roundtrip(
        result, source=source, spec=spec, slot=slot or _control_slot("serialization")
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
def clean_source() -> LabelNoiseSource:
    return _clean_source()


@pytest.fixture
def corruption_spec() -> LabelCorruptionSpec:
    return _corruption_spec()


@pytest.fixture
def corrupted(
    clean_source: LabelNoiseSource, corruption_spec: LabelCorruptionSpec
) -> LabelCorruptionResult:
    return apply_label_corruption(source=clean_source, spec=corruption_spec)


@pytest.fixture
def repaired(
    clean_source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    corruption_spec: LabelCorruptionSpec,
) -> LabelRepairResult:
    return apply_label_repair(source=clean_source, corrupted=corrupted, spec=corruption_spec)


@pytest.fixture
def semantic_source() -> SemanticTargetSource:
    return _semantic_source()


@pytest.fixture
def serialization_spec() -> SerializationControlSpec:
    return _serialization_spec()


@pytest.fixture
def roundtrip(
    semantic_source: SemanticTargetSource, serialization_spec: SerializationControlSpec
) -> SerializationRoundTripResult:
    return apply_serialization_roundtrip(source=semantic_source, spec=serialization_spec)


# --------------------------------------------------------------------------- #
# Repair control — positive
# --------------------------------------------------------------------------- #


def test_repair_restores_the_trusted_clean_targets_exactly(
    clean_source: LabelNoiseSource, repaired: LabelRepairResult
) -> None:
    assert repaired.repaired_targets == clean_source.targets


def test_unified_validator_binds_the_label_repair_control(
    clean_source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    corruption_spec: LabelCorruptionSpec,
    repaired: LabelRepairResult,
) -> None:
    slot = _control_slot("repair")
    execution = _execution(slot)
    binding = validate_mechanism_candidate(
        repaired,
        slot=slot,
        inputs=LabelRepairControlInputs(
            source=clean_source,
            corrupted=corrupted,
            spec=corruption_spec,
        ),
        execution=execution,
        disposition=TechnicalDispositionEntry(
            candidate_id=execution.candidate_id,
            disposition="technically_valid",
        ),
    )
    assert binding.candidate_id == execution.candidate_id
    assert binding.artifact_sha256 == repaired.artifact_sha256()


def test_repair_restores_exactly_the_records_the_mutation_map_names(
    corrupted: LabelCorruptionResult, repaired: LabelRepairResult
) -> None:
    assert repaired.restored_record_ids == tuple(sorted(corrupted.mutation_map.record_ids()))


def test_repair_restores_the_declared_number_of_records(
    corrupted: LabelCorruptionResult, repaired: LabelRepairResult
) -> None:
    """The frozen rate and rounding rule imply 200 of 1000 records."""

    assert corrupted.mutation_map.count == 200
    assert repaired.provenance.restored_count == 200
    assert len(repaired.restored_record_ids) == 200


def test_repair_leaves_every_unmutated_record_untouched(
    corrupted: LabelCorruptionResult, repaired: LabelRepairResult
) -> None:
    mutated = corrupted.mutation_map.record_ids()
    corrupted_by_id = dict(zip(corrupted.record_ids, corrupted.mutated_targets, strict=True))
    repaired_by_id = dict(zip(repaired.record_ids, repaired.repaired_targets, strict=True))
    untouched = [record_id for record_id in repaired.record_ids if record_id not in mutated]
    assert untouched
    assert all(repaired_by_id[record_id] == corrupted_by_id[record_id] for record_id in untouched)


def test_repair_preserves_record_order_and_membership(
    clean_source: LabelNoiseSource, repaired: LabelRepairResult
) -> None:
    assert repaired.record_ids == clean_source.record_ids
    assert set(repaired.record_ids) == set(clean_source.record_ids)


def test_repair_is_byte_identical_when_rerun(
    clean_source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    corruption_spec: LabelCorruptionSpec,
    repaired: LabelRepairResult,
) -> None:
    again = apply_label_repair(source=clean_source, corrupted=corrupted, spec=corruption_spec)
    assert again.artifact_sha256() == repaired.artifact_sha256()


def test_repair_binds_both_corrupted_reference_digests(
    corrupted: LabelCorruptionResult, repaired: LabelRepairResult
) -> None:
    provenance = repaired.provenance
    assert provenance.corrupted_reference_artifact_sha256 == corrupted.artifact_sha256()
    assert provenance.corrupted_reference_semantic_sha256 == corrupted.semantic_sha256()
    assert provenance.mutation_map_sha256 == corrupted.mutation_map.canonical_sha256()


def test_repair_reproduces_the_clean_labelled_record_digest(
    clean_source: LabelNoiseSource, repaired: LabelRepairResult
) -> None:
    provenance = repaired.provenance
    assert provenance.repaired_targets_sha256 == clean_source.targets_sha256()
    assert provenance.clean_source_targets_sha256 == clean_source.targets_sha256()


def test_repair_copies_the_attested_hashes_from_the_clean_source(
    clean_source: LabelNoiseSource, repaired: LabelRepairResult
) -> None:
    provenance = repaired.provenance
    assert provenance.attested_feature_matrix_sha256 == (
        clean_source.attested_feature_matrix_sha256
    )
    assert provenance.attested_preprocessing_specification_sha256 == (
        clean_source.attested_preprocessing_specification_sha256
    )
    assert provenance.attested_model_specification_sha256 == (
        clean_source.attested_model_specification_sha256
    )


def test_repair_pins_its_protocol_and_intervention_type(repaired: LabelRepairResult) -> None:
    assert repaired.provenance.repair_protocol_version == REPAIR_PROTOCOL_VERSION
    assert repaired.provenance.intervention_type == "training_target_label_repair"
    assert repaired.provenance.schema_version == LABEL_REPAIR_SCHEMA_VERSION


def test_repair_result_cannot_carry_an_outcome_or_a_cause() -> None:
    names = _field_tokens(LabelRepairResult)
    offending = {name for name in names for token in _FORBIDDEN_FIELD_TOKENS if token in name}
    assert offending == set()


def test_validate_repair_accepts_the_artifact_it_produced(
    clean_source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    corruption_spec: LabelCorruptionSpec,
    repaired: LabelRepairResult,
) -> None:
    returned = validate_label_repair(
        repaired, source=clean_source, corrupted=corrupted, spec=corruption_spec
    )
    assert returned.artifact_sha256() == repaired.artifact_sha256()


# --------------------------------------------------------------------------- #
# Repair control — exploits
# --------------------------------------------------------------------------- #


def test_repair_rejects_a_forged_target_built_with_model_copy(
    clean_source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    corruption_spec: LabelCorruptionSpec,
    repaired: LabelRepairResult,
) -> None:
    flipped = list(repaired.repaired_targets)
    flipped[0] = 1 - flipped[0]
    forged = _forge(repaired, repaired_targets=tuple(flipped))
    with pytest.raises(LabelControlError, match="do not match the restoration"):
        validate_label_repair(
            forged, source=clean_source, corrupted=corrupted, spec=corruption_spec
        )


def test_repair_rejects_a_forged_result_built_with_model_construct(
    clean_source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    corruption_spec: LabelCorruptionSpec,
    repaired: LabelRepairResult,
) -> None:
    forged = LabelRepairResult.model_construct(
        schema_version=repaired.schema_version,
        record_ids=repaired.record_ids,
        repaired_targets=repaired.repaired_targets,
        restored_record_ids=repaired.restored_record_ids[:-1],
        provenance=repaired.provenance,
    )
    with pytest.raises(ValidationError, match="restored identifier count"):
        validate_label_repair(
            forged, source=clean_source, corrupted=corrupted, spec=corruption_spec
        )


def test_repair_rejects_a_clean_source_that_disagrees_with_the_mutation_map(
    corrupted: LabelCorruptionResult, corruption_spec: LabelCorruptionSpec
) -> None:
    other_targets = tuple(1 - label for label in _binary(1000))
    other_source = _clean_source(targets=other_targets)
    with pytest.raises(LabelNoiseError, match="target digest"):
        apply_label_repair(source=other_source, corrupted=corrupted, spec=corruption_spec)


def test_repair_rejects_a_reference_with_a_dropped_mutation_entry(
    clean_source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    corruption_spec: LabelCorruptionSpec,
) -> None:
    shrunk = _forge(
        corrupted,
        mutation_map=MutationMap(
            schema_version=LABEL_MUTATION_SCHEMA_VERSION,
            entries=corrupted.mutation_map.entries[:-1],
        ),
    )
    with pytest.raises((LabelNoiseError, ValidationError)):
        apply_label_repair(source=clean_source, corrupted=shrunk, spec=corruption_spec)


def test_repair_rejects_a_reference_with_an_invented_mutation_entry(
    clean_source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    corruption_spec: LabelCorruptionSpec,
) -> None:
    untouched = next(
        record_id
        for record_id in corrupted.record_ids
        if record_id not in corrupted.mutation_map.record_ids()
    )
    clean_by_id = dict(zip(clean_source.record_ids, clean_source.targets, strict=True))
    invented = MutationEntry(
        record_id=untouched,
        original_label=clean_by_id[untouched],
        mutated_label=1 - clean_by_id[untouched],
    )
    grown = _forge(
        corrupted,
        mutation_map=MutationMap(
            schema_version=LABEL_MUTATION_SCHEMA_VERSION,
            entries=(*corrupted.mutation_map.entries, invented),
        ),
    )
    with pytest.raises((LabelNoiseError, ValidationError)):
        apply_label_repair(source=clean_source, corrupted=grown, spec=corruption_spec)


def test_repair_rejects_a_mutation_entry_whose_original_label_is_wrong(
    clean_source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    corruption_spec: LabelCorruptionSpec,
) -> None:
    first = corrupted.mutation_map.entries[0]
    swapped = MutationEntry(
        record_id=first.record_id,
        original_label=first.mutated_label,
        mutated_label=first.original_label,
    )
    tampered = _forge(
        corrupted,
        mutation_map=MutationMap(
            schema_version=LABEL_MUTATION_SCHEMA_VERSION,
            entries=(swapped, *corrupted.mutation_map.entries[1:]),
        ),
    )
    with pytest.raises((LabelNoiseError, ValidationError)):
        apply_label_repair(source=clean_source, corrupted=tampered, spec=corruption_spec)


def test_repair_rejects_restored_identifiers_outside_the_mutation_map(
    clean_source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    corruption_spec: LabelCorruptionSpec,
    repaired: LabelRepairResult,
) -> None:
    untouched = next(
        record_id
        for record_id in repaired.record_ids
        if record_id not in corrupted.mutation_map.record_ids()
    )
    swapped = tuple(sorted({*repaired.restored_record_ids[1:], untouched}))
    forged = _forge(repaired, restored_record_ids=swapped)
    with pytest.raises(LabelControlError, match="do not match the mutation map"):
        validate_label_repair(
            forged, source=clean_source, corrupted=corrupted, spec=corruption_spec
        )


def test_repair_rejects_unsorted_restored_identifiers(repaired: LabelRepairResult) -> None:
    reordered = (repaired.restored_record_ids[-1], *repaired.restored_record_ids[:-1])
    with pytest.raises(ValidationError, match="canonical sorted order"):
        LabelRepairResult(
            record_ids=repaired.record_ids,
            repaired_targets=repaired.repaired_targets,
            restored_record_ids=reordered,
            provenance=repaired.provenance,
        )


def test_repair_rejects_duplicate_restored_identifiers(repaired: LabelRepairResult) -> None:
    duplicated = (repaired.restored_record_ids[0], *repaired.restored_record_ids)
    with pytest.raises(ValidationError, match="at most once"):
        LabelRepairResult(
            record_ids=repaired.record_ids,
            repaired_targets=repaired.repaired_targets,
            restored_record_ids=duplicated,
            provenance=repaired.provenance,
        )


def test_repair_rejects_reordered_record_identifiers(
    clean_source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    corruption_spec: LabelCorruptionSpec,
    repaired: LabelRepairResult,
) -> None:
    forged = _forge(
        repaired,
        record_ids=tuple(reversed(repaired.record_ids)),
        repaired_targets=tuple(reversed(repaired.repaired_targets)),
    )
    with pytest.raises(LabelControlError, match="clean-test evaluation source|order included"):
        validate_label_repair(
            forged, source=clean_source, corrupted=corrupted, spec=corruption_spec
        )


def test_repair_rejects_a_changed_membership(
    clean_source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    corruption_spec: LabelCorruptionSpec,
    repaired: LabelRepairResult,
) -> None:
    renamed = ("99999-SYNTH", *repaired.record_ids[1:])
    forged = _forge(repaired, record_ids=renamed)
    with pytest.raises(LabelControlError, match="order included"):
        validate_label_repair(
            forged, source=clean_source, corrupted=corrupted, spec=corruption_spec
        )


@pytest.mark.parametrize(
    "field",
    [
        "clean_source_record_ids_sha256",
        "clean_source_membership_sha256",
        "repaired_membership_sha256",
        "corrupted_reference_artifact_sha256",
        "corrupted_reference_semantic_sha256",
        "mutation_map_sha256",
        "restored_record_ids_sha256",
    ],
)
def test_repair_rejects_a_tampered_provenance_digest(
    field: str,
    clean_source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    corruption_spec: LabelCorruptionSpec,
    repaired: LabelRepairResult,
) -> None:
    forged = _forge_provenance(repaired, **{field: _HEX_0})
    with pytest.raises((LabelControlError, ValidationError)):
        validate_label_repair(
            forged, source=clean_source, corrupted=corrupted, spec=corruption_spec
        )


def test_repair_rejects_a_tampered_clean_target_digest(repaired: LabelRepairResult) -> None:
    """Changing one side of the equality the provenance asserts must not pass."""

    with pytest.raises(ValidationError, match="reproduce the clean labelled record set"):
        _forge_provenance(repaired, clean_source_targets_sha256=_HEX_0).provenance.model_validate(
            _forge(repaired.provenance, clean_source_targets_sha256=_HEX_0).model_dump()
        )


def test_repair_rejects_a_seed_that_differs_from_the_specification(
    clean_source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    corruption_spec: LabelCorruptionSpec,
    repaired: LabelRepairResult,
) -> None:
    forged = _forge_provenance(repaired, seed=_REPAIR_SEED + 1)
    with pytest.raises(LabelControlError, match="seed differs"):
        validate_label_repair(
            forged, source=clean_source, corrupted=corrupted, spec=corruption_spec
        )


def test_repair_rejects_a_flip_rate_that_differs_from_the_specification(
    clean_source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    corruption_spec: LabelCorruptionSpec,
    repaired: LabelRepairResult,
) -> None:
    forged = _forge_provenance(repaired, corrupted_reference_flip_rate=0.05)
    with pytest.raises(LabelControlError, match="flip rate differs"):
        validate_label_repair(
            forged, source=clean_source, corrupted=corrupted, spec=corruption_spec
        )


def test_repair_rejects_a_restored_count_that_contradicts_the_mutation_map(
    repaired: LabelRepairResult,
) -> None:
    with pytest.raises(ValidationError, match="restored identifier count"):
        LabelRepairResult(
            record_ids=repaired.record_ids,
            repaired_targets=repaired.repaired_targets,
            restored_record_ids=repaired.restored_record_ids,
            provenance=_forge(repaired.provenance, restored_count=199, restored_ratio=199 / 1000),
        )


def test_repair_rejects_an_attested_hash_the_clean_source_never_declared(
    clean_source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    corruption_spec: LabelCorruptionSpec,
    repaired: LabelRepairResult,
) -> None:
    forged = _forge_provenance(repaired, attested_model_specification_sha256=_HEX_0)
    with pytest.raises(LabelControlError, match="is not the value the clean source attested"):
        validate_label_repair(
            forged, source=clean_source, corrupted=corrupted, spec=corruption_spec
        )


def test_repair_cannot_be_replayed_against_a_different_source(
    corrupted: LabelCorruptionResult,
    corruption_spec: LabelCorruptionSpec,
    repaired: LabelRepairResult,
) -> None:
    """A valid artifact must not validate against a source it was not built on."""

    other_source = _clean_source(attested_feature_matrix_sha256="e" * 64)
    with pytest.raises((LabelControlError, LabelNoiseError)):
        validate_label_repair(
            repaired, source=other_source, corrupted=corrupted, spec=corruption_spec
        )


def test_repair_rejects_a_corrupted_reference_from_a_different_rate(
    clean_source: LabelNoiseSource, corrupted: LabelCorruptionResult
) -> None:
    with pytest.raises(LabelControlError, match="frozen M2-I1"):
        apply_label_repair(
            source=clean_source, corrupted=corrupted, spec=_corruption_spec(flip_rate=0.05)
        )


def test_repair_rejects_a_noncanonical_seed_before_restoration(
    clean_source: LabelNoiseSource,
) -> None:
    wrong = _corruption_spec(seed=999)
    corrupted = apply_label_corruption(source=clean_source, spec=wrong)
    with pytest.raises(LabelControlError, match="frozen M2-I1"):
        apply_label_repair(source=clean_source, corrupted=corrupted, spec=wrong)


def test_repair_rejects_a_forged_lifecycle_slot(
    clean_source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    corruption_spec: LabelCorruptionSpec,
) -> None:
    forged = _forge(_control_slot("repair"), slot_id="M2-B1")
    with pytest.raises(LabelControlError, match="frozen slot M2-I1"):
        _apply_label_repair(
            source=clean_source,
            corrupted=corrupted,
            spec=corruption_spec,
            slot=forged,
        )


def test_repair_artifact_cannot_be_replayed_under_another_family_identity(
    clean_source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    corruption_spec: LabelCorruptionSpec,
    repaired: LabelRepairResult,
) -> None:
    slot = _control_slot("repair")
    foreign = _forge(
        slot,
        identity=_forge(slot.identity, dataset_sha256="9" * 64),
    )
    with pytest.raises(LabelControlError, match="control_slot_sha256"):
        _validate_label_repair(
            repaired,
            source=clean_source,
            corrupted=corrupted,
            spec=corruption_spec,
            slot=foreign,
        )


# --------------------------------------------------------------------------- #
# Serialization control — positive
# --------------------------------------------------------------------------- #


def test_roundtrip_recovers_every_original_semantic_label(
    semantic_source: SemanticTargetSource, roundtrip: SerializationRoundTripResult
) -> None:
    assert roundtrip.decoded_targets == semantic_source.targets


def test_roundtrip_uses_the_dataset_adapter_mapping(
    roundtrip: SerializationRoundTripResult,
) -> None:
    """Yes maps to 1 and No maps to 0, exactly as the loader binarises them."""

    assert SEMANTIC_TO_CODE[POSITIVE_LABEL] == 1
    assert SEMANTIC_TO_CODE[NEGATIVE_LABEL] == 0
    expected = tuple(SEMANTIC_TO_CODE[label] for label in roundtrip.original_targets)
    assert roundtrip.encoded_codes == expected


def test_roundtrip_preserves_membership_and_row_order(
    semantic_source: SemanticTargetSource, roundtrip: SerializationRoundTripResult
) -> None:
    assert roundtrip.record_ids == semantic_source.record_ids
    assert set(roundtrip.record_ids) == set(semantic_source.record_ids)
    assert len(roundtrip.record_ids) == len(semantic_source.record_ids)


def test_roundtrip_is_byte_identical_when_rerun(
    semantic_source: SemanticTargetSource,
    serialization_spec: SerializationControlSpec,
    roundtrip: SerializationRoundTripResult,
) -> None:
    again = apply_serialization_roundtrip(source=semantic_source, spec=serialization_spec)
    assert again.artifact_sha256() == roundtrip.artifact_sha256()


def test_codec_is_a_finite_bijection() -> None:
    assert len(SEMANTIC_TO_CODE) == len(CODE_TO_SEMANTIC) == 2
    for label, code in SEMANTIC_TO_CODE.items():
        assert decode_target_code(encode_target_label(label)) == label
        assert encode_target_label(decode_target_code(code)) == code


def test_codec_digest_is_stable_and_bound_into_the_artifact(
    roundtrip: SerializationRoundTripResult,
) -> None:
    assert codec_sha256() == codec_sha256()
    assert roundtrip.provenance.codec_sha256 == codec_sha256()
    assert roundtrip.provenance.codec_version == TARGET_CODEC_VERSION


def test_roundtrip_pins_its_protocol_and_intervention_type(
    roundtrip: SerializationRoundTripResult,
) -> None:
    provenance = roundtrip.provenance
    assert provenance.roundtrip_protocol_version == SERIALIZATION_PROTOCOL_VERSION
    assert provenance.intervention_type == "target_label_serialization_roundtrip"
    assert provenance.declared_flip_rate == 0.0


def test_roundtrip_result_carries_no_selection_or_mutation(
    roundtrip: SerializationRoundTripResult,
) -> None:
    """No record was selected, so there is nowhere to record a selection."""

    names = set(type(roundtrip).model_fields) | set(type(roundtrip.provenance).model_fields)
    assert not any("mutation" in name or "selection" in name for name in names)


def test_roundtrip_result_cannot_carry_a_self_declared_conclusion() -> None:
    names = _field_tokens(SerializationRoundTripResult)
    offending = {name for name in names for token in _FORBIDDEN_FIELD_TOKENS if token in name}
    assert offending == set()


def test_validate_roundtrip_accepts_the_artifact_it_produced(
    semantic_source: SemanticTargetSource,
    serialization_spec: SerializationControlSpec,
    roundtrip: SerializationRoundTripResult,
) -> None:
    returned = validate_serialization_roundtrip(
        roundtrip, source=semantic_source, spec=serialization_spec
    )
    assert returned.artifact_sha256() == roundtrip.artifact_sha256()


def _evidence(
    result: SerializationRoundTripResult, *, diverge: int = 0
) -> PredictionEquivalenceEvidence:
    evaluation_source = _prediction_source()
    reference = evaluation_source.true_labels
    roundtrip_predictions = list(reference)
    for position in range(diverge):
        roundtrip_predictions[position] = 1 - roundtrip_predictions[position]
    return PredictionEquivalenceEvidence(
        schema_version=PREDICTION_EVIDENCE_SCHEMA_VERSION,
        roundtrip_artifact_sha256=result.artifact_sha256(),
        evaluation_source_sha256=evaluation_source.artifact_sha256(),
        record_ids=evaluation_source.record_ids,
        true_labels=evaluation_source.true_labels,
        reference_predictions=reference,
        roundtrip_predictions=tuple(roundtrip_predictions),
    )


def test_unified_validator_binds_the_label_benign_control(
    semantic_source: SemanticTargetSource,
    serialization_spec: SerializationControlSpec,
    roundtrip: SerializationRoundTripResult,
) -> None:
    slot = _control_slot("serialization")
    execution = _execution(slot)
    evidence = _evidence(roundtrip)
    evaluation_source = _prediction_source(
        record_ids=evidence.record_ids,
        true_labels=evidence.true_labels,
    )
    binding = validate_mechanism_candidate(
        roundtrip,
        slot=slot,
        inputs=LabelBenignControlInputs(
            source=semantic_source,
            spec=serialization_spec,
            evaluation_source=evaluation_source,
            prediction_evidence=evidence,
        ),
        execution=execution,
        disposition=TechnicalDispositionEntry(
            candidate_id=execution.candidate_id,
            disposition="technically_valid",
        ),
    )
    assert binding.fault_type == "label_noise"
    assert binding.disposition.disposition == "technically_valid"
    assert binding.supporting_evidence_sha256 is not None


def test_unified_validator_cannot_promote_a_diverging_label_control(
    semantic_source: SemanticTargetSource,
    serialization_spec: SerializationControlSpec,
    roundtrip: SerializationRoundTripResult,
) -> None:
    slot = _control_slot("serialization")
    execution = _execution(slot)
    evidence = _evidence(roundtrip, diverge=1)
    evaluation_source = _prediction_source(
        record_ids=evidence.record_ids,
        true_labels=evidence.true_labels,
    )
    binding = validate_mechanism_candidate(
        roundtrip,
        slot=slot,
        inputs=LabelBenignControlInputs(
            source=semantic_source,
            spec=serialization_spec,
            evaluation_source=evaluation_source,
            prediction_evidence=evidence,
        ),
        execution=execution,
        disposition=TechnicalDispositionEntry(
            candidate_id=execution.candidate_id,
            disposition="technical_rejected",
            rejection_reason="benign_equivalence_failure",
        ),
    )
    assert binding.disposition.disposition == "technical_rejected"
    assert binding.disposition.rejection_reason == "benign_equivalence_failure"


def _source_for_roundtrip(result: SerializationRoundTripResult) -> SemanticTargetSource:
    return SemanticTargetSource(
        schema_version=SEMANTIC_TARGET_SOURCE_SCHEMA_VERSION,
        split="train",
        record_ids=result.record_ids,
        targets=result.original_targets,
        attested_feature_matrix_sha256=result.provenance.attested_feature_matrix_sha256,
        attested_preprocessing_specification_sha256=(
            result.provenance.attested_preprocessing_specification_sha256
        ),
        attested_model_specification_sha256=result.provenance.attested_model_specification_sha256,
    )


def verify_prediction_equivalence(
    evidence: PredictionEquivalenceEvidence,
    *,
    result: SerializationRoundTripResult,
    evaluation_source: PredictionEvaluationSource | None = None,
) -> PredictionEquivalenceReport:
    return _verify_prediction_equivalence(
        evidence,
        result=result,
        source=_source_for_roundtrip(result),
        spec=_serialization_spec(),
        slot=_control_slot("serialization"),
        evaluation_source=evaluation_source
        or _prediction_source(record_ids=evidence.record_ids, true_labels=evidence.true_labels),
    )


def test_prediction_comparison_derives_its_verdict_from_the_vectors(
    roundtrip: SerializationRoundTripResult,
) -> None:
    report = verify_prediction_equivalence(_evidence(roundtrip), result=roundtrip)
    assert report.diverging_record_count == 0
    assert report.verdict == "prediction_invariance_verified"
    assert report.compared_record_count == len(roundtrip.record_ids)


def test_benign_control_without_predictions_is_pending_not_passing(
    semantic_source: SemanticTargetSource,
    serialization_spec: SerializationControlSpec,
    roundtrip: SerializationRoundTripResult,
) -> None:
    readiness = _benign_control_readiness(
        result=roundtrip,
        source=semantic_source,
        spec=serialization_spec,
        slot=_control_slot("serialization"),
    )
    assert readiness == "pending_post_execution_equivalence"


def test_benign_control_with_equal_predictions_stops_short_of_a_pass(
    semantic_source: SemanticTargetSource,
    serialization_spec: SerializationControlSpec,
    roundtrip: SerializationRoundTripResult,
) -> None:
    evidence = _evidence(roundtrip)
    readiness = _benign_control_readiness(
        result=roundtrip,
        source=semantic_source,
        spec=serialization_spec,
        slot=_control_slot("serialization"),
        evaluation_source=_prediction_source(
            record_ids=evidence.record_ids, true_labels=evidence.true_labels
        ),
        prediction_evidence=evidence,
    )
    assert readiness == "structural_equivalence_verified_pending_guardrail_report"


# --------------------------------------------------------------------------- #
# Serialization control — exploits
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("label", ["Maybe", "yes", "YES", " Yes", "Yes ", "1", ""])
def test_roundtrip_source_rejects_a_label_outside_the_declared_vocabulary(label: str) -> None:
    with pytest.raises(ValidationError):
        _semantic_source(count=4, targets=(POSITIVE_LABEL, NEGATIVE_LABEL, POSITIVE_LABEL, label))


@pytest.mark.parametrize("label", ["yes", "NO", " No", "Churn"])
def test_encoder_refuses_to_normalise_a_near_miss_label(label: str) -> None:
    with pytest.raises(LabelControlError, match="unknown target label"):
        encode_target_label(label)


@pytest.mark.parametrize("code", [2, -1, 10])
def test_decoder_rejects_a_code_outside_the_declared_domain(code: int) -> None:
    with pytest.raises(LabelControlError, match="unknown target code"):
        decode_target_code(code)


def test_decoder_rejects_a_boolean_masquerading_as_a_code() -> None:
    with pytest.raises(LabelControlError, match="must be integers"):
        decode_target_code(True)


def test_roundtrip_rejects_a_forged_code_vector(
    roundtrip: SerializationRoundTripResult,
) -> None:
    flipped = list(roundtrip.encoded_codes)
    flipped[0] = 1 - flipped[0]
    with pytest.raises(ValidationError, match="not the encoding of its label"):
        SerializationRoundTripResult(
            record_ids=roundtrip.record_ids,
            original_targets=roundtrip.original_targets,
            encoded_codes=tuple(flipped),
            decoded_targets=roundtrip.decoded_targets,
            provenance=roundtrip.provenance,
        )


def test_roundtrip_rejects_a_forged_decoded_vector(
    roundtrip: SerializationRoundTripResult,
) -> None:
    decoded = list(roundtrip.decoded_targets)
    decoded[0] = NEGATIVE_LABEL if decoded[0] == POSITIVE_LABEL else POSITIVE_LABEL
    with pytest.raises(ValidationError, match="not the decoding of its code"):
        SerializationRoundTripResult(
            record_ids=roundtrip.record_ids,
            original_targets=roundtrip.original_targets,
            encoded_codes=roundtrip.encoded_codes,
            decoded_targets=tuple(decoded),
            provenance=roundtrip.provenance,
        )


def test_roundtrip_provenance_rejects_a_decoded_target_digest_that_moved(
    roundtrip: SerializationRoundTripResult,
) -> None:
    forged = _forge(roundtrip.provenance, decoded_targets_sha256=_HEX_0)
    with pytest.raises(ValidationError, match="must not change any label"):
        type(forged).model_validate(forged.model_dump())


def test_roundtrip_provenance_rejects_a_reordering(
    roundtrip: SerializationRoundTripResult,
) -> None:
    forged = _forge(roundtrip.provenance, decoded_record_ids_sha256=_HEX_0)
    with pytest.raises(ValidationError, match="must not reorder records"):
        type(forged).model_validate(forged.model_dump())


def test_roundtrip_provenance_rejects_a_membership_change(
    roundtrip: SerializationRoundTripResult,
) -> None:
    forged = _forge(roundtrip.provenance, decoded_membership_sha256=_HEX_0)
    with pytest.raises(ValidationError, match="must not change membership"):
        type(forged).model_validate(forged.model_dump())


def test_roundtrip_provenance_rejects_a_tampered_codec_digest(
    roundtrip: SerializationRoundTripResult,
) -> None:
    forged = _forge(roundtrip.provenance, codec_sha256=_HEX_0)
    with pytest.raises(ValidationError, match="pinned codec table"):
        type(forged).model_validate(forged.model_dump())


def test_roundtrip_rejects_a_source_it_was_not_built_from(
    serialization_spec: SerializationControlSpec, roundtrip: SerializationRoundTripResult
) -> None:
    other = _semantic_source(count=200, record_ids=tuple(reversed(_ids(200))))
    with pytest.raises(LabelControlError, match="clean-test evaluation source|order included"):
        validate_serialization_roundtrip(roundtrip, source=other, spec=serialization_spec)


def test_roundtrip_rejects_a_source_with_different_membership(
    serialization_spec: SerializationControlSpec, roundtrip: SerializationRoundTripResult
) -> None:
    renamed = ("99999-SYNTH", *_ids(200)[1:])
    other = _semantic_source(count=200, record_ids=renamed)
    with pytest.raises(LabelControlError, match="order included"):
        validate_serialization_roundtrip(roundtrip, source=other, spec=serialization_spec)


def test_serialization_spec_refuses_a_positive_flip_rate() -> None:
    """A zero-rate corruption renamed as a benign control must not construct."""

    with pytest.raises(ValidationError, match="changes no label"):
        _serialization_spec(flip_rate=0.20)


def test_serialization_spec_refuses_a_noncanonical_seed() -> None:
    with pytest.raises(ValidationError, match="requires seed 205"):
        _serialization_spec(seed=999)


def test_serialization_rejects_a_forged_lifecycle_slot(
    semantic_source: SemanticTargetSource,
    serialization_spec: SerializationControlSpec,
) -> None:
    forged = _forge(_control_slot("serialization"), role="fault_directed")
    with pytest.raises(LabelControlError, match="frozen slot M2-B1"):
        _apply_serialization_roundtrip(
            source=semantic_source,
            spec=serialization_spec,
            slot=forged,
        )


def test_serialization_artifact_cannot_be_replayed_under_another_family_identity(
    semantic_source: SemanticTargetSource,
    serialization_spec: SerializationControlSpec,
    roundtrip: SerializationRoundTripResult,
) -> None:
    slot = _control_slot("serialization")
    foreign = _forge(
        slot,
        identity=_forge(slot.identity, dataset_sha256="9" * 64),
    )
    with pytest.raises(LabelControlError, match="control_slot_sha256"):
        _validate_serialization_roundtrip(
            roundtrip,
            source=semantic_source,
            spec=serialization_spec,
            slot=foreign,
        )


def test_serialization_spec_refuses_a_scope_outside_training() -> None:
    """The scope is closed at the parameter type, before the spec is reached.

    The spec repeats the check for defence in depth, but the boundary that
    actually rejects a non-training scope is the parameter literal, so that is
    what this test pins.
    """

    with pytest.raises(ValidationError, match="Input should be 'train'"):
        _serialization_spec(scope="test")


def test_roundtrip_rejects_a_seed_that_differs_from_the_specification(
    semantic_source: SemanticTargetSource,
    serialization_spec: SerializationControlSpec,
    roundtrip: SerializationRoundTripResult,
) -> None:
    forged = _forge(roundtrip, provenance=_forge(roundtrip.provenance, seed=999))
    with pytest.raises(LabelControlError, match="seed differs"):
        validate_serialization_roundtrip(forged, source=semantic_source, spec=serialization_spec)


def test_prediction_evidence_must_be_bound_to_its_own_artifact(
    roundtrip: SerializationRoundTripResult,
) -> None:
    evidence = _evidence(roundtrip)
    forged = _forge(evidence, roundtrip_artifact_sha256=_HEX_0)
    with pytest.raises(LabelControlError, match="not bound to this round-trip artifact"):
        verify_prediction_equivalence(forged, result=roundtrip)


def test_prediction_evidence_must_cover_the_same_records_in_the_same_order(
    roundtrip: SerializationRoundTripResult,
) -> None:
    evidence = _evidence(roundtrip)
    forged = _forge(evidence, record_ids=tuple(reversed(evidence.record_ids)))
    with pytest.raises(LabelControlError, match="clean-test evaluation source|order included"):
        verify_prediction_equivalence(forged, result=roundtrip)


def test_prediction_evidence_uses_clean_test_ids_not_training_ids(
    roundtrip: SerializationRoundTripResult,
) -> None:
    evidence = _evidence(roundtrip)
    assert evidence.record_ids != roundtrip.record_ids
    assert all(record_id.startswith("test-") for record_id in evidence.record_ids)


def test_prediction_evidence_rejects_a_different_clean_test_source(
    roundtrip: SerializationRoundTripResult,
) -> None:
    evidence = _evidence(roundtrip)
    different = _prediction_source(
        record_ids=("test-foreign", *evidence.record_ids[1:]),
        true_labels=evidence.true_labels,
    )
    with pytest.raises(LabelControlError, match="clean-test evaluation source"):
        verify_prediction_equivalence(evidence, result=roundtrip, evaluation_source=different)


def test_prediction_evidence_rejects_a_non_binary_vector(
    roundtrip: SerializationRoundTripResult,
) -> None:
    evidence = _evidence(roundtrip)
    broken = (2, *evidence.reference_predictions[1:])
    with pytest.raises(ValidationError, match="must be binary"):
        PredictionEquivalenceEvidence(
            schema_version=PREDICTION_EVIDENCE_SCHEMA_VERSION,
            roundtrip_artifact_sha256=evidence.roundtrip_artifact_sha256,
            evaluation_source_sha256=evidence.evaluation_source_sha256,
            record_ids=evidence.record_ids,
            true_labels=evidence.true_labels,
            reference_predictions=broken,
            roundtrip_predictions=evidence.roundtrip_predictions,
        )


def test_prediction_report_cannot_claim_a_verdict_its_counts_contradict(
    roundtrip: SerializationRoundTripResult,
) -> None:
    with pytest.raises(ValidationError, match="derived from the divergence count"):
        PredictionEquivalenceReport(
            schema_version=PREDICTION_REPORT_SCHEMA_VERSION,
            verdict="prediction_invariance_verified",
            compared_record_count=200,
            diverging_record_count=3,
            evidence_sha256=_HEX_0,
            roundtrip_artifact_sha256=roundtrip.artifact_sha256(),
            evaluation_source_sha256=_HEX_0,
        )


def test_readiness_cannot_accept_a_self_declared_prediction_report() -> None:
    assert "prediction_report" not in signature(_benign_control_readiness).parameters


def test_codec_tables_are_runtime_immutable() -> None:
    with pytest.raises(TypeError):
        SEMANTIC_TO_CODE[POSITIVE_LABEL] = 0  # type: ignore[index]
    with pytest.raises(TypeError):
        CODE_TO_SEMANTIC[1] = NEGATIVE_LABEL  # type: ignore[index]


def test_diverging_predictions_become_an_equivalence_failure_not_a_stable_result(
    semantic_source: SemanticTargetSource,
    serialization_spec: SerializationControlSpec,
    roundtrip: SerializationRoundTripResult,
) -> None:
    evidence = _evidence(roundtrip, diverge=4)
    report = verify_prediction_equivalence(evidence, result=roundtrip)
    assert report.verdict == "prediction_divergence"
    readiness = _benign_control_readiness(
        result=roundtrip,
        source=semantic_source,
        spec=serialization_spec,
        slot=_control_slot("serialization"),
        evaluation_source=_prediction_source(
            record_ids=evidence.record_ids, true_labels=evidence.true_labels
        ),
        prediction_evidence=evidence,
    )
    assert readiness == "benign_equivalence_failure"


def test_benign_readiness_rejects_evidence_from_another_artifact(
    semantic_source: SemanticTargetSource,
    serialization_spec: SerializationControlSpec,
    roundtrip: SerializationRoundTripResult,
) -> None:
    other = apply_serialization_roundtrip(
        source=_semantic_source(count=120), spec=serialization_spec
    )
    foreign = _evidence(other)
    with pytest.raises(LabelControlError, match="not bound to this round-trip artifact"):
        _benign_control_readiness(
            result=roundtrip,
            source=semantic_source,
            spec=serialization_spec,
            slot=_control_slot("serialization"),
            evaluation_source=_prediction_source(
                record_ids=foreign.record_ids, true_labels=foreign.true_labels
            ),
            prediction_evidence=foreign,
        )


def test_benign_readiness_never_returns_a_benign_pass(
    semantic_source: SemanticTargetSource,
    serialization_spec: SerializationControlSpec,
    roundtrip: SerializationRoundTripResult,
) -> None:
    """The strongest reachable state stops one step short of benign_control.

    The readiness type itself is checked as well as the two reachable values.
    A future edit that adds a benign member to the literal would pass a
    value-only assertion, because no call site would return it yet.
    """

    declared = set(get_args(BenignControlReadiness))
    assert declared == {
        "pending_post_execution_equivalence",
        "structural_equivalence_verified_pending_guardrail_report",
        "benign_equivalence_failure",
    }
    assert not any(state in {"benign", "benign_control", "stable"} for state in declared)

    evidence = _evidence(roundtrip)
    evaluation_source = _prediction_source(
        record_ids=evidence.record_ids, true_labels=evidence.true_labels
    )
    reachable = {
        _benign_control_readiness(
            result=roundtrip,
            source=semantic_source,
            spec=serialization_spec,
            slot=_control_slot("serialization"),
        ),
        _benign_control_readiness(
            result=roundtrip,
            source=semantic_source,
            spec=serialization_spec,
            slot=_control_slot("serialization"),
            evaluation_source=evaluation_source,
            prediction_evidence=evidence,
        ),
    }
    assert reachable <= declared


# --------------------------------------------------------------------------- #
# Digest coverage
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "field",
    [
        "seed",
        "restored_count",
        "clean_source_record_ids_sha256",
        "corrupted_reference_artifact_sha256",
        "corrupted_reference_semantic_sha256",
        "mutation_map_sha256",
        "restored_record_ids_sha256",
        "control_slot_sha256",
        "attested_feature_matrix_sha256",
        "attested_preprocessing_specification_sha256",
        "attested_model_specification_sha256",
    ],
)
def test_every_bound_repair_provenance_field_changes_the_artifact_digest(
    field: str, repaired: LabelRepairResult
) -> None:
    baseline = repaired.artifact_sha256()
    value: object = _HEX_0 if field.endswith("sha256") else 999
    assert _forge_provenance(repaired, **{field: value}).artifact_sha256() != baseline


@pytest.mark.parametrize(
    "field",
    [
        "seed",
        "codec_sha256",
        "original_record_ids_sha256",
        "original_targets_sha256",
        "control_slot_sha256",
        "attested_feature_matrix_sha256",
        "attested_model_specification_sha256",
    ],
)
def test_every_bound_roundtrip_provenance_field_changes_the_artifact_digest(
    field: str, roundtrip: SerializationRoundTripResult
) -> None:
    baseline = roundtrip.artifact_sha256()
    value: object = _HEX_0 if field.endswith("sha256") else 999
    forged = _forge(roundtrip, provenance=_forge(roundtrip.provenance, **{field: value}))
    assert forged.artifact_sha256() != baseline


def test_repair_artifact_digest_is_sensitive_to_row_order(
    repaired: LabelRepairResult,
) -> None:
    reordered = _forge(
        repaired,
        record_ids=tuple(reversed(repaired.record_ids)),
        repaired_targets=tuple(reversed(repaired.repaired_targets)),
    )
    assert reordered.artifact_sha256() != repaired.artifact_sha256()


def test_roundtrip_artifact_digest_is_sensitive_to_row_order(
    roundtrip: SerializationRoundTripResult,
) -> None:
    reordered = _forge(
        roundtrip,
        record_ids=tuple(reversed(roundtrip.record_ids)),
        original_targets=tuple(reversed(roundtrip.original_targets)),
        encoded_codes=tuple(reversed(roundtrip.encoded_codes)),
        decoded_targets=tuple(reversed(roundtrip.decoded_targets)),
    )
    assert reordered.artifact_sha256() != roundtrip.artifact_sha256()


def test_neither_control_publishes_a_semantic_digest() -> None:
    """No consumer needs one, and an unused digest is a claim nobody checks."""

    assert not hasattr(LabelRepairResult, "semantic_sha256")
    assert not hasattr(SerializationRoundTripResult, "semantic_sha256")


def test_artifact_digests_bind_the_pinned_protocol_versions(
    repaired: LabelRepairResult, roundtrip: SerializationRoundTripResult
) -> None:
    repair_payload = repaired.model_dump(mode="json")
    roundtrip_payload = roundtrip.model_dump(mode="json")
    assert repair_payload["provenance"]["repair_protocol_version"] == REPAIR_PROTOCOL_VERSION
    assert (
        roundtrip_payload["provenance"]["roundtrip_protocol_version"]
        == SERIALIZATION_PROTOCOL_VERSION
    )
    assert repaired.artifact_sha256() != roundtrip.artifact_sha256()
