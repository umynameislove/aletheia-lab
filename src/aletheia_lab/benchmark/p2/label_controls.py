"""Predeclared semantics-preserving and repair controls for the label mechanism.

Two controls live here, and they answer two different questions about the label
mechanism. Neither of them is a corruption.

**The repair control** takes a predeclared corrupted training reference, restores
exactly the records the corruption touched using trusted original labels, and
changes nothing else. It exists to show that the measured effect tracks the
labels rather than an unrelated property of the pipeline. The module proves the
restoration is faithful; it never claims the repair improved anything.

**The serialization round-trip control** encodes semantic target labels into a
finite bijective code and decodes them back. It exists to show that touching the
target-serialization path without changing target semantics leaves the
experiment alone. It is *not* zero-rate label corruption: no corruption injector
runs, no record is selected, and no label changes value at any point.

Three boundaries are enforced by construction rather than by discipline.

**No outcome vocabulary.** No model in this module owns a field for measured
outcome, eligibility, family class or improvement. A caller cannot smuggle a
verdict through a control artifact, because there is nowhere to put one.

**No self-declared conclusions.** There is no ``roundtrip_passed``,
``predictions_equal`` or ``guardrails_passed`` field. Every conclusion this
module reaches is the return value of a function that recomputed it from
authoritative vectors and digests.

**No guardrail reporting.** Prediction invariance is structurally verified from
exact vectors. Canonical macro-F1, minority-recall and confusion reporting
remains the responsibility of the later authoritative alpha evaluator. This
module therefore never produces a benign PASS: the strongest state it can reach
is structural verification pending that report.

Two error kinds are raised, matching the convention of the corruption injector:

* a malformed *object* raises :class:`pydantic.ValidationError`, because model
  validators report through Pydantic;
* a malformed *relationship between objects* raises
  :class:`LabelControlError`, because procedural checks run outside any model.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from types import MappingProxyType
from typing import Annotated, Final, Literal, NoReturn, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.baseline.schema import NEGATIVE_LABEL, POSITIVE_LABEL
from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import CandidateSlot
from aletheia_lab.benchmark.p2.identity import (
    SHA256_PATTERN,
    LabelNoiseParameters,
    LabelNoiseScope,
)
from aletheia_lab.benchmark.p2.label_noise import (
    BINARY_LABELS,
    LabelCorruptionResult,
    LabelCorruptionSpec,
    LabelNoiseSource,
    validate_label_corruption,
)
from aletheia_lab.benchmark.p2.validation import ContractViolation

# --------------------------------------------------------------------------- #
# Schema versions
# --------------------------------------------------------------------------- #

SEMANTIC_TARGET_SOURCE_SCHEMA_VERSION: Final[Literal["p2-semantic-target-source/v1"]] = (
    "p2-semantic-target-source/v1"
)
LABEL_REPAIR_SCHEMA_VERSION: Final[Literal["p2-label-repair/v1"]] = "p2-label-repair/v1"
LABEL_REPAIR_RESULT_SCHEMA_VERSION: Final[Literal["p2-label-repair-result/v1"]] = (
    "p2-label-repair-result/v1"
)
LABEL_REPAIR_ARTIFACT_DIGEST_SCHEMA_VERSION: Final[
    Literal["p2-label-repair-artifact-digest/v1"]
] = "p2-label-repair-artifact-digest/v1"
TARGET_CODEC_SCHEMA_VERSION: Final[Literal["p2-target-label-codec/v1"]] = "p2-target-label-codec/v1"
SERIALIZATION_ROUNDTRIP_SCHEMA_VERSION: Final[Literal["p2-target-serialization-roundtrip/v1"]] = (
    "p2-target-serialization-roundtrip/v1"
)
SERIALIZATION_RESULT_SCHEMA_VERSION: Final[
    Literal["p2-target-serialization-roundtrip-result/v1"]
] = "p2-target-serialization-roundtrip-result/v1"
SERIALIZATION_ARTIFACT_DIGEST_SCHEMA_VERSION: Final[
    Literal["p2-target-serialization-artifact-digest/v1"]
] = "p2-target-serialization-artifact-digest/v1"
PREDICTION_EVIDENCE_SCHEMA_VERSION: Final[Literal["p2-prediction-equivalence-evidence/v1"]] = (
    "p2-prediction-equivalence-evidence/v1"
)
PREDICTION_REPORT_SCHEMA_VERSION: Final[Literal["p2-prediction-equivalence-report/v1"]] = (
    "p2-prediction-equivalence-report/v1"
)
PREDICTION_EVALUATION_SOURCE_SCHEMA_VERSION: Final[
    Literal["p2-prediction-evaluation-source/v1"]
] = "p2-prediction-evaluation-source/v1"

#: Pinned protocol identifiers. Both are ``Literal`` types rather than free text,
#: so no caller can invent a protocol name that looks official.
REPAIR_PROTOCOL_VERSION: Final[Literal["training-target-label-repair/v1"]] = (
    "training-target-label-repair/v1"
)
SERIALIZATION_PROTOCOL_VERSION: Final[Literal["target-label-serialization-roundtrip/v1"]] = (
    "target-label-serialization-roundtrip/v1"
)
TARGET_CODEC_VERSION: Final[Literal["target-label-codec/v1"]] = "target-label-codec/v1"

#: Intervention types the frozen alpha plan binds to these two control slots.
REPAIR_INTERVENTION_TYPE: Final[Literal["training_target_label_repair"]] = (
    "training_target_label_repair"
)
SERIALIZATION_INTERVENTION_TYPE: Final[Literal["target_label_serialization_roundtrip"]] = (
    "target_label_serialization_roundtrip"
)
REPAIR_SLOT_ID: Final[Literal["M2-I1"]] = "M2-I1"
SERIALIZATION_SLOT_ID: Final[Literal["M2-B1"]] = "M2-B1"
REPAIR_SEED: Final[int] = 204
SERIALIZATION_SEED: Final[int] = 205
REPAIR_REFERENCE_RATE: Final[float] = 0.20
CONTROL_SLOT_BINDING_SCHEMA_VERSION: Final[Literal["p2-control-slot-binding/v1"]] = (
    "p2-control-slot-binding/v1"
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]

_MAX_RECORDS: Final[int] = 5_000_000
_FLOAT_TOLERANCE: Final[float] = 1e-12

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class LabelControlError(ContractViolation):
    """Raised when control artifacts disagree with one another.

    Malformed single objects raise ``pydantic.ValidationError`` instead: model
    validators report through Pydantic, which wraps every ``ValueError`` they
    raise. Keeping the two kinds apart lets callers tell "this object is
    invalid" from "these objects do not belong together".
    """


def _fail(message: str) -> NoReturn:
    """Reject a cross-object relationship."""

    raise LabelControlError(message)


def _revalidated(model: _ModelT) -> _ModelT:
    """Re-run validators even when a caller built the object unsafely.

    ``model_copy(update=...)`` and ``model_construct()`` skip validation in
    Pydantic v2, so any object arriving at a trust boundary is rebuilt from its
    own dump before it is trusted.
    """

    return type(model).model_validate(model.model_dump())


class _StrictFrozenModel(BaseModel):
    """Reject unknown fields, implicit coercion and post-construction mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


# --------------------------------------------------------------------------- #
# Target label codec
# --------------------------------------------------------------------------- #

#: The semantic target vocabulary, taken from the dataset adapter rather than
#: restated here. ``baseline.schema`` is the single source of truth for what a
#: positive and a negative churn label look like, and ``baseline.loader`` maps
#: the positive label to 1. Duplicating that decision would create a second
#: mapping that could silently drift out of agreement with the first.
SEMANTIC_TARGET_LABELS: Final[tuple[str, str]] = (NEGATIVE_LABEL, POSITIVE_LABEL)

#: The finite bijection between semantic labels and canonical codes.
SEMANTIC_TO_CODE: Final[Mapping[str, int]] = MappingProxyType(
    {NEGATIVE_LABEL: 0, POSITIVE_LABEL: 1}
)
CODE_TO_SEMANTIC: Final[Mapping[int, str]] = MappingProxyType(
    {0: NEGATIVE_LABEL, 1: POSITIVE_LABEL}
)


def _assert_codec_is_bijective() -> None:
    """Prove the pinned mapping is a bijection before anything can use it.

    A codec table is the kind of constant that survives a careless edit without
    anyone noticing, so the property is checked at import rather than left to a
    test that a future contributor might not run.
    """

    if set(SEMANTIC_TO_CODE) != set(SEMANTIC_TARGET_LABELS):
        raise LabelControlError("the codec must cover exactly the semantic target vocabulary")
    if set(SEMANTIC_TO_CODE.values()) != set(CODE_TO_SEMANTIC):
        raise LabelControlError("the codec code domain and its inverse disagree")
    if len(set(SEMANTIC_TO_CODE.values())) != len(SEMANTIC_TO_CODE):
        raise LabelControlError("the codec maps two labels onto one code")
    for label, code in SEMANTIC_TO_CODE.items():
        if CODE_TO_SEMANTIC[code] != label:
            raise LabelControlError("the codec inverse does not round-trip its own table")
    if set(CODE_TO_SEMANTIC) != set(BINARY_LABELS):
        raise LabelControlError("the codec must produce the binary codes the mechanism uses")


_assert_codec_is_bijective()


def encode_target_label(label: str) -> int:
    """Encode one semantic label.

    Unknown labels are rejected outright. Nothing is trimmed, case-folded or
    Unicode-normalized on the way in: silently repairing ``" yes"`` would make
    the control claim a round-trip it did not perform.
    """

    if not isinstance(label, str):
        _fail("target labels must be strings")
    code = SEMANTIC_TO_CODE.get(label)
    if code is None:
        _fail(f"unknown target label {label!r}; the codec accepts only the declared vocabulary")
    return code


def decode_target_code(code: int) -> str:
    """Decode one canonical code back to its semantic label."""

    if isinstance(code, bool) or not isinstance(code, int):
        _fail("target codes must be integers")
    label = CODE_TO_SEMANTIC.get(code)
    if label is None:
        _fail(f"unknown target code {code!r}; the codec accepts only the declared code domain")
    return label


def codec_sha256() -> str:
    """Digest the pinned codec table so artifacts bind the mapping they used."""

    pairs: list[dict[str, object]] = [
        {"label": label, "code": code} for label, code in SEMANTIC_TO_CODE.items()
    ]
    pairs.sort(key=lambda item: str(item["label"]))
    return canonical_sha256(
        {
            "schema_version": TARGET_CODEC_SCHEMA_VERSION,
            "codec_version": TARGET_CODEC_VERSION,
            "mapping": pairs,
        }
    )


# --------------------------------------------------------------------------- #
# Shared record helpers
# --------------------------------------------------------------------------- #


def _check_record_ids(record_ids: Sequence[str]) -> None:
    """Reject anything that cannot anchor a per-record artifact."""

    if not record_ids:
        raise ValueError("a control source must contain at least one record")
    if len(record_ids) > _MAX_RECORDS:
        raise ValueError("a control source exceeds the supported record count")
    seen: set[str] = set()
    for position, record_id in enumerate(record_ids):
        if not record_id or record_id != record_id.strip():
            raise ValueError(f"record ID at position {position} must be non-blank and trimmed")
        if record_id != unicodedata.normalize("NFC", record_id):
            raise ValueError(f"record ID at position {position} must be Unicode NFC")
        if record_id in seen:
            raise ValueError("record IDs must be unique; duplicates cannot anchor a control")
        seen.add(record_id)


def _ordered_ids_digest(*, schema_version: str, record_ids: Sequence[str]) -> str:
    """Digest an identifier sequence with its order included."""

    return canonical_sha256({"schema_version": schema_version, "record_ids": list(record_ids)})


def _membership_digest(*, schema_version: str, record_ids: Iterable[str]) -> str:
    """Digest which records exist, order excluded."""

    return canonical_sha256({"schema_version": schema_version, "membership": sorted(record_ids)})


def _binary_records_digest(
    *, schema_version: str, record_ids: Sequence[str], labels: Sequence[int]
) -> str:
    """Digest a binary-labelled record set independently of listing order."""

    pairs: list[dict[str, object]] = [
        {"record_id": record_id, "label": label}
        for record_id, label in zip(record_ids, labels, strict=True)
    ]
    pairs.sort(key=lambda item: str(item["record_id"]))
    return canonical_sha256({"schema_version": schema_version, "labelled_records": pairs})


def _semantic_records_digest(
    *, schema_version: str, record_ids: Sequence[str], labels: Sequence[str]
) -> str:
    """Digest a semantically-labelled record set independently of listing order."""

    pairs: list[dict[str, object]] = [
        {"record_id": record_id, "label": label}
        for record_id, label in zip(record_ids, labels, strict=True)
    ]
    pairs.sort(key=lambda item: str(item["record_id"]))
    return canonical_sha256({"schema_version": schema_version, "labelled_records": pairs})


def _validate_control_slot(
    slot: CandidateSlot,
    *,
    expected_slot_id: Literal["M2-I1", "M2-B1"],
    expected_role: Literal["designed_improvement_control", "designed_benign_control"],
    expected_intervention_type: Literal[
        "training_target_label_repair", "target_label_serialization_roundtrip"
    ],
    expected_seed: int,
    expected_flip_rate: float,
    attested_preprocessing_specification_sha256: str,
    attested_model_specification_sha256: str,
) -> CandidateSlot:
    """Bind one control operation to its frozen lifecycle slot and identity."""

    slot = _revalidated(slot)
    identity = slot.identity
    parameters = identity.canonical_intervention_parameters
    if (
        slot.slot_id != expected_slot_id
        or slot.slot_kind != "primary"
        or slot.fault_type != "label_noise"
        or slot.role != expected_role
    ):
        _fail(f"control must be bound to frozen slot {expected_slot_id}")
    if (
        identity.fault_type != "label_noise"
        or identity.intervention_type != expected_intervention_type
        or identity.seed != expected_seed
        or not isinstance(parameters, LabelNoiseParameters)
    ):
        _fail(f"slot {expected_slot_id} identity differs from the frozen control contract")
    if (
        abs(parameters.flip_rate - expected_flip_rate) > _FLOAT_TOLERANCE
        or parameters.flip_direction != "symmetric"
        or parameters.selection_policy != "seeded_record_hash"
        or parameters.scope != "train"
    ):
        _fail(f"slot {expected_slot_id} parameters differ from the frozen control contract")
    if (
        identity.preprocessing_specification_sha256 != attested_preprocessing_specification_sha256
        or identity.model_specification_sha256 != attested_model_specification_sha256
    ):
        _fail(f"slot {expected_slot_id} identity is not bound to the control source attestations")
    return slot


def _control_slot_sha256(slot: CandidateSlot) -> str:
    """Bind the complete frozen slot, including its twelve-field family identity."""

    slot = _revalidated(slot)
    return canonical_sha256(
        {
            "schema_version": CONTROL_SLOT_BINDING_SCHEMA_VERSION,
            "slot": slot.model_dump(mode="json"),
        }
    )


# --------------------------------------------------------------------------- #
# 1. Paired label-repair control
# --------------------------------------------------------------------------- #


class LabelRepairProvenance(_StrictFrozenModel):
    """Everything an evaluator needs to reproduce and audit one repair.

    ``attested_*`` fields are copied and cross-bound to the validated clean
    source. They are not recomputed from raw feature, split or model data: this
    module never receives those artifacts, so it can only prove that the value
    it carries is the value the clean source declared.
    """

    schema_version: Literal["p2-label-repair/v1"]
    intervention_type: Literal["training_target_label_repair"]
    repair_protocol_version: Literal["training-target-label-repair/v1"]
    seed: int = Field(ge=0)
    corrupted_reference_flip_rate: float = Field(gt=0.0, le=0.5)
    record_count: int = Field(ge=1)
    restored_count: int = Field(ge=1)
    restored_ratio: float = Field(ge=0.0, le=1.0)
    clean_source_record_ids_sha256: Sha256
    clean_source_membership_sha256: Sha256
    clean_source_targets_sha256: Sha256
    repaired_targets_sha256: Sha256
    repaired_membership_sha256: Sha256
    corrupted_reference_artifact_sha256: Sha256
    corrupted_reference_semantic_sha256: Sha256
    mutation_map_sha256: Sha256
    restored_record_ids_sha256: Sha256
    control_slot_sha256: Sha256
    attested_feature_matrix_sha256: Sha256
    attested_preprocessing_specification_sha256: Sha256
    attested_model_specification_sha256: Sha256

    @model_validator(mode="after")
    def _counts_and_digests_agree(self) -> LabelRepairProvenance:
        if self.restored_count > self.record_count:
            raise ValueError("restored records cannot exceed the record count")
        expected_ratio = self.restored_count / self.record_count
        if abs(self.restored_ratio - expected_ratio) > _FLOAT_TOLERANCE:
            raise ValueError("restored_ratio must be derived from the two counts")
        if self.repaired_targets_sha256 != self.clean_source_targets_sha256:
            raise ValueError(
                "a complete repair must reproduce the clean labelled record set exactly"
            )
        if self.repaired_membership_sha256 != self.clean_source_membership_sha256:
            raise ValueError("a repair must not add, drop or rename a record")
        return self


class LabelRepairResult(_StrictFrozenModel):
    """A completed repair: restored targets and the provenance that pins them.

    The result describes what was restored. It carries no measured outcome,
    eligibility, family class or improvement claim; whether the repair helped is
    decided later, from measurements this module never performs.
    """

    schema_version: Literal["p2-label-repair-result/v1"] = "p2-label-repair-result/v1"
    record_ids: tuple[str, ...]
    repaired_targets: tuple[int, ...]
    restored_record_ids: tuple[str, ...]
    provenance: LabelRepairProvenance

    @model_validator(mode="after")
    def _result_is_internally_aligned(self) -> LabelRepairResult:
        _check_record_ids(self.record_ids)
        if len(self.record_ids) != len(self.repaired_targets):
            raise ValueError("result identifiers and targets must align")
        if len(self.record_ids) != self.provenance.record_count:
            raise ValueError("result record count must match its provenance")
        for position, label in enumerate(self.repaired_targets):
            if label not in BINARY_LABELS:
                raise ValueError(f"repaired target at position {position} must be binary 0 or 1")
        if not self.restored_record_ids:
            raise ValueError("a repair must restore at least one record")
        if len(set(self.restored_record_ids)) != len(self.restored_record_ids):
            raise ValueError("a record may be restored at most once")
        if list(self.restored_record_ids) != sorted(self.restored_record_ids):
            raise ValueError(
                "restored_record_ids must be in canonical sorted order so the ordered "
                "digest is reproducible"
            )
        missing = set(self.restored_record_ids) - set(self.record_ids)
        if missing:
            raise ValueError(f"restored records must exist in the source: {sorted(missing)}")
        if len(self.restored_record_ids) != self.provenance.restored_count:
            raise ValueError("restored identifier count must match its provenance")
        return self

    def artifact_sha256(self) -> str:
        """Digest every serialized field, including source row ordering.

        Reordering the source changes ``record_ids`` and
        ``clean_source_record_ids_sha256``, so it must change this digest even
        when the repaired experiment is semantically the same. No semantic
        digest is published for this artifact: nothing consumes one yet, and an
        unused digest is one more claim nobody checks.
        """

        return canonical_sha256(
            {
                "digest_schema_version": LABEL_REPAIR_ARTIFACT_DIGEST_SCHEMA_VERSION,
                "artifact": self.model_dump(mode="json"),
            }
        )


def _repair_from_corrupted(
    *, source: LabelNoiseSource, corrupted: LabelCorruptionResult
) -> tuple[tuple[int, ...], tuple[str, ...]]:
    """Restore exactly the mutated records, and nothing else.

    The restoration starts from the corrupted targets and walks the mutation
    map, rather than simply copying the clean targets across. Copying would
    produce the right answer even if the mutation map were wrong, which would
    make the repair unverifiable. Walking the map means a mutation entry that
    disagrees with the clean source is caught here.
    """

    clean_by_id = dict(zip(source.record_ids, source.targets, strict=True))
    working = dict(zip(corrupted.record_ids, corrupted.mutated_targets, strict=True))

    for entry in corrupted.mutation_map.entries:
        if entry.record_id not in working:
            _fail(
                f"mutation map names a record absent from the corrupted artifact: {entry.record_id}"
            )
        if working[entry.record_id] != entry.mutated_label:
            _fail(
                "the corrupted artifact does not carry the mutated label the mutation map "
                f"records for {entry.record_id}"
            )
        clean_label = clean_by_id.get(entry.record_id)
        if clean_label is None:
            _fail(f"mutation map names a record absent from the clean source: {entry.record_id}")
        if clean_label != entry.original_label:
            _fail(
                "the mutation map original label disagrees with the trusted clean source "
                f"for {entry.record_id}"
            )
        working[entry.record_id] = clean_label

    repaired = tuple(working[record_id] for record_id in corrupted.record_ids)
    restored = tuple(sorted(entry.record_id for entry in corrupted.mutation_map.entries))
    return repaired, restored


def apply_label_repair(
    *,
    source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    spec: LabelCorruptionSpec,
    slot: CandidateSlot,
) -> LabelRepairResult:
    """Repair a predeclared corrupted reference back to its trusted labels.

    The corrupted reference is revalidated against the clean source and the
    corruption specification before anything is restored, so a forged reference
    cannot become the baseline a repair is measured against.
    """

    source = _revalidated(source)
    spec = _revalidated(spec)
    slot = _validate_control_slot(
        slot,
        expected_slot_id=REPAIR_SLOT_ID,
        expected_role="designed_improvement_control",
        expected_intervention_type=REPAIR_INTERVENTION_TYPE,
        expected_seed=REPAIR_SEED,
        expected_flip_rate=REPAIR_REFERENCE_RATE,
        attested_preprocessing_specification_sha256=(
            source.attested_preprocessing_specification_sha256
        ),
        attested_model_specification_sha256=source.attested_model_specification_sha256,
    )
    if (
        spec.seed != slot.identity.seed
        or abs(spec.parameters.flip_rate - REPAIR_REFERENCE_RATE) > _FLOAT_TOLERANCE
        or spec.parameters != slot.identity.canonical_intervention_parameters
    ):
        _fail("repair specification differs from the frozen M2-I1 identity")
    corrupted = validate_label_corruption(corrupted, source=source, spec=spec)

    repaired, restored = _repair_from_corrupted(source=source, corrupted=corrupted)

    clean_targets_digest = source.targets_sha256()
    repaired_digest = _binary_records_digest(
        schema_version=source.schema_version,
        record_ids=corrupted.record_ids,
        labels=repaired,
    )
    if repaired_digest != clean_targets_digest:
        _fail("the repaired labelled record set does not reproduce the trusted clean source")

    provenance = LabelRepairProvenance(
        schema_version=LABEL_REPAIR_SCHEMA_VERSION,
        intervention_type=REPAIR_INTERVENTION_TYPE,
        repair_protocol_version=REPAIR_PROTOCOL_VERSION,
        seed=spec.seed,
        corrupted_reference_flip_rate=spec.parameters.flip_rate,
        record_count=source.record_count,
        restored_count=len(restored),
        restored_ratio=len(restored) / source.record_count,
        clean_source_record_ids_sha256=source.record_ids_sha256(),
        clean_source_membership_sha256=source.membership_sha256(),
        clean_source_targets_sha256=clean_targets_digest,
        repaired_targets_sha256=repaired_digest,
        repaired_membership_sha256=_membership_digest(
            schema_version=source.schema_version, record_ids=corrupted.record_ids
        ),
        corrupted_reference_artifact_sha256=corrupted.artifact_sha256(),
        corrupted_reference_semantic_sha256=corrupted.semantic_sha256(),
        mutation_map_sha256=corrupted.mutation_map.canonical_sha256(),
        restored_record_ids_sha256=_ordered_ids_digest(
            schema_version=LABEL_REPAIR_SCHEMA_VERSION, record_ids=restored
        ),
        control_slot_sha256=_control_slot_sha256(slot),
        attested_feature_matrix_sha256=source.attested_feature_matrix_sha256,
        attested_preprocessing_specification_sha256=(
            source.attested_preprocessing_specification_sha256
        ),
        attested_model_specification_sha256=source.attested_model_specification_sha256,
    )
    return LabelRepairResult(
        record_ids=corrupted.record_ids,
        repaired_targets=repaired,
        restored_record_ids=restored,
        provenance=provenance,
    )


def validate_label_repair(
    result: LabelRepairResult,
    *,
    source: LabelNoiseSource,
    corrupted: LabelCorruptionResult,
    spec: LabelCorruptionSpec,
    slot: CandidateSlot,
) -> LabelRepairResult:
    """Recompute every claim a repair result makes and reject any mismatch.

    Both the result and its inputs are rebuilt from their own dumps first, so an
    object assembled with ``model_copy`` or ``model_construct`` is re-validated
    rather than trusted.
    """

    result = _revalidated(result)
    source = _revalidated(source)
    spec = _revalidated(spec)
    slot = _validate_control_slot(
        slot,
        expected_slot_id=REPAIR_SLOT_ID,
        expected_role="designed_improvement_control",
        expected_intervention_type=REPAIR_INTERVENTION_TYPE,
        expected_seed=REPAIR_SEED,
        expected_flip_rate=REPAIR_REFERENCE_RATE,
        attested_preprocessing_specification_sha256=(
            source.attested_preprocessing_specification_sha256
        ),
        attested_model_specification_sha256=source.attested_model_specification_sha256,
    )
    if (
        spec.seed != slot.identity.seed
        or abs(spec.parameters.flip_rate - REPAIR_REFERENCE_RATE) > _FLOAT_TOLERANCE
        or spec.parameters != slot.identity.canonical_intervention_parameters
    ):
        _fail("repair specification differs from the frozen M2-I1 identity")
    corrupted = validate_label_corruption(corrupted, source=source, spec=spec)
    provenance = result.provenance

    if result.record_ids != corrupted.record_ids:
        _fail("repair identifiers must match the corrupted reference exactly, order included")
    if provenance.seed != spec.seed:
        _fail("repair provenance seed differs from the corruption specification")
    if abs(provenance.corrupted_reference_flip_rate - spec.parameters.flip_rate) > _FLOAT_TOLERANCE:
        _fail("repair provenance flip rate differs from the corruption specification")
    if provenance.record_count != source.record_count:
        _fail("repair provenance record count differs from the clean source")

    expected_repaired, expected_restored = _repair_from_corrupted(
        source=source, corrupted=corrupted
    )
    if result.repaired_targets != expected_repaired:
        _fail("repaired targets do not match the restoration derived from the mutation map")
    if result.restored_record_ids != expected_restored:
        _fail("restored identifiers do not match the mutation map, in content or in order")
    if provenance.restored_count != len(expected_restored):
        _fail("restored count does not match the mutation map size")

    untouched = set(result.record_ids) - set(expected_restored)
    clean_by_id = dict(zip(source.record_ids, source.targets, strict=True))
    repaired_by_id = dict(zip(result.record_ids, result.repaired_targets, strict=True))
    for record_id in sorted(untouched):
        if repaired_by_id[record_id] != clean_by_id[record_id]:
            _fail(f"a record outside the mutation map changed value: {record_id}")

    expected_digests: dict[str, str] = {
        "clean_source_record_ids_sha256": source.record_ids_sha256(),
        "clean_source_membership_sha256": source.membership_sha256(),
        "clean_source_targets_sha256": source.targets_sha256(),
        "repaired_targets_sha256": _binary_records_digest(
            schema_version=source.schema_version,
            record_ids=result.record_ids,
            labels=result.repaired_targets,
        ),
        "repaired_membership_sha256": _membership_digest(
            schema_version=source.schema_version, record_ids=result.record_ids
        ),
        "corrupted_reference_artifact_sha256": corrupted.artifact_sha256(),
        "corrupted_reference_semantic_sha256": corrupted.semantic_sha256(),
        "mutation_map_sha256": corrupted.mutation_map.canonical_sha256(),
        "restored_record_ids_sha256": _ordered_ids_digest(
            schema_version=LABEL_REPAIR_SCHEMA_VERSION,
            record_ids=result.restored_record_ids,
        ),
        "control_slot_sha256": _control_slot_sha256(slot),
    }
    for field_name, expected in expected_digests.items():
        if getattr(provenance, field_name) != expected:
            _fail(f"repair provenance {field_name} does not match the recomputed digest")

    attested: dict[str, str] = {
        "attested_feature_matrix_sha256": source.attested_feature_matrix_sha256,
        "attested_preprocessing_specification_sha256": (
            source.attested_preprocessing_specification_sha256
        ),
        "attested_model_specification_sha256": source.attested_model_specification_sha256,
    }
    for field_name, declared in attested.items():
        if getattr(provenance, field_name) != declared:
            _fail(f"repair provenance {field_name} is not the value the clean source attested")

    return result


# --------------------------------------------------------------------------- #
# 2. Benign target-serialization round-trip control
# --------------------------------------------------------------------------- #


class SemanticTargetSource(_StrictFrozenModel):
    """Training targets in their semantic form, before any encoding.

    The round-trip control operates on the labels a dataset actually stores, so
    it takes ``"Yes"`` and ``"No"`` rather than codes. Taking codes would make
    the control trivially true and would prove nothing about the serialization
    path it is supposed to exercise.
    """

    schema_version: Literal["p2-semantic-target-source/v1"]
    split: LabelNoiseScope
    record_ids: tuple[str, ...]
    targets: tuple[str, ...]
    attested_feature_matrix_sha256: Sha256
    attested_preprocessing_specification_sha256: Sha256
    attested_model_specification_sha256: Sha256

    @model_validator(mode="after")
    def _source_is_well_formed(self) -> SemanticTargetSource:
        _check_record_ids(self.record_ids)
        if len(self.record_ids) != len(self.targets):
            raise ValueError(
                "record_ids and targets must align: "
                f"{len(self.record_ids)} ids against {len(self.targets)} targets"
            )
        for position, label in enumerate(self.targets):
            if label not in SEMANTIC_TO_CODE:
                raise ValueError(
                    f"target at position {position} is outside the declared semantic "
                    f"vocabulary: {label!r}"
                )
        return self

    @property
    def record_count(self) -> int:
        return len(self.record_ids)

    def record_ids_sha256(self) -> str:
        """Digest the identifier sequence, order included."""

        return _ordered_ids_digest(schema_version=self.schema_version, record_ids=self.record_ids)

    def membership_sha256(self) -> str:
        """Digest which records exist, order excluded."""

        return _membership_digest(schema_version=self.schema_version, record_ids=self.record_ids)

    def targets_sha256(self) -> str:
        """Digest the semantically-labelled record set, order excluded."""

        return _semantic_records_digest(
            schema_version=self.schema_version,
            record_ids=self.record_ids,
            labels=self.targets,
        )


class SerializationControlSpec(_StrictFrozenModel):
    """The predeclared parameters of the semantics-preserving control.

    The zero flip rate here is not a corruption of size zero. It is the frozen
    plan's way of recording that this slot performs no label change at all, and
    the validator refuses any positive rate so the control can never be produced
    by running the corruption injector and renaming its output.
    """

    schema_version: Literal["p2-target-serialization-spec/v1"] = "p2-target-serialization-spec/v1"
    parameters: LabelNoiseParameters
    seed: int = Field(ge=0)

    @model_validator(mode="after")
    def _spec_preserves_semantics(self) -> SerializationControlSpec:
        parameters = self.parameters
        if parameters.flip_rate != 0.0:
            raise ValueError(
                "the serialization control changes no label; a positive flip rate belongs "
                "to the corruption mechanism"
            )
        if parameters.flip_direction != "symmetric":
            raise ValueError("the alpha slice declares symmetric parameters for this slot")
        if parameters.selection_policy != "seeded_record_hash":
            raise ValueError("the alpha slice declares seeded record-hash parameters")
        if parameters.scope != "train":
            raise ValueError("the serialization control operates on the training split")
        if self.seed != SERIALIZATION_SEED:
            raise ValueError("the frozen serialization control requires seed 205")
        return self


class SerializationRoundTripProvenance(_StrictFrozenModel):
    """Everything an evaluator needs to reproduce and audit one round trip.

    ``attested_*`` fields are copied and cross-bound to the validated semantic
    source. They are not recomputed from raw feature, split or model data.
    """

    schema_version: Literal["p2-target-serialization-roundtrip/v1"]
    intervention_type: Literal["target_label_serialization_roundtrip"]
    roundtrip_protocol_version: Literal["target-label-serialization-roundtrip/v1"]
    codec_version: Literal["target-label-codec/v1"]
    codec_sha256: Sha256
    seed: int = Field(ge=0)
    declared_flip_rate: float = Field(ge=0.0, le=0.0)
    record_count: int = Field(ge=1)
    original_record_ids_sha256: Sha256
    decoded_record_ids_sha256: Sha256
    original_membership_sha256: Sha256
    decoded_membership_sha256: Sha256
    original_targets_sha256: Sha256
    decoded_targets_sha256: Sha256
    control_slot_sha256: Sha256
    attested_feature_matrix_sha256: Sha256
    attested_preprocessing_specification_sha256: Sha256
    attested_model_specification_sha256: Sha256

    @model_validator(mode="after")
    def _roundtrip_preserved_everything(self) -> SerializationRoundTripProvenance:
        if self.declared_flip_rate != 0.0:
            raise ValueError("the serialization control declares a zero flip rate")
        if self.codec_sha256 != codec_sha256():
            raise ValueError("codec_sha256 does not match the pinned codec table")
        if self.original_record_ids_sha256 != self.decoded_record_ids_sha256:
            raise ValueError("a semantics-preserving round trip must not reorder records")
        if self.original_membership_sha256 != self.decoded_membership_sha256:
            raise ValueError("a semantics-preserving round trip must not change membership")
        if self.original_targets_sha256 != self.decoded_targets_sha256:
            raise ValueError("a semantics-preserving round trip must not change any label")
        return self


class SerializationRoundTripResult(_StrictFrozenModel):
    """A completed round trip: the codes produced and the labels recovered.

    No field records whether the round trip succeeded. Success is the return
    value of :func:`validate_serialization_roundtrip`, which recomputes the
    encoding and the decoding from the source rather than reading a flag.
    """

    schema_version: Literal["p2-target-serialization-roundtrip-result/v1"] = (
        "p2-target-serialization-roundtrip-result/v1"
    )
    record_ids: tuple[str, ...]
    original_targets: tuple[str, ...]
    encoded_codes: tuple[int, ...]
    decoded_targets: tuple[str, ...]
    provenance: SerializationRoundTripProvenance

    @model_validator(mode="after")
    def _result_is_internally_aligned(self) -> SerializationRoundTripResult:
        _check_record_ids(self.record_ids)
        count = len(self.record_ids)
        if not (
            count
            == len(self.original_targets)
            == len(self.encoded_codes)
            == len(self.decoded_targets)
        ):
            raise ValueError("identifiers, original targets, codes and decoded targets must align")
        if count != self.provenance.record_count:
            raise ValueError("result record count must match its provenance")
        for position, (original, code, decoded) in enumerate(
            zip(self.original_targets, self.encoded_codes, self.decoded_targets, strict=True)
        ):
            if SEMANTIC_TO_CODE.get(original) != code:
                raise ValueError(f"code at position {position} is not the encoding of its label")
            if CODE_TO_SEMANTIC.get(code) != decoded:
                raise ValueError(f"label at position {position} is not the decoding of its code")
            if decoded != original:
                raise ValueError(f"the round trip changed the label at position {position}")
        return self

    def artifact_sha256(self) -> str:
        """Digest every serialized field, including row ordering.

        No semantic digest accompanies this one. The control asserts that
        ordering and membership are untouched, so an order-insensitive identity
        would have nothing left to distinguish.
        """

        return canonical_sha256(
            {
                "digest_schema_version": SERIALIZATION_ARTIFACT_DIGEST_SCHEMA_VERSION,
                "artifact": self.model_dump(mode="json"),
            }
        )


def apply_serialization_roundtrip(
    *, source: SemanticTargetSource, spec: SerializationControlSpec, slot: CandidateSlot
) -> SerializationRoundTripResult:
    """Encode the semantic targets and decode them back, touching nothing else.

    No corruption injector is involved and no record is selected: every record
    takes the same path, which is what makes the intervention semantics
    preserving by construction rather than by measurement.
    """

    source = _revalidated(source)
    spec = _revalidated(spec)
    slot = _validate_control_slot(
        slot,
        expected_slot_id=SERIALIZATION_SLOT_ID,
        expected_role="designed_benign_control",
        expected_intervention_type=SERIALIZATION_INTERVENTION_TYPE,
        expected_seed=SERIALIZATION_SEED,
        expected_flip_rate=0.0,
        attested_preprocessing_specification_sha256=(
            source.attested_preprocessing_specification_sha256
        ),
        attested_model_specification_sha256=source.attested_model_specification_sha256,
    )
    if spec.seed != slot.identity.seed or spec.parameters != (
        slot.identity.canonical_intervention_parameters
    ):
        _fail("serialization specification differs from the frozen M2-B1 identity")

    encoded = tuple(encode_target_label(label) for label in source.targets)
    decoded = tuple(decode_target_code(code) for code in encoded)

    if decoded != source.targets:
        _fail("the codec failed to recover the original semantic targets")

    decoded_ids_digest = _ordered_ids_digest(
        schema_version=source.schema_version, record_ids=source.record_ids
    )
    provenance = SerializationRoundTripProvenance(
        schema_version=SERIALIZATION_ROUNDTRIP_SCHEMA_VERSION,
        intervention_type=SERIALIZATION_INTERVENTION_TYPE,
        roundtrip_protocol_version=SERIALIZATION_PROTOCOL_VERSION,
        codec_version=TARGET_CODEC_VERSION,
        codec_sha256=codec_sha256(),
        seed=spec.seed,
        declared_flip_rate=spec.parameters.flip_rate,
        record_count=source.record_count,
        original_record_ids_sha256=source.record_ids_sha256(),
        decoded_record_ids_sha256=decoded_ids_digest,
        original_membership_sha256=source.membership_sha256(),
        decoded_membership_sha256=_membership_digest(
            schema_version=source.schema_version, record_ids=source.record_ids
        ),
        original_targets_sha256=source.targets_sha256(),
        decoded_targets_sha256=_semantic_records_digest(
            schema_version=source.schema_version,
            record_ids=source.record_ids,
            labels=decoded,
        ),
        control_slot_sha256=_control_slot_sha256(slot),
        attested_feature_matrix_sha256=source.attested_feature_matrix_sha256,
        attested_preprocessing_specification_sha256=(
            source.attested_preprocessing_specification_sha256
        ),
        attested_model_specification_sha256=source.attested_model_specification_sha256,
    )
    return SerializationRoundTripResult(
        record_ids=source.record_ids,
        original_targets=source.targets,
        encoded_codes=encoded,
        decoded_targets=decoded,
        provenance=provenance,
    )


def validate_serialization_roundtrip(
    result: SerializationRoundTripResult,
    *,
    source: SemanticTargetSource,
    spec: SerializationControlSpec,
    slot: CandidateSlot,
) -> SerializationRoundTripResult:
    """Recompute the round trip from the source and reject any divergence.

    This covers the construction tier of the equivalence contract: decoded-label
    equality, membership equality, row-order equality and target-digest
    equality. The post-execution tier lives in
    :func:`verify_prediction_equivalence`.
    """

    result = _revalidated(result)
    source = _revalidated(source)
    spec = _revalidated(spec)
    slot = _validate_control_slot(
        slot,
        expected_slot_id=SERIALIZATION_SLOT_ID,
        expected_role="designed_benign_control",
        expected_intervention_type=SERIALIZATION_INTERVENTION_TYPE,
        expected_seed=SERIALIZATION_SEED,
        expected_flip_rate=0.0,
        attested_preprocessing_specification_sha256=(
            source.attested_preprocessing_specification_sha256
        ),
        attested_model_specification_sha256=source.attested_model_specification_sha256,
    )
    if spec.seed != slot.identity.seed or spec.parameters != (
        slot.identity.canonical_intervention_parameters
    ):
        _fail("serialization specification differs from the frozen M2-B1 identity")
    provenance = result.provenance

    if result.record_ids != source.record_ids:
        _fail("round-trip identifiers must match the source exactly, order included")
    if set(result.record_ids) != set(source.record_ids):
        _fail("round-trip membership differs from the source")
    if result.original_targets != source.targets:
        _fail("round-trip original targets differ from the source targets")

    expected_codes = tuple(encode_target_label(label) for label in source.targets)
    if result.encoded_codes != expected_codes:
        _fail("encoded codes do not match the pinned codec applied to the source targets")
    expected_decoded = tuple(decode_target_code(code) for code in expected_codes)
    if result.decoded_targets != expected_decoded:
        _fail("decoded targets do not match the pinned codec inverse")
    if result.decoded_targets != source.targets:
        _fail("the round trip did not recover the original semantic targets")

    if provenance.seed != spec.seed:
        _fail("round-trip provenance seed differs from the control specification")
    if provenance.declared_flip_rate != spec.parameters.flip_rate:
        _fail("round-trip provenance flip rate differs from the control specification")
    if provenance.record_count != source.record_count:
        _fail("round-trip provenance record count differs from the source")

    decoded_targets_digest = _semantic_records_digest(
        schema_version=source.schema_version,
        record_ids=result.record_ids,
        labels=result.decoded_targets,
    )
    expected_digests: dict[str, str] = {
        "codec_sha256": codec_sha256(),
        "original_record_ids_sha256": source.record_ids_sha256(),
        "decoded_record_ids_sha256": _ordered_ids_digest(
            schema_version=source.schema_version, record_ids=result.record_ids
        ),
        "original_membership_sha256": source.membership_sha256(),
        "decoded_membership_sha256": _membership_digest(
            schema_version=source.schema_version, record_ids=result.record_ids
        ),
        "original_targets_sha256": source.targets_sha256(),
        "decoded_targets_sha256": decoded_targets_digest,
        "control_slot_sha256": _control_slot_sha256(slot),
    }
    for field_name, expected in expected_digests.items():
        if getattr(provenance, field_name) != expected:
            _fail(f"round-trip provenance {field_name} does not match the recomputed digest")

    attested: dict[str, str] = {
        "attested_feature_matrix_sha256": source.attested_feature_matrix_sha256,
        "attested_preprocessing_specification_sha256": (
            source.attested_preprocessing_specification_sha256
        ),
        "attested_model_specification_sha256": source.attested_model_specification_sha256,
    }
    for field_name, declared in attested.items():
        if getattr(provenance, field_name) != declared:
            _fail(f"round-trip provenance {field_name} is not the value the source attested")

    return result


# --------------------------------------------------------------------------- #
# 3. Post-execution prediction equivalence
# --------------------------------------------------------------------------- #


class PredictionEvaluationSource(_StrictFrozenModel):
    """Frozen clean-test rows against which both control runs are compared."""

    schema_version: Literal["p2-prediction-evaluation-source/v1"]
    split: Literal["test"]
    record_ids: tuple[str, ...]
    true_labels: tuple[int, ...]
    attested_test_feature_matrix_sha256: Sha256
    attested_split_manifest_sha256: Sha256

    @model_validator(mode="after")
    def _test_rows_are_well_formed(self) -> PredictionEvaluationSource:
        _check_record_ids(self.record_ids)
        if len(self.record_ids) != len(self.true_labels):
            raise ValueError("clean-test identifiers and true labels must align")
        for position, label in enumerate(self.true_labels):
            if label not in BINARY_LABELS:
                raise ValueError(f"true label at position {position} must be binary 0 or 1")
        return self

    def artifact_sha256(self) -> str:
        """Bind exact test ordering, labels and external split attestations."""

        return canonical_sha256(
            {
                "schema_version": self.schema_version,
                "source": self.model_dump(mode="json"),
            }
        )


class PredictionEquivalenceEvidence(_StrictFrozenModel):
    """Real prediction vectors from a reference run and a round-trip run.

    Every field is measured data. There is no verdict field, no pass flag and no
    metric: this model is the input to a comparison, never its conclusion.
    """

    schema_version: Literal["p2-prediction-equivalence-evidence/v1"]
    roundtrip_artifact_sha256: Sha256
    evaluation_source_sha256: Sha256
    record_ids: tuple[str, ...]
    true_labels: tuple[int, ...]
    reference_predictions: tuple[int, ...]
    roundtrip_predictions: tuple[int, ...]

    @model_validator(mode="after")
    def _vectors_align(self) -> PredictionEquivalenceEvidence:
        _check_record_ids(self.record_ids)
        count = len(self.record_ids)
        if not (
            count
            == len(self.true_labels)
            == len(self.reference_predictions)
            == len(self.roundtrip_predictions)
        ):
            raise ValueError("every prediction vector must have one entry per record")
        vectors = {
            "true_labels": self.true_labels,
            "reference_predictions": self.reference_predictions,
            "roundtrip_predictions": self.roundtrip_predictions,
        }
        for name, vector in vectors.items():
            for position, value in enumerate(vector):
                if value not in BINARY_LABELS:
                    raise ValueError(f"{name}[{position}] must be binary 0 or 1, got {value!r}")
        return self

    def evidence_sha256(self) -> str:
        """Digest the evidence with its row ordering included."""

        return canonical_sha256(
            {
                "schema_version": self.schema_version,
                "evidence": self.model_dump(mode="json"),
            }
        )


#: The three states a recomputed prediction comparison can reach. None of them
#: is a benign verdict: that decision needs the authoritative guardrail report.
PredictionEquivalenceVerdict = Literal["prediction_invariance_verified", "prediction_divergence"]


class PredictionEquivalenceReport(_StrictFrozenModel):
    """The recomputed comparison of two prediction vectors.

    ``verdict`` is derived from ``diverging_record_count`` by a validator, so a
    caller who edits one without the other is rejected rather than believed.
    """

    schema_version: Literal["p2-prediction-equivalence-report/v1"]
    verdict: PredictionEquivalenceVerdict
    compared_record_count: int = Field(ge=1)
    diverging_record_count: int = Field(ge=0)
    evidence_sha256: Sha256
    roundtrip_artifact_sha256: Sha256
    evaluation_source_sha256: Sha256

    @model_validator(mode="after")
    def _verdict_is_derived(self) -> PredictionEquivalenceReport:
        if self.diverging_record_count > self.compared_record_count:
            raise ValueError("diverging records cannot exceed compared records")
        expected = (
            "prediction_invariance_verified"
            if self.diverging_record_count == 0
            else "prediction_divergence"
        )
        if self.verdict != expected:
            raise ValueError(
                f"verdict must be derived from the divergence count; expected {expected!r}"
            )
        return self


def verify_prediction_equivalence(
    evidence: PredictionEquivalenceEvidence,
    *,
    result: SerializationRoundTripResult,
    source: SemanticTargetSource,
    spec: SerializationControlSpec,
    slot: CandidateSlot,
    evaluation_source: PredictionEvaluationSource,
) -> PredictionEquivalenceReport:
    """Compare two real prediction vectors element by element.

    The evidence is bound to the round-trip artifact it describes, so a vector
    pair measured on a different construction cannot be presented as evidence
    for this one.

    Prediction invariance is structurally verified from exact vectors. Canonical
    macro-F1, minority-recall and confusion reporting remains the responsibility
    of the later authoritative alpha evaluator; this function does not compute,
    and must not be read as computing, any guardrail metric.
    """

    evidence = _revalidated(evidence)
    result = validate_serialization_roundtrip(result, source=source, spec=spec, slot=slot)
    evaluation_source = _revalidated(evaluation_source)

    artifact_digest = result.artifact_sha256()
    if evidence.roundtrip_artifact_sha256 != artifact_digest:
        _fail("prediction evidence is not bound to this round-trip artifact")
    evaluation_digest = evaluation_source.artifact_sha256()
    if evidence.evaluation_source_sha256 != evaluation_digest:
        _fail("prediction evidence is not bound to this clean-test evaluation source")
    if evidence.record_ids != evaluation_source.record_ids:
        _fail("prediction evidence identifiers must match the clean-test source, order included")
    if evidence.true_labels != evaluation_source.true_labels:
        _fail("prediction evidence true labels differ from the clean-test source")

    diverging = sum(
        1
        for reference, roundtrip in zip(
            evidence.reference_predictions, evidence.roundtrip_predictions, strict=True
        )
        if reference != roundtrip
    )
    return PredictionEquivalenceReport(
        schema_version=PREDICTION_REPORT_SCHEMA_VERSION,
        verdict=("prediction_invariance_verified" if diverging == 0 else "prediction_divergence"),
        compared_record_count=len(evidence.record_ids),
        diverging_record_count=diverging,
        evidence_sha256=evidence.evidence_sha256(),
        roundtrip_artifact_sha256=artifact_digest,
        evaluation_source_sha256=evaluation_digest,
    )


#: What a benign control has actually established so far.
#:
#: ``benign_control`` is deliberately absent. Reaching it requires an
#: authoritative guardrail record this module does not produce, so the strongest
#: state expressible here stops one step short and says so.
BenignControlReadiness = Literal[
    "pending_post_execution_equivalence",
    "structural_equivalence_verified_pending_guardrail_report",
    "benign_equivalence_failure",
]


def benign_control_readiness(
    *,
    result: SerializationRoundTripResult,
    source: SemanticTargetSource,
    spec: SerializationControlSpec,
    slot: CandidateSlot,
    evaluation_source: PredictionEvaluationSource | None = None,
    prediction_evidence: PredictionEquivalenceEvidence | None = None,
) -> BenignControlReadiness:
    """Report how far the benign control has been verified, and no further.

    A missing prediction report is not a failure and not a pass: it is the
    honest statement that the post-execution tier has not been measured yet.
    Divergence maps to ``benign_equivalence_failure``, which is the rejection
    reason the contract already defines; it is never relabelled as stable.
    """

    validate_serialization_roundtrip(result, source=source, spec=spec, slot=slot)

    if evaluation_source is None and prediction_evidence is None:
        return "pending_post_execution_equivalence"
    if evaluation_source is None or prediction_evidence is None:
        _fail("prediction evidence and its clean-test source must be supplied together")
    prediction_report = verify_prediction_equivalence(
        prediction_evidence,
        result=result,
        source=source,
        spec=spec,
        slot=slot,
        evaluation_source=evaluation_source,
    )
    if prediction_report.verdict == "prediction_divergence":
        return "benign_equivalence_failure"
    return "structural_equivalence_verified_pending_guardrail_report"
