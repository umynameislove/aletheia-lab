"""Adapter boundary for FactoryLens case studies."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FactoryLensCaseRef:
    """Reference to one FactoryLens diagnostic case."""

    case_id: str
    report_path: str
    evidence_path: str | None = None
