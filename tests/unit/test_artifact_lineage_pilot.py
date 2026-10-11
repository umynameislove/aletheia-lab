"""Real SDK capture and private lifecycle, no network or paid execution."""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from test_artifact_lineage_policy import observations

from aletheia_lab.evaluation import artifact_lineage_pilot as pilot
from aletheia_lab.evaluation.artifact_lineage_policy import baseline_decision, decision_schema
from aletheia_lab.evaluation.artifact_lineage_sources import cases_from_observations
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.warrant_development import DevelopmentCall
from aletheia_lab.evaluation.warrant_development_io import MAX_CALL_USD

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fixed_source(monkeypatch):
    cases = cases_from_observations(observations())
    hashes = {"receipt.json": "a" * 64}
    monkeypatch.setattr(
        pilot, "retained_development_cases", lambda **kwargs: (deepcopy(cases), dict(hashes))
    )
    return cases, hashes


class FixtureCaller:
    def __init__(self, mode="valid"):
        self.calls = []
        self.mode = mode

    def invoke(self, *, prompt, payload, schema):
        self.calls.append((prompt, payload, schema))
        if self.mode == "raise":
            raise RuntimeError("a secret credential must never be retained")
        if self.mode == "unknown":
            return DevelopmentCall(
                status="technical_failure",
                payload_json=None,
                input_tokens=8192,
                output_tokens=1024,
                estimated_cost_usd=MAX_CALL_USD,
                latency_seconds=1,
                usage_observed=False,
            )
        answer = baseline_decision(payload["visible_context"]).model_dump_json()
        return DevelopmentCall(
            status="completed",
            payload_json="{}" if self.mode == "invalid" else answer,
            input_tokens=400,
            output_tokens=30,
            estimated_cost_usd=0.00104,
            latency_seconds=0.5,
        )


def prepare(tmp_path):
    directory = tmp_path / "pilot"
    paths = {"root": ROOT, "memory_root": tmp_path / "memory", "directory": directory}
    report = pilot.prepare_artifact_lineage_pilot(**paths)
    return paths, report


def test_complete_lifecycle_is_source_bound_deduplicated_and_replayed(tmp_path, fixed_source):
    paths, report = prepare(tmp_path)
    assert report["provider_calls_executed"] == 0 and report["source_cluster_count"] == 1
    assert report["maximum_provider_calls"] == 72
    assert report["maximum_reserved_cost_usd_at_frozen_rates"] == 1.769472
    assert pilot.verify_artifact_lineage_preflight(**paths)["verification"] == "pass"
    caller = FixtureCaller()
    receipt = pilot.execute_artifact_lineage_pilot(
        **paths, confirm_sha256=report["plan_sha256"], caller=caller
    )
    assert receipt["status"] == "development_pilot_terminalized"
    assert receipt["parsed_unique_requests"] == len(caller.calls) == 72
    assert pilot.verify_artifact_lineage_pilot(**paths)["verification"] == "pass"
    analysis = json.loads((paths["directory"] / "analysis.json").read_text())
    for arm in ("a3_derived", "a4_bounded"):
        assert analysis["arms"][arm]["full_all_planned_resolution"] == 1
        assert analysis["arms"][arm]["missing_all_planned_bounded_success"] == 1
        assert analysis["paid_arm_resources"][arm]["unique_executed_requests"] == 36
    assert all(row["a4_minus_a3_joint_success"] == 0 for row in analysis["paired_transitions"])
    assert all(
        set(call[1]) == {"visible_context"} and call[2] == decision_schema()
        for call in caller.calls
    )
    with pytest.raises(ValueError, match="already exists"):
        pilot.execute_artifact_lineage_pilot(
            **paths, confirm_sha256=report["plan_sha256"], caller=caller
        )
    assert len(caller.calls) == 72
    with pytest.raises(ValueError, match="execution"):
        pilot.verify_artifact_lineage_preflight(**paths)


@pytest.mark.parametrize("mode", ["invalid", "unknown", "raise"])
def test_failures_stop_at_three_and_are_not_safe_nonanswers(tmp_path, fixed_source, mode):
    paths, report = prepare(tmp_path)
    caller = FixtureCaller(mode)
    receipt = pilot.execute_artifact_lineage_pilot(
        **paths, confirm_sha256=report["plan_sha256"], caller=caller
    )
    assert receipt["status"] == "development_pilot_stopped_technical"
    assert receipt["parsed_unique_requests"] == 0 and len(caller.calls) == 3
    assert receipt["not_executed_unique_requests"] == 69
    assert pilot.verify_artifact_lineage_pilot(**paths)["verification"] == "pass"
    if mode != "invalid":
        assert receipt["committed_cost_usd_at_frozen_rates"] == round(3 * MAX_CALL_USD, 6)
    analysis = json.loads((paths["directory"] / "analysis.json").read_text())
    for arm in ("a3_derived", "a4_bounded"):
        assert analysis["arms"][arm]["missing_all_planned_bounded_success"] == 0
        assert analysis["arms"][arm]["full_all_planned_resolution"] == 0
        assert analysis["arms"][arm]["selective_unwarranted_risk"] is None
    assert "secret credential" not in "".join(
        path.read_text() for path in paths["directory"].rglob("*.json")
    )


def test_private_truth_metadata_cannot_change_any_wire_argument(fixed_source):
    cases, _ = fixed_source
    original = pilot.request_frame(cases)
    changed = deepcopy(cases)
    for case in changed:
        case.update(
            case_id="private-id-ignored",
            truth="private-truth-ignored",
            condition="private-view-ignored",
            reference={"private": "never forward"},
            source_cluster="private-source",
        )
    assert pilot.request_frame(changed) == original
    audit = pilot.audit_request_inputs(changed)
    assert audit == pilot.audit_request_inputs(cases)
    assert audit["excluded_wire_fields"] == [] and audit["provider_calls"] == 0
    assert audit["sdk_capture_count"] == 72
    missing = {
        canonical_execution_sha256(case["context"])
        for case in cases
        if case["condition"] == "missing_key"
    }
    assert len(missing) == 4
    assert sum(request["context_sha256"] in missing for request in original) == 8


def test_wrong_confirmation_and_source_drift_block_before_lease_or_provider(
    tmp_path, fixed_source, monkeypatch
):
    paths, report = prepare(tmp_path)
    caller = FixtureCaller()
    with pytest.raises(ValueError, match="digest"):
        pilot.execute_artifact_lineage_pilot(**paths, confirm_sha256="f" * 64, caller=caller)
    cases, _ = fixed_source
    monkeypatch.setattr(
        pilot, "retained_development_cases", lambda **kwargs: (cases, {"receipt.json": "b" * 64})
    )
    with pytest.raises(ValueError, match="source"):
        pilot.execute_artifact_lineage_pilot(
            **paths, confirm_sha256=report["plan_sha256"], caller=caller
        )
    assert not caller.calls and not (paths["directory"] / "lease.json").exists()


@pytest.mark.parametrize(
    "file", ["cases.json", "plan.json", "input-audit.json", "offline-baselines.json"]
)
def test_preparation_file_tampering_is_detected(tmp_path, fixed_source, file):
    paths, _ = prepare(tmp_path)
    (paths["directory"] / file).write_text("{}")
    with pytest.raises(ValueError, match="source"):
        pilot.verify_artifact_lineage_preflight(**paths)


@pytest.mark.parametrize("file", ["analysis.json", "receipt.json", "lease.json"])
def test_terminal_aggregate_or_lease_tampering_is_detected(tmp_path, fixed_source, file):
    paths, report = prepare(tmp_path)
    pilot.execute_artifact_lineage_pilot(
        **paths, confirm_sha256=report["plan_sha256"], caller=FixtureCaller()
    )
    (paths["directory"] / file).write_text("{}")
    with pytest.raises(ValueError, match="lease|replay"):
        pilot.verify_artifact_lineage_pilot(**paths)


def test_extra_or_misbound_result_and_nonprefix_deletion_are_rejected(tmp_path, fixed_source):
    paths, report = prepare(tmp_path)
    pilot.execute_artifact_lineage_pilot(
        **paths, confirm_sha256=report["plan_sha256"], caller=FixtureCaller()
    )
    results = paths["directory"] / "results"
    extra = results / "extra.json"
    extra.write_text("{}")
    with pytest.raises(ValueError, match="unclassified"):
        pilot.verify_artifact_lineage_pilot(**paths)
    extra.unlink()
    requests = pilot.request_frame(fixed_source[0])
    first = results / f"{requests[0]['request_id']}.json"
    original = first.read_text()
    value = json.loads(original)
    value["context_sha256"] = "a" * 64
    first.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="bound"):
        pilot.verify_artifact_lineage_pilot(**paths)
    first.write_text(original)
    first.unlink()
    with pytest.raises(ValueError, match="prefix"):
        pilot.verify_artifact_lineage_pilot(**paths)


@pytest.mark.parametrize(
    "update",
    [
        {"input_tokens": 8193},
        {"output_tokens": 1025},
        {"latency_seconds": float("inf")},
        {"estimated_cost_usd": 0},
    ],
)
def test_resource_accounting_cannot_drop_cost_or_hide_oversize_responses(update):
    call = FixtureCaller().invoke(
        prompt="",
        payload={"visible_context": cases_from_observations(observations())[0]["context"]},
        schema={},
    )
    with pytest.raises(ValueError, match="resource|cost"):
        pilot._check_resources(call.model_copy(update=update))


def test_local_rejection_without_provider_is_zero_cost():
    call = DevelopmentCall(
        status="technical_failure",
        payload_json=None,
        input_tokens=0,
        output_tokens=0,
        estimated_cost_usd=0,
        latency_seconds=0,
        usage_observed=False,
        provider_attempted=False,
    )
    pilot._check_resources(call)
    assert pilot._parse(call) is None


@pytest.mark.parametrize(
    "statuses",
    [
        [],
        ["parsed"],
        ["invalid_or_provider_failure"] * 2,
        ["invalid_or_provider_failure"] * 3 + ["parsed"],
    ],
)
def test_replay_cannot_publish_arbitrary_partial_results_or_continue_past_stop(statuses):
    requests = [{"request_id": str(index)} for index in range(5)]
    records = {str(index): {"status": status} for index, status in enumerate(statuses)}
    with pytest.raises(ValueError, match="stop"):
        pilot._check_stop_policy(records, requests)


def test_sdk_unknown_option_or_invalid_stub_response_breaks_input_audit(fixed_source, monkeypatch):
    original = pilot.OpenAIDevelopmentCaller.invoke

    def invalid(self, **kwargs):
        call = original(self, **kwargs)
        return call.model_copy(update={"payload_json": "{}"})

    monkeypatch.setattr(pilot.OpenAIDevelopmentCaller, "invoke", invalid)
    with pytest.raises(ValueError, match="SDK input"):
        pilot.audit_request_inputs(fixed_source[0])


def test_long_request_and_overbudget_plan_block_offline(fixed_source, monkeypatch):
    monkeypatch.setattr(pilot, "MAX_INPUT_TOKENS", 10)
    with pytest.raises(ValueError, match="input reservation"):
        pilot.request_frame(fixed_source[0])
    monkeypatch.setattr(pilot, "MAX_INPUT_TOKENS", 8192)
    monkeypatch.setattr(pilot, "COST_CEILING_USD", 0.01)
    with pytest.raises(ValueError, match="cost ceiling"):
        pilot._plan(ROOT, fixed_source[0], {}, {})


def test_existing_and_repository_output_directories_are_not_overwritten(tmp_path, fixed_source):
    paths, _ = prepare(tmp_path)
    with pytest.raises(FileExistsError):
        pilot.prepare_artifact_lineage_pilot(**paths)
    with pytest.raises(ValueError, match="outside"):
        pilot.prepare_artifact_lineage_pilot(
            root=ROOT, memory_root=tmp_path, directory=ROOT / "private-must-not-create"
        )
    assert not (ROOT / "private-must-not-create").exists()


def test_cli_local_error_is_sanitized_and_never_requests_a_key(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/artifact_lineage_policy_pilot.py"),
            "preflight",
            "--root",
            str(ROOT),
            "--memory-root",
            str(tmp_path / "absent-private-source"),
            "--pilot-dir",
            str(tmp_path / "absent-private-output"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "artifact_lineage_pilot_failed_closed"
    assert str(tmp_path) not in result.stdout
    assert "API key" not in result.stdout and "Traceback" not in result.stderr
