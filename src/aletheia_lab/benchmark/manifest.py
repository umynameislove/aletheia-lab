"""Benchmark case manifest schema."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Split = Literal["dev", "main", "human_audit", "organic_validity"]


class GroundTruth(BaseModel):
    """Hidden answer key for one injected or organic failure."""

    cause_label: str
    causal_mechanism: str
    injected_change: str
    affected_components: list[str] = Field(default_factory=list)
    expected_symptoms: list[str] = Field(default_factory=list)


class BenchmarkCase(BaseModel):
    """One diagnosable failure case."""

    case_id: str
    fault_type: str
    dataset_id: str
    injection_id: str
    ground_truth: GroundTruth
    evidence_bundle_id: str
    split: Split
    created_at: str
    notes: str | None = None


def load_case(path: str | Path) -> BenchmarkCase:
    """Load and validate a benchmark case from JSON."""

    case_path = Path(path)
    with case_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return BenchmarkCase.model_validate(data)
