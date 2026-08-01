"""Deterministic inference-time encoder mapping mismatch for the Phase 2 benchmark.

The mechanism makes the inference preprocessing step encode exactly one category
of exactly one categorical feature as if it were a different category, and
changes nothing else. Raw evaluation records, evaluation targets, record
membership and ordering, the trained model, the fitted training transform and
every other transform must survive untouched.

Four design choices carry that guarantee.

**The module never receives a model, a target vector or a feature matrix.** It
takes record identifiers, the raw values of one categorical column, a frozen
training vocabulary and digests of everything else. A function that has no model
cannot retrain one, so those parts of the one-factor invariant hold by
construction rather than by a check somebody could forget to run.

**Category choice is a pure function of rank, never of an outcome.** The caller
supplies ranks; the module resolves them against a vocabulary counted once on the
frozen training split. Ranking is total and deterministic — descending count,
then canonical lexical order — so the same vocabulary always produces the same
category for a rank, on any machine and in any listing order.

**Raw values are never rewritten.** The result carries the raw column unchanged
alongside a separate inference view. Rewriting the raw column would turn a
transform regression into a data regression and quietly break the one-factor
invariant the mechanism exists to preserve.

**The module owns no verdict.** No model here has a field for a metric, a
measured outcome, eligibility, a family class or a cause. Whether this
intervention harmed anything is decided later, from measurements this module
never performs.

Two error kinds are raised, matching the convention of the other Phase 2
injectors:

* a malformed *object* raises :class:`pydantic.ValidationError`, because model
  validators report through Pydantic;
* a malformed *relationship between objects* raises
  :class:`PreprocessingInterventionError`, because procedural checks run outside
  any model.

The frozen slot grid lives in :mod:`aletheia_lab.benchmark.p2.validation` and is
not restated here. This module reads the ranks and the seed from the slot it is
given and enforces the mechanism contract; which ranks belong to which slot
remains the business of the single authoritative plan validator.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from typing import Annotated, Final, Literal, NoReturn, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import CandidateSlot
from aletheia_lab.benchmark.p2.identity import (
    SHA256_PATTERN,
    SLOT_ID_PATTERN,
    PreprocessingBugParameters,
)
from aletheia_lab.benchmark.p2.validation import (
    ContractViolation,
    validate_frozen_alpha_slot,
)

# --------------------------------------------------------------------------- #
# Schema and protocol versions
# --------------------------------------------------------------------------- #

CATEGORY_VOCABULARY_SCHEMA_VERSION: Final[Literal["p2-category-vocabulary/v1"]] = (
    "p2-category-vocabulary/v1"
)
INFERENCE_SOURCE_SCHEMA_VERSION: Final[Literal["p2-inference-transform-source/v1"]] = (
    "p2-inference-transform-source/v1"
)
MISMATCH_SPEC_SCHEMA_VERSION: Final[Literal["p2-encoder-mapping-mismatch-spec/v1"]] = (
    "p2-encoder-mapping-mismatch-spec/v1"
)
MISMATCH_PROVENANCE_SCHEMA_VERSION: Final[Literal["p2-encoder-mapping-mismatch/v1"]] = (
    "p2-encoder-mapping-mismatch/v1"
)
MISMATCH_RESULT_SCHEMA_VERSION: Final[Literal["p2-encoder-mapping-mismatch-result/v1"]] = (
    "p2-encoder-mapping-mismatch-result/v1"
)
MISMATCH_ARTIFACT_DIGEST_SCHEMA_VERSION: Final[
    Literal["p2-encoder-mapping-mismatch-artifact-digest/v1"]
] = "p2-encoder-mapping-mismatch-artifact-digest/v1"
FAULT_SLOT_BINDING_SCHEMA_VERSION: Final[Literal["p2-fault-slot-binding/v1"]] = (
    "p2-fault-slot-binding/v1"
)
MAPPING_CHANGE_SCHEMA_VERSION: Final[Literal["p2-encoder-mapping-change/v1"]] = (
    "p2-encoder-mapping-change/v1"
)

#: Pinned protocol identifier. A ``Literal`` rather than free text, so no caller
#: can invent a protocol name that looks official.
MISMATCH_PROTOCOL_VERSION: Final[Literal["inference-encoder-mapping-mismatch/v1"]] = (
    "inference-encoder-mapping-mismatch/v1"
)

#: The one intervention type this entry point accepts. Both M3 controls carry a
#: different intervention type, so they are rejected here by contract rather
#: than by a slot-identifier blacklist that a new slot could slip past.
MISMATCH_INTERVENTION_TYPE: Final[Literal["inference_encoder_mapping_mismatch"]] = (
    "inference_encoder_mapping_mismatch"
)

#: The alpha slice touches inference only. ``PreprocessingBugParameters`` also
#: admits ``"both"``; this module refuses it, because changing the training
#: transform as well would break the one-factor invariant.
ALPHA_PREPROCESSING_MODE: Final[Literal["inference_only"]] = "inference_only"
ALPHA_TARGET_FEATURE: Final[Literal["Contract"]] = "Contract"
ALPHA_TRANSFORM_NAME: Final[Literal["one_hot_encoder"]] = "one_hot_encoder"

#: The alpha slice needs three ranked categories: the contract defines the fault
#: slots over ranks 1, 2 and 3 only.
ALPHA_RANK_COUNT: Final[int] = 3

#: The ranking rule, versioned because it decides which category a slot names.
#:
#: Descending count, then ascending canonical category string. Ties are broken
#: lexically on Unicode code points rather than by a locale collation, so the
#: rank of a category never depends on the machine that computed it.
CATEGORY_RANK_RULE: Final[Literal["count-desc-then-lexical/v1"]] = "count-desc-then-lexical/v1"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
InjectionId = Annotated[str, Field(pattern=SLOT_ID_PATTERN)]

_MAX_RECORDS: Final[int] = 5_000_000
_MAX_CATEGORIES: Final[int] = 4_096
_FLOAT_TOLERANCE: Final[float] = 1e-12

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class PreprocessingInterventionError(ContractViolation):
    """Raised when preprocessing-intervention artifacts disagree with one another.

    Malformed single objects raise ``pydantic.ValidationError`` instead: model
    validators report through Pydantic, which wraps every ``ValueError`` they
    raise. Keeping the two kinds apart lets callers tell "this object is
    invalid" from "these objects do not belong together".
    """


def _fail(message: str) -> NoReturn:
    """Reject a cross-object relationship."""

    raise PreprocessingInterventionError(message)


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
    """Reject text that would make two spellings of one category hash differently."""

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
        raise ValueError("an inference source must contain at least one record")
    if len(record_ids) > _MAX_RECORDS:
        raise ValueError("an inference source exceeds the supported record count")
    seen: set[str] = set()
    for position, record_id in enumerate(record_ids):
        _require_canonical_text(record_id, label=f"record ID at position {position}")
        if record_id in seen:
            raise ValueError("record IDs must be unique; duplicates cannot anchor an intervention")
        seen.add(record_id)


def _ordered_digest(*, schema_version: str, key: str, values: Sequence[str]) -> str:
    """Digest a string sequence with its order included."""

    return canonical_sha256({"schema_version": schema_version, key: list(values)})


def _membership_digest(*, schema_version: str, record_ids: Iterable[str]) -> str:
    """Digest which records exist, order excluded."""

    return canonical_sha256({"schema_version": schema_version, "membership": sorted(record_ids)})


def _block_digest(*, schema_version: str, key: str, block: Sequence[Sequence[int]]) -> str:
    """Digest an indicator matrix with row order and column order included."""

    return canonical_sha256({"schema_version": schema_version, key: [list(row) for row in block]})


# --------------------------------------------------------------------------- #
# Frozen training vocabulary and ranking
# --------------------------------------------------------------------------- #


class CategoryFrequency(_StrictFrozenModel):
    """One category of the target feature and how often it occurs in training."""

    category: str = Field(min_length=1, max_length=256)
    count: int = Field(ge=1)

    @model_validator(mode="after")
    def _category_is_canonical(self) -> CategoryFrequency:
        _require_canonical_text(self.category, label="category")
        return self


class FrozenCategoryVocabulary(_StrictFrozenModel):
    """The category counts of one feature, measured once on the training split.

    Counts come from training only. Using the evaluation split would let the
    ranks — and therefore the category a slot names — depend on the data the
    intervention is measured against.
    """

    schema_version: Literal["p2-category-vocabulary/v1"]
    feature: str = Field(min_length=1, max_length=128)
    split: Literal["train"]
    rank_rule: Literal["count-desc-then-lexical/v1"]
    frequencies: tuple[CategoryFrequency, ...]

    @model_validator(mode="after")
    def _vocabulary_is_well_formed(self) -> FrozenCategoryVocabulary:
        _require_canonical_text(self.feature, label="feature name")
        if len(self.frequencies) < ALPHA_RANK_COUNT:
            raise ValueError(
                "the alpha slice addresses ranks 1 to 3, so the vocabulary needs at least "
                f"{ALPHA_RANK_COUNT} categories, got {len(self.frequencies)}"
            )
        if len(self.frequencies) > _MAX_CATEGORIES:
            raise ValueError("vocabulary exceeds the supported category count")
        names = [entry.category for entry in self.frequencies]
        if len(set(names)) != len(names):
            raise ValueError(
                "categories must be unique; a duplicate would make two ranks name one category"
            )
        return self

    def ranked_categories(self) -> tuple[str, ...]:
        """Return categories from rank 1 (most frequent) to rank N (rarest).

        Sorting is total: descending count first, then the canonical category
        string. Two categories with the same count therefore always rank in the
        same order, whatever order the caller listed them in.
        """

        ordered = sorted(self.frequencies, key=lambda entry: (-entry.count, entry.category))
        return tuple(entry.category for entry in ordered)

    def category_for_rank(self, rank: int) -> str:
        """Return the category at ``rank``, counting from 1."""

        ranked = self.ranked_categories()
        if rank < 1 or rank > len(ranked):
            _fail(f"rank {rank} is outside the vocabulary of {len(ranked)} categories")
        return ranked[rank - 1]

    def rank_for_category(self, category: str) -> int:
        """Return the rank of ``category``, counting from 1."""

        ranked = self.ranked_categories()
        if category not in ranked:
            _fail(f"category {category!r} is not in the frozen training vocabulary")
        return ranked.index(category) + 1

    def encoder_column_order(self) -> tuple[str, ...]:
        """Return the one-hot column order of the fitted encoder.

        The fitted encoder stores its categories sorted, so the indicator block
        is emitted in sorted order rather than in rank order. Rank decides
        *which* category a slot names; it never decides column layout.
        """

        return tuple(sorted(entry.category for entry in self.frequencies))

    def canonical_sha256(self) -> str:
        """Digest the vocabulary independently of the caller's listing order."""

        entries: list[dict[str, object]] = [
            {"category": entry.category, "count": entry.count} for entry in self.frequencies
        ]
        entries.sort(key=lambda item: str(item["category"]))
        return canonical_sha256(
            {
                "schema_version": self.schema_version,
                "feature": self.feature,
                "split": self.split,
                "rank_rule": self.rank_rule,
                "frequencies": entries,
            }
        )


# --------------------------------------------------------------------------- #
# Inference source
# --------------------------------------------------------------------------- #


class InferenceTransformSource(_StrictFrozenModel):
    """The evaluation rows of one categorical feature, plus digests of the rest.

    The feature matrix, the targets, the trained model and the fitted training
    transform are deliberately absent. The module carries their digests forward
    and names them ``attested_*``: it cannot see those artifacts, so it cannot
    recompute them, and the caller that holds them is responsible for proving
    they are unchanged.
    """

    schema_version: Literal["p2-inference-transform-source/v1"]
    split: Literal["test"]
    feature: str = Field(min_length=1, max_length=128)
    record_ids: tuple[str, ...]
    raw_categories: tuple[str, ...]
    vocabulary: FrozenCategoryVocabulary
    attested_raw_feature_matrix_sha256: Sha256
    attested_raw_target_sha256: Sha256
    attested_model_sha256: Sha256
    attested_fitted_training_transform_sha256: Sha256
    attested_other_transform_config_sha256: Sha256

    @model_validator(mode="after")
    def _source_is_well_formed(self) -> InferenceTransformSource:
        _require_canonical_text(self.feature, label="feature name")
        if self.feature != ALPHA_TARGET_FEATURE:
            raise ValueError(f"the alpha preprocessing slice targets {ALPHA_TARGET_FEATURE!r} only")
        _check_record_ids(self.record_ids)
        if len(self.record_ids) != len(self.raw_categories):
            raise ValueError(
                "record_ids and raw_categories must align: "
                f"{len(self.record_ids)} ids against {len(self.raw_categories)} values"
            )
        if self.feature != self.vocabulary.feature:
            raise ValueError("the source feature must match the vocabulary feature")
        known = set(self.vocabulary.ranked_categories())
        for position, category in enumerate(self.raw_categories):
            _require_canonical_text(category, label=f"raw category at position {position}")
            if category not in known:
                raise ValueError(
                    f"raw category at position {position} is outside the frozen training "
                    f"vocabulary: {category!r}"
                )
        return self

    @property
    def record_count(self) -> int:
        return len(self.record_ids)

    def record_ids_sha256(self) -> str:
        """Digest the identifier sequence, order included."""

        return _ordered_digest(
            schema_version=self.schema_version, key="record_ids", values=self.record_ids
        )

    def membership_sha256(self) -> str:
        """Digest which records exist, order excluded."""

        return _membership_digest(schema_version=self.schema_version, record_ids=self.record_ids)

    def raw_categories_sha256(self) -> str:
        """Digest the raw column exactly as it stands, order included.

        Order matters here: the mechanism must not permute rows, and an
        order-invariant digest could not tell a permutation from a no-op.
        """

        return _ordered_digest(
            schema_version=self.schema_version, key="raw_categories", values=self.raw_categories
        )

    def indicator_block(self, categories: Sequence[str]) -> tuple[tuple[int, ...], ...]:
        """Return the one-hot block the fitted encoder would emit for ``categories``.

        The encoder is not refitted: the column order comes from the frozen
        vocabulary, so an unseen value could only appear if a caller bypassed
        validation, and that is rejected rather than encoded as all zeros.
        """

        column_order = self.vocabulary.encoder_column_order()
        index_of = {category: index for index, category in enumerate(column_order)}
        rows: list[tuple[int, ...]] = []
        for position, category in enumerate(categories):
            index = index_of.get(category)
            if index is None:
                _fail(
                    f"cannot encode value at position {position}: {category!r} is not in the "
                    "frozen encoder vocabulary"
                )
            rows.append(tuple(1 if column == index else 0 for column in range(len(column_order))))
        return tuple(rows)


# --------------------------------------------------------------------------- #
# Specification
# --------------------------------------------------------------------------- #


class EncoderMappingMismatchSpec(_StrictFrozenModel):
    """One prespecified mapping mismatch: which ranks, which seed.

    The parameters are the canonical identity model rather than a second set of
    fields, so a specification and the family identity it belongs to cannot
    describe two different interventions.
    """

    schema_version: Literal["p2-encoder-mapping-mismatch-spec/v1"] = (
        "p2-encoder-mapping-mismatch-spec/v1"
    )
    injection_id: InjectionId
    parameters: PreprocessingBugParameters
    source_category: str = Field(min_length=1, max_length=256)
    mapped_category: str = Field(min_length=1, max_length=256)
    seed: int = Field(ge=0)

    @model_validator(mode="after")
    def _spec_describes_the_alpha_slice(self) -> EncoderMappingMismatchSpec:
        parameters = self.parameters
        _require_canonical_text(self.source_category, label="source category")
        _require_canonical_text(self.mapped_category, label="mapped category")
        if self.source_category == self.mapped_category:
            raise ValueError("source_category and mapped_category must differ")
        if parameters.target_feature != ALPHA_TARGET_FEATURE:
            raise ValueError(f"the alpha preprocessing slice targets {ALPHA_TARGET_FEATURE!r} only")
        if parameters.mode != ALPHA_PREPROCESSING_MODE:
            raise ValueError(
                "the alpha slice changes the inference transform only; "
                f"mode must be {ALPHA_PREPROCESSING_MODE!r}"
            )
        if parameters.transform_name != ALPHA_TRANSFORM_NAME:
            raise ValueError(
                f"the alpha mapping mismatch requires transform {ALPHA_TRANSFORM_NAME!r}"
            )
        if parameters.source_rank is None or parameters.mapped_rank is None:
            raise ValueError("a mapping mismatch requires both a source rank and a mapped rank")
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
        """The declared source rank.

        ``PreprocessingBugParameters`` allows both ranks to be absent, because
        the benign control needs that shape. This specification does not, and
        the model validator has already rejected it; the guard here exists so
        the narrowing is enforced rather than asserted.
        """

        rank = self.parameters.source_rank
        if rank is None:
            _fail("the specification is missing its source rank")
        return rank

    @property
    def mapped_rank(self) -> int:
        """The declared mapped rank, guarded the same way as :attr:`source_rank`."""

        rank = self.parameters.mapped_rank
        if rank is None:
            _fail("the specification is missing its mapped rank")
        return rank


# --------------------------------------------------------------------------- #
# Slot binding
# --------------------------------------------------------------------------- #


def _validate_fault_slot(slot: CandidateSlot, *, spec: EncoderMappingMismatchSpec) -> CandidateSlot:
    """Bind one intervention to its frozen fault slot.

    Which ranks and seed belong to which slot identifier is decided by the
    single authoritative plan validator, so this function does not restate that
    table. It enforces the mechanism contract — this is a preprocessing fault,
    it is fault-directed, it is the alpha intervention, and the specification
    the caller passed is the one the slot declares.
    """

    try:
        slot = validate_frozen_alpha_slot(slot)
    except ContractViolation as error:
        _fail(str(error))
    identity = slot.identity
    parameters = identity.canonical_intervention_parameters

    if slot.fault_type != "preprocessing_bug" or identity.fault_type != "preprocessing_bug":
        _fail("the preprocessing intervention accepts preprocessing_bug slots only")
    if slot.role != "fault_directed":
        _fail(
            "the fault-injection entry point accepts fault-directed slots only; "
            f"got role {slot.role!r}"
        )
    if identity.intervention_type != MISMATCH_INTERVENTION_TYPE:
        _fail(
            "the fault-injection entry point implements "
            f"{MISMATCH_INTERVENTION_TYPE!r} only; got {identity.intervention_type!r}"
        )
    if slot.slot_id != spec.injection_id:
        _fail("the specification injection_id differs from the frozen slot identifier")
    if not isinstance(parameters, PreprocessingBugParameters):
        _fail("a preprocessing_bug slot must carry preprocessing parameters")
    if parameters != spec.parameters:
        _fail("the specification differs from the parameters the slot declares")
    if identity.seed != spec.seed:
        _fail("the specification seed differs from the seed the slot declares")
    return slot


def _fault_slot_sha256(slot: CandidateSlot) -> str:
    """Bind the complete frozen slot, including its twelve-field family identity."""

    slot = _revalidated(slot)
    return canonical_sha256(
        {
            "schema_version": FAULT_SLOT_BINDING_SCHEMA_VERSION,
            "slot": slot.model_dump(mode="json"),
        }
    )


def _declared_mapping_sha256(spec: EncoderMappingMismatchSpec) -> str:
    """Digest the one and only declared inference mapping change."""

    return canonical_sha256(
        {
            "schema_version": MAPPING_CHANGE_SCHEMA_VERSION,
            "injection_id": spec.injection_id,
            "target_feature": spec.parameters.target_feature,
            "source_category": spec.source_category,
            "mapped_category": spec.mapped_category,
            "mode": spec.parameters.mode,
            "transform_name": spec.parameters.transform_name,
            "seed": spec.seed,
        }
    )


# --------------------------------------------------------------------------- #
# Provenance and result
# --------------------------------------------------------------------------- #


class EncoderMappingMismatchProvenance(_StrictFrozenModel):
    """Everything an evaluator needs to reproduce and audit one mismatch.

    ``attested_*`` fields are copied and cross-bound to the validated inference
    source. They are not recomputed from raw feature, target, model or fitted
    training-transform data, because this module never receives those artifacts.
    """

    schema_version: Literal["p2-encoder-mapping-mismatch/v1"]
    intervention_type: Literal["inference_encoder_mapping_mismatch"]
    mismatch_protocol_version: Literal["inference-encoder-mapping-mismatch/v1"]
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
    affected_record_count: int = Field(ge=1)
    affected_ratio: float = Field(gt=0.0, le=1.0)
    column_count: int = Field(ge=ALPHA_RANK_COUNT)
    vocabulary_sha256: Sha256
    encoder_column_order_sha256: Sha256
    source_record_ids_sha256: Sha256
    source_membership_sha256: Sha256
    raw_categories_sha256: Sha256
    inference_view_categories_sha256: Sha256
    reference_block_sha256: Sha256
    mismatched_block_sha256: Sha256
    affected_record_ids_sha256: Sha256
    fault_slot_sha256: Sha256
    declared_mapping_sha256: Sha256
    attested_raw_feature_matrix_sha256: Sha256
    attested_raw_target_sha256: Sha256
    attested_model_sha256: Sha256
    attested_fitted_training_transform_sha256: Sha256
    attested_other_transform_config_sha256: Sha256

    @model_validator(mode="after")
    def _counts_and_digests_agree(self) -> EncoderMappingMismatchProvenance:
        _require_canonical_text(self.source_category, label="source category")
        _require_canonical_text(self.mapped_category, label="mapped category")
        if self.source_rank == self.mapped_rank:
            raise ValueError("source_rank and mapped_rank must differ")
        if self.source_category == self.mapped_category:
            raise ValueError("source_category and mapped_category must differ")
        if self.affected_record_count > self.record_count:
            raise ValueError("affected records cannot exceed the record count")
        expected_ratio = self.affected_record_count / self.record_count
        if abs(self.affected_ratio - expected_ratio) > _FLOAT_TOLERANCE:
            raise ValueError("affected_ratio must be derived from the two counts")
        if self.reference_block_sha256 == self.mismatched_block_sha256:
            raise ValueError(
                "a mapping mismatch must change the encoded block; identical digests mean "
                "the intervention had no effect"
            )
        if self.raw_categories_sha256 == self.inference_view_categories_sha256:
            raise ValueError("a mapping mismatch must change the inference view of some row")
        return self


class EncoderMappingMismatchResult(_StrictFrozenModel):
    """A completed mismatch: the raw column, the inference view and both blocks.

    The result describes what was done to the inference transform. It carries no
    metric, measured outcome, eligibility, family class or cause; those are
    decided later, from measurements this module never performs.
    """

    schema_version: Literal["p2-encoder-mapping-mismatch-result/v1"] = (
        "p2-encoder-mapping-mismatch-result/v1"
    )
    record_ids: tuple[str, ...]
    raw_categories: tuple[str, ...]
    inference_view_categories: tuple[str, ...]
    encoder_column_order: tuple[str, ...]
    reference_block: tuple[tuple[int, ...], ...]
    mismatched_block: tuple[tuple[int, ...], ...]
    affected_record_ids: tuple[str, ...]
    provenance: EncoderMappingMismatchProvenance

    @model_validator(mode="after")
    def _result_is_internally_aligned(self) -> EncoderMappingMismatchResult:
        _check_record_ids(self.record_ids)
        count = len(self.record_ids)
        if not (
            count
            == len(self.raw_categories)
            == len(self.inference_view_categories)
            == len(self.reference_block)
            == len(self.mismatched_block)
        ):
            raise ValueError("identifiers, category columns and both blocks must align")
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
            ("reference_block", self.reference_block),
            ("mismatched_block", self.mismatched_block),
        ):
            for position, row in enumerate(block):
                if len(row) != width:
                    raise ValueError(f"{name}[{position}] must have one column per category")
                if any(value not in (0, 1) for value in row):
                    raise ValueError(f"{name}[{position}] must contain indicator values only")
                if sum(row) != 1:
                    raise ValueError(f"{name}[{position}] must set exactly one indicator")

        if not self.affected_record_ids:
            raise ValueError("a mapping mismatch must affect at least one record")
        if len(set(self.affected_record_ids)) != len(self.affected_record_ids):
            raise ValueError("a record may be affected at most once")
        if list(self.affected_record_ids) != sorted(self.affected_record_ids):
            raise ValueError(
                "affected_record_ids must be in canonical sorted order so the ordered "
                "digest is reproducible"
            )
        missing = set(self.affected_record_ids) - set(self.record_ids)
        if missing:
            raise ValueError(f"affected records must exist in the source: {sorted(missing)}")
        if len(self.affected_record_ids) != self.provenance.affected_record_count:
            raise ValueError("affected identifier count must match its provenance")
        return self

    def artifact_sha256(self) -> str:
        """Digest every serialized field, including row and column ordering.

        This is the integrity digest for an exact artifact, and it is
        order-sensitive by design: permuting rows or columns produces a
        different experiment, so it must produce a different digest. No semantic
        digest accompanies it — nothing consumes one yet, and an unused digest is
        one more claim nobody checks.
        """

        return canonical_sha256(
            {
                "digest_schema_version": MISMATCH_ARTIFACT_DIGEST_SCHEMA_VERSION,
                "artifact": self.model_dump(mode="json"),
            }
        )


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #


def _resolve_category_pair(
    *, source: InferenceTransformSource, spec: EncoderMappingMismatchSpec
) -> tuple[str, str]:
    """Resolve the declared ranks against the frozen vocabulary."""

    source_category = source.vocabulary.category_for_rank(spec.source_rank)
    mapped_category = source.vocabulary.category_for_rank(spec.mapped_rank)
    if source_category == mapped_category:
        # Unreachable while the vocabulary rejects duplicate categories, because
        # rank-to-category is then a bijection. Kept because the guarantee it
        # relies on lives in a different model.
        _fail("the source and mapped ranks resolve to one category")
    if spec.source_category != source_category or spec.mapped_category != mapped_category:
        _fail("the declared category pair differs from the frozen training ranks")
    return source_category, mapped_category


def _inference_view(
    *, raw_categories: Sequence[str], source_category: str, mapped_category: str
) -> tuple[str, ...]:
    """Return what the encoder sees: the mapped value on source rows, raw elsewhere."""

    return tuple(
        mapped_category if category == source_category else category for category in raw_categories
    )


def apply_encoder_mapping_mismatch(
    *,
    source: InferenceTransformSource,
    spec: EncoderMappingMismatchSpec,
    slot: CandidateSlot,
) -> EncoderMappingMismatchResult:
    """Mis-encode one category of one feature at inference, and nothing else.

    The caller's objects are never mutated: the raw column is carried through
    untouched and the inference view is a new tuple. No encoder is refitted and
    no model is retrained, because neither is reachable from here.
    """

    source = _revalidated(source)
    spec = _revalidated(spec)
    slot = _validate_fault_slot(slot, spec=spec)

    if spec.parameters.target_feature != source.feature:
        _fail("the specification targets a feature the source does not carry")

    source_category, mapped_category = _resolve_category_pair(source=source, spec=spec)
    inference_view = _inference_view(
        raw_categories=source.raw_categories,
        source_category=source_category,
        mapped_category=mapped_category,
    )
    affected = tuple(
        sorted(
            record_id
            for record_id, category in zip(source.record_ids, source.raw_categories, strict=True)
            if category == source_category
        )
    )
    if not affected:
        _fail(
            "no evaluation record carries the source category, so the intervention would "
            "have no effect"
        )

    reference_block = source.indicator_block(source.raw_categories)
    mismatched_block = source.indicator_block(inference_view)
    if reference_block == mismatched_block:
        _fail("the declared mapping produces the same encoding, so it is not an intervention")

    column_order = source.vocabulary.encoder_column_order()
    provenance = EncoderMappingMismatchProvenance(
        schema_version=MISMATCH_PROVENANCE_SCHEMA_VERSION,
        intervention_type=MISMATCH_INTERVENTION_TYPE,
        mismatch_protocol_version=MISMATCH_PROTOCOL_VERSION,
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
        affected_record_count=len(affected),
        affected_ratio=len(affected) / source.record_count,
        column_count=len(column_order),
        vocabulary_sha256=source.vocabulary.canonical_sha256(),
        encoder_column_order_sha256=_ordered_digest(
            schema_version=CATEGORY_VOCABULARY_SCHEMA_VERSION,
            key="encoder_column_order",
            values=column_order,
        ),
        source_record_ids_sha256=source.record_ids_sha256(),
        source_membership_sha256=source.membership_sha256(),
        raw_categories_sha256=source.raw_categories_sha256(),
        inference_view_categories_sha256=_ordered_digest(
            schema_version=MISMATCH_PROVENANCE_SCHEMA_VERSION,
            key="inference_view_categories",
            values=inference_view,
        ),
        reference_block_sha256=_block_digest(
            schema_version=MISMATCH_PROVENANCE_SCHEMA_VERSION,
            key="reference_block",
            block=reference_block,
        ),
        mismatched_block_sha256=_block_digest(
            schema_version=MISMATCH_PROVENANCE_SCHEMA_VERSION,
            key="mismatched_block",
            block=mismatched_block,
        ),
        affected_record_ids_sha256=_ordered_digest(
            schema_version=MISMATCH_PROVENANCE_SCHEMA_VERSION,
            key="affected_record_ids",
            values=affected,
        ),
        fault_slot_sha256=_fault_slot_sha256(slot),
        declared_mapping_sha256=_declared_mapping_sha256(spec),
        attested_raw_feature_matrix_sha256=source.attested_raw_feature_matrix_sha256,
        attested_raw_target_sha256=source.attested_raw_target_sha256,
        attested_model_sha256=source.attested_model_sha256,
        attested_fitted_training_transform_sha256=(
            source.attested_fitted_training_transform_sha256
        ),
        attested_other_transform_config_sha256=source.attested_other_transform_config_sha256,
    )
    return EncoderMappingMismatchResult(
        record_ids=source.record_ids,
        raw_categories=source.raw_categories,
        inference_view_categories=inference_view,
        encoder_column_order=column_order,
        reference_block=reference_block,
        mismatched_block=mismatched_block,
        affected_record_ids=affected,
        provenance=provenance,
    )


# --------------------------------------------------------------------------- #
# Authoritative validation
# --------------------------------------------------------------------------- #


def validate_preprocessing_intervention(
    result: EncoderMappingMismatchResult,
    *,
    source: InferenceTransformSource,
    spec: EncoderMappingMismatchSpec,
    slot: CandidateSlot,
) -> EncoderMappingMismatchResult:
    """Recompute every claim a mismatch result makes and reject any mismatch.

    This is the one authoritative entry point. Every object is rebuilt from its
    own dump first, so anything assembled with ``model_copy`` or
    ``model_construct`` is re-validated rather than trusted, and every value that
    can be recomputed is recomputed rather than read.
    """

    result = _revalidated(result)
    source = _revalidated(source)
    spec = _revalidated(spec)
    slot = _validate_fault_slot(slot, spec=spec)
    provenance = result.provenance

    if spec.parameters.target_feature != source.feature:
        _fail("the specification targets a feature the source does not carry")

    # --- membership, ordering and the untouched raw column ------------------ #
    if result.record_ids != source.record_ids:
        _fail("result identifiers must match the source exactly, order included")
    if set(result.record_ids) != set(source.record_ids):
        _fail("result membership differs from the source")
    if result.raw_categories != source.raw_categories:
        _fail("the raw evaluation column must survive the intervention untouched")

    # --- the intervention itself -------------------------------------------- #
    source_category, mapped_category = _resolve_category_pair(source=source, spec=spec)
    expected_view = _inference_view(
        raw_categories=source.raw_categories,
        source_category=source_category,
        mapped_category=mapped_category,
    )
    if result.inference_view_categories != expected_view:
        _fail("the inference view does not match the declared rank mapping")

    expected_affected = tuple(
        sorted(
            record_id
            for record_id, category in zip(source.record_ids, source.raw_categories, strict=True)
            if category == source_category
        )
    )
    if result.affected_record_ids != expected_affected:
        _fail("affected identifiers do not match the rows carrying the source category")

    raw_by_id = dict(zip(result.record_ids, result.raw_categories, strict=True))
    view_by_id = dict(zip(result.record_ids, result.inference_view_categories, strict=True))
    expected_affected_set = set(expected_affected)
    for record_id in result.record_ids:
        if record_id in expected_affected_set:
            continue
        if view_by_id[record_id] != raw_by_id[record_id]:
            _fail(f"a record outside the declared source category changed: {record_id}")

    # --- the encoded blocks -------------------------------------------------- #
    expected_columns = source.vocabulary.encoder_column_order()
    if result.encoder_column_order != expected_columns:
        _fail("encoder column order does not match the frozen vocabulary")
    expected_reference = source.indicator_block(source.raw_categories)
    expected_mismatched = source.indicator_block(expected_view)
    if result.reference_block != expected_reference:
        _fail("the reference block does not match the frozen encoder applied to the raw column")
    if result.mismatched_block != expected_mismatched:
        _fail("the mismatched block does not match the frozen encoder applied to the view")
    if len(expected_reference) != len(expected_mismatched):
        _fail("the two blocks describe different numbers of rows")
    if result.reference_block == result.mismatched_block:
        _fail("the declared mapping produces the same encoding, so it is not an intervention")

    # --- scalar provenance --------------------------------------------------- #
    if provenance.source_rank != spec.source_rank or provenance.mapped_rank != spec.mapped_rank:
        _fail("provenance ranks differ from the specification")
    if provenance.injection_id != spec.injection_id:
        _fail("provenance injection_id differs from the specification")
    if (
        provenance.source_category != source_category
        or provenance.mapped_category != mapped_category
    ):
        _fail("provenance category pair differs from the frozen training ranks")
    if provenance.seed != spec.seed:
        _fail("provenance seed differs from the specification")
    if provenance.target_feature != source.feature:
        _fail("provenance target feature differs from the source")
    if provenance.transform_name != spec.parameters.transform_name:
        _fail("provenance transform name differs from the specification")
    if provenance.record_count != source.record_count:
        _fail("provenance record count differs from the source")
    if provenance.column_count != len(expected_columns):
        _fail("provenance column count differs from the frozen vocabulary")
    if provenance.affected_record_count != len(expected_affected):
        _fail("provenance affected count differs from the recomputed set")

    # --- recomputed digests -------------------------------------------------- #
    expected_digests: dict[str, str] = {
        "vocabulary_sha256": source.vocabulary.canonical_sha256(),
        "encoder_column_order_sha256": _ordered_digest(
            schema_version=CATEGORY_VOCABULARY_SCHEMA_VERSION,
            key="encoder_column_order",
            values=expected_columns,
        ),
        "source_record_ids_sha256": source.record_ids_sha256(),
        "source_membership_sha256": source.membership_sha256(),
        "raw_categories_sha256": source.raw_categories_sha256(),
        "inference_view_categories_sha256": _ordered_digest(
            schema_version=MISMATCH_PROVENANCE_SCHEMA_VERSION,
            key="inference_view_categories",
            values=expected_view,
        ),
        "reference_block_sha256": _block_digest(
            schema_version=MISMATCH_PROVENANCE_SCHEMA_VERSION,
            key="reference_block",
            block=expected_reference,
        ),
        "mismatched_block_sha256": _block_digest(
            schema_version=MISMATCH_PROVENANCE_SCHEMA_VERSION,
            key="mismatched_block",
            block=expected_mismatched,
        ),
        "affected_record_ids_sha256": _ordered_digest(
            schema_version=MISMATCH_PROVENANCE_SCHEMA_VERSION,
            key="affected_record_ids",
            values=expected_affected,
        ),
        "fault_slot_sha256": _fault_slot_sha256(slot),
        "declared_mapping_sha256": _declared_mapping_sha256(spec),
    }
    for field_name, expected in expected_digests.items():
        if getattr(provenance, field_name) != expected:
            _fail(f"provenance {field_name} does not match the recomputed digest")

    # --- attestations copied from the validated source ----------------------- #
    attested: dict[str, str] = {
        "attested_raw_feature_matrix_sha256": source.attested_raw_feature_matrix_sha256,
        "attested_raw_target_sha256": source.attested_raw_target_sha256,
        "attested_model_sha256": source.attested_model_sha256,
        "attested_fitted_training_transform_sha256": (
            source.attested_fitted_training_transform_sha256
        ),
        "attested_other_transform_config_sha256": source.attested_other_transform_config_sha256,
    }
    for field_name, declared in attested.items():
        if getattr(provenance, field_name) != declared:
            _fail(f"provenance {field_name} is not the value the source attested")

    return result
