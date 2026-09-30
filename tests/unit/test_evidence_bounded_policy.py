"""Decision warrant is not hidden cause accuracy or zero-coverage safety."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from test_score_mapping_symptom_matching import _observation

from aletheia_lab.evaluation.evidence_bounded_policy import (
    PolicyDecision,
    analyze_policy_rows,
    assess_decision,
    compatibility_reference,
    decision_schema,
    deterministic_decision,
)
from aletheia_lab.evaluation.score_mapping_reader import build_score_mapping_reader_context
from aletheia_lab.model_gateway.openai import _openai_response_format


def context(condition="full", *, rival=False):
    observation = _observation()
    if rival:
        observation = replace(
            observation,
            evaluator_classes=(0, 1),
            example_observed_positive=0.9,
            target_binding_matches_source=False,
            corrected_log_loss=0.6000002,
            observed_log_loss=0.6000002,
        )
    return build_score_mapping_reader_context(observation, condition=condition).model_payload()


def decision(
    action="singleton",
    causes=("score_mapping",),
    *,
    claims=(),
    reason="witness",
    citations=("column-interpretation", "target-binding-check", "independent-recomputation"),
):
    return PolicyDecision(
        action=action,
        candidates=causes,
        causal_claims=claims,
        reason=reason,
        measurement="column_and_target_provenance" if action == "next_measurement" else "none",
        cited_fields=citations,
    )


@pytest.mark.parametrize("condition", ["full", "noisy", "misleading"])
@pytest.mark.parametrize("rival", [False, True])
def test_semantic_witness_resolves_not_distractor_or_condition(condition, rival):
    visible = context(condition, rival=rival)
    cause = "target_binding" if rival else "score_mapping"
    assert compatibility_reference(visible) == {"status": "resolved", "compatible": [cause]}
    result = assess_decision(visible, decision(causes=(cause,)))
    assert result["valid_resolution"] and not result["unsupported_commitment"]


def test_whole_missing_context_identical_and_lucky_singleton_is_unwarranted():
    left, right = context("missing_key"), context("missing_key", rival=True)
    assert left == right
    assert compatibility_reference(left)["compatible"] == ["score_mapping", "target_binding"]
    result = assess_decision(left, decision(citations=()))
    assert result["unsupported_commitment"] and not result["valid_resolution"]
    assert assess_decision(left, decision("candidate_set", citations=(), reason="insufficient"))[
        "unsupported_exclusion"
    ]


def test_abstention_cannot_hide_definitive_claim_and_conflict_is_not_missing():
    visible = context("missing_key")
    mixed = decision("abstain", (), claims=("score_mapping",), reason="insufficient", citations=())
    assert assess_decision(visible, mixed)["unsupported_commitment"]
    wrong_reason = decision("abstain", (), reason="conflict", citations=())
    result = assess_decision(visible, wrong_reason)
    assert result["wrong_reason"] and not result["bounded_ambiguous_response"]
    proper = decision(
        "candidate_set", ("score_mapping", "target_binding"), reason="insufficient", citations=()
    )
    result = assess_decision(visible, proper)
    assert result["bounded_ambiguous_response"] and not result["valid_resolution"]


def test_proposed_measurement_is_discriminating_only_when_missing():
    proposed = decision("next_measurement", (), reason="insufficient", citations=())
    assert assess_decision(context("missing_key"), proposed)["bounded_ambiguous_response"]
    result = assess_decision(context(), proposed)
    assert result["redundant_measurement"] and not result["valid_resolution"]


def test_citation_validity_does_not_establish_witness_support():
    result = assess_decision(context(), decision(citations=("performance-comparison",)))
    assert result["citation_valid"] and not result["unsupported_commitment"]
    assert result["incomplete_commitment_citation"] and not result["valid_resolution"]
    result = assess_decision(context("missing_key"), decision("abstain", (), reason="insufficient"))
    assert not result["citation_valid"] and not result["bounded_ambiguous_response"]


def test_evidence_warrant_is_invariant_to_citation_completeness():
    visible = context()
    incomplete = assess_decision(
        visible, decision(citations=("column-interpretation", "target-binding-check"))
    )
    complete = assess_decision(visible, decision())
    assert incomplete["reference"] == complete["reference"]
    assert not incomplete["unsupported_commitment"] and not complete["unsupported_commitment"]
    assert incomplete["incomplete_commitment_citation"]
    assert not incomplete["valid_resolution"] and complete["valid_resolution"]


def test_one_candidate_set_has_same_unique_attribution_semantics_as_singleton():
    singleton = assess_decision(context(), decision())
    set_answer = assess_decision(context(), decision("candidate_set"))
    assert set_answer["commitments"] == singleton["commitments"]
    assert set_answer["valid_resolution"] == singleton["valid_resolution"]
    ambiguous = assess_decision(
        context("missing_key"), decision("candidate_set", citations=(), reason="insufficient")
    )
    assert ambiguous["unsupported_exclusion"] and ambiguous["unsupported_commitment"]


@pytest.mark.parametrize("flat", [False, True])
def test_class_order_is_decoded_by_semantics_even_for_flat_example(flat):
    observation = replace(
        _observation(),
        model_classes=(1, 0),
        evaluator_classes=(0, 1),
        example_score_pair=(0.5, 0.5) if flat else (0.9, 0.1),
        example_corrected_positive=0.5 if flat else 0.9,
        example_observed_positive=0.5 if flat else 0.1,
    )
    visible = build_score_mapping_reader_context(observation, condition="full").model_payload()
    assert compatibility_reference(visible)["compatible"] == ["score_mapping"]


def test_private_identity_has_no_effect_and_multifault_is_not_conflict():
    observation = replace(_observation(), source_identity_sha256="f" * 64)
    assert (
        context()
        == build_score_mapping_reader_context(observation, condition="full").model_payload()
    )
    multi = replace(observation, target_binding_matches_source=False)
    visible = build_score_mapping_reader_context(multi, condition="full").model_payload()
    assert compatibility_reference(visible)["status"] == "out_of_scope"


def test_inconsistent_reference_and_hidden_context_extras_fail_closed():
    malformed = {**context(), "truth": "score_mapping"}
    with pytest.raises(ValueError):
        compatibility_reference(malformed)
    wrong = replace(_observation(), corrected_log_loss=0.9)
    with pytest.raises(ValueError, match="correction"):
        compatibility_reference(
            build_score_mapping_reader_context(wrong, condition="full").model_payload()
        )


def test_always_abstain_has_no_selective_accuracy_and_provider_failure_not_safe():
    rows = [
        {
            "policy": arm,
            "condition": condition,
            "execution_status": "deterministic" if output else "invalid_or_provider_failure",
            "provider_attempted": output is None,
            "assessment": assess_decision(context(condition), output),
        }
        for condition in ("full", "missing_key")
        for arm, output in (
            ("always_abstain", deterministic_decision(context(condition), always_abstain=True)),
            ("a4_bounded", None),
        )
    ]
    report = analyze_policy_rows(rows)["arms"]
    assert report["always_abstain"]["full_all_planned_resolution_rate"] == 0
    assert report["always_abstain"]["selective_warranted_commitment_rate"] is None
    assert report["a4_bounded"]["unsupported_ambiguous_commitment_rate"] is None
    assert report["a4_bounded"]["technical_or_schema_failed_views"] == 2


def test_shared_schema_is_actual_strict_wire_compatible_and_has_no_free_prose():
    schema = decision_schema()
    wire = _openai_response_format(json.dumps(schema))
    assert wire["json_schema"]["strict"] is True
    assert "rationale" not in schema["properties"]
    with pytest.raises(ValueError):
        PolicyDecision.model_validate_json(
            json.dumps(
                {**decision().model_dump(mode="json"), "rationale": "secret confident cause"}
            )
        )


@pytest.mark.parametrize(
    "changed",
    [
        {"candidates": ["score_mapping", "score_mapping"]},
        {"candidates": []},
        {"action": "candidate_set", "candidates": []},
        {"action": "abstain"},
        {"measurement": "column_and_target_provenance"},
        {"cited_fields": ["private-source-hash"]},
    ],
)
def test_shared_action_constraints_reject_incoherent_decisions(changed):
    with pytest.raises(ValueError):
        PolicyDecision.model_validate_json(
            json.dumps({**decision().model_dump(mode="json"), **changed})
        )
