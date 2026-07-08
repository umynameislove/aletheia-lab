"""Diagnosis variant labels."""

from __future__ import annotations

from enum import StrEnum


class DiagnosisVariant(StrEnum):
    """Variants compared in the main evaluation."""

    PLAIN_LLM = "plain_llm"
    RAG_BASELINE = "rag_baseline"
    EVIDENCE_BOUND = "evidence_bound"
    FULL_ALETHEIA = "full_aletheia"
