"""Prospective design, immutable bindings and offline runtime contracts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from aletheia_lab.evaluation import cache_lifecycle_study as study
from aletheia_lab.evaluation.cache_lifecycle_analysis import aggregate
from aletheia_lab.evaluation.cache_lifecycle_source import run_cell
from aletheia_lab.evaluation.cache_lifecycle_study import (
    FILES,
    check_bindings,
    design,
    environment,
    read_sealed,
    seal,
)

ROOT = Path(__file__).resolve().parents[2]


def identity() -> dict:
    return {"packages": {"aiohttp": "3.14.3", "cachetools": "6.2.6", "in-toto": "3.0.0"}}


def test_design_is_deterministic_and_outcome_free() -> None:
    first = design(ROOT, identity())
    assert first == design(ROOT, identity())
    assert len(first["cells"]) == 48
    assert first["planned_inferences"] == 3456
    assert first["planned_reload_operations"] == 96
    assert first["protocol"]["provider_calls"] == 0
    assert first["protocol"]["protected_runs"] == 0
    check_bindings(ROOT, first)
    assert set(first["bindings"]) == set(FILES)


@pytest.mark.parametrize("package", ["aiohttp", "cachetools"])
def test_native_version_drift_rejected(package: str) -> None:
    value = identity()
    value["packages"][package] = "0.0"
    with pytest.raises(ValueError, match="native versions"):
        design(ROOT, value)


def test_sealed_document_detects_changed_prediction(tmp_path: Path) -> None:
    value = seal({"prediction": 0})
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(value))
    assert read_sealed(path) == value
    value["prediction"] = 1
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="digest differs"):
        read_sealed(path)


def test_child_does_not_inherit_credentials_or_python_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-not-a-key")
    monkeypatch.setenv("PYTHONPATH", "untrusted")
    monkeypatch.setenv("HTTPS_PROXY", "untrusted")
    result = environment(ROOT, Path("/native"))
    assert "OPENAI_API_KEY" not in result and "HTTPS_PROXY" not in result
    assert "untrusted" not in result["PYTHONPATH"]


def test_worker_rejects_unbounded_config_before_sdk_import(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="fixed owned"):
        run_cell(
            {"arm": "isolated", "order": "old_first", "evidence": "full", "steady_requests": 1},
            tmp_path / "cell",
        )
    assert not (tmp_path / "cell").exists()


def test_prediction_counts_include_reverse_return_substitution() -> None:
    predictions = design(ROOT, identity())["protocol"]["predictions"]["violation_counts"]
    assert predictions["clear"] == {"old_first": 2, "new_first": 1}
    assert predictions["input_key"] == {"old_first": 3, "new_first": 2}
    assert (
        predictions["isolated"] == predictions["generation_key"] == {"old_first": 0, "new_first": 0}
    )


@pytest.mark.parametrize("field", ["cells", "protocol", "planned_inferences"])
def test_even_rehashed_plan_cannot_change_canonical_design(field: str) -> None:
    value = design(ROOT, identity())
    value.pop("sha256")
    if field == "cells":
        value[field] = value[field][:-1]
    elif field == "protocol":
        value[field]["predictions"]["violation_counts"]["clear"]["old_first"] = 0
    else:
        value[field] = 1
    with pytest.raises(ValueError, match="canonical design"):
        check_bindings(ROOT, seal(value))


def test_worker_launch_failure_is_preserved_not_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(*args, **kwargs):
        raise FileNotFoundError("private executable path must not be printed")

    monkeypatch.setattr(study.subprocess, "run", missing)
    result = study._execute(ROOT, tmp_path, 0, {}, Path("missing"), tmp_path, 1)
    assert result["index"] == 0 and result["returncode"] == -2
    assert result["source_sha256"] is None
    assert (tmp_path / "cell-000/stderr.log").read_bytes() == b"FileNotFoundError"


def test_additive_closeout_never_changes_primary_cost_or_service() -> None:
    old = {
        key: 0
        for key in (
            "truth_counts",
            "violated_tokens",
            "compute_count",
            "forecasts",
            "measurements",
            "complete",
            "offered_inferences",
            "offered_reloads",
            "steady_latency_median_ns",
            "physical_bytes_live",
            "physical_bytes_closed",
            "logical_bytes",
            "commit_count",
            "signed_status",
        )
    }
    old["service"] = {"correct": 72, "false": 0, "unknown_or_unserved": 0, "records": 10}
    current = deepcopy(old)
    current["service"]["conditional_native_correct"] = 0
    study._unchanged_primary(current, old)
    current["physical_bytes_closed"] = 1
    with pytest.raises(ValueError, match="primary outcome or cost"):
        study._unchanged_primary(current, old)
    current["physical_bytes_closed"] = 0
    current["service"]["correct"] = 71
    with pytest.raises(ValueError, match="materialized service"):
        study._unchanged_primary(current, old)


@pytest.mark.parametrize("helper_changed", [False, True])
def test_cli_archived_replay_preserves_failed_census_and_rejects_helper_drift(
    tmp_path: Path, helper_changed: bool
) -> None:
    snapshot = tmp_path / "snapshot"
    for name in FILES:
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    if helper_changed:
        target = snapshot / "src/aletheia_lab/evaluation/model_load_retention.py"
        target.write_bytes(target.read_bytes() + b"\n")
    plan = design(snapshot, identity())
    executions = [
        {"index": index, "config": cell, "returncode": -2, "source_sha256": None}
        for index, cell in enumerate(plan["cells"])
    ]
    findings = [None] * len(executions)
    report = seal(
        {
            "schema": "cache-lifecycle-results/v1",
            "plan_sha256": plan["sha256"],
            "executions": executions,
            "findings": findings,
            "analysis": aggregate(plan, executions, findings),
        }
    )
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    (tmp_path / "results.json").write_text(json.dumps(report))
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/cache_lifecycle_validation.py"),
            "verify",
            "--root",
            str(snapshot),
            "--study-dir",
            str(tmp_path),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    value = json.loads(result.stdout)
    assert str(tmp_path) not in result.stdout
    if helper_changed:
        assert result.returncode == 1
        assert value == {"status": "cache_lifecycle_failed_closed", "error_type": "ValueError"}
    else:
        assert result.returncode == 0, result.stderr
        assert value["verification"] == "pass"
        assert value["analysis"]["failed_or_incomplete_cells"] == list(range(48))
        assert (
            sum(g["unknown_or_unserved"] for g in value["analysis"]["by_evidence"].values()) == 3456
        )
