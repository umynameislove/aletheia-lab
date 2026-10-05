"""Pinned, read-only acceptance of historical model-load research artifacts.

Identity-only inspection and exact historical replay are distinct operations.
Neither admits a mechanism, authenticates a hostile host, or integrates a product.
Only allowlisted aggregate endpoints leave this boundary; retained rows stay private.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.model_load_provenance import document_digest
from aletheia_lab.project.identity import content_sha256

SCHEMAS = {
    "observability": "model-load-observability-development/v1",
    "provenance": "model-load-provenance-development/v1",
    "validation": "model-load-validation-results/v1",
    "closeout": "model-load-evidence-closeout/v1",
    "runtime": "model-load-runtime-development/v1",
    "application": "model-load-application-development/v1",
}
MAX_DOCUMENT_BYTES = 33_554_432
_VERDICTS = {"compliant", "violation", "unknown", "conflict", "no_new_load", "None"}
_DISPOSITIONS = {
    "observability": {
        "narrow_controlled_feasibility_no_new_method_evidence",
        "implementation_or_model_gap",
        "implementation_or_reference_gap",
    },
    "provenance": {
        "incomplete_development_census",
        "baseline_or_contract_gap",
        "bounded_capture_finding_no_new_checker_advantage",
        "no_discriminating_capture_finding",
    },
    "validation": {
        "incomplete_validation",
        "baseline_or_capture_contract_gap",
        "bounded_transfer_no_new_checker_advantage",
    },
    "runtime": {
        "development_complete",
        "development_incomplete_failures_preserved",
        "source_changed_partial_evidence",
    },
    "application": {
        "bounded_application_capture_transfer_no_new_checker_advantage",
        "incomplete_or_failed_application_development",
    },
    "closeout": {"exposed_post_result_descriptive_ablation"},
}


def count(value: Any) -> int:
    """A missing count or boolean is not a measured zero or one."""
    if type(value) is not int or value < 0:
        raise ValueError("invalid nonnegative integer count")
    return int(value)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON member")
        value[key] = item
    return value


def _constant(value: str) -> None:
    raise ValueError("nonfinite JSON constant")


def _float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite JSON number")
    return result


def _boolean(value: Any) -> bool:
    if type(value) is not bool:
        raise ValueError("invalid measured boolean")
    return bool(value)


def read_document(path: Path, *, limit: int = MAX_DOCUMENT_BYTES) -> tuple[bytes, dict[str, Any]]:
    """Bounded JSON only, with no pickle, dynamic path references or model loading."""
    if type(limit) is not int or not 0 < limit <= MAX_DOCUMENT_BYTES:
        raise ValueError("invalid document allowance")
    if any(part.is_symlink() for part in (path, *path.absolute().parents)):
        raise ValueError("symlink document is not accepted")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("document exceeds acceptance allowance")
    value = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant, parse_float=_float)
    if not isinstance(value, dict):
        raise ValueError("document must be a JSON object")
    return raw, value


def _histogram(value: dict[str, Any], allowed: set[str]) -> dict[str, int]:
    if not set(value) <= allowed:
        raise ValueError("unexpected terminal state")
    return {key: count(item) for key, item in sorted(value.items())}


def _comparison(
    value: dict[str, Any],
    denominator: int,
    method: str,
    cutoff: str,
    *,
    unit: str,
    verdict_denominator: int | None = None,
    verdict_unit: str | None = None,
) -> dict[str, Any]:
    identified = count(
        value.get("correct_identified", value.get("correct_identified_load_attempts"))
    )
    if identified > denominator:
        raise ValueError("numerator exceeds its declared denominator")
    histogram = _histogram(value["verdict_counts"], _VERDICTS)
    histogram_denominator = denominator if verdict_denominator is None else verdict_denominator
    if sum(histogram.values()) != histogram_denominator:
        raise ValueError("verdict census differs from its declared unit")
    false_compliance, false_violation = (
        count(value["false_compliance"]),
        count(value["false_violation"]),
    )
    if false_compliance + false_violation > denominator:
        raise ValueError("false verdicts exceed the comparison denominator")
    if identified > histogram.get("compliant", 0) + histogram.get("violation", 0):
        raise ValueError("identifications exceed definite verdicts")
    return {
        "method": method,
        "cutoff": cutoff,
        "unit": unit,
        "correct_identified": identified,
        "denominator": denominator,
        "rate": identified / denominator if denominator else None,
        "false_compliance": false_compliance,
        "false_violation": false_violation,
        "verdict_counts": histogram,
        "verdict_counts_unit": unit if verdict_unit is None else verdict_unit,
        "verdict_counts_denominator": histogram_denominator,
    }


def _comparisons(
    cutoffs: dict[str, Any],
    denominator: int | None,
    *,
    unit: str,
    verdict_denominator: int | None = None,
    verdict_unit: str | None = None,
) -> list[dict[str, Any]]:
    if not set(cutoffs) <= {"native", "before", "after", "scoped"}:
        raise ValueError("unexpected observation cutoff")
    if any(not set(methods) <= {"S", "T", "P"} for methods in cutoffs.values()):
        raise ValueError("unexpected comparator")
    return [
        _comparison(
            value,
            count(value["planned_denominator"]) if denominator is None else denominator,
            method,
            cutoff,
            unit=unit,
            verdict_denominator=verdict_denominator,
            verdict_unit=verdict_unit,
        )
        for cutoff, methods in sorted(cutoffs.items())
        for method, value in sorted(methods.items())
    ]


def _development(value: dict[str, Any], kind: str) -> dict[str, Any]:
    summary = value["summary"]
    episodes = count(summary["episode_count"])
    if kind == "observability":
        denominator = count(summary["eligible_load_attempts"])
        comparisons = _comparisons(
            summary["comparisons"],
            denominator,
            unit="eligible_load_attempt",
            verdict_denominator=episodes,
            verdict_unit="authored_episode",
        )
        cache = count(summary["no_new_load_attempts"])
    else:
        denominator = count(summary["planned_load_attempts"])
        comparisons = _comparisons(
            {"scoped": summary["comparisons"]}, denominator, unit="planned_load_attempt"
        )
        cache = count(summary["cache_only_attempts"])
    if episodes != denominator + cache:
        raise ValueError("load/cache census does not reconcile")
    return {
        "unit": "authored_episode",
        "episode_count": episodes,
        "load_attempts": denominator,
        "no_new_load_attempts": cache,
        "comparisons": comparisons,
        "technical_failure_count": count(summary["technical_failure_count"])
        if kind == "provenance"
        else None,
        "technical_failure_count_status": "reported"
        if kind == "provenance"
        else "not_separately_estimated",
    }


def _validation(value: dict[str, Any]) -> dict[str, Any]:
    analysis = value["analysis"]
    slots, loads, cache = (
        count(analysis[key])
        for key in ("planned_slots", "planned_load_slots", "planned_cache_slots")
    )
    if slots != loads + cache:
        raise ValueError("validation load/cache census does not reconcile")
    terminal = _histogram(
        analysis["status_counts"],
        {
            "completed",
            "technical_failure",
            "unexecuted",
            "slot_timeout",
            "invalid_worker_output",
            "worker_failure",
            "reference_integrity_failure",
        },
    )
    if sum(terminal.values()) != slots:
        raise ValueError("validation terminal census does not reconcile")
    return {
        "unit": "planned_slot",
        "planned_slots": slots,
        "planned_load_slots": loads,
        "planned_cache_slots": cache,
        "terminal_status_counts": terminal,
        "retained_native_entries": count(analysis["retained_native_entries"]),
        "reserved_native_entries": count(value["reserved_native_entries"]),
        "loader_format_strata": count(analysis["loader_format_strata"]),
        "independent_deployments": count(analysis["independent_deployments"]),
        "prevention": {
            key: count(analysis["prevention"][key])
            for key in (
                "planned_opportunities",
                "unavailable",
                "blocked_proposals",
                "false_blocks",
                "compliant_load_completions",
                "residual_violations",
                "cache_only",
                "blocked_without_load",
            )
        },
        "prevention_unit": "planned_prevention_opportunity_not_observation_load",
        "comparisons": _comparisons(
            analysis["comparisons"], None, unit="planned_observation_load_slot"
        ),
    }


def _application(value: dict[str, Any]) -> dict[str, Any]:
    summary, comparison = value["summary"], value["comparison"]
    statuses = _histogram(
        comparison["reference_counts"], {"compliant", "violation", "unknown", "no_new_load"}
    )
    operations = count(comparison["operation_denominator"])
    if sum(statuses.values()) != operations:
        raise ValueError("application operation/reference census does not reconcile")
    completed, planned = (
        count(summary["http_operation_count"]),
        count(summary["planned_http_operations"]),
    )
    if (
        completed > planned
        or count(summary["http_failure_count"]) > completed
        or operations > completed
    ):
        raise ValueError("application HTTP census does not reconcile")
    if count(summary["reconstruction_entries"]) > count(summary["loader_calls"]):
        raise ValueError("reconstructions exceed loader entries")
    if count(comparison["decided_load_operations"]) != statuses.get("compliant", 0) + statuses.get(
        "violation", 0
    ):
        raise ValueError("application decided load census differs")
    if count(comparison["unknown_load_operations"]) != statuses.get("unknown", 0):
        raise ValueError("application unknown load census differs")
    agreement: list[dict[str, Any]] = [
        {
            "method": method,
            "unit": "application_operation",
            "numerator": count(comparison[f"{method}_reference_agreement"]),
            "denominator": operations,
        }
        for method in ("S", "P")
    ]
    if any(item["numerator"] > operations for item in agreement):
        raise ValueError("application agreement exceeds its denominator")
    if count(comparison["planned_operation_denominator"]) != operations + count(
        comparison["unscored_planned_operations"]
    ):
        raise ValueError("application planned/scored census does not reconcile")
    return {
        "unit": "application_operation",
        "planned_http_operations": count(summary["planned_http_operations"]),
        "completed_http_operations": count(summary["http_operation_count"]),
        "http_failure_count": count(summary["http_failure_count"]),
        "known_native_loader_calls": count(summary["loader_calls"]),
        "known_reconstruction_entries": count(summary["reconstruction_entries"]),
        "native_census_complete": _boolean(summary["native_census_complete"]),
        "failed_arms_with_unknown_partial_native_census": count(
            summary["failed_arms_with_unknown_partial_native_census"]
        ),
        "planned_comparator_operations": count(comparison["planned_operation_denominator"]),
        "comparator_operations": operations,
        "unscored_planned_operations": count(comparison["unscored_planned_operations"]),
        "decided_load_operations": count(comparison["decided_load_operations"]),
        "failed_explicit_load_operations": count(comparison["failed_explicit_load_operations"]),
        "unknown_load_operations": count(comparison["unknown_load_operations"]),
        "reference_counts": statuses,
        "agreement": agreement,
        "source_cluster_count": count(summary["source_cluster_count"]),
        "authorization_basis": "research_operator_pin_not_native_application_policy",
        "capture_condition": "stable_opened_inode_not_every_consumed_byte",
    }


def _closeout(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "unit": "planned_observation_load_slot",
        "comparisons": _comparisons(
            {key: item["unmodified"] for key, item in value["cutoffs"].items()},
            None,
            unit="planned_observation_load_slot",
        ),
        "parent_results_sha256": value["validation_results_sha256"],
        "analysis_role": "exposed_post_result_descriptive_ablation",
    }


def _runtime(value: dict[str, Any]) -> dict[str, Any]:
    native, concurrency = value["native_cost"], value["concurrency"]
    terminal = _histogram(native["status_counts"], {"completed", "failed", "worker_failed_unknown"})
    if sum(terminal.values()) != count(native["planned_native_loads"]):
        raise ValueError("runtime terminal sample census does not reconcile")
    if count(native["known_native_completed"]) > count(native["known_native_entries"]):
        raise ValueError("completed native entries exceed known entries")
    comparisons = []
    for item in concurrency["comparisons"]:
        if item["service"] not in {"children_only", "root_and_children"}:
            raise ValueError("unknown retention query service")
        comparisons.append(
            {
                "service": item["service"],
                "horizon": count(item["horizon"]),
                "comparison_available": _boolean(item["comparison_available"]),
                "matched_immediate_decisions": _boolean(item["matched_immediate_decisions"]),
                "matched_audit_decisions_and_availability": _boolean(
                    item["matched_audit_decisions_and_availability"]
                ),
            }
        )
    return {
        "unit": "native_cost_sample",
        "planned_workers": count(native["planned_workers"]),
        "planned_native_loads": count(native["planned_native_loads"]),
        "known_native_entries": count(native["known_native_entries"]),
        "known_native_completed": count(native["known_native_completed"]),
        "terminal_status_counts": terminal,
        "all_native_entry_counts_known": _boolean(native["all_native_entry_counts_known"]),
        "retention_configuration_runs": count(concurrency["executed_configuration_runs"]),
        "retention_primary_requests": count(concurrency["primary_requests"]),
        "retention_primary_attempts": count(concurrency["primary_attempts"]),
        "retention_comparisons": comparisons,
        "resource_scope": "local_descriptive_cost_and_query_specific_retention_not_population_or_throughput",
    }


def _replay(
    root: Path, path: Path, value: dict[str, Any], kind: str, plan: Path | None, study: Path | None
) -> None:
    if kind == "observability":
        from aletheia_lab.evaluation.model_load_observability import verify_report

        verify_report(value, root=root)
    elif kind == "provenance":
        from aletheia_lab.evaluation.model_load_provenance_study import (
            verify_report as verify_provenance,
        )

        verify_provenance(value, root)
    elif kind == "application":
        from aletheia_lab.evaluation.model_load_application_analysis import verify

        verify(root, path)
    elif kind == "runtime":
        from aletheia_lab.evaluation.model_load_runtime_cost import verify_report as verify_runtime

        verify_runtime(root, path)
    else:
        from aletheia_lab.evaluation import model_load_validation_run as frozen

        if plan is None or study is None:
            raise ValueError("validation replay requires the original plan and study")
        verified = frozen.verify(root, plan, study)
        if kind == "validation":
            if verified["results_sha256"] != value["results_sha256"]:
                raise ValueError("replayed validation differs from pinned input")
        else:
            from aletheia_lab.evaluation.model_load_evidence_analysis import analyze_rows

            if (verified["results_sha256"], verified["seal_sha256"]) != (
                value["validation_results_sha256"],
                value["validation_seal_sha256"],
            ):
                raise ValueError("closeout parent identity changed")
            parent = frozen.read_signed(study / "results.json", "results_sha256")
            recomputed = analyze_rows(parent["rows"])
            if document_digest(recomputed) != document_digest(
                {key: value[key] for key in recomputed}
            ):
                raise ValueError("closeout endpoints changed")


def accept_receipt(
    root: Path,
    path: Path,
    kind: str,
    expected_sha256: str,
    *,
    replay: bool = True,
    plan: Path | None = None,
    study: Path | None = None,
    parent_results_sha256: str | None = None,
) -> dict[str, Any]:
    """The caller supplies the accepted identity, not a hash learned from this file."""
    if kind not in SCHEMAS or re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None:
        raise ValueError("unknown artifact kind or invalid accepted identity")
    _boolean(replay)
    root = root.resolve()
    if path.resolve().is_relative_to(root):
        raise ValueError("historical research artifact must remain outside the repository")
    before, value = read_document(path)
    field = "results_sha256" if kind == "validation" else "report_sha256"
    if (
        value.get("schema_version") != SCHEMAS[kind]
        or value.get(field) != expected_sha256
        or document_digest({key: item for key, item in value.items() if key != field})
        != expected_sha256
    ):
        raise ValueError("artifact is not the pinned accepted document")
    if type(value.get("provider_calls")) is not int or value["provider_calls"] != 0:
        raise ValueError("unexpected provider activity")
    if kind == "closeout" and (
        parent_results_sha256 is None
        or re.fullmatch(r"[a-f0-9]{64}", parent_results_sha256) is None
        or value["validation_results_sha256"] != parent_results_sha256
    ):
        raise ValueError("closeout requires the accepted parent result identity")
    if replay:
        _replay(root, path, value, kind, plan, study)
    if path.read_bytes() != before:
        raise ValueError("input mutated during acceptance")
    if kind in {"observability", "provenance"}:
        summary = _development(value, kind)
    else:
        summary = {
            "validation": _validation,
            "closeout": _closeout,
            "runtime": _runtime,
            "application": _application,
        }[kind](value)
    disposition = {
        "validation": value.get("analysis", {}).get("disposition"),
        "application": value.get("summary", {}).get("disposition"),
        "runtime": value.get("status"),
        "closeout": value.get("analysis_class"),
    }.get(kind, value.get("disposition"))
    if disposition not in _DISPOSITIONS[kind]:
        raise ValueError("unknown historical scientific disposition")
    return {
        "schema_version": "model-load-research-acceptance/v1",
        "status": "exact_historical_replay_pass" if replay else "pinned_summary_only_not_replayed",
        "artifact_kind": kind,
        "source_sha256": expected_sha256,
        "source_file_sha256": content_sha256(before),
        "summary": summary,
        "historical_disposition": disposition,
        "native_loads_replayed": 0,
        "provider_calls": 0,
        "source_mutated": False,
        "retained_signatures_independently_reverified": False,
        "fresh_local_rule_checks": replay and kind in {"provenance", "validation", "closeout"},
        "population_inference": False,
        "scientific_admission_changed": False,
        "product_consumer_integration": "not_executed",
    }
