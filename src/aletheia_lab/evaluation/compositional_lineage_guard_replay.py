"""Retrospective development replay: attribution without any new provider call.

The old experiment remains immutable. Neither a tool-generated action nor a
proof reconstruction is counted as new unaided LLM reasoning or replication.
"""

from __future__ import annotations

from collections import Counter
from contextlib import suppress
from pathlib import Path
from typing import Any

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation import compositional_lineage_pilot as pilot
from aletheia_lab.evaluation.compositional_lineage import (
    POLICIES,
    Decision,
    assess_decision,
    parse_decision,
)
from aletheia_lab.evaluation.compositional_lineage_cases import (
    assessment_row,
    baseline_rows,
    summarize_rows,
    validate_cases,
)
from aletheia_lab.evaluation.compositional_lineage_guard import (
    GuardMode,
    canonicalize_decision,
    guard_decision,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.warrant_development import DevelopmentCall
from aletheia_lab.evaluation.warrant_development_io import _read

STAGES = ("raw", "action_canonicalized", "reject_only", "proof_guarded")
MODES: tuple[GuardMode, ...] = ("reject_only", "proof_guarded")
FAILURE_CATEGORIES = (
    "valid",
    "unavailable",
    "unwarranted_commitment",
    "correct_status_invalid_certificate_or_contract",
    "invalid_noncommitment",
)


def _decision(
    request: dict[str, Any], record: dict[str, Any] | None
) -> tuple[Decision | None, str]:
    if record is None:
        return None, "not_executed"
    if set(record) != {"request_id", "policy", "context_sha256", "status", "call"} or any(
        record[key] != request[key] for key in ("request_id", "policy", "context_sha256")
    ):
        raise ValueError("cached result binding differs from the fixed frame")
    call = DevelopmentCall.model_validate(record["call"])
    pilot._resources(call)
    decision = None
    if call.status == "completed" and call.payload_json is not None:
        with suppress(ValueError, TypeError):
            decision = parse_decision(call.payload_json)
    status = "parsed" if decision is not None else "invalid_or_provider_failure"
    if record["status"] != status:
        raise ValueError("cached status differs from local parsing")
    return decision, status


def _failure_category(assessment: dict[str, Any]) -> str:
    if not assessment["assessable"]:
        return "unavailable"
    if assessment["action_success"]:
        return "valid"
    if assessment["unwarranted_commitment"]:
        return "unwarranted_commitment"
    if assessment["commitment"]:
        return "correct_status_invalid_certificate_or_contract"
    return "invalid_noncommitment"


def analyze_cached(
    cases: list[dict[str, Any]], records: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Apply the same interventions to both arms, preserving all 48 views."""
    validate_cases(cases)
    requests = pilot.request_frame(cases)
    if set(records) != {request["request_id"] for request in requests[: len(records)]}:
        raise ValueError("cached records are not the fixed-order execution prefix")
    by_key = {(request["policy"], request["context_sha256"]): request for request in requests}
    rows: dict[str, list[dict[str, Any]]] = {stage: [] for stage in STAGES}
    provenance: dict[GuardMode, dict[str, Counter[str]]] = {
        mode: {arm: Counter() for arm in POLICIES} for mode in MODES
    }
    failures: dict[str, Counter[str]] = {arm: Counter() for arm in POLICIES}
    canonicalized = dict.fromkeys(POLICIES, 0)
    for case in cases:
        context = case["context"]
        digest = canonical_execution_sha256(context)
        for arm in POLICIES:
            request = by_key[(arm, digest)]
            proposal, status = _decision(request, records.get(request["request_id"]))
            normalized = canonicalize_decision(proposal)
            canonicalized[arm] += int(proposal != normalized)
            decisions = {"raw": proposal, "action_canonicalized": normalized}
            for mode in MODES:
                guarded = guard_decision(context, proposal, mode=mode)
                decisions[mode] = guarded.decision
                provenance[mode][arm][guarded.origin] += 1
            for stage, decision in decisions.items():
                assessment = assess_decision(context, decision)
                rows[stage].append(assessment_row(case, arm, assessment, status))
                if stage == "raw":
                    failures[arm][_failure_category(assessment)] += 1
    return {
        "schema_version": "compositional-lineage-guard-analysis/v1",
        "status": "offline_cached_development_replay",
        "stages": {stage: summarize_rows(selected) for stage, selected in rows.items()},
        "deterministic_baselines": summarize_rows(baseline_rows(cases))["arms"],
        "guard_provenance": {
            mode: {arm: dict(sorted(counts.items())) for arm, counts in arms.items()}
            for mode, arms in provenance.items()
        },
        "raw_failure_categories": {
            arm: {key: counts[key] for key in FAILURE_CATEGORIES}
            for arm, counts in failures.items()
        },
        "action_canonicalization_count": canonicalized,
        "new_provider_calls": 0,
        "new_provider_cost_usd": 0.0,
        "checker_latency_measured": False,
        "new_data_efficacy_measured": False,
        "protected_predictions_used": False,
        "mechanism_admitted": False,
        "authored_motif_count": 12,
        "source_cluster_count": 1,
        "views_per_arm": 48,
        "interpretation": "post-observation development repair on cached outputs; solver assistance and reduced coverage are explicit; not A4 superiority or new-data replication",
    }


def _tree(directory: Path) -> dict[str, str]:
    paths = sorted(directory.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("cached pilot cannot contain symlinked artifacts")
    return {
        path.relative_to(directory).as_posix(): file_sha256(path)
        for path in paths
        if path.is_file()
    }


def replay_pilot(*, root: Path, memory_root: Path, directory: Path) -> dict[str, Any]:
    """Verify the original experiment before and after replay; never execute it."""
    receipt = pilot.verify(root=root, memory_root=memory_root, directory=directory)
    before = _tree(directory)
    cases = _read(directory / "cases.json")
    records = pilot._records(directory, cases)
    report = analyze_cached(cases, records)
    original = _read(directory / "analysis.json")
    if (
        report["stages"]["raw"]["arms"] != {arm: original["arms"][arm] for arm in POLICIES}
        or report["stages"]["raw"]["paired_motif_transitions"]
        != original["paired_motif_transitions"]
    ):
        raise ValueError("raw replay does not reproduce the frozen analysis")
    after_receipt = pilot.verify(root=root, memory_root=memory_root, directory=directory)
    after = _tree(directory)
    if before != after or receipt != after_receipt:
        raise ValueError("source pilot changed during offline analysis")
    code_paths = (
        "src/aletheia_lab/evaluation/compositional_lineage_guard.py",
        "src/aletheia_lab/evaluation/compositional_lineage_guard_replay.py",
        "scripts/analyze_compositional_lineage_guard.py",
    )
    report.update(
        verification="pass",
        provider_calls_executed=0,
        original_provider_attempt_count=receipt["provider_attempt_count"],
        original_completed_unique_requests=receipt["completed_unique_requests"],
        original_receipt_sha256=canonical_execution_sha256(
            {key: value for key, value in receipt.items() if key != "verification"}
        ),
        original_plan_sha256=receipt["plan_sha256"],
        original_analysis_sha256=receipt["analysis_sha256"],
        original_tree_sha256=canonical_execution_sha256(before),
        original_pilot_mutated=False,
        original_paid_arm_resources=original["paid_arm_resources"],
        code_sha256={name: file_sha256(root / name) for name in code_paths},
    )
    return report
