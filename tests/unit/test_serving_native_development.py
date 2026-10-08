from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation import serving_native_development as native
from aletheia_lab.evaluation.request_model_audit import digest


def test_source_pins_reject_changes_before_execution(tmp_path: Path, monkeypatch):
    source = tmp_path / "source.py"
    source.write_text("raise RuntimeError('must not execute')\n")
    monkeypatch.setattr(native, "PINS", {source.name: file_sha256(source)})
    assert native.checked_sources(tmp_path) == {source.name: source}
    source.write_text("changed")
    with pytest.raises(ValueError, match="pin mismatch"):
        native.checked_sources(tmp_path)


def test_source_symlink_rejected_even_with_matching_bytes(tmp_path: Path, monkeypatch):
    target = tmp_path / "target"
    target.write_text("value")
    linked = tmp_path / "source.py"
    linked.symlink_to(target)
    monkeypatch.setattr(native, "PINS", {linked.name: file_sha256(target)})
    with pytest.raises(ValueError, match="symbolic"):
        native.checked_sources(tmp_path)


def test_method_selection_preserves_original_body_and_census(tmp_path: Path, monkeypatch):
    source = tmp_path / "source.py"
    source.write_text(
        "class Worker:\n    def value(self, x: UndefinedType):\n        return x + 17\n"
    )
    before = source.read_bytes()
    monkeypatch.setattr(native, "PINS", {source.name: file_sha256(source)})
    selected = native._methods(source, "Worker", ("value",), {})
    assert selected().value(5) == 22
    assert source.read_bytes() == before
    with pytest.raises(ValueError, match="census"):
        native._methods(source, "Worker", ("missing",), {})
    source.write_text("raise RuntimeError('must not execute')")
    with pytest.raises(ValueError, match="pin mismatch before execution"):
        native._methods(source, "Worker", ("value",), {})


def test_unpinned_source_cannot_be_executed_directly(tmp_path: Path):
    source = tmp_path / "unapproved.py"
    source.write_text("raise RuntimeError('must not execute')")
    with pytest.raises(ValueError, match="pinned regular"):
        native._source_module(source, "unapproved")


def test_runtime_failure_remains_in_six_condition_census(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(native, "checked_sources", lambda root: {})
    monkeypatch.setattr(native.importlib.metadata, "version", lambda name: "test")

    async def registry(paths, side):
        if side == "affected":
            raise RuntimeError("failed attempt retained")
        return {"family": "test"}

    async def settings(paths, output, side, helper):
        return {"family": "test"}

    monkeypatch.setattr(native, "_registry_case", registry)
    monkeypatch.setattr(native, "_settings_case", settings)
    output = tmp_path / "run"
    report = asyncio.run(native.run_development(tmp_path, output))
    assert report["planned_conditions"] == 6 and report["completed_conditions"] == 5
    assert report["rows"][0]["status"] == "technical_failure"
    assert len(report["rows"]) == 6
    body = {key: value for key, value in report.items() if key != "report_sha256"}
    assert report["report_sha256"] == digest(body)
    before = (output / "results.json").read_bytes()
    with pytest.raises(FileExistsError):
        asyncio.run(native.run_development(tmp_path, output))
    assert (output / "results.json").read_bytes() == before


def test_rehashed_census_change_cannot_hide_failure(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(native, "checked_sources", lambda root: {})
    rows = [
        {"side": side, "condition": condition, "status": "technical_failure"}
        for side in ("affected", "fixed")
        for condition in ("registry", "top", "helper")
    ]
    body = {
        "schema": "serving-native-development/v1",
        "source_file_sha256": native.PINS,
        "planned_conditions": 6,
        "completed_conditions": 0,
        "rows": rows,
    }
    path = tmp_path / "results.json"
    path.write_text(json.dumps({**body, "report_sha256": digest(body)}))
    before = path.read_bytes()
    result = native.verify_development(tmp_path, tmp_path)
    assert result["status"] == "development_incomplete"
    assert path.read_bytes() == before
    body["completed_conditions"] = 6
    path.write_text(json.dumps({**body, "report_sha256": digest(body)}))
    with pytest.raises(ValueError, match="failure census"):
        native.verify_development(tmp_path, tmp_path)


def test_fixed_top_module_does_not_imply_dependency_repair(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(native, "artifact_closure", lambda path: {"test": True})
    monkeypatch.setattr(native, "verify_local", lambda *args: None)
    row = {
        "family": "MLServer#705",
        "side": "fixed",
        "condition": "helper",
        "helper_dependency": True,
        "initial_output": 1,
        "expected_after_update": 10,
        "after_update_output": 1,
        "component_repair_output": 1,
        "closure_repair_output": 10,
        "integrated_baseline_verdict": "noncompliant",
        "candidate_same_witness_verdict": "noncompliant",
        "artifact_only_actual_use_verdict": "unknown",
        "official_disk_signature_valid": True,
        "signed_closure": {"test": True},
    }
    native._verify_settings_row(row, tmp_path)
    row["integrated_baseline_verdict"] = "compliant"
    with pytest.raises(ValueError, match="query differs"):
        native._verify_settings_row(row, tmp_path)
