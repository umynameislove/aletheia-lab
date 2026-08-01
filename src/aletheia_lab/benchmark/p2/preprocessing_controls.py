"""Predeclared controls for the inference preprocessing mechanism.

Two controls live here, and neither is a fault.

**The mapping-repair control** builds a predeclared mismatched inference
reference of its own, then restores the fitted training mapping. It exists to
show that the measured effect tracks the encoder mapping rather than an
unrelated property of the pipeline. The module proves the restoration is exact;
it never calls the restoration an improvement, because whether it improved
anything is a question about predictions this module does not make.

**The column-permutation control** changes the physical column order of the
input frame in front of a name-bound transformer, and changes nothing a
name-bound transformer can see. It exists to show that touching the input layout
without touching semantics leaves the experiment alone.

Four boundaries are enforced by construction rather than by discipline.

**Semantics are bound to names, not to positions.** A table carries an ordered
tuple of feature names and, per record, an ordered tuple of values aligned to
them. The semantic digest sorts on the name, so swapping two values between two
names changes it even though the multiset of values is unchanged. Comparing two
lists of column names would not catch that.

**No self-declared conclusions.** There is no ``equivalence_passed``,
``benign`` or ``guardrails_passed`` field anywhere. Every conclusion is the
return value of a function that recomputed it from vectors, matrices and
digests.

**No family class.** Nothing here produces ``eligible_failure``,
``stable_control``, ``improvement_control``, ``benign_control``, an accepted
family, a case family ID or an admission record. The strongest state the benign
control reaches is equivalence verified pending admission.

**No guardrail verdict.** The macro-F1 and minority-recall harm thresholds for
this mechanism are still alpha-provisional, so a fault-directed or
improvement-control measurement reports its deltas and stops at
``validity_review_required``.

Two error kinds are raised, matching the convention of the other Phase 2
modules:

* a malformed *object* raises :class:`pydantic.ValidationError`;
* a malformed *relationship between objects* raises
  :class:`PreprocessingControlError`.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Sequence
from typing import Annotated, Final, Literal, NoReturn, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.binary_evaluation import (
    CleanTestSet,
    MetricComparison,
    PredictionVector,
    compare_binary_metrics,
    metric_snapshot,
    validate_metric_comparison,
)
from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import CandidateSlot
from aletheia_lab.benchmark.p2.identity import (
    SHA256_PATTERN,
    PreprocessingBugParameters,
)
from aletheia_lab.benchmark.p2.preprocessing_intervention import (
    ALPHA_PREPROCESSING_MODE,
    ALPHA_RANK_COUNT,
    ALPHA_TARGET_FEATURE,
    ALPHA_TRANSFORM_NAME,
    CATEGORY_RANK_RULE,
    InferenceTransformSource,
)
from aletheia_lab.benchmark.p2.validation import ContractViolation, validate_frozen_alpha_slot

# --------------------------------------------------------------------------- #
# Schema and protocol versions
# --------------------------------------------------------------------------- #

ENCODER_REPAIR_SPEC_SCHEMA_VERSION: Final[Literal["p2-encoder-mapping-repair-spec/v1"]] = (
    "p2-encoder-mapping-repair-spec/v1"
)
ENCODER_REPAIR_PROVENANCE_SCHEMA_VERSION: Final[Literal["p2-encoder-mapping-repair/v1"]] = (
    "p2-encoder-mapping-repair/v1"
)
ENCODER_REPAIR_RESULT_SCHEMA_VERSION: Final[Literal["p2-encoder-mapping-repair-result/v1"]] = (
    "p2-encoder-mapping-repair-result/v1"
)
ENCODER_REPAIR_ARTIFACT_DIGEST_SCHEMA_VERSION: Final[
    Literal["p2-encoder-mapping-repair-artifact-digest/v1"]
] = "p2-encoder-mapping-repair-artifact-digest/v1"
ENCODER_REPAIR_MEASUREMENT_SCHEMA_VERSION: Final[Literal["p2-repair-control-measurement/v1"]] = (
    "p2-repair-control-measurement/v1"
)
NAMED_FEATURE_TABLE_SCHEMA_VERSION: Final[Literal["p2-named-feature-table/v1"]] = (
    "p2-named-feature-table/v1"
)
PERMUTATION_SPEC_SCHEMA_VERSION: Final[Literal["p2-column-permutation-spec/v1"]] = (
    "p2-column-permutation-spec/v1"
)
PERMUTATION_PROVENANCE_SCHEMA_VERSION: Final[Literal["p2-column-permutation/v1"]] = (
    "p2-column-permutation/v1"
)
PERMUTATION_RESULT_SCHEMA_VERSION: Final[Literal["p2-column-permutation-result/v1"]] = (
    "p2-column-permutation-result/v1"
)
PERMUTATION_ARTIFACT_DIGEST_SCHEMA_VERSION: Final[
    Literal["p2-column-permutation-artifact-digest/v1"]
] = "p2-column-permutation-artifact-digest/v1"
TRANSFORMED_MATRIX_SCHEMA_VERSION: Final[Literal["p2-transformed-matrix/v1"]] = (
    "p2-transformed-matrix/v1"
)
BENIGN_EVIDENCE_SCHEMA_VERSION: Final[Literal["p2-benign-equivalence-evidence/v1"]] = (
    "p2-benign-equivalence-evidence/v1"
)
PREPROCESSING_CONTROL_SLOT_BINDING_SCHEMA_VERSION: Final[
    Literal["p2-preprocessing-control-slot-binding/v1"]
] = "p2-preprocessing-control-slot-binding/v1"
PERMUTATION_ORDER_SCHEMA_VERSION: Final[Literal["p2-column-permutation-order/v1"]] = (
    "p2-column-permutation-order/v1"
)

#: Pinned protocol identifiers, ``Literal`` rather than free text.
ENCODER_REPAIR_PROTOCOL_VERSION: Final[Literal["inference-encoder-mapping-repair/v1"]] = (
    "inference-encoder-mapping-repair/v1"
)
PERMUTATION_PROTOCOL_VERSION: Final[Literal["name-bound-column-order-permutation/v1"]] = (
    "name-bound-column-order-permutation/v1"
)

#: The intervention types the two control entry points accept. Each is checked
#: independently of the slot role, so a slot that carries the wrong pair is
#: rejected twice rather than once.
ENCODER_REPAIR_INTERVENTION_TYPE: Final[Literal["inference_encoder_mapping_repair"]] = (
    "inference_encoder_mapping_repair"
)
PERMUTATION_INTERVENTION_TYPE: Final[Literal["name_bound_column_order_permutation"]] = (
    "name_bound_column_order_permutation"
)

#: The status a fault-directed or improvement-control measurement can reach.
#:
#: Neither value is an eligibility decision. ``validity_review_required`` says
#: the measurement is complete and the guardrail policy is not frozen yet;
#: ``control_direction_violation`` says an improvement control moved the wrong
#: way, which the contract already treats as an exclusion reason rather than a
#: reason to re-run with a different seed.
RepairControlStatus = Literal["validity_review_required", "control_direction_violation"]

#: How far the benign control has been verified, and no further.
#:
#: ``benign_control`` is deliberately absent: admission belongs to a later layer,
#: so the strongest state expressible here stops one step short and says so.
BenignEquivalenceStatus = Literal[
    "pending_post_execution_equivalence",
    "equivalence_verified_pending_admission",
    "benign_equivalence_failure",
]

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
InjectionId = Annotated[str, Field(pattern=r"^M[123]-(?:F|S|I|B|R)[0-9]{1,2}$")]

_MAX_RECORDS: Final[int] = 5_000_000
_MAX_COLUMNS: Final[int] = 4_096
_FLOAT_TOLERANCE: Final[float] = 1e-12
_MIN_PERMUTABLE_COLUMNS: Final[int] = 2

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class PreprocessingControlError(ContractViolation):
    """Raised when preprocessing-control artifacts disagree with one another."""


def _fail(message: str) -> NoReturn:
    """Reject a cross-object relationship."""

    raise PreprocessingControlError(message)


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


def _require_canonical_text(value: str, *, label: str) -> str:
    """Reject text that would make two spellings of one name hash differently."""

    if not value:
        raise ValueError(f"{label} must not be empty")
    if value != value.strip():
        raise ValueError(f"{label} must not have leading or trailing whitespace: {value!r}")
    if value != unicodedata.normalize("NFC", value):
        raise ValueError(f"{label} must already be Unicode NFC: {value!r}")
    if any(character.isspace() and character != " " for character in value):
        raise ValueError(f"{label} must not contain tabs or newlines: {value!r}")
    return value


def _check_record_ids(record_ids: Sequence[str]) -> None:
    """Reject anything that cannot anchor a per-record artifact."""

    if not record_ids:
        raise ValueError("a control table must contain at least one record")
    if len(record_ids) > _MAX_RECORDS:
        raise ValueError("a control table exceeds the supported record count")
    seen: set[str] = set()
    for position, record_id in enumerate(record_ids):
        _require_canonical_text(record_id, label=f"record ID at position {position}")
        if record_id in seen:
            raise ValueError("record IDs must be unique; duplicates cannot anchor a control")
        seen.add(record_id)


def _ordered_digest(*, schema_version: str, key: str, values: Sequence[str]) -> str:
    """Digest a string sequence with its order included."""

    return canonical_sha256({"schema_version": schema_version, key: list(values)})


def _block_digest(*, schema_version: str, key: str, block: Sequence[Sequence[int]]) -> str:
    """Digest an indicator matrix with row order and column order included."""

    return canonical_sha256({"schema_version": schema_version, key: [list(row) for row in block]})


def _validate_control_slot(
    slot: CandidateSlot,
    *,
    expected_role: Literal["designed_improvement_control", "designed_benign_control"],
    expected_intervention_type: str,
    injection_id: str,
    parameters: PreprocessingBugParameters,
    seed: int,
) -> CandidateSlot:
    """Bind one control operation to its frozen slot and family identity.

    Which ranks and seed belong to which slot identifier is decided by
    :func:`validate_frozen_alpha_slot`, the single authoritative grid. This
    function adds only the mechanism contract: preprocessing, the right control
    role, the right intervention type, and a specification the slot declares.
    """

    try:
        slot = validate_frozen_alpha_slot(slot)
    except ContractViolation as error:
        _fail(str(error))
    identity = slot.identity
    declared = identity.canonical_intervention_parameters

    if slot.fault_type != "preprocessing_bug" or identity.fault_type != "preprocessing_bug":
        _fail("the preprocessing controls accept preprocessing_bug slots only")
    if slot.role != expected_role:
        _fail(f"this control entry point accepts {expected_role!r} slots only; got {slot.role!r}")
    if identity.intervention_type != expected_intervention_type:
        _fail(
            f"this control entry point implements {expected_intervention_type!r} only; "
            f"got {identity.intervention_type!r}"
        )
    if slot.slot_id != injection_id:
        _fail("the specification injection_id differs from the frozen slot identifier")
    if not isinstance(declared, PreprocessingBugParameters):
        _fail("a preprocessing_bug slot must carry preprocessing parameters")
    if declared != parameters:
        _fail("the specification differs from the parameters the slot declares")
    if identity.seed != seed:
        _fail("the specification seed differs from the seed the slot declares")
    return slot


def _control_slot_sha256(slot: CandidateSlot) -> str:
    """Bind the complete frozen slot, including its twelve-field family identity."""

    slot = _revalidated(slot)
    return canonical_sha256(
        {
            "schema_version": PREPROCESSING_CONTROL_SLOT_BINDING_SCHEMA_VERSION,
            "slot": slot.model_dump(mode="json"),
        }
    )


# --------------------------------------------------------------------------- #
# 1. Paired encoder mapping repair control
# --------------------------------------------------------------------------- #


class EncoderMappingRepairSpec(_StrictFrozenModel):
    """One prespecified repair: which ranks the reference mismatched, which seed."""

    schema_version: Literal["p2-encoder-mapping-repair-spec/v1"] = (
        "p2-encoder-mapping-repair-spec/v1"
    )
    injection_id: InjectionId
    parameters: PreprocessingBugParameters
    source_category: str = Field(min_length=1, max_length=256)
    mapped_category: str = Field(min_length=1, max_length=256)
    seed: int = Field(ge=0)

    @model_validator(mode="after")
    def _spec_describes_the_alpha_repair(self) -> EncoderMappingRepairSpec:
        parameters = self.parameters
        _require_canonical_text(self.source_category, label="source category")
        _require_canonical_text(self.mapped_category, label="mapped category")
        if self.source_category == self.mapped_category:
            raise ValueError("source_category and mapped_category must differ")
        if parameters.target_feature != ALPHA_TARGET_FEATURE:
            raise ValueError(f"the alpha preprocessing slice targets {ALPHA_TARGET_FEATURE!r} only")
        if parameters.mode != ALPHA_PREPROCESSING_MODE:
            raise ValueError(
                "the repair control restores the inference transform only; "
                f"mode must be {ALPHA_PREPROCESSING_MODE!r}"
            )
        if parameters.transform_name != ALPHA_TRANSFORM_NAME:
            raise ValueError(
                f"the alpha repair control requires transform {ALPHA_TRANSFORM_NAME!r}"
            )
        if parameters.source_rank is None or parameters.mapped_rank is None:
            raise ValueError("a repair control names the reference mismatch by both ranks")
        for name, rank in (
            ("source_rank", parameters.source_rank),
            ("mapped_rank", parameters.mapped_rank),
        ):
            if rank > ALPHA_RANK_COUNT:
                raise ValueError(
                    f"{name} must address one of the first {ALPHA_RANK_COUNT} ranks, got {rank}"
                )
        return self

    @property
    def source_rank(self) -> int:
        """The rank the reference mismatch reads from."""

        rank = self.parameters.source_rank
        if rank is None:
            _fail("the specification is missing its source rank")
        return rank

    @property
    def mapped_rank(self) -> int:
        """The rank the reference mismatch writes as."""

        rank = self.parameters.mapped_rank
        if rank is None:
            _fail("the specification is missing its mapped rank")
        return rank


class EncoderMappingRepairProvenance(_StrictFrozenModel):
    """Everything an evaluator needs to reproduce and audit one repair.

    ``attested_*`` fields are copied and cross-bound to the validated inference
    source. They are not recomputed from raw feature, target, model or fitted
    training-transform data, because this module never receives those artifacts.
    """

    schema_version: Literal["p2-encoder-mapping-repair/v1"]
    intervention_type: Literal["inference_encoder_mapping_repair"]
    repair_protocol_version: Literal["inference-encoder-mapping-repair/v1"]
    injection_id: InjectionId
    mode: Literal["inference_only"]
    rank_rule: Literal["count-desc-then-lexical/v1"]
    target_feature: str = Field(min_length=1, max_length=128)
    transform_name: str = Field(min_length=1, max_length=128)
    source_rank: int = Field(ge=1, le=ALPHA_RANK_COUNT)
    mapped_rank: int = Field(ge=1, le=ALPHA_RANK_COUNT)
    source_category: str = Field(min_length=1, max_length=256)
    mapped_category: str = Field(min_length=1, max_length=256)
    seed: int = Field(ge=0)
    record_count: int = Field(ge=1)
    restored_record_count: int = Field(ge=1)
    restored_ratio: float = Field(gt=0.0, le=1.0)
    column_count: int = Field(ge=ALPHA_RANK_COUNT)
    vocabulary_sha256: Sha256
    encoder_column_order_sha256: Sha256
    source_record_ids_sha256: Sha256
    source_membership_sha256: Sha256
    raw_categories_sha256: Sha256
    mismatched_reference_view_sha256: Sha256
    clean_block_sha256: Sha256
    mismatched_reference_block_sha256: Sha256
    repaired_block_sha256: Sha256
    restored_record_ids_sha256: Sha256
    control_slot_sha256: Sha256
    attested_raw_feature_matrix_sha256: Sha256
    attested_raw_target_sha256: Sha256
    attested_model_sha256: Sha256
    attested_fitted_training_transform_sha256: Sha256
    attested_other_transform_config_sha256: Sha256

    @model_validator(mode="after")
    def _counts_and_digests_agree(self) -> EncoderMappingRepairProvenance:
        _require_canonical_text(self.source_category, label="source category")
        _require_canonical_text(self.mapped_category, label="mapped category")
        if self.source_rank == self.mapped_rank:
            raise ValueError("source_rank and mapped_rank must differ")
        if self.source_category == self.mapped_category:
            raise ValueError("source_category and mapped_category must differ")
        if self.restored_record_count > self.record_count:
            raise ValueError("restored records cannot exceed the record count")
        expected_ratio = self.restored_record_count / self.record_count
        if abs(self.restored_ratio - expected_ratio) > _FLOAT_TOLERANCE:
            raise ValueError("restored_ratio must be derived from the two counts")
        if self.repaired_block_sha256 != self.clean_block_sha256:
            raise ValueError("a complete repair must reproduce the clean fitted-transform block")
        if self.mismatched_reference_block_sha256 == self.clean_block_sha256:
            raise ValueError(
                "the predeclared reference must actually differ from the clean block, "
                "or there is nothing to repair"
            )
        if self.mismatched_reference_view_sha256 == self.raw_categories_sha256:
            raise ValueError("the predeclared reference must change the inference view of some row")
        return self


class EncoderMappingRepairResult(_StrictFrozenModel):
    """A completed repair: the clean block, the mismatched reference and the restoration.

    The result describes what was restored. It carries no metric, measured
    outcome, eligibility, family class or cause; whether the repair helped is
    decided later, from predictions this module never makes.
    """

    schema_version: Literal["p2-encoder-mapping-repair-result/v1"] = (
        "p2-encoder-mapping-repair-result/v1"
    )
    record_ids: tuple[str, ...]
    raw_categories: tuple[str, ...]
    mismatched_reference_view: tuple[str, ...]
    repaired_view: tuple[str, ...]
    encoder_column_order: tuple[str, ...]
    clean_block: tuple[tuple[int, ...], ...]
    mismatched_reference_block: tuple[tuple[int, ...], ...]
    repaired_block: tuple[tuple[int, ...], ...]
    restored_record_ids: tuple[str, ...]
    provenance: EncoderMappingRepairProvenance

    @model_validator(mode="after")
    def _result_is_internally_aligned(self) -> EncoderMappingRepairResult:
        _check_record_ids(self.record_ids)
        count = len(self.record_ids)
        if not (
            count
            == len(self.raw_categories)
            == len(self.mismatched_reference_view)
            == len(self.repaired_view)
            == len(self.clean_block)
            == len(self.mismatched_reference_block)
            == len(self.repaired_block)
        ):
            raise ValueError("identifiers, category columns and all three blocks must align")
        if count != self.provenance.record_count:
            raise ValueError("result record count must match its provenance")

        width = len(self.encoder_column_order)
        if width != self.provenance.column_count:
            raise ValueError("result column count must match its provenance")
        if len(set(self.encoder_column_order)) != width:
            raise ValueError("encoder columns must be unique")
        if list(self.encoder_column_order) != sorted(self.encoder_column_order):
            raise ValueError("encoder_column_order must be in canonical sorted order")

        for name, block in (
            ("clean_block", self.clean_block),
            ("mismatched_reference_block", self.mismatched_reference_block),
            ("repaired_block", self.repaired_block),
        ):
            for position, row in enumerate(block):
                if len(row) != width:
                    raise ValueError(f"{name}[{position}] must have one column per category")
                if any(value not in (0, 1) for value in row):
                    raise ValueError(f"{name}[{position}] must contain indicator values only")
                if sum(row) != 1:
                    raise ValueError(f"{name}[{position}] must set exactly one indicator")

        if self.repaired_view != self.raw_categories:
            raise ValueError("a complete repair restores the raw categories exactly")
        if self.repaired_block != self.clean_block:
            raise ValueError("a complete repair reproduces the clean fitted-transform block")
        if self.mismatched_reference_block == self.clean_block:
            raise ValueError("the predeclared reference must differ from the clean block")

        if not self.restored_record_ids:
            raise ValueError("a repair must restore at least one record")
        if len(set(self.restored_record_ids)) != len(self.restored_record_ids):
            raise ValueError("a record may be restored at most once")
        if list(self.restored_record_ids) != sorted(self.restored_record_ids):
            raise ValueError("restored_record_ids must be in canonical sorted order")
        missing = set(self.restored_record_ids) - set(self.record_ids)
        if missing:
            raise ValueError(f"restored records must exist in the source: {sorted(missing)}")
        if len(self.restored_record_ids) != self.provenance.restored_record_count:
            raise ValueError("restored identifier count must match its provenance")
        return self

    def artifact_sha256(self) -> str:
        """Digest every serialized field, including row and column ordering.

        Order-sensitive by design: permuting rows or columns produces a
        different experiment. No semantic digest accompanies it, because no
        consumer needs one.
        """

        return canonical_sha256(
            {
                "digest_schema_version": ENCODER_REPAIR_ARTIFACT_DIGEST_SCHEMA_VERSION,
                "artifact": self.model_dump(mode="json"),
            }
        )


def _resolved_repair_categories(
    *, source: InferenceTransformSource, spec: EncoderMappingRepairSpec
) -> tuple[str, str]:
    """Resolve the declared ranks and check the spec names the same categories."""

    source_category = source.vocabulary.category_for_rank(spec.source_rank)
    mapped_category = source.vocabulary.category_for_rank(spec.mapped_rank)
    if source_category == mapped_category:
        _fail("the source and mapped ranks resolve to one category")
    if spec.source_category != source_category or spec.mapped_category != mapped_category:
        _fail("the specification names categories the frozen ranks do not resolve to")
    return source_category, mapped_category


def _mismatched_view(
    *, raw_categories: Sequence[str], source_category: str, mapped_category: str
) -> tuple[str, ...]:
    """Return the predeclared mismatched inference view."""

    return tuple(
        mapped_category if category == source_category else category for category in raw_categories
    )


def apply_encoder_mapping_repair(
    *,
    source: InferenceTransformSource,
    spec: EncoderMappingRepairSpec,
    slot: CandidateSlot,
) -> EncoderMappingRepairResult:
    """Build a predeclared mismatched reference and restore the fitted mapping.

    The caller's objects are never mutated. No encoder is refitted and no model
    is retrained, because neither is reachable from here.
    """

    source = _revalidated(source)
    spec = _revalidated(spec)
    slot = _validate_control_slot(
        slot,
        expected_role="designed_improvement_control",
        expected_intervention_type=ENCODER_REPAIR_INTERVENTION_TYPE,
        injection_id=spec.injection_id,
        parameters=spec.parameters,
        seed=spec.seed,
    )
    if spec.parameters.target_feature != source.feature:
        _fail("the specification targets a feature the source does not carry")

    source_category, mapped_category = _resolved_repair_categories(source=source, spec=spec)
    mismatched_view = _mismatched_view(
        raw_categories=source.raw_categories,
        source_category=source_category,
        mapped_category=mapped_category,
    )
    restored = tuple(
        sorted(
            record_id
            for record_id, category in zip(source.record_ids, source.raw_categories, strict=True)
            if category == source_category
        )
    )
    if not restored:
        _fail(
            "no evaluation record carries the source category, so the predeclared reference "
            "would be identical to the clean transform and there would be nothing to repair"
        )

    clean_block = source.indicator_block(source.raw_categories)
    mismatched_block = source.indicator_block(mismatched_view)
    repaired_view = source.raw_categories
    repaired_block = source.indicator_block(repaired_view)
    if mismatched_block == clean_block:
        _fail("the predeclared reference produces the clean encoding, so it is not a reference")
    if repaired_block != clean_block:
        _fail("the repair failed to reproduce the clean fitted-transform block")

    column_order = source.vocabulary.encoder_column_order()
    provenance = EncoderMappingRepairProvenance(
        schema_version=ENCODER_REPAIR_PROVENANCE_SCHEMA_VERSION,
        intervention_type=ENCODER_REPAIR_INTERVENTION_TYPE,
        repair_protocol_version=ENCODER_REPAIR_PROTOCOL_VERSION,
        injection_id=spec.injection_id,
        mode=ALPHA_PREPROCESSING_MODE,
        rank_rule=CATEGORY_RANK_RULE,
        target_feature=source.feature,
        transform_name=spec.parameters.transform_name,
        source_rank=spec.source_rank,
        mapped_rank=spec.mapped_rank,
        source_category=source_category,
        mapped_category=mapped_category,
        seed=spec.seed,
        record_count=source.record_count,
        restored_record_count=len(restored),
        restored_ratio=len(restored) / source.record_count,
        column_count=len(column_order),
        vocabulary_sha256=source.vocabulary.canonical_sha256(),
        encoder_column_order_sha256=_ordered_digest(
            schema_version=ENCODER_REPAIR_PROVENANCE_SCHEMA_VERSION,
            key="encoder_column_order",
            values=column_order,
        ),
        source_record_ids_sha256=source.record_ids_sha256(),
        source_membership_sha256=source.membership_sha256(),
        raw_categories_sha256=source.raw_categories_sha256(),
        mismatched_reference_view_sha256=_ordered_digest(
            schema_version=ENCODER_REPAIR_PROVENANCE_SCHEMA_VERSION,
            key="mismatched_reference_view",
            values=mismatched_view,
        ),
        clean_block_sha256=_block_digest(
            schema_version=ENCODER_REPAIR_PROVENANCE_SCHEMA_VERSION,
            key="clean_block",
            block=clean_block,
        ),
        mismatched_reference_block_sha256=_block_digest(
            schema_version=ENCODER_REPAIR_PROVENANCE_SCHEMA_VERSION,
            key="mismatched_reference_block",
            block=mismatched_block,
        ),
        # The domain key is deliberately the same as the clean block's. The
        # provenance validator asserts the two digests are equal, which is only
        # meaningful if both are computed over the same domain; a distinct key
        # would make the assertion unsatisfiable and therefore vacuous.
        repaired_block_sha256=_block_digest(
            schema_version=ENCODER_REPAIR_PROVENANCE_SCHEMA_VERSION,
            key="clean_block",
            block=repaired_block,
        ),
        restored_record_ids_sha256=_ordered_digest(
            schema_version=ENCODER_REPAIR_PROVENANCE_SCHEMA_VERSION,
            key="restored_record_ids",
            values=restored,
        ),
        control_slot_sha256=_control_slot_sha256(slot),
        attested_raw_feature_matrix_sha256=source.attested_raw_feature_matrix_sha256,
        attested_raw_target_sha256=source.attested_raw_target_sha256,
        attested_model_sha256=source.attested_model_sha256,
        attested_fitted_training_transform_sha256=source.attested_fitted_training_transform_sha256,
        attested_other_transform_config_sha256=source.attested_other_transform_config_sha256,
    )
    return EncoderMappingRepairResult(
        record_ids=source.record_ids,
        raw_categories=source.raw_categories,
        mismatched_reference_view=mismatched_view,
        repaired_view=repaired_view,
        encoder_column_order=column_order,
        clean_block=clean_block,
        mismatched_reference_block=mismatched_block,
        repaired_block=repaired_block,
        restored_record_ids=restored,
        provenance=provenance,
    )


def validate_encoder_mapping_repair(
    result: EncoderMappingRepairResult,
    *,
    source: InferenceTransformSource,
    spec: EncoderMappingRepairSpec,
    slot: CandidateSlot,
) -> EncoderMappingRepairResult:
    """Recompute the whole repair and reject any mismatch.

    This is the one authoritative entry point for a repair result. The
    recomputation runs the same construction again and compares the artifact
    digest, so a forged field anywhere in the artifact is caught by one check
    rather than by a list of checks somebody has to keep complete.
    """

    result = _revalidated(result)
    expected = apply_encoder_mapping_repair(source=source, spec=spec, slot=slot)

    if result.record_ids != expected.record_ids:
        _fail("result identifiers must match the source exactly, order included")
    if result.raw_categories != expected.raw_categories:
        _fail("the raw evaluation column must survive the control untouched")
    if result.restored_record_ids != expected.restored_record_ids:
        _fail("restored identifiers do not match the rows carrying the source category")
    if result.repaired_view != expected.repaired_view:
        _fail("the repaired inference view does not restore the raw categories")
    if result.repaired_block != expected.repaired_block:
        _fail("the repaired block does not match the clean fitted-transform block")
    if result.mismatched_reference_block != expected.mismatched_reference_block:
        _fail("the predeclared reference block does not match the declared rank mapping")
    if result.artifact_sha256() != expected.artifact_sha256():
        _fail("the repair artifact does not match the recomputed construction")
    return result


class RepairControlMeasurement(_StrictFrozenModel):
    """What the repair control measured, and how far that measurement may go.

    ``status`` is derived by a validator from the measured primary outcome, so a
    caller who edits one without the other is rejected. Neither status is an
    eligibility decision.
    """

    schema_version: Literal["p2-repair-control-measurement/v1"]
    repair_artifact_sha256: Sha256
    control_slot_sha256: Sha256
    inference_source_sha256: Sha256
    evaluation_source_sha256: Sha256
    comparison: MetricComparison
    status: RepairControlStatus

    @model_validator(mode="after")
    def _status_is_derived(self) -> RepairControlMeasurement:
        # Pydantic does not revalidate an already-created nested model by
        # default. Rebuild it explicitly so ``model_construct`` cannot smuggle
        # a non-finite or internally inconsistent comparison into a new,
        # otherwise-valid measurement.
        MetricComparison.model_validate(self.comparison.model_dump())
        outcome = self.comparison.measured_primary_outcome
        expected: RepairControlStatus = (
            "control_direction_violation" if outcome == "regression" else "validity_review_required"
        )
        if self.status != expected:
            raise ValueError(
                f"status must be derived from the measured primary outcome; expected {expected!r}"
            )
        return self

    def canonical_sha256(self) -> str:
        """Digest the complete measurement, including both source bindings."""

        return canonical_sha256(
            {"schema_version": self.schema_version, "measurement": self.model_dump(mode="json")}
        )


def _inference_source_sha256(source: InferenceTransformSource) -> str:
    """Bind a measurement to every declared inference-source field.

    ``InferenceTransformSource`` intentionally carries attestations rather than
    raw matrices, targets and model bytes. This digest therefore binds those
    attestations; it does not claim to recompute the unavailable artifacts.
    """

    return canonical_sha256(
        {
            "schema_version": source.schema_version,
            "inference_source": source.model_dump(mode="json"),
        }
    )


def measure_repair_control(
    *,
    result: EncoderMappingRepairResult,
    source: InferenceTransformSource,
    spec: EncoderMappingRepairSpec,
    slot: CandidateSlot,
    test_set: CleanTestSet,
    mismatched_reference_predictions: PredictionVector,
    repaired_predictions: PredictionVector,
) -> RepairControlMeasurement:
    """Score the repaired run against its own predeclared mismatched reference.

    The reference is the mismatched transform, not the clean one: the control
    asks whether restoring the fitted mapping recovers what the mismatch cost.
    The outcome is whatever the vectors say. A regression is reported as a
    control-direction violation rather than hidden by re-running with another
    seed, and a stable result is reported as stable.
    """

    source = _revalidated(source)
    test_set = _revalidated(test_set)
    if source.record_ids != test_set.record_ids:
        _fail(
            "the repair source and clean test set must contain the same records in the same order"
        )
    if source.attested_raw_feature_matrix_sha256 != (test_set.attested_test_feature_matrix_sha256):
        _fail("the repair source and clean test set are not bound to the same feature matrix")
    if source.attested_raw_target_sha256 != test_set.attested_target_sha256:
        _fail("the repair source and clean test set are not bound to the same target artifact")
    if source.attested_model_sha256 != test_set.attested_model_sha256:
        _fail("the repair source and clean test set are not bound to the same fitted model")

    validated = validate_encoder_mapping_repair(result, source=source, spec=spec, slot=slot)
    comparison = compare_binary_metrics(
        test_set=test_set,
        reference_predictions=mismatched_reference_predictions,
        observed_predictions=repaired_predictions,
    )
    outcome = comparison.measured_primary_outcome
    status: RepairControlStatus = (
        "control_direction_violation" if outcome == "regression" else "validity_review_required"
    )
    return RepairControlMeasurement(
        schema_version=ENCODER_REPAIR_MEASUREMENT_SCHEMA_VERSION,
        repair_artifact_sha256=validated.artifact_sha256(),
        control_slot_sha256=validated.provenance.control_slot_sha256,
        inference_source_sha256=_inference_source_sha256(source),
        evaluation_source_sha256=test_set.artifact_sha256(),
        comparison=comparison,
        status=status,
    )


def validate_repair_control_measurement(
    measurement: RepairControlMeasurement,
    *,
    result: EncoderMappingRepairResult,
    source: InferenceTransformSource,
    spec: EncoderMappingRepairSpec,
    slot: CandidateSlot,
    test_set: CleanTestSet,
    mismatched_reference_predictions: PredictionVector,
    repaired_predictions: PredictionVector,
) -> RepairControlMeasurement:
    """Recompute a repair measurement and reject forged or replayed records."""

    measurement = _revalidated(measurement)
    expected = measure_repair_control(
        result=result,
        source=source,
        spec=spec,
        slot=slot,
        test_set=test_set,
        mismatched_reference_predictions=mismatched_reference_predictions,
        repaired_predictions=repaired_predictions,
    )
    if measurement.canonical_sha256() != expected.canonical_sha256():
        _fail("the repair measurement does not match the recomputed bound measurement")
    return measurement


# --------------------------------------------------------------------------- #
# 2. Name-bound column order permutation control
# --------------------------------------------------------------------------- #


class NamedFeatureRow(_StrictFrozenModel):
    """One record's values, aligned by position to the table's feature names."""

    record_id: str = Field(min_length=1, max_length=256)
    values: tuple[str, ...]

    @model_validator(mode="after")
    def _row_is_canonical(self) -> NamedFeatureRow:
        _require_canonical_text(self.record_id, label="record ID")
        if not self.values:
            raise ValueError("a row must carry at least one value")
        for position, value in enumerate(self.values):
            if value != unicodedata.normalize("NFC", value):
                raise ValueError(f"value at position {position} must already be Unicode NFC")
        return self


class NamedFeatureTable(_StrictFrozenModel):
    """An input frame as a name-bound transformer sees it.

    Values are bound to names by position within a row, and the semantic digest
    sorts on the name before hashing. Two tables that differ only in physical
    column order therefore share a semantic digest, while swapping two values
    between two names changes it — which is exactly the difference the benign
    control has to be able to detect.
    """

    schema_version: Literal["p2-named-feature-table/v1"]
    feature_names: tuple[str, ...]
    rows: tuple[NamedFeatureRow, ...]

    @model_validator(mode="after")
    def _table_is_well_formed(self) -> NamedFeatureTable:
        width = len(self.feature_names)
        if width < _MIN_PERMUTABLE_COLUMNS:
            raise ValueError(
                f"a permutable table needs at least {_MIN_PERMUTABLE_COLUMNS} feature names"
            )
        if width > _MAX_COLUMNS:
            raise ValueError("table exceeds the supported column count")
        for position, name in enumerate(self.feature_names):
            _require_canonical_text(name, label=f"feature name at position {position}")
        if len(set(self.feature_names)) != width:
            raise ValueError("feature names must be unique; a duplicate breaks the name binding")
        if not self.rows:
            raise ValueError("a table must contain at least one record")
        _check_record_ids(tuple(row.record_id for row in self.rows))
        for position, row in enumerate(self.rows):
            if len(row.values) != width:
                raise ValueError(f"row {position} must carry one value per feature name")
        return self

    @property
    def record_ids(self) -> tuple[str, ...]:
        return tuple(row.record_id for row in self.rows)

    def physical_order_sha256(self) -> str:
        """Digest the column order itself, order included."""

        return _ordered_digest(
            schema_version=self.schema_version, key="feature_names", values=self.feature_names
        )

    def record_ids_sha256(self) -> str:
        """Digest record identifiers in evaluation order."""

        return _ordered_digest(
            schema_version=self.schema_version, key="record_ids", values=self.record_ids
        )

    def artifact_sha256(self) -> str:
        """Digest every serialized table field, including row and column order."""

        return canonical_sha256(
            {"schema_version": self.schema_version, "table": self.model_dump(mode="json")}
        )

    def semantic_sha256(self) -> str:
        """Digest the name-to-value mapping of every record, column order excluded.

        Row order is kept because two records are not interchangeable. Column
        order is dropped because a name-bound transformer does not read it.
        """

        records: list[dict[str, object]] = []
        for row in self.rows:
            pairs = [
                {"name": name, "value": value}
                for name, value in zip(self.feature_names, row.values, strict=True)
            ]
            pairs.sort(key=lambda item: str(item["name"]))
            records.append({"record_id": row.record_id, "named_values": pairs})
        return canonical_sha256({"schema_version": self.schema_version, "records": records})

    def permuted(self, order: Sequence[int]) -> NamedFeatureTable:
        """Return the same data with columns physically reordered by ``order``."""

        width = len(self.feature_names)
        if sorted(order) != list(range(width)):
            _fail("a column permutation must be an exact permutation of the existing columns")
        return NamedFeatureTable(
            schema_version=self.schema_version,
            feature_names=tuple(self.feature_names[index] for index in order),
            rows=tuple(
                NamedFeatureRow(
                    record_id=row.record_id,
                    values=tuple(row.values[index] for index in order),
                )
                for row in self.rows
            ),
        )


def permutation_order(*, feature_names: Sequence[str], seed: int) -> tuple[int, ...]:
    """Return the deterministic non-identity column permutation for ``seed``.

    Columns are ranked by a seeded digest of their name, which is independent of
    dictionary iteration order and of the machine. If that ranking happens to be
    the identity, a single rotation is applied: the control has to change the
    physical order, and silently returning the identity would make it prove
    nothing.
    """

    width = len(feature_names)
    if width < _MIN_PERMUTABLE_COLUMNS:
        _fail(f"a permutation needs at least {_MIN_PERMUTABLE_COLUMNS} columns")
    if len(set(feature_names)) != width:
        _fail("feature names must be unique before they can be permuted")
    ranked = sorted(
        range(width),
        key=lambda index: canonical_sha256(
            {
                "schema_version": PERMUTATION_ORDER_SCHEMA_VERSION,
                "seed": seed,
                "feature_name": feature_names[index],
            }
        ),
    )
    if ranked == list(range(width)):
        ranked = [*ranked[1:], ranked[0]]
    return tuple(ranked)


class ColumnPermutationSpec(_StrictFrozenModel):
    """The predeclared parameters of the semantics-preserving layout control.

    The absent ranks are not an oversight: this control names no category pair,
    and the frozen plan records that by leaving both ranks empty. A rank here
    would describe an intervention this control does not perform.
    """

    schema_version: Literal["p2-column-permutation-spec/v1"] = "p2-column-permutation-spec/v1"
    injection_id: InjectionId
    parameters: PreprocessingBugParameters
    seed: int = Field(ge=0)

    @model_validator(mode="after")
    def _spec_preserves_semantics(self) -> ColumnPermutationSpec:
        parameters = self.parameters
        if parameters.source_rank is not None or parameters.mapped_rank is not None:
            raise ValueError(
                "the column-permutation control maps no category; a rank pair belongs to a "
                "mapping intervention"
            )
        if parameters.target_feature != ALPHA_TARGET_FEATURE:
            raise ValueError(f"the alpha preprocessing slice targets {ALPHA_TARGET_FEATURE!r} only")
        if parameters.mode != ALPHA_PREPROCESSING_MODE:
            raise ValueError(
                f"the layout control touches inference only; mode must be "
                f"{ALPHA_PREPROCESSING_MODE!r}"
            )
        if parameters.transform_name != ALPHA_TRANSFORM_NAME:
            raise ValueError(
                f"the alpha layout control requires transform {ALPHA_TRANSFORM_NAME!r}"
            )
        return self


class ColumnPermutationProvenance(_StrictFrozenModel):
    """Everything an evaluator needs to reproduce and audit one permutation."""

    schema_version: Literal["p2-column-permutation/v1"]
    intervention_type: Literal["name_bound_column_order_permutation"]
    permutation_protocol_version: Literal["name-bound-column-order-permutation/v1"]
    injection_id: InjectionId
    mode: Literal["inference_only"]
    target_feature: str = Field(min_length=1, max_length=128)
    transform_name: str = Field(min_length=1, max_length=128)
    seed: int = Field(ge=0)
    record_count: int = Field(ge=1)
    column_count: int = Field(ge=_MIN_PERMUTABLE_COLUMNS)
    original_physical_order_sha256: Sha256
    permuted_physical_order_sha256: Sha256
    original_semantic_sha256: Sha256
    permuted_semantic_sha256: Sha256
    record_ids_sha256: Sha256
    permutation_sha256: Sha256
    control_slot_sha256: Sha256

    @model_validator(mode="after")
    def _layout_changed_and_semantics_did_not(self) -> ColumnPermutationProvenance:
        if self.original_physical_order_sha256 == self.permuted_physical_order_sha256:
            raise ValueError("the layout control must actually change the physical column order")
        if self.original_semantic_sha256 != self.permuted_semantic_sha256:
            raise ValueError("the layout control must not change any name-to-value mapping")
        return self


class ColumnPermutationResult(_StrictFrozenModel):
    """A completed permutation: both tables and the exact permutation applied.

    No field records whether the control succeeded. Equivalence is the return
    value of :func:`benign_equivalence_status`, which recomputes it.
    """

    schema_version: Literal["p2-column-permutation-result/v1"] = "p2-column-permutation-result/v1"
    original_table: NamedFeatureTable
    permuted_table: NamedFeatureTable
    permutation: tuple[int, ...]
    provenance: ColumnPermutationProvenance

    @model_validator(mode="after")
    def _result_is_internally_aligned(self) -> ColumnPermutationResult:
        width = len(self.original_table.feature_names)
        if sorted(self.permutation) != list(range(width)):
            raise ValueError("permutation must be an exact permutation of the existing columns")
        if list(self.permutation) == list(range(width)):
            raise ValueError("the identity permutation would leave the physical order unchanged")
        if self.permuted_table.feature_names == self.original_table.feature_names:
            raise ValueError("the permuted table must carry a different physical column order")
        if set(self.permuted_table.feature_names) != set(self.original_table.feature_names):
            raise ValueError("a permutation must not add, drop or rename a column")
        if self.original_table.record_ids != self.permuted_table.record_ids:
            raise ValueError("a permutation must not reorder or rename records")
        if self.original_table.semantic_sha256() != self.permuted_table.semantic_sha256():
            raise ValueError("a permutation must not change any name-to-value mapping")
        if len(self.original_table.rows) != self.provenance.record_count:
            raise ValueError("result record count must match its provenance")
        if width != self.provenance.column_count:
            raise ValueError("result column count must match its provenance")
        return self

    def artifact_sha256(self) -> str:
        """Digest every serialized field, including both physical column orders."""

        return canonical_sha256(
            {
                "digest_schema_version": PERMUTATION_ARTIFACT_DIGEST_SCHEMA_VERSION,
                "artifact": self.model_dump(mode="json"),
            }
        )


def apply_column_permutation(
    *, table: NamedFeatureTable, spec: ColumnPermutationSpec, slot: CandidateSlot
) -> ColumnPermutationResult:
    """Permute the physical column order and change nothing a name can reach."""

    table = _revalidated(table)
    spec = _revalidated(spec)
    slot = _validate_control_slot(
        slot,
        expected_role="designed_benign_control",
        expected_intervention_type=PERMUTATION_INTERVENTION_TYPE,
        injection_id=spec.injection_id,
        parameters=spec.parameters,
        seed=spec.seed,
    )

    order = permutation_order(feature_names=table.feature_names, seed=spec.seed)
    permuted = table.permuted(order)
    if permuted.feature_names == table.feature_names:
        _fail("the derived permutation left the physical column order unchanged")
    if permuted.semantic_sha256() != table.semantic_sha256():
        _fail("the permutation changed a name-to-value mapping")

    provenance = ColumnPermutationProvenance(
        schema_version=PERMUTATION_PROVENANCE_SCHEMA_VERSION,
        intervention_type=PERMUTATION_INTERVENTION_TYPE,
        permutation_protocol_version=PERMUTATION_PROTOCOL_VERSION,
        injection_id=spec.injection_id,
        mode=ALPHA_PREPROCESSING_MODE,
        target_feature=spec.parameters.target_feature,
        transform_name=spec.parameters.transform_name,
        seed=spec.seed,
        record_count=len(table.rows),
        column_count=len(table.feature_names),
        original_physical_order_sha256=table.physical_order_sha256(),
        permuted_physical_order_sha256=permuted.physical_order_sha256(),
        original_semantic_sha256=table.semantic_sha256(),
        permuted_semantic_sha256=permuted.semantic_sha256(),
        record_ids_sha256=_ordered_digest(
            schema_version=PERMUTATION_PROVENANCE_SCHEMA_VERSION,
            key="record_ids",
            values=table.record_ids,
        ),
        permutation_sha256=canonical_sha256(
            {
                "schema_version": PERMUTATION_ORDER_SCHEMA_VERSION,
                "seed": spec.seed,
                "permutation": list(order),
            }
        ),
        control_slot_sha256=_control_slot_sha256(slot),
    )
    return ColumnPermutationResult(
        original_table=table,
        permuted_table=permuted,
        permutation=order,
        provenance=provenance,
    )


def validate_column_permutation(
    result: ColumnPermutationResult,
    *,
    table: NamedFeatureTable,
    spec: ColumnPermutationSpec,
    slot: CandidateSlot,
) -> ColumnPermutationResult:
    """Recompute the whole permutation and reject any mismatch.

    This is the one authoritative entry point for a permutation result.
    """

    result = _revalidated(result)
    expected = apply_column_permutation(table=table, spec=spec, slot=slot)

    if result.original_table.feature_names != expected.original_table.feature_names:
        _fail("the original physical column order does not match the supplied table")
    if result.original_table.semantic_sha256() != expected.original_table.semantic_sha256():
        _fail("the original name-to-value mapping does not match the supplied table")
    if result.permutation != expected.permutation:
        _fail("the permutation is not the deterministic order for this seed")
    if result.permuted_table.feature_names != expected.permuted_table.feature_names:
        _fail("the permuted physical column order does not match the deterministic permutation")
    if result.artifact_sha256() != expected.artifact_sha256():
        _fail("the permutation artifact does not match the recomputed construction")
    return result


# --------------------------------------------------------------------------- #
# 3. Post-execution equivalence for the benign control
# --------------------------------------------------------------------------- #


class TransformedMatrix(_StrictFrozenModel):
    """A transformed feature matrix as the fitted transformer emitted it."""

    schema_version: Literal["p2-transformed-matrix/v1"]
    column_names: tuple[str, ...]
    rows: tuple[tuple[float, ...], ...]

    @model_validator(mode="after")
    def _matrix_is_finite_and_aligned(self) -> TransformedMatrix:
        width = len(self.column_names)
        if width == 0:
            raise ValueError("a transformed matrix must have at least one column")
        for position, name in enumerate(self.column_names):
            _require_canonical_text(name, label=f"transformed column at position {position}")
        if len(set(self.column_names)) != width:
            raise ValueError("transformed column names must be unique")
        if not self.rows:
            raise ValueError("a transformed matrix must have at least one row")
        for position, row in enumerate(self.rows):
            if len(row) != width:
                raise ValueError(f"transformed row {position} must have one value per column")
            for column, value in enumerate(row):
                if not math.isfinite(value):
                    raise ValueError(
                        f"transformed value at row {position}, column {column} is not finite"
                    )
        return self

    def canonical_sha256(self) -> str:
        """Digest the matrix with its row and column order included."""

        return canonical_sha256(
            {
                "schema_version": self.schema_version,
                "column_names": list(self.column_names),
                "rows": [list(row) for row in self.rows],
            }
        )


class BenignEquivalenceEvidence(_StrictFrozenModel):
    """Real transformed matrices and prediction vectors from both layouts.

    Every field is measured data. There is no verdict field, no pass flag and no
    metric: this model is the input to a comparison, never its conclusion.
    """

    schema_version: Literal["p2-benign-equivalence-evidence/v1"]
    permutation_artifact_sha256: Sha256
    source_table_sha256: Sha256
    record_ids_sha256: Sha256
    evaluation_source_sha256: Sha256
    attested_model_sha256: Sha256
    preprocessing_specification_sha256: Sha256
    original_transformed: TransformedMatrix
    permuted_transformed: TransformedMatrix
    reference_predictions: PredictionVector
    observed_predictions: PredictionVector

    @model_validator(mode="after")
    def _evidence_is_aligned(self) -> BenignEquivalenceEvidence:
        if self.reference_predictions.role != "reference":
            raise ValueError("the original-layout vector must declare the reference role")
        if self.observed_predictions.role != "observed":
            raise ValueError("the permuted-layout vector must declare the observed role")
        counts = {
            len(self.original_transformed.rows),
            len(self.permuted_transformed.rows),
            len(self.reference_predictions.predictions),
            len(self.observed_predictions.predictions),
        }
        if len(counts) != 1:
            raise ValueError("matrices and prediction vectors must describe the same records")
        return self


def benign_equivalence_status(
    *,
    result: ColumnPermutationResult,
    table: NamedFeatureTable,
    spec: ColumnPermutationSpec,
    slot: CandidateSlot,
    test_set: CleanTestSet | None = None,
    evidence: BenignEquivalenceEvidence | None = None,
) -> BenignEquivalenceStatus:
    """Report how far the benign control has been verified, and no further.

    Missing post-execution evidence is neither a pass nor a failure: it is the
    honest statement that the second tier has not been measured. When evidence
    is present, transformed-matrix equality, prediction equality and all three
    metric snapshots are recomputed here rather than read from a flag, and any
    difference maps to ``benign_equivalence_failure`` — never to stable, and
    never to an eligible failure.

    Even when everything passes, the return value stops at
    ``equivalence_verified_pending_admission``. Admission is a later decision.
    """

    validated = validate_column_permutation(result, table=table, spec=spec, slot=slot)

    if test_set is None and evidence is None:
        return "pending_post_execution_equivalence"
    if test_set is None or evidence is None:
        _fail("post-execution evidence and its clean test set must be supplied together")

    test_set = _revalidated(test_set)
    evidence = _revalidated(evidence)
    if evidence.permutation_artifact_sha256 != validated.artifact_sha256():
        _fail("the equivalence evidence is not bound to this permutation artifact")
    if evidence.source_table_sha256 != table.artifact_sha256():
        _fail("the equivalence evidence is not bound to this source feature table")
    if table.record_ids != test_set.record_ids:
        _fail("the source feature table and clean test set must contain identical ordered records")
    if evidence.record_ids_sha256 != table.record_ids_sha256():
        _fail("the equivalence evidence is not bound to the source table record sequence")
    if evidence.evaluation_source_sha256 != test_set.artifact_sha256():
        _fail("the equivalence evidence is not bound to this clean test set")
    if evidence.attested_model_sha256 != test_set.attested_model_sha256:
        _fail("the equivalence evidence is not bound to the clean-test fitted model")
    if evidence.preprocessing_specification_sha256 != (
        slot.identity.preprocessing_specification_sha256
    ):
        _fail("the equivalence evidence is not bound to the slot preprocessing specification")
    if len(evidence.reference_predictions.predictions) != test_set.record_count:
        _fail("the prediction vectors must have one entry per clean-test record")

    if evidence.original_transformed.canonical_sha256() != (
        evidence.permuted_transformed.canonical_sha256()
    ):
        return "benign_equivalence_failure"
    if evidence.reference_predictions.predictions != evidence.observed_predictions.predictions:
        return "benign_equivalence_failure"

    reference = metric_snapshot(test_set=test_set, predictions=evidence.reference_predictions)
    observed = metric_snapshot(test_set=test_set, predictions=evidence.observed_predictions)
    comparison = compare_binary_metrics(
        test_set=test_set,
        reference_predictions=evidence.reference_predictions,
        observed_predictions=evidence.observed_predictions,
    )
    validate_metric_comparison(
        comparison,
        test_set=test_set,
        reference_predictions=evidence.reference_predictions,
        observed_predictions=evidence.observed_predictions,
    )
    if reference.canonical_sha256() != observed.canonical_sha256():
        return "benign_equivalence_failure"
    return "equivalence_verified_pending_admission"
