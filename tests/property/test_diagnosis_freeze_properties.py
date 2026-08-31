"""Property tests for diagnosis freeze completeness and budget fairness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from aletheia_lab.evaluation.variant_fairness import (
    MATCHED_MODEL_VARIANTS,
    REQUIRED_VARIANTS,
    DiagnosisVariantFairnessFreeze,
    audit_diagnosis_variant_fairness,
)

ROOT = Path(__file__).resolve().parents[2]
TRACKED_FREEZE = ROOT / "configs/evaluation/diagnosis_variant_fairness_freeze.json"


def _payload() -> dict[str, object]:
    payload = json.loads(TRACKED_FREEZE.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


@given(removed=st.sampled_from(REQUIRED_VARIANTS))
@settings(max_examples=len(REQUIRED_VARIANTS), deadline=None)
def test_every_missing_variant_fails_closed(removed: str) -> None:
    payload = _payload()
    variants = payload["variants"]
    assert isinstance(variants, list)
    payload["variants"] = [item for item in variants if item["variant_id"] != removed]

    with pytest.raises(ValidationError):
        DiagnosisVariantFairnessFreeze.model_validate_json(json.dumps(payload))


@given(
    variant_id=st.sampled_from(MATCHED_MODEL_VARIANTS),
    context_delta=st.integers(min_value=1, max_value=8192),
)
@settings(max_examples=30, deadline=None)
def test_any_matched_context_advantage_is_detected(
    variant_id: str,
    context_delta: int,
) -> None:
    payload = _payload()
    budgets = payload["information_budgets"]
    variants = payload["variants"]
    assert isinstance(budgets, dict)
    assert isinstance(variants, list)
    baseline = budgets["main_matched_v1"]
    assert isinstance(baseline, dict)
    budgets["mutated_matched_budget"] = {
        **baseline,
        "maximum_context_tokens": baseline["maximum_context_tokens"] + context_delta,
    }
    target = next(item for item in variants if item["variant_id"] == variant_id)
    target["information_budget_ref"] = "mutated_matched_budget"

    freeze = DiagnosisVariantFairnessFreeze.model_validate_json(json.dumps(payload))
    receipt = audit_diagnosis_variant_fairness(freeze)

    assert "matched_information_budget" in receipt.blocker_codes


@given(
    variant_id=st.sampled_from(REQUIRED_VARIANTS),
    suffix=st.text(
        alphabet=st.characters(min_codepoint=33, max_codepoint=126),
        min_size=1,
        max_size=24,
    ),
)
@settings(max_examples=30, deadline=None)
def test_any_unhashed_prompt_change_is_rejected(
    variant_id: str,
    suffix: str,
) -> None:
    payload = _payload()
    prompts = payload["prompt_policies"]
    assert isinstance(prompts, dict)
    prompt = prompts[variant_id]
    assert isinstance(prompt, dict)
    prompt["instruction_contract"] += suffix

    with pytest.raises(ValidationError):
        DiagnosisVariantFairnessFreeze.model_validate_json(json.dumps(payload))
