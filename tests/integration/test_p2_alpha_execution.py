"""Real-data integration gate for the complete frozen P2 alpha execution."""

from __future__ import annotations

from pathlib import Path

import pytest

from aletheia_lab.benchmark.p2.alpha_execution import (
    execute_alpha_slot,
    execute_primary_alpha,
    prepare_alpha_runtime,
)
from aletheia_lab.benchmark.p2.alpha_recovery import (
    ReserveRecoveryError,
    execute_reserve_recovery,
    validate_reserve_recovery_pair,
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

    with pytest.raises(ReserveRecoveryError, match="source store hash"):
        execute_reserve_recovery(
            runtime=runtime,
            source=loaded.artifacts,
            source_store_sha256="f" * 64,
        )

    recovered = execute_reserve_recovery(
        runtime=runtime,
        source=loaded.artifacts,
        source_store_sha256=loaded.manifest.store_sha256,
    )
    recovered.validate()
    recovery = recovered.execution.reserve_recovery_authorization
    assert recovery is not None
    assert recovery.source_store_sha256 == loaded.manifest.store_sha256
    assert recovery.activated_reserve_slot_ids == ("M2-R1", "M2-R2", "M2-R3")
    assert recovered.report.executed == 18
    assert recovered.report.activated_reserve == 3
    assert recovered.report.accepted == 15
    assert recovered.report.excluded_valid == 3
    assert recovered.report.context_count == len(recovered.contexts.entries)
    assert recovered.report.mechanism_coverage_passed is False
    assert recovered.report.gate_status == "fail"
    assert "label_noise=no_eligible_failure" in (recovered.report.deviation_note or "")
    reserve_outcomes = {
        next(
            execution.slot_id
            for execution in recovered.execution.executed
            if execution.candidate_id == classification.candidate_id
        ): classification
        for classification in recovered.classifications.entries
        if classification.candidate_id
        in {
            execution.candidate_id
            for execution in recovered.execution.executed
            if execution.slot_kind == "reserve"
        }
    }
    assert set(reserve_outcomes) == {"M2-R1", "M2-R2", "M2-R3"}
    assert all(item.measured_outcome == "stable" for item in reserve_outcomes.values())
    assert reserve_outcomes["M2-R3"].delta == pytest.approx(-0.0066225165562913135)
    assert tuple(
        item
        for item in recovered.classifications.entries
        if item.candidate_id
        in {
            execution.candidate_id
            for execution in recovered.execution.executed
            if execution.slot_kind == "primary"
        }
    ) == artifacts.classifications.entries

    recovery_manifest = save_contract_store(recovered, tmp_path / "alpha-recovery")
    recovered_loaded = load_contract_store(tmp_path / "alpha-recovery")
    assert recovery_manifest.artifact_count == 9
    assert recovered_loaded.artifacts == recovered
    validate_reserve_recovery_pair(
        source=loaded.artifacts,
        source_store_sha256=loaded.manifest.store_sha256,
        recovered=recovered_loaded.artifacts,
    )

    with pytest.raises(ReserveRecoveryError, match="source store hash"):
        validate_reserve_recovery_pair(
            source=loaded.artifacts,
            source_store_sha256="f" * 64,
            recovered=recovered_loaded.artifacts,
        )
