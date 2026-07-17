"""Schema and hard ground-truth boundary for P1 benchmark cases.

A benchmark case is split into four payloads with an enforced trust boundary:

- ``DiagnosisInput`` is the ONLY thing a diagnosis model may see. It carries
  observable signals and a neutral task prompt, built by a whitelist projection
  (``project_diagnosis_input``) so a hidden field can never leak by omission.
- ``CaseGroundTruth`` is evaluator-only. It always records the intervention and
  measured outcome, but asserts a hidden failure cause only for eligible failures.
- ``InjectionProvenance`` records how the case was produced.
- ``CaseManifest`` is the internal manifest tying everything together.

The semantic chain is: injected change -> observed outcome -> versioned failure
eligibility -> optional hidden failure cause. The injection is always
``data_drift``, but its measured effect is not forced to be a failure.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.benchmark.signals import population_stability_index
from aletheia_lab.evidence.rubric import (
    EVIDENCE_CONDITIONS as _RUBRIC_EVIDENCE_CONDITIONS,
)
from aletheia_lab.evidence.rubric import (
    EvidenceCondition,
    ExpectedDiagnosisBehavior,
    expected_behavior_for,
)

EVIDENCE_CONDITIONS: tuple[EvidenceCondition, ...] = _RUBRIC_EVIDENCE_CONDITIONS

SCHEMA_VERSION = "p1-cases/5"
CASE_FAMILY_ID_VERSION = "p1-case-family/v1"
DIAGNOSIS_CONTEXT_ID_VERSION = "p1-diagnosis-context/v1"
ELIGIBILITY_POLICY_VERSION = "accuracy-regression/v1"

MetricOutcome = Literal["regression", "improvement", "stable"]
FailureEligibilityClass = Literal["eligible_failure", "stable_control", "improvement_control"]

# Fixed decision threshold for classifying a measured accuracy delta.
METRIC_CHANGE_THRESHOLD = 0.01
# A distractor feature may only be labelled "stable" if its PSI is at most this.
DISTRACTOR_STABLE_PSI_MAX = 0.01

_OUTCOME_SYMPTOM: dict[str, str] = {
    "regression": "metric_regression",
    "improvement": "metric_improvement",
    "stable": "metric_stable",
}
_METRIC_SYMPTOMS: frozenset[str] = frozenset(_OUTCOME_SYMPTOM.values())

FORBIDDEN_TERMS: tuple[str, ...] = (
    "data_drift",
    "data-drift",
    "data drift",
    "ground_truth",
    "ground truth",
    "answer_key",
    "answer key",
    "injected",
    "injection",
    "causal_mechanism",
    "categorical_distribution_shift",
    "cause_label",
)

EXPECTED_BEHAVIOR: dict[EvidenceCondition, ExpectedDiagnosisBehavior] = {
    condition: expected_behavior_for(condition) for condition in EVIDENCE_CONDITIONS
}


def classify_outcome(delta: float, threshold: float = METRIC_CHANGE_THRESHOLD) -> MetricOutcome:
    """Classify a measured metric delta into regression / improvement / stable."""

    if not math.isfinite(delta):
        msg = f"metric delta must be finite, got {delta}"
        raise ValueError(msg)
    if not math.isfinite(threshold) or threshold <= 0:
        msg = f"metric change threshold must be finite and positive, got {threshold}"
        raise ValueError(msg)
    if delta <= -threshold:
        return "regression"
    if delta >= threshold:
        return "improvement"
    return "stable"


def failure_eligibility_for(
    delta: float, threshold: float = METRIC_CHANGE_THRESHOLD
) -> FailureEligibilityClass:
    """Derive failure eligibility from a measured metric delta."""

    outcome = classify_outcome(delta, threshold)
    if outcome == "regression":
        return "eligible_failure"
    if outcome == "improvement":
        return "improvement_control"
    return "stable_control"


def expected_symptom_for(outcome: MetricOutcome) -> str:
    """Return the outcome-consistent expected symptom label."""

    return _OUTCOME_SYMPTOM[outcome]


class _StrictModel(BaseModel):
    """Base for case artifacts: reject unknown fields and implicit type coercion."""

    model_config = ConfigDict(extra="forbid", strict=True)


class InjectedChange(_StrictModel):
    """Structured intervention record with no failure assertion."""

    intervention_type: Literal["categorical_distribution_shift"]
    feature: str
    distribution_reference: dict[str, float]
    distribution_achieved: dict[str, float]

    @field_validator("feature")
    @classmethod
    def _feature_nonempty(cls, value: str) -> str:
        if not value.strip():
            msg = "injected change feature must not be empty"
            raise ValueError(msg)
        return value

    @field_validator("distribution_reference", "distribution_achieved")
    @classmethod
    def _valid_distribution(cls, dist: dict[str, float]) -> dict[str, float]:
        if not dist:
            msg = "injected change distribution must not be empty"
            raise ValueError(msg)
        for category, weight in dist.items():
            if not math.isfinite(weight) or weight < 0:
                msg = f"injected change weight for {category!r} must be finite and non-negative"
                raise ValueError(msg)
        if abs(sum(dist.values()) - 1.0) > 1e-6:
            msg = "injected change distribution must sum to ~1.0"
            raise ValueError(msg)
        return dist


class ObservedOutcome(_StrictModel):
    """Measured model outcome on a clean vs injected evaluation split.

    Fails closed: only supported metric/split, probabilities in [0, 1], all
    finite, ``delta`` equals ``observed - reference``, and ``classification``
    is derived from that delta under the canonical threshold.
    """

    metric: Literal["accuracy"]
    reference_split: Literal["test"]
    reference: float
    observed: float
    delta: float
    classification: MetricOutcome

    @field_validator("reference", "observed")
    @classmethod
    def _unit_interval(cls, value: float) -> float:
        if not math.isfinite(value):
            msg = "metric value must be finite"
            raise ValueError(msg)
        if not 0.0 <= value <= 1.0:
            msg = f"metric value out of [0, 1]: {value}"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _derived_fields_match(self) -> ObservedOutcome:
        if not math.isfinite(self.delta):
            msg = "delta must be finite"
            raise ValueError(msg)
        if abs(self.delta - (self.observed - self.reference)) > 1e-12:
            msg = "delta must equal observed - reference"
            raise ValueError(msg)
        expected = classify_outcome(self.delta)
        if self.classification != expected:
            msg = f"classification must be derived from delta ({expected})"
            raise ValueError(msg)
        return self


class FailureEligibility(_StrictModel):
    """Versioned decision about whether an observed outcome is a failure.

    The policy metadata is locked here. The enclosing ``CaseGroundTruth``
    recomputes ``classification`` from its ``ObservedOutcome`` so a caller
    cannot supply a synchronized but unsupported failure label.
    """

    policy_version: str = ELIGIBILITY_POLICY_VERSION
    metric_change_threshold: float = METRIC_CHANGE_THRESHOLD
    classification: FailureEligibilityClass

    @model_validator(mode="after")
    def _canonical_policy(self) -> FailureEligibility:
        if self.policy_version != ELIGIBILITY_POLICY_VERSION:
            msg = (
                f"unexpected eligibility policy {self.policy_version!r}; "
                f"expected {ELIGIBILITY_POLICY_VERSION!r}"
            )
            raise ValueError(msg)
        if not math.isfinite(self.metric_change_threshold):
            msg = "metric_change_threshold must be finite"
            raise ValueError(msg)
        if self.metric_change_threshold != METRIC_CHANGE_THRESHOLD:
            msg = (
                f"metric_change_threshold must be canonical ({METRIC_CHANGE_THRESHOLD}), "
                f"got {self.metric_change_threshold}"
            )
            raise ValueError(msg)
        return self


class AdditionalComparison(_StrictModel):
    """A neutral measured before/after comparison for an additional feature.

    Fails closed: non-empty feature and distributions, finite non-negative weights
    that sum to ~1, finite PSI, and PSI within [0, DISTRACTOR_STABLE_PSI_MAX] so a
    feature can only be recorded here when it is operationally low-shift. The
    serialized diagnosis payload deliberately does not label the item a distractor;
    that experimental role is evaluator-only metadata.
    """

    feature: str
    distribution_reference: dict[str, float]
    distribution_observed: dict[str, float]
    psi: float

    @field_validator("feature")
    @classmethod
    def _feature_nonempty(cls, value: str) -> str:
        if not value.strip():
            msg = "additional comparison feature must not be empty"
            raise ValueError(msg)
        return value

    @field_validator("distribution_reference", "distribution_observed")
    @classmethod
    def _valid_distribution(cls, dist: dict[str, float]) -> dict[str, float]:
        if not dist:
            msg = "additional comparison distribution must not be empty"
            raise ValueError(msg)
        for category, weight in dist.items():
            if not math.isfinite(weight) or weight < 0:
                msg = (
                    f"additional comparison weight for {category!r} must be finite and non-negative"
                )
                raise ValueError(msg)
        if abs(sum(dist.values()) - 1.0) > 1e-6:
            msg = "additional comparison distribution must sum to ~1.0"
            raise ValueError(msg)
        return dist

    @model_validator(mode="after")
    def _valid_psi(self) -> AdditionalComparison:
        if not math.isfinite(self.psi):
            msg = "additional comparison psi must be finite"
            raise ValueError(msg)
        if not 0.0 <= self.psi <= DISTRACTOR_STABLE_PSI_MAX:
            msg = f"additional comparison psi {self.psi} out of [0, {DISTRACTOR_STABLE_PSI_MAX}]"
            raise ValueError(msg)
        computed = population_stability_index(
            self.distribution_reference, self.distribution_observed
        )
        if abs(self.psi - computed) > 1e-9:
            msg = (
                f"additional comparison psi {self.psi} inconsistent with "
                f"its distributions ({computed})"
            )
            raise ValueError(msg)
        return self


class ObservableSignals(_StrictModel):
    """Evidence-safe signals a diagnoser may see. Never names the cause."""

    candidate_feature: str | None = None
    distribution_reference: dict[str, float] | None = None
    distribution_observed: dict[str, float] | None = None
    psi: float | None = None
    sample_size: int | None = None
    baseline_metric_reference: ObservedOutcome | None = None
    additional_comparisons: list[AdditionalComparison] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @field_validator("sample_size")
    @classmethod
    def _positive_sample_size(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            msg = f"sample_size must be positive, got {value}"
            raise ValueError(msg)
        return value


class DiagnosisInput(_StrictModel):
    """The diagnosis-visible payload. Must contain no ground truth or condition label."""

    schema_version: str = SCHEMA_VERSION

    @field_validator("schema_version")
    @classmethod
    def _locked_schema_version(cls, value: str) -> str:
        if value != SCHEMA_VERSION:
            msg = f"unexpected schema_version {value!r}; expected {SCHEMA_VERSION!r}"
            raise ValueError(msg)
        return value

    diagnosis_context_id: str = Field(pattern=r"^p1-context-[0-9a-f]{64}$")
    dataset_id: str
    dataset_sha256: str
    split_manifest_sha256: str
    task_prompt: str
    observable_signals: ObservableSignals


class HiddenFailureCause(_StrictModel):
    """Evaluator-only cause assertion, valid only for an eligible failure."""

    cause_label: str
    causal_mechanism: str
    affected_components: list[str]
    expected_symptoms: list[str]


class CaseGroundTruth(_StrictModel):
    """Evaluator-only semantics for an injected benchmark context.

    ``injected_change`` records the intervention regardless of its measured
    effect. A hidden failure cause exists only when the canonical, versioned
    eligibility policy classifies the observed outcome as an eligible failure.
    """

    injected_change: InjectedChange
    injection_parameters: dict[str, object]
    observed_outcome: ObservedOutcome
    failure_eligibility: FailureEligibility
    hidden_failure_cause: HiddenFailureCause | None = None

    @model_validator(mode="after")
    def _consistent(self) -> CaseGroundTruth:
        expected = failure_eligibility_for(self.observed_outcome.delta)
        if self.failure_eligibility.classification != expected:
            msg = f"failure eligibility must be recomputed from observed outcome ({expected})"
            raise ValueError(msg)
        if expected == "eligible_failure":
            if self.hidden_failure_cause is None:
                msg = "eligible failure requires hidden_failure_cause"
                raise ValueError(msg)
            right = expected_symptom_for(self.observed_outcome.classification)
            if right not in self.hidden_failure_cause.expected_symptoms:
                msg = "hidden failure cause is missing the measured outcome symptom"
                raise ValueError(msg)
            conflicting = (_METRIC_SYMPTOMS - {right}) & set(
                self.hidden_failure_cause.expected_symptoms
            )
            if conflicting:
                msg = (
                    "hidden failure cause contains conflicting metric symptom(s): "
                    f"{sorted(conflicting)}"
                )
                raise ValueError(msg)
        elif self.hidden_failure_cause is not None:
            msg = "control outcome must not assert hidden_failure_cause"
            raise ValueError(msg)
        return self


class InjectionProvenance(_StrictModel):
    """Provenance for one injection (reproduction, not diagnosis-visible).

    Fails closed: reference/achieved distributions must be non-empty, finite,
    non-negative and sum to ~1; PSI must be finite, non-negative and equal to the
    PSI recomputed from the two distributions; and ``output_size`` must be
    positive. This means a fabricated PSI cannot pass even if synced everywhere.
    """

    injection_id: str
    injector: str
    fault_type: str
    feature: str
    seed: int
    target_distribution: dict[str, float]
    achieved_distribution: dict[str, float]
    reference_distribution: dict[str, float]
    psi: float
    output_size: int
    dataset_id: str
    dataset_sha256: str

    @field_validator("target_distribution")
    @classmethod
    def _valid_target(cls, dist: dict[str, float]) -> dict[str, float]:
        if not dist:
            msg = "target_distribution must not be empty"
            raise ValueError(msg)
        for category, weight in dist.items():
            if not math.isfinite(weight) or weight < 0:
                msg = f"target_distribution weight for {category!r} must be finite and non-negative"
                raise ValueError(msg)
        if sum(dist.values()) <= 0:
            msg = "target_distribution total must be positive (raw weights are normalized)"
            raise ValueError(msg)
        return dist

    @field_validator("reference_distribution", "achieved_distribution")
    @classmethod
    def _valid_distribution(cls, dist: dict[str, float]) -> dict[str, float]:
        if not dist:
            msg = "distribution must not be empty"
            raise ValueError(msg)
        for category, weight in dist.items():
            if not math.isfinite(weight) or weight < 0:
                msg = f"distribution weight for {category!r} must be finite and non-negative"
                raise ValueError(msg)
        if abs(sum(dist.values()) - 1.0) > 1e-6:
            msg = "distribution must sum to ~1.0"
            raise ValueError(msg)
        return dist

    @model_validator(mode="after")
    def _valid_psi_and_size(self) -> InjectionProvenance:
        if self.output_size <= 0:
            msg = f"output_size must be positive, got {self.output_size}"
            raise ValueError(msg)
        if not math.isfinite(self.psi) or self.psi < 0:
            msg = f"psi must be finite and non-negative, got {self.psi}"
            raise ValueError(msg)
        computed = population_stability_index(
            self.reference_distribution, self.achieved_distribution
        )
        if abs(self.psi - computed) > 1e-9:
            msg = f"recorded psi {self.psi} does not match recomputed psi {computed}"
            raise ValueError(msg)
        unknown = set(self.target_distribution) - set(self.reference_distribution)
        if unknown:
            msg = f"target categories not present in the reference distribution: {sorted(unknown)}"
            raise ValueError(msg)
        return self


class CaseManifest(_StrictModel):
    """Internal manifest for one case (not shown to the diagnoser)."""

    schema_version: str = SCHEMA_VERSION

    @field_validator("schema_version")
    @classmethod
    def _locked_schema_version(cls, value: str) -> str:
        if value != SCHEMA_VERSION:
            msg = f"unexpected schema_version {value!r}; expected {SCHEMA_VERSION!r}"
            raise ValueError(msg)
        return value

    case_id: str
    case_family_id: str = Field(pattern=r"^p1-family-[0-9a-f]{64}$")
    public_id: str
    fault_type: str
    dataset_id: str
    dataset_sha256: str
    split_manifest_sha256: str
    injection_id: str
    injection_seed: int
    injection_parameters: dict[str, object]
    injection_setting: str
    severity_rank: int
    evidence_condition: EvidenceCondition
    evidence_bundle_id: str
    expected_diagnosis_behavior: ExpectedDiagnosisBehavior
    observable_signals: ObservableSignals
    artifacts: dict[str, str]
    reproduction: dict[str, str]
    ground_truth_ref: str
    split: str
    tag: str


def case_family_id_for(
    *,
    fault_type: str,
    dataset_id: str,
    dataset_sha256: str,
    split_manifest_sha256: str,
    injection_id: str,
    injector: str,
    feature: str,
    seed: int,
    target_distribution: dict[str, float],
    output_size: int,
) -> str:
    """Return a deterministic ID for one injection family.

    Evidence condition and context IDs are intentionally absent. Canonical JSON
    plus SHA-256 keeps the ID stable across processes and independent of Python's
    randomized hash implementation.
    """

    identity = {
        "version": CASE_FAMILY_ID_VERSION,
        "fault_type": fault_type,
        "dataset_id": dataset_id,
        "dataset_sha256": dataset_sha256,
        "split_manifest_sha256": split_manifest_sha256,
        "injection_id": injection_id,
        "injector": injector,
        "feature": feature,
        "seed": seed,
        "target_distribution": target_distribution,
        "output_size": output_size,
    }
    canonical = json.dumps(
        identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return f"p1-family-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def diagnosis_context_id_for(*, case_id: str, case_family_id: str) -> str:
    """Return an opaque, deterministic ID for a diagnosis-visible context.

    Internal case IDs contain evidence-condition slugs.  Hashing the canonical
    internal identity gives the diagnoser a stable citation namespace without
    exposing whether the context is ``full``, ``missing_key`` or ``noisy``.
    """

    identity = {
        "version": DIAGNOSIS_CONTEXT_ID_VERSION,
        "case_id": case_id,
        "case_family_id": case_family_id,
    }
    canonical = json.dumps(
        identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return f"p1-context-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def project_diagnosis_input(manifest: CaseManifest) -> DiagnosisInput:
    """Build the diagnosis-visible payload by whitelisting safe fields only."""

    return DiagnosisInput(
        schema_version=manifest.schema_version,
        diagnosis_context_id=diagnosis_context_id_for(
            case_id=manifest.case_id, case_family_id=manifest.case_family_id
        ),
        dataset_id=manifest.dataset_id,
        dataset_sha256=manifest.dataset_sha256,
        split_manifest_sha256=manifest.split_manifest_sha256,
        task_prompt=(
            "You are shown observed signals about a model's input data across two "
            "evaluation windows, plus a baseline metric reference. Identify the most "
            "likely root cause of the metric change, citing the signals you rely on. "
            "If the evidence is insufficient to decide, abstain and state what is "
            "missing."
        ),
        observable_signals=manifest.observable_signals,
    )
