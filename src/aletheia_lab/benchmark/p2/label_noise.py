"""Deterministic training-label corruption for the Phase 2 benchmark.

The mechanism changes the target labels of a deterministically selected subset of
training records and nothing else. Every other property of the experiment —
features, record identifiers, membership, ordering, preprocessing, model
specification, validation and test data — must survive untouched.

Three design choices carry that guarantee:

**The injector never receives features.** It takes record identifiers, binary
targets and digests of everything else. A function that has no feature matrix
cannot modify one, so that part of the one-factor invariant holds by construction
rather than by a check somebody could forget to run.

**Selection is a pure function of the seed and the record identifier.** Row
position, dictionary iteration order, interpreter start-up state and locale never
enter the computation, so the same seed selects the same records on any machine
and in any row ordering.

**Evaluator provenance and diagnosis evidence are different types.** The mutation
map lives in :class:`LabelCorruptionProvenance`; the diagnoser receives a
:class:`TargetQualityAudit` whose fields are numbers and one pinned protocol
identifier, so it cannot carry a record identifier or a label at all. A
vocabulary scan runs afterwards as defence in depth, not as the boundary.

Two error kinds are raised, and the difference matters to callers:

* a malformed *object* raises :class:`pydantic.ValidationError`, because model
  validators report through Pydantic;
* a malformed *relationship between objects* raises :class:`LabelNoiseError`,
  because procedural checks run outside any model.

The injector describes an intervention. It never decides whether that
intervention harmed the model: measured outcome, eligibility and family class are
produced later, from measurements this module never sees.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Final, Literal, NoReturn, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.identity import (
    SHA256_PATTERN,
    FlipDirection,
    LabelNoiseParameters,
    LabelNoiseScope,
    SelectionPolicy,
)
from aletheia_lab.benchmark.p2.validation import ContractViolation

LABEL_SOURCE_SCHEMA_VERSION: Final[Literal["p2-label-source/v1"]] = "p2-label-source/v1"
LABEL_SELECTION_SCHEMA_VERSION: Final[Literal["p2-label-selection/v1"]] = "p2-label-selection/v1"
LABEL_MUTATION_SCHEMA_VERSION: Final[Literal["p2-label-mutation-map/v1"]] = (
    "p2-label-mutation-map/v1"
)
LABEL_PROVENANCE_SCHEMA_VERSION: Final[Literal["p2-label-corruption/v1"]] = "p2-label-corruption/v1"
TARGET_QUALITY_AUDIT_SCHEMA_VERSION: Final[Literal["p2-target-quality-audit/v1"]] = (
    "p2-target-quality-audit/v1"
)
LABEL_ARTIFACT_DIGEST_SCHEMA_VERSION: Final[Literal["p2-label-artifact-digest/v1"]] = (
    "p2-label-artifact-digest/v1"
)
LABEL_SEMANTIC_DIGEST_SCHEMA_VERSION: Final[Literal["p2-label-semantic-digest/v1"]] = (
    "p2-label-semantic-digest/v1"
)

#: Rounding rule for the declared mutation count.
#:
#: The canonical contract requires a prespecified rounding rule but does not fix
#: one, so it is fixed here and versioned. ``ROUND_HALF_UP`` on an exact
#: ``Decimal`` product is used rather than ``round()`` or ``int(rate * n)``:
#: ``round`` applies banker's rounding (``round(2.5) == 2``) and binary floats
#: misrepresent decimal rates (``0.29 * 100 == 28.999999999999996``). Both would
#: make the mutation count depend on representation accidents.
MUTATION_COUNT_RULE: Final[Literal["decimal-round-half-up/v1"]] = "decimal-round-half-up/v1"

#: Interval method for the audited disagreement rate.
#:
#: Wilson score rather than the normal approximation: it stays inside [0, 1] and
#: remains usable at the small counts a low flip rate produces, and it needs only
#: ``math.sqrt`` so the bounds are reproducible without a numerical library.
AUDIT_INTERVAL_METHOD: Final[Literal["wilson-score/95"]] = "wilson-score/95"

#: Two-sided 95% standard normal quantile, pinned so the bounds never depend on
#: an external library version.
_WILSON_Z: Final[float] = 1.959963984540054

#: The one protocol identifier a diagnosis-facing audit may carry. Pinning it
#: removes the last free-text field from the diagnosis surface.
AuditProtocolVersion = Literal["target-quality-audit/v1"]

#: The alpha slice corrupts binary targets only.
BINARY_LABELS: Final[frozenset[int]] = frozenset({0, 1})

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]

_MAX_RECORDS: Final[int] = 5_000_000
_FLOAT_TOLERANCE: Final[float] = 1e-12

#: Vocabulary that must never reach a diagnosis-facing payload. This is a second
#: line of defence; the primary boundary is that evaluator data and diagnosis
#: data are separate types with no field able to hold the sensitive values.
_EVALUATOR_VOCABULARY: Final[tuple[str, ...]] = (
    "label_noise",
    "flip",
    "corrupt",
    "mutation",
    "mutated",
    "ground_truth",
    "answer_key",
    "expected_behavior",
    "eligible_failure",
    "seed",
    "injection",
    "evaluator",
    "hidden_cause",
    "cause_label",
    "original_label",
)

#: Anything shaped like a record identifier. Diagnosis payloads describe
#: aggregates, so a run of digits long enough to index a record is suspicious.
_RECORD_IDENTIFIER_SHAPE: Final[re.Pattern[str]] = re.compile(r"\d{3,}")

_NON_ALPHANUMERIC: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class LabelNoiseError(ContractViolation):
    """Raised when label-noise artifacts disagree with one another.

    Malformed single objects raise ``pydantic.ValidationError`` instead: model
    validators report through Pydantic, which wraps every ``ValueError`` they
    raise. Keeping the two kinds apart lets callers tell "this object is
    invalid" from "these objects do not belong together".
    """


def _fail(message: str) -> NoReturn:
    """Reject a cross-object relationship."""

    raise LabelNoiseError(message)


def _revalidated(model: _ModelT) -> _ModelT:
    """Re-run validators even when a caller built the object unsafely.

    ``model_copy(update=...)`` and ``model_construct()`` skip validation in
    Pydantic v2, so any object arriving at a trust boundary is rebuilt from its
    own dump before it is trusted.
    """

    return type(model).model_validate(model.model_dump())


def _fold(text: str) -> str:
    """Collapse a string to a comparable form for the vocabulary scan.

    Compatibility normalization plus case folding plus separator collapse means
    ``FLIP-audit``, ``flip audit`` and ``Flip.Audit`` all reduce to the same
    token sequence, so a scan cannot be evaded by punctuation.
    """

    normalized = unicodedata.normalize("NFKC", text).casefold()
    return _NON_ALPHANUMERIC.sub("_", normalized).strip("_")


class _StrictFrozenModel(BaseModel):
    """Reject unknown fields, implicit coercion and post-construction mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _labelled_records_digest(
    *, schema_version: str, record_ids: Sequence[str], labels: Sequence[int]
) -> str:
    """Digest a labelled record set independently of its listing order.

    Two orderings of the same records describe the same experiment, so the
    digest sorts on the identifier before hashing. Order is proved separately by
    :meth:`LabelNoiseSource.record_ids_sha256`, which is order-sensitive on
    purpose.
    """

    pairs: list[dict[str, object]] = [
        {"record_id": record_id, "label": label}
        for record_id, label in zip(record_ids, labels, strict=True)
    ]
    pairs.sort(key=lambda item: str(item["record_id"]))
    return canonical_sha256({"schema_version": schema_version, "labelled_records": pairs})


def _membership_digest(*, schema_version: str, record_ids: Iterable[str]) -> str:
    """Digest which records exist, independently of their listing order."""

    return canonical_sha256({"schema_version": schema_version, "membership": sorted(record_ids)})


# --------------------------------------------------------------------------- #
# Source
# --------------------------------------------------------------------------- #


class LabelNoiseSource(_StrictFrozenModel):
    """The training targets a corruption may touch, plus digests of what it may not.

    Features are deliberately absent: the injector cannot alter a feature matrix
    it never receives.

    ``attested_*`` fields name their own limitation. They describe artifacts this
    module cannot see, so it can only carry them forward; the caller that holds
    those artifacts is responsible for proving they are unchanged. Everything
    without that prefix is recomputed by :func:`validate_label_corruption`.
    """

    schema_version: Literal["p2-label-source/v1"]
    split: LabelNoiseScope
    record_ids: tuple[str, ...]
    targets: tuple[int, ...]
    attested_feature_matrix_sha256: Sha256
    attested_preprocessing_specification_sha256: Sha256
    attested_model_specification_sha256: Sha256

    @model_validator(mode="after")
    def _source_is_well_formed(self) -> LabelNoiseSource:
        if not self.record_ids:
            raise ValueError("label-noise source must contain at least one training record")
        if len(self.record_ids) > _MAX_RECORDS:
            raise ValueError("label-noise source exceeds the supported record count")
        if len(self.record_ids) != len(self.targets):
            raise ValueError(
                "record_ids and targets must align: "
                f"{len(self.record_ids)} ids against {len(self.targets)} targets"
            )
        seen: set[str] = set()
        for position, record_id in enumerate(self.record_ids):
            if not record_id or record_id != record_id.strip():
                raise ValueError(f"record ID at position {position} must be non-blank and trimmed")
            if record_id != unicodedata.normalize("NFC", record_id):
                raise ValueError(f"record ID at position {position} must be Unicode NFC")
            if record_id in seen:
                raise ValueError(
                    "record IDs must be unique; duplicates cannot anchor a mutation map"
                )
            seen.add(record_id)
        for position, label in enumerate(self.targets):
            if label not in BINARY_LABELS:
                raise ValueError(
                    f"target at position {position} must be binary 0 or 1, got {label!r}"
                )
        return self

    @property
    def record_count(self) -> int:
        return len(self.record_ids)

    def record_ids_sha256(self) -> str:
        """Digest the identifier sequence, order included."""

        return canonical_sha256(
            {"schema_version": self.schema_version, "record_ids": list(self.record_ids)}
        )

    def membership_sha256(self) -> str:
        """Digest which records exist, order excluded."""

        return _membership_digest(schema_version=self.schema_version, record_ids=self.record_ids)

    def targets_sha256(self) -> str:
        """Digest the labelled record set, order excluded."""

        return _labelled_records_digest(
            schema_version=self.schema_version,
            record_ids=self.record_ids,
            labels=self.targets,
        )

    def label_counts(self) -> dict[str, int]:
        positives = sum(1 for label in self.targets if label == 1)
        return {"positive": positives, "negative": self.record_count - positives}


# --------------------------------------------------------------------------- #
# Specification
# --------------------------------------------------------------------------- #


class LabelCorruptionSpec(_StrictFrozenModel):
    """One prespecified corruption: which parameters, which seed."""

    schema_version: Literal["p2-label-corruption-spec/v1"] = "p2-label-corruption-spec/v1"
    parameters: LabelNoiseParameters
    seed: int = Field(ge=0)

    @model_validator(mode="after")
    def _spec_describes_a_corruption(self) -> LabelCorruptionSpec:
        parameters = self.parameters
        if parameters.flip_rate <= 0.0:
            raise ValueError(
                "a corruption requires a positive flip rate; zero is reserved for the "
                "semantics-preserving serialization control"
            )
        if parameters.flip_direction != "symmetric":
            raise ValueError("the alpha slice implements symmetric binary flips only")
        if parameters.selection_policy != "seeded_record_hash":
            raise ValueError("the alpha slice implements seeded record-hash selection only")
        if parameters.scope != "train":
            raise ValueError("label corruption may only touch the training split")
        return self


# --------------------------------------------------------------------------- #
# Selection, count and interval
# --------------------------------------------------------------------------- #


def selection_digest(*, seed: int, record_id: str) -> str:
    """Return the deterministic ranking digest for one record."""

    return canonical_sha256(
        {
            "selection_schema_version": LABEL_SELECTION_SCHEMA_VERSION,
            "seed": seed,
            "record_id": record_id,
        }
    )


def mutation_count(*, flip_rate: float, record_count: int) -> int:
    """Return the exact number of labels a declared rate mutates."""

    if record_count <= 0:
        _fail("mutation count requires at least one record")
    if not 0.0 <= flip_rate <= 0.5:
        _fail(f"flip rate must lie in [0, 0.5], got {flip_rate!r}")
    exact = Decimal(str(flip_rate)) * Decimal(record_count)
    return int(exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def wilson_interval(*, successes: int, trials: int) -> tuple[float, float]:
    """Return the two-sided 95% Wilson score interval for a proportion."""

    if trials <= 0:
        _fail("a confidence interval requires at least one trial")
    if not 0 <= successes <= trials:
        _fail(f"successes must lie in [0, {trials}], got {successes}")
    proportion = successes / trials
    z_squared = _WILSON_Z * _WILSON_Z
    denominator = 1.0 + z_squared / trials
    centre = (proportion + z_squared / (2.0 * trials)) / denominator
    spread = (
        _WILSON_Z
        / denominator
        * math.sqrt(proportion * (1.0 - proportion) / trials + z_squared / (4.0 * trials * trials))
    )
    return (max(0.0, centre - spread), min(1.0, centre + spread))


def select_record_ids(*, source: LabelNoiseSource, seed: int, count: int) -> tuple[str, ...]:
    """Return the ``count`` lowest-ranked record IDs for ``seed``."""

    if count < 0:
        _fail("selection count must not be negative")
    if count > source.record_count:
        _fail(f"cannot select {count} records from a source of {source.record_count}")
    ranked = sorted(
        source.record_ids,
        key=lambda record_id: (selection_digest(seed=seed, record_id=record_id), record_id),
    )
    return tuple(ranked[:count])


# --------------------------------------------------------------------------- #
# Mutation map — evaluator only
# --------------------------------------------------------------------------- #


class MutationEntry(_StrictFrozenModel):
    """One label that changed, with the value on both sides. Evaluator only."""

    record_id: str = Field(min_length=1)
    original_label: int
    mutated_label: int

    @model_validator(mode="after")
    def _entry_is_a_binary_flip(self) -> MutationEntry:
        if self.original_label not in BINARY_LABELS or self.mutated_label not in BINARY_LABELS:
            raise ValueError("mutation entries record binary labels only")
        if self.original_label == self.mutated_label:
            raise ValueError("a mutation entry must record an actual change")
        return self


class MutationMap(_StrictFrozenModel):
    """The exact set of mutated records. Never reaches a diagnosis payload."""

    schema_version: Literal["p2-label-mutation-map/v1"]
    entries: tuple[MutationEntry, ...]

    @model_validator(mode="after")
    def _entries_are_unique(self) -> MutationMap:
        identifiers = [entry.record_id for entry in self.entries]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("a record may be mutated at most once")
        return self

    @property
    def count(self) -> int:
        return len(self.entries)

    def record_ids(self) -> frozenset[str]:
        return frozenset(entry.record_id for entry in self.entries)

    def canonical_sha256(self) -> str:
        """Digest the mutation map independently of entry order."""

        entries: list[dict[str, object]] = [
            {
                "record_id": entry.record_id,
                "original_label": entry.original_label,
                "mutated_label": entry.mutated_label,
            }
            for entry in self.entries
        ]
        entries.sort(key=lambda item: str(item["record_id"]))
        return canonical_sha256({"schema_version": self.schema_version, "entries": entries})


# --------------------------------------------------------------------------- #
# Diagnosis-facing evidence
# --------------------------------------------------------------------------- #


class TargetQualityAudit(_StrictFrozenModel):
    """Aggregate label-quality evidence a diagnoser may see.

    Every field is a count, a derived rate or a pinned protocol identifier. There
    is no field capable of carrying a record identifier, a label value, a seed or
    a declared rate, so the boundary is structural rather than a matter of
    remembering to redact.
    """

    schema_version: Literal["p2-target-quality-audit/v1"]
    audited_record_count: int = Field(ge=1)
    disagreeing_record_count: int = Field(ge=0)
    disagreement_rate: float = Field(ge=0.0, le=1.0)
    disagreement_rate_lower_bound: float = Field(ge=0.0, le=1.0)
    disagreement_rate_upper_bound: float = Field(ge=0.0, le=1.0)
    interval_method: Literal["wilson-score/95"]
    protocol_version: AuditProtocolVersion

    @model_validator(mode="after")
    def _rate_and_interval_are_derived(self) -> TargetQualityAudit:
        if self.disagreeing_record_count > self.audited_record_count:
            raise ValueError("disagreeing records cannot exceed audited records")
        expected_rate = self.disagreeing_record_count / self.audited_record_count
        if abs(self.disagreement_rate - expected_rate) > _FLOAT_TOLERANCE:
            raise ValueError("disagreement_rate must be derived from the two counts")
        lower, upper = wilson_interval(
            successes=self.disagreeing_record_count, trials=self.audited_record_count
        )
        if abs(self.disagreement_rate_lower_bound - lower) > _FLOAT_TOLERANCE:
            raise ValueError("the lower bound must be the Wilson score bound for these counts")
        if abs(self.disagreement_rate_upper_bound - upper) > _FLOAT_TOLERANCE:
            raise ValueError("the upper bound must be the Wilson score bound for these counts")
        if self.disagreement_rate_lower_bound > self.disagreement_rate_upper_bound:
            raise ValueError("the interval bounds are inverted")
        return self


class TargetDistributionComparison(_StrictFrozenModel):
    """Observed label balance before and after, without naming a cause."""

    schema_version: Literal["p2-target-distribution-comparison/v1"] = (
        "p2-target-distribution-comparison/v1"
    )
    reference_positive_count: int = Field(ge=0)
    reference_negative_count: int = Field(ge=0)
    observed_positive_count: int = Field(ge=0)
    observed_negative_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _totals_match(self) -> TargetDistributionComparison:
        reference = self.reference_positive_count + self.reference_negative_count
        observed = self.observed_positive_count + self.observed_negative_count
        if reference != observed:
            raise ValueError("a label intervention must not change the number of records")
        if reference == 0:
            raise ValueError("a distribution comparison requires at least one record")
        return self


class LabelNoiseProjection(_StrictFrozenModel):
    """The complete diagnosis-facing view of a label intervention."""

    schema_version: Literal["p2-label-projection/v1"] = "p2-label-projection/v1"
    sample_size: int = Field(ge=1)
    target_distribution_comparison: TargetDistributionComparison
    target_quality_audit: TargetQualityAudit


# --------------------------------------------------------------------------- #
# Provenance — evaluator only
# --------------------------------------------------------------------------- #


class LabelCorruptionProvenance(_StrictFrozenModel):
    """Everything an evaluator needs to reproduce and audit one corruption."""

    schema_version: Literal["p2-label-corruption/v1"]
    intervention_type: Literal["training_target_label_corruption"]
    mutation_count_rule: Literal["decimal-round-half-up/v1"]
    selection_policy: SelectionPolicy
    flip_direction: FlipDirection
    scope: LabelNoiseScope
    seed: int = Field(ge=0)
    declared_flip_rate: float = Field(gt=0.0, le=0.5)
    achieved_flip_rate: float = Field(ge=0.0, le=1.0)
    record_count: int = Field(ge=1)
    mutation_count: int = Field(ge=1)
    source_record_ids_sha256: Sha256
    source_membership_sha256: Sha256
    source_targets_sha256: Sha256
    mutated_targets_sha256: Sha256
    attested_feature_matrix_sha256: Sha256
    attested_preprocessing_specification_sha256: Sha256
    attested_model_specification_sha256: Sha256
    mutation_map_sha256: Sha256

    @model_validator(mode="after")
    def _counts_and_rates_agree(self) -> LabelCorruptionProvenance:
        if self.mutation_count > self.record_count:
            raise ValueError("mutation count cannot exceed the record count")
        expected_count = int(
            (Decimal(str(self.declared_flip_rate)) * Decimal(self.record_count)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        if self.mutation_count != expected_count:
            raise ValueError(
                "mutation_count must follow the declared rate and rounding rule; "
                f"expected {expected_count}, recorded {self.mutation_count}"
            )
        expected_rate = self.mutation_count / self.record_count
        if abs(self.achieved_flip_rate - expected_rate) > _FLOAT_TOLERANCE:
            raise ValueError(
                "achieved_flip_rate must be derived from the mutation and record counts"
            )
        if self.source_targets_sha256 == self.mutated_targets_sha256:
            raise ValueError("a corruption must change the labelled record set")
        return self


class LabelCorruptionResult(_StrictFrozenModel):
    """A completed corruption: mutated targets, evaluator provenance, safe view.

    The result describes what was done. It carries no measured outcome,
    eligibility or family class; those are decided later from measurements this
    module never performs.
    """

    schema_version: Literal["p2-label-corruption-result/v1"] = "p2-label-corruption-result/v1"
    record_ids: tuple[str, ...]
    mutated_targets: tuple[int, ...]
    mutation_map: MutationMap
    provenance: LabelCorruptionProvenance
    projection: LabelNoiseProjection

    @model_validator(mode="after")
    def _result_is_internally_aligned(self) -> LabelCorruptionResult:
        if len(self.record_ids) != len(self.mutated_targets):
            raise ValueError("result identifiers and targets must align")
        if len(self.record_ids) != self.provenance.record_count:
            raise ValueError("result record count must match its provenance")
        if self.mutation_map.count != self.provenance.mutation_count:
            raise ValueError("mutation map size must match the recorded mutation count")
        return self

    def artifact_sha256(self) -> str:
        """Digest every serialized field, including source row ordering.

        This is the integrity digest for an exact artifact. Reordering the source
        changes ``record_ids`` and ``source_record_ids_sha256``, so it must change
        this digest even when the intervention is semantically equivalent.
        """

        return canonical_sha256(
            {
                "digest_schema_version": LABEL_ARTIFACT_DIGEST_SCHEMA_VERSION,
                "artifact": self.model_dump(mode="json"),
            }
        )

    def semantic_sha256(self) -> str:
        """Digest the intervention independently of source listing order.

        Semantic identity deliberately excludes only
        ``source_record_ids_sha256``. Membership, source labels, mutated labels,
        mutation map, specification, attestations and diagnosis projection
        remain bound. The full order-sensitive artifact is independently bound
        by :meth:`artifact_sha256`.
        """

        semantic_provenance = self.provenance.model_dump(mode="json")
        semantic_provenance.pop("source_record_ids_sha256")
        return canonical_sha256(
            {
                "digest_schema_version": LABEL_SEMANTIC_DIGEST_SCHEMA_VERSION,
                "labelled_records": _labelled_records_digest(
                    schema_version=self.schema_version,
                    record_ids=self.record_ids,
                    labels=self.mutated_targets,
                ),
                "mutation_map_sha256": self.mutation_map.canonical_sha256(),
                "provenance": semantic_provenance,
                "projection": self.projection.model_dump(mode="json"),
            }
        )


# --------------------------------------------------------------------------- #
# Injection
# --------------------------------------------------------------------------- #


def apply_label_corruption(
    *, source: LabelNoiseSource, spec: LabelCorruptionSpec
) -> LabelCorruptionResult:
    """Corrupt a deterministic subset of training labels.

    The source is never modified: the mutated sequence is built as a new tuple.
    """

    source = _revalidated(source)
    spec = _revalidated(spec)

    if source.split != spec.parameters.scope:
        _fail(
            f"source split {source.split!r} does not match the declared scope "
            f"{spec.parameters.scope!r}"
        )

    count = mutation_count(flip_rate=spec.parameters.flip_rate, record_count=source.record_count)
    if count == 0:
        _fail(
            "the declared rate mutates no records at this sample size; "
            "the candidate has no effective intervention"
        )

    selected = select_record_ids(source=source, seed=spec.seed, count=count)
    selected_set = frozenset(selected)

    original_by_id = dict(zip(source.record_ids, source.targets, strict=True))
    mutated_targets = tuple(
        1 - label if record_id in selected_set else label
        for record_id, label in zip(source.record_ids, source.targets, strict=True)
    )

    entries = tuple(
        MutationEntry(
            record_id=record_id,
            original_label=original_by_id[record_id],
            mutated_label=1 - original_by_id[record_id],
        )
        for record_id in sorted(selected)
    )
    mutation_map = MutationMap(schema_version=LABEL_MUTATION_SCHEMA_VERSION, entries=entries)

    mutated_positive = sum(1 for label in mutated_targets if label == 1)
    mutated_negative = source.record_count - mutated_positive
    if mutated_positive == 0 or mutated_negative == 0:
        _fail("the corruption erased a class from the training split")

    reference_counts = source.label_counts()
    lower, upper = wilson_interval(successes=count, trials=source.record_count)

    provenance = LabelCorruptionProvenance(
        schema_version=LABEL_PROVENANCE_SCHEMA_VERSION,
        intervention_type="training_target_label_corruption",
        mutation_count_rule=MUTATION_COUNT_RULE,
        selection_policy=spec.parameters.selection_policy,
        flip_direction=spec.parameters.flip_direction,
        scope=spec.parameters.scope,
        seed=spec.seed,
        declared_flip_rate=spec.parameters.flip_rate,
        achieved_flip_rate=count / source.record_count,
        record_count=source.record_count,
        mutation_count=count,
        source_record_ids_sha256=source.record_ids_sha256(),
        source_membership_sha256=source.membership_sha256(),
        source_targets_sha256=source.targets_sha256(),
        mutated_targets_sha256=_labelled_records_digest(
            schema_version=source.schema_version,
            record_ids=source.record_ids,
            labels=mutated_targets,
        ),
        attested_feature_matrix_sha256=source.attested_feature_matrix_sha256,
        attested_preprocessing_specification_sha256=(
            source.attested_preprocessing_specification_sha256
        ),
        attested_model_specification_sha256=source.attested_model_specification_sha256,
        mutation_map_sha256=mutation_map.canonical_sha256(),
    )

    projection = LabelNoiseProjection(
        sample_size=source.record_count,
        target_distribution_comparison=TargetDistributionComparison(
            reference_positive_count=reference_counts["positive"],
            reference_negative_count=reference_counts["negative"],
            observed_positive_count=mutated_positive,
            observed_negative_count=mutated_negative,
        ),
        target_quality_audit=TargetQualityAudit(
            schema_version=TARGET_QUALITY_AUDIT_SCHEMA_VERSION,
            audited_record_count=source.record_count,
            disagreeing_record_count=count,
            disagreement_rate=count / source.record_count,
            disagreement_rate_lower_bound=lower,
            disagreement_rate_upper_bound=upper,
            interval_method=AUDIT_INTERVAL_METHOD,
            protocol_version="target-quality-audit/v1",
        ),
    )

    return LabelCorruptionResult(
        record_ids=source.record_ids,
        mutated_targets=mutated_targets,
        mutation_map=mutation_map,
        provenance=provenance,
        projection=projection,
    )


# --------------------------------------------------------------------------- #
# Authoritative validation
# --------------------------------------------------------------------------- #


def _assert_projection_is_diagnosis_safe(payload: object, path: str = "$") -> None:
    """Reject evaluator vocabulary or identifier shapes in a diagnosis payload."""

    if isinstance(payload, Mapping):
        for raw_key, nested in payload.items():
            key = str(raw_key)
            if any(marker in _fold(key) for marker in _EVALUATOR_VOCABULARY):
                _fail(f"diagnosis projection exposes evaluator vocabulary at {path}.{key}")
            _assert_projection_is_diagnosis_safe(nested, f"{path}.{key}")
        return
    if isinstance(payload, Sequence) and not isinstance(payload, str | bytes):
        for index, nested in enumerate(payload):
            _assert_projection_is_diagnosis_safe(nested, f"{path}[{index}]")
        return
    if isinstance(payload, str):
        folded = _fold(payload)
        if any(marker in folded for marker in _EVALUATOR_VOCABULARY):
            _fail(f"diagnosis projection exposes evaluator vocabulary at {path}")
        if _RECORD_IDENTIFIER_SHAPE.search(payload):
            _fail(f"diagnosis projection exposes an identifier-shaped value at {path}")


def validate_label_corruption(
    result: LabelCorruptionResult,
    *,
    source: LabelNoiseSource,
    spec: LabelCorruptionSpec,
) -> LabelCorruptionResult:
    """Recompute every derived value in ``result`` from ``source`` and ``spec``.

    This is the single entry point for trusting a corruption artifact. It assumes
    nothing about how the objects were built and rebuilds each from its own dump
    first, so an object assembled with ``model_copy(update=...)`` or
    ``model_construct()`` cannot smuggle an unvalidated field past the boundary.

    Returns the revalidated result so callers can bind "validated" to a value
    rather than to the memory of having called this function.
    """

    result = _revalidated(result)
    source = _revalidated(source)
    spec = _revalidated(spec)

    provenance = result.provenance

    # 1. The intervention matches its declared specification.
    if provenance.seed != spec.seed:
        _fail("provenance seed differs from the specification")
    if abs(provenance.declared_flip_rate - spec.parameters.flip_rate) > _FLOAT_TOLERANCE:
        _fail("provenance flip rate differs from the specification")
    if provenance.selection_policy != spec.parameters.selection_policy:
        _fail("provenance selection policy differs from the specification")
    if provenance.flip_direction != spec.parameters.flip_direction:
        _fail("provenance flip direction differs from the specification")
    if provenance.scope != spec.parameters.scope or source.split != spec.parameters.scope:
        _fail("provenance scope differs from the specification or the source split")

    # 2. The source is the one the provenance claims. The first three digests are
    #    recomputed here; the attested ones describe artifacts this module cannot
    #    see and are only checked for consistency with the source record.
    if provenance.source_record_ids_sha256 != source.record_ids_sha256():
        _fail("provenance record-ID digest does not match the source")
    if provenance.source_membership_sha256 != source.membership_sha256():
        _fail("provenance membership digest does not match the source")
    if provenance.source_targets_sha256 != source.targets_sha256():
        _fail("provenance target digest does not match the source")
    for field_name in (
        "attested_feature_matrix_sha256",
        "attested_preprocessing_specification_sha256",
        "attested_model_specification_sha256",
    ):
        if getattr(provenance, field_name) != getattr(source, field_name):
            _fail(f"provenance {field_name} does not match the source")
    if provenance.record_count != source.record_count:
        _fail("provenance record count does not match the source")

    # 3. Identifiers and their order are untouched.
    if result.record_ids != source.record_ids:
        _fail("a label intervention must not add, remove or reorder records")

    # 4. The mutation count follows the declared rate, recomputed here.
    expected_count = mutation_count(
        flip_rate=spec.parameters.flip_rate, record_count=source.record_count
    )
    if expected_count == 0:
        _fail("the declared rate mutates no records at this sample size")
    if provenance.mutation_count != expected_count:
        _fail(
            f"mutation count must be {expected_count} for the declared rate, "
            f"provenance records {provenance.mutation_count}"
        )
    if result.mutation_map.count != expected_count:
        _fail("mutation map size does not match the recomputed mutation count")

    # 5. The selected records are the ones this seed selects, recomputed here.
    expected_selection = frozenset(
        select_record_ids(source=source, seed=spec.seed, count=expected_count)
    )
    if result.mutation_map.record_ids() != expected_selection:
        _fail("the mutation map does not match the deterministic selection for this seed")

    # 6. Exactly the selected labels changed, and every one of them flipped.
    original_by_id = dict(zip(source.record_ids, source.targets, strict=True))
    mutated_by_id = dict(zip(result.record_ids, result.mutated_targets, strict=True))
    changed: set[str] = set()
    for record_id, original in original_by_id.items():
        mutated = mutated_by_id[record_id]
        if mutated not in BINARY_LABELS:
            _fail("a mutated target is not binary")
        if mutated != original:
            changed.add(record_id)
    if changed != expected_selection:
        _fail("the set of changed labels differs from the deterministic selection")
    for entry in result.mutation_map.entries:
        if original_by_id[entry.record_id] != entry.original_label:
            _fail("mutation map original label disagrees with the source")
        if mutated_by_id[entry.record_id] != entry.mutated_label:
            _fail("mutation map mutated label disagrees with the result")
        if entry.mutated_label != 1 - entry.original_label:
            _fail("a symmetric flip must invert the binary label")

    # 7. Digests over the produced sequences, recomputed here.
    if provenance.mutation_map_sha256 != result.mutation_map.canonical_sha256():
        _fail("provenance mutation-map digest does not match the mutation map")
    mutated_digest = _labelled_records_digest(
        schema_version=source.schema_version,
        record_ids=result.record_ids,
        labels=result.mutated_targets,
    )
    if provenance.mutated_targets_sha256 != mutated_digest:
        _fail("provenance mutated-target digest does not match the produced targets")

    # 8. No class was erased.
    positives = sum(1 for label in result.mutated_targets if label == 1)
    if positives == 0 or positives == len(result.mutated_targets):
        _fail("the corruption erased a class from the training split")

    # 9. The diagnosis projection reports the observed data and nothing else.
    _validate_projection(result=result, source=source)

    return result


def _validate_projection(*, result: LabelCorruptionResult, source: LabelNoiseSource) -> None:
    projection = result.projection
    comparison = projection.target_distribution_comparison
    reference = source.label_counts()
    observed_positive = sum(1 for label in result.mutated_targets if label == 1)

    if projection.sample_size != source.record_count:
        _fail("projection sample size does not match the source")
    if (
        comparison.reference_positive_count != reference["positive"]
        or comparison.reference_negative_count != reference["negative"]
    ):
        _fail("projected reference distribution does not match the source")
    if (
        comparison.observed_positive_count != observed_positive
        or comparison.observed_negative_count != source.record_count - observed_positive
    ):
        _fail("projected observed distribution does not match the mutated targets")

    audit = projection.target_quality_audit
    if audit.audited_record_count != source.record_count:
        _fail("audited record count does not match the source")
    if audit.disagreeing_record_count != result.mutation_map.count:
        _fail("audited disagreement count does not match the mutation map")

    lower, upper = wilson_interval(successes=result.mutation_map.count, trials=source.record_count)
    if abs(audit.disagreement_rate_lower_bound - lower) > _FLOAT_TOLERANCE:
        _fail("audited interval lower bound does not match the recomputed Wilson bound")
    if abs(audit.disagreement_rate_upper_bound - upper) > _FLOAT_TOLERANCE:
        _fail("audited interval upper bound does not match the recomputed Wilson bound")

    _assert_projection_is_diagnosis_safe(projection.model_dump(mode="json"))


def diagnosis_projection(
    result: LabelCorruptionResult,
    *,
    source: LabelNoiseSource,
    spec: LabelCorruptionSpec,
) -> LabelNoiseProjection:
    """Return the only part of a corruption a diagnoser may receive.

    ``source`` and ``spec`` are required rather than optional: a projection is
    trustworthy only if the artifact it came from has been validated against the
    inputs that produced it. Making the arguments mandatory removes the failure
    mode where a caller exports a projection without ever validating its parent.
    """

    validated = validate_label_corruption(result, source=source, spec=spec)
    return validated.projection


def selected_record_ids(entries: Iterable[MutationEntry]) -> tuple[str, ...]:
    """Return mutated identifiers in canonical order. Evaluator-side helper."""

    return tuple(sorted(entry.record_id for entry in entries))
