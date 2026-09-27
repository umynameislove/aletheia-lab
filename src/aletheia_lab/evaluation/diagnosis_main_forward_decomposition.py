"""Descriptive, outcome-aware decomposition of the already-locked P5 runs.

This is a forward secondary analysis. It never replaces the frozen loss,
denominator, report, or original registered attempt.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

from aletheia_lab.evaluation._diagnosis_main_analysis_contracts import (
    ALL_CONDITIONS,
    CONTROLLED_VARIANTS,
    CORE_CONDITIONS,
    DiagnosisMainAnalysisCensus,
    DiagnosisMainAnalysisInput,
    DiagnosisMainAnalysisPlan,
    DiagnosisMainAnalysisReport,
    DiagnosisMainObservedRecord,
    MetricInterval,
)

CATEGORIES = ("technical_failure", "actual_abstention", "zero_claim", "claim_scored")
_CLAIM_WEIGHTS = {
    "contradicted": 1.0,
    "unsupported": 1.0,
    "fully_supported": 0.0,
}


class ForwardDecompositionError(ValueError):
    """A source or recomputed frozen endpoint does not reconcile."""


def classify_output(
    record: DiagnosisMainObservedRecord, condition: str, partial_weight: float
) -> tuple[str, float]:
    """Apply the frozen per-output loss while retaining its operational cause."""

    if record.technical_status != "success":
        return "technical_failure", 1.0
    if record.output_status == "abstained":
        return "actual_abstention", (
            0.0 if condition in {"missing_key", "counterevidence"} else 1.0
        )
    if not record.claims:
        return "zero_claim", 1.0
    weights = [
        partial_weight
        if claim.support_label == "partially_supported"
        else _CLAIM_WEIGHTS[claim.support_label]
        for claim in record.claims
    ]
    return "claim_scored", math.fsum(weights) / len(weights)


def _require_locked_inputs(
    name: str,
    plan: DiagnosisMainAnalysisPlan,
    census: DiagnosisMainAnalysisCensus,
    analysis_input: DiagnosisMainAnalysisInput,
    report: DiagnosisMainAnalysisReport,
) -> tuple[MetricInterval, MetricInterval, dict[str, MetricInterval]]:
    primary = report.primary_effect
    sensitivity = report.partial_support_sensitivity_effect
    conditions = report.condition_primary_effects
    if (
        report.status != "valid_registered_analysis"
        or report.analysis_plan_sha256 != plan.plan_sha256
        or report.census_sha256 != census.census_sha256
        or report.input_sha256 != analysis_input.input_sha256
        or analysis_input.analysis_plan_sha256 != plan.plan_sha256
        or analysis_input.census_sha256 != census.census_sha256
        or report.expected_request_count != len(census.requests)
        or report.terminal_request_count != len(analysis_input.records)
        or primary is None
        or sensitivity is None
        or conditions is None
    ):
        raise ForwardDecompositionError(f"{name} is not bound to the frozen census and report")
    return primary, sensitivity, conditions


def _require_complete_records(
    name: str,
    census: DiagnosisMainAnalysisCensus,
    analysis_input: DiagnosisMainAnalysisInput,
    report: DiagnosisMainAnalysisReport,
) -> dict[str, DiagnosisMainObservedRecord]:
    expected = {request.request_id: request for request in census.requests}
    observed = {record.request_id: record for record in analysis_input.records}
    if set(expected) != set(observed) or any(
        observed[request_id].request_sha256 != request.request_sha256
        for request_id, request in expected.items()
    ):
        raise ForwardDecompositionError(f"{name} request identities are incomplete or changed")
    if Counter(record.technical_status for record in observed.values()) != Counter(
        report.technical_status_counts
    ):
        raise ForwardDecompositionError(f"{name} technical status counts disagree with report")
    if sum(len(record.claims) for record in observed.values()) != report.raw_claim_count:
        raise ForwardDecompositionError(f"{name} claim count disagrees with report")
    return observed


def _checked_run(
    *,
    name: str,
    plan: DiagnosisMainAnalysisPlan,
    census: DiagnosisMainAnalysisCensus,
    analysis_input: DiagnosisMainAnalysisInput,
    report: DiagnosisMainAnalysisReport,
) -> dict[str, Any]:
    primary_report, sensitivity_report, condition_reports = _require_locked_inputs(
        name, plan, census, analysis_input, report
    )
    observed = _require_complete_records(name, census, analysis_input, report)

    contexts = {context.context_id: context for context in census.contexts}
    cells: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "category_counts": Counter(),
            "primary_loss_sums": Counter(),
            "sensitivity_loss_sum": 0.0,
            "emitted_claim_count": 0,
        }
    )
    family_losses: dict[tuple[str, str, str], tuple[float, float]] = {}
    for request in census.requests:
        record = observed[request.request_id]
        context = contexts[request.context_id]
        key = (context.evidence_condition, request.variant)
        cell = cells[key]
        category, loss = classify_output(record, key[0], plan.partial_support_primary_weight)
        _, sensitivity_loss = classify_output(
            record, key[0], plan.partial_support_sensitivity_weight
        )
        cell["count"] += 1
        cell["category_counts"][category] += 1
        cell["primary_loss_sums"][category] += loss
        cell["sensitivity_loss_sum"] += sensitivity_loss
        cell["emitted_claim_count"] += len(record.claims)
        family_key = (context.case_family_id, *key)
        if family_key in family_losses:
            raise ForwardDecompositionError(f"{name} repeats a family/condition/variant")
        family_losses[family_key] = (loss, sensitivity_loss)

    if set(cells) != {
        (condition, variant) for condition in ALL_CONDITIONS for variant in CONTROLLED_VARIANTS
    }:
        raise ForwardDecompositionError(f"{name} has an incomplete arm-by-condition census")
    cell_output = {
        f"{condition}.{variant}": {
            "request_count": cell["count"],
            "category_counts": {
                category: cell["category_counts"][category] for category in CATEGORIES
            },
            "mean_primary_loss": math.fsum(cell["primary_loss_sums"].values()) / cell["count"],
            "mean_sensitivity_loss": cell["sensitivity_loss_sum"] / cell["count"],
            "primary_loss_contributions": {
                category: cell["primary_loss_sums"][category] / cell["count"]
                for category in CATEGORIES
            },
            "emitted_claim_count": cell["emitted_claim_count"],
        }
        for (condition, variant), cell in sorted(cells.items())
    }
    family_ids = tuple(family.family_id for family in census.families)

    def paired_effect(conditions: tuple[str, ...], index: int) -> float:
        return math.fsum(
            math.fsum(
                family_losses[(family_id, condition, "B1")][index]
                - family_losses[(family_id, condition, "A3")][index]
                for condition in conditions
            )
            / len(conditions)
            for family_id in family_ids
        ) / len(family_ids)

    primary_effect = paired_effect(CORE_CONDITIONS, 0)
    sensitivity_effect = paired_effect(CORE_CONDITIONS, 1)
    if not math.isclose(
        primary_effect, primary_report.estimate, rel_tol=0.0, abs_tol=1e-12
    ) or not math.isclose(
        sensitivity_effect,
        sensitivity_report.estimate,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ForwardDecompositionError(f"{name} primary or sensitivity endpoint changed")
    for condition in CORE_CONDITIONS:
        if not math.isclose(
            paired_effect((condition,), 0),
            condition_reports[condition].estimate,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ForwardDecompositionError(f"{name} {condition} effect changed")

    def category_contrast(conditions: tuple[str, ...]) -> dict[str, float]:
        return {
            category: math.fsum(
                cell_output[f"{condition}.B1"]["primary_loss_contributions"][category]
                - cell_output[f"{condition}.A3"]["primary_loss_contributions"][category]
                for condition in conditions
            )
            / len(conditions)
            for category in CATEGORIES
        }

    contrasts = {condition: category_contrast((condition,)) for condition in CORE_CONDITIONS}
    contrasts["primary_three_condition_mean"] = category_contrast(CORE_CONDITIONS)
    if not math.isclose(
        math.fsum(contrasts["primary_three_condition_mean"].values()),
        primary_effect,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ForwardDecompositionError(f"{name} category decomposition does not close")
    return {
        "analysis_input_sha256": analysis_input.input_sha256,
        "report_sha256": report.report_sha256,
        "technical_status_counts": report.technical_status_counts,
        "raw_claim_count": report.raw_claim_count,
        "primary_effect": primary_report.model_dump(mode="json"),
        "partial_support_sensitivity_effect": sensitivity_report.model_dump(mode="json"),
        "cells": cell_output,
        "B1_minus_A3_primary_loss_contributions": contrasts,
    }


def decompose_locked_runs(
    *,
    plan: DiagnosisMainAnalysisPlan,
    census: DiagnosisMainAnalysisCensus,
    original_input: DiagnosisMainAnalysisInput,
    original_report: DiagnosisMainAnalysisReport,
    recovery_input: DiagnosisMainAnalysisInput,
    recovery_report: DiagnosisMainAnalysisReport,
) -> dict[str, Any]:
    """Reconcile both runs separately and emit aggregate-only secondary analysis."""

    original = _checked_run(
        name="original_registered_attempt",
        plan=plan,
        census=census,
        analysis_input=original_input,
        report=original_report,
    )
    recovery = _checked_run(
        name="forward_technical_recovery",
        plan=plan,
        census=census,
        analysis_input=recovery_input,
        report=recovery_report,
    )
    return {
        "schema_version": "diagnosis-main-forward-decomposition/v1",
        "status": "descriptive_secondary_analysis",
        "no_pooling_or_frozen_endpoint_changes": True,
        "analysis_plan_sha256": plan.plan_sha256,
        "census_sha256": census.census_sha256,
        "original_registered_attempt": original,
        "forward_technical_recovery": recovery,
    }
