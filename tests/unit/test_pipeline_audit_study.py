"""Prospective matrix and retained parent/worker evidence cannot be relabeled."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from aletheia_lab.evaluation.calibration_audit_study import read, sealed
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.pipeline_audit_study import (
    configurations,
    prepare,
    run,
    storage_bindings,
    verify,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def study(tmp_path_factory):
    native = pytest.importorskip("sklearn")
    if native.__version__ != "1.9.0":
        pytest.skip("scientific source contract pins sklearn 1.9.0")
    directory = tmp_path_factory.mktemp("forward-parent") / "owned"
    prepare(ROOT, directory)
    run(ROOT, directory, "development")
    return directory


def test_development_replay_is_read_only_and_final_matrix_is_fixed(study):
    plan = read(study / "plan.json")
    assert len(configurations(plan)) == 40
    assert plan["quotas"]["pressure"] < plan["charge_qualification"]["last_reserve_32"]
    assert plan["quotas"]["sufficient"] > plan["charge_qualification"]["last_reserve_64"]
    before = storage_bindings(study / "development")
    result = verify(ROOT, study, "development")
    assert result["status"] == "forward_pipeline_read_only_replay_pass"
    assert result["analysis"]["prediction_status_counts"] == {"supported": 18}
    assert storage_bindings(study / "development") == before


@pytest.mark.parametrize(
    "tamper",
    [
        "omit_control",
        "duplicate_transfer",
        "cost_index",
        "timeout",
        "command",
        "input",
        "status",
        "transfer_count",
    ],
)
def test_rehashed_parent_cannot_hide_missing_or_changed_worker_evidence(study, tamper):
    path = study / "development/results.json"
    original = path.read_bytes()
    report = copy.deepcopy(read(path))
    if tamper == "omit_control":
        report["controls"] = []
    elif tamper == "duplicate_transfer":
        report["transfers"] *= 2
    elif tamper == "cost_index":
        report["costs"][0]["index"] = "1"
    elif tamper == "timeout":
        report["costs"][0]["diagnostics"]["timeout_seconds"] += 1
    elif tamper == "command":
        report["costs"][0]["diagnostics"]["command_sha256"] = "f" * 64
    elif tamper == "input":
        report["costs"][0]["quota"] += 1
    elif tamper == "status":
        report["costs"][0]["status"] = "worker_failure"
    else:
        report["transfers"][0]["result"]["cases"][0]["native_top_level_predictions"] += 1
    path.write_text(encode(sealed(report)))
    try:
        with pytest.raises(ValueError):
            verify(ROOT, study, "development")
    finally:
        path.write_bytes(original)
