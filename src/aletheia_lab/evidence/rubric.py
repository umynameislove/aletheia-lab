"""Canonical P1 evidence-condition and diagnosis-behavior contract.

The rubric is evaluator-side metadata.  Diagnosis payloads must never include
these labels because they describe how an answer will be judged rather than an
observable fact about the incident.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

RUBRIC_SCHEMA_VERSION: Final[Literal["condition-rubric/2"]] = "condition-rubric/2"

EvidenceCondition = Literal["full", "missing_key", "noisy"]
EVIDENCE_CONDITIONS: tuple[EvidenceCondition, ...] = ("full", "missing_key", "noisy")

EvidenceSufficiency = Literal[
    "bounded_hypothesis_supported",
    "bounded_hypothesis_tentative_only",
]
ClaimLevel = Literal[
    "observation",
    "comparison",
    "bounded_causal_hypothesis",
    "causal_conclusion",
    "strong_causal_conclusion",
    "uncertainty",
    "missing_evidence",
    "next_check",
    "remediation",
]
EvidenceRole = Literal[
    "symptom",
    "candidate_distribution_reference",
    "candidate_distribution_observed",
    "candidate_psi",
    "metric_comparison",
    "secondary_distribution_comparison",
]
DiagnosisBehavior = Literal[
    "describe_observed_facts",
    "distinguish_observation_from_cause",
    "cite_supporting_evidence",
    "qualify_causal_hypothesis",
    "express_uncertainty",
    "abstain_on_causal_conclusion",
    "request_missing_decisive_evidence",
    "reject_unsupported_distractors",
    "assert_unsupported_extra_cause",
    "assert_confident_cause_without_decisive_evidence",
    "select_unsupported_distractor",
    "blanket_abstention",
]
ExpectedDiagnosisBehavior = Literal[
    "report_bounded_hypothesis_with_citations",
    "report_tentative_hypothesis_and_request_evidence",
    "report_bounded_hypothesis_with_citations_and_handle_secondary_comparison",
]

_DECISIVE_ROLES: tuple[EvidenceRole, ...] = (
    "symptom",
    "candidate_distribution_reference",
    "candidate_distribution_observed",
    "candidate_psi",
    "metric_comparison",
)


class ConditionRubric(BaseModel):
    """Immutable, typed evaluator contract for one evidence condition."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["condition-rubric/2"]
    condition: EvidenceCondition
    causal_claim_sufficiency: EvidenceSufficiency
    allowed_claim_levels: tuple[ClaimLevel, ...]
    forbidden_claim_levels: tuple[ClaimLevel, ...]
    required_behaviors: tuple[DiagnosisBehavior, ...]
    forbidden_behaviors: tuple[DiagnosisBehavior, ...]
    expected_diagnosis_behavior: ExpectedDiagnosisBehavior
    required_evidence_roles: tuple[EvidenceRole, ...]
    intentionally_withheld_evidence_roles: tuple[EvidenceRole, ...]

    @field_validator(
        "allowed_claim_levels",
        "forbidden_claim_levels",
        "required_behaviors",
        "forbidden_behaviors",
        "required_evidence_roles",
        "intentionally_withheld_evidence_roles",
    )
    @classmethod
    def _unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("rubric tuple values must be unique")
        return value

    @model_validator(mode="after")
    def _internally_consistent(self) -> ConditionRubric:
        if set(self.allowed_claim_levels) & set(self.forbidden_claim_levels):
            raise ValueError("claim levels cannot be both allowed and forbidden")
        if set(self.required_behaviors) & set(self.forbidden_behaviors):
            raise ValueError("behaviors cannot be both required and forbidden")
        if not set(self.intentionally_withheld_evidence_roles).issubset(
            self.required_evidence_roles
        ):
            raise ValueError("intentionally withheld roles must be required causal evidence")
        if self.causal_claim_sufficiency == "bounded_hypothesis_supported" and (
            self.intentionally_withheld_evidence_roles
        ):
            raise ValueError(
                "a bounded-hypothesis-supported condition cannot withhold required evidence"
            )
        if self.causal_claim_sufficiency == "bounded_hypothesis_tentative_only" and not (
            self.intentionally_withheld_evidence_roles
        ):
            raise ValueError("a tentative-only condition must identify withheld evidence")
        if "causal_conclusion" not in self.forbidden_claim_levels:
            raise ValueError("all P1 conditions must forbid causal conclusions")
        if "strong_causal_conclusion" not in self.forbidden_claim_levels:
            raise ValueError("all P1 conditions must forbid strong causal conclusions")
        if "bounded_causal_hypothesis" not in self.allowed_claim_levels:
            raise ValueError("all P1 conditions must allow bounded causal hypotheses")
        if "qualify_causal_hypothesis" not in self.required_behaviors:
            raise ValueError("all P1 conditions must require qualified causal hypotheses")
        if self.condition == "missing_key" and "express_uncertainty" not in (
            self.required_behaviors
        ):
            raise ValueError("missing_key must require explicit uncertainty")
        return self


_COMMON_ALLOWED_CLAIMS: tuple[ClaimLevel, ...] = (
    "observation",
    "comparison",
    "bounded_causal_hypothesis",
    "uncertainty",
    "missing_evidence",
    "next_check",
)

_COMMON_FORBIDDEN_CLAIMS: tuple[ClaimLevel, ...] = (
    "causal_conclusion",
    "strong_causal_conclusion",
    "remediation",
)

_CONDITION_RUBRICS: dict[EvidenceCondition, ConditionRubric] = {
    "full": ConditionRubric(
        schema_version=RUBRIC_SCHEMA_VERSION,
        condition="full",
        causal_claim_sufficiency="bounded_hypothesis_supported",
        allowed_claim_levels=_COMMON_ALLOWED_CLAIMS,
        forbidden_claim_levels=_COMMON_FORBIDDEN_CLAIMS,
        required_behaviors=(
            "describe_observed_facts",
            "distinguish_observation_from_cause",
            "cite_supporting_evidence",
            "qualify_causal_hypothesis",
        ),
        forbidden_behaviors=(
            "assert_unsupported_extra_cause",
            "assert_confident_cause_without_decisive_evidence",
            "blanket_abstention",
        ),
        expected_diagnosis_behavior="report_bounded_hypothesis_with_citations",
        required_evidence_roles=_DECISIVE_ROLES,
        intentionally_withheld_evidence_roles=(),
    ),
    "missing_key": ConditionRubric(
        schema_version=RUBRIC_SCHEMA_VERSION,
        condition="missing_key",
        causal_claim_sufficiency="bounded_hypothesis_tentative_only",
        allowed_claim_levels=_COMMON_ALLOWED_CLAIMS,
        forbidden_claim_levels=_COMMON_FORBIDDEN_CLAIMS,
        required_behaviors=(
            "describe_observed_facts",
            "distinguish_observation_from_cause",
            "qualify_causal_hypothesis",
            "express_uncertainty",
            "abstain_on_causal_conclusion",
            "request_missing_decisive_evidence",
        ),
        forbidden_behaviors=(
            "assert_unsupported_extra_cause",
            "assert_confident_cause_without_decisive_evidence",
        ),
        expected_diagnosis_behavior="report_tentative_hypothesis_and_request_evidence",
        required_evidence_roles=_DECISIVE_ROLES,
        intentionally_withheld_evidence_roles=(
            "candidate_distribution_reference",
            "candidate_psi",
            "metric_comparison",
        ),
    ),
    "noisy": ConditionRubric(
        schema_version=RUBRIC_SCHEMA_VERSION,
        condition="noisy",
        causal_claim_sufficiency="bounded_hypothesis_supported",
        allowed_claim_levels=_COMMON_ALLOWED_CLAIMS,
        forbidden_claim_levels=_COMMON_FORBIDDEN_CLAIMS,
        required_behaviors=(
            "describe_observed_facts",
            "distinguish_observation_from_cause",
            "cite_supporting_evidence",
            "qualify_causal_hypothesis",
            "reject_unsupported_distractors",
        ),
        forbidden_behaviors=(
            "assert_unsupported_extra_cause",
            "select_unsupported_distractor",
            "blanket_abstention",
        ),
        expected_diagnosis_behavior=(
            "report_bounded_hypothesis_with_citations_and_handle_secondary_comparison"
        ),
        required_evidence_roles=(*_DECISIVE_ROLES, "secondary_distribution_comparison"),
        intentionally_withheld_evidence_roles=(),
    ),
}


def condition_rubric_for(condition: EvidenceCondition) -> ConditionRubric:
    """Return the canonical immutable rubric for ``condition``."""

    return _CONDITION_RUBRICS[condition]


def expected_behavior_for(condition: EvidenceCondition) -> ExpectedDiagnosisBehavior:
    """Return the canonical expected-behavior code for ``condition``."""

    return condition_rubric_for(condition).expected_diagnosis_behavior


def validate_condition_rubric(rubric: ConditionRubric) -> None:
    """Fail if a supplied rubric differs from the frozen canonical contract."""

    expected = condition_rubric_for(rubric.condition)
    if rubric != expected:
        msg = f"rubric for {rubric.condition!r} does not match the canonical contract"
        raise ValueError(msg)


def validate_condition_rubric_set(rubrics: Iterable[ConditionRubric]) -> None:
    """Require exactly one canonical rubric for each P1 sibling condition."""

    materialized = tuple(rubrics)
    conditions = tuple(rubric.condition for rubric in materialized)
    if len(conditions) != len(EVIDENCE_CONDITIONS) or set(conditions) != set(EVIDENCE_CONDITIONS):
        msg = "rubric set must contain exactly one full, missing_key and noisy contract"
        raise ValueError(msg)
    for rubric in materialized:
        validate_condition_rubric(rubric)
