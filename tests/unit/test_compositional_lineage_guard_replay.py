"""Cached guard comparisons preserve scientific attribution and source bytes."""

from __future__ import annotations

import json
import runpy
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation import compositional_lineage_guard_replay as replay
from aletheia_lab.evaluation import compositional_lineage_pilot as pilot
from aletheia_lab.evaluation import warrant_development_live
from aletheia_lab.evaluation.compositional_lineage import (
    POLICIES,
    Decision,
    baseline_decision,
    visible_reference,
)
from aletheia_lab.evaluation.compositional_lineage_cases import authored_cases
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.warrant_development import DevelopmentCall

ROOT = Path(__file__).resolve().parents[2]
STAGES = ("raw", "action_canonicalized", "reject_only", "proof_guarded")


def _cache(cases, proposal=None):
    records = {}
    for request in pilot.request_frame(cases):
        context = request["payload"]["visible_context"]
        answer = baseline_decision(context) if proposal is None else proposal(context)
        call = DevelopmentCall(
            status="completed",
            payload_json=answer.model_dump_json(),
            input_tokens=400,
            output_tokens=40,
            estimated_cost_usd=0.00112,
            latency_seconds=0.5,
        )
        records[request["request_id"]] = {
            **{key: request[key] for key in ("request_id", "policy", "context_sha256")},
            "status": "parsed",
            "call": call.model_dump(mode="json"),
        }
    return records


def _metric(report, stage, arm="a3_derived"):
    return report["stages"][stage]["arms"][arm]


def _origin(report, stage, arm, origin):
    return report["guard_provenance"][stage][arm].get(origin, 0)


def _snapshot(directory):
    return {
        path.relative_to(directory).as_posix(): file_sha256(path)
        for path in directory.rglob("*")
        if path.is_file()
    }


@pytest.fixture
def source(monkeypatch):
    cases = authored_cases()
    hashes = {"anchor/receipt.json": "a" * 64, "predecessor/receipt.json": "b" * 64}
    monkeypatch.setattr(pilot, "_source", lambda root, memory_root: (deepcopy(cases), dict(hashes)))
    return cases


class Caller:
    def __init__(self, *, invalid=False):
        self.invalid = invalid
        self.count = 0

    def invoke(self, *, prompt, payload, schema):
        self.count += 1
        answer = baseline_decision(payload["visible_context"]).model_dump_json()
        return DevelopmentCall(
            status="completed",
            payload_json="{}" if self.invalid else answer,
            input_tokens=400,
            output_tokens=40,
            estimated_cost_usd=0.00112,
            latency_seconds=0.5,
        )


def _completed_pilot(tmp_path, *, invalid=False):
    paths = {"root": ROOT, "memory_root": tmp_path / "memory", "directory": tmp_path / "pilot"}
    prepared = pilot.prepare(**paths)
    caller = Caller(invalid=invalid)
    receipt = pilot.execute(**paths, confirm_sha256=prepared["plan_sha256"], caller=caller)
    return paths, receipt, caller


def test_perfect_model_outputs_remain_model_outputs_in_every_stage():
    cases = authored_cases()
    records = _cache(cases)
    before = deepcopy((cases, records))
    report = replay.analyze_cached(cases, records)
    assert (cases, records) == before
    assert set(report["stages"]) == set(STAGES)
    for stage in STAGES:
        assert len(report["stages"][stage]["paired_motif_transitions"]) == 12
        for arm in POLICIES:
            metrics = _metric(report, stage, arm)
            assert metrics["planned_views"] == metrics["action_success"] == 48
            assert metrics["valid_resolution"] == 28
            assert metrics["minimum_guaranteed_check"] == 16
            assert metrics["correct_conflict"] == 4
            assert metrics["unwarranted_commitment"] == 0
    for stage in ("reject_only", "proof_guarded"):
        for arm in POLICIES:
            assert _origin(report, stage, arm, "model") == 48
            assert sum(report["guard_provenance"][stage][arm].values()) == 48
    for arm in POLICIES:
        assert report["raw_failure_categories"][arm]["valid"] == 48
        assert sum(report["raw_failure_categories"][arm].values()) == 48
    assert report["deterministic_baselines"]["visible_resolver"]["action_success"] == 48


def test_correct_status_proof_reconstruction_is_not_grammar_normalization():
    cases = authored_cases()

    def proposal(context):
        answer = baseline_decision(context)
        if visible_reference(context)["state"] == "identified":
            return answer.model_copy(update={"cited_records": []})
        return answer

    report = replay.analyze_cached(cases, _cache(cases, proposal))
    for arm in POLICIES:
        assert _metric(report, "raw", arm)["action_success"] == 20
        assert _metric(report, "action_canonicalized", arm)["action_success"] == 20
        assert _metric(report, "reject_only", arm)["action_success"] == 20
        assert _metric(report, "proof_guarded", arm)["action_success"] == 48
        assert _origin(report, "proof_guarded", arm, "proof_reconstructed") == 28
        assert _origin(report, "reject_only", arm, "rejected") == 28
        assert report["raw_failure_categories"][arm] == {
            "valid": 20,
            "unavailable": 0,
            "unwarranted_commitment": 0,
            "correct_status_invalid_certificate_or_contract": 28,
            "invalid_noncommitment": 0,
        }


def test_explicit_query_canonicalization_has_separate_identical_arm_accounting():
    cases = authored_cases()

    def proposal(context):
        answer = baseline_decision(context)
        if answer.decision == "check_evidence":
            return answer.model_copy(update={"decision": "abstain"})
        return answer

    report = replay.analyze_cached(cases, _cache(cases, proposal))
    for arm in POLICIES:
        assert _metric(report, "raw", arm)["action_success"] == 32
        assert _metric(report, "raw", arm)["bounded_nonanswer"] == 0
        for stage in STAGES[1:]:
            assert _metric(report, stage, arm)["action_success"] == 48
            assert _metric(report, stage, arm)["unwarranted_commitment"] == 0
        assert _origin(report, "proof_guarded", arm, "model") == 48


def test_guard_does_not_replace_wrong_identified_status_with_resolver_answer():
    cases = authored_cases()
    answer = Decision(
        decision="binding_fault", basis="entailed", next_check="none", cited_records=[]
    )
    report = replay.analyze_cached(cases, _cache(cases, lambda context: answer))
    for arm in POLICIES:
        assert _metric(report, "raw", arm)["commitment"] == 48
        assert _metric(report, "raw", arm)["unwarranted_commitment"] == 40
        assert _metric(report, "reject_only", arm)["action_success"] == 0
        guarded = _metric(report, "proof_guarded", arm)
        assert guarded["action_success"] == 28
        assert guarded["valid_resolution"] == guarded["commitment"] == 8
        assert guarded["unwarranted_commitment"] == 0
        assert _origin(report, "proof_guarded", arm, "proof_reconstructed") == 8
        assert _origin(report, "proof_guarded", arm, "resolver_query") == 16
        assert _origin(report, "proof_guarded", arm, "resolver_conflict") == 4
        assert _origin(report, "proof_guarded", arm, "rejected") == 20
        assert report["raw_failure_categories"][arm]["unwarranted_commitment"] == 40
        assert (
            report["raw_failure_categories"][arm]["correct_status_invalid_certificate_or_contract"]
            == 8
        )
    assert report["deterministic_baselines"]["visible_resolver"]["action_success"] == 48


def test_missing_cached_outputs_never_become_generated_successes():
    report = replay.analyze_cached(authored_cases(), {})
    for stage in STAGES:
        for arm in POLICIES:
            metrics = _metric(report, stage, arm)
            assert metrics["planned_views"] == 48
            assert metrics["assessable"] == metrics["action_success"] == 0
            assert metrics["bounded_nonanswer"] == metrics["commitment"] == 0
    for arm in POLICIES:
        assert report["raw_failure_categories"][arm]["unavailable"] == 48
        for stage in ("reject_only", "proof_guarded"):
            assert _origin(report, stage, arm, "invalid_proposal") == 48


def test_retained_parse_failures_keep_all_planned_denominators():
    cases = authored_cases()
    records = _cache(cases)
    for arm in POLICIES:
        identifier = next(key for key, value in records.items() if value["policy"] == arm)
        records[identifier]["call"]["payload_json"] = "{}"
        records[identifier]["status"] = "invalid_or_provider_failure"
    report = replay.analyze_cached(cases, records)
    for stage in STAGES:
        for arm in POLICIES:
            assert _metric(report, stage, arm)["planned_views"] == 48
            assert _metric(report, stage, arm)["assessable"] == 47
            assert _metric(report, stage, arm)["action_success"] == 47
    for arm in POLICIES:
        assert report["raw_failure_categories"][arm]["unavailable"] == 1
        assert _origin(report, "proof_guarded", arm, "invalid_proposal") == 1


@pytest.mark.parametrize(
    "mutation",
    ["context", "policy", "request", "unknown", "status", "cost", "extra", "missing_usage"],
)
def test_wrong_record_identity_status_schema_and_resources_fail_closed(mutation):
    cases = authored_cases()
    records = _cache(cases)
    identifier = next(iter(records))
    record = records[identifier]
    if mutation == "context":
        record["context_sha256"] = "0" * 64
    elif mutation == "policy":
        record["policy"] = "hidden_arm"
    elif mutation == "request":
        record["request_id"] = "0" * 64
    elif mutation == "unknown":
        records["0" * 64] = record
    elif mutation == "status":
        record["status"] = "invalid_or_provider_failure"
    elif mutation == "cost":
        record["call"]["estimated_cost_usd"] = 0.0
    elif mutation == "extra":
        record["hidden_answer"] = "private"
    else:
        record["call"]["usage_observed"] = False
    with pytest.raises(ValueError):
        replay.analyze_cached(cases, records)


def test_case_frame_oracle_and_hidden_metadata_cannot_be_replaced():
    cases = authored_cases()
    records = _cache(cases)
    cases[0]["reference"] = {"compatible": ["binding_fault"]}
    with pytest.raises(ValueError):
        replay.analyze_cached(cases, records)
    with pytest.raises(ValueError):
        replay.analyze_cached(authored_cases()[:-1], records)


def test_guard_receives_visible_context_and_proposal_not_case_metadata(monkeypatch):
    cases = authored_cases()
    original_guard = replay.guard_decision
    observed = []

    def visible_only(context, proposal, *, mode):
        assert set(context) == {"schema_version", "request", "attempt", "records"}
        assert "motif" not in context and "reference" not in context
        assert isinstance(proposal, Decision)
        observed.append((canonical_execution_sha256(context), mode))
        return original_guard(context, proposal, mode=mode)

    monkeypatch.setattr(replay, "guard_decision", visible_only)
    replay.analyze_cached(cases, _cache(cases))
    assert len(observed) == 192
    assert len({digest for digest, mode in observed}) == 48


def test_hole_in_fixed_execution_prefix_cannot_be_presented_as_a_valid_cache():
    cases = authored_cases()
    records = _cache(cases)
    records.pop(next(iter(records)))
    with pytest.raises(ValueError, match="prefix"):
        replay.analyze_cached(cases, records)


def test_private_raw_record_prose_is_not_serialized_in_aggregate_report():
    cases = authored_cases()
    records = _cache(cases)
    secret = "private-response-marker-never-serialize"
    identifier = next(iter(records))
    records[identifier]["call"]["payload_json"] = json.dumps({"private": secret})
    records[identifier]["status"] = "invalid_or_provider_failure"
    report = replay.analyze_cached(cases, records)
    text = json.dumps(report)
    assert secret not in text
    assert "payload_json" not in text
    assert "visible_context" not in text


def test_replay_preserves_source_tree_and_original_nonzero_resources(tmp_path, source, monkeypatch):
    paths, receipt, caller = _completed_pilot(tmp_path)
    before = _snapshot(paths["directory"])
    original = json.loads((paths["directory"] / "analysis.json").read_text())

    def forbidden(*args, **kwargs):
        raise AssertionError("offline replay must not invoke or construct a paid caller")

    original_caller = pilot.OpenAIDevelopmentCaller

    def captured_client_only(*args, **kwargs):
        if kwargs.get("client") is None:
            raise AssertionError("offline replay must not construct a network client")
        return original_caller(*args, **kwargs)

    monkeypatch.setattr(pilot, "execute", forbidden)
    monkeypatch.setattr(warrant_development_live, "OpenAIDevelopmentCaller", forbidden)
    monkeypatch.setattr(pilot, "OpenAIDevelopmentCaller", captured_client_only)
    report = replay.replay_pilot(**paths)
    assert _snapshot(paths["directory"]) == before
    assert caller.count == receipt["provider_attempt_count"] == 96
    assert report["provider_calls_executed"] == 0
    assert report["original_paid_arm_resources"] == original["paid_arm_resources"]
    assert all(
        item["total_latency_seconds"] > 0 and item["committed_cost_usd_at_frozen_rates"] > 0
        for item in report["original_paid_arm_resources"].values()
    )
    assert report["original_receipt_sha256"] == canonical_execution_sha256(receipt)
    assert report["original_plan_sha256"] == receipt["plan_sha256"]
    assert report["original_analysis_sha256"] == receipt["analysis_sha256"]
    assert report["original_tree_sha256"] == canonical_execution_sha256(before)
    assert report["original_pilot_mutated"] is False


def test_verified_stopped_prefix_does_not_acquire_synthetic_safe_outputs(tmp_path, source):
    paths, receipt, caller = _completed_pilot(tmp_path, invalid=True)
    before = _snapshot(paths["directory"])
    report = replay.replay_pilot(**paths)
    assert caller.count == receipt["completed_unique_requests"] == 3
    assert receipt["not_executed_unique_requests"] == 93
    for stage in STAGES:
        for arm in POLICIES:
            assert _metric(report, stage, arm)["planned_views"] == 48
            assert _metric(report, stage, arm)["action_success"] == 0
            assert _metric(report, stage, arm)["assessable"] == 0
    assert _snapshot(paths["directory"]) == before


def test_replay_first_verifies_original_receipt_and_detects_tampering(tmp_path, source):
    paths, _, _ = _completed_pilot(tmp_path)
    receipt_path = paths["directory"] / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["analysis_sha256"] = "0" * 64
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        replay.replay_pilot(**paths)


def test_change_during_replay_is_detected_by_second_source_verification(
    tmp_path, source, monkeypatch
):
    paths, _, _ = _completed_pilot(tmp_path)
    original_analysis = replay.analyze_cached

    def changing_source(cases, records):
        report = original_analysis(cases, records)
        path = paths["directory"] / "receipt.json"
        payload = json.loads(path.read_text())
        payload["results_sha256"] = "0" * 64
        path.write_text(json.dumps(payload))
        return report

    monkeypatch.setattr(replay, "analyze_cached", changing_source)
    with pytest.raises(ValueError):
        replay.replay_pilot(**paths)


def test_cli_stdout_and_private_output_are_aggregate_only_and_immutable(
    tmp_path, source, monkeypatch, capsys
):
    paths, _, _ = _completed_pilot(tmp_path)
    namespace = runpy.run_path(str(ROOT / "scripts/analyze_compositional_lineage_guard.py"))
    output = tmp_path / "private" / "guard-analysis.json"
    output.parent.mkdir()
    arguments = [
        "cli",
        "--root",
        str(ROOT),
        "--memory-root",
        str(paths["memory_root"]),
        "--pilot-dir",
        str(paths["directory"]),
        "--output",
        str(output),
    ]
    monkeypatch.setattr(sys, "argv", arguments)
    before = _snapshot(paths["directory"])
    assert namespace["main"]() == 0
    stdout = capsys.readouterr().out
    result = json.loads(stdout)
    assert result["provider_calls_executed"] == 0
    assert "payload_json" not in stdout and "visible_context" not in stdout
    written = output.read_bytes()
    assert canonical_execution_sha256(json.loads(written))
    assert _snapshot(paths["directory"]) == before
    assert namespace["main"]() == 1
    assert output.read_bytes() == written


@pytest.mark.parametrize("location", ["predecessor", "checkout"])
def test_cli_rejects_output_in_completed_pilot_or_public_checkout(
    tmp_path, source, monkeypatch, capsys, location
):
    paths, _, _ = _completed_pilot(tmp_path)
    namespace = runpy.run_path(str(ROOT / "scripts/analyze_compositional_lineage_guard.py"))
    destination = (
        paths["directory"] / "guard-analysis.json"
        if location == "predecessor"
        else ROOT / "guard-analysis-must-not-be-created.json"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "--root",
            str(ROOT),
            "--memory-root",
            str(paths["memory_root"]),
            "--pilot-dir",
            str(paths["directory"]),
            "--output",
            str(destination),
        ],
    )
    assert namespace["main"]() == 1
    assert not destination.exists()
    assert "failed_closed" in capsys.readouterr().out
