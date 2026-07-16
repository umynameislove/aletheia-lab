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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.benchmark.signals import population_stability_index

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
        "Decisive evidence is present alongside a measured, operationally low-shift distractor: "
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


class _StrictModel(BaseModel):
    """Base for case artifacts: reject unknown fields and implicit type coercion."""

    model_config = ConfigDict(extra="forbid", strict=True)


class MetricComparison(_StrictModel):
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


class DistractorComparison(_StrictModel):
    """A measured before/after comparison for a low-shift distractor feature.

    Fails closed: non-empty feature and distributions, finite non-negative weights
    that sum to ~1, finite PSI, and PSI within [0, DISTRACTOR_STABLE_PSI_MAX] so a
    feature can only be recorded here when it is operationally low-shift under the
    injection.
    """

    feature: str
    distribution_reference: dict[str, float]
    distribution_observed: dict[str, float]
    psi: float

    @field_validator("feature")
    @classmethod
    def _feature_nonempty(cls, value: str) -> str:
        if not value.strip():
            msg = "distractor feature must not be empty"
            raise ValueError(msg)
        return value

    @field_validator("distribution_reference", "distribution_observed")
    @classmethod
    def _valid_distribution(cls, dist: dict[str, float]) -> dict[str, float]:
        if not dist:
            msg = "distractor distribution must not be empty"
            raise ValueError(msg)
        for category, weight in dist.items():
            if not math.isfinite(weight) or weight < 0:
                msg = f"distractor weight for {category!r} must be finite and non-negative"
                raise ValueError(msg)
        if abs(sum(dist.values()) - 1.0) > 1e-6:
            msg = "distractor distribution must sum to ~1.0"
            raise ValueError(msg)
        return dist

    @model_validator(mode="after")
    def _valid_psi(self) -> DistractorComparison:
        if not math.isfinite(self.psi):
            msg = "distractor psi must be finite"
            raise ValueError(msg)
        if not 0.0 <= self.psi <= DISTRACTOR_STABLE_PSI_MAX:
            msg = f"distractor psi {self.psi} out of [0, {DISTRACTOR_STABLE_PSI_MAX}]"
            raise ValueError(msg)
        computed = population_stability_index(
            self.distribution_reference, self.distribution_observed
        )
        if abs(self.psi - computed) > 1e-9:
            msg = f"distractor psi {self.psi} inconsistent with its distributions ({computed})"
            raise ValueError(msg)
        return self


class ObservableSignals(_StrictModel):
    """Evidence-safe signals a diagnoser may see. Never names the cause."""

    candidate_feature: str | None = None
    distribution_reference: dict[str, float] | None = None
    distribution_observed: dict[str, float] | None = None
    psi: float | None = None
    sample_size: int | None = None
    baseline_metric_reference: MetricComparison | None = None
    distractor_comparisons: list[DistractorComparison] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @field_validator("sample_size")
    @classmethod
    def _positive_sample_size(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            msg = f"sample_size must be positive, got {value}"
            raise ValueError(msg)
        return value


class DiagnosisInput(_StrictModel):
    """The diagnosis-visible payload. Must contain no ground truth."""

    schema_version: str = SCHEMA_VERSION

    @field_validator("schema_version")
    @classmethod
    def _locked_schema_version(cls, value: str) -> str:
        if value != SCHEMA_VERSION:
            msg = f"unexpected schema_version {value!r}; expected {SCHEMA_VERSION!r}"
            raise ValueError(msg)
        return value

    public_id: str
    evidence_condition: EvidenceCondition
    dataset_id: str
    dataset_sha256: str
    split_manifest_sha256: str
    task_prompt: str
    observable_signals: ObservableSignals


class CaseGroundTruth(_StrictModel):
    """Hidden answer key plus the injection parameters an evaluator needs.

    Fails closed on internal inconsistency: ``metric_delta`` must be finite,
    ``metric_outcome`` must be its threshold classification, ``case_role`` must
    follow the outcome, and ``expected_symptoms`` must contain the outcome's
    symptom and no conflicting metric symptom.
    """

    cause_label: str
    causal_mechanism: str
    injected_change: str
    affected_components: list[str]
    expected_symptoms: list[str]
    injection_parameters: dict[str, object]
    metric_outcome: MetricOutcome
    metric_delta: float
    case_role: CaseRole

    @model_validator(mode="after")
    def _consistent(self) -> CaseGroundTruth:
        if not math.isfinite(self.metric_delta):
            msg = "metric_delta must be finite"
            raise ValueError(msg)
        if self.metric_outcome != classify_outcome(self.metric_delta):
            msg = "metric_outcome does not match classify_outcome(metric_delta)"
            raise ValueError(msg)
        if self.case_role != case_role_for(self.metric_outcome):
            msg = "case_role does not match metric_outcome"
            raise ValueError(msg)
        right = expected_symptom_for(self.metric_outcome)
        if right not in self.expected_symptoms:
            msg = "expected_symptoms is missing the outcome symptom"
            raise ValueError(msg)
        conflicting = (_METRIC_SYMPTOMS - {right}) & set(self.expected_symptoms)
        if conflicting:
            msg = f"expected_symptoms contains conflicting metric symptom(s): {sorted(conflicting)}"
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
