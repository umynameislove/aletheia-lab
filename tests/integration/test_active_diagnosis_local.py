"""Actual native acquisition -> diagnosed repair, without an LLM or remote IO."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.diagnosis.active_native import PROBES, create_case
from aletheia_lab.diagnosis.active_study import close_loop

pytestmark = pytest.mark.integration


def test_native_twins_require_acquisition_and_the_chosen_repair_really_runs():
    left = create_case("iris", 42001, "input_sign")
    right = create_case("iris", 42001, "resident_sign")
    assert left.initial() == right.initial()
    for case, expected in ((left, "restore_input"), (right, "restore_model")):
        raw_before = case.reference()
        result = close_loop(
            case,
            policy="ec2",
            available=PROBES,
            costs=dict.fromkeys(PROBES, 0.0001),
            budget_seconds=10,
        )
        assert result["result"]["identified"]
        assert result["result"]["repair"]["action"] == expected
        assert result["result"]["repair"]["functional_restored"]
        assert result["result"]["repair"]["state_restored"]
        assert case.reference() == raw_before


def test_compensating_repair_recovers_behavior_without_restoring_intended_state():
    for condition, wrong_action in (
        ("input_sign", "toggle_model_sign"),
        ("resident_sign", "toggle_input_sign"),
    ):
        result = create_case("iris", 42001, condition).compensation_control()[wrong_action]
        assert result["functional_restored"] and not result["state_restored"]


def test_raw_recorder_binds_actual_evidence_and_cannot_change_original_snapshot():
    case = create_case("iris", 42001, "target_roll")
    raw = []
    before = case.reference()
    event = case.probe("restore_model", recorder=raw.append)
    assert len(raw) == 1 and canonical_sha256(raw[0]) == event["evidence_sha256"]
    assert raw[0]["functional_restored"] and not raw[0]["state_restored"]
    raw[0]["value"] = "not_restored"
    assert case.reference() == before
    assert case.probe("refit")["value"] == "restored"


def test_common_recovery_can_run_while_cause_is_still_ambiguous():
    case = create_case("iris", 42001, "target_roll")
    result = close_loop(
        case,
        policy="ec2",
        available=PROBES[3:],
        costs=dict.fromkeys(PROBES, 0.0001),
        budget_seconds=10,
    )
    assert not result["result"]["identified"]
    assert result["result"]["repair"]["basis"] == "common_functional_recovery"
    assert result["result"]["repair"]["functional_restored"]
    assert not result["result"]["repair"]["state_restored"]


def test_no_budget_does_not_get_a_free_repair_or_free_cause():
    case = create_case("iris", 42001, "input_sign")
    result = close_loop(
        case, policy="ec2", available=PROBES, costs=dict.fromkeys(PROBES, 0.0001), budget_seconds=0
    )
    assert not result["result"]["identified"]
    assert result["result"]["repair"]["status"] == "not_attempted"
    assert not result["raw_probe_evidence"]


def test_final_recovery_decision_time_is_inside_the_deadline(monkeypatch):
    from aletheia_lab.diagnosis import active_policy, active_study

    original = active_study._recovery_action
    clock = [0.0]

    def tick():
        clock[0] += 0.0001
        return clock[0]

    for module in (active_policy, active_study):
        monkeypatch.setattr(module, "time", SimpleNamespace(perf_counter=tick))

    def slow(result):
        clock[0] += 0.2
        return original(result)

    monkeypatch.setattr(active_study, "_recovery_action", slow)
    result = close_loop(
        create_case("iris", 42001, "input_sign"),
        policy="ec2",
        available=PROBES,
        costs=dict.fromkeys(PROBES, 0.000001),
        budget_seconds=0.1,
    )
    assert not result["result"]["on_budget"]
    assert result["result"]["total_seconds"] > 0.2
    assert result["result"]["repair"]["status"] == "not_attempted"


@pytest.mark.parametrize(
    "condition",
    ["healthy", "double_sign", "input_permutation", "resident_permutation", "intercept_offset"],
)
def test_extended_native_states_keep_fidelity_and_an_immutable_source(condition):
    case = create_case("iris", 42001, condition)
    before = case.reference()
    assert before["fidelity_verified"]
    for action in PROBES:
        raw = []
        event = case.probe(action, recorder=raw.append)
        assert event["evidence_sha256"] == canonical_sha256(raw[0])
        assert case.reference() == before


def test_development_census_cost_calibration_and_saved_summary_agree():
    from aletheia_lab.diagnosis.active_study import calibrate_costs, run_study, summarize

    costs = calibrate_costs()
    assert len(costs["rows"]) == 60
    result = run_study("development", costs)
    assert result["planned"] == len(result["rows"]) == 200
    assert result["summary"] == summarize(result["rows"])


def test_qualification_failure_keeps_every_planned_opportunity(monkeypatch):
    from aletheia_lab.diagnosis import active_study

    def fail(*_):
        raise ValueError("invalid native execution")

    monkeypatch.setattr(active_study, "create_case", fail)
    result = active_study.run_study("development", {"budgets": {"tight": 0.001, "relaxed": 1}})
    assert len(result["rows"]) == 200
    assert all(r["status"] == "qualification_failure" for r in result["rows"])


@pytest.mark.parametrize("action", ["run_shell", "__dict__", "read_reference", "../private"])
def test_native_probe_cannot_expand_tool_authority(action):
    with pytest.raises(ValueError):
        create_case("iris", 42001, "healthy").probe(action)


def _entrypoint():
    root = Path(__file__).resolve().parents[2]
    return runpy.run_path(str(root / "scripts/active_diagnosis_study.py"))


def _cost_artifact(entrypoint):
    from aletheia_lab.diagnosis.active_study import study_design

    return {
        "phase": "calibrate",
        "design_sha256": canonical_sha256(study_design()),
        "code_sha256": entrypoint["code_bindings"](),
        "calibration": {"costs": dict.fromkeys(PROBES, 0.0001)},
    }


def test_entrypoint_rejects_changed_calibration_code_before_execution(tmp_path):
    entrypoint = _entrypoint()
    data = _cost_artifact(entrypoint)
    data["code_sha256"]["native"] = "0" * 64
    path = tmp_path / "costs.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="calibration code differs"):
        entrypoint["checked_calibration"](path)


def test_transfer_rejects_calibration_bytes_changed_after_design(tmp_path, monkeypatch):
    from aletheia_lab.content_hashing import file_sha256
    from aletheia_lab.diagnosis import active_study

    entrypoint = _entrypoint()
    calibration = tmp_path / "costs.json"
    data = _cost_artifact(entrypoint)
    calibration.write_text(json.dumps(data), encoding="utf-8")
    design = tmp_path / "design.json"
    design.write_text(
        json.dumps(
            {
                "phase": "design",
                "design_sha256": data["design_sha256"],
                "code_sha256": data["code_sha256"],
                "calibration_sha256": file_sha256(calibration),
            }
        ),
        encoding="utf-8",
    )
    # Even whitespace drift fails the bound artifact check before any world runs.
    calibration.write_text(json.dumps(data) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        active_study,
        "run_study",
        lambda *_: pytest.fail("changed calibration reached native execution"),
    )
    output = tmp_path / "result.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "study",
            "transfer",
            "--calibration",
            str(calibration),
            "--design",
            str(design),
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit):
        entrypoint["main"]()
    assert not output.exists()


def test_entrypoint_never_overwrites_an_existing_attempt(tmp_path, monkeypatch):
    output = tmp_path / "result.json"
    output.write_bytes(b"original receipt")
    monkeypatch.setattr(sys, "argv", ["study", "design", "--output", str(output)])
    assert _entrypoint()["main"]() == 1
    assert output.read_bytes() == b"original receipt"


def test_offline_analysis_does_not_execute_native_and_rejects_changed_aggregate(
    tmp_path, monkeypatch
):
    from aletheia_lab.diagnosis import active_study

    source = tmp_path / "saved.json"
    source.write_text(
        json.dumps({"study": {"rows": [], "summary": {"invented": "success"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        active_study, "create_case", lambda *_: pytest.fail("offline analysis executed native")
    )
    output = tmp_path / "rebuilt.json"
    monkeypatch.setattr(
        sys, "argv", ["study", "analyze", "--input", str(source), "--output", str(output)]
    )
    assert _entrypoint()["main"]() == 1
    assert not output.exists()
