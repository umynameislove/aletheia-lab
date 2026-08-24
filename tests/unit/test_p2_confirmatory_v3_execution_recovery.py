"""Fail-closed execution behavior for recovered v3 calibration."""

from __future__ import annotations

import pandas as pd
import pytest

import aletheia_lab.benchmark.p2.confirmatory_v3_execution as execution_module
from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    load_v3_dataset_binding_manifest,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_execution import (
    ExecutionPlan,
    execute_v3_dataset_fail_closed,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    load_v3_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    CalibrationAbstention,
    ModelCalibrationAbstention,
    ModelCalibrationAbstentionSignal,
    V3RuntimeError,
)


def test_dataset_execution_converts_only_calibration_failure_to_abstention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol = load_v3_confirmatory_protocol()
    dataset = load_v3_dataset_binding_manifest().datasets[0]
    calibration = CalibrationAbstention(
        reason_code="iteration_budget_exhausted",
        iterations=100,
        gradient_infinity_norm=2e-8,
        objective_mean=0.4,
        development_record_count=6000,
    )
    expected = ModelCalibrationAbstention(
        protocol_sha256=protocol.canonical_sha256(),
        dataset_id=dataset.dataset_id,
        dataset_role=dataset.role,
        model_kind="logistic_regression",
        training_role="reciprocal-yes_to_no-0.1-6103",
        training_targets_sha256="a" * 64,
        sample_weights_sha256=None,
        calibration_abstention=calibration,
    )

    def abstain(**_kwargs: object) -> None:
        raise ModelCalibrationAbstentionSignal(expected)

    monkeypatch.setattr(execution_module, "execute_v3_dataset", abstain)
    attempt = execute_v3_dataset_fail_closed(
        protocol=protocol,
        dataset=dataset,
        frame=pd.DataFrame(),
        plan=ExecutionPlan.synthetic(corruption_seeds=(6101,), environment_seeds=(7101,)),
    )
    assert attempt == expected
    assert not attempt.predictive_metrics_generated
    assert not attempt.partial_model_reusable


def test_dataset_execution_does_not_hide_non_calibration_defects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol = load_v3_confirmatory_protocol()
    dataset = load_v3_dataset_binding_manifest().datasets[0]

    def fail(**_kwargs: object) -> None:
        raise V3RuntimeError("provenance mismatch")

    monkeypatch.setattr(execution_module, "execute_v3_dataset", fail)
    with pytest.raises(V3RuntimeError, match="provenance mismatch"):
        execute_v3_dataset_fail_closed(
            protocol=protocol,
            dataset=dataset,
            frame=pd.DataFrame(),
            plan=ExecutionPlan.synthetic(
                corruption_seeds=(6101,), environment_seeds=(7101,)
            ),
        )
