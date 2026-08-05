"""Phase 2 native categorical data drift.

Phase 1 already shifts the marginal distribution of one categorical feature, and
the research meaning of that intervention is unchanged here: resample rows
within each category so the feature follows a predeclared target distribution,
leaving the within-category conditional structure of the other columns alone.

What is *not* reused is the Phase 1 artifact. A ``p1-cases/5`` case, a
``p1-family-`` identifier and a Phase 1 canonical hash belong to a different
generation with a different serialization; accepting one here as Phase 2
evidence would merge two identity namespaces that the kernel keeps apart on
purpose. This module therefore produces Phase 2 artifacts, bound to Phase 2
identity, digested with :mod:`aletheia_lab.benchmark.p2.canonical`, and it has
no code path that can read a Phase 1 artifact at all.

Five decisions make the intervention checkable.

**The injector never receives a feature matrix, a target vector or a model.**
It takes record identifiers, the values of one categorical column, and digests
of everything else. The later measurement boundary receives frozen clean
labels and prediction vectors solely to score the exact resampled occurrences;
it still cannot retrain a model or alter a target.

**Apportionment is exact and named.** Category counts come from the
largest-remainder method with a category-name tiebreak, so the output batch is
exactly the requested size instead of drifting with per-category rounding, and
two runs of the same specification apportion identically.

**Row selection is a pure function of the seed and the record identifier.**
Phase 1 draws rows with a NumPy generator; Phase 2 ranks each candidate row by a
canonical digest of ``(seed, injection_id, category, record_id)`` instead. The
result is reproducible without depending on a bit-generator version, and it is
independent of dictionary iteration order and of ``PYTHONHASHSEED``.

**The selected rows are fingerprinted, with multiplicity.** Resampling draws
with replacement, so a set digest would hide how often a row was drawn. The
fingerprint binds the ordered batch.

**The module owns no research verdict.** There is no field for eligibility,
family class or cause. It derives metrics and a technical control status, then
stops at validity review because guardrail harm thresholds are still
alpha-provisional.

Two error kinds are raised, matching the other Phase 2 mechanisms:

* a malformed *object* raises :class:`pydantic.ValidationError`;
* a malformed *relationship between objects* raises :class:`DataDriftError`.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Annotated, Final, Literal, NoReturn, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.benchmark.p2.binary_evaluation import (
    METRIC_PROTOCOL_VERSION,
    METRIC_SNAPSHOT_SCHEMA_VERSION,
    MINORITY_LABEL,
    PRIMARY_ACCURACY_THRESHOLD,
    PRIMARY_METRIC,
    ZERO_DIVISION_POLICY,
    BinaryMetricSnapshot,
    CleanTestSet,
    PredictionVector,
    confusion_for,
    derived_primary_outcome,
    metric_snapshot,
)
from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import CandidateSlot
from aletheia_lab.benchmark.p2.identity import (
    SHA256_PATTERN,
    SLOT_ID_PATTERN,
    DataDriftParameters,
)
from aletheia_lab.benchmark.p2.validation import ContractViolation, validate_frozen_alpha_slot

# The distribution and PSI formulas are imported rather than restated. They are
# pure functions of two mappings, they carry no Phase 1 artifact or identity,
# and a second copy would be free to drift away from the one Phase 1 reports.
from aletheia_lab.benchmark.signals import (
    categorical_distribution,
    population_stability_index,
)

# --------------------------------------------------------------------------- #
# Schema and protocol versions
# --------------------------------------------------------------------------- #

DRIFT_SOURCE_SCHEMA_VERSION: Final[Literal["p2-drift-evaluation-source/v1"]] = (
    "p2-drift-evaluation-source/v1"
)
DRIFT_SPEC_SCHEMA_VERSION: Final[Literal["p2-categorical-drift-spec/v1"]] = (
    "p2-categorical-drift-spec/v1"
)
DRIFT_PROVENANCE_SCHEMA_VERSION: Final[Literal["p2-categorical-drift/v1"]] = (
    "p2-categorical-drift/v1"
)
DRIFT_RESULT_SCHEMA_VERSION: Final[Literal["p2-categorical-drift-result/v1"]] = (
    "p2-categorical-drift-result/v1"
)
DRIFT_ARTIFACT_DIGEST_SCHEMA_VERSION: Final[Literal["p2-categorical-drift-artifact-digest/v1"]] = (
    "p2-categorical-drift-artifact-digest/v1"
)
DRIFT_SLOT_BINDING_SCHEMA_VERSION: Final[Literal["p2-drift-slot-binding/v1"]] = (
    "p2-drift-slot-binding/v1"
)
DRIFT_SELECTION_SCHEMA_VERSION: Final[Literal["p2-drift-row-selection/v1"]] = (
    "p2-drift-row-selection/v1"
)
DRIFT_MEASUREMENT_SCHEMA_VERSION: Final[Literal["p2-drift-measurement/v1"]] = (
    "p2-drift-measurement/v1"
)
DRIFT_OBSERVED_EVALUATION_SET_SCHEMA_VERSION: Final[
    Literal["p2-drift-observed-evaluation-set/v1"]
] = "p2-drift-observed-evaluation-set/v1"
DRIFT_METRIC_COMPARISON_SCHEMA_VERSION: Final[Literal["p2-drift-binary-metric-comparison/v1"]] = (
    "p2-drift-binary-metric-comparison/v1"
)
DRIFT_OCCURRENCE_ID_SCHEMA_VERSION: Final[Literal["p2-drift-occurrence-id/v1"]] = (
    "p2-drift-occurrence-id/v1"
)

#: Pinned protocol identifiers, ``Literal`` rather than free text.
DRIFT_PROTOCOL_VERSION: Final[Literal["categorical-distribution-shift/v1"]] = (
    "categorical-distribution-shift/v1"
)

#: The two intervention types the frozen alpha plan binds to M1 slots. Each entry
#: point accepts exactly one of them, checked independently of the slot role.
DRIFT_INTERVENTION_TYPE: Final[Literal["categorical_distribution_shift"]] = (
    "categorical_distribution_shift"
)
RESAMPLING_CONTROL_INTERVENTION_TYPE: Final[
    Literal["empirical_distribution_resampling_control"]
] = "empirical_distribution_resampling_control"

#: How category counts are split across an output batch.
#:
#: Largest remainder, with the category name breaking ties in descending order,
#: exactly as Phase 1 apportions. Named and versioned because it decides how many
#: rows each category contributes, and a different rule would produce a different
#: experiment from the same declared proportions. The direction is stated here
#: because "tiebreak by name" alone would leave two defensible answers.
APPORTIONMENT_RULE: Final[Literal["largest-remainder-name-tiebreak/v1"]] = (
    "largest-remainder-name-tiebreak/v1"
)

#: How rows are drawn inside a category.
#:
#: Candidates are ranked by a canonical digest of the seed, the injection
#: identifier, the category and the record identifier, then taken in rank order,
#: cycling when the batch needs more rows than the pool holds. Phase 1 uses a
#: NumPy generator for the same purpose; the digest ranking is used here so
#: reproducibility does not depend on a bit-generator version.
ROW_SELECTION_POLICY: Final[Literal["seeded-record-hash-rank/v1"]] = "seeded-record-hash-rank/v1"

#: The PSI variant reported. Epsilon smoothing matches the shared implementation.
PSI_METHOD: Final[Literal["psi-epsilon-1e-6/v1"]] = "psi-epsilon-1e-6/v1"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
InjectionId = Annotated[str, Field(pattern=SLOT_ID_PATTERN)]

_MAX_RECORDS: Final[int] = 5_000_000
_MAX_CATEGORIES: Final[int] = 4_096
_FLOAT_TOLERANCE: Final[float] = 1e-12
_DISTRIBUTION_TOLERANCE: Final[float] = 1e-9
DRIFT_DISTRIBUTION_EQUIVALENCE_TOLERANCE: Final[float] = 1e-12

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class DataDriftError(ContractViolation):
    """Raised when Phase 2 drift artifacts disagree with one another."""


def _fail(message: str) -> NoReturn:
    """Reject a cross-object relationship."""

    raise DataDriftError(message)


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
    """Reject text that would make two spellings of one value hash differently."""

    if not value:
        raise ValueError(f"{label} must not be empty")
    if value != value.strip():
        raise ValueError(f"{label} must not have leading or trailing whitespace: {value!r}")
    if value != unicodedata.normalize("NFC", value):
        raise ValueError(f"{label} must already be Unicode NFC: {value!r}")
    if any(character.isspace() and character != " " for character in value):
        raise ValueError(f"{label} must not contain tabs or newlines: {value!r}")
    return value


def _reject_phase_one_namespace(value: str, *, label: str) -> str:
    """Refuse a Phase 1 identifier anywhere in a Phase 2 drift artifact.

    Phase 2 drift is a different generation with a different serialization. A
    Phase 1 case, family or context identifier reaching this module would mean
    an artifact from that generation was being replayed here, so it is rejected
    outright rather than carried forward as if the two were interchangeable.
    """

    folded = value.casefold()
    for marker in ("p1-", "p1_", "p1-cases", "p1-family-", "p1-context-"):
        if folded.startswith(marker) or marker in folded:
            raise ValueError(
                f"{label} carries a Phase 1 identifier ({value!r}); Phase 1 artifacts are "
                "not Phase 2 evidence"
            )
    return value


def _check_source_record_ids(record_ids: Sequence[str]) -> None:
    """Reject anything that cannot anchor a per-record source pool."""

    if not record_ids:
        raise ValueError("a drift source must contain at least one record")
    if len(record_ids) > _MAX_RECORDS:
        raise ValueError("a drift source exceeds the supported record count")
    seen: set[str] = set()
    for position, record_id in enumerate(record_ids):
        _require_canonical_text(record_id, label=f"record ID at position {position}")
        _reject_phase_one_namespace(record_id, label=f"record ID at position {position}")
        if record_id in seen:
            raise ValueError("source record IDs must be unique; duplicates break the pools")
        seen.add(record_id)


def _ordered_digest(*, schema_version: str, key: str, values: Sequence[str]) -> str:
    """Digest a string sequence with its order and multiplicity included."""

    return canonical_sha256({"schema_version": schema_version, key: list(values)})


def _membership_digest(*, schema_version: str, record_ids: Sequence[str]) -> str:
    """Digest which records exist, order excluded."""

    return canonical_sha256({"schema_version": schema_version, "membership": sorted(record_ids)})


def _distribution_digest(
    *, schema_version: str, key: str, distribution: Mapping[str, float]
) -> str:
    """Digest a category-proportion map independently of its insertion order."""

    entries = [
        {"category": category, "proportion": distribution[category]}
        for category in sorted(distribution)
    ]
    return canonical_sha256({"schema_version": schema_version, key: entries})


def normalize_distribution(distribution: Mapping[str, float]) -> dict[str, float]:
    """Return proportions that sum to one, keyed by the same categories.

    ``math.fsum`` is used for the total so a long category list does not drift
    with float accumulation order.
    """

    if not distribution:
        _fail("a target distribution must name at least one category")
    if len(distribution) > _MAX_CATEGORIES:
        _fail("a target distribution exceeds the supported category count")
    checked: dict[str, float] = {}
    for category, weight in distribution.items():
        if not isinstance(category, str):
            _fail("distribution categories must be strings")
        _require_canonical_text(category, label="distribution category")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            _fail(f"distribution weight for {category!r} must be numeric")
        if not math.isfinite(weight):
            _fail(f"distribution weight for {category!r} must be finite")
        if weight < 0.0 or (weight == 0.0 and math.copysign(1.0, weight) < 0.0):
            _fail(f"distribution weight for {category!r} must be non-negative")
        checked[category] = float(weight)
    total = math.fsum(checked.values())
    if not math.isfinite(total) or total <= 0.0:
        _fail("a target distribution must have a positive finite total")
    return {category: checked[category] / total for category in sorted(checked)}


def distribution_total_variation(first: Mapping[str, float], second: Mapping[str, float]) -> float:
    """Return total-variation distance after strict distribution validation."""

    left = normalize_distribution(first)
    right = normalize_distribution(second)
    categories = set(left) | set(right)
    return 0.5 * math.fsum(abs(left.get(key, 0.0) - right.get(key, 0.0)) for key in categories)


def apportion(*, target_distribution: Mapping[str, float], output_size: int) -> dict[str, int]:
    """Split ``output_size`` across categories, summing to exactly that number.

    Largest-remainder over the normalized proportions, with the category name
    breaking ties so the result never depends on mapping order.
    """

    if isinstance(output_size, bool) or not isinstance(output_size, int) or output_size <= 0:
        _fail(f"output_size must be positive, got {output_size}")
    if not target_distribution:
        _fail("a target distribution must name at least one category")
    normalized = normalize_distribution(target_distribution)
    raw = {category: proportion * output_size for category, proportion in normalized.items()}
    counts = {category: int(value) for category, value in raw.items()}
    remainder = output_size - sum(counts.values())
    order = sorted(
        raw, key=lambda category: (raw[category] - counts[category], category), reverse=True
    )
    for category in order[:remainder]:
        counts[category] += 1
    return counts


def drift_selection_digest(*, seed: int, injection_id: str, category: str, record_id: str) -> str:
    """Return the deterministic ranking digest for one candidate row."""

    return canonical_sha256(
        {
            "schema_version": DRIFT_SELECTION_SCHEMA_VERSION,
            "policy": ROW_SELECTION_POLICY,
            "seed": seed,
            "injection_id": injection_id,
            "category": category,
            "record_id": record_id,
        }
    )


def select_category_rows(
    *, pool_record_ids: Sequence[str], count: int, seed: int, injection_id: str, category: str
) -> tuple[str, ...]:
    """Draw ``count`` rows from one category pool, deterministically.

    Ranking is by seeded digest, so the draw does not depend on the order the
    caller listed the pool in. When the batch needs more rows than the pool
    holds, the ranked list is cycled: resampling draws with replacement, and
    cycling makes the multiplicity reproducible instead of leaving it to a
    generator's internal state.
    """

    if count < 0:
        _fail(f"a category count must not be negative, got {count}")
    if count == 0:
        return ()
    if not pool_record_ids:
        _fail(f"category {category!r} is absent from the source; drift cannot be injected")
    ranked = sorted(
        pool_record_ids,
        key=lambda record_id: drift_selection_digest(
            seed=seed, injection_id=injection_id, category=category, record_id=record_id
        ),
    )
    return tuple(ranked[index % len(ranked)] for index in range(count))


# --------------------------------------------------------------------------- #
# Source
# --------------------------------------------------------------------------- #


class DriftEvaluationSource(_StrictFrozenModel):
    """The clean evaluation rows a drift batch is resampled from.

    Only the identifiers and one categorical column are carried. The feature
    matrix, the targets and the model are described by ``attested_*`` digests:
    this module cannot see them, so it cannot recompute them, and it says so in
    the field names rather than implying otherwise.
    """

    schema_version: Literal["p2-drift-evaluation-source/v1"]
    split: Literal["test"]
    dataset_snapshot_id: str = Field(min_length=1, max_length=128)
    dataset_sha256: Sha256
    model_data_split_manifest_sha256: Sha256
    feature: str = Field(min_length=1, max_length=128)
    record_ids: tuple[str, ...]
    feature_values: tuple[str, ...]
    attested_raw_feature_matrix_sha256: Sha256
    attested_raw_target_sha256: Sha256
    attested_model_sha256: Sha256
    attested_preprocessing_specification_sha256: Sha256

    @model_validator(mode="after")
    def _source_is_well_formed(self) -> DriftEvaluationSource:
        _require_canonical_text(self.dataset_snapshot_id, label="dataset_snapshot_id")
        _reject_phase_one_namespace(self.dataset_snapshot_id, label="dataset_snapshot_id")
        _require_canonical_text(self.feature, label="feature name")
        _reject_phase_one_namespace(self.feature, label="feature name")
        _check_source_record_ids(self.record_ids)
        if len(self.record_ids) != len(self.feature_values):
            raise ValueError(
                "record_ids and feature_values must align: "
                f"{len(self.record_ids)} ids against {len(self.feature_values)} values"
            )
        categories: set[str] = set()
        for position, value in enumerate(self.feature_values):
            _require_canonical_text(value, label=f"feature value at position {position}")
            categories.add(value)
        if len(categories) < 2:
            raise ValueError("a drift source needs at least two categories to shift between")
        if len(categories) > _MAX_CATEGORIES:
            raise ValueError("drift source exceeds the supported category count")
        return self

    @property
    def record_count(self) -> int:
        return len(self.record_ids)

    def categories(self) -> tuple[str, ...]:
        """Return the source categories in canonical sorted order."""

        return tuple(sorted(set(self.feature_values)))

    def category_pool(self, category: str) -> tuple[str, ...]:
        """Return the record identifiers whose feature value is ``category``."""

        return tuple(
            record_id
            for record_id, value in zip(self.record_ids, self.feature_values, strict=True)
            if value == category
        )

    def observed_distribution(self) -> dict[str, float]:
        """Return the empirical category proportions of the clean source."""

        return categorical_distribution(list(self.feature_values))

    def record_ids_sha256(self) -> str:
        """Digest the identifier sequence, order included."""

        return _ordered_digest(
            schema_version=self.schema_version, key="record_ids", values=self.record_ids
        )

    def membership_sha256(self) -> str:
        """Digest which records exist, order excluded."""

        return _membership_digest(schema_version=self.schema_version, record_ids=self.record_ids)

    def feature_values_sha256(self) -> str:
        """Digest the categorical column exactly as it stands, order included."""

        return _ordered_digest(
            schema_version=self.schema_version, key="feature_values", values=self.feature_values
        )

    def artifact_sha256(self) -> str:
        """Digest every declared field of the source."""

        return canonical_sha256(
            {"schema_version": self.schema_version, "source": self.model_dump(mode="json")}
        )


# --------------------------------------------------------------------------- #
# Specification
# --------------------------------------------------------------------------- #


class CategoricalDriftSpec(_StrictFrozenModel):
    """One prespecified marginal shift: which feature, which target, which seed.

    The parameters are the canonical identity model rather than a second set of
    fields, so a specification and the family identity it belongs to cannot
    describe two different interventions.
    """

    schema_version: Literal["p2-categorical-drift-spec/v1"] = "p2-categorical-drift-spec/v1"
    injection_id: InjectionId
    parameters: DataDriftParameters
    seed: int = Field(ge=0)

    @model_validator(mode="after")
    def _spec_describes_a_marginal_shift(self) -> CategoricalDriftSpec:
        _reject_phase_one_namespace(self.injection_id, label="injection_id")
        if not self.injection_id.startswith("M1-"):
            raise ValueError("a categorical drift specification belongs to an M1 slot")
        if len(self.parameters.target_distribution) < 2:
            raise ValueError("a marginal shift needs at least two categories")
        return self

    def target_distribution_sha256(self) -> str:
        """Digest the declared target distribution, insertion order excluded."""

        return _distribution_digest(
            schema_version=self.schema_version,
            key="target_distribution",
            distribution=self.parameters.target_distribution,
        )

    def canonical_sha256(self) -> str:
        """Digest the complete specification."""

        return canonical_sha256(
            {"schema_version": self.schema_version, "spec": self.model_dump(mode="json")}
        )


# --------------------------------------------------------------------------- #
# Slot binding
# --------------------------------------------------------------------------- #


def _validate_drift_slot(
    slot: CandidateSlot,
    *,
    source: DriftEvaluationSource,
    spec: CategoricalDriftSpec,
    expected_intervention_type: str,
    expected_role: str | None = None,
) -> CandidateSlot:
    """Bind one drift operation to its frozen slot and family identity.

    Which target distribution and seed belong to which slot identifier is
    decided by :func:`validate_frozen_alpha_slot`, the single authoritative
    grid. This function adds only the mechanism contract.
    """

    try:
        slot = validate_frozen_alpha_slot(slot)
    except ContractViolation as error:
        _fail(str(error))
    identity = slot.identity
    declared = identity.canonical_intervention_parameters

    if slot.fault_type != "data_drift" or identity.fault_type != "data_drift":
        _fail("the Phase 2 drift mechanism accepts data_drift slots only")
    if identity.intervention_type != expected_intervention_type:
        _fail(
            f"this entry point implements {expected_intervention_type!r} only; "
            f"got {identity.intervention_type!r}"
        )
    if expected_role is not None and slot.role != expected_role:
        _fail(f"this entry point accepts {expected_role!r} slots only; got {slot.role!r}")
    if slot.slot_id != spec.injection_id:
        _fail("the specification injection_id differs from the frozen slot identifier")
    if not isinstance(declared, DataDriftParameters):
        _fail("a data_drift slot must carry data-drift parameters")
    if declared != spec.parameters:
        _fail("the specification differs from the parameters the slot declares")
    if identity.seed != spec.seed:
        _fail("the specification seed differs from the seed the slot declares")
    _reject_phase_one_namespace(identity.dataset_snapshot_id, label="dataset_snapshot_id")
    source_bindings = (
        ("dataset_snapshot_id", source.dataset_snapshot_id, identity.dataset_snapshot_id),
        ("dataset_sha256", source.dataset_sha256, identity.dataset_sha256),
        (
            "model_data_split_manifest_sha256",
            source.model_data_split_manifest_sha256,
            identity.model_data_split_manifest_sha256,
        ),
        (
            "model_specification_sha256",
            source.attested_model_sha256,
            identity.model_specification_sha256,
        ),
        (
            "preprocessing_specification_sha256",
            source.attested_preprocessing_specification_sha256,
            identity.preprocessing_specification_sha256,
        ),
    )
    for name, observed, expected in source_bindings:
        if observed != expected:
            _fail(f"the drift source {name} differs from the frozen family identity")
    return slot


def _drift_slot_sha256(slot: CandidateSlot) -> str:
    """Bind the complete frozen slot, including its twelve-field family identity."""

    slot = _revalidated(slot)
    return canonical_sha256(
        {
            "schema_version": DRIFT_SLOT_BINDING_SCHEMA_VERSION,
            "slot": slot.model_dump(mode="json"),
        }
    )


# --------------------------------------------------------------------------- #
# Provenance and result
# --------------------------------------------------------------------------- #


class CategoricalDriftProvenance(_StrictFrozenModel):
    """Everything an evaluator needs to reproduce and audit one drift batch.

    ``attested_*`` fields are copied and cross-bound to the validated source.
    They are not recomputed from raw feature, target or model data, because this
    module never receives those artifacts.
    """

    schema_version: Literal["p2-categorical-drift/v1"]
    intervention_type: Literal[
        "categorical_distribution_shift", "empirical_distribution_resampling_control"
    ]
    drift_protocol_version: Literal["categorical-distribution-shift/v1"]
    apportionment_rule: Literal["largest-remainder-name-tiebreak/v1"]
    row_selection_policy: Literal["seeded-record-hash-rank/v1"]
    psi_method: Literal["psi-epsilon-1e-6/v1"]
    injection_id: InjectionId
    feature: str = Field(min_length=1, max_length=128)
    seed: int = Field(ge=0)
    source_record_count: int = Field(ge=1)
    output_size: int = Field(ge=1)
    population_stability_index: float = Field(ge=0.0)
    reference_distribution_sha256: Sha256
    target_distribution_sha256: Sha256
    achieved_distribution_sha256: Sha256
    source_record_ids_sha256: Sha256
    source_membership_sha256: Sha256
    source_feature_values_sha256: Sha256
    source_artifact_sha256: Sha256
    selected_record_ids_sha256: Sha256
    selected_feature_values_sha256: Sha256
    category_counts_sha256: Sha256
    spec_sha256: Sha256
    drift_slot_sha256: Sha256
    attested_raw_feature_matrix_sha256: Sha256
    attested_raw_target_sha256: Sha256
    attested_model_sha256: Sha256
    attested_preprocessing_specification_sha256: Sha256

    @model_validator(mode="after")
    def _provenance_is_self_consistent(self) -> CategoricalDriftProvenance:
        _require_canonical_text(self.feature, label="feature name")
        if not math.isfinite(self.population_stability_index):
            raise ValueError("the population stability index must be finite")
        if self.intervention_type == "categorical_distribution_shift":
            if self.target_distribution_sha256 == self.reference_distribution_sha256:
                raise ValueError(
                    "a fault-directed drift must declare a target distribution that differs "
                    "from the clean reference"
                )
        elif self.target_distribution_sha256 != self.reference_distribution_sha256:
            raise ValueError(
                "the empirical resampling control must target the clean reference "
                "distribution exactly"
            )
        return self


class CategoricalDriftResult(_StrictFrozenModel):
    """A completed drift batch: which rows were drawn, and what they look like.

    The result describes the batch. It carries no metric, measured outcome,
    eligibility, family class or cause; those are decided later, from
    measurements this module never performs.
    """

    schema_version: Literal["p2-categorical-drift-result/v1"] = "p2-categorical-drift-result/v1"
    selected_record_ids: tuple[str, ...]
    selected_feature_values: tuple[str, ...]
    category_counts: tuple[tuple[str, int], ...]
    reference_distribution: tuple[tuple[str, float], ...]
    achieved_distribution: tuple[tuple[str, float], ...]
    provenance: CategoricalDriftProvenance

    @model_validator(mode="after")
    def _result_is_internally_aligned(self) -> CategoricalDriftResult:
        provenance = self.provenance
        if not self.selected_record_ids:
            raise ValueError("a drift batch must contain at least one row")
        if len(self.selected_record_ids) != len(self.selected_feature_values):
            raise ValueError("selected identifiers and feature values must align")
        if len(self.selected_record_ids) != provenance.output_size:
            raise ValueError("the batch size must match the declared output size")
        for position, record_id in enumerate(self.selected_record_ids):
            _reject_phase_one_namespace(record_id, label=f"selected record at position {position}")

        for name, pairs in (
            ("reference_distribution", self.reference_distribution),
            ("achieved_distribution", self.achieved_distribution),
        ):
            categories = [category for category, _ in pairs]
            if list(categories) != sorted(categories):
                raise ValueError(f"{name} must be in canonical sorted category order")
            if len(set(categories)) != len(categories):
                raise ValueError(f"{name} must not repeat a category")
            for category, proportion in pairs:
                _require_canonical_text(category, label=f"{name} category")
                if not math.isfinite(proportion) or proportion < 0.0:
                    raise ValueError(f"{name} proportions must be finite and non-negative")
            total = math.fsum(proportion for _, proportion in pairs)
            if abs(total - 1.0) > _DISTRIBUTION_TOLERANCE:
                raise ValueError(f"{name} must sum to one, got {total}")

        count_categories = [category for category, _ in self.category_counts]
        if list(count_categories) != sorted(count_categories):
            raise ValueError("category_counts must be in canonical sorted category order")
        if len(set(count_categories)) != len(count_categories):
            raise ValueError("category_counts must not repeat a category")
        if any(count < 0 for _, count in self.category_counts):
            raise ValueError("category counts must not be negative")
        if sum(count for _, count in self.category_counts) != len(self.selected_record_ids):
            raise ValueError("category counts must sum to the batch size")

        observed = dict(zip(count_categories, (c for _, c in self.category_counts), strict=True))
        actual: dict[str, int] = {}
        for value in self.selected_feature_values:
            actual[value] = actual.get(value, 0) + 1
        if {k: v for k, v in observed.items() if v} != actual:
            raise ValueError("category counts must match the drawn feature values")

        batch = len(self.selected_feature_values)
        for category, proportion in self.achieved_distribution:
            expected = actual.get(category, 0) / batch
            if abs(proportion - expected) > _DISTRIBUTION_TOLERANCE:
                raise ValueError(
                    "achieved_distribution must be the proportions of the drawn feature "
                    f"values; category {category!r} differs"
                )
        if set(dict(self.achieved_distribution)) != set(actual):
            raise ValueError("achieved_distribution must name exactly the drawn categories")
        return self

    def artifact_sha256(self) -> str:
        """Digest every serialized field, including batch order and multiplicity.

        Order-sensitive by design: the batch is an ordered sample drawn with
        replacement, so reordering it or changing how often a row appears
        produces a different experiment. No semantic digest accompanies it,
        because no consumer needs one.
        """

        return canonical_sha256(
            {
                "digest_schema_version": DRIFT_ARTIFACT_DIGEST_SCHEMA_VERSION,
                "artifact": self.model_dump(mode="json"),
            }
        )


class DriftObservedEvaluationSet(_StrictFrozenModel):
    """The exact labelled row occurrences scored after resampling.

    Source record identifiers may repeat because drift uses sampling with
    replacement. ``occurrence_ids`` disambiguate those copies, while
    ``selected_record_ids`` preserve their origin. True labels are derived
    from the frozen clean test set by record identifier; callers never get to
    supply a new target vector for the drifted batch.
    """

    schema_version: Literal["p2-drift-observed-evaluation-set/v1"]
    split: Literal["test"]
    drift_artifact_sha256: Sha256
    source_artifact_sha256: Sha256
    selected_record_ids: tuple[str, ...]
    occurrence_ids: tuple[str, ...]
    true_labels: tuple[int, ...]
    attested_drifted_feature_matrix_sha256: Sha256
    attested_target_source_sha256: Sha256
    attested_split_manifest_sha256: Sha256
    attested_model_sha256: Sha256
    attested_preprocessing_specification_sha256: Sha256

    @model_validator(mode="after")
    def _rows_are_aligned(self) -> DriftObservedEvaluationSet:
        size = len(self.selected_record_ids)
        if size == 0:
            raise ValueError("a drift observed evaluation set must not be empty")
        if len(self.occurrence_ids) != size or len(self.true_labels) != size:
            raise ValueError("selected IDs, occurrence IDs and true labels must align")
        if len(set(self.occurrence_ids)) != size:
            raise ValueError("drift occurrence IDs must be unique")
        for position, (record_id, occurrence_id, label) in enumerate(
            zip(self.selected_record_ids, self.occurrence_ids, self.true_labels, strict=True)
        ):
            _require_canonical_text(record_id, label=f"selected record at position {position}")
            _require_canonical_text(occurrence_id, label=f"occurrence ID at position {position}")
            if label not in (0, 1):
                raise ValueError(f"true label at position {position} must be binary 0 or 1")
        return self

    @property
    def record_count(self) -> int:
        return len(self.selected_record_ids)

    def selected_targets_sha256(self) -> str:
        """Digest the derived target sequence, including order and multiplicity."""

        return canonical_sha256(
            {
                "schema_version": self.schema_version,
                "selected_record_ids": list(self.selected_record_ids),
                "true_labels": list(self.true_labels),
            }
        )

    def artifact_sha256(self) -> str:
        """Digest the complete observed evaluation source."""

        return canonical_sha256(
            {"schema_version": self.schema_version, "observed_set": self.model_dump(mode="json")}
        )


def _validate_clean_test_binding(
    *, source: DriftEvaluationSource, test_set: CleanTestSet
) -> CleanTestSet:
    """Bind the metric reference to the exact rows and artifacts used for drift."""

    test_set = _revalidated(test_set)
    checks = (
        ("record identifiers", test_set.record_ids, source.record_ids),
        (
            "raw feature matrix",
            test_set.attested_test_feature_matrix_sha256,
            source.attested_raw_feature_matrix_sha256,
        ),
        ("target source", test_set.attested_target_sha256, source.attested_raw_target_sha256),
        (
            "split manifest",
            test_set.attested_split_manifest_sha256,
            source.model_data_split_manifest_sha256,
        ),
        ("model", test_set.attested_model_sha256, source.attested_model_sha256),
    )
    for name, observed, expected in checks:
        if observed != expected:
            _fail(f"the clean metric source {name} differs from the drift source")
    return test_set


def _occurrence_ids(result: CategoricalDriftResult) -> tuple[str, ...]:
    return tuple(
        canonical_sha256(
            {
                "schema_version": DRIFT_OCCURRENCE_ID_SCHEMA_VERSION,
                "drift_artifact_sha256": result.artifact_sha256(),
                "position": position,
                "source_record_id": record_id,
            }
        )
        for position, record_id in enumerate(result.selected_record_ids)
    )


def build_drift_observed_evaluation_set(
    *,
    result: CategoricalDriftResult,
    source: DriftEvaluationSource,
    test_set: CleanTestSet,
    attested_drifted_feature_matrix_sha256: str,
) -> DriftObservedEvaluationSet:
    """Build the only valid scoring source for a resampled drift batch."""

    result = _revalidated(result)
    source = _revalidated(source)
    test_set = _validate_clean_test_binding(source=source, test_set=test_set)
    if result.provenance.source_artifact_sha256 != source.artifact_sha256():
        _fail("the drift batch is not bound to this source")
    labels_by_id = dict(zip(test_set.record_ids, test_set.attested_true_labels, strict=True))
    try:
        selected_labels = tuple(labels_by_id[record_id] for record_id in result.selected_record_ids)
    except KeyError as error:  # pragma: no cover - drift validation normally catches this first
        _fail(f"selected record is absent from the clean target source: {error.args[0]!r}")
    return DriftObservedEvaluationSet(
        schema_version=DRIFT_OBSERVED_EVALUATION_SET_SCHEMA_VERSION,
        split="test",
        drift_artifact_sha256=result.artifact_sha256(),
        source_artifact_sha256=source.artifact_sha256(),
        selected_record_ids=result.selected_record_ids,
        occurrence_ids=_occurrence_ids(result),
        true_labels=selected_labels,
        attested_drifted_feature_matrix_sha256=attested_drifted_feature_matrix_sha256,
        attested_target_source_sha256=test_set.attested_target_sha256,
        attested_split_manifest_sha256=test_set.attested_split_manifest_sha256,
        attested_model_sha256=test_set.attested_model_sha256,
        attested_preprocessing_specification_sha256=(
            source.attested_preprocessing_specification_sha256
        ),
    )


def validate_drift_observed_evaluation_set(
    observed_set: DriftObservedEvaluationSet,
    *,
    result: CategoricalDriftResult,
    source: DriftEvaluationSource,
    test_set: CleanTestSet,
) -> DriftObservedEvaluationSet:
    """Recompute every derivable observed-set field and reject replay/tampering."""

    observed_set = _revalidated(observed_set)
    expected = build_drift_observed_evaluation_set(
        result=result,
        source=source,
        test_set=test_set,
        attested_drifted_feature_matrix_sha256=(
            observed_set.attested_drifted_feature_matrix_sha256
        ),
    )
    if observed_set.artifact_sha256() != expected.artifact_sha256():
        _fail("the observed evaluation set does not match the resampled drift batch")
    return observed_set


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #


def _build_result(
    *,
    source: DriftEvaluationSource,
    spec: CategoricalDriftSpec,
    slot: CandidateSlot,
    intervention_type: str,
) -> CategoricalDriftResult:
    """Draw one batch and bind everything needed to reproduce it."""

    target = spec.parameters.target_distribution
    output_size = spec.parameters.output_size
    reference = source.observed_distribution()

    missing = sorted(set(target) - set(source.categories()))
    if missing:
        _fail(f"target categories are absent from the source: {missing}")

    counts = apportion(target_distribution=target, output_size=output_size)
    selected: list[str] = []
    values: list[str] = []
    for category in sorted(counts):
        drawn = select_category_rows(
            pool_record_ids=source.category_pool(category),
            count=counts[category],
            seed=spec.seed,
            injection_id=spec.injection_id,
            category=category,
        )
        selected.extend(drawn)
        values.extend(category for _ in drawn)

    achieved = categorical_distribution(values)
    psi = population_stability_index(reference, achieved)
    if not math.isfinite(psi):
        _fail("the population stability index is not finite")

    provenance = CategoricalDriftProvenance(
        schema_version=DRIFT_PROVENANCE_SCHEMA_VERSION,
        intervention_type=intervention_type,  # type: ignore[arg-type]
        drift_protocol_version=DRIFT_PROTOCOL_VERSION,
        apportionment_rule=APPORTIONMENT_RULE,
        row_selection_policy=ROW_SELECTION_POLICY,
        psi_method=PSI_METHOD,
        injection_id=spec.injection_id,
        feature=source.feature,
        seed=spec.seed,
        source_record_count=source.record_count,
        output_size=output_size,
        population_stability_index=psi,
        reference_distribution_sha256=_distribution_digest(
            schema_version=DRIFT_PROVENANCE_SCHEMA_VERSION,
            key="distribution",
            distribution=reference,
        ),
        target_distribution_sha256=_distribution_digest(
            schema_version=DRIFT_PROVENANCE_SCHEMA_VERSION,
            key="distribution",
            distribution=normalize_distribution(target),
        ),
        achieved_distribution_sha256=_distribution_digest(
            schema_version=DRIFT_PROVENANCE_SCHEMA_VERSION,
            key="distribution",
            distribution=achieved,
        ),
        source_record_ids_sha256=source.record_ids_sha256(),
        source_membership_sha256=source.membership_sha256(),
        source_feature_values_sha256=source.feature_values_sha256(),
        source_artifact_sha256=source.artifact_sha256(),
        selected_record_ids_sha256=_ordered_digest(
            schema_version=DRIFT_PROVENANCE_SCHEMA_VERSION,
            key="selected_record_ids",
            values=selected,
        ),
        selected_feature_values_sha256=_ordered_digest(
            schema_version=DRIFT_PROVENANCE_SCHEMA_VERSION,
            key="selected_feature_values",
            values=values,
        ),
        category_counts_sha256=canonical_sha256(
            {
                "schema_version": DRIFT_PROVENANCE_SCHEMA_VERSION,
                "category_counts": [
                    {"category": category, "count": counts[category]} for category in sorted(counts)
                ],
            }
        ),
        spec_sha256=spec.canonical_sha256(),
        drift_slot_sha256=_drift_slot_sha256(slot),
        attested_raw_feature_matrix_sha256=source.attested_raw_feature_matrix_sha256,
        attested_raw_target_sha256=source.attested_raw_target_sha256,
        attested_model_sha256=source.attested_model_sha256,
        attested_preprocessing_specification_sha256=(
            source.attested_preprocessing_specification_sha256
        ),
    )
    return CategoricalDriftResult(
        selected_record_ids=tuple(selected),
        selected_feature_values=tuple(values),
        category_counts=tuple((category, counts[category]) for category in sorted(counts)),
        reference_distribution=tuple((c, reference[c]) for c in sorted(reference)),
        achieved_distribution=tuple((c, achieved[c]) for c in sorted(achieved)),
        provenance=provenance,
    )


def apply_categorical_drift(
    *, source: DriftEvaluationSource, spec: CategoricalDriftSpec, slot: CandidateSlot
) -> CategoricalDriftResult:
    """Shift the marginal of one categorical feature and change nothing else.

    The caller's objects are never mutated, no model is retrained and no target
    is touched: neither is reachable from here.
    """

    source = _revalidated(source)
    spec = _revalidated(spec)
    slot = _validate_drift_slot(
        slot,
        source=source,
        spec=spec,
        expected_intervention_type=DRIFT_INTERVENTION_TYPE,
    )
    if spec.parameters.feature != source.feature:
        _fail("the specification targets a feature the source does not carry")

    result = _build_result(
        source=source, spec=spec, slot=slot, intervention_type=DRIFT_INTERVENTION_TYPE
    )
    if result.achieved_distribution == result.reference_distribution:
        _fail("the declared target reproduces the clean distribution, so nothing drifted")
    return result


def apply_empirical_resampling_control(
    *, source: DriftEvaluationSource, spec: CategoricalDriftSpec, slot: CandidateSlot
) -> CategoricalDriftResult:
    """Resample the batch to the clean empirical distribution, changing no marginal.

    This is the M1-B1 benign control. It runs the same resampling machinery as a
    fault-directed shift, but targets the distribution the source already has,
    so the marginal it is supposed to preserve is preserved by construction and
    then checked.
    """

    source = _revalidated(source)
    spec = _revalidated(spec)
    slot = _validate_drift_slot(
        slot,
        source=source,
        spec=spec,
        expected_intervention_type=RESAMPLING_CONTROL_INTERVENTION_TYPE,
        expected_role="designed_benign_control",
    )
    if spec.parameters.feature != source.feature:
        _fail("the specification targets a feature the source does not carry")
    if spec.parameters.output_size != source.record_count:
        _fail(
            "the empirical resampling control must preserve the source row count so "
            "distribution equivalence is testable"
        )

    reference = source.observed_distribution()
    declared = normalize_distribution(spec.parameters.target_distribution)
    if set(declared) != set(reference):
        _fail("the resampling control must declare exactly the clean source categories")
    for category, proportion in reference.items():
        if abs(declared[category] - proportion) > _DISTRIBUTION_TOLERANCE:
            _fail(
                "the resampling control must declare the clean empirical distribution; "
                f"category {category!r} differs"
            )
    result = _build_result(
        source=source,
        spec=spec,
        slot=slot,
        intervention_type=RESAMPLING_CONTROL_INTERVENTION_TYPE,
    )
    total_variation = distribution_total_variation(
        dict(result.reference_distribution), dict(result.achieved_distribution)
    )
    if (
        total_variation > DRIFT_DISTRIBUTION_EQUIVALENCE_TOLERANCE
        or result.provenance.population_stability_index > DRIFT_DISTRIBUTION_EQUIVALENCE_TOLERANCE
    ):
        _fail("the empirical resampling control failed distribution equivalence")
    return result


# --------------------------------------------------------------------------- #
# Authoritative validation
# --------------------------------------------------------------------------- #


def validate_categorical_drift(
    result: CategoricalDriftResult,
    *,
    source: DriftEvaluationSource,
    spec: CategoricalDriftSpec,
    slot: CandidateSlot,
) -> CategoricalDriftResult:
    """Recompute the whole drift batch and reject any mismatch.

    This is the one authoritative entry point for a Phase 2 drift artifact. The
    result is rebuilt from its own dump first, so anything assembled with
    ``model_copy`` or ``model_construct`` is re-validated rather than trusted,
    and the construction is then run again and compared by digest — so a forged
    field anywhere in the artifact is caught by one check rather than by a list
    of checks somebody has to keep complete.
    """

    result = _revalidated(result)
    intervention_type = result.provenance.intervention_type
    if intervention_type == RESAMPLING_CONTROL_INTERVENTION_TYPE:
        expected = apply_empirical_resampling_control(source=source, spec=spec, slot=slot)
    elif intervention_type == DRIFT_INTERVENTION_TYPE:
        expected = apply_categorical_drift(source=source, spec=spec, slot=slot)
    else:  # pragma: no cover - the Literal admits nothing else
        _fail(f"unknown drift intervention type: {intervention_type!r}")

    if result.selected_record_ids != expected.selected_record_ids:
        _fail("the drawn rows do not match the deterministic selection for this seed")
    if result.selected_feature_values != expected.selected_feature_values:
        _fail("the drawn feature values do not match the drawn rows")
    if result.category_counts != expected.category_counts:
        _fail("the category counts do not match the declared apportionment")
    if result.reference_distribution != expected.reference_distribution:
        _fail("the reference distribution does not match the clean source")
    if result.achieved_distribution != expected.achieved_distribution:
        _fail("the achieved distribution does not match the drawn batch")
    if result.artifact_sha256() != expected.artifact_sha256():
        _fail("the drift artifact does not match the recomputed construction")
    return result


# --------------------------------------------------------------------------- #
# Post-execution measurement
# --------------------------------------------------------------------------- #

#: How far a measured drift candidate has been taken, and no further.
#:
#: Neither value is an eligibility decision or a family class.
DriftMeasurementStatus = Literal[
    "validity_review_required",
    "benign_equivalence_failure",
    "equivalence_verified_pending_admission",
]


class DriftMetricComparison(_StrictFrozenModel):
    """Reference and drift-batch metrics with separate evaluation bindings.

    A drift batch can contain a different number and ordering of row
    occurrences from its clean source. It is therefore invalid to reuse the
    generic same-test-set comparison. The metric formulas remain authoritative
    from :mod:`binary_evaluation`; only the two source bindings differ here.
    """

    schema_version: Literal["p2-drift-binary-metric-comparison/v1"]
    metric_protocol_version: Literal["binary-alpha-metrics/v1"]
    primary_metric: Literal["accuracy"]
    primary_threshold: float = Field(gt=0.0)
    reference: BinaryMetricSnapshot
    observed: BinaryMetricSnapshot
    accuracy_delta: float
    macro_f1_delta: float
    minority_recall_delta: float
    measured_primary_outcome: Literal["regression", "stable", "improvement"]
    reference_evaluation_source_sha256: Sha256
    observed_evaluation_source_sha256: Sha256
    reference_predictions_sha256: Sha256
    observed_predictions_sha256: Sha256

    @field_validator("accuracy_delta", "macro_f1_delta", "minority_recall_delta")
    @classmethod
    def _deltas_are_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("metric deltas must be finite")
        return value

    @model_validator(mode="after")
    def _values_are_derived(self) -> DriftMetricComparison:
        if abs(self.primary_threshold - PRIMARY_ACCURACY_THRESHOLD) > _FLOAT_TOLERANCE:
            raise ValueError("the drift comparison must use the frozen accuracy threshold")
        expected_values = (
            (
                "accuracy_delta",
                self.accuracy_delta,
                self.observed.accuracy - self.reference.accuracy,
            ),
            (
                "macro_f1_delta",
                self.macro_f1_delta,
                self.observed.macro_f1 - self.reference.macro_f1,
            ),
            (
                "minority_recall_delta",
                self.minority_recall_delta,
                self.observed.minority_recall - self.reference.minority_recall,
            ),
        )
        for name, declared, expected in expected_values:
            if abs(declared - expected) > _FLOAT_TOLERANCE:
                raise ValueError(f"{name} must be observed minus reference")
        if self.measured_primary_outcome != derived_primary_outcome(self.accuracy_delta):
            raise ValueError("measured_primary_outcome must be derived from accuracy_delta")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(
            {"schema_version": self.schema_version, "comparison": self.model_dump(mode="json")}
        )


def _snapshot_for_observed_batch(
    *, observed_set: DriftObservedEvaluationSet, predictions: PredictionVector
) -> BinaryMetricSnapshot:
    predictions = _revalidated(predictions)
    if predictions.role != "observed":
        _fail("the drift-batch prediction vector must declare the observed role")
    if len(predictions.predictions) != observed_set.record_count:
        _fail(
            "the observed prediction vector must have one entry per resampled occurrence: "
            f"{len(predictions.predictions)} against {observed_set.record_count}"
        )
    confusion = confusion_for(
        true_labels=observed_set.true_labels,
        predictions=predictions.predictions,
    )
    return BinaryMetricSnapshot(
        schema_version=METRIC_SNAPSHOT_SCHEMA_VERSION,
        metric_protocol_version=METRIC_PROTOCOL_VERSION,
        zero_division_policy=ZERO_DIVISION_POLICY,
        minority_label=MINORITY_LABEL,
        prediction_count=observed_set.record_count,
        accuracy=confusion.accuracy(),
        macro_f1=confusion.macro_f1(),
        minority_recall=confusion.minority_recall(),
        confusion=confusion,
    )


def compare_drift_metrics(
    *,
    test_set: CleanTestSet,
    observed_set: DriftObservedEvaluationSet,
    clean_reference_predictions: PredictionVector,
    observed_predictions: PredictionVector,
) -> DriftMetricComparison:
    """Score each vector against the labels of the rows it actually predicts."""

    clean_reference_predictions = _revalidated(clean_reference_predictions)
    observed_predictions = _revalidated(observed_predictions)
    if clean_reference_predictions.role != "reference":
        _fail("the clean prediction vector must declare the reference role")
    reference = metric_snapshot(
        test_set=test_set,
        predictions=clean_reference_predictions,
    )
    observed = _snapshot_for_observed_batch(
        observed_set=observed_set,
        predictions=observed_predictions,
    )
    accuracy_delta = observed.accuracy - reference.accuracy
    return DriftMetricComparison(
        schema_version=DRIFT_METRIC_COMPARISON_SCHEMA_VERSION,
        metric_protocol_version=METRIC_PROTOCOL_VERSION,
        primary_metric=PRIMARY_METRIC,
        primary_threshold=PRIMARY_ACCURACY_THRESHOLD,
        reference=reference,
        observed=observed,
        accuracy_delta=accuracy_delta,
        macro_f1_delta=observed.macro_f1 - reference.macro_f1,
        minority_recall_delta=observed.minority_recall - reference.minority_recall,
        measured_primary_outcome=derived_primary_outcome(accuracy_delta),
        reference_evaluation_source_sha256=test_set.artifact_sha256(),
        observed_evaluation_source_sha256=observed_set.artifact_sha256(),
        reference_predictions_sha256=clean_reference_predictions.canonical_sha256(),
        observed_predictions_sha256=observed_predictions.canonical_sha256(),
    )


class DriftMeasurement(_StrictFrozenModel):
    """What a drift candidate measured, bound to the batch it measured.

    ``status`` is derived by a validator from the role and the recomputed
    metrics, so a caller who edits one without the other is rejected. Guardrail
    deltas are reported and left unjudged: the macro-F1 and minority-recall harm
    thresholds for this mechanism are still alpha-provisional.
    """

    schema_version: Literal["p2-drift-measurement/v1"]
    intervention_type: Literal[
        "categorical_distribution_shift", "empirical_distribution_resampling_control"
    ]
    drift_artifact_sha256: Sha256
    drift_slot_sha256: Sha256
    reference_evaluation_source_sha256: Sha256
    observed_evaluation_source_sha256: Sha256
    distribution_total_variation: float = Field(ge=0.0, le=1.0)
    population_stability_index: float = Field(ge=0.0)
    distribution_equivalence_tolerance: float = Field(ge=0.0)
    comparison: DriftMetricComparison
    status: DriftMeasurementStatus

    @field_validator(
        "distribution_total_variation",
        "population_stability_index",
        "distribution_equivalence_tolerance",
    )
    @classmethod
    def _distribution_values_are_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("distribution equivalence values must be finite")
        return value

    @model_validator(mode="after")
    def _status_is_derived(self) -> DriftMeasurement:
        DriftMetricComparison.model_validate(self.comparison.model_dump())
        if (
            abs(self.distribution_equivalence_tolerance - DRIFT_DISTRIBUTION_EQUIVALENCE_TOLERANCE)
            > _FLOAT_TOLERANCE
        ):
            raise ValueError("the distribution-equivalence tolerance is protocol-pinned")
        if (
            self.reference_evaluation_source_sha256
            != self.comparison.reference_evaluation_source_sha256
            or self.observed_evaluation_source_sha256
            != self.comparison.observed_evaluation_source_sha256
        ):
            raise ValueError("measurement source bindings must match the metric comparison")
        if self.intervention_type == DRIFT_INTERVENTION_TYPE:
            if self.status != "validity_review_required":
                raise ValueError(
                    "a fault-directed drift measurement stops at validity review; the "
                    "guardrail policy is not frozen"
                )
            return self
        metric_equivalent = (
            self.comparison.measured_primary_outcome == "stable"
            and abs(self.comparison.accuracy_delta) <= _FLOAT_TOLERANCE
            and abs(self.comparison.macro_f1_delta) <= _FLOAT_TOLERANCE
            and abs(self.comparison.minority_recall_delta) <= _FLOAT_TOLERANCE
        )
        distribution_equivalent = (
            self.distribution_total_variation <= self.distribution_equivalence_tolerance
            and self.population_stability_index <= self.distribution_equivalence_tolerance
        )
        expected: DriftMeasurementStatus = (
            "equivalence_verified_pending_admission"
            if metric_equivalent and distribution_equivalent
            else "benign_equivalence_failure"
        )
        if self.status != expected:
            raise ValueError(
                f"the control status must be derived from the recomputed metrics; "
                f"expected {expected!r}"
            )
        return self

    def canonical_sha256(self) -> str:
        """Digest the complete measurement, including both source bindings."""

        return canonical_sha256(
            {"schema_version": self.schema_version, "measurement": self.model_dump(mode="json")}
        )


def measure_drift_candidate(
    *,
    result: CategoricalDriftResult,
    source: DriftEvaluationSource,
    spec: CategoricalDriftSpec,
    slot: CandidateSlot,
    test_set: CleanTestSet,
    observed_set: DriftObservedEvaluationSet,
    clean_reference_predictions: PredictionVector,
    observed_predictions: PredictionVector,
) -> DriftMeasurement:
    """Score a drift batch against the clean reference run.

    The outcome is whatever the vectors say. A benign control that moved any of
    the three reported metrics is reported as an equivalence failure; it is
    never relabelled stable, and it never becomes an eligible failure.
    """

    validated = validate_categorical_drift(result, source=source, spec=spec, slot=slot)
    source = _revalidated(source)
    test_set = _validate_clean_test_binding(source=source, test_set=test_set)
    observed_set = validate_drift_observed_evaluation_set(
        observed_set,
        result=validated,
        source=source,
        test_set=test_set,
    )
    comparison = compare_drift_metrics(
        test_set=test_set,
        observed_set=observed_set,
        clean_reference_predictions=clean_reference_predictions,
        observed_predictions=observed_predictions,
    )
    intervention_type = validated.provenance.intervention_type
    total_variation = distribution_total_variation(
        dict(validated.reference_distribution),
        dict(validated.achieved_distribution),
    )
    if intervention_type == DRIFT_INTERVENTION_TYPE:
        status: DriftMeasurementStatus = "validity_review_required"
    else:
        distribution_equivalent = (
            total_variation <= DRIFT_DISTRIBUTION_EQUIVALENCE_TOLERANCE
            and validated.provenance.population_stability_index
            <= DRIFT_DISTRIBUTION_EQUIVALENCE_TOLERANCE
        )
        metric_equivalent = (
            comparison.measured_primary_outcome == "stable"
            and abs(comparison.accuracy_delta) <= _FLOAT_TOLERANCE
            and abs(comparison.macro_f1_delta) <= _FLOAT_TOLERANCE
            and abs(comparison.minority_recall_delta) <= _FLOAT_TOLERANCE
        )
        status = (
            "equivalence_verified_pending_admission"
            if distribution_equivalent and metric_equivalent
            else "benign_equivalence_failure"
        )
    return DriftMeasurement(
        schema_version=DRIFT_MEASUREMENT_SCHEMA_VERSION,
        intervention_type=intervention_type,
        drift_artifact_sha256=validated.artifact_sha256(),
        drift_slot_sha256=validated.provenance.drift_slot_sha256,
        reference_evaluation_source_sha256=test_set.artifact_sha256(),
        observed_evaluation_source_sha256=observed_set.artifact_sha256(),
        distribution_total_variation=total_variation,
        population_stability_index=validated.provenance.population_stability_index,
        distribution_equivalence_tolerance=DRIFT_DISTRIBUTION_EQUIVALENCE_TOLERANCE,
        comparison=comparison,
        status=status,
    )


def validate_drift_measurement(
    measurement: DriftMeasurement,
    *,
    result: CategoricalDriftResult,
    source: DriftEvaluationSource,
    spec: CategoricalDriftSpec,
    slot: CandidateSlot,
    test_set: CleanTestSet,
    observed_set: DriftObservedEvaluationSet,
    clean_reference_predictions: PredictionVector,
    observed_predictions: PredictionVector,
) -> DriftMeasurement:
    """Recompute a drift measurement and reject forged or replayed records."""

    measurement = _revalidated(measurement)
    expected = measure_drift_candidate(
        result=result,
        source=source,
        spec=spec,
        slot=slot,
        test_set=test_set,
        observed_set=observed_set,
        clean_reference_predictions=clean_reference_predictions,
        observed_predictions=observed_predictions,
    )
    if measurement.canonical_sha256() != expected.canonical_sha256():
        _fail("the drift measurement does not match the recomputed bound measurement")
    return measurement
