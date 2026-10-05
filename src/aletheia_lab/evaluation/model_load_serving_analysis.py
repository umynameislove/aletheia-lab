"""Read-only, hash-bound replay of a fixed serving cost/audit experiment.

Three process repetitions on one machine are descriptive, not deployments.
Native/hash have no sufficient query service and cannot win the service frontier.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from typing import Any

from aletheia_lab.evaluation.model_load_serving_workload import decide


def quantile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def metrics(values: list[int]) -> dict[str, Any]:
    return {
        "n": len(values),
        "median_ns": statistics.median(values) if values else None,
        "p95_nearest_rank_ns": quantile(values, 0.95),
        "sum_ns": sum(values),
    }


def compare(decision: dict[str, Any], truth: dict[str, Any]) -> tuple[bool, bool]:
    correct = decision == truth
    false = (
        decision["verdict"] in ("compliant", "violation")
        and decision["verdict"] != truth["verdict"]
    ) or (
        decision["resident"] in ("compliant", "violation")
        and decision["resident"] != truth["resident"]
    )
    return correct, false


def validate_worker(worker: dict[str, Any], models: dict[str, Any]) -> None:
    config, rows = worker["config"], worker["rows"]
    ratio = config["inferences"]
    scopes = [
        scope
        for step in range(12)
        for scope in [f"load-{step}", *[f"slot-{step}-infer-{i}" for i in range(ratio)]]
    ]
    if [row["scope"] for row in rows] != scopes:
        raise ValueError("serving operation denominator changed")
    band = models["bands"][str(config["depth"])]
    generation, resident_truth = None, None
    for row in rows:
        _validate_row(row, config, band)
        if row["kind"] == "load" and row["status"] == 200:
            generation, resident_truth = row["scope"], row["truth"]["verdict"]
        if row["frame"]["generation"] != generation:
            raise ValueError("resident generation failed to preserve actual load binding")
        if row["kind"] == "infer" and row["truth"]["resident"] != resident_truth:
            raise ValueError("resident reference attribution changed")
    _validate_audits(worker)


def _validate_row(row: dict[str, Any], config: dict[str, Any], band: dict[str, Any]) -> None:
    frame, step = row["frame"], row["step"]
    failed = row["kind"] == "load" and step in (3, 7, 11)
    if row["status"] != (422 if failed else 200) or row["error"] is not None:
        raise ValueError(
            "unexpected runtime failure retained; worker not valid for matched frontier"
        )
    if row["kind"] == "load":
        expected = "A" if step % 2 == 0 else "B"
        delivered = "B" if step in (2, 6, 10) else expected
        truth = {
            "verdict": None if failed else "violation" if expected != delivered else "compliant",
            "eligibility": "no_new_load" if failed else "load",
            "resident": None,
        }
        if frame["expected"] != band[expected]["digest"] or row["truth"] != truth:
            raise ValueError("load reference/control schedule changed")
        observed = [] if failed or config["arm"] == "native" else [band[delivered]["digest"]]
    else:
        observed = []
        if row["truth"]["verdict"] is not None or row["truth"]["eligibility"] != "no_new_load":
            raise ValueError("resident request is not a new load")
    if frame["observed"] != observed or frame["count"] != len(observed):
        raise ValueError("descriptor reconstruction census changed")
    if [event["digest"] for event in row["events"]] != observed:
        raise ValueError("capture evidence differs from stored frame")
    if any(not event["completed"] for event in row["events"]):
        raise ValueError("incomplete reconstruction retained")
    if any(
        type(row[key]) is not int or row[key] < 0
        for key in ("rest_ns", "write_ns", "end_to_end_ns")
    ):
        raise ValueError("invalid measured duration")


def _validate_audits(worker: dict[str, Any]) -> None:
    rows = {row["scope"]: row for row in worker["rows"]}
    ratio = worker["config"]["inferences"]
    pairs = {
        (row["scope"], age)
        for row in rows.values()
        if row["kind"] == "load" or (ratio and row["scope"].endswith("infer-15"))
        for age in (0, 2, 8)
        if row["step"] + age < 12
    }
    if {(audit["scope"], audit["age"]) for audit in worker["audits"]} != pairs or len(
        worker["audits"]
    ) != len(pairs):
        raise ValueError("audit census changed or right-censoring hidden")
    for audit in worker["audits"]:
        if audit["truth"] != rows[audit["scope"]]["truth"]:
            raise ValueError("audit reference changed")
        snapshot = audit["snapshot"]
        if bool(snapshot is not None) != audit["available"]:
            raise ValueError("audit availability inconsistent")
        expected = (
            decide(snapshot["frame"], snapshot["parent_frame"])
            if snapshot
            else {"verdict": "unknown", "eligibility": "undetermined", "resident": None}
        )
        if snapshot and snapshot["frame"] != rows[audit["scope"]]["frame"]:
            raise ValueError("durable frame differs from occurrence frame")
        if (
            snapshot
            and snapshot["parent"] is not None
            and snapshot["parent_frame"] != rows[snapshot["parent"]]["frame"]
        ):
            raise ValueError("dependency frame does not bind original occurrence")
        if snapshot and snapshot["parent"] != (
            rows[audit["scope"]]["frame"]["generation"] if audit["kind"] == "infer" else None
        ):
            raise ValueError("inference parent does not bind actual resident generation")
        if audit["decision"] != expected:
            raise ValueError("checker replay differs")
    validate_retention(worker)


def validate_retention(worker: dict[str, Any]) -> None:
    """Independent set/ancestor replay, not a second SQLite execution."""
    retained: dict[str, tuple[int, str | None]] = {}
    horizon, arm = worker["config"]["horizon"], worker["config"]["arm"]
    for step in range(12):
        current = [row for row in worker["rows"] if row["step"] == step]
        active = current[-1]["frame"]["generation"]
        for row in current:
            retained[row["scope"]] = (
                step + horizon,
                row["frame"]["generation"] if row["kind"] == "infer" else None,
            )
        for audit in worker["audits"]:
            if (
                next(row["step"] for row in worker["rows"] if row["scope"] == audit["scope"])
                + audit["age"]
                == step
            ):
                available = arm in ("static", "full") and audit["scope"] in retained
                if available != audit["available"]:
                    raise ValueError("audit availability differs from independent retention replay")
        needed = {key for key, (expiry, _) in retained.items() if expiry >= step}
        if active is not None:
            needed.add(active)
        keep_dependencies(needed, retained)
        retained = {key: value for key, value in retained.items() if key in needed}


def keep_dependencies(needed: set[str], retained: dict[str, tuple[int, str | None]]) -> None:
    pending = list(needed)
    while pending:
        key = pending.pop()
        parent = retained[key][1]
        if parent is not None and parent not in needed:
            needed.add(parent)
            pending.append(parent)


def worker_summary(worker: dict[str, Any]) -> dict[str, Any]:
    config = worker["config"]
    if worker.get("status") != "complete":
        return {
            "config": config,
            "status": worker["status"],
            "failure": worker.get("error_type"),
            "operation_count": 12 * (1 + config["inferences"]),
            "native_census": "unknown_partial",
            "audit": {
                str(age): {
                    "denominator": count * (1 + bool(config["inferences"])),
                    "correct": 0,
                    "false": 0,
                    "unknown": count * (1 + bool(config["inferences"])),
                    "available": 0,
                }
                for age, count in ((0, 12), (2, 10), (8, 4))
            },
        }
    rows, audits = worker["rows"], worker["audits"]
    audit_groups: dict[str, Any] = {}
    for age in (0, 2, 8):
        values = [audit for audit in audits if audit["age"] == age]
        scored = [compare(audit["decision"], audit["truth"]) for audit in values]
        audit_groups[str(age)] = {
            "denominator": len(values),
            "correct": sum(c for c, _ in scored),
            "false": sum(f for _, f in scored),
            "unknown": sum(not c and not f for c, f in scored),
            "available": sum(audit["available"] for audit in values),
        }
    by_kind = {}
    for kind, selected in (
        ("successful_load", [r for r in rows if r["kind"] == "load" and r["status"] == 200]),
        ("failed_reload", [r for r in rows if r["kind"] == "load" and r["status"] != 200]),
        ("inference", [r for r in rows if r["kind"] == "infer"]),
    ):
        by_kind[kind] = {
            "rest": metrics([r["rest_ns"] for r in selected]),
            "end_to_end": metrics([r["end_to_end_ns"] for r in selected]),
        }
    events = [event for row in rows for event in row["events"]]
    audit_ns = sum(a["query_ns"] + a["verify_ns"] for a in audits)
    service_ns = (
        sum(r["end_to_end_ns"] for r in rows)
        + audit_ns
        + worker["retire_ns"]
        + worker["close_ns"]
        + worker["collector_init_ns"]
    )
    return {
        "config": config,
        "status": "complete",
        "operation_count": len(rows),
        "http_failures": sum(r["status"] != 200 for r in rows),
        "captured_reconstructions": len(events),
        "hashed_bytes": sum(e["bytes"] for e in events),
        "audit": audit_groups,
        "latency": by_kind,
        "service_ns": service_ns,
        "hash_ns": sum(e["hash_ns"] for e in events),
        "descriptor_read_ns": sum(e["read_ns"] for e in events),
        "write_ns": sum(r["write_ns"] for r in rows),
        "query_ns": sum(a["query_ns"] for a in audits),
        "verify_ns": sum(a["verify_ns"] for a in audits),
        **{
            key: worker[key]
            for key in (
                "setup_ns",
                "reference_ns",
                "retire_ns",
                "close_ns",
                "collector_init_ns",
                "monitor_ns",
                "cpu_ns",
                "peak_process_rss_bytes",
                "store",
            )
        },
    }


def analyze(workers: list[dict[str, Any]], models: dict[str, Any]) -> dict[str, Any]:
    summaries = []
    for worker in workers:
        if worker.get("status") == "complete":
            validate_worker(worker, models)
        summaries.append(worker_summary(worker))
    groups: dict[str, list[dict[str, Any]]] = {}
    for summary in summaries:
        cfg = summary["config"]
        key = f"d{cfg['depth']}-i{cfg['inferences']}-h{cfg['horizon']}-{cfg['arm']}"
        groups.setdefault(key, []).append(summary)
    cells: list[dict[str, Any]] = []
    for key, values in sorted(groups.items()):
        complete = [v for v in values if v["status"] == "complete"]
        cell = {
            "key": key,
            "config": values[0]["config"],
            "planned_repeats": len(values),
            "completed_repeats": len(complete),
            "summaries": values,
            "audit": {
                str(age): {
                    name: sum(v["audit"][str(age)][name] for v in values)
                    for name in ("denominator", "correct", "false", "unknown", "available")
                }
                for age in (0, 2, 8)
            },
        }
        if complete:
            cell["service_ns_median"] = statistics.median(v["service_ns"] for v in complete)
            cell["service_ns_range"] = [
                min(v["service_ns"] for v in complete),
                max(v["service_ns"] for v in complete),
            ]
        cells.append(cell)
    counts = Counter(w["status"] for w in workers)
    return {
        "worker_counts": dict(counts),
        "cells": cells,
        "frontier": frontier(cells),
        "source_cluster_count": 1,
        "concurrent_throughput_measured": False,
        "new_policy_implemented": False,
        "disposition": "bounded_static_service_frontier_no_new_method_admission",
    }


def frontier(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only compare same depth, ratio, full audit service and zero false verdicts."""
    results = []
    for depth in (2, 6, 10):
        for ratio in (0, 16):
            options = [
                c
                for c in cells
                if c["config"]["depth"] == depth
                and c["config"]["inferences"] == ratio
                and c["config"]["arm"] in ("static", "full")
            ]
            eligible = [
                c
                for c in options
                if c["completed_repeats"] == c["planned_repeats"]
                and all(
                    a["correct"] == a["denominator"] and a["false"] == 0
                    for a in c["audit"].values()
                )
            ]
            cheapest = min(eligible, key=lambda c: c["service_ns_median"]) if eligible else None
            results.append(
                {
                    "depth": depth,
                    "inferences": ratio,
                    "eligible_keys": [c["key"] for c in eligible],
                    "cheapest_measured_key": cheapest["key"] if cheapest else None,
                    "cost_ns_median": cheapest["service_ns_median"] if cheapest else None,
                    "claim": "descriptive measured minimum; three dependent same-machine process repetitions",
                }
            )
    return results
