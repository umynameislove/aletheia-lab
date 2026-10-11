"""Actual CLI offline verification without importing/entering native SDKs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation.model_load_provenance import document_digest
from aletheia_lab.evaluation.model_load_retention_concurrency import run_concurrency
from aletheia_lab.evaluation.model_load_runtime_cost import (
    BACKENDS,
    MODES,
    PHASE_KEYS,
    code_bindings,
    summarize,
    verify_report,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/model_load_runtime_development.py"


def _run(command: str, path: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, str(SCRIPT), command, "--root", str(ROOT), "--report", str(path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


def _report() -> dict[str, Any]:
    samples = [
        {
            "backend": backend,
            "mode": mode,
            "repeat": repeat,
            "sample": sample,
            "temperature": "first_use_after_import" if sample == 0 else "warm_fresh_object",
            "status": "completed",
            "native_entries": 1,
            "native_completed": 1,
            "offered_boundaries": 1,
            "artifact_sha256": "a" * 64,
            "artifact_bytes": 1,
            "load_and_observer_ns": 1000,
            "read_to_decision_ns": 1000,
            "phases": {key: 0 for key in PHASE_KEYS},
        }
        for backend in BACKENDS
        for mode in MODES
        for repeat in range(3)
        for sample in range(3)
    ]
    report = {
        "schema_version": "model-load-runtime-development/v1",
        "status": "development_complete",
        "code_bindings": code_bindings(ROOT),
        "provider_calls": 0,
        "protected_validation_executed": False,
        "scope": "synthetic test cost values; no native loads executed",
        "concurrency": run_concurrency(),
        "native_cost": {
            "summary": summarize(samples),
            "samples": samples,
            "workers": [
                {"backend": backend, "mode": mode, "repeat": repeat, "status": "completed"}
                for backend in BACKENDS
                for mode in MODES
                for repeat in range(3)
            ],
            "planned_workers": 24,
            "planned_native_loads": 72,
            "preparation_fits": 2,
            "all_native_entry_counts_known": True,
            "artifacts": {backend: {"A": {"sha256": "a" * 64, "bytes": 1}} for backend in BACKENDS},
            "status_counts": {"completed": 72},
            "known_native_entries": 72,
            "known_native_completed": 72,
        },
    }
    report["report_sha256"] = document_digest(report)
    return report


def test_cli_read_only_rebuild_and_changed_summary_fail_closed(tmp_path: Path) -> None:
    report = _report()
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    before = path.read_bytes()
    verified = _run("verify", path)
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert json.loads(verified.stdout)["report_sha256"] == report["report_sha256"]
    assert path.read_bytes() == before
    report["native_cost"]["known_native_completed"] -= 1
    report.pop("report_sha256")
    report["report_sha256"] = document_digest(report)
    path.write_text(json.dumps(report))
    rejected = _run("verify", path)
    assert rejected.returncode == 1
    assert json.loads(rejected.stdout)["status"] == "runtime_development_failed_closed"
    assert str(tmp_path) not in rejected.stdout


def test_cli_existing_report_and_protected_destination_rejected_before_execution(
    tmp_path: Path,
) -> None:
    path = tmp_path / "already.json"
    path.write_bytes(b"preserve")
    assert _run("run", path).returncode == 1
    assert path.read_bytes() == b"preserve"
    forbidden = ROOT.parent / "memory/model-load-validation-v1/not-created-by-test.json"
    assert _run("run", forbidden).returncode == 1
    assert not forbidden.exists()


@pytest.mark.parametrize(
    "mutation",
    (
        "schema",
        "protected",
        "disposition",
        "provider",
        "planned",
        "fit",
        "workers",
        "worker_status",
        "known",
        "entries",
        "phase",
        "omitted_phase",
        "artifact",
        "unknown_entries",
        "incomplete_complete",
    ),
)
def test_rehashed_report_cannot_hide_changed_contract_or_failed_census(
    tmp_path: Path, mutation: str
) -> None:
    report = _report()
    native = report["native_cost"]
    row = native["samples"][0]
    if mutation == "schema":
        report["schema_version"] = "different/v1"
    elif mutation == "protected":
        report["protected_validation_executed"] = True
    elif mutation == "disposition":
        report["status"] = "production_proven"
    elif mutation == "provider":
        report["provider_calls"] = 1
    elif mutation == "planned":
        native["planned_native_loads"] = 71
    elif mutation == "fit":
        native["preparation_fits"] = 3
    elif mutation == "workers":
        native["workers"][0] = native["workers"][1]
    elif mutation == "worker_status":
        native["workers"][0]["status"] = "worker_failed_entry_count_unknown"
    elif mutation == "known":
        native["all_native_entry_counts_known"] = False
    elif mutation == "entries":
        row["native_entries"] = 2
        native["known_native_entries"] = 73
    elif mutation == "phase":
        row["phases"]["hash_ns"] = -1
    elif mutation == "omitted_phase":
        for sample in native["samples"]:
            sample["phases"].pop("sdk_load_ns")
    elif mutation == "artifact":
        row["artifact_sha256"] = "b" * 64
    elif mutation == "unknown_entries":
        for sample in native["samples"][:3]:
            sample["status"] = "worker_failed_unknown"
        native["workers"][0]["status"] = "worker_failed_entry_count_unknown"
        native["all_native_entry_counts_known"] = False
        native["status_counts"] = {"completed": 69, "worker_failed_unknown": 3}
    elif mutation == "incomplete_complete":
        row["status"] = "failed"
        native["status_counts"] = {"completed": 71, "failed": 1}
    native["summary"] = summarize(native["samples"])
    report.pop("report_sha256")
    report["report_sha256"] = document_digest(report)
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        verify_report(ROOT, path)
