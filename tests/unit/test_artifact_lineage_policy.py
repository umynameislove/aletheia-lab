"""Synthetic controls for loader-status warrant, not labels of human claims."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from aletheia_lab.evaluation.artifact_binding_reader import (
    ArtifactBindingObservation,
    build_artifact_binding_context,
)
from aletheia_lab.evaluation.artifact_lineage_policy import (
    PROMPTS,
    LineageDecision,
    assess_decision,
    baseline_decision,
    decision_schema,
    parse_decision,
    summarize_rows,
    visible_observation,
    visible_reference,
)
from aletheia_lab.evaluation.artifact_lineage_sources import cases_from_observations
from aletheia_lab.model_gateway.openai import _openai_response_format
from aletheia_lab.model_gateway.schema import validate_response_payload


def observations():
    fault = ArtifactBindingObservation(
        record_count=40,
        reference_log_loss=0.4,
        observed_log_loss=0.7,
        intended_artifact="artifact-0",
        loaded_artifact="artifact-1",
        reported_artifact="artifact-0",
        model_classes=(0, 1),
        consumed_classes=(0, 1),
        source_targets=(0, 1),
        scoring_targets=(0, 1),
        raw_probe_scores=((0.6, 0.4), (0.4, 0.6)),
        scored_probe_positive=(0.4, 0.6),
        feature_count=3,
        training_count=80,
        calibration_count=40,
        private_source_sha256="a" * 64,
    )
    healthy = replace(fault, loaded_artifact="artifact-0", observed_log_loss=0.4)
    return {
        "faulty": fault,
        "healthy": healthy,
        "sham": healthy,
        "corrected": healthy,
        "legitimate_B": replace(
            fault, intended_artifact="artifact-1", reported_artifact="artifact-1"
        ),
        "manifest_text_only": replace(healthy, reported_artifact="artifact-1"),
        "adapter_column_reversal": replace(
            healthy,
            observed_log_loss=1.1,
            consumed_classes=(1, 0),
            scored_probe_positive=(0.6, 0.4),
        ),
        "two_row_target_swap": replace(healthy, observed_log_loss=0.41, scoring_targets=(1, 0)),
    }


def context(name="faulty", condition="full"):
    return json.loads(
        json.dumps(
            build_artifact_binding_context(
                observations()[name], condition=condition
            ).model_payload()
        )
    )


@pytest.mark.parametrize(
    "name,status",
    [
        ("faulty", "binding_fault"),
        ("legitimate_B", "no_binding_fault"),
        ("adapter_column_reversal", "no_binding_fault"),
        ("two_row_target_swap", "no_binding_fault"),
    ],
)
@pytest.mark.parametrize("condition", ["full", "noisy", "misleading"])
def test_status_reference_uses_loader_witness_not_loss_or_other_faults(name, status, condition):
    visible = context(name, condition)
    assert visible_reference(visible) == {"compatible": [status], "witness_available": True}
    decision = baseline_decision(visible)
    assert decision.decision == status
    assert assess_decision(visible, decision)["valid_resolution"]


def test_identical_missing_input_never_has_a_hidden_singleton_reference():
    left, right = context(condition="missing_key"), context("legitimate_B", "missing_key")
    assert left == right
    assert visible_reference(left) == {
        "compatible": ["binding_fault", "no_binding_fault"],
        "witness_available": False,
    }
    for status in ("binding_fault", "no_binding_fault"):
        decision = LineageDecision(
            decision=status, basis="none", cited_fields=["performance-comparison"]
        )
        result = assess_decision(left, decision)
        assert result["unwarranted_commitment"]
        assert not result["valid_resolution"]
    assert assess_decision(left, baseline_decision(left))["bounded_nonanswer"]


def test_metadata_and_input_identity_do_not_name_a_preferred_artifact():
    cases = cases_from_observations(observations())
    assert len(cases) == 64
    for name in ("healthy", "faulty", "legitimate_B"):
        selected = [
            case for case in cases if case["case_kind"] == name and case["condition"] == "full"
        ]
        assert selected[0]["context"] != selected[1]["context"]
        assert selected[0]["reference"] == selected[1]["reference"]
    assert all("private_source_sha256" not in json.dumps(case["context"]) for case in cases)


def test_untrusted_manifest_and_symmetric_aliases_cannot_overrule_loader_measurement():
    reported = context("manifest_text_only", "misleading")
    assert visible_observation(reported)["reported-manifest"]["reported_artifact"] == "artifact-1"
    assert baseline_decision(reported).decision == "no_binding_fault"
    swapped = replace(
        observations()["faulty"],
        intended_artifact="artifact-1",
        loaded_artifact="artifact-0",
        reported_artifact="artifact-1",
    )
    visible = build_artifact_binding_context(swapped, condition="full").model_payload()
    assert visible_reference(visible)["compatible"] == ["binding_fault"]


@pytest.mark.parametrize(
    "change",
    [
        {"basis": "none"},
        {"cited_fields": ["performance-comparison"]},
        {"cited_fields": ["artifact-load-binding", "artifact-load-binding"]},
    ],
)
def test_correct_singleton_basis_and_citation_are_separate_from_status_correctness(change):
    raw = baseline_decision(context()).model_dump()
    raw.update(change)
    assessment = assess_decision(context(), LineageDecision.model_validate(raw))
    assert assessment["correct_visible_status"]
    assert not assessment["unwarranted_commitment"]
    assert not assessment["valid_resolution"]


def test_missing_wrong_nonanswer_reason_is_assessable_not_a_technical_failure():
    decision = LineageDecision(decision="check_binding", basis="trusted_binding", cited_fields=[])
    result = assess_decision(context(condition="missing_key"), decision)
    assert result["assessable"] and result["wrong_nonanswer_reason"]
    assert not result["bounded_nonanswer"]
    assert not result["next_measurement_correct"]
    assert assess_decision(context(), decision)["wrong_nonanswer_reason"]


@pytest.mark.parametrize(
    "citations", [["artifact-load-binding"], ["performance-comparison", "performance-comparison"]]
)
def test_next_measurement_with_invalid_citations_has_no_valid_action_credit(citations):
    decision = LineageDecision(
        decision="check_binding", basis="binding_unavailable", cited_fields=citations
    )
    result = assess_decision(context(condition="missing_key"), decision)
    assert result["assessable"]
    assert not result["next_measurement_correct"] and not result["bounded_nonanswer"]


def test_failures_receive_no_safe_abstention_credit():
    assessment = assess_decision(context(condition="missing_key"), None)
    assert not assessment["assessable"]
    assert not assessment["bounded_nonanswer"] and not assessment["valid_resolution"]


@pytest.mark.parametrize(
    "bad",
    [
        '{"decision":"abstain","decision":"binding_fault","basis":"none","cited_fields":[]}',
        '{"decision":"abstain","basis":"none","cited_fields":[],"rationale":"private path"}',
        '{"decision":"healthy","basis":"none","cited_fields":[]}',
        '{"decision":"abstain","basis":"none","cited_fields":["private-reference"]}',
        "{}",
        "not json",
        "x" * 64001,
    ],
)
def test_grammar_cannot_hide_commitments_in_duplicate_unknown_or_free_prose_fields(bad):
    with pytest.raises(ValueError):
        parse_decision(bad)


def test_shared_wire_schema_accepts_all_declared_examples_and_parser_matches():
    schema = decision_schema()
    wire = _openai_response_format(json.dumps(schema))["json_schema"]["schema"]
    for visible in (context(), context("legitimate_B"), context(condition="missing_key")):
        raw = baseline_decision(visible).model_dump()
        validate_response_payload(raw, schema)
        validate_response_payload(raw, wire)
        assert parse_decision(json.dumps(raw)).model_dump() == raw
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"decision", "basis", "cited_fields"}
    common_length = len(PROMPTS["a3_derived"].split("Give a precise")[0])
    assert PROMPTS["a3_derived"][:common_length] == PROMPTS["a4_bounded"][:common_length]
    assert '"decision":"check_binding"' in PROMPTS["a3_derived"]


def rows_for_baselines():
    return [
        {
            **case,
            "policy": policy,
            "execution_status": "deterministic",
            "assessment": assess_decision(
                case["context"], baseline_decision(case["context"], baseline=policy)
            ),
        }
        for case in cases_from_observations(observations())
        for policy in ("visible_rule", "always_abstain", "metric_only")
    ]


def test_joint_transition_has_headroom_and_abstention_is_not_a_free_win():
    report = summarize_rows(rows_for_baselines())
    rule, abstain, metric = (
        report["arms"][key] for key in ("visible_rule", "always_abstain", "metric_only")
    )
    assert rule["full_all_planned_resolution"] == 1
    assert rule["missing_all_planned_bounded_success"] == 1
    assert rule["commitment_coverage_all_planned"] == 48 / 64
    assert rule["selective_unwarranted_risk"] == 0
    assert abstain["full_all_planned_resolution"] == 0
    assert abstain["missing_all_planned_bounded_success"] == 1
    assert abstain["selective_unwarranted_risk"] is None
    assert abstain["commitment_coverage_all_planned"] == 0
    assert metric["full_legitimate_B_false_positive_count"] == 2
    assert metric["full_legitimate_B_false_positive_rate_assessable"] == 1
    assert metric["missing_assessable_unique_commitment_rate"] == 1
    for pair in report["paired_transitions"]:
        assert pair["a4_minus_a3_joint_success"] is None
        assert pair["both_worlds_full_resolved_and_missing_bounded"] == {
            "always_abstain": False,
            "metric_only": False,
            "visible_rule": True,
        }
    assert not report["population_intervals_computed"] and not report["aurc_computed"]


def test_incomplete_pair_cannot_receive_vacuous_joint_success():
    rows = [
        row
        for row in rows_for_baselines()
        if not (row["case_kind"] == "legitimate_B" and row["condition"] == "missing_key")
    ]
    report = summarize_rows(rows)
    assert all(
        not pair["both_worlds_full_resolved_and_missing_bounded"]["visible_rule"]
        for pair in report["paired_transitions"]
    )


def test_nonexecution_is_not_assessable_and_legitimate_false_positive_rate_is_null():
    rows = [
        {
            **case,
            "policy": "a4_bounded",
            "execution_status": "not_executed",
            "assessment": assess_decision(case["context"], None),
        }
        for case in cases_from_observations(observations())
    ]
    report = summarize_rows(rows)["arms"]["a4_bounded"]
    assert report["not_executed_views"] == 64
    assert report["full_legitimate_B_false_positive_rate_assessable"] is None
    assert report["missing_assessable_unique_commitment_rate"] is None
    assert report["missing_all_planned_bounded_success"] == 0
    assert report["full_all_planned_resolution"] == 0


def test_unclassified_observation_and_wrong_boundary_fail_before_reference():
    wrong = replace(observations()["faulty"], private_source_sha256="invalid")
    with pytest.raises(ValueError):
        build_artifact_binding_context(wrong, condition="full")
    bad = context()
    bad["context_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        visible_reference(bad)
    with pytest.raises(ValueError, match="unknown"):
        baseline_decision(context(), baseline="magic")
