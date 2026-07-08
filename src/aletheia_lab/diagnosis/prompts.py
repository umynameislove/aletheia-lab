"""Prompt templates for diagnosis variants."""

from __future__ import annotations


EVIDENCE_BOUND_PROMPT = """\
You are diagnosing a machine learning system failure.

Rules:
1. Use only the provided evidence.
2. Cite evidence IDs for every causal claim.
3. If evidence is insufficient, abstain or state what is missing.
4. Do not invent logs, metrics, configs, or experiments.

Return:
- root_cause_hypothesis
- supporting_evidence_ids
- counterevidence_ids
- missing_evidence
- confidence
- abstention_if_needed
"""


PLAIN_PROMPT = """\
Diagnose the likely root cause of the failure from the short summary.
Be concise and include your confidence.
"""
