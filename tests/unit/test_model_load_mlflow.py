from __future__ import annotations

import copy
import os
import pickle
import socket
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation import model_load_mlflow as runtime
from aletheia_lab.evaluation import model_load_provenance_study as study
from aletheia_lab.evaluation.model_load_provenance import document_digest

pytest.importorskip("mlflow", reason="requires the pinned provenance optional extra")
pytest.importorskip("in_toto", reason="requires the pinned provenance optional extra")
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def report() -> dict[str, Any]:
    return study.run_development(ROOT)


def test_actual_sdk_census_signed_baseline_and_capture_pair(report: dict[str, Any]) -> None:
    summary = report["summary"]
    assert summary["episode_count"] == 8 and summary["workflow_cluster_count"] == 1
    assert summary["planned_load_attempts"] == summary["completed_load_attempts"] == 7
    assert summary["cache_only_attempts"] == 1 and summary["retained_auxiliary_native_loads"] == 4
    assert summary["technical_failure_count"] == summary["same_evidence_disagreements"] == 0
    assert summary["reference_status_counts"] == {"compliant": 3, "violation": 4, "no_new_load": 1}
    for method in ("S", "T", "P"):
        assert summary["comparisons"][method]["correct_identified"] == 6
        assert summary["comparisons"][method]["false_compliance"] == 0
        assert summary["comparisons"][method]["false_violation"] == 0
        assert summary["comparisons"][method]["verdict_counts"] == {
            "compliant": 3,
            "violation": 3,
            "unknown": 1,
        }
    assert summary["real_scoped_verifier_calls"] == 7
    assert summary["path_only_verification_counts"] == {"pass": 7, "not_applicable": 1}
    assert summary["critical_pair"]["established"] is True
    assert report["disposition"] == "bounded_capture_finding_no_new_checker_advantage"
    assert report["validation_locked"] is False
    assert report["provider_calls"] == 0 and report["historical_artifacts_read"] is False
    legal, race = report["rows"][0], report["rows"][3]
    assert legal["native_fact_frame"] == race["native_fact_frame"]
    assert legal["target_buffers"][0]["raw_hex"] != race["target_buffers"][0]["raw_hex"]
    assert legal["target_buffers"][0]["prediction"] != race["target_buffers"][0]["prediction"]


def test_replay_is_hash_only_never_unpickles_retained_model(
    report: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_: object, **__: object) -> None:
        raise AssertionError("replay cannot deserialize retained model bytes")

    monkeypatch.setattr(pickle, "load", forbidden)
    monkeypatch.setattr(pickle, "loads", forbidden)
    assert study.verify_report(report, ROOT)["verification"] == "pass"
    row = copy.deepcopy(report["rows"][3])
    row["observation"] = {}
    row["decisions"] = {}
    assert study.reference(row, report["artifact_sha256"]) == row["reference"]


@pytest.mark.parametrize(
    "mutation",
    [
        "identity",
        "code",
        "schema",
        "census",
        "truth",
        "decision",
        "summary",
        "disposition",
        "validation",
        "calls",
        "history",
        "packages",
        "scope",
        "policy",
        "snapshot",
        "native-frame",
        "buffer",
        "path",
        "metadata",
        "mlmodel",
        "closure",
        "selection",
        "observer-buffer",
        "retry",
        "transport",
    ],
    ids=lambda name: name,
)
def test_rehashed_report_cannot_change_capture_or_promote_development(
    report: dict[str, Any],
    mutation: str,
) -> None:
    value = copy.deepcopy(report)
    row = value["rows"][0]
    buffer = row["target_buffers"][0]
    if mutation == "identity":
        value["report_sha256"] = "bad"
    elif mutation == "code":
        value["code_sha256"][study.CODE_PATHS[0]] = "bad"
    elif mutation == "schema":
        value["schema_version"] = "other"
    elif mutation == "census":
        value["rows"].pop()
    elif mutation == "truth":
        row["reference"]["verdict"] = "violation"
    elif mutation == "decision":
        row["decisions"]["P"]["verdict"] = "violation"
    elif mutation == "summary":
        value["summary"]["episode_count"] += 1
    elif mutation == "disposition":
        value["disposition"] = "new-method-advantage"
    elif mutation == "validation":
        value["validation_locked"] = True
    elif mutation == "calls":
        value["provider_calls"] = 1
    elif mutation == "history":
        value["historical_artifacts_read"] = True
    elif mutation == "packages":
        value["environment"]["packages"]["mlflow-skinny"] = "other"
    elif mutation == "scope":
        row["observation"]["scope"]["request"] = "foreign"
    elif mutation == "policy":
        row["observation"]["contract"]["policy"] = "resolve_at_load"
    elif mutation == "snapshot":
        row["accepted_snapshot"]["version"] = "3"
    elif mutation == "native-frame":
        row["native_fact_frame"]["current_alias"]["version"] = "3"
    elif mutation == "buffer":
        buffer["raw_hex"] = value["rows"][3]["target_buffers"][0]["raw_hex"]
    elif mutation == "path":
        buffer["after_sha256"] = value["artifact_sha256"]["B"]
    elif mutation == "metadata":
        buffer["registered_model_meta"] = "model_name: model-load-development\nmodel_version: '2'\n"
    elif mutation == "mlmodel":
        buffer["model_metadata_text"] += "\nchanged: true\n"
    elif mutation == "closure":
        row["observation"]["records"][-1]["load_count"] = 2
    elif mutation == "selection":
        row["observation"]["records"][0]["digest"] = value["artifact_sha256"]["B"]
    elif mutation == "observer-buffer":
        row["observation"]["records"][1]["digest"] = value["artifact_sha256"]["B"]
    elif mutation == "retry":
        value["rows"][4]["observation"]["records"][0]["parent_selection"] = "foreign"
    elif mutation == "transport":
        row["transport"]["dropped"] = 1
    if mutation != "identity":
        value["report_sha256"] = document_digest(
            {k: v for k, v in value.items() if k != "report_sha256"}
        )
    with pytest.raises((ValueError, KeyError)):
        study.verify_report(value, ROOT)


def test_local_sdk_denies_network_and_restores_global_settings_after_error(tmp_path: Path) -> None:
    import mlflow

    previous = mlflow.get_tracking_uri(), mlflow.get_registry_uri()
    old_env = os.environ.get("MLFLOW_DISABLE_TELEMETRY")
    unpickler, connect = pickle.load, socket.create_connection
    with pytest.raises(RuntimeError, match="network"), runtime.local_sdk(tmp_path):
        socket.create_connection(("127.0.0.1", 9))
    assert (mlflow.get_tracking_uri(), mlflow.get_registry_uri()) == previous
    assert os.environ.get("MLFLOW_DISABLE_TELEMETRY") == old_env
    assert pickle.load is unpickler and socket.create_connection is connect


def test_reference_retains_unknown_failed_slot_and_rejects_cache_load(
    report: dict[str, Any],
) -> None:
    row = copy.deepcopy(report["rows"][-1])
    row["target_buffers"] = report["rows"][0]["target_buffers"]
    with pytest.raises(ValueError, match="cache exclusion"):
        study.reference(row, report["artifact_sha256"])
    assert study.reference({"terminal": "technical_failure"}, {}) == {
        "verdict": "unknown",
        "eligibility": "intended_load",
    }


def test_sdk_exception_retained_in_full_denominator(monkeypatch: pytest.MonkeyPatch) -> None:
    from mlflow.exceptions import MlflowException

    original = runtime.NativeWorkflow.run

    def fail_once(self: runtime.NativeWorkflow, schedule: runtime.Schedule) -> dict[str, Any]:
        if schedule.name == "same_path_restored_fault":
            raise MlflowException("sensitive exception text must not be retained")
        return original(self, schedule)

    monkeypatch.setattr(runtime.NativeWorkflow, "run", fail_once)
    report = study.run_development(ROOT)
    assert report["summary"]["planned_load_attempts"] == 7
    assert report["summary"]["completed_load_attempts"] == 6
    assert report["summary"]["technical_failure_count"] == 1
    assert report["disposition"] == "incomplete_development_census"
    assert report["rows"][3]["error_type"] == "MlflowException"
    assert "sensitive" not in str(report)
    assert study.verify_report(report, ROOT)["verification"] == "pass"


def test_code_change_during_run_is_not_bound_to_a_report(monkeypatch: pytest.MonkeyPatch) -> None:
    original = study.file_sha256
    calls = 0

    def changing(path: Path) -> str:
        nonlocal calls
        calls += 1
        return "f" * 64 if calls > len(study.CODE_PATHS) else original(path)

    monkeypatch.setattr(study, "file_sha256", changing)
    with pytest.raises(ValueError, match="code changed"):
        study.run_development(ROOT)


def test_loader_refuses_unowned_or_unknown_bytes_before_native_unpickle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from aletheia_lab.evaluation.model_load_contract import Record, Scope
    from aletheia_lab.evaluation.model_load_runtime import Transport

    calls = []
    monkeypatch.setattr(pickle, "load", lambda *args, **kwargs: calls.append(1))

    def fake_load(uri: str, dst_path: str) -> None:
        path = Path(dst_path) / "model.pkl"
        path.write_bytes(b"unknown-not-a-pickle")
        with path.open("rb") as stream:
            pickle.load(stream)

    workflow = runtime.NativeWorkflow.__new__(runtime.NativeWorkflow)
    workflow.sdk = SimpleNamespace(sklearn=SimpleNamespace(load_model=fake_load))
    workflow.name = "model-load-development"
    workflow.digests = {"A": "a" * 64, "B": "b" * 64}
    scope = Scope("request", 0)
    selected = Record("select", scope, "selection", "a" * 64, "token", 1, "pin_at_acceptance")
    spool = Transport(tmp_path / "observer.sqlite", "complete", scope)
    try:
        with pytest.raises(ValueError, match="freshly generated"):
            workflow.consume(tmp_path, scope, selected, spool)
        assert not calls
        with (
            (tmp_path / "outside.pkl").open("wb") as stream,
            pytest.raises(ValueError, match="unowned"),
        ):
            runtime._owned_model_path(stream, tmp_path / "consumer-0")
    finally:
        spool.close()


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"], ids=["lf", "crlf"])
def test_capture_preserves_native_metadata_bytes_for_hash_only_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    newline: bytes,
) -> None:
    from types import SimpleNamespace

    from aletheia_lab.evaluation.model_load_contract import Record, Scope
    from aletheia_lab.evaluation.model_load_runtime import Transport

    raw = b"owned-local-buffer"
    metadata = "name: native-café\nflavor: sklearn\n".encode().replace(b"\n", newline)
    consumed: list[bytes] = []

    def fake_unpickler(stream: Any) -> SimpleNamespace:
        consumed.append(stream.read())
        return SimpleNamespace(predict=lambda _: SimpleNamespace(tolist=lambda: [0, 1]))

    def fake_load(uri: str, dst_path: str) -> Any:
        directory = Path(dst_path)
        (directory / "model.pkl").write_bytes(raw)
        (directory / "MLmodel").write_bytes(metadata)
        (directory / "registered_model_meta").write_bytes(
            b"model_name: model-load-development\nmodel_version: '1'\n"
        )
        with (directory / "model.pkl").open("rb") as stream:
            return pickle.load(stream)

    monkeypatch.setattr(pickle, "load", fake_unpickler)
    workflow = runtime.NativeWorkflow.__new__(runtime.NativeWorkflow)
    workflow.sdk = SimpleNamespace(sklearn=SimpleNamespace(load_model=fake_load))
    workflow.name = "model-load-development"
    workflow.digests = {"A": runtime.content_sha256(raw), "B": "b" * 64}
    scope = Scope("request", 0)
    selected = Record(
        "select", scope, "selection", workflow.digests["A"], "token", 1, "pin_at_acceptance"
    )
    spool = Transport(tmp_path / "observer.sqlite", "complete", scope)
    try:
        captured = workflow.consume(tmp_path, scope, selected, spool)
    finally:
        spool.close()
    assert consumed == [raw]
    assert captured["model_metadata_text"].encode("utf-8") == metadata
    assert captured["model_metadata_sha256"] == runtime.content_sha256(metadata)
    # A separate JSON round trip must retain native line endings, not normalize
    # their hash away. Replay still rejects a changed byte or LF-only substitute.
    import json

    retained = json.loads(json.dumps(captured))
    study._validate_buffer(retained, workflow.digests, "A", "A", False)
    retained["model_metadata_text"] += "changed"
    with pytest.raises(ValueError, match="native MLmodel hash changed"):
        study._validate_buffer(retained, workflow.digests, "A", "A", False)
    if newline == b"\r\n":
        retained["model_metadata_text"] = captured["model_metadata_text"].replace("\r\n", "\n")
        with pytest.raises(ValueError, match="native MLmodel hash changed"):
            study._validate_buffer(retained, workflow.digests, "A", "A", False)
