"""Paired development measurements without vacuous precision or stale references."""

from __future__ import annotations

from collections import defaultdict
from statistics import fmean
from typing import Any

from aletheia_lab.evaluation.claim_corpus_contracts import SUPPORT_LABELS
from aletheia_lab.evaluation.warrant_development import (
    JUDGES,
    WRITERS,
    Judge,
    WarrantCaseResult,
    WarrantReference,
    Writer,
    output_sha256,
    validate_reference,
)


def _support_overgraded(actual: str, predicted: str | None) -> bool:
    """Do not impose an order between contradicted and unsupported."""

    return (predicted == "fully_supported" and actual != "fully_supported") or (
        predicted == "partially_supported" and actual in {"unsupported", "contradicted"}
    )


def _nested_mean(rows: list[tuple[str, str, float]]) -> float | None:
    """Average cases within source output, outputs within family, then families."""

    by_output: dict[tuple[str, str], list[float]] = defaultdict(list)
    for family, output, value in rows:
        by_output[family, output].append(value)
    by_family: dict[str, list[float]] = defaultdict(list)
    for (family, _), values in by_output.items():
        by_family[family].append(fmean(values))
    return fmean(fmean(values) for values in by_family.values()) if by_family else None


def _confusion_metrics(matrix: dict[str, dict[str, int]]) -> dict[str, object]:
    per_class = {}
    f1_values: list[float] = []
    for label in SUPPORT_LABELS:
        actual = sum(matrix[label].values())
        predicted = sum(row[label] for row in matrix.values())
        tp = matrix[label][label]
        f1 = 2 * tp / (actual + predicted) if actual + predicted else None
        per_class[label] = {
            "reference_count": actual,
            "predicted_count": predicted,
            "recall": tp / actual if actual else None,
            "precision": tp / predicted if predicted else None,
            "f1": f1,
        }
        if f1 is not None:
            f1_values.append(f1)
    nonfull = sum(sum(row.values()) for label, row in matrix.items() if label != "fully_supported")
    falsefull = sum(
        row["fully_supported"] for label, row in matrix.items() if label != "fully_supported"
    )
    return {
        "confusion_reference_by_prediction": matrix,
        "per_class": per_class,
        "macro_f1_evaluable_classes": fmean(f1_values) if f1_values else None,
        "macro_f1_class_count": len(f1_values),
        "false_full_support_count": falsefull,
        "non_full_reference_count": nonfull,
        "false_full_support_rate": falsefull / nonfull if nonfull else None,
    }


def analyze_warrant_results(
    results: tuple[WarrantCaseResult, ...], references: tuple[WarrantReference, ...] = ()
) -> dict[str, Any]:
    """Missing new-text reference or content coverage prevents winner selection."""

    ids = tuple(result.case.case_id for result in results)
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("results require a nonempty unique case census")
    reference_map: dict[tuple[str, str], WarrantReference] = {}
    outputs = {
        (result.case.case_id, output_sha256(writer.output)): (result.case, writer.output)
        for result in results
        for writer in result.writers
    }
    for reference in references:
        key = reference.case_id, reference.output_sha256
        if key in reference_map or key not in outputs:
            raise ValueError("duplicate or foreign development reference")
        case, output = outputs[key]
        validate_reference(case, output, reference)
        reference_map[key] = reference
    reports: dict[str, Any] = {}
    selection_ready = True
    for component in sorted({result.case.component for result in results}):
        selected = tuple(result for result in results if result.case.component == component)
        cells: dict[str, Any] = {}
        for writer_name in WRITERS:
            for judge_name in JUDGES:
                cell = _cell_summary(selected, reference_map, writer_name, judge_name)
                cells[f"{writer_name}/{judge_name}"] = cell
                selection_ready &= cell["reference_output_count"] == len(selected)
                selection_ready &= cell["coverage_output_count"] == len(selected)
        reports[component] = {
            "case_count": len(selected),
            "source_output_count": len({result.case.source_output_id for result in selected}),
            "family_count": len({result.case.family_id for result in selected}),
            "cells": cells,
            "paired_apparent_support": _paired_contrasts(selected),
        }
    calls = [
        call
        for result in results
        for writer in result.writers
        for call in ([writer.call] if writer.call else [])
        + [judgment.call for judgments in writer.judgments.values() for judgment in judgments]
    ]
    return {
        "schema_version": "warrant-development-analysis/v1",
        "status": "development_only",
        "experiment": "cached_claim_vs_evidence_bounded_rewrite_crossed_with_two_judges",
        "candidate_selection_reference_complete": selection_ready,
        "selected_winner": None,
        "components": reports,
        "caller_invocation_count": len(calls),
        "provider_invocation_count": sum(call.provider_attempted for call in calls),
        "usage_unobserved_call_count": sum(
            not call.usage_observed and call.provider_attempted for call in calls
        ),
        "input_tokens": sum(call.input_tokens for call in calls),
        "output_tokens": sum(call.output_tokens for call in calls),
        "estimated_cost_usd": sum(call.estimated_cost_usd for call in calls),
        "limitations": [
            "Purposive development, not population accuracy or final validation.",
            "Cached control does not establish a fair fresh-diagnosis generator comparison.",
            "Apparent factorial support contrasts are not semantic improvement without references and coverage.",
            "Families sharing source data may be dependent; no inferential CI is claimed.",
        ],
    }


def _paired_contrasts(results: tuple[WarrantCaseResult, ...]) -> dict[str, Any]:
    rows: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
    count = 0
    for result in results:
        values = {
            f"{writer.writer}/{judge}": sum(item.label == "fully_supported" for item in judgments)
            / len(judgments)
            for writer in result.writers
            for judge, judgments in writer.judgments.items()
            if judgments and all(item.label is not None for item in judgments)
        }
        if len(values) != 4:
            continue
        count += 1
        for key, value in values.items():
            rows[key].append((result.case.family_id, result.case.source_output_id, value))
    means = {key: _nested_mean(values) for key, values in rows.items()}
    contrasts = None
    if count:
        a, b, c, d = (
            means[key]
            for key in (
                "cached/legacy",
                "bounded_rewrite/legacy",
                "cached/warrant",
                "bounded_rewrite/warrant",
            )
        )
        if a is None or b is None or c is None or d is None:
            raise ValueError("complete paired case has an empty family cell")
        contrasts = {
            "writer_under_legacy": b - a,
            "writer_under_warrant": d - c,
            "judge_on_cached": c - a,
            "judge_on_rewrite": d - b,
            "interaction": d - b - c + a,
        }
    return {
        "paired_case_count": count,
        "excluded_empty_or_invalid_case_count": len(results) - count,
        "family_macro_support": means,
        "contrasts": contrasts,
        "interpretation": "complete-case apparent support only; empty/failure counts and semantic coverage remain separately reported",
    }


def _cell_summary(
    results: tuple[WarrantCaseResult, ...],
    references: dict[tuple[str, str], WarrantReference],
    writer_name: Writer,
    judge_name: Judge,
) -> dict[str, Any]:
    matrix: dict[str, dict[str, int]] = {
        actual: {predicted: 0 for predicted in (*SUPPORT_LABELS, "invalid_response")}
        for actual in SUPPORT_LABELS
    }
    automatic: list[tuple[str, str, float]] = []
    coverage: list[tuple[str, str, float]] = []
    reference_support: list[tuple[str, str, float]] = []
    unsupported: list[tuple[str, str, float]] = []
    judge_error: list[tuple[str, str, float]] = []
    judge_overgrading: list[tuple[str, str, float]] = []
    force_overgrading: list[tuple[str, str, float]] = []
    terminal = defaultdict[str, int](int)
    claim_counts: list[tuple[str, str, float]] = []
    reference_count = invalid = overgraded = assessed = force_nonfull = force_falsefull = 0
    for result in results:
        case = result.case
        writer = next(item for item in result.writers if item.writer == writer_name)
        terminal[writer.output.status] += 1
        claim_counts.append(
            (case.family_id, case.source_output_id, float(len(writer.output.claims)))
        )
        judgments = writer.judgments[judge_name]
        invalid += sum(item.label is None for item in judgments)
        if judgments and all(item.label is not None for item in judgments):
            automatic.append(
                (
                    case.family_id,
                    case.source_output_id,
                    sum(item.label == "fully_supported" for item in judgments) / len(judgments),
                )
            )
        reference = references.get((case.case_id, output_sha256(writer.output)))
        if reference is None:
            continue
        reference_count += 1
        if reference.warranted_unit_ids is not None:
            coverage.append(
                (
                    case.family_id,
                    case.source_output_id,
                    len(reference.warranted_unit_ids) / len(case.required_unit_ids),
                )
            )
        if reference.labels:
            comparisons = tuple(zip(reference.labels, judgments, strict=True))
            judge_error.append(
                (
                    case.family_id,
                    case.source_output_id,
                    sum(prediction.label != actual for actual, prediction in comparisons)
                    / len(comparisons),
                )
            )
            judge_overgrading.append(
                (
                    case.family_id,
                    case.source_output_id,
                    sum(
                        _support_overgraded(actual, prediction.label)
                        for actual, prediction in comparisons
                    )
                    / len(comparisons),
                )
            )
            if case.source_claim.claim_type in {"cause_assertion", "recommended_action"}:
                eligible = [
                    (actual, prediction)
                    for actual, prediction in comparisons
                    if actual != "fully_supported"
                ]
                if eligible:
                    force_overgrading.append(
                        (
                            case.family_id,
                            case.source_output_id,
                            sum(prediction.label == "fully_supported" for _, prediction in eligible)
                            / len(eligible),
                        )
                    )
                    force_nonfull += len(eligible)
                    force_falsefull += sum(
                        prediction.label == "fully_supported" for _, prediction in eligible
                    )
            reference_support.append(
                (
                    case.family_id,
                    case.source_output_id,
                    reference.labels.count("fully_supported") / len(reference.labels),
                )
            )
            unsupported.append(
                (
                    case.family_id,
                    case.source_output_id,
                    float(any(label != "fully_supported" for label in reference.labels)),
                )
            )
        for actual, prediction in zip(reference.labels, judgments, strict=True):
            matrix[actual][
                prediction.label if prediction.label is not None else "invalid_response"
            ] += 1
            if prediction.label is not None:
                assessed += 1
                overgraded += _support_overgraded(actual, prediction.label)
    return {
        "eligible_case_count": len(results),
        "terminal_counts": dict(terminal),
        "family_macro_claim_count": _nested_mean(claim_counts),
        "reference_output_count": reference_count,
        "coverage_output_count": len(coverage),
        "invalid_judgment_count": invalid,
        "assessed_reference_claim_count": assessed,
        "support_overgrading_count": overgraded,
        "support_overgrading_rate_among_valid": overgraded / assessed if assessed else None,
        "source_force_nonfull_reference_count": force_nonfull,
        "source_force_false_full_count": force_falsefull,
        "source_force_false_full_rate": force_falsefull / force_nonfull if force_nonfull else None,
        "family_macro_automatic_full_support": _nested_mean(automatic),
        "automatic_evaluable_output_count": len(automatic),
        "family_macro_reference_full_support": _nested_mean(reference_support),
        "family_macro_warranted_coverage": _nested_mean(coverage),
        "family_macro_any_unwarranted_claim": _nested_mean(unsupported),
        "family_macro_judge_error_including_invalid": _nested_mean(judge_error),
        "family_macro_support_overgrading_including_invalid": _nested_mean(judge_overgrading),
        "family_macro_source_force_false_full": _nested_mean(force_overgrading),
        "judge_metric_note": "Raw confusion/per-class F1 are claim-level diagnostics; judge comparisons use nested family means and include invalid responses as errors.",
        "judge_metrics": _confusion_metrics(matrix),
    }
