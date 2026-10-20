"""Local smoke and publication guards, not new held-out scientific samples."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.ground_truth_study import run_source, study_design

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/p2_ground_truth_validity.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_native_iris_controls_execute_repair_and_preserve_full_payload_twins() -> None:
    report = run_source("iris", phase="development", seeds=(31001,))
    assert not report["failures"] and len(report["records"]) == 7
    records = {row["case"]: row for row in report["records"]}
    a, b = records["input_sign"], records["resident_sign"]
    assert a["prediction_payload"] == b["prediction_payload"]
    assert b["exact_probability_buffer_equal_to_input_sign"]
    assert a["lineage_payload"] != b["lineage_payload"]
    assert a["harmful_loss_delta"] > 0.01 and a["repair_residual"] == 0
    assert a["views"]["prediction_only"]["status"] == "ambiguous"
    assert a["views"]["configured_pipeline_lineage"]["status"] == "supported_in_catalogue"
    assert records["wrong_repair"]["repair_residual"] > 0.01
    assert records["not_applied"]["views"]["prediction_only"]["status"] == "invalid_intervention"
    assert records["double_sign"]["fidelity_verified"]
    assert records["double_sign"]["harmful_loss_delta"] == 0
    assert all(abs(row["standardized_loss_delta"]) < 1e-12 for row in a["prior_nuisance_checks"])
    assert all(
        "case" not in a[key] and "fidelity_verified" not in a[key]
        for key in ("prediction_payload", "lineage_payload")
    )


def test_source_phase_mismatch_rejected_before_fitting() -> None:
    with pytest.raises(ValueError):
        run_source("wine", phase="development")


def test_design_record_precedes_execution_and_refuses_overwrite(tmp_path: Path) -> None:
    destination = tmp_path / "nested" / "design.json"
    first = _run("design", "--output", str(destination))
    assert first.returncode == 0, first.stderr
    before = destination.read_bytes()
    payload = json.loads(before)
    assert payload["design_sha256"] == canonical_sha256(study_design())
    assert payload["phase"] == "design"
    assert payload["historical_registered_runs_executed"] is False
    assert set(payload["code_sha256"]) == {"study", "validity", "entrypoint"}
    repeated = _run("design", "--output", str(destination))
    assert repeated.returncode != 0
    assert destination.read_bytes() == before


def test_transfer_refuses_missing_or_tampered_code_seal_before_sources(tmp_path: Path) -> None:
    digest = canonical_sha256(study_design())
    missing = _run("transfer", "--expected-design-sha256", digest)
    assert missing.returncode != 0 and "saved design" in missing.stderr
    path = tmp_path / "design.json"
    assert _run("design", "--output", str(path)).returncode == 0
    payload = json.loads(path.read_bytes())
    payload["code_sha256"]["study"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    rejected = _run("transfer", "--expected-design-sha256", digest, "--seal", str(path))
    assert rejected.returncode != 0 and "differs" in rejected.stderr
    assert (
        _run("transfer", "--expected-design-sha256", "0" * 64, "--seal", str(path)).returncode != 0
    )


def test_analysis_interval_is_simulation_rate_not_native_source_ci() -> None:
    spec = importlib.util.spec_from_file_location(
        "ground_truth_analysis", ROOT / "scripts/analyze_p2_ground_truth_validity.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    low, high = module.wilson(5, 100)
    assert 0.021 < low < 0.022 and 0.111 < high < 0.113
    assert module.wilson(100, 100)[0] < 1.0


def test_replay_rejects_changed_payload_gate_and_unbound_effect() -> None:
    spec = importlib.util.spec_from_file_location(
        "ground_truth_analysis", ROOT / "scripts/analyze_p2_ground_truth_validity.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    records = run_source("iris", phase="development", seeds=(31001,))["records"]
    original = next(row for row in records if row["case"] == "input_sign")
    module._check_record(original, records)
    payload_tamper = copy.deepcopy(original)
    payload_tamper["lineage_payload"]["actual_model"] = "0" * 64
    with pytest.raises(ValueError, match="digest"):
        module._check_record(payload_tamper, records)
    effect_tamper = copy.deepcopy(original)
    effect_tamper["harmful_loss_delta"] += 1
    with pytest.raises(ValueError, match="effect"):
        module._check_record(effect_tamper, records)
    gate_tamper = copy.deepcopy(original)
    gate_tamper["views"]["prediction_only"]["status"] = "supported_in_catalogue"
    with pytest.raises(ValueError, match="decision"):
        module._check_record(gate_tamper, records)
    twin_tamper = copy.deepcopy(original)
    twin_tamper["exact_probability_buffer_equal_to_input_sign"] = False
    with pytest.raises(ValueError, match="probability-buffer equality"):
        module._check_record(twin_tamper, records)
