"""Regression tests for diagnosis variant completeness and fairness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.evaluation.variant_fairness import (
    MATCHED_MODEL_VARIANTS,
    REQUIRED_VARIANTS,
    DiagnosisVariantFairnessFreeze,
    DiagnosisVariantFairnessReceipt,
    audit_diagnosis_variant_fairness,
    load_diagnosis_variant_freeze,
)

ROOT = Path(__file__).resolve().parents[2]
TRACKED_FREEZE = ROOT / "configs/evaluation/diagnosis_variant_fairness_freeze.json"


def _payload() -> dict[str, object]:
    payload = json.loads(TRACKED_FREEZE.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_versioned_freeze_has_complete_census_and_honest_implementation_blocker() -> None:
    receipt = audit_diagnosis_variant_fairness(load_diagnosis_variant_freeze(TRACKED_FREEZE))

    assert receipt.variant_ids == REQUIRED_VARIANTS
    assert receipt.status == "fairness_policy_frozen_execution_blocked"
    assert receipt.blocker_codes == ("implementation_artifacts_resolve",)
    finding = next(
        item for item in receipt.findings if item.code == "implementation_artifacts_resolve"
    )
    assert finding.variants == ("A1", "A2", "B0", "B2", "B3", "CodeGraph", "FULL")
    assert receipt.protected_outcomes_opened is False
    assert receipt.execution_authorized is False


def test_all_matched_variants_share_model_and_information_budget() -> None:
    freeze = load_diagnosis_variant_freeze(TRACKED_FREEZE)
    by_id = {item.variant_id: item for item in freeze.variants}

    assert {by_id[item].model_policy_ref for item in MATCHED_MODEL_VARIANTS} == {"main_llm_v1"}
    assert {by_id[item].information_budget_ref for item in MATCHED_MODEL_VARIANTS} == {
        "main_matched_v1"
    }
    assert all(by_id[item].fallback_policy == "forbidden" for item in REQUIRED_VARIANTS)


@pytest.mark.parametrize("removed", REQUIRED_VARIANTS)
def test_removing_any_variant_is_rejected(removed: str) -> None:
    payload = _payload()
    variants = payload["variants"]
    assert isinstance(variants, list)
    payload["variants"] = [item for item in variants if item["variant_id"] != removed]

    with pytest.raises(ValidationError):
        DiagnosisVariantFairnessFreeze.model_validate_json(json.dumps(payload))


def test_changed_matched_model_policy_is_a_blocker() -> None:
    payload = _payload()
    model_policies = payload["model_policies"]
    assert isinstance(model_policies, dict)
    model_policies["other_model"] = {
        **model_policies["main_llm_v1"],
        "model_version": "different-model-version",
    }
    variants = payload["variants"]
    assert isinstance(variants, list)
    next(item for item in variants if item["variant_id"] == "A1")["model_policy_ref"] = (
        "other_model"
    )
    freeze = DiagnosisVariantFairnessFreeze.model_validate_json(json.dumps(payload))

    receipt = audit_diagnosis_variant_fairness(freeze)
    assert "matched_model_policy" in receipt.blocker_codes


def test_changed_matched_information_budget_is_a_blocker() -> None:
    payload = _payload()
    budgets = payload["information_budgets"]
    assert isinstance(budgets, dict)
    budgets["larger_context"] = {
        **budgets["main_matched_v1"],
        "maximum_context_tokens": 16000,
    }
    variants = payload["variants"]
    assert isinstance(variants, list)
    next(item for item in variants if item["variant_id"] == "B2")["information_budget_ref"] = (
        "larger_context"
    )
    freeze = DiagnosisVariantFairnessFreeze.model_validate_json(json.dumps(payload))

    receipt = audit_diagnosis_variant_fairness(freeze)
    assert "matched_information_budget" in receipt.blocker_codes


def test_prompt_hash_mutation_is_rejected() -> None:
    payload = _payload()
    prompts = payload["prompt_policies"]
    assert isinstance(prompts, dict)
    prompts["A3"]["instruction_contract"] += " Changed after freeze."

    with pytest.raises(ValidationError):
        DiagnosisVariantFairnessFreeze.model_validate_json(json.dumps(payload))


def test_unreferenced_shadow_policy_is_rejected() -> None:
    payload = _payload()
    model_policies = payload["model_policies"]
    assert isinstance(model_policies, dict)
    model_policies["unregistered_fallback"] = model_policies["main_llm_v1"]

    with pytest.raises(ValidationError):
        DiagnosisVariantFairnessFreeze.model_validate_json(json.dumps(payload))


def test_noncomparable_variant_cannot_be_reclassified_into_primary_pool() -> None:
    payload = _payload()
    variants = payload["variants"]
    assert isinstance(variants, list)
    codegraph = next(item for item in variants if item["variant_id"] == "CodeGraph")
    codegraph["comparison_class"] = "matched_main"
    codegraph["pooling_policy"] = "matched_primary"

    with pytest.raises(ValidationError):
        DiagnosisVariantFairnessFreeze.model_validate_json(json.dumps(payload))


def test_ready_implementations_remove_only_execution_artifact_blocker() -> None:
    freeze = load_diagnosis_variant_freeze(TRACKED_FREEZE)
    variants = tuple(
        item.model_copy(
            update={
                "implementation_state": "ready",
                "implementation_reference": (
                    item.implementation_reference
                    or "aletheia_lab.diagnosis.prompts:system_prompt_for"
                ),
            }
        )
        for item in freeze.variants
    )
    ready = freeze.model_copy(update={"variants": variants})

    receipt = audit_diagnosis_variant_fairness(ready)
    assert receipt.status == "fairness_policy_frozen_ready_for_registration"
    assert receipt.blocker_codes == ()


def test_external_and_extended_paths_are_never_pooled_as_matched_primary() -> None:
    freeze = load_diagnosis_variant_freeze(TRACKED_FREEZE)
    by_id = {item.variant_id: item for item in freeze.variants}

    assert by_id["B3"].pooling_policy == "external_only"
    assert by_id["B0"].pooling_policy == "separate"
    assert by_id["FULL"].pooling_policy == "separate"
    assert by_id["CodeGraph"].pooling_policy == "separate"


def test_receipt_blockers_and_hash_cannot_be_forged() -> None:
    receipt = audit_diagnosis_variant_fairness(load_diagnosis_variant_freeze(TRACKED_FREEZE))
    forged = receipt.model_dump(mode="json")
    forged["blocker_codes"] = []

    with pytest.raises(ValidationError):
        DiagnosisVariantFairnessReceipt.model_validate_json(json.dumps(forged))
