"""Full offline lifecycle, wire, failures and frozen predecessor boundaries."""

from __future__ import annotations

import json
import os
import platform
import runpy
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from aletheia_lab.evaluation import compositional_lineage_pilot as pilot
from aletheia_lab.evaluation import warrant_development_live
from aletheia_lab.evaluation.compositional_lineage import (
    Decision,
    baseline_decision,
    decision_schema,
)
from aletheia_lab.evaluation.compositional_lineage_cases import authored_cases
from aletheia_lab.evaluation.warrant_development import DevelopmentCall
from aletheia_lab.evaluation.warrant_development_io import MAX_CALL_USD

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def source(monkeypatch):
    cases = authored_cases()
    hashes = {"anchor/receipt.json": "a" * 64, "predecessor/receipt.json": "b" * 64}
    monkeypatch.setattr(pilot, "_source", lambda root, memory_root: (deepcopy(cases), dict(hashes)))
    return cases, hashes


class Caller:
    def __init__(self, mode="valid"):
        self.mode = mode
        self.calls = []

    def invoke(self, *, prompt, payload, schema):
        self.calls.append((prompt, payload, schema))
        if self.mode == "raise":
            raise RuntimeError("a secret key must not be persisted")
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
        answer = baseline_decision(payload["visible_context"]).model_dump_json()
        if self.mode == "semantic":
            answer = Decision(
                decision="binding_fault", basis="none", next_check="none", cited_records=[]
            ).model_dump_json()
        call = DevelopmentCall(
            status="completed",
            payload_json="{}" if self.mode == "invalid" else answer,
            input_tokens=400,
            output_tokens=40,
            estimated_cost_usd=0.00112,
            latency_seconds=0.5,
        )
        if self.mode == "resource":
            call = call.model_copy(update={"estimated_cost_usd": 2.0})
        return call


def prepare(tmp_path):
    paths = {"root": ROOT, "memory_root": tmp_path / "memory", "directory": tmp_path / "new-pilot"}
    report = pilot.prepare(**paths)
    return paths, report


def test_full_lifecycle_deduplication_and_exact_offline_replay(tmp_path, source):
    paths, report = prepare(tmp_path)
    assert report["maximum_provider_calls"] == 96
    assert report["maximum_reserved_cost_usd_at_frozen_rates"] == 2.359296
    assert report["sdk_capture_count"] == 96
    assert report["provider_calls_executed"] == 0
    assert not report["live_policy_efficacy_measured"]
    assert pilot.preflight(**paths)["verification"] == "pass"
    caller = Caller()
    progress = []
    receipt = pilot.execute(
        **paths, confirm_sha256=report["plan_sha256"], caller=caller, progress=progress.append
    )
    assert (
        receipt["parsed_unique_requests"]
        == receipt["completed_unique_requests"]
        == len(caller.calls)
        == 96
    )
    assert receipt["not_executed_unique_requests"] == 0
    assert progress[-1]["completed_unique_requests"] == 96
    assert not receipt["mechanism_admitted"] and not receipt["protected_predictions_used"]
    assert pilot.verify(**paths)["verification"] == "pass"
    analysis = json.loads((paths["directory"] / "analysis.json").read_text())
    assert len(analysis["paired_motif_transitions"]) == 12
    assert all(
        row["a4_minus_a3_joint_success"] == 0 for row in analysis["paired_motif_transitions"]
    )
    for arm in pilot.POLICIES:
        assert analysis["arms"][arm]["all_planned_action_success"] == 1.0
        assert analysis["paid_arm_resources"][arm]["unique_executed_requests"] == 48
    with pytest.raises(ValueError, match="already exists"):
        pilot.execute(**paths, confirm_sha256=report["plan_sha256"], caller=caller)
    with pytest.raises(ValueError, match="execution"):
        pilot.preflight(**paths)
    assert len(caller.calls) == 96


@pytest.mark.parametrize("mode", ["invalid", "technical", "raise"])
def test_failures_stop_and_never_become_safe_abstentions(tmp_path, source, mode):
    paths, report = prepare(tmp_path)
    caller = Caller(mode)
    receipt = pilot.execute(**paths, confirm_sha256=report["plan_sha256"], caller=caller)
    assert len(caller.calls) == 3 and receipt["not_executed_unique_requests"] == 93
    assert receipt["status"] == "development_pilot_stopped_technical"
    assert receipt["parsed_unique_requests"] == 0
    assert pilot.verify(**paths)["verification"] == "pass"
    analysis = json.loads((paths["directory"] / "analysis.json").read_text())
    for arm in pilot.POLICIES:
        assert analysis["arms"][arm]["all_planned_action_success"] == 0
        assert analysis["arms"][arm]["ambiguous_boundedness"] == 0
        assert analysis["arms"][arm]["selective_unwarranted_risk"] is None
    assert "secret key" not in "".join(
        path.read_text() for path in paths["directory"].rglob("*.json")
    )


def test_semantic_errors_are_retained_not_retried_or_stopped(tmp_path, source):
    paths, report = prepare(tmp_path)
    caller = Caller("semantic")
    receipt = pilot.execute(**paths, confirm_sha256=report["plan_sha256"], caller=caller)
    assert receipt["parsed_unique_requests"] == len(caller.calls) == 96
    analysis = json.loads((paths["directory"] / "analysis.json").read_text())
    assert analysis["arms"]["a4_bounded"]["unwarranted_commitment"] > 0
    assert analysis["arms"]["a4_bounded"]["valid_resolution"] == 0
    assert pilot.verify(**paths)["verification"] == "pass"


@pytest.mark.parametrize("filename", sorted(pilot.BASE_FILES))
def test_changed_preparation_fails_before_caller(tmp_path, source, filename):
    paths, report = prepare(tmp_path)
    (paths["directory"] / filename).write_text("{}", encoding="utf-8")
    caller = Caller()
    with pytest.raises((ValueError, TypeError)):
        pilot.execute(**paths, confirm_sha256=report["plan_sha256"], caller=caller)
    assert caller.calls == []
    assert not (paths["directory"] / "lease.json").exists()


def test_code_source_runtime_and_confirmation_drift_fail_closed(tmp_path, source, monkeypatch):
    paths, report = prepare(tmp_path)
    with pytest.raises(ValueError, match="digest"):
        pilot.execute(**paths, confirm_sha256="0" * 64, caller=Caller())
    source[1]["anchor/receipt.json"] = "c" * 64
    with pytest.raises(ValueError, match="changed"):
        pilot.preflight(**paths)
    source[1]["anchor/receipt.json"] = "a" * 64
    monkeypatch.setattr(pilot, "_identity", lambda root: {"changed": "d" * 64})
    with pytest.raises(ValueError, match="changed"):
        pilot.preflight(**paths)


def test_request_frame_and_all_sdk_arguments_ignore_private_metadata(source):
    cases = source[0]
    changed = deepcopy(cases)
    for case in changed:
        case.update(
            case_id="private",
            motif="private",
            truth="private",
            reference={"private": True},
            source_cluster="private",
            receipt="private",
            raw="private",
        )
    assert pilot.request_frame(cases) == pilot.request_frame(changed)
    assert pilot.audit_request_inputs(cases) == pilot.audit_request_inputs(changed)
    assert pilot.audit_request_inputs(cases)["provider_calls"] == 0
    requests = pilot.request_frame(cases + deepcopy(cases))
    assert len(requests) == 96
    for request in requests:
        wire = pilot._wire(request)
        assert set(wire) == {
            "model",
            "messages",
            "response_format",
            "temperature",
            "seed",
            "max_tokens",
            "store",
        }
        assert wire["store"] is False and wire["seed"] == 731
        assert wire["response_format"]["json_schema"]["schema"] == decision_schema()
        assert set(json.loads(wire["messages"][1]["content"])) == {"visible_context"}
        assert request["input_token_upper_bound"] <= 8192


@pytest.mark.parametrize(
    "mutation",
    ["binding", "status", "cost", "extra", "delete"],
    ids=["request-binding", "parse-status", "frozen-cost", "extra-record", "missing-record"],
)
def test_paid_record_tampering_is_detected(tmp_path, source, mutation):
    paths, report = prepare(tmp_path)
    pilot.execute(**paths, confirm_sha256=report["plan_sha256"], caller=Caller("invalid"))
    result = sorted((paths["directory"] / "results").iterdir())[0]
    record = json.loads(result.read_text())
    if mutation == "binding":
        record["context_sha256"] = "0" * 64
    elif mutation == "status":
        record["status"] = "parsed"
    elif mutation == "cost":
        record["call"]["estimated_cost_usd"] = 0
    elif mutation == "extra":
        (result.parent / "extra.json").write_text("{}")
    else:
        result.unlink()
    if mutation != "delete":
        result.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        pilot.verify(**paths)


def test_bad_resource_accounting_stops_without_retry(tmp_path, source):
    paths, report = prepare(tmp_path)
    caller = Caller("resource")
    with pytest.raises(ValueError, match="resource"):
        pilot.execute(**paths, confirm_sha256=report["plan_sha256"], caller=caller)
    assert len(caller.calls) == 1
    assert (paths["directory"] / "lease.json").exists()
    with pytest.raises(ValueError, match="already exists"):
        pilot.execute(**paths, confirm_sha256=report["plan_sha256"], caller=caller)


@pytest.mark.parametrize("field", ["provider_attempted", "usage_observed"])
def test_completed_output_requires_provider_and_usage_observations(field):
    call = Caller().invoke(
        prompt="unused", payload={"visible_context": authored_cases()[0]["context"]}, schema={}
    )
    with pytest.raises(ValueError, match="observations"):
        pilot._resources(call.model_copy(update={field: False}))


def test_source_checks_completed_predecessor_and_binds_every_retained_json(tmp_path, monkeypatch):
    cases = authored_cases()
    predecessor = tmp_path / "artifact-lineage-policy-development-v1"
    predecessor.mkdir()
    retained = predecessor / "receipt.json"
    retained.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        pilot, "source_bound_cases", lambda **kwargs: (cases, {"receipt.json": "a" * 64})
    )
    receipt = {
        "plan_sha256": pilot.PREDECESSOR_PLAN_SHA256,
        "completed_unique_requests": 72,
        "status": "development_pilot_terminalized",
    }
    monkeypatch.setattr(pilot, "verify_artifact_lineage_pilot", lambda **kwargs: receipt)
    observed, hashes = pilot._source(ROOT, tmp_path)
    assert observed == cases
    assert hashes == {
        "anchor/receipt.json": "a" * 64,
        "predecessor/receipt.json": pilot.file_sha256(retained),
    }
    receipt["completed_unique_requests"] = 71
    with pytest.raises(ValueError, match="completed retained"):
        pilot._source(ROOT, tmp_path)


def test_cli_local_failure_does_not_construct_live_client(tmp_path, source, monkeypatch, capsys):
    paths, _ = prepare(tmp_path)

    def forbidden(**kwargs):
        raise AssertionError("live client must not be created")

    monkeypatch.setattr(warrant_development_live, "OpenAIDevelopmentCaller", forbidden)
    namespace = runpy.run_path(str(ROOT / "scripts/compositional_lineage_pilot.py"))
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


def test_frame_and_wire_hash_are_process_seed_stable():
    code = "from aletheia_lab.evaluation.compositional_lineage_cases import authored_cases; from aletheia_lab.evaluation.compositional_lineage_pilot import request_frame; from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256; print(canonical_execution_sha256(request_frame(authored_cases())))"
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
            check=True,
            capture_output=True,
            text=True,
            timeout=120 if platform.system() == "Windows" else 30,
        )
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1]
