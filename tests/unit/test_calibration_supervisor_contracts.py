"""Synthetic child boundaries and real durable replay; no study workers run."""

import hashlib
import json
import random
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from test_calibration_audit_verification import _fixture_packet

from aletheia_lab.evaluation import calibration_audit_study as study
from aletheia_lab.evaluation.calibration_audit_verification import check_cost
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_audit import digest

ROOT = Path(__file__).resolve().parents[2]


def write(path, payload):
    path.write_bytes(encode(study.sealed(payload)).encode())


@pytest.fixture(autouse=True)
def deny_real_child(monkeypatch):
    monkeypatch.setattr(
        study.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("synthetic contracts must not launch a worker"),
    )


def test_child_command_binds_owned_source_and_disables_telemetry(tmp_path, monkeypatch):
    calls = []
    expected = SimpleNamespace(returncode=0)

    def completed(command, **kwargs):
        calls.append((command, kwargs))
        return expected

    monkeypatch.setattr(study.subprocess, "run", completed)
    assert study._child(tmp_path, ["transfer-worker", "--seed", "5"]) is expected
    command, options = calls[0]
    assert command[1:] == [
        str(tmp_path / "scripts/calibration_audit_study.py"),
        "transfer-worker",
        "--seed",
        "5",
    ]
    assert options["cwd"] == tmp_path and options["timeout"] == 120
    assert options["capture_output"] and options["text"] and options["check"] is False
    assert options["env"]["PYTHONPATH"] == str(tmp_path / "src")
    assert options["env"]["OTEL_SDK_DISABLED"] == "true"


def test_transfer_census_keeps_success_failure_and_timeout(tmp_path, monkeypatch):
    calls = []
    successful = {"seed": 5, "status": "owned", "cases": []}

    def child(root, arguments):
        seed = int(arguments[arguments.index("--seed") + 1])
        calls.append(seed)
        if seed == 5:
            write(Path(arguments[arguments.index("--output") + 1]), successful)
            return SimpleNamespace(returncode=0)
        if seed == 7:
            return SimpleNamespace(returncode=3)
        raise subprocess.TimeoutExpired("owned", 120)

    monkeypatch.setattr(study, "_child", child)
    assert study.run_transfers(tmp_path, tmp_path, [5, 7, 9]) == [
        successful,
        {"seed": 7, "status": "worker_failure", "error_type": "child_failure"},
        {"seed": 9, "status": "worker_failure", "error_type": "TimeoutExpired"},
    ]
    assert calls == [5, 7, 9]


def test_cost_census_has_sealed_inputs_progress_and_no_retry(tmp_path, monkeypatch, capsys):
    plan = {"cost_seed": 13, "logical_quota": 65536, "growth_bound": 16384}
    calls = []

    def child(root, arguments):
        specification = study.read(Path(arguments[arguments.index("--config") + 1]))
        output = Path(arguments[arguments.index("--output") + 1])
        calls.append(specification)
        if len(calls) == 1:
            write(output, {**specification, "status": "complete", "synthetic": True})
            return SimpleNamespace(returncode=0)
        if len(calls) == 2:
            return SimpleNamespace(returncode=2)
        raise subprocess.TimeoutExpired("owned", 120)

    monkeypatch.setattr(study, "_child", child)
    rows = study.run_costs(tmp_path, tmp_path, plan, "development")
    expected = [
        {
            "mode": mode,
            "count": 8,
            "repeat": 0,
            "deadline_ms": 30000,
            "seed": 13,
            "quota": 65536,
            "bound": 16384,
        }
        for mode in ("raw", "compact", "whole")
    ]
    random.Random(9181).shuffle(expected)
    assert calls == expected
    assert rows[0] == {**expected[0], "status": "complete", "synthetic": True}
    assert rows[1] == {**expected[1], "status": "worker_failure", "error_type": "child_failure"}
    assert rows[2] == {**expected[2], "status": "worker_failure", "error_type": "TimeoutExpired"}
    progress = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["completed_processes"] for row in progress] == [1, 2, 3]
    assert all(row["planned_processes"] == 3 for row in progress)
    assert len(list(tmp_path.glob("config-*.json"))) == 3


def test_control_census_keeps_all_nine_boundaries_and_failure_reasons(tmp_path, monkeypatch):
    calls = []

    def recovery(directory, mode, seed):
        calls.append((mode, "recovery", seed))
        if mode == "compact":
            raise ValueError("synthetic recovery failure")
        return {"mode": mode, "control": "original_token_eviction_recovery", "status": "pass"}

    def exit_boundary(root, directory, mode, boundary):
        calls.append((mode, boundary))
        if mode == "raw" and boundary == "before_commit":
            raise subprocess.TimeoutExpired("owned", 120)
        if mode == "whole" and boundary == "after_ack":
            raise OSError("synthetic exit failure")
        return {"mode": mode, "control": boundary, "status": "pass"}

    monkeypatch.setattr(study, "recovery_control", recovery)
    monkeypatch.setattr(study, "exit_control", exit_boundary)
    rows = study.run_controls(tmp_path, tmp_path)
    assert len(rows) == len(calls) == 9
    assert [(row["mode"], row["control"]) for row in rows] == [
        (mode, boundary)
        for mode in ("raw", "compact", "whole")
        for boundary in ("original_token_eviction_recovery", "before_commit", "after_ack")
    ]
    assert [row["error_type"] for row in rows if row["status"] == "worker_failure"] == [
        "TimeoutExpired",
        "ValueError",
        "OSError",
    ]


@pytest.mark.parametrize("mode", ["native", "hash"])
def test_application_floor_cannot_claim_durable_audit_service(mode, monkeypatch):
    calls = []

    class Predictor:
        def predict_proba(self, query):
            calls.append(query.copy())
            return np.array([[0.5, 0.5]])

    ticks = iter(range(0, 1000, 10))
    monkeypatch.setattr(study, "perf_counter_ns", lambda: next(ticks))
    query = np.array([[1.0]])
    row = study.native_cost({"mode": mode, "count": 3}, {"model": Predictor(), "query": query}, 17)
    assert len(calls) == row["native_predictions"] == 3 and row["setup_ns"] == 17
    assert row["timers"]["serving_ns"] == 30
    assert row["timers"]["hash_ns"] == (30 if mode == "hash" else 0)
    assert row["last_output_sha256"] == digest([[0.5, 0.5]])
    assert row["service"] == {
        "accepted": 0,
        "refused": 3,
        "complete": 0,
        "unknown": 3,
        "late": 0,
        "wrong": 0,
        "accepted_but_unserved": 0,
    }
    assert row["native_packets"] == row["offers"] == row["audits"] == []
    check_cost(row)
    with pytest.raises(ValueError, match="nonempty"):
        study.native_cost({"mode": mode, "count": 0}, {"model": Predictor(), "query": query}, 17)


@pytest.mark.parametrize("mode", ["raw", "compact", "whole"])
@pytest.mark.parametrize("boundary", ["before_commit", "after_ack"])
def test_exit_replay_uses_actual_database_frontier_not_return_code_alone(
    tmp_path, monkeypatch, mode, boundary
):
    packet, _ = _fixture_packet("crash-call")

    def child(root, arguments):
        owned = Path(arguments[arguments.index("--directory") + 1])
        owned.mkdir()
        write(owned / "packet.json", packet)
        archive = study.archive(mode, owned / "primary.sqlite", 131072)
        if boundary == "after_ack":
            archive.put_evidence(packet, now=0)
            (owned / "ack.json").write_bytes(b"true")
        archive.close()
        return SimpleNamespace(returncode=19)

    monkeypatch.setattr(study, "_child", child)
    row = study.exit_control(tmp_path, tmp_path, mode, boundary)
    assert row["status"] == "pass" and row["returncode"] == 19
    assert row["ack_observed"] is (boundary == "after_ack")
    assert row["expected_packet"] == packet
    assert (
        row["reopened_witness"]["state"]["entries"] != {}
        if boundary == "after_ack"
        else row["reopened_witness"]["state"]["entries"] == {}
    )


@pytest.fixture
def failed_development(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "environment", lambda: {"fixture": "owned"})
    monkeypatch.setattr(study, "_child", lambda *args: SimpleNamespace(returncode=3))
    plan = {
        "schema": "synthetic-supervisor/v1",
        "code_sha256": study.bindings(ROOT),
        "environment": {"fixture": "owned"},
        "development_seeds": [11, 13],
        "evaluation_seeds": [],
        "cost_seed": 17,
        "logical_quota": 131072,
        "growth_bound": 32768,
    }
    write(tmp_path / "plan.json", plan)
    result = study.run(ROOT, tmp_path, "development")
    return tmp_path, plan, result


def test_synthetic_failed_development_preserves_census_and_replays_read_only(failed_development):
    directory, _, result = failed_development
    before = {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    replay = study.verify(ROOT, directory, "development")
    assert replay["analysis"] == result["analysis"]
    assert replay["analysis"]["prediction_status_counts"] == {"not_tested": 2}
    assert len(replay["analysis"]["frontier"]) == 3
    assert all(not row["same_service_eligible"] for row in replay["analysis"]["frontier"])
    assert {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()} == before
    with pytest.raises(FileExistsError):
        study.run(ROOT, directory, "development")


@pytest.mark.parametrize("mutation", ["analysis", "database", "binding"])
def test_resealed_report_cannot_hide_analysis_database_or_binding_change(
    failed_development, mutation
):
    directory, _, _ = failed_development
    path = directory / "development/results.json"
    report = deepcopy(study.read(path))
    if mutation == "analysis":
        report["analysis"]["false_conclusions"] += 1
    elif mutation == "database":
        (directory / "development/extra.sqlite").write_bytes(b"owned bytes")
    else:
        report["plan_sha256"] = "0" * 64
    write(path, report)
    with pytest.raises(
        ValueError, match="analysis differs|durable database differs|study binding differs"
    ):
        study.verify(ROOT, directory, "development")


def test_database_binding_rejects_alias_and_sealed_reader_rejects_symlink(tmp_path):
    directory = tmp_path / "nested"
    directory.mkdir()
    path = directory / "owned.sqlite"
    path.write_bytes(b"owned database bytes")
    assert study.database_bindings(tmp_path) == {
        "nested/owned.sqlite": hashlib.sha256(path.read_bytes()).hexdigest()
    }
    link = directory / "alias.sqlite"
    try:
        link.symlink_to(path)
    except (OSError, NotImplementedError):
        pytest.skip("host lacks symlink capability")
    with pytest.raises(ValueError, match="symlink"):
        study.database_bindings(tmp_path)
    with pytest.raises(ValueError, match="symlink"):
        study.read(link)


def test_transfer_wrapper_dispatches_every_declared_arm_once_without_native_calls(monkeypatch):
    calls = []

    def authored_case(arm, seed):
        calls.append((arm, seed))
        return {"arm": arm, "seed": seed, "fixture": "synthetic"}

    monkeypatch.setattr(study, "execute", authored_case)
    report = study.transfer_worker(771)
    assert calls == [(arm, 771) for arm in study.ARMS]
    assert report == {
        "status": "complete",
        "seed": 771,
        "cases": [{"arm": arm, "seed": 771, "fixture": "synthetic"} for arm in study.ARMS],
    }


def test_missing_upstream_source_identity_fails_without_native_execution(monkeypatch):
    monkeypatch.setattr(study, "import_module", lambda name: SimpleNamespace(__file__=None))
    with pytest.raises(ValueError, match="native source identity missing"):
        study.environment()


def test_changed_code_binding_stops_before_child_dispatch(failed_development, monkeypatch):
    directory, plan, _ = failed_development
    plan["code_sha256"][study.FILES[0]] = "0" * 64
    write(directory / "plan.json", plan)
    monkeypatch.setattr(
        study, "_child", lambda *args: pytest.fail("changed binding cannot dispatch")
    )
    with pytest.raises(ValueError, match="code/native environment changed"):
        study.run(ROOT, directory, "development")
