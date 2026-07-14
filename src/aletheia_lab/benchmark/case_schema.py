"""Schema and hard ground-truth boundary for P1 benchmark cases.

A benchmark case is split into four payloads with an enforced trust boundary:

- ``DiagnosisInput`` is the ONLY thing a diagnosis model may see. It carries
  observable signals and a neutral task prompt, and is built by a whitelist
  projection (``project_diagnosis_input``) rather than by stripping a full case,
  so a hidden field can never leak by omission.
- ``CaseGroundTruth`` is the hidden answer key (evaluator-only).
- ``InjectionProvenance`` records how the case was produced (reproduction).
- ``CaseManifest`` is the internal manifest tying everything together; it is not
  shown to the diagnoser.

The boundary is enforced by code (the projection) and by tests, not by comments.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "p1-cases/1"

EvidenceCondition = Literal["full", "missing_key", "noisy"]
EVIDENCE_CONDITIONS: tuple[EvidenceCondition, ...] = ("full", "missing_key", "noisy")

# Terms that would reveal the answer key if visible to the diagnoser. The
# observable distribution/PSI signals are legitimate evidence and are NOT listed
# here; only cause-naming and injection-naming terms are forbidden in the
# diagnosis-visible payload.
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

# Expected diagnosis behavior per evidence condition (evaluator-side; never shown
# to the diagnoser because it would hint at the answer).
EXPECTED_BEHAVIOR: dict[str, str] = {
    "full": (
        "Sufficient evidence: the diagnoser may state a root-cause hypothesis and "
        "must cite the distribution/PSI signals it relies on."
    ),
    "missing_key": (
        "The decisive before/after comparison is withheld: a grounded diagnosis "
        "must abstain or name the missing evidence, not assert a cause confidently."
    ),
    "noisy": (
        "Decisive evidence is present alongside irrelevant distractors: the "
        "diagnoser must rely on the supporting signals and not be led to an "
        "unsupported cause by the noise."
    ),
}


class ObservableSignals(BaseModel):
    """Evidence-safe signals a diagnoser may see. Never names the cause."""

    candidate_feature: str | None = None
    distribution_reference: dict[str, float] | None = None
    distribution_observed: dict[str, float] | None = None
    psi: float | None = None
    sample_size: int | None = None
    baseline_metric_reference: dict[str, float] | None = None
    distractor_signals: dict[str, object] = Field(default_factory=dict)
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
    """Build the diagnosis-visible payload by whitelisting safe fields only.

    This is the single enforced boundary: it copies exactly the fields a
    diagnoser is allowed to see and nothing else, so ground-truth data cannot
    leak through an overlooked field.
    """

    return DiagnosisInput(
        schema_version=manifest.schema_version,
        public_id=manifest.public_id,
        evidence_condition=manifest.evidence_condition,
        dataset_id=manifest.dataset_id,
        dataset_sha256=manifest.dataset_sha256,
        split_manifest_sha256=manifest.split_manifest_sha256,
        task_prompt=(
            "You are shown observed signals about a model's input data across two "
            "time windows, plus a baseline metric reference. Identify the most "
            "likely root cause of the metric change, citing the signals you rely "
            "on. If the evidence is insufficient to decide, abstain and state what "
            "is missing."
        ),
        observable_signals=manifest.observable_signals,
    )
