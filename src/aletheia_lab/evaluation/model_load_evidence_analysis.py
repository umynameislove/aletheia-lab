"""Exposed, hash-only evidence ablations; never repair the frozen validation input."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from itertools import combinations
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.model_load_contract import (
    Decision,
    Observation,
    Record,
    completion_monitor,
    receipt_checker,
)
from aletheia_lab.evaluation.model_load_provenance import document_digest
from aletheia_lab.evaluation.model_load_validation_lifecycle import observation
from aletheia_lab.filesystem import publish_immutable_file

GROUPS = ("selection", "load", "closure", "parent", "other")


def publish_report(output: Path, report: dict[str, Any]) -> None:
    """Single aggregate file allowance, not the frozen study's directory budget."""
    raw = json.dumps(report, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("development aggregate exceeds four MiB")
    if publish_immutable_file(output, raw) != "created":
        raise ValueError("development report already exists")


def record_group(record: Record, obs: Observation) -> str:
    if record.scope == obs.scope:
        return record.kind if record.kind in GROUPS[:3] else "other"
    return "parent" if record.kind == "selection" else "other"


def sufficient_records(obs: Observation) -> tuple[Record, ...]:
    """A static selector, not an optimizer or an inference over reference truth.

    Keep every current-scope record (including conflicting identity/kind) and all
    root selections referenced by any current selection. Do not choose a latest
    parent or collapse distinct load occurrences. This preserves the bounded S/T
    admission frame; it does not authenticate records or prove producer capture.
    """
    targets = tuple(record for record in obs.records if record.scope == obs.scope)
    parents = {
        (record.parent_scope, record.parent_selection)
        for record in targets
        if record.kind == "selection"
    }
    return tuple(
        record
        for record in obs.records
        if record.scope == obs.scope
        or record.kind == "selection"
        and (record.scope, record.selection) in parents
    )


def ablate(
    obs: Observation, groups: tuple[str, ...], *, remove_policy: bool = False
) -> Observation:
    if any(group not in GROUPS for group in groups):
        raise ValueError("unknown evidence group")
    return replace(
        obs,
        contract=None if remove_policy else obs.contract,
        records=tuple(record for record in obs.records if record_group(record, obs) not in groups),
    )


def count_decisions(choices: list[tuple[Decision, dict[str, Any]]]) -> dict[str, Any]:
    assessable = [(d, t) for d, t in choices if t["assessable"]]
    identified = sum(
        d.verdict in {"compliant", "violation"} and d.verdict == t["verdict"] for d, t in assessable
    )
    return {
        "planned_denominator": len(choices),
        "reference_assessable": len(assessable),
        "correct_identified": identified,
        "planned_load_denominator": sum(t["verdict"] is not None for _, t in choices),
        "correct_no_new_load": sum(
            d.verdict is None and d.eligibility == "no_new_load" and t["verdict"] is None
            for d, t in assessable
        ),
        "false_compliance": sum(
            d.verdict == "compliant" and t["verdict"] != "compliant" for d, t in assessable
        ),
        "false_violation": sum(
            d.verdict == "violation" and t["verdict"] != "violation" for d, t in assessable
        ),
        "verdict_counts": dict(sorted(Counter(str(d.verdict) for d, _ in choices).items())),
        "reference_verdict_counts": dict(
            sorted(Counter(str(t["verdict"]) for _, t in assessable).items())
        ),
    }


def _choices(
    rows: list[dict[str, Any]], cutoff: str, removed: tuple[str, ...], remove_policy: bool = False
) -> dict[str, Any]:
    values: dict[str, list[tuple[Decision, dict[str, Any]]]] = {"S": [], "T": []}
    for row in rows:
        if row["status"] == "completed":
            obs = ablate(
                observation(row["observations"][cutoff]), removed, remove_policy=remove_policy
            )
            pair = {"S": receipt_checker(obs), "T": completion_monitor(obs)}
        else:
            pair = {
                key: Decision("unknown", "unavailable_execution", "undetermined") for key in values
            }
        for key, decision in pair.items():
            values[key].append((decision, row["reference"]))
    return {key: count_decisions(value) for key, value in values.items()}


def _subset_diagnostics(rows: list[dict[str, Any]], cutoff: str) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    removed_records = comparisons = 0
    for row in rows:
        if row["status"] != "completed":
            continue
        obs = observation(row["observations"][cutoff])
        minimal = replace(obs, records=sufficient_records(obs))
        for checker in (receipt_checker, completion_monitor):
            before, after = checker(obs), checker(minimal)
            if (before.verdict, before.reason, before.eligibility) != (
                after.verdict,
                after.reason,
                after.eligibility,
            ):
                raise ValueError("static sufficient selection changed a decision")
        comparisons += 1
        removed_records += len(obs.records) - len(minimal.records)
        original = receipt_checker(obs)
        if original.verdict not in {"compliant", "violation"}:
            counts["original_unidentified"] += 1
            continue
        # Exposed exact group-subset enumeration; not an online acquisition policy.
        # Test all subsets, since malformed evidence need not be monotone.
        sizes = []
        for size in range(len(GROUPS) + 1):
            for kept in combinations(GROUPS, size):
                removed = tuple(g for g in GROUPS if g not in kept)
                decision = receipt_checker(ablate(obs, removed))
                if (decision.verdict, decision.eligibility) == (
                    original.verdict,
                    original.eligibility,
                ):
                    sizes.append(size)
        counts[str(min(sizes))] += 1
    return {
        "decision_preserving_static_views": comparisons,
        "removed_irrelevant_records": removed_records,
        "minimum_group_count_histogram": dict(sorted(counts.items())),
        "subset_count_per_view": 32,
        "interpretation": "post-result finite group cardinality, not byte-optimal or online-safe pruning",
    }


def analyze_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Caller must first validate result authority and independent references."""
    selected = [
        r
        for r in rows
        if r["slot"]["branch"] == "observation" and r["slot"]["planned_target_entries"] > 0
    ]
    if not selected:
        raise ValueError("empty planned observation-load census")
    cutoffs = {}
    for cutoff in ("before", "after"):
        cutoffs[cutoff] = {
            "unmodified": _choices(selected, cutoff, ()),
            "remove_policy": _choices(selected, cutoff, (), True),
            "remove_each_group": {g: _choices(selected, cutoff, (g,)) for g in GROUPS},
            "sufficiency": _subset_diagnostics(selected, cutoff),
        }
    gaps: Counter[str] = Counter()
    recovered: Counter[str] = Counter()
    for row in selected:
        if row["status"] != "completed":
            gaps["unavailable_execution"] += 1
            continue
        before = receipt_checker(observation(row["observations"]["before"]))
        after = receipt_checker(observation(row["observations"]["after"]))
        if before.verdict == "unknown" and after.verdict in {"compliant", "violation"}:
            recovered[after.verdict] += 1
        if after.verdict == "unknown":
            gaps[f"{after.reason}:reference_{row['reference']['verdict']}"] += 1
    return {
        "schema_version": "model-load-evidence-closeout/v1",
        "analysis_class": "exposed_post_result_descriptive_ablation",
        "cutoffs": cutoffs,
        "delayed_delivery_resolutions": dict(sorted(recovered.items())),
        "remaining_gaps": dict(sorted(gaps.items())),
        "full_scoped_opposite_status_claim": "not established; original exact grouping has zero opposite groups",
        "limitations": "one authored scheduler, no repaired observer inputs, no population inference",
    }


def closeout(root: Path, plan: Path, study: Path, output: Path) -> dict[str, Any]:
    from aletheia_lab.evaluation import model_load_validation_run as frozen

    root, study, output = root.resolve(), study.resolve(), output.absolute()
    if output.resolve().is_relative_to(root) or output.resolve().is_relative_to(study):
        raise ValueError("closeout must be outside repository and frozen study")
    before = (study / "results.json").read_bytes()
    verified = frozen.verify(root, plan, study)  # Hash-only; never executes a native loader.
    result = frozen.read_signed(study / "results.json", "results_sha256")
    analysis = analyze_rows(result["rows"])
    if (study / "results.json").read_bytes() != before:
        raise ValueError("frozen result changed during analysis")
    report = {
        **analysis,
        "validation_results_sha256": verified["results_sha256"],
        "validation_seal_sha256": verified["seal_sha256"],
        "frozen_validation_verification": "pass",
        "native_loads_replayed": 0,
        "provider_calls": 0,
        "validation_mutated": False,
    }
    report["report_sha256"] = document_digest(report)
    publish_report(output, report)
    return report
