"""Synthetic dispatch failures with real census, log hashes and replay checks."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from aletheia_lab.evaluation import model_load_application_analysis as application
from aletheia_lab.evaluation import response_origin_study as origin
from aletheia_lab.evaluation import serving_audit_cost_analysis as cost_analysis
from aletheia_lab.evaluation import serving_audit_cost_study as cost
from aletheia_lab.evaluation.request_model_audit import digest
from aletheia_lab.project.identity import content_sha256


@pytest.mark.parametrize("failure", ["timeout", "nonzero"])
def test_origin_failed_launches_retain_every_offered_inference(tmp_path, monkeypatch, failure):
    root = Path(origin.__file__).resolve().parents[3]
    calls = []

    def refused(command, **kwargs):
        assert Path(command[1]) == root / "scripts/response_origin_validation.py"
        assert command[2] == "worker"
        assert kwargs["env"]["OMP_NUM_THREADS"] == "1"
        calls.append(command)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 90, output=b"partial", stderr=b"failed")
        return subprocess.CompletedProcess(command, 2, b"partial", b"failed")

    monkeypatch.setattr(origin.subprocess, "run", refused)
    directory = tmp_path / "synthetic-study"
    result = origin.run(root, directory, Path("fixture-python"))
    analysis = result["analysis"]
    assert len(calls) == analysis["cell_count"] == analysis["failed_or_missing_cells"] == 36
    assert analysis["offered_inferences"] == 180 and analysis["attempted_operations"] == 0
    assert sum(group["reference_counts"]["unknown"] for group in analysis["by_arm"].values()) == 180
    assert all(
        group["capture_comparators"]["origin"]["false_conclusive"] == 0
        for group in analysis["by_arm"].values()
    )
    before = {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    assert origin.verify(root, directory)["native_execution_repeated"] is False
    assert before == {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    execution = directory / "cell-00/execution.json"
    changed = json.loads(execution.read_bytes())
    changed["arm"] = "invented-arm"
    execution.write_bytes(json.dumps(changed).encode())
    with pytest.raises(ValueError, match="identity differs"):
        origin.verify(root, directory)


def test_origin_rehashed_aggregate_cannot_hide_missing_offers(tmp_path, monkeypatch):
    root = Path(origin.__file__).resolve().parents[3]
    monkeypatch.setattr(
        origin.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 2, b"", b"failed"),
    )
    directory = tmp_path / "synthetic-study"
    origin.run(root, directory, Path("fixture-python"))
    path = directory / "results.json"
    report = json.loads(path.read_bytes())
    report["analysis"]["offered_inferences"] = 0
    report.pop("results_sha256")
    report["results_sha256"] = digest(report)
    path.write_bytes(json.dumps(report).encode())
    with pytest.raises(ValueError, match="raw independent replay"):
        origin.verify(root, directory)


@pytest.mark.parametrize("failure", ["timeout", "nonzero", "oserror"])
def test_cost_dispatch_is_single_use_and_preserves_all_worker_failures(
    tmp_path, monkeypatch, failure
):
    root = Path(cost.__file__).resolve().parents[3]
    path = tmp_path / "plan.json"
    path.write_bytes(b"synthetic dispatch boundary only")
    plan = {
        "root": str(root),
        "configs": cost.configurations(),
        "worker_timeout_seconds": 1,
        "plan_sha256": content_sha256(path.read_bytes()),
    }
    seals = []

    def checked(received):
        assert received == path
        seals.append(received)
        return plan

    monkeypatch.setattr(cost, "check_plan", checked)
    calls = []

    def refused(command, **kwargs):
        assert Path(command[1]) == root / cost.CODE[0] and command[2] == "worker"
        assert command[command.index("--plan") + 1] == str(path.absolute())
        calls.append(command)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 1, output=b"partial", stderr=b"timeout")
        if failure == "oserror":
            raise OSError("synthetic launch unavailable")
        return subprocess.CompletedProcess(command, 7, b"partial", b"failed")

    monkeypatch.setattr(cost.subprocess, "run", refused)
    result = cost.execute(path)
    assert len(calls) == len(result["workers"]) == 50 and len(seals) == 2
    assert [row["config"] for row in result["workers"]] == plan["configs"]
    assert all(row["worker_report_sha256"] is None for row in result["workers"])
    assert all(
        row["returncode"] == (7 if failure == "nonzero" else None) for row in result["workers"]
    )
    assert all(
        row["error_type"]
        == {"timeout": "TimeoutExpired", "oserror": "OSError", "nonzero": None}[failure]
        for row in result["workers"]
    )
    assert all(row["supervisor_elapsed_ns"] >= 0 for row in result["workers"])
    assert json.loads((tmp_path / "execution.json").read_bytes()) == result
    analysis = cost_analysis.analyze(tmp_path, plan)
    assert analysis["missing_worker_reports"] == 50
    assert analysis["known_native_calls"] == 0
    assert analysis["service_census"]["planned_offered"] == 1920
    assert analysis["service_census"]["acceptance_unknown"] == 1920
    assert analysis["service_census"]["accepted"] is None
    assert analysis["service_census"]["accepted_but_unserved"] is None
    assert analysis["service_census"]["complete"] == 0
    assert {row["forecast"] for row in analysis["forecasts"].values()} == {"unidentified"}
    if failure != "oserror":
        assert (
            tmp_path / "workers" / plan["configs"][0]["worker_id"] / "stdout.txt"
        ).read_bytes() == b"partial"
    with pytest.raises(FileExistsError):
        cost.execute(path)
    assert len(calls) == 50


@pytest.mark.parametrize("failure", ["timeout", "nonzero"])
def test_application_failure_replay_keeps_four_arms_and_drops_provider_environment(
    tmp_path, monkeypatch, failure
):
    root = Path(application.__file__).resolve().parents[3]
    monkeypatch.setattr(application, "os", SimpleNamespace(name="posix", environ=os.environ))
    monkeypatch.setattr(
        application, "environment", lambda: {"classification": "owned-unit-metadata"}
    )
    monkeypatch.setattr(
        application,
        "prepare_artifacts",
        lambda path: {"A": content_sha256(b"inert-owned-A"), "B": content_sha256(b"inert-owned-B")},
    )
    for prefix in ("OPENAI_", "ANTHROPIC_", "AWS_", "AZURE_"):
        monkeypatch.setenv(prefix + "SYNTHETIC_TEST", "inert boundary test")
    calls = []

    def refused(command, **kwargs):
        assert command[1:3] == ["-c", application.WORKER_CODE]
        assert not any(
            key.startswith(("OPENAI_", "ANTHROPIC_", "AWS_", "AZURE_")) for key in kwargs["env"]
        )
        calls.append(command)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 90)
        return subprocess.CompletedProcess(command, 3, "", "synthetic worker failure")

    monkeypatch.setattr(application.subprocess, "run", refused)
    result = application.run(root)
    assert len(calls) == len(result["rows"]) == 4
    assert [row["arm"] for row in result["rows"]] == list(application.ARMS)
    assert {row["status"] for row in result["rows"]} == {
        "timeout" if failure == "timeout" else "worker_failed"
    }
    assert result["summary"]["completed_arms"] == 0
    assert result["summary"]["failed_arms_with_unknown_partial_native_census"] == 4
    assert result["comparison"]["unscored_planned_operations"] == 26
    assert result["comparison"]["actual_in_toto_verifier_calls"] == 0
    path = tmp_path / "synthetic-report.json"
    path.write_bytes(json.dumps(result).encode())
    before = path.read_bytes()
    assert application.verify(root, path) == result
    assert path.read_bytes() == before and len(calls) == 4
    result["provider_calls"] = 1
    result.pop("report_sha256")
    result["report_sha256"] = application.document_digest(result)
    path.write_bytes(json.dumps(result).encode())
    with pytest.raises(ValueError, match="scope changed"):
        application.verify(root, path)
