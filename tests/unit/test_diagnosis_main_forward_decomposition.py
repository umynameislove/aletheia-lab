"""The forward P5 analysis keeps failure, abstention and claim harm distinct."""

from __future__ import annotations

import pytest
from test_diagnosis_main_analysis import _census, _input, _plan

from aletheia_lab.evaluation._diagnosis_main_analysis_contracts import (
    DiagnosisMainAnalysisCensus,
    DiagnosisMainAnalysisInput,
    DiagnosisMainAnalysisPlan,
    DiagnosisMainAnalysisReport,
    DiagnosisMainClaim,
    DiagnosisMainObservedRecord,
)
from aletheia_lab.evaluation.diagnosis_main_analysis import analyse_diagnosis_main
from aletheia_lab.evaluation.diagnosis_main_forward_decomposition import (
    CATEGORIES,
    ForwardDecompositionError,
    _checked_run,
    classify_output,
    decompose_locked_runs,
)

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
    return (
        plan,
        census,
        original_input,
        analyse_diagnosis_main(plan, census, original_input),
        recovery_input,
        analyse_diagnosis_main(plan, census, recovery_input),
    )


def _record(
    *,
    technical_status: str = "success",
    output_status: str | None = "completed",
    labels: tuple[str, ...] = (),
) -> DiagnosisMainObservedRecord:
    claims = tuple(
        DiagnosisMainClaim(
            claim_id=f"claim-{index}",
            claim_type="evidence_statement",
            support_label=label,
            citation_required=False,
            citation_present=False,
            citation_ids_valid=False,
        )
        for index, label in enumerate(labels)
    )
    return DiagnosisMainObservedRecord(
        request_id="request-1",
        request_sha256="a" * 64,
        technical_status=technical_status,
        output_status=output_status,
        claims=claims,
    )


def test_technical_failure_is_not_abstention() -> None:
    failure = _record(technical_status="provider_failure", output_status=None)
    assert classify_output(failure, "full", 0.0) == ("technical_failure", 1.0)
    assert classify_output(failure, "missing_key", 0.5) == ("technical_failure", 1.0)


@pytest.mark.parametrize(
    ("condition", "expected_loss"),
    [("full", 1.0), ("noisy", 1.0), ("missing_key", 0.0), ("counterevidence", 0.0)],
)
def test_actual_abstention_uses_frozen_condition_policy(
    condition: str, expected_loss: float
) -> None:
    record = _record(output_status="abstained")
    assert classify_output(record, condition, 0.0) == ("actual_abstention", expected_loss)


def test_zero_claim_is_not_technical_failure() -> None:
    assert classify_output(_record(), "full", 0.0) == ("zero_claim", 1.0)


def test_partial_support_sensitivity_only_changes_claim_scored_loss() -> None:
    record = _record(labels=("fully_supported", "partially_supported", "unsupported"))
    assert classify_output(record, "full", 0.0) == ("claim_scored", 1 / 3)
    assert classify_output(record, "full", 0.5) == ("claim_scored", 0.5)


def test_two_locked_runs_reconcile_separately_without_pooling(locked_runs: LockedRuns) -> None:
    plan, census, original_input, original_report, recovery_input, recovery_report = locked_runs
    result = decompose_locked_runs(
        plan=plan,
        census=census,
        original_input=original_input,
        original_report=original_report,
        recovery_input=recovery_input,
        recovery_report=recovery_report,
    )

    assert result["status"] == "descriptive_secondary_analysis"
    assert result["no_pooling_or_frozen_endpoint_changes"] is True
    assert result["analysis_plan_sha256"] == plan.plan_sha256
    assert result["census_sha256"] == census.census_sha256
    original = result["original_registered_attempt"]
    recovery = result["forward_technical_recovery"]
    assert original["analysis_input_sha256"] == original_input.input_sha256
    assert recovery["analysis_input_sha256"] == recovery_input.input_sha256
    assert original["report_sha256"] == original_report.report_sha256
    assert recovery["report_sha256"] == recovery_report.report_sha256
    assert original["technical_status_counts"]["parse_failure"] == 1
    assert recovery["technical_status_counts"].get("parse_failure", 0) == 0
    assert original["raw_claim_count"] + 1 == recovery["raw_claim_count"]
    for run, report in ((original, original_report), (recovery, recovery_report)):
        assert run["primary_effect"]["estimate"] == report.primary_effect.estimate
        assert len(run["cells"]) == 32
        assert all(cell["request_count"] == 32 for cell in run["cells"].values())
        assert all(
            sum(cell["category_counts"].values()) == cell["request_count"]
            and set(cell["category_counts"]) == set(CATEGORIES)
            for cell in run["cells"].values()
        )
        assert sum(
            run["B1_minus_A3_primary_loss_contributions"]["primary_three_condition_mean"].values()
        ) == pytest.approx(report.primary_effect.estimate, abs=1e-12)
    assert original["cells"]["full.A3"]["category_counts"]["technical_failure"] == 1
    assert recovery["cells"]["full.A3"]["category_counts"]["technical_failure"] == 0
    assert original["primary_effect"]["estimate"] < recovery["primary_effect"]["estimate"]


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("analysis_plan_sha256", "0" * 64, "frozen census and report"),
        ("status", "invalid_registered_execution", "frozen census and report"),
        ("primary_effect", None, "frozen census and report"),
        ("partial_support_sensitivity_effect", None, "frozen census and report"),
        ("condition_primary_effects", None, "frozen census and report"),
        ("technical_status_counts", {"success": 1024}, "technical status counts"),
        ("raw_claim_count", 0, "claim count"),
    ],
)
def test_decomposition_rejects_inconsistent_report(
    locked_runs: LockedRuns,
    field: str,
    value: object,
    error: str,
) -> None:
    plan, census, original_input, original_report, _, _ = locked_runs
    changed = original_report.model_copy(update={field: value})
    with pytest.raises(ForwardDecompositionError, match=error):
        _checked_run(
            name="original_registered_attempt",
            plan=plan,
            census=census,
            analysis_input=original_input,
            report=changed,
        )


def test_decomposition_rejects_changed_request_binding(locked_runs: LockedRuns) -> None:
    plan, census, original_input, original_report, _, _ = locked_runs
    first = original_input.records[0].model_copy(update={"request_sha256": "0" * 64})
    changed = original_input.model_copy(update={"records": (first, *original_input.records[1:])})
    with pytest.raises(ForwardDecompositionError, match="request identities"):
        _checked_run(
            name="original_registered_attempt",
            plan=plan,
            census=census,
            analysis_input=changed,
            report=original_report,
        )


def test_decomposition_rejects_changed_frozen_endpoint(locked_runs: LockedRuns) -> None:
    plan, census, original_input, original_report, _, _ = locked_runs
    assert original_report.primary_effect is not None
    changed_metric = original_report.primary_effect.model_copy(
        update={"estimate": original_report.primary_effect.estimate + 0.1}
    )
    changed = original_report.model_copy(update={"primary_effect": changed_metric})
    with pytest.raises(ForwardDecompositionError, match="endpoint changed"):
        _checked_run(
            name="original_registered_attempt",
            plan=plan,
            census=census,
            analysis_input=original_input,
            report=changed,
        )
