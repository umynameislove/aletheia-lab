"""Condition-safe diagnosis projections for accepted Phase 2 families.

The mechanism validators establish that an artifact belongs to a frozen
candidate.  This module starts strictly after that boundary: it binds neutral,
typed observations to the validated candidate and materialises the context set
permitted by the accepted family class.

Condition labels remain evaluator-side in :class:`ContextEntry`.  The payload a
diagnoser receives contains only a candidate-binding digest and whitelisted
evidence items.  Missing and noisy siblings are derived from the same in-memory
evidence object, so callers cannot independently assemble three payloads that
quietly describe different families.
"""

from __future__ import annotations

import math
import unicodedata
from typing import Annotated, Final, Literal, NoReturn, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.benchmark.p2.binary_evaluation import (
    PRIMARY_ACCURACY_THRESHOLD,
    BinaryMetricSnapshot,
    ConfusionMatrix,
    MetricComparison,
)
from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import (
    CONTEXT_CARDINALITY,
    ContextEntry,
    EvidenceConditionName,
    FamilyCensusEntry,
    context_id_for,
)
from aletheia_lab.benchmark.p2.data_drift import DriftMetricComparison
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN
from aletheia_lab.benchmark.p2.label_noise import (
    TargetDistributionComparison,
    TargetQualityAudit,
)
from aletheia_lab.benchmark.p2.mechanism_validation import ValidatedMechanismCandidate
from aletheia_lab.benchmark.p2.validation import ContractViolation

DIAGNOSIS_PROJECTION_SCHEMA_VERSION: Final[Literal["p2-diagnosis-projection/v1"]] = (
    "p2-diagnosis-projection/v1"
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
_FLOAT_TOLERANCE: Final[float] = 1e-12
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class EvidenceProjectionError(ContractViolation):
    """Raised when evidence, candidate, family or projected contexts disagree."""


def _fail(message: str) -> NoReturn:
    raise EvidenceProjectionError(message)


def _revalidated(model: _ModelT) -> _ModelT:
    """Re-run validators even if an unsafe Pydantic constructor was used."""

    return type(model).model_validate(model.model_dump())


def _finite(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DiagnosisMetricSnapshot(_StrictFrozenModel):
    """Diagnosis-safe rates for one evaluation run."""

    accuracy: float = Field(ge=0.0, le=1.0)
    macro_f1: float = Field(ge=0.0, le=1.0)
    minority_recall: float = Field(ge=0.0, le=1.0)

    @field_validator("accuracy", "macro_f1", "minority_recall")
    @classmethod
    def _rates_are_finite(cls, value: float) -> float:
        return _finite(value, "metric rate")


class DiagnosisPerformanceComparison(_StrictFrozenModel):
    """Visible metric comparison with provenance and outcome labels removed."""

    reference_sample_size: int = Field(ge=1)
    observed_sample_size: int = Field(ge=1)
    reference: DiagnosisMetricSnapshot
    observed: DiagnosisMetricSnapshot
    accuracy_delta: float
    macro_f1_delta: float
    minority_recall_delta: float

    @field_validator("accuracy_delta", "macro_f1_delta", "minority_recall_delta")
    @classmethod
    def _deltas_are_finite(cls, value: float) -> float:
        return _finite(value, "metric delta")

    @model_validator(mode="after")
    def _deltas_are_derived(self) -> DiagnosisPerformanceComparison:
        for name, declared, expected in (
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
        ):
            if abs(declared - expected) > _FLOAT_TOLERANCE:
                raise ValueError(f"{name} must be observed minus reference")
        return self


class DiagnosisConfusionComparison(_StrictFrozenModel):
    """Visible confusion counts for the same two runs."""

    reference: ConfusionMatrix
    observed: ConfusionMatrix


class PerformanceEvidence(_StrictFrozenModel):
    """Cross-checked performance rates and confusion counts."""

    performance_comparison: DiagnosisPerformanceComparison
    confusion_comparison: DiagnosisConfusionComparison

    @model_validator(mode="after")
    def _rates_match_confusion_counts(self) -> PerformanceEvidence:
        comparison = self.performance_comparison
        confusion = self.confusion_comparison
        pairs = (
            (
                "reference",
                comparison.reference_sample_size,
                comparison.reference,
                confusion.reference,
            ),
            ("observed", comparison.observed_sample_size, comparison.observed, confusion.observed),
        )
        for role, sample_size, rates, counts in pairs:
            if counts.total != sample_size:
                raise ValueError(f"{role} confusion counts must add up to its sample size")
            for name, declared, expected in (
                ("accuracy", rates.accuracy, counts.accuracy()),
                ("macro_f1", rates.macro_f1, counts.macro_f1()),
                ("minority_recall", rates.minority_recall, counts.minority_recall()),
            ):
                if abs(declared - expected) > _FLOAT_TOLERANCE:
                    raise ValueError(f"{role} {name} must be derived from confusion counts")
        return self


def _safe_snapshot(snapshot: BinaryMetricSnapshot) -> DiagnosisMetricSnapshot:
    snapshot = _revalidated(snapshot)
    return DiagnosisMetricSnapshot(
        accuracy=snapshot.accuracy,
        macro_f1=snapshot.macro_f1,
        minority_recall=snapshot.minority_recall,
    )


def performance_evidence_from(
    comparison: MetricComparison | DriftMetricComparison,
) -> PerformanceEvidence:
    """Whitelist performance evidence from either authoritative comparison type."""

    comparison = _revalidated(comparison)
    return PerformanceEvidence(
        performance_comparison=DiagnosisPerformanceComparison(
            reference_sample_size=comparison.reference.prediction_count,
            observed_sample_size=comparison.observed.prediction_count,
            reference=_safe_snapshot(comparison.reference),
            observed=_safe_snapshot(comparison.observed),
            accuracy_delta=comparison.accuracy_delta,
            macro_f1_delta=comparison.macro_f1_delta,
            minority_recall_delta=comparison.minority_recall_delta,
        ),
        confusion_comparison=DiagnosisConfusionComparison(
            reference=comparison.reference.confusion,
            observed=comparison.observed.confusion,
        ),
    )


class CategoryShare(_StrictFrozenModel):
    """One neutral category and its observed proportion."""

    category: str = Field(min_length=1, max_length=256)
    proportion: float = Field(ge=0.0, le=1.0)

    @field_validator("category")
    @classmethod
    def _category_is_canonical(cls, value: str) -> str:
        if value != value.strip() or value != unicodedata.normalize("NFC", value):
            raise ValueError("category must be trimmed Unicode NFC")
        return value

    @field_validator("proportion")
    @classmethod
    def _proportion_is_finite(cls, value: float) -> float:
        return _finite(value, "category proportion")


class DistributionSnapshot(_StrictFrozenModel):
    """A diagnosis-facing categorical distribution and its sample size."""

    sample_size: int = Field(ge=1)
    categories: tuple[CategoryShare, ...]

    @model_validator(mode="after")
    def _distribution_is_canonical(self) -> DistributionSnapshot:
        names = tuple(item.category for item in self.categories)
        if not names:
            raise ValueError("a distribution must contain at least one category")
        if len(set(names)) != len(names):
            raise ValueError("distribution categories must be unique")
        if names != tuple(sorted(names)):
            raise ValueError("distribution categories must use canonical sorted order")
        if abs(math.fsum(item.proportion for item in self.categories) - 1.0) > _FLOAT_TOLERANCE:
            raise ValueError("distribution proportions must sum to one")
        return self


class SecondaryComparison(_StrictFrozenModel):
    """Neutral noisy-condition evidence proven stable by construction."""

    reference_value: float
    observed_value: float
    absolute_delta: float = Field(ge=0.0)
    stability_bound: float = Field(gt=0.0)

    @field_validator("reference_value", "observed_value", "absolute_delta", "stability_bound")
    @classmethod
    def _values_are_finite(cls, value: float) -> float:
        return _finite(value, "secondary comparison value")

    @model_validator(mode="after")
    def _comparison_is_stable(self) -> SecondaryComparison:
        expected = abs(self.observed_value - self.reference_value)
        if abs(self.absolute_delta - expected) > _FLOAT_TOLERANCE:
            raise ValueError("absolute_delta must be derived from the two values")
        if self.absolute_delta > self.stability_bound + _FLOAT_TOLERANCE:
            raise ValueError("secondary comparison must remain inside its stability bound")
        return self


class DataDriftDiagnosisEvidence(_StrictFrozenModel):
    """Neutral observable evidence for one distribution-shift candidate."""

    kind: Literal["distribution_observations"] = "distribution_observations"
    performance: PerformanceEvidence
    reference_distribution: DistributionSnapshot
    observed_distribution: DistributionSnapshot
    population_stability_index: float = Field(ge=0.0)
    secondary_comparison: SecondaryComparison | None = None

    @field_validator("population_stability_index")
    @classmethod
    def _psi_is_finite(cls, value: float) -> float:
        return _finite(value, "population stability index")

    @model_validator(mode="after")
    def _categories_align(self) -> DataDriftDiagnosisEvidence:
        reference = tuple(item.category for item in self.reference_distribution.categories)
        observed = tuple(item.category for item in self.observed_distribution.categories)
        if reference != observed:
            raise ValueError("reference and observed distributions must name the same categories")
        return self


class LabelDiagnosisEvidence(_StrictFrozenModel):
    """Aggregate-only observable evidence for one target-quality candidate."""

    kind: Literal["target_observations"] = "target_observations"
    performance: PerformanceEvidence
    target_distribution_comparison: TargetDistributionComparison
    target_quality_audit_summary: TargetQualityAudit
    secondary_comparison: SecondaryComparison | None = None

    @model_validator(mode="after")
    def _aggregate_sample_sizes_align(self) -> LabelDiagnosisEvidence:
        comparison = self.target_distribution_comparison
        total = comparison.reference_positive_count + comparison.reference_negative_count
        if self.target_quality_audit_summary.audited_record_count != total:
            raise ValueError("target audit and distribution comparison must use the same sample")
        return self


class TransformSignatureComparison(_StrictFrozenModel):
    """Opaque signatures of the reference and observed transform outputs."""

    reference_signature_sha256: Sha256
    observed_signature_sha256: Sha256
    signatures_equal: bool

    @model_validator(mode="after")
    def _equality_is_derived(self) -> TransformSignatureComparison:
        if self.signatures_equal != (
            self.reference_signature_sha256 == self.observed_signature_sha256
        ):
            raise ValueError("signatures_equal must be derived from the two signatures")
        return self


class TargetProjectionComparison(_StrictFrozenModel):
    """Aggregate transform-output difference without row or mapping details."""

    sample_size: int = Field(ge=1)
    differing_record_count: int = Field(ge=0)
    difference_rate: float = Field(ge=0.0, le=1.0)
    reference_projection_sha256: Sha256
    observed_projection_sha256: Sha256

    @field_validator("difference_rate")
    @classmethod
    def _rate_is_finite(cls, value: float) -> float:
        return _finite(value, "target projection difference rate")

    @model_validator(mode="after")
    def _difference_is_derived(self) -> TargetProjectionComparison:
        if self.differing_record_count > self.sample_size:
            raise ValueError("differing records cannot exceed the sample size")
        expected = self.differing_record_count / self.sample_size
        if abs(self.difference_rate - expected) > _FLOAT_TOLERANCE:
            raise ValueError("difference_rate must be derived from the two counts")
        equal = self.reference_projection_sha256 == self.observed_projection_sha256
        if equal != (self.differing_record_count == 0):
            raise ValueError("projection signatures and differing count must agree")
        return self


class SchemaComparison(_StrictFrozenModel):
    """Shape-only schema evidence without feature names or configuration."""

    reference_field_count: int = Field(ge=1)
    observed_field_count: int = Field(ge=1)
    field_sets_equal: bool

    @model_validator(mode="after")
    def _equality_allows_equal_counts(self) -> SchemaComparison:
        if self.field_sets_equal and self.reference_field_count != self.observed_field_count:
            raise ValueError("equal field sets must have equal field counts")
        return self


class PreprocessingDiagnosisEvidence(_StrictFrozenModel):
    """Neutral observable evidence for one inference-transform candidate."""

    kind: Literal["transform_observations"] = "transform_observations"
    performance: PerformanceEvidence
    transform_signature_comparison: TransformSignatureComparison
    target_projection_comparison: TargetProjectionComparison
    schema_comparison: SchemaComparison
    secondary_comparison: SecondaryComparison | None = None


MechanismDiagnosisEvidence = (
    DataDriftDiagnosisEvidence | LabelDiagnosisEvidence | PreprocessingDiagnosisEvidence
)


def _item(evidence_id: str, payload: BaseModel) -> dict[str, object]:
    return {"id": evidence_id, "payload": payload.model_dump(mode="json")}


def _full_items(evidence: MechanismDiagnosisEvidence) -> tuple[dict[str, object], ...]:
    performance = evidence.performance
    if isinstance(evidence, DataDriftDiagnosisEvidence):
        return (
            _item("distribution-reference", evidence.reference_distribution),
            _item("distribution-observed", evidence.observed_distribution),
            _item(
                "population-stability-summary",
                _ScalarValue(value=evidence.population_stability_index),
            ),
            _item("performance-comparison", performance.performance_comparison),
            _item("confusion-comparison", performance.confusion_comparison),
        )
    if isinstance(evidence, LabelDiagnosisEvidence):
        return (
            _item("target-distribution-comparison", evidence.target_distribution_comparison),
            _item("target-quality-audit-summary", evidence.target_quality_audit_summary),
            _item("performance-comparison", performance.performance_comparison),
            _item("confusion-comparison", performance.confusion_comparison),
        )
    return (
        _item("transform-signature-comparison", evidence.transform_signature_comparison),
        _item("target-projection-comparison", evidence.target_projection_comparison),
        _item("performance-comparison", performance.performance_comparison),
        _item("schema-comparison", evidence.schema_comparison),
    )


class _ScalarValue(_StrictFrozenModel):
    value: float

    @field_validator("value")
    @classmethod
    def _value_is_finite(cls, value: float) -> float:
        return _finite(value, "scalar evidence value")


def _missing_items(evidence: MechanismDiagnosisEvidence) -> tuple[dict[str, object], ...]:
    performance = evidence.performance
    if isinstance(evidence, DataDriftDiagnosisEvidence):
        return (
            _item("distribution-observed", evidence.observed_distribution),
            _item("confusion-comparison", performance.confusion_comparison),
        )
    if isinstance(evidence, LabelDiagnosisEvidence):
        return (
            _item("performance-comparison", performance.performance_comparison),
            _item("confusion-comparison", performance.confusion_comparison),
        )
    return (
        _item("performance-comparison", performance.performance_comparison),
        _item("schema-comparison", evidence.schema_comparison),
    )


def _projection(
    *,
    source_binding_sha256: str,
    items: tuple[dict[str, object], ...],
) -> dict[str, object]:
    return {
        "schema_version": DIAGNOSIS_PROJECTION_SCHEMA_VERSION,
        "source_binding_sha256": source_binding_sha256,
        "items": list(items),
    }


def _context_entry(
    *,
    family: FamilyCensusEntry,
    condition: EvidenceConditionName,
    projection: dict[str, object],
) -> ContextEntry:
    return ContextEntry(
        diagnosis_context_id=context_id_for(
            case_family_id=family.case_family_id,
            evidence_condition=condition,
        ),
        case_family_id=family.case_family_id,
        evidence_condition=condition,
        diagnosis_projection=projection,
        diagnosis_projection_sha256=canonical_sha256(projection),
    )


def _validate_binding(
    *,
    candidate: ValidatedMechanismCandidate,
    family: FamilyCensusEntry,
) -> None:
    if candidate.disposition.disposition != "technically_valid":
        _fail("a technically rejected candidate cannot produce diagnosis contexts")
    if family.candidate_id != candidate.candidate_id:
        _fail("family census entry belongs to a different candidate")
    if family.fault_type != candidate.fault_type:
        _fail("family census mechanism differs from the validated candidate")
    if family.proposed_family_sha256 != candidate.proposed_family_sha256:
        _fail("family census fingerprint differs from the validated candidate")


def _validate_decisive_failure_evidence(evidence: MechanismDiagnosisEvidence) -> None:
    """Ensure an eligible full view actually contains a decisive observation."""

    if (
        evidence.performance.performance_comparison.accuracy_delta
        > -PRIMARY_ACCURACY_THRESHOLD + _FLOAT_TOLERANCE
    ):
        _fail("eligible-failure performance evidence must show the frozen accuracy regression")
    if isinstance(evidence, DataDriftDiagnosisEvidence):
        if (
            evidence.reference_distribution == evidence.observed_distribution
            or evidence.population_stability_index <= _FLOAT_TOLERANCE
        ):
            _fail("eligible distribution evidence must show a non-zero distribution change")
    elif isinstance(evidence, LabelDiagnosisEvidence):
        if evidence.target_quality_audit_summary.disagreeing_record_count == 0:
            _fail("eligible target-quality evidence must contain aggregate disagreement")
    elif (
        evidence.transform_signature_comparison.signatures_equal
        or evidence.target_projection_comparison.differing_record_count == 0
    ):
        _fail("eligible transform evidence must show a changed target projection")


def build_diagnosis_contexts(
    *,
    candidate: ValidatedMechanismCandidate,
    family: FamilyCensusEntry,
    evidence: MechanismDiagnosisEvidence | None,
) -> tuple[ContextEntry, ...]:
    """Build the exact diagnosis-context set allowed for one accepted family."""

    candidate = _revalidated(candidate)
    family = _revalidated(family)
    _validate_binding(candidate=candidate, family=family)

    expected_conditions = CONTEXT_CARDINALITY[family.family_class]
    if not expected_conditions:
        if evidence is not None:
            _fail("a benign family must not receive diagnosis evidence")
        return ()
    if evidence is None:
        _fail("a family with diagnosis contexts requires observable evidence")
    evidence = _revalidated(evidence)

    expected_kind = {
        "data_drift": DataDriftDiagnosisEvidence,
        "label_noise": LabelDiagnosisEvidence,
        "preprocessing_bug": PreprocessingDiagnosisEvidence,
    }[candidate.fault_type]
    if not isinstance(evidence, expected_kind):
        _fail("diagnosis evidence belongs to a different mechanism")

    full = _full_items(evidence)
    by_condition: dict[str, tuple[dict[str, object], ...]] = {"full": full}
    if family.family_class == "eligible_failure":
        _validate_decisive_failure_evidence(evidence)
        if evidence.secondary_comparison is None:
            _fail("an eligible failure requires a stable secondary comparison")
        by_condition["missing_key"] = _missing_items(evidence)
        by_condition["noisy"] = full + (
            _item("secondary-comparison", evidence.secondary_comparison),
        )

    source_binding_sha256 = candidate.binding_sha256()
    condition_order: tuple[EvidenceConditionName, ...] = ("full", "missing_key", "noisy")
    entries = tuple(
        _context_entry(
            family=family,
            condition=condition,
            projection=_projection(
                source_binding_sha256=source_binding_sha256,
                items=by_condition[condition],
            ),
        )
        for condition in condition_order
        if condition in expected_conditions
    )
    if {entry.evidence_condition for entry in entries} != set(expected_conditions):
        _fail("projected contexts do not match the family-class cardinality")
    return entries


def validate_diagnosis_contexts(
    contexts: tuple[ContextEntry, ...],
    *,
    candidate: ValidatedMechanismCandidate,
    family: FamilyCensusEntry,
    evidence: MechanismDiagnosisEvidence | None,
) -> tuple[ContextEntry, ...]:
    """Recompute and compare contexts, catching tamper and cross-family replay."""

    contexts = tuple(_revalidated(context) for context in contexts)
    expected = build_diagnosis_contexts(candidate=candidate, family=family, evidence=evidence)
    actual_payload = tuple(context.model_dump(mode="json") for context in contexts)
    expected_payload = tuple(context.model_dump(mode="json") for context in expected)
    if actual_payload != expected_payload:
        _fail("diagnosis contexts differ from the canonical family projection")
    return contexts
