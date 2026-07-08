"""Evidence schema used by diagnosis variants."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


EvidenceKind = Literal[
    "metric",
    "config",
    "log",
    "artifact",
    "dataset_profile",
    "lineage",
    "counterfactual",
    "human_note",
]


class EvidenceItem(BaseModel):
    """One item that a diagnosis model may cite."""

    evidence_id: str
    kind: EvidenceKind
    title: str
    content: str
    source_path: str | None = None
    created_at: str | None = None
    visible_to_diagnoser: bool = True


class EvidenceBundle(BaseModel):
    """Evidence attached to one benchmark case."""

    evidence_bundle_id: str
    case_id: str
    allowed_evidence: list[EvidenceItem] = Field(default_factory=list)
    withheld_evidence: list[EvidenceItem] = Field(default_factory=list)
    counterfactual_evidence: list[EvidenceItem] = Field(default_factory=list)
    leakage_check_passed: bool = False
