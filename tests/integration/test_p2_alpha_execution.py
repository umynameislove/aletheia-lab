"""Real-data integration gate for the complete frozen P2 alpha execution."""

from __future__ import annotations

from pathlib import Path

import pytest

from aletheia_lab.benchmark.p2.alpha_execution import (
    execute_alpha_slot,
    execute_primary_alpha,
    prepare_alpha_runtime,
)
from aletheia_lab.benchmark.p2.artifacts import load_contract_store, save_contract_store
from aletheia_lab.benchmark.p2.data_drift import DriftMetricComparison

_PROCESSED = Path("data/processed/telco_customer_churn.csv")


@pytest.mark.skipif(not _PROCESSED.exists(), reason="processed Telco split is not present")
def test_real_primary_alpha_executes_validates_and_round_trips(tmp_path: Path) -> None:
    runtime = prepare_alpha_runtime()

    assert len(runtime.plan.slots) == 24
    assert sum(slot.slot_kind == "primary" for slot in runtime.plan.slots) == 15
    assert runtime.plan.slots[4].identity.canonical_intervention_parameters.target_distribution == (
        runtime.drift_source.observed_distribution()
    )

    first_drift = next(slot for slot in runtime.plan.slots if slot.slot_id == "M1-F1")
    measured_drift = execute_alpha_slot(first_drift, runtime)
    assert isinstance(measured_drift.comparison, DriftMetricComparison)
    assert measured_drift.comparison.measured_primary_outcome == "regression"

    artifacts = execute_primary_alpha(runtime)
    assert artifacts.report.executed == 15
    assert artifacts.report.technically_valid == 15
    assert artifacts.report.technical_rejected == 0
    assert artifacts.report.accepted == 15
    assert artifacts.report.mechanism_coverage_passed is False
    assert artifacts.report.gate_status == "fail"
    assert "label_noise=no_eligible_failure" in (artifacts.report.deviation_note or "")
    assert artifacts.report.context_count == len(artifacts.contexts.entries)

    manifest = save_contract_store(artifacts, tmp_path / "alpha")
    loaded = load_contract_store(tmp_path / "alpha")
    assert manifest.artifact_count == 9
    assert loaded.artifacts == artifacts
