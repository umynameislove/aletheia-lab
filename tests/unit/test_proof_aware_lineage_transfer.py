"""Offline lifecycle, model-visible formats, attribution and immutable paid slots."""

from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from aletheia_lab.evaluation import proof_aware_lineage_transfer as pilot
from aletheia_lab.evaluation import warrant_development_live
from aletheia_lab.evaluation.compositional_lineage import POLICIES, Decision, baseline_decision
from aletheia_lab.evaluation.proof_aware_lineage_transfer_analysis import STAGES, analyze
from aletheia_lab.evaluation.proof_aware_lineage_transfer_cases import (
    decode_context,
    transfer_cases,
)
from aletheia_lab.evaluation.warrant_development import DevelopmentCall
from aletheia_lab.evaluation.warrant_development_io import MAX_CALL_USD

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def frame():
    cases = transfer_cases()
    return cases, pilot._audit(cases)


@pytest.fixture
def source(monkeypatch, frame):
    hashes = {"prior_pilot_tree": "a" * 64, "prior_guard_aggregate": "b" * 64}
    monkeypatch.setattr(pilot, "_source", lambda root, memory_root: dict(hashes))
    # The real SDK/corpus audit ran once above; don't recapture it for every
    # file-tamper test. The sealed bytes and comparisons are still real.
    monkeypatch.setattr(pilot, "_audit", lambda cases: deepcopy(frame[1]))
    return hashes


class Caller:
    def __init__(self, mode="valid"):
        self.mode = mode
        self.calls = []

    def invoke(self, *, prompt, payload, schema):
        self.calls.append((prompt, deepcopy(payload), schema))
        if self.mode == "raise":
            raise RuntimeError("secret provider exception must not be retained")
        if self.mode == "technical":
            return DevelopmentCall(
                status="technical_failure",
                payload_json=None,
                input_tokens=8192,
                output_tokens=1024,
                estimated_cost_usd=MAX_CALL_USD,
                latency_seconds=0,
                usage_observed=False,
            )
        decision = baseline_decision(decode_context(payload["visible_context"]))
        if self.mode == "semantic":
            decision = Decision(
                decision="binding_fault", basis="none", next_check="none", cited_records=[]
            )
        return DevelopmentCall(
            status="completed",
            payload_json="{}" if self.mode == "invalid" else decision.model_dump_json(),
            input_tokens=400,
            output_tokens=40,
            estimated_cost_usd=0.00112,
            latency_seconds=0.5,
        )


def prepare(tmp_path):
    paths = {"root": ROOT, "memory_root": tmp_path / "memory", "directory": tmp_path / "pilot"}
    return paths, pilot.prepare(**paths)


def test_full_lifecycle_actual_formats_and_independent_replay(tmp_path, source):
    paths, report = prepare(tmp_path)
    assert report["maximum_provider_calls"] == report["sdk_capture_count"] == 96
    assert report["authored_motif_count"] == 24
    assert report["maximum_reserved_cost_usd_at_frozen_rates"] == 2.359296
    assert report["provider_calls_executed"] == 0
    assert report["frozen_guard_preserved"] and not report["live_transfer_efficacy_measured"]
    assert pilot.preflight(**paths)["verification"] == "pass"
    caller, progress = Caller(), []
    receipt = pilot.execute(
        **paths, confirm_sha256=report["plan_sha256"], caller=caller, progress=progress.append
    )
    assert (
        receipt["parsed_unique_requests"]
        == receipt["completed_unique_requests"]
        == len(caller.calls)
        == 96
    )
    assert progress[-1]["completed_unique_requests"] == 96
    assert pilot.verify(**paths)["verification"] == "pass"
    encoded = [call[1]["visible_context"] for call in caller.calls]
    assert (
        sum("rows" in value for value in encoded)
        == sum("records" in value for value in encoded)
        == 48
    )
    analysis = json.loads((paths["directory"] / "analysis.json").read_text())
    for stage in STAGES:
        for arm in POLICIES:
            assert analysis["stages"][stage]["arms"][arm]["action_success"] == 48
            assert analysis["format_pair_checks"][stage][arm]["both_formats_action_success"] == 24
    for arm in POLICIES:
        assert analysis["guard_provenance"]["proof_guarded"][arm] == {"model": 48}
        assert all(
            value >= 0
            for value in analysis["paid_arm_resources"][arm]["measured_local_seconds"].values()
        )
    assert analysis["deterministic_baselines"]["visible_resolver"]["action_success"] == 48
    assert not receipt["mechanism_admitted"] and not receipt["protected_predictions_used"]
    with pytest.raises(ValueError, match="attempt exists"):
        pilot.execute(**paths, confirm_sha256=report["plan_sha256"], caller=caller)
    with pytest.raises(ValueError, match="execution"):
        pilot.preflight(**paths)
    assert len(caller.calls) == 96


@pytest.mark.parametrize("mode", ["invalid", "technical", "raise"])
def test_terminal_failure_prefix_keeps_all_denominators(tmp_path, source, mode):
    paths, report = prepare(tmp_path)
    caller = Caller(mode)
    receipt = pilot.execute(**paths, confirm_sha256=report["plan_sha256"], caller=caller)
    assert receipt["status"] == "development_transfer_stopped_technical"
    assert len(caller.calls) == 3 and receipt["not_executed_unique_requests"] == 93
    assert receipt["parsed_unique_requests"] == 0
    assert pilot.verify(**paths)["verification"] == "pass"
    analysis = json.loads((paths["directory"] / "analysis.json").read_text())
    for stage in STAGES:
        for arm in POLICIES:
            row = analysis["stages"][stage]["arms"][arm]
            assert row["planned_views"] == 48 and row["action_success"] == 0
            assert row["bounded_nonanswer"] == row["assessable"] == 0
            assert row["selective_unwarranted_risk"] is None
    assert "secret provider exception" not in "".join(
        p.read_text() for p in paths["directory"].rglob("*.json")
    )


def test_semantic_errors_retained_without_automatic_retry(tmp_path, source):
    paths, report = prepare(tmp_path)
    caller = Caller("semantic")
    receipt = pilot.execute(**paths, confirm_sha256=report["plan_sha256"], caller=caller)
    assert receipt["parsed_unique_requests"] == len(caller.calls) == 96
    analysis = json.loads((paths["directory"] / "analysis.json").read_text())
    for arm in POLICIES:
        assert analysis["stages"]["raw"]["arms"][arm]["unwarranted_commitment"] == 40
        assert analysis["stages"]["proof_guarded"]["arms"][arm]["unwarranted_commitment"] == 0
        assert analysis["proof_guarded_success_origin_and_preserved_abstention"][arm] == {
            "resolver_conflict": 16,
            "resolver_query": 16,
        }
    assert pilot.verify(**paths)["verification"] == "pass"


@pytest.mark.parametrize("filename", sorted(pilot.BASE_FILES))
def test_prepared_artifact_tamper_blocks_before_provider(tmp_path, source, filename):
    paths, report = prepare(tmp_path)
    (paths["directory"] / filename).write_text("{}", encoding="utf-8")
    caller = Caller()
    with pytest.raises((ValueError, TypeError)):
        pilot.execute(**paths, confirm_sha256=report["plan_sha256"], caller=caller)
    assert caller.calls == [] and not (paths["directory"] / "lease.json").exists()


def test_source_identity_and_confirmation_drift_fail_closed(tmp_path, source, monkeypatch):
    paths, report = prepare(tmp_path)
    with pytest.raises(ValueError, match="digest"):
        pilot.execute(**paths, confirm_sha256="0" * 64, caller=Caller())
    source["prior_guard_aggregate"] = "c" * 64
    with pytest.raises(ValueError, match="changed"):
        pilot.preflight(**paths)
    source["prior_guard_aggregate"] = "b" * 64
    monkeypatch.setattr(pilot, "_identity", lambda root: {"different": "d" * 64})
    with pytest.raises(ValueError, match="changed"):
        pilot.preflight(**paths)


def test_guard_is_frozen_before_plan_can_be_created(tmp_path, source, monkeypatch):
    original = pilot.file_sha256
    frozen = next(iter(pilot.FROZEN_CODE))
    monkeypatch.setattr(
        pilot, "file_sha256", lambda path: "0" * 64 if path == ROOT / frozen else original(path)
    )
    with pytest.raises(ValueError, match="frozen guard"):
        prepare(tmp_path)
    assert not (tmp_path / "pilot").exists()


@pytest.mark.parametrize(
    "mutation", ["binding", "status", "cost", "time", "extra", "delete", "analysis", "lease"]
)
def test_paid_store_tampering_is_detected(tmp_path, source, mutation):
    paths, report = prepare(tmp_path)
    pilot.execute(**paths, confirm_sha256=report["plan_sha256"], caller=Caller("invalid"))
    result = sorted((paths["directory"] / "results").iterdir())[0]
    record = json.loads(result.read_text())
    if mutation == "binding":
        record["payload_sha256"] = "0" * 64
    elif mutation == "status":
        record["status"] = "parsed"
    elif mutation == "cost":
        record["call"]["estimated_cost_usd"] = 0
    elif mutation == "time":
        record["local_seconds"]["decode"] = -1
    elif mutation == "extra":
        (result.parent / "unknown.json").write_text("{}")
    elif mutation == "delete":
        result.unlink()
    else:
        (paths["directory"] / f"{mutation}.json").write_text("{}")
    if mutation != "delete":
        result.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        pilot.verify(**paths)


def test_all_wire_fields_same_between_arms_except_policy(frame):
    requests = pilot.request_frame(frame[0])
    assert len(requests) == 96
    for first, second in zip(requests[::2], requests[1::2], strict=True):
        assert first["case_id"] == second["case_id"]
        assert first["payload"] == second["payload"]
        wires = [pilot._wire(request) for request in (first, second)]
        for wire, request in zip(wires, (first, second), strict=True):
            assert set(wire) == {
                "model",
                "messages",
                "response_format",
                "temperature",
                "seed",
                "max_tokens",
                "store",
            }
            assert set(json.loads(wire["messages"][1]["content"])) == {"visible_context"}
            assert request["case_id"] not in wire["messages"][1]["content"]
            assert wire["messages"][0]["content"].endswith(pilot.FORMAT_NOTE)
            assert request["input_token_upper_bound"] <= 8192
            wire["messages"][0]["content"] = "policy difference removed for equality check"
        assert wires[0] == wires[1]


def test_fixed_schedule_balances_format_policy_and_interleaves_states(frame):
    cases = {case["case_id"]: case for case in frame[0]}
    pairs = pilot.request_frame(frame[0])[::2]
    assert [cases[r["case_id"]]["expected_state"] for r in pairs[:6]] == [
        "identified",
        "ambiguous",
        "conflict",
        "identified",
        "ambiguous",
        "conflict",
    ]
    for representation in ("records", "table"):
        selected = [r for r in pairs if cases[r["case_id"]]["representation"] == representation]
        assert len(selected) == 24
        assert sum(r["policy"] == POLICIES[0] for r in selected) == 12
    for state in ("identified", "ambiguous", "conflict"):
        first = [r for r in pairs if cases[r["case_id"]]["expected_state"] == state][::2]
        assert sum(cases[r["case_id"]]["representation"] == "records" for r in first) == 4


def test_attribution_exposes_guard_abstention_nonmonotonicity(frame):
    cases = frame[0]
    proposals = {
        (c["case_id"], arm): baseline_decision(c["context"]) for c in cases for arm in POLICIES
    }
    case = next(c for c in cases if c["expected_state"] == "ambiguous")
    proposals[(case["case_id"], POLICIES[0])] = Decision(
        decision="abstain", basis="underdetermined", next_check="none", cited_records=[]
    )
    proposals[(case["case_id"], POLICIES[1])] = Decision(
        decision="binding_fault", basis="none", next_check="none", cited_records=[]
    )
    report = analyze(cases, proposals)
    assert report["stages"]["raw"]["arms"][POLICIES[0]]["bounded_nonanswer"] == 16
    assert report["stages"]["raw"]["arms"][POLICIES[1]]["bounded_nonanswer"] == 15
    assert report["stages"]["proof_guarded"]["arms"][POLICIES[0]]["action_success"] == 47
    assert report["stages"]["proof_guarded"]["arms"][POLICIES[1]]["action_success"] == 48
    origin = report["proof_guarded_success_origin_and_preserved_abstention"]
    assert origin[POLICIES[0]]["preserved_safe_simple_abstention"] == 1
    assert origin[POLICIES[1]]["resolver_query"] == 1
    assert not report["guarded_query_success_is_monotone_in_raw_quality"]
    proposals.pop((case["case_id"], POLICIES[0]))
    with pytest.raises(ValueError, match="all planned"):
        analyze(cases, proposals)


def test_source_replay_binds_checkpoint_and_rejects_mutation(tmp_path, monkeypatch):
    directory = tmp_path / "compositional-lineage-policy-development-v1"
    directory.mkdir()
    (directory / "receipt.json").write_text("{}")
    report = {"original_completed_unique_requests": 96}
    path = tmp_path / "compositional-lineage-guard-development-v1.json"
    path.write_text(json.dumps(report))
    monkeypatch.setattr(pilot, "replay_pilot", lambda **kwargs: report)
    hashes = pilot._source(ROOT, tmp_path)
    assert hashes["prior_guard_aggregate"] == pilot.file_sha256(path)
    report["original_completed_unique_requests"] = 95
    with pytest.raises(ValueError, match="checkpoint"):
        pilot._source(ROOT, tmp_path)
    report["original_completed_unique_requests"] = 96

    def mutate(**kwargs):
        (directory / "extra.json").write_text("{}")
        return report

    monkeypatch.setattr(pilot, "replay_pilot", mutate)
    with pytest.raises(ValueError, match="changed"):
        pilot._source(ROOT, tmp_path)


def test_cli_local_gate_never_constructs_live_client(tmp_path, source, monkeypatch, capsys):
    paths, _ = prepare(tmp_path)

    def forbidden(**kwargs):
        raise AssertionError("local failure constructed network client")

    monkeypatch.setattr(warrant_development_live, "OpenAIDevelopmentCaller", forbidden)
    namespace = runpy.run_path(str(ROOT / "scripts/proof_aware_lineage_transfer.py"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "execute",
            "--root",
            str(ROOT),
            "--memory-root",
            str(paths["memory_root"]),
            "--pilot-dir",
            str(paths["directory"]),
            "--confirm-plan-sha256",
            "0" * 64,
        ],
    )
    assert namespace["main"]() == 1
    assert "failed_closed" in capsys.readouterr().out


def test_frame_and_wire_hash_stable_across_process_hash_seeds():
    code = "from aletheia_lab.evaluation.proof_aware_lineage_transfer import request_frame, sha256; from aletheia_lab.evaluation.proof_aware_lineage_transfer_cases import transfer_cases; print(sha256(request_frame(transfer_cases())))"
    outputs = []
    for seed in ("1", "997"):
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith("OPENAI_")
        }
        environment.update(PYTHONHASHSEED=seed, PYTHONPATH=str(ROOT / "src"))
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=90,
            check=True,
        )
        outputs.append(completed.stdout.strip())
    assert outputs[0] == outputs[1] and len(outputs[0]) == 64
