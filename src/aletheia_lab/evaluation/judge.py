"""Judge result schema.

LLM judges, rule-based judges, and human judges should all emit this same
contract so the evaluation tables stay comparable.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class JudgeResult(BaseModel):
    """One evaluator's judgment for a diagnosis."""

    case_id: str
    variant: str
    correctness: float = Field(ge=0.0, le=1.0)
    faithfulness: float = Field(ge=0.0, le=1.0)
    abstention: float = Field(ge=0.0, le=1.0)
    notes: str | None = None
    judge_id: str
