"""Failure-census and immutable-replay contracts with native execution denied."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from aletheia_lab.evaluation import auditability_closeout as closeout
from aletheia_lab.evaluation import incident_audit_transfer as transfer
from aletheia_lab.evaluation import module_realization_study as module_study
from aletheia_lab.evaluation.cache_lifecycle_study import read_sealed, seal
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

ROOT = Path(__file__).resolve().parents[2]


def test_module_failure_census_replays_without_native_or_artifact_construction(
    tmp_path, monkeypatch
):
    directory = tmp_path / "synthetic-study"
    module_study.prepare(ROOT, directory)
    build_calls, worker_calls = [], []

    def build_only(command, **kwargs):
        assert Path(command[1]) == ROOT / "scripts/module_realization_validation.py"
        assert command[2] == "build"
        assert kwargs["env"]["MLFLOW_DISABLE_TELEMETRY"] == "true"
        assert kwargs["env"]["MLFLOW_ENABLE_ASYNC_LOGGING"] == "false"
        build_calls.append(command)
        return subprocess.CompletedProcess(command, 0, b"synthetic build boundary", b"")

    def refused_worker(command, environment, target, config):
        assert Path(command[1]) == ROOT / "scripts/module_realization_validation.py"
        assert command[2] == "worker" and target.parent == directory
        assert json.loads(Path(command[-1]).read_bytes()) == config
        worker_calls.append(command)
        return {"config": config, "failure": "synthetic_native_unavailable"}

    monkeypatch.setattr(module_study.subprocess, "run", build_only)
    monkeypatch.setattr(module_study, "drive", refused_worker)
    result = module_study.run(ROOT, directory, Path("fixture-python"), tmp_path)
    assert len(build_calls) == 1 and len(worker_calls) == 54
    assert result["analysis"]["planned_cells"] == 54
    assert result["analysis"]["verified_cells"] == 0
    assert sum(group["cells"] for group in result["analysis"]["groups"].values()) == 54
    assert all(group["requests"] == 0 for group in result["analysis"]["groups"].values())
    before = (directory / "results.json").read_bytes()
    assert module_study.verify(ROOT, directory)["analysis"] == result["analysis"]
    assert module_study.closeout(ROOT, directory)["analysis"] == result["analysis"]
    assert module_study.closeout(ROOT, directory, save=False)["verification"] == "pass"
    assert (directory / "results.json").read_bytes() == before
    assert len(worker_calls) == 54 and len(build_calls) == 1
    changed = read_sealed(directory / "results.json")
    changed["executions"].pop()
    changed.pop("sha256")
    (directory / "results.json").write_bytes(encode(seal(changed)).encode())
    with pytest.raises(ValueError, match="census"):
        module_study.verify(ROOT, directory)


def test_module_build_failure_preserves_attempt_and_logs(tmp_path, monkeypatch):
    directory = tmp_path / "synthetic-study"
    module_study.prepare(ROOT, directory)

    def refused_build(command, **kwargs):
        assert command[2] == "build"
        return subprocess.CompletedProcess(command, 2, b"partial build", b"synthetic failure")

    monkeypatch.setattr(module_study.subprocess, "run", refused_build)
    with pytest.raises(ValueError, match="construction failed"):
        module_study.run(ROOT, directory, Path("fixture-python"), tmp_path)
    assert (directory / "execution-started.json").is_file()
    assert (directory / "build-stderr.log").read_bytes() == b"synthetic failure"
    assert not (directory / "results.json").exists()


@pytest.mark.parametrize("failure", ["timeout", "nonzero"])
def test_transfer_failed_processes_keep_full_bound_census_and_streams(
    tmp_path, monkeypatch, failure
):
    source = tmp_path / "inert-upstream.py"
    source.write_bytes(b"# owned non-executable metadata fixture\n")
    digest = content_sha256(source.read_bytes())
    monkeypatch.setattr(transfer, "SOURCE_HASH", digest)
    monkeypatch.setattr(transfer, "source_identity", lambda: (source, {"source_sha256": digest}))
    calls = []

    def refused_native(command, **kwargs):
        assert command[1:4] == ["-m", "aletheia_lab.evaluation.incident_audit_transfer", "worker"]
        calls.append(command)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 60, output=b"partial", stderr=b"timeout")
        return subprocess.CompletedProcess(command, 3, b"partial", b"failed")

    monkeypatch.setattr(transfer.subprocess, "run", refused_native)
    directory = tmp_path / "synthetic-study"
    result = transfer.run(ROOT, directory)
    assert len(calls) == result["planned_cells"] == result["failed_processes"] == 8
    before = {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    verified = transfer.verify(ROOT, directory)
    assert verified["verification"] == "pass" and verified["failed_processes"] == 8
    assert {row["status"] for row in verified["cells"]} == {
        "worker_timeout" if failure == "timeout" else "worker_failed"
    }
    assert before == {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    stream = next(directory.glob("*.stdout"))
    stream.write_bytes(b"changed partial log")
    with pytest.raises(ValueError, match="stream differs"):
        transfer.verify(ROOT, directory)


@pytest.mark.parametrize("missing", [False, True])
def test_closeout_analysis_keeps_false_conclusion_and_unserved_admission(tmp_path, missing):
    forecasts = {arm: ["pass", "compliant"] for arm in closeout.ARMS}
    write_new_file(
        tmp_path / "plan.json", encode(closeout.sealed({"transfer_forecasts": forecasts})).encode()
    )
    if not missing:
        for arm in closeout.ARMS:
            rows = [
                {
                    "frame": {"token": f"p{index}"},
                    "reference": "compliant",
                    "resolution": "compliant",
                }
                for index in range(4)
            ]
            document = {
                "chain_rows": rows,
                "source_status": "pass",
                "attempted_native_predictions": 4,
                "offers": [
                    {"scope": ["p0"], "admitted": True, "answers": {"p0": "violation"}},
                    {"scope": ["p1"], "admitted": True, "answers": {"p1": "unknown"}},
                    {"scope": ["p2"], "admitted": False, "answers": {"p2": "compliant"}},
                ],
            }
            write_new_file(
                tmp_path / f"transfer-{arm}.json", encode(closeout.sealed(document)).encode()
            )
        for repeat, latency in enumerate((10, 40, 20)):
            cost = {
                "serving_stage_ns": latency,
                "query_ns": 2,
                "verify_ns": 3,
                "sql_submitted_payload_bytes": 4,
                "closed_db_bytes": 5,
                "peak_db_wal_shm_bytes": 6,
                "answers": {"p0": "compliant", "p1": "unknown"},
                "numerical_correct": repeat != 1,
            }
            write_new_file(
                tmp_path / f"cost-{closeout.MODES[0]}-16-{repeat}.json",
                encode(closeout.sealed(cost)).encode(),
            )
    result = closeout.analyze(tmp_path)
    assert {row["status"] for row in result["predictions"]} == {
        "insufficient_evidence" if missing else "supported"
    }
    assert result["accepted_but_unserved"] == (0 if missing else 2 * len(closeout.ARMS))
    assert result["false_conclusive"] == (0 if missing else len(closeout.ARMS))
    group = next(
        row for row in result["costs"] if row["count"] == 16 and row["mode"] == closeout.MODES[0]
    )
    assert group["completed_workers"] == (0 if missing else 3)
    assert group["serving_stage_ns"] == (None if missing else 20)
    assert group["numerical_reference_pass"] is False
    assert group["correct_audit_count"] == ([] if missing else [1, 1, 1])
    if not missing:
        path = tmp_path / f"transfer-{closeout.ARMS[0]}.json"
        document = closeout.read(path)
        document["chain_rows"][-1]["resolution"] = "unknown"
        document.pop("sha256")
        path.write_bytes(encode(closeout.sealed(document)).encode())
        assert closeout.analyze(tmp_path)["predictions"][0]["status"] == "contradicted"


def test_transfer_cell_checks_independent_arithmetic_before_retention(tmp_path):
    values = [[float(index * 3 + column) for column in range(3)] for index in range(3)]
    weights = transfer.expected_weights()
    assert transfer.matrix_output(values, weights)[0] == [945000, 1015200, 1085400]
    rows = []
    for ordinal in range(3):
        raw = {
            "input": values,
            "weights": copy.deepcopy(weights),
            "output": transfer.matrix_output(values, weights),
            "effective_proto_sha256": "a" * 64,
        }
        frame = transfer.make_frame(ordinal, raw, True)
        rows.append(
            {
                **raw,
                "captured": True,
                "frame": frame,
                "reference": "compliant",
                "capture_answer": "compliant",
            }
        )
    cell = {
        "arm": "lawful",
        "method": transfer.METHODS[0],
        "rows": rows,
        "actual_native_calls": 3,
        "planned_native_calls": 3,
        "native_test_error": None,
        "archives": {},
        "native_files": {},
    }
    assert transfer.verify_cell(cell, tmp_path) == {
        "native_calls": 3,
        "correct_capture": 3,
        "unknown_capture": 0,
        "violations": 0,
    }
    cell["rows"][0]["output"][0][0] += 1
    with pytest.raises(ValueError, match="arithmetic differs"):
        transfer.verify_cell(cell, tmp_path)
