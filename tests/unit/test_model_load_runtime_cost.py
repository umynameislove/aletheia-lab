"""Cost census and failure semantics without real SDK loads in unit tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation import model_load_runtime_cost as cost
from aletheia_lab.evaluation.model_load_provenance import document_digest


@pytest.mark.parametrize("mode", cost.MODES)
@pytest.mark.parametrize("failure", ("none", "native", "outer"))
def test_measured_phases_and_failed_entries_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, failure: str
) -> None:
    payload = b"local-artifact"
    path = tmp_path / "A.bin"
    path.write_bytes(payload)
    domain = (hashlib.sha256(payload).hexdigest(), "b" * 64)

    class FakeAdapter:
        def __init__(self, backend: str, before: Any, after: Any) -> None:
            self.before, self.after = before, after

        def load(self, offered: bytes) -> object:
            self.before(offered)
            self.after(failure != "native")
            if failure != "none":
                raise RuntimeError("local failure after native entry")
            return object()

    monkeypatch.setattr(cost, "NativeAdapter", FakeAdapter)
    result = cost._measure("skops", mode, path, domain, 0)
    assert result["status"] == ("completed" if failure == "none" else "failed")
    assert result["native_entries"] == 1
    assert result["native_completed"] == int(failure != "native")
    assert all(value >= 0 for value in result["phases"].values())
    assert (
        result["read_to_decision_ns"]
        == result["load_and_observer_ns"] + result["phases"]["read_ns"]
    )
    if mode == "observer_disabled":
        assert result["phases"]["hash_ns"] == result["phases"]["write_ns"] == 0
    if mode in {"full", "static_sufficient"}:
        assert result["resources"]["inserts"] == (3 if failure == "none" else 2)
    else:
        assert result["resources"] is None


def test_worker_import_setup_is_outside_timing_and_rejects_changed_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"artifact"
    (tmp_path / "skops-A.bin").write_bytes(payload)
    manifest = {
        "skops": {"A": {"sha256": hashlib.sha256(payload).hexdigest()}, "B": {"sha256": "b" * 64}}
    }
    (tmp_path / "artifacts.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(cost, "environment", lambda: {"python": "test"})
    imported: list[str] = []
    monkeypatch.setattr(cost.importlib, "import_module", lambda name: imported.append(name))
    calls: list[int] = []
    monkeypatch.setattr(
        cost,
        "_measure",
        lambda backend, mode, path, domain, index: calls.append(index) or {"sample": index},
    )
    report = cost.worker("skops", "hash_only", tmp_path)
    assert imported == ["skops.io"] and calls == [0, 1, 2]
    assert len(report["samples"]) == 3 and report["setup_and_import_ns"] > 0
    (tmp_path / "skops-A.bin").write_bytes(b"changed")
    with pytest.raises(ValueError, match="preparation"):
        cost.worker("skops", "hash_only", tmp_path)
    with pytest.raises(ValueError, match="census"):
        cost.worker("joblib", "hash_only", tmp_path)


def test_failed_worker_census_is_not_dropped_and_credentials_are_not_inherited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def prepare(directory: Path) -> dict[str, Any]:
        for name in ("artifacts.json", "skops-A.bin", "onnxruntime-A.bin"):
            (directory / name).write_bytes(b"inert")
        return {}

    calls: list[list[str]] = []

    def failed(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert "AWS_SECRET_ACCESS_KEY" not in kwargs["env"]
        assert kwargs["timeout"] == 90 and kwargs["check"] is False
        return subprocess.CompletedProcess(command, 1, "", "private error not published")

    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not-a-real-key")
    monkeypatch.setattr(cost, "_prepare", prepare)
    monkeypatch.setattr(cost.subprocess, "run", failed)
    result = cost.run_native_cost(tmp_path)
    assert len(calls) == len(result["workers"]) == 24
    assert len(result["samples"]) == result["planned_native_loads"] == 72
    assert result["status_counts"] == {"worker_failed_unknown": 72}
    assert result["known_native_entries"] == 0 and result["all_native_entry_counts_known"] is False
    assert "private error" not in json.dumps(result)


def test_summary_keeps_negative_delta_and_separates_failed_samples() -> None:
    rows = [
        {
            "backend": "skops",
            "temperature": "first_use_after_import",
            "mode": mode,
            "status": "completed",
            "load_and_observer_ns": value,
            "phases": {"hash_ns": 1},
        }
        for mode, value in (("observer_disabled", 100), ("hash_only", 90))
    ]
    rows.append(
        {
            "backend": "skops",
            "temperature": "first_use_after_import",
            "mode": "hash_only",
            "status": "failed",
        }
    )
    summary = cost.summarize(rows)["skops/first_use_after_import"]
    assert summary["hash_only"]["delta_from_disabled_ns"] == -10
    assert summary["hash_only"]["ratio_to_disabled"] == 0.9
    assert summary["hash_only"]["status_counts"] == {"completed": 1, "failed": 1}
    assert summary["full"]["median_ns"] is None


def test_verify_refuses_report_byte_or_code_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cost, "code_bindings", lambda root: {"code": "a" * 64})
    report: dict[str, Any] = {
        "code_bindings": {"code": "a" * 64},
        "native_cost": {"summary": {}, "samples": []},
        "provider_calls": 0,
        "schema_version": "model-load-runtime-development/v1",
        "status": "development_complete",
        "protected_validation_executed": False,
    }
    report["report_sha256"] = document_digest(report)
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="census"):
        cost.verify_report(tmp_path, path)
    report["provider_calls"] = 1
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="binding"):
        cost.verify_report(tmp_path, path)


def test_before_callback_failure_does_not_invent_a_native_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "A.bin"
    path.write_bytes(b"measured")

    class WrongBufferAdapter:
        def __init__(self, backend: str, before: Any, after: Any) -> None:
            self.before = before

        def load(self, offered: bytes) -> object:
            self.before(b"a different buffer")
            raise AssertionError("must fail before native entry")

    monkeypatch.setattr(cost, "NativeAdapter", WrongBufferAdapter)
    result = cost._measure(
        "skops", "full", path, (hashlib.sha256(b"measured").hexdigest(), "b" * 64), 0
    )
    assert result["status"] == "failed"
    assert result["native_entries"] == result["native_completed"] == 0
    assert result["phases"]["sdk_load_ns"] > 0
    assert result["phases"]["native_entry_ns"] == 0
    assert result["phases"]["object_release_ns"] == 0
