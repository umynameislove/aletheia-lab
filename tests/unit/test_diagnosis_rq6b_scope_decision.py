from __future__ import annotations

import json
from pathlib import Path

from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256

ROOT = Path(__file__).resolve().parents[2]
DECISION = ROOT / "configs/evaluation/diagnosis_rq6b_scope_decision.json"


def test_rq6b_scope_decision_is_self_hashed_and_precedes_data_opening() -> None:
    payload = json.loads(DECISION.read_text(encoding="utf-8"))
    declared = payload.pop("decision_sha256")

    assert canonical_execution_sha256(payload) == declared
    assert payload["status"] == (
        "excluded_from_current_registration_deferred_to_separate_prospective_study"
    )
    assert payload["protected_rq6b_data_opened"] is False
    assert payload["protected_main_outcomes_opened"] is False
    assert payload["chronology"]["decision_made_before_rq6b_data_opening"] is True


def test_rq6b_is_absent_from_current_denominators_without_erasing_later_study() -> None:
    payload = json.loads(DECISION.read_text(encoding="utf-8"))
    effect = payload["current_registration_effect"]
    later = payload["preserved_later_study_boundary"]

    assert effect["rq6b_cases"] == 0
    assert effect["rq6b_requests"] == 0
    assert effect["rq6b_endpoints_registered"] == []
    assert effect["rq6b_in_primary_denominator"] is False
    assert effect["rq6b_absence_blocks_controlled_main"] is False
    assert later["study_type"] == "separate_prospective_named_project_case"
    assert later["pool_with_controlled_main"] is False
    assert later["pool_with_logdx_ci"] is False


def test_later_rq6b_preserves_truth_tiers_and_fail_closed_seal_policy() -> None:
    payload = json.loads(DECISION.read_text(encoding="utf-8"))
    later = payload["preserved_later_study_boundary"]

    assert set(later["correctness_eligible_reference_tiers"]) == {"G3", "G4"}
    assert set(later["correctness_ineligible_reference_tiers"]) == {
        "G0",
        "G1",
        "G2",
    }
    assert later["sealed_holdout_may_tune"] == []
    assert later["early_access_consequence"] == (
        "downgrade_to_exploratory_descriptive_evidence"
    )
    assert payload["chronology"]["reactivation_requires_a_new_forward_registration"] is True
