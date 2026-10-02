"""Offline authored-frame, source-boundary, and planned-denominator checks."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy

import pytest

from aletheia_lab.evaluation import compositional_lineage_cases as frames
from aletheia_lab.evaluation.compositional_lineage import (
    POLICIES,
    assess_decision,
    baseline_decision,
    compatible_worlds,
    reachable_statuses,
    visible_reference,
)

SOURCE = "retained-online-shoppers-development"
EXPECTED = {
    "complete_fault": ("identified", ["binding_fault"], []),
    "legitimate_same_loaded_artifact": ("identified", ["no_binding_fault"], []),
    "pinned_old_snapshot": ("identified", ["no_binding_fault"], []),
    "declared_correct_consumed_wrong": ("identified", ["binding_fault"], []),
    "declared_wrong_consumed_correct": ("identified", ["no_binding_fault"], []),
    "off_attempt_and_reported_decoys": ("identified", ["no_binding_fault"], []),
    "missing_request_selector": (
        "ambiguous",
        ["binding_fault", "no_binding_fault"],
        ["requested_endpoint"],
    ),
    "missing_execution_selector": (
        "ambiguous",
        ["binding_fault", "no_binding_fault"],
        ["loaded_endpoint"],
    ),
    "missing_manifest_leaf": (
        "ambiguous",
        ["binding_fault", "no_binding_fault"],
        ["requested_endpoint"],
    ),
    "redundant_missing_request_selector": ("identified", ["no_binding_fault"], []),
    "both_endpoints_unknown": (
        "ambiguous",
        ["binding_fault", "no_binding_fault"],
        ["both_endpoints"],
    ),
    "attested_conflict": ("conflict", [], []),
}


@pytest.fixture
def cases():
    return frames.authored_cases()


def _index(cases):
    return {(case["motif"], case["alias_order"], case["record_order"]): case for case in cases}


def _without_selector(context):
    return {
        **context,
        "records": [
            {key: value for key, value in record.items() if key != "id"}
            for record in context["records"]
            if record["kind"] != "request_snapshot"
        ],
    }


def _loaded(world):
    execution = world["request_execution"]
    buffer = world[f"execution:{execution}"]
    return world[f"buffer:{buffer}"]


def _policy_rows(cases):
    return [
        frames.assessment_row(
            case,
            arm,
            assess_decision(case["context"], baseline_decision(case["context"])),
            "deterministic",
        )
        for case in cases
        for arm in POLICIES
    ]


def test_authored_census_has_twelve_motifs_four_dependent_variants_and_one_source(cases):
    assert len(cases) == 48
    assert len({case["case_id"] for case in cases}) == 48
    assert set(frames.MOTIFS) == set(EXPECTED)
    assert Counter(case["motif"] for case in cases) == {motif: 4 for motif in EXPECTED}
    assert {(case["motif"], case["alias_order"], case["record_order"]) for case in cases} == {
        (motif, alias, order) for motif in EXPECTED for alias in (0, 1) for order in (0, 1)
    }
    assert {case["source_cluster"] for case in cases} == {SOURCE}
    assert all("authored" in case["construction"] for case in cases)
    assert all(
        set(case["context"]) == {"schema_version", "request", "attempt", "records"}
        for case in cases
    )


@pytest.mark.parametrize("motif", EXPECTED)
def test_each_motif_has_the_declared_visible_status_and_correct_measurement(cases, motif):
    state, statuses, checks = EXPECTED[motif]
    for case in cases:
        if case["motif"] != motif:
            continue
        reference = visible_reference(case["context"])
        assert reference["state"] == state
        assert reference["compatible"] == statuses
        assert reference["minimum_guaranteed_checks"] == checks
        assert reachable_statuses(case["context"]) == statuses
        assert bool(compatible_worlds(case["context"])) == (state != "conflict")


def test_fault_legitimate_and_missing_selector_share_every_other_fact(cases):
    indexed = _index(cases)
    for alias in (0, 1):
        for order in (0, 1):
            fault = indexed[("complete_fault", alias, order)]["context"]
            legitimate = indexed[("legitimate_same_loaded_artifact", alias, order)]["context"]
            missing = indexed[("missing_request_selector", alias, order)]["context"]
            assert _without_selector(fault) == _without_selector(legitimate)
            assert _without_selector(fault) == _without_selector(missing)
            assert {_loaded(world) for world in compatible_worlds(fault)} == {
                _loaded(world) for world in compatible_worlds(legitimate)
            }
            assert visible_reference(fault)["compatible"] == ["binding_fault"]
            assert visible_reference(legitimate)["compatible"] == ["no_binding_fault"]
            assert visible_reference(missing)["compatible"] == ["binding_fault", "no_binding_fault"]


def test_aliases_list_order_and_evidence_ids_do_not_change_reference(cases):
    indexed = _index(cases)
    for motif in EXPECTED:
        expected = visible_reference(indexed[(motif, 0, 0)]["context"])
        for alias in (0, 1):
            for order in (0, 1):
                context = indexed[(motif, alias, order)]["context"]
                assert visible_reference(context) == expected
                permuted = deepcopy(context)
                permuted["records"] = permuted["records"][1:] + permuted["records"][:1]
                for index, record in enumerate(permuted["records"]):
                    record["id"] = f"r{90 - index:02d}"
                assert visible_reference(permuted) == expected
                assert assess_decision(permuted, baseline_decision(permuted))["action_success"]


def test_missing_redundant_selector_still_identifies_without_unique_world(cases):
    context = _index(cases)[("redundant_missing_request_selector", 0, 0)]["context"]
    reference = visible_reference(context)
    assert reference["world_count"] > 1
    assert reference["compatible"] == ["no_binding_fault"]
    assert assess_decision(context, baseline_decision(context))["valid_resolution"]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_slot",
        "duplicate_slot",
        "wrong_case_id",
        "wrong_source",
        "measured_log_claim",
        "identified_answer_flip",
        "alias_semantic_drift",
        "order_semantic_drift",
        "missing_view_shortcut",
        "conflict_removed",
    ],
)
def test_frame_mutations_cannot_preserve_a_valid_census(cases, mutation):
    mutated = deepcopy(cases)
    indexed = _index(mutated)
    if mutation == "missing_slot":
        mutated.pop()
    elif mutation == "duplicate_slot":
        mutated[-1] = deepcopy(mutated[0])
    elif mutation == "wrong_case_id":
        mutated[0]["case_id"] = "invented-independent-case"
    elif mutation == "wrong_source":
        mutated[0]["source_cluster"] = "invented-independent-source"
    elif mutation == "measured_log_claim":
        mutated[0]["construction"] = "observed deployment log"
    elif mutation == "identified_answer_flip":
        indexed[("complete_fault", 0, 0)]["context"] = deepcopy(
            indexed[("legitimate_same_loaded_artifact", 0, 0)]["context"]
        )
    elif mutation in {"alias_semantic_drift", "order_semantic_drift"}:
        alias, order = (1, 0) if mutation == "alias_semantic_drift" else (0, 1)
        context = indexed[("pinned_old_snapshot", alias, order)]["context"]
        record = next(
            record
            for record in context["records"]
            if record["kind"] == "buffer_artifact" and record["subject"] == "buffer-0"
        )
        record["value"] = "artifact-0" if record["value"] == "artifact-1" else "artifact-1"
    elif mutation == "missing_view_shortcut":
        context = indexed[("missing_request_selector", 0, 0)]["context"]
        context["records"][-1]["value"] = "This is the faulty hidden world."
    else:
        context = indexed[("attested_conflict", 0, 0)]["context"]
        context["records"].pop()
    with pytest.raises(ValueError):
        frames.validate_cases(mutated)


def test_oracle_audit_reports_exact_development_counts_and_constructive_ambiguity(cases):
    audit = frames.oracle_audit(cases)
    assert audit["case_count"] == 48
    assert audit["authored_motif_count"] == 12
    assert audit["source_cluster_count"] == 1
    assert audit["world_domain_size"] == 256
    assert audit["state_counts"] == {"identified": 28, "ambiguous": 16, "conflict": 4}
    assert len(audit["ambiguous_witness_frame_sha256"]) == 64
    assert audit["order_variant_is_new_representation"] is False
    for case in cases:
        if visible_reference(case["context"])["state"] != "ambiguous":
            continue
        statuses = set()
        for world in compatible_worlds(case["context"]):
            requested = world[f"manifest:{world['request_snapshot']}"]
            statuses.add("no_binding_fault" if requested == _loaded(world) else "binding_fault")
        assert statuses == {"binding_fault", "no_binding_fault"}


def test_authored_generator_never_opens_the_retained_source(monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("authored envelopes must not read, fit, predict, or deserialize source data")

    monkeypatch.setattr(frames, "retained_development_cases", forbidden)
    assert len(frames.authored_cases()) == 48


def _anchor_frame():
    return [
        {
            "case_kind": name,
            "condition": "full",
            "alias_order": 0,
            "source_cluster": SOURCE,
            "reference": {"compatible": [status]},
        }
        for name, status in (("faulty", "binding_fault"), ("legitimate_B", "no_binding_fault"))
    ]


def test_source_bound_adapter_calls_only_the_retained_development_reader(tmp_path, monkeypatch):
    calls = []
    hashes = {"receipt.json": "a" * 64}

    def retained(**kwargs):
        calls.append(kwargs)
        return _anchor_frame(), hashes

    monkeypatch.setattr(frames, "retained_development_cases", retained)
    memory = tmp_path / "retained-development"
    cases, observed_hashes = frames.source_bound_cases(root=tmp_path, memory_root=memory)
    assert calls == [{"root": tmp_path, "memory_root": memory}]
    assert observed_hashes == hashes
    assert cases == frames.authored_cases()
    assert {case["source_cluster"] for case in cases} == {SOURCE}


@pytest.mark.parametrize("anchor", ["faulty", "legitimate_B"])
def test_source_anchor_status_cannot_be_replaced(tmp_path, monkeypatch, anchor):
    anchors = _anchor_frame()
    next(case for case in anchors if case["case_kind"] == anchor)["reference"]["compatible"] = [
        "no_binding_fault" if anchor == "faulty" else "binding_fault"
    ]
    monkeypatch.setattr(frames, "retained_development_cases", lambda **kwargs: (anchors, {}))
    with pytest.raises(ValueError, match="anchors"):
        frames.source_bound_cases(root=tmp_path, memory_root=tmp_path)


def test_deterministic_ceiling_and_constant_abstention_have_distinct_credit(cases):
    summary = frames.summarize_rows(frames.baseline_rows(cases))
    resolver = summary["arms"]["visible_resolver"]
    abstain = summary["arms"]["always_abstain"]
    assert resolver["planned_views"] == abstain["planned_views"] == 48
    assert resolver["state_counts"] == {"identified": 28, "ambiguous": 16, "conflict": 4}
    assert resolver["action_success"] == 48
    assert resolver["valid_resolution"] == 28
    assert resolver["minimum_guaranteed_check"] == 16
    assert resolver["correct_conflict"] == 4
    assert resolver["all_planned_action_success"] == 1
    assert resolver["selective_unwarranted_risk"] == 0
    assert abstain["assessable"] == 48
    assert abstain["bounded_nonanswer"] == 16
    assert abstain["ambiguous_boundedness"] == 1
    assert abstain["valid_resolution"] == abstain["correct_conflict"] == 0
    assert abstain["action_success"] == abstain["commitment"] == 0
    assert abstain["selective_unwarranted_risk"] is None


@pytest.mark.parametrize("status", ["invalid_or_provider_failure", "not_executed"])
def test_missing_outputs_stay_in_planned_denominator_without_abstention_credit(cases, status):
    rows = _policy_rows(cases)
    target = next(
        row
        for row in rows
        if row["case_id"] == "missing_request_selector:0:0" and row["arm"] == POLICIES[1]
    )
    context = _index(cases)[("missing_request_selector", 0, 0)]["context"]
    target["assessment"] = assess_decision(context, None)
    target["execution_status"] = status
    assert not target["assessment"]["assessable"]
    assert not target["assessment"]["bounded_nonanswer"]
    summary = frames.summarize_rows(rows)
    arm = summary["arms"][POLICIES[1]]
    assert arm["planned_views"] == 48
    assert arm["assessable"] == arm["action_success"] == 47
    assert arm["bounded_nonanswer"] == 15
    assert arm["all_planned_action_success"] == 47 / 48
    assert arm["ambiguous_boundedness"] == 15 / 16
    joint = next(
        item
        for item in summary["paired_motif_transitions"]
        if item["motif"] == "missing_request_selector"
    )
    assert joint["planned_dependent_variants"] == 4
    assert joint[POLICIES[0]] is True and joint[POLICIES[1]] is False
    assert joint["a4_minus_a3_joint_success"] == -1


@pytest.mark.parametrize("status", ["invalid_or_provider_failure", "not_executed"])
def test_failed_status_cannot_carry_manufactured_success_or_safe_abstention(cases, status):
    rows = _policy_rows(cases)
    row = next(row for row in rows if row["motif"] == "missing_request_selector")
    assert row["assessment"]["bounded_nonanswer"]
    row["execution_status"] = status
    with pytest.raises(ValueError):
        frames.summarize_rows(rows)


@pytest.mark.parametrize(
    "mutation", ["missing_row", "duplicate_row", "wrong_motif", "unknown_case"]
)
def test_summary_rejects_a_reduced_or_duplicated_planned_frame(cases, mutation):
    rows = _policy_rows(cases)
    if mutation == "missing_row":
        rows.pop()
    elif mutation == "duplicate_row":
        rows.append(deepcopy(rows[0]))
    elif mutation == "wrong_motif":
        rows[0]["motif"] = "pinned_old_snapshot"
    else:
        rows[0]["case_id"] = "invented-independent-case"
    with pytest.raises(ValueError):
        frames.summarize_rows(rows)


def test_motif_joint_scores_are_twelve_descriptive_dependent_family_results(cases):
    summary = frames.summarize_rows(_policy_rows(cases))
    joint = summary["paired_motif_transitions"]
    assert len(joint) == 12
    assert {item["motif"] for item in joint} == set(EXPECTED)
    assert all(item["planned_dependent_variants"] == 4 for item in joint)
    assert all(item[POLICIES[0]] and item[POLICIES[1]] for item in joint)
    assert all(item["a4_minus_a3_joint_success"] == 0 for item in joint)
    assert "no population interval" in summary["interpretation"]
    assert "causal loss" in summary["interpretation"]
    assert not any(key.lower() == "ci" or "confidence" in key.lower() for key in summary)
