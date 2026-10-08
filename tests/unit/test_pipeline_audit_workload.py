"""Matched service, partial worker truth and no unknown-as-success regression."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from aletheia_lab.evaluation import pipeline_audit_source as source
from aletheia_lab.evaluation import pipeline_audit_workload as workload
from aletheia_lab.evaluation.calibration_audit_archive import frame
from aletheia_lab.evaluation.calibration_audit_study import archive
from aletheia_lab.evaluation.pipeline_audit_progress import rebuild
from aletheia_lab.evaluation.pipeline_audit_study import execute_cost
from aletheia_lab.evaluation.request_model_audit import resolve

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def native_runtime():
    native = pytest.importorskip("sklearn")
    if native.__version__ != "1.9.0":
        pytest.skip("scientific source contract pins sklearn 1.9.0")


def config(mode="compact"):
    return {
        "mode": mode,
        "count": 2,
        "repeat": 0,
        "quota_kind": "sufficient",
        "quota": 262144,
        "bound": 32768,
        "seed": 4101,
        "deadline_ms": 30000,
    }


@pytest.mark.parametrize("mode", ["raw", "compact", "whole", "native", "hash"])
def test_completed_stage_timers_and_offered_service(tmp_path, mode):
    directory, specification = tmp_path / mode, config(mode)
    assert workload.worker(specification, directory)["status"] == "complete"
    row = rebuild(directory, specification, interrupted=False)
    assert row["service"]["native_completed"] == 2
    assert row["service"]["wrong"] == 0
    if mode in {"native", "hash"}:
        assert row["service"]["offered"] == row["service"]["refused"] == 0
        assert row["service"]["complete"] == 0
        assert row["terminal"]["closed_storage_bytes"] == 0
    else:
        assert row["service"]["accepted"] == row["service"]["complete"] == 2
        assert row["service"]["accepted_without_proven_service"] == 0
        assert row["terminal"]["timers"]["instrumented_prediction_ns"] > 0
    assert row["terminal"]["timers"]["progress_journal_ns"] > 0


def test_stage_error_keeps_accepted_obligation_and_partial_timer(tmp_path, monkeypatch):
    def fail(*args):
        raise RuntimeError("owned native-stage error")

    monkeypatch.setattr(workload, "_native", fail)
    directory, specification = tmp_path / "partial", config()
    with pytest.raises(RuntimeError, match="native-stage"):
        workload.worker(specification, directory)
    result = rebuild(directory, specification, interrupted=True)
    assert result["service"]["accepted"] == 1
    assert result["service"]["accepted_but_unserved"] == 1
    assert result["service"]["native_indeterminate"] == 1
    assert result["service"]["not_started"] == 1
    assert result["failed_operation_ns"] > 0
    assert result["completed_stage_timers"]["reserve_ack_ns"] > 0
    assert result["terminal"] is None


def test_unknown_native_failure_cannot_fulfil_accepted_service(tmp_path, monkeypatch):
    original = source.predict

    def invalid(session, token):
        return original(session, token, invalid=token != "warmup")

    monkeypatch.setattr(source, "predict", invalid)
    directory, specification = tmp_path / "failure", config()
    workload.worker(specification, directory)
    row = rebuild(directory, specification, interrupted=False)
    assert row["service"]["native_failed"] == 2
    assert row["service"]["accepted"] == 2
    assert row["service"]["unknown"] == 2
    assert row["service"]["complete"] == 0
    assert row["service"]["accepted_but_unserved"] == 2


def test_partial_pipeline_frame_is_not_false_approval_violation(tmp_path):
    packet = source.execute("missing_preprocessing_with_transformed", 4101)["captured_packet"]
    assert source.assess(packet)["authorized_state"] == "unknown"
    assert resolve(frame(packet)) == "unknown"
    owner = archive("compact", tmp_path / "archive.sqlite", 262144)
    owner.put_evidence(packet, now=0)
    assert owner.evidence(packet["token"]) == packet
    owner.close()


def test_owned_process_exit_keeps_ack_without_inventing_audit(tmp_path):
    specification = {**config(), "interrupt": "after_ack"}
    row = execute_cost(ROOT, tmp_path, specification, "exit", 30)
    assert row["status"] == "worker_failure"
    assert row["diagnostics"]["returncode"] == 23
    assert row["service"]["accepted"] == row["service"]["accepted_but_unserved"] == 1
    assert row["service"]["native_completed"] == 1
    assert row["service"]["audit_unobserved"] == 1
    assert row["service"]["not_started"] == 1


def test_resealed_terminal_timer_reduction_is_rejected(tmp_path):
    from aletheia_lab.evaluation.audit_stage_journal import read_progress
    from aletheia_lab.evaluation.model_load_retention import encode
    from aletheia_lab.evaluation.request_model_audit import digest

    directory, specification = tmp_path / "forged", config()
    workload.worker(specification, directory)
    path = directory / "progress.jsonl"
    records = copy.deepcopy(read_progress(path, specification, interrupted=False)["records"])
    records[-1]["payload"]["timers"]["query_verify_ns"] = 0
    previous, lines = "0" * 64, []
    for record in records:
        record["previous"] = previous
        previous = digest(record)
        lines.append(encode({**record, "sha256": previous}))
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError, match="timers differ"):
        rebuild(directory, specification, interrupted=False)
