"""Aggregate-only P5 specification curve for the partial-claim harm weight.

This outcome-aware, secondary analysis leaves the frozen reports untouched. It
varies one scoring assumption, not the data, judge, diagnosis, or estimand.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, TypedDict

from aletheia_lab.evaluation._diagnosis_main_analysis_contracts import (
    CORE_CONDITIONS,
    DiagnosisMainAnalysisCensus,
    DiagnosisMainAnalysisInput,
    DiagnosisMainAnalysisPlan,
    DiagnosisMainAnalysisReport,
)
from aletheia_lab.evaluation.diagnosis_main_forward_decomposition import (
    classify_output,
    decompose_locked_runs,
)


class PartialSensitivityError(ValueError):
    """The frozen P5 inputs cannot support this secondary comparison."""


class CurveRow(TypedDict):
    partial_harm_weight: float
    B1_minus_A3: float
    leave_one_family_out_range: list[float]
    leave_one_superfamily_out_range: list[float]
    positive_family_fraction: float


def _mean(values: list[float]) -> float:
    if not values:
        raise PartialSensitivityError("an expected paired cell is empty")
    return math.fsum(values) / len(values)


def _range(values: list[float]) -> list[float]:
    if not values:
        raise PartialSensitivityError("a deletion removed every family")
    return [min(values), max(values)]


def _family_coefficients(
    census: DiagnosisMainAnalysisCensus, recovery_input: DiagnosisMainAnalysisInput
) -> tuple[dict[str, tuple[float, float]], dict[str, str], int]:
    """Keep frozen output aggregation while expressing each family as intercept+slope."""

    contexts = {context.context_id: context for context in census.contexts}
    records = {record.request_id: record for record in recovery_input.records}
    grouped: dict[tuple[str, str, str], list[tuple[float, float]]] = defaultdict(list)
    for request in census.requests:
        context = contexts[request.context_id]
        if request.variant not in {"B1", "A3"} or context.evidence_condition not in CORE_CONDITIONS:
            continue
        record = records[request.request_id]
        _, at_zero = classify_output(record, context.evidence_condition, 0.0)
        _, at_one = classify_output(record, context.evidence_condition, 1.0)
        grouped[(context.case_family_id, context.evidence_condition, request.variant)].append(
            (at_zero, at_one - at_zero)
        )

    coefficients: dict[str, tuple[float, float]] = {}
    superfamilies = {family.family_id: family.superfamily_id for family in census.families}
    for family_id in sorted(superfamilies):
        intercepts: list[float] = []
        slopes: list[float] = []
        for condition in CORE_CONDITIONS:
            b1 = grouped[(family_id, condition, "B1")]
            a3 = grouped[(family_id, condition, "A3")]
            intercepts.append(_mean([pair[0] for pair in b1]) - _mean([pair[0] for pair in a3]))
            slopes.append(_mean([pair[1] for pair in b1]) - _mean([pair[1] for pair in a3]))
        coefficients[family_id] = (_mean(intercepts), _mean(slopes))
    if len(grouped) != len(coefficients) * len(CORE_CONDITIONS) * 2:
        raise PartialSensitivityError("the paired family-condition census is incomplete")
    return coefficients, superfamilies, sum(map(len, grouped.values()))


def _estimate(
    coefficients: dict[str, tuple[float, float]], weight: float, family_ids: list[str]
) -> float:
    return _mean(
        [
            coefficients[family_id][0] + weight * coefficients[family_id][1]
            for family_id in family_ids
        ]
    )


def _curve_rows(
    coefficients: dict[str, tuple[float, float]], superfamilies: dict[str, str]
) -> list[CurveRow]:
    family_ids = sorted(coefficients)
    superfamily_ids = sorted(set(superfamilies.values()))
    curve: list[CurveRow] = []
    for tenth in range(11):
        weight = tenth / 10
        effects = [
            coefficients[family_id][0] + weight * coefficients[family_id][1]
            for family_id in family_ids
        ]
        curve.append(
            {
                "partial_harm_weight": weight,
                "B1_minus_A3": _estimate(coefficients, weight, family_ids),
                "leave_one_family_out_range": _range(
                    [
                        _estimate(
                            coefficients,
                            weight,
                            [other for other in family_ids if other != family_id],
                        )
                        for family_id in family_ids
                    ]
                ),
                "leave_one_superfamily_out_range": _range(
                    [
                        _estimate(
                            coefficients,
                            weight,
                            [family for family in family_ids if superfamilies[family] != group],
                        )
                        for group in superfamily_ids
                    ]
                ),
                "positive_family_fraction": sum(effect > 0 for effect in effects) / len(effects),
            }
        )
    return curve


def analyse_partial_support_curve(
    *,
    plan: DiagnosisMainAnalysisPlan,
    census: DiagnosisMainAnalysisCensus,
    original_input: DiagnosisMainAnalysisInput,
    original_report: DiagnosisMainAnalysisReport,
    recovery_input: DiagnosisMainAnalysisInput,
    recovery_report: DiagnosisMainAnalysisReport,
) -> dict[str, Any]:
    """Reconcile both historical runs and reweight only recovery partial claims."""

    if plan.partial_support_primary_weight != 0.0 or plan.partial_support_sensitivity_weight != 0.5:
        raise PartialSensitivityError("the two historical anchor weights changed")
    decompose_locked_runs(
        plan=plan,
        census=census,
        original_input=original_input,
        original_report=original_report,
        recovery_input=recovery_input,
        recovery_report=recovery_report,
    )
    primary = recovery_report.primary_effect
    half = recovery_report.partial_support_sensitivity_effect
    if primary is None or half is None:
        raise PartialSensitivityError("a historical effect is missing")

    coefficients, superfamilies, scored_request_count = _family_coefficients(census, recovery_input)
    family_ids = sorted(coefficients)
    if not math.isclose(
        _estimate(coefficients, 0.0, family_ids), primary.estimate, rel_tol=0, abs_tol=1e-12
    ):
        raise PartialSensitivityError("the primary endpoint did not reconcile")
    if not math.isclose(
        _estimate(coefficients, 0.5, family_ids), half.estimate, rel_tol=0, abs_tol=1e-12
    ):
        raise PartialSensitivityError("the prespecified half-weight endpoint did not reconcile")
    curve = _curve_rows(coefficients, superfamilies)

    return {
        "schema_version": "diagnosis-main-partial-specification-curve/v1",
        "status": "descriptive_secondary_analysis",
        "scientific_scope": "finite_frozen_benchmark_assumption_sensitivity_only",
        "analysis_plan_sha256": plan.plan_sha256,
        "census_sha256": census.census_sha256,
        "original_input_sha256": original_input.input_sha256,
        "original_report_sha256": original_report.report_sha256,
        "recovery_input_sha256": recovery_input.input_sha256,
        "recovery_report_sha256": recovery_report.report_sha256,
        "family_count": len(family_ids),
        "superfamily_count": len(set(superfamilies.values())),
        "scored_B1_A3_core_request_count": scored_request_count,
        "historical_primary": primary.model_dump(mode="json"),
        "historical_prespecified_half": half.model_dump(mode="json"),
        "effect_intercept_at_zero": curve[0]["B1_minus_A3"],
        "effect_slope_per_unit_partial_harm": _estimate(coefficients, 1.0, family_ids)
        - _estimate(coefficients, 0.0, family_ids),
        "curve": curve,
        "all_weights_point_estimate_negative": all(row["B1_minus_A3"] < 0 for row in curve),
        "all_weights_superfamily_deletion_negative": all(
            row["leave_one_superfamily_out_range"][1] < 0 for row in curve
        ),
        "interpretation": [
            "Only partial-claim harm weight changes; technical failures remain loss one.",
            "The historical primary and half-weight intervals remain authoritative anchors.",
            "Deletion ranges are deterministic finite-census diagnostics, not confidence intervals.",
            "This post-outcome curve does not authorize selection of a new primary weight,",
            "provide population generalization, or correct automatic-label errors.",
        ],
        "provider_calls_executed": False,
        "historical_reports_mutated": False,
    }
