"""Diagnosis runner interfaces."""

from __future__ import annotations

from pydantic import BaseModel, Field

from aletheia_lab.diagnosis.variants import DiagnosisVariant


class DiagnosisOutput(BaseModel):
    """Structured diagnosis output."""

    case_id: str
    variant: DiagnosisVariant
    root_cause_hypothesis: str
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    counterevidence_ids: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    abstention_if_needed: bool = False


class DiagnosisRunner:
    """Placeholder runner.

    Replace this with concrete LLM/local-model integrations after the benchmark
    and evaluator are stable.
    """

    def run(self, case_id: str, variant: DiagnosisVariant, failure_summary: str) -> DiagnosisOutput:
        """Return a conservative placeholder diagnosis."""

        return DiagnosisOutput(
            case_id=case_id,
            variant=variant,
            root_cause_hypothesis=f"Insufficient evidence to diagnose: {failure_summary}",
            confidence=0.0,
            abstention_if_needed=True,
        )
