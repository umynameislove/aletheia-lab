"""Same-service cost/coverage envelopes from immutable incident-audit development.

Each source arm has nested fresh-process repetitions, not independent deployments.
Logical quota, physical SQLite allocation and measured component latency are
distinct. All reports, including failures, remain part of the planned census.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any, cast

from aletheia_lab.evaluation.cache_lifecycle_study import read_sealed, seal
from aletheia_lab.evaluation.incident_audit_verification import verify
from aletheia_lab.project.identity import content_sha256

SERVICE_COUNTS = (
    "offered",
    "accepted",
    "refused",
    "on_time_correct",
    "deadline_misses",
    "accepted_but_unserved",
    "wrong",
)


def _offers(arm: dict[str, Any]) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], arm["prospective_admissions"] + arm["offers"])


def _on_time(item: dict[str, Any]) -> bool:
    return bool(
        item["queried_at"] <= item["deadline"]
        and item["query_wall_ns"] <= item["response_budget_ns"]
    )


def _refusal_class(item: dict[str, Any]) -> str:
    if item["kind"] == "preknown":
        return "prospective_admission_refused_cause_not_recorded"
    if any(answer is None for answer in item["answers"].values()):
        return "missing_or_unrecoverable_evidence"
    return "admission_rejected_with_present_closure"


def _scopes(items: list[dict[str, Any]], truth: dict[str, str]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in items:
        for scope, answer in item["answers"].items():
            counts["offered_scopes"] += 1
            counts["missing_scopes"] += int(answer is None)
            counts["unknown_scopes"] += int(answer == "unknown")
            counts["native_failure_scopes"] += int(truth[scope] == "unknown")
            counts["correct_conclusive_scopes"] += int(
                answer in {"compliant", "violation"} and answer == truth[scope]
            )
            counts["false_scopes"] += int(answer not in {None, "unknown", truth[scope]})
    return dict(counts)


def point(name: str, reports: list[dict[str, Any]], budgets: list[int]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    scope_counts: Counter[str] = Counter()
    refusal_evidence: Counter[str] = Counter()
    latencies: list[int] = []
    closed_bytes: list[int] = []
    secondary_bytes: list[int] = []
    peak_bytes: list[int] = []
    writes: list[int] = []
    recovery_reads: list[int] = []
    primary_peaks: list[int] = []
    primary_payloads: list[int] = []
    sensitivities: dict[str, Counter[str]] = {str(b): Counter() for b in budgets}
    full_equivalent = 0
    service_equivalent = True
    tier = next(
        (t for t in ("durable_secondary", "secondary_unavailable") if name.endswith(t)), "none"
    )
    for report in reports:
        arm = report["archives"][name]
        counts.update(arm["summary"])
        items = _offers(arm)
        for item in items:
            if not item["accepted"]:
                refusal_evidence[_refusal_class(item)] += 1
        truth = {row["frame"]["token"]: row["reference"] for row in report["rows"]}
        scope_counts.update(_scopes(items, truth))
        reference = {item["id"]: item for item in _offers(report["archives"]["full-reference"])}
        full_equivalent += sum(
            item["answers"] == reference[item["id"]]["answers"] for item in items
        )
        service_equivalent &= all(
            item["accepted"] == reference[item["id"]]["accepted"]
            and _on_time(item) == _on_time(reference[item["id"]])
            for item in items
        )
        reference_summary = report["archives"]["full-reference"]["summary"]
        service_equivalent &= all(
            arm["summary"].get(key, 0) == reference_summary.get(key, 0) for key in SERVICE_COUNTS
        )
        latencies.extend(item["query_wall_ns"] for item in items)
        closed_bytes.append(arm["closed_db_bytes"])
        primary_peaks.append(arm["storage"]["peak_charge"])
        primary_payloads.append(arm["storage"]["logical_charge"])
        peak_bytes.append(arm["storage"]["peak_db_wal_shm_bytes"])
        writes.append(arm["storage"]["write_ns"])
        recovery_reads.append(
            sum(item.get("recovery_meter", {}).get("read_ns", 0) for item in items)
        )
        secondary_bytes.append(report["secondary"]["closed_db_bytes"] if tier != "none" else 0)
        for budget in budgets:
            stats = sensitivities[str(budget)]
            for item in items:
                complete = all(
                    value in {"compliant", "violation"} and value == truth[scope]
                    for scope, value in item["answers"].items()
                )
                on_time = item["query_wall_ns"] <= budget and item["queried_at"] <= item["deadline"]
                stats["on_time_correct"] += int(complete and on_time)
                stats["deadline_misses"] += int(not on_time)
                stats["accepted_but_unserved"] += int(
                    item["accepted"] and not (complete and on_time)
                )
    return {
        "name": name,
        "recovery_tier": tier,
        "processes": len(reports),
        **dict(counts),
        "scope_census": dict(scope_counts),
        "refusal_evidence_class": dict(refusal_evidence),
        "full_frontier_matching_queries": full_equivalent,
        "same_full_frontier": full_equivalent == counts["offered"],
        "same_full_service": service_equivalent and full_equivalent == counts["offered"],
        "response_budget_sensitivity": {key: dict(v) for key, v in sensitivities.items()},
        "median_query_wall_ns": median(latencies),
        "maximum_query_wall_ns": max(latencies),
        "median_primary_closed_db_bytes": median(closed_bytes),
        "median_allocated_secondary_closed_db_bytes": median(secondary_bytes),
        "median_matched_tier_closed_db_bytes": median(
            [a + b for a, b in zip(closed_bytes, secondary_bytes, strict=True)]
        ),
        "median_primary_peak_db_wal_shm_bytes": median(peak_bytes),
        "median_primary_write_ns": median(writes),
        "median_secondary_retrieval_ns": median(recovery_reads),
        "median_primary_peak_charge": median(primary_peaks),
        "median_primary_final_charge": median(primary_payloads),
        "secondary_cost_contract": "full measured tier allocation charged to each enabled-tier candidate, per-arm measured read costs; one shared experimental writer, not per-policy serving overhead",
    }


def envelope(points: list[dict[str, Any]]) -> dict[str, Any]:
    sufficient = [p for p in points if p["same_full_service"] and p.get("wrong", 0) == 0]
    ordinary = [p for p in sufficient if p["name"] != "full-reference"]
    if not ordinary:
        return {"status": "no_tested_ordinary_matched_service_candidate"}
    cheapest = min(
        ordinary,
        key=lambda p: (p["median_matched_tier_closed_db_bytes"], p["median_query_wall_ns"]),
    )
    return {
        "status": "ordinary_candidate_matches_tested_full_service",
        "lowest_measured_closed_storage": cheapest["median_matched_tier_closed_db_bytes"],
        "candidate": cheapest["name"],
        "query_wall_ns": cheapest["median_query_wall_ns"],
        "offered": cheapest["offered"],
        "correct_complete": cheapest.get("correct_complete", 0),
        "accepted_but_unserved": cheapest.get("accepted_but_unserved", 0),
        "qualification": "same answers, per-obligation admission and measured response-budget disposition; full service still includes native failure/uncaptured unknown; closed-storage minimum among tested candidates, not all obligations fulfilled, physical quota or global cost optimum",
    }


def analyze(directory: Path) -> dict[str, Any]:
    verification = verify(directory / "code-snapshot", directory)
    plan, results = read_sealed(directory / "plan.json"), read_sealed(directory / "results.json")
    config = plan["protocol"]
    reports = [r for r in results["processes"] if r["status"] == "development_executed"]
    arms = {}
    for source_arm in config["source_arms"]:
        group = [r for r in reports if r["source_arm"] == source_arm]
        if not group:
            arms[source_arm] = {
                "status": "all_processes_failed",
                "planned": config["process_replicates"],
            }
            continue
        points = [
            point(name, group, config["response_budget_sensitivity_ns"])
            for name in sorted(group[0]["archives"])
        ]
        native = [row["native_ns"] for r in group for row in r["rows"]]
        capture = [row["capture_and_live_resolve_ns"] for r in group for row in r["rows"]]
        arms[source_arm] = {
            "points": points,
            "same_service_envelope": envelope(points),
            "native_call_median_ns": median(native),
            "capture_live_resolve_median_ns": median(capture),
            "source_load_ns": [r["source_load_ns"] for r in group],
            "peak_whole_process_rss_bytes": [r["memory"]["peak_process_rss_bytes"] for r in group],
            "unique_offered_audits": sum(
                len(_offers(r["archives"]["full-reference"])) for r in group
            ),
        }
    floors = [
        row["native_ns"]
        for r in results["processes"]
        if r["status"] == "native_control_executed"
        for row in r["rows"]
    ]
    return seal(
        {
            "schema": "incident-audit-cost-coverage-analysis/v1",
            "results_sha256": results["sha256"],
            "plan_sha256": plan["sha256"],
            "analysis_code_sha256": content_sha256(Path(__file__).read_bytes()),
            "verification": verification,
            "arms": arms,
            "native_floor_median_ns": median(floors) if floors else None,
            "A_disposition": "supports conditional session/dependency/capture/retention boundaries in source-informed development; unused operational candidate validation and field deployment remain open",
            "B_disposition": "NARROW: ordinary baselines meet the tested full captured frontier; no distinct method novelty or operational headroom validation",
            "limitations": [
                "one exposed runtime workflow; authored schedule/quotas/response budgets",
                "process repetitions nested, not population confidence intervals",
                "secondary allocation and primary component cost, not independently measured per-policy serving overhead",
                "observer witness instrumentation included in query latency",
                "no operator SLA, physical quota, adversarial host or power-loss guarantee",
            ],
            "provider_calls": 0,
        }
    )
