"""Actual optional MLServer handlers and private hash-only replay; no sockets."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mlserver")
pytest.importorskip("mlserver_sklearn")
pytest.importorskip("in_toto")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.name == "nt", reason="held-descriptor atomic replacement not qualified on Windows"
    ),
]

from aletheia_lab.evaluation import model_load_application_analysis as study  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def test_public_cli_refuses_standalone_worker_before_artifact_reads(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/model_load_application.py"),
            "worker",
            "--root",
            str(ROOT),
            "--artifacts",
            str(tmp_path / "foreign-models"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 2
    assert "invalid choice" in completed.stderr
    assert not list(tmp_path.iterdir())


@pytest.fixture(scope="module")
def executed(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    directory = tmp_path_factory.mktemp("owned-application")
    report = directory / "report.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/model_load_application.py"),
            "run",
            "--root",
            str(ROOT),
            "--report",
            str(report),
        ],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    public = json.loads(completed.stdout)
    assert public["status"] == "bounded_application_capture_transfer_no_new_checker_advantage"
    assert "raw_hex" not in completed.stdout and str(directory) not in completed.stdout
    return report, json.loads(report.read_bytes())


def test_real_application_census_and_matched_baselines(
    executed: tuple[Path, dict[str, Any]],
) -> None:
    _, report = executed
    summary, comparison = report["summary"], report["comparison"]
    assert summary["http_operation_count"] == summary["planned_http_operations"] == 38
    assert summary["loader_calls"] == summary["reconstruction_entries"] == 6
    assert summary["http_failure_count"] == 6
    assert summary["transparency_on_normal_lifecycle"]
    assert summary["native_lifecycle_semantics_checked"]
    assert summary["equal_declared_path_frame"] and summary["different_descriptor_snapshots"]
    assert comparison["reference_counts"] == {"compliant": 3, "violation": 1, "no_new_load": 22}
    assert comparison["actual_in_toto_verifier_calls"] == comparison["decided_load_operations"] == 4
    assert comparison["S_reference_agreement"] == comparison["P_reference_agreement"] == 26
    assert not comparison["comparator_superiority_supported"]


def test_replay_never_deserializes_and_does_not_mutate(
    executed: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    import joblib

    path, report = executed
    before = path.read_bytes()

    def no_deserialization(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("replay must not load a model")

    monkeypatch.setattr(joblib, "load", no_deserialization)
    assert study.verify(ROOT, path) == report
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "mutation",
    [
        "deleted-operation",
        "forged-rule-result",
        "raw-snapshot",
        "metadata",
        "total",
        "code",
        "failed-arm",
        "scope-flags",
    ],
)
def test_rehashed_forgery_is_rejected(
    executed: tuple[Path, dict[str, Any]], tmp_path: Path, mutation: str
) -> None:
    _, original = executed
    report = deepcopy(original)
    captured = report["rows"][1]
    if mutation == "deleted-operation":
        for arm in report["rows"][:2]:
            arm["evidence"]["rows"].pop()
    elif mutation == "forged-rule-result":
        captured["comparators"]["initial-load"]["verification"] = "artifact_rule_mismatch"
    elif mutation == "raw-snapshot":
        captured["evidence"]["reference"][0]["raw_hex"] = b"untrusted".hex()
    elif mutation == "metadata":
        report["plan"]["source"]["commit"] = "0" * 40
    elif mutation == "total":
        captured["evidence"]["counts"]["loader_calls"] = 0
    elif mutation == "code":
        report["plan"]["code_bindings"][study.CODE[0]] = "0" * 64
    elif mutation == "failed-arm":
        captured["status"] = "worker_failed"
    else:
        report["protected_validation_executed"] = True
    report.pop("report_sha256")
    report["report_sha256"] = study.document_digest(report)
    path = tmp_path / "forged.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError):
        study.verify(ROOT, path)


def test_cli_replay_and_immutable_destination(executed: tuple[Path, dict[str, Any]]) -> None:
    path, _ = executed
    before = path.read_bytes()
    base = [sys.executable, str(ROOT / "scripts/model_load_application.py")]
    verified = subprocess.run(
        [*base, "verify", "--root", str(ROOT), "--report", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr
    refused = subprocess.run(
        [*base, "run", "--root", str(ROOT), "--report", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert refused.returncode == 1
    assert path.read_bytes() == before
