"""Schema and hard ground-truth boundary for P1 benchmark cases.

A benchmark case is split into four payloads with an enforced trust boundary:

- ``DiagnosisInput`` is the ONLY thing a diagnosis model may see. It carries
  observable signals and a neutral task prompt, built by a whitelist projection
  (``project_diagnosis_input``) so a hidden field can never leak by omission.
- ``CaseGroundTruth`` is the hidden answer key (evaluator-only).
- ``InjectionProvenance`` records how the case was produced.
- ``CaseManifest`` is the internal manifest tying everything together.

Metric outcomes are classified honestly from the measured accuracy delta on the
held-out test split (regression / improvement / stable); the injection is always
``data_drift``, but its effect on the model is not forced to be a regression.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

SCHEMA_VERSION = "p1-cases/2"

EvidenceCondition = Literal["full", "missing_key", "noisy"]
EVIDENCE_CONDITIONS: tuple[EvidenceCondition, ...] = ("full", "missing_key", "noisy")

MetricOutcome = Literal["regression", "improvement", "stable"]
CaseRole = Literal["failure", "control"]

# Fixed decision threshold for classifying a measured accuracy delta.
METRIC_CHANGE_THRESHOLD = 0.01
# A distractor feature may only be labelled "stable" if its PSI is at most this.
DISTRACTOR_STABLE_PSI_MAX = 0.01

_OUTCOME_SYMPTOM: dict[str, str] = {
    "regression": "metric_regression",
    "improvement": "metric_improvement",
    "stable": "metric_stable",
}

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

EXPECTED_BEHAVIOR: dict[str, str] = {
    "full": (
        "Sufficient evidence: the diagnoser may state a root-cause hypothesis and "
        "must cite the distribution/PSI/metric signals it relies on."
    ),
    "missing_key": (
        "The decisive before/after comparison is withheld: a grounded diagnosis "
        "must abstain or name the missing evidence, not assert a cause confidently."
    ),
    "noisy": (
        "Decisive evidence is present alongside a measured, unrelated distractor: "
        "the diagnoser must rely on the supporting signals and not be led astray."
    ),
}


def classify_outcome(delta: float, threshold: float = METRIC_CHANGE_THRESHOLD) -> MetricOutcome:
    """Classify a measured metric delta into regression / improvement / stable."""

    if delta <= -threshold:
        return "regression"
    if delta >= threshold:
        return "improvement"
    return "stable"


def case_role_for(outcome: MetricOutcome) -> CaseRole:
    """A regression is a failure case; improvement/stable are controls."""

    return "failure" if outcome == "regression" else "control"


def expected_symptom_for(outcome: MetricOutcome) -> str:
    """Return the outcome-consistent expected symptom label."""

    return _OUTCOME_SYMPTOM[outcome]


class MetricComparison(BaseModel):
    """A measured baseline metric on a clean vs a drifted evaluation split.

    Fails closed: only supported metric/split, probabilities in [0, 1], all
    finite, and ``delta`` must equal ``observed - reference``.
    """

    metric: Literal["accuracy"]
    reference_split: Literal["test"]
    reference: float
    observed: float
    delta: float

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
    def _delta_matches(self) -> MetricComparison:
        if not math.isfinite(self.delta):
            msg = "delta must be finite"
            raise ValueError(msg)
        if abs(self.delta - (self.observed - self.reference)) > 1e-12:
            msg = "delta must equal observed - reference"
            raise ValueError(msg)
        return self


class DistractorComparison(BaseModel):
    """A measured before/after comparison for an unrelated distractor feature."""

    feature: str
    distribution_reference: dict[str, float]
    distribution_observed: dict[str, float]
    psi: float


class ObservableSignals(BaseModel):
    """Evidence-safe signals a diagnoser may see. Never names the cause."""

    candidate_feature: str | None = None
    distribution_reference: dict[str, float] | None = None
    distribution_observed: dict[str, float] | None = None
    psi: float | None = None
    sample_size: int | None = None
    baseline_metric_reference: MetricComparison | None = None
    distractor_comparisons: list[DistractorComparison] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DiagnosisInput(BaseModel):
    """The diagnosis-visible payload. Must contain no ground truth."""

    schema_version: str = SCHEMA_VERSION
    public_id: str
    evidence_condition: EvidenceCondition
    dataset_id: str
    dataset_sha256: str
    split_manifest_sha256: str
    task_prompt: str
    observable_signals: ObservableSignals


class CaseGroundTruth(BaseModel):
    """Hidden answer key plus the injection parameters an evaluator needs."""

    cause_label: str
    causal_mechanism: str
    injected_change: str
    affected_components: list[str]
    expected_symptoms: list[str]
    injection_parameters: dict[str, object]
    metric_outcome: MetricOutcome
    metric_delta: float
    case_role: CaseRole


class InjectionProvenance(BaseModel):
    """Provenance for one injection (reproduction, not diagnosis-visible)."""

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


class CaseManifest(BaseModel):
    """Internal manifest for one case (not shown to the diagnoser)."""

    schema_version: str = SCHEMA_VERSION
    case_id: str
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
    expected_diagnosis_behavior: str
    observable_signals: ObservableSignals
    artifacts: dict[str, str]
    reproduction: dict[str, str]
    ground_truth_ref: str
    split: str
    tag: str


def project_diagnosis_input(manifest: CaseManifest) -> DiagnosisInput:
    """Build the diagnosis-visible payload by whitelisting safe fields only."""

    return DiagnosisInput(
        schema_version=manifest.schema_version,
        public_id=manifest.public_id,
        evidence_condition=manifest.evidence_condition,
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
