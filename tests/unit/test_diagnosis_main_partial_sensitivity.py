"""The P5 partial-weight curve is a secondary reweighting, not a new run."""

from __future__ import annotations

import json
from typing import Any

import pytest
from test_diagnosis_main_analysis import _census, _input, _plan

from aletheia_lab.evaluation._diagnosis_main_analysis_contracts import (
    DiagnosisMainAnalysisCensus,
    DiagnosisMainAnalysisInput,
    DiagnosisMainAnalysisPlan,
    DiagnosisMainAnalysisReport,
)
from aletheia_lab.evaluation.diagnosis_main_analysis import analyse_diagnosis_main
from aletheia_lab.evaluation.diagnosis_main_forward_decomposition import ForwardDecompositionError
from aletheia_lab.evaluation.diagnosis_main_partial_sensitivity import (
    PartialSensitivityError,
    _curve_rows,
    analyse_partial_support_curve,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256

LockedRuns = tuple[
    DiagnosisMainAnalysisPlan,
    DiagnosisMainAnalysisCensus,
    DiagnosisMainAnalysisInput,
    DiagnosisMainAnalysisReport,
    DiagnosisMainAnalysisInput,
    DiagnosisMainAnalysisReport,
]


@pytest.fixture(scope="module")
def locked_runs() -> LockedRuns:
    plan = _plan()
    census = _census()
    original_input = _input(plan, census, fail_first_a3_full=True)
    recovery_input = _input(plan, census)
    records = []
    b1_ids = {request.request_id for request in census.requests if request.variant == "B1"}
    for record in recovery_input.records:
        if record.request_id in b1_ids and record.claims:
            claims = tuple(
                claim.model_copy(update={"support_label": "partially_supported"})
                for claim in record.claims
            )
            record = record.model_copy(update={"claims": claims})
        records.append(record.model_dump(mode="json"))
    payload = recovery_input.model_dump(mode="json")
    payload["records"] = records
    payload["input_sha256"] = canonical_execution_sha256(
        {key: value for key, value in payload.items() if key != "input_sha256"}
    )
    recovery_input = DiagnosisMainAnalysisInput.model_validate_json(json.dumps(payload))
    return (
        plan,
        census,
        original_input,
        analyse_diagnosis_main(plan, census, original_input),
        recovery_input,
        analyse_diagnosis_main(plan, census, recovery_input),
    )


def _curve(runs: LockedRuns) -> dict[str, Any]:
    plan, census, original_input, original_report, recovery_input, recovery_report = runs
    return analyse_partial_support_curve(
        plan=plan,
        census=census,
        original_input=original_input,
        original_report=original_report,
        recovery_input=recovery_input,
        recovery_report=recovery_report,
    )


def test_curve_reconciles_anchors_and_is_affine(locked_runs: LockedRuns) -> None:
    result = _curve(locked_runs)
    report = locked_runs[-1]
    assert report.primary_effect is not None
    assert report.partial_support_sensitivity_effect is not None
    assert result["provider_calls_executed"] is False
    assert result["historical_reports_mutated"] is False
    assert result["family_count"] == 32
    assert result["scored_B1_A3_core_request_count"] == 192
    assert len(result["curve"]) == 11
    assert result["curve"][0]["B1_minus_A3"] == pytest.approx(report.primary_effect.estimate)
    assert result["curve"][5]["B1_minus_A3"] == pytest.approx(
        report.partial_support_sensitivity_effect.estimate
    )
    intercept = result["effect_intercept_at_zero"]
    slope = result["effect_slope_per_unit_partial_harm"]
    assert slope > 0
    for row in result["curve"]:
        assert row["B1_minus_A3"] == pytest.approx(
            intercept + row["partial_harm_weight"] * slope, abs=1e-12
        )
    assert "family-01" not in json.dumps(result)
    assert "claim-1" not in json.dumps(result)


def test_deletion_ranges_use_families_and_source_groups() -> None:
    curve = _curve_rows(
        {"a": (0.2, 0.3), "b": (-0.2, -0.1), "c": (0.4, 0.0)},
        {"a": "source-x", "b": "source-x", "c": "source-y"},
    )
    assert curve[0]["B1_minus_A3"] == pytest.approx(0.4 / 3)
    assert curve[0]["leave_one_family_out_range"] == pytest.approx([0.0, 0.3])
    assert curve[0]["leave_one_superfamily_out_range"] == pytest.approx([0.0, 0.4])
    assert curve[10]["B1_minus_A3"] == pytest.approx(0.2)
    assert curve[10]["leave_one_family_out_range"] == pytest.approx([0.05, 0.45])
    assert curve[10]["leave_one_superfamily_out_range"] == pytest.approx([0.1, 0.4])


def test_changed_historical_endpoint_fails_closed(locked_runs: LockedRuns) -> None:
    plan, census, original_input, original_report, recovery_input, recovery_report = locked_runs
    assert recovery_report.primary_effect is not None
    changed_effect = recovery_report.primary_effect.model_copy(
        update={"estimate": recovery_report.primary_effect.estimate + 0.1}
    )
    changed_report = recovery_report.model_copy(update={"primary_effect": changed_effect})
    with pytest.raises(ForwardDecompositionError, match="endpoint changed"):
        analyse_partial_support_curve(
            plan=plan,
            census=census,
            original_input=original_input,
            original_report=original_report,
            recovery_input=recovery_input,
            recovery_report=changed_report,
        )


def test_changed_anchor_weight_is_not_reinterpreted(locked_runs: LockedRuns) -> None:
    plan, census, original_input, original_report, recovery_input, recovery_report = locked_runs
    with pytest.raises(PartialSensitivityError, match="anchor weights changed"):
        analyse_partial_support_curve(
            plan=plan.model_copy(update={"partial_support_sensitivity_weight": 0.6}),
            census=census,
            original_input=original_input,
            original_report=original_report,
            recovery_input=recovery_input,
            recovery_report=recovery_report,
        )
