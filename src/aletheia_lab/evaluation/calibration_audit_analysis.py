"""Independent caller-history replay and bounded same-service synthesis."""

from __future__ import annotations

import math
from collections import Counter
from statistics import median
from typing import Any

from aletheia_lab.evaluation.calibration_audit_source import QUERIES
from aletheia_lab.evaluation.request_model_audit import digest


def history_reference(row: dict[str, Any]) -> dict[str, str]:
    """Rebuild from recorded fit arrays and caller snapshots, not capture verdicts.

    This does not call the adapter, archive resolver or native predictor. The
    caller and adapter share an honest process; independence is computational.
    """
    ledger = row["fit_call_ledger"]
    base = next(item for item in ledger if item["api"] == "base.fit")
    cal = [item for item in ledger if item["api"] == "calibrator.fit"][-1]
    packet = row["native_packets"][-1]
    bindings = {}
    for name, record in (("base", base), ("calibration", cal)):
        if not (len(record["members"]) == len(record["input"]) == len(record["labels"])):
            raise ValueError("fit census differs")
        bindings[name] = [
            {"id": identifier, "sha256": digest([features, label])}
            for identifier, features, label in zip(
                record["members"], record["input"], record["labels"], strict=True
            )
        ]
    if packet["membership"] != bindings or packet["approved_state"] != base["returned_state"]:
        raise ValueError("native capsule differs from actual fit history")
    call = packet["call"]
    if not call["closed"] or call["failed"] or call["output"] is None:
        return dict.fromkeys(QUERIES, "unknown")
    if (
        call["sigmoid"] != cal["returned_sigmoid"]
        or not call["state_stable"]
        or not call["held_base_is_enrolled"]
    ):
        raise ValueError("calibrator/delegation premise differs")
    actual = call["actual_state"]
    probabilities = []
    for features in call["input"]:
        margin = (
            sum(w * x for w, x in zip(actual["coef"][0], features, strict=True))
            + actual["intercept"][0]
        )
        z = -(call["sigmoid"]["a"] * margin + call["sigmoid"]["b"])
        positive = 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))
        probabilities.append([1 - positive, positive])
    arithmetic_ok = len(probabilities) == len(call["output"]) and all(
        len(observed) == 2
        and all(
            math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-12)
            for a, b in zip(expected, observed, strict=True)
        )
        for expected, observed in zip(probabilities, call["output"], strict=True)
    )
    if "native_probability_output" in call and any(
        not math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-12)
        for expected, observed in zip(probabilities, call["native_probability_output"], strict=True)
        for a, b in zip(expected, observed, strict=True)
    ):
        raise ValueError("recorded native arithmetic differs")
    shared_ids = {item["id"] for item in bindings["base"]} & {
        item["id"] for item in bindings["calibration"]
    }
    shared_rows = {item["sha256"] for item in bindings["base"]} & {
        item["sha256"] for item in bindings["calibration"]
    }
    return {
        "authorized_state": "compliant" if actual == base["returned_state"] else "violation",
        "disjoint_membership": "violation" if shared_ids or shared_rows else "compliant",
        "sigmoid_arithmetic": "compliant" if arithmetic_ok else "violation",
    }


def historical_membership(row: dict[str, Any]) -> str:
    """Fit-data separation remains answerable even when prediction fails."""
    records = row["fit_call_ledger"]
    base = next(item for item in records if item["api"] == "base.fit")
    cal = [item for item in records if item["api"] == "calibrator.fit"][-1]
    base_content = {digest([x, y]) for x, y in zip(base["input"], base["labels"], strict=True)}
    cal_content = {digest([x, y]) for x, y in zip(cal["input"], cal["labels"], strict=True)}
    return (
        "violation"
        if set(base["members"]) & set(cal["members"]) or base_content & cal_content
        else "compliant"
    )


def summarize(
    transfers: list[dict[str, Any]], costs: list[dict[str, Any]], controls: list[dict[str, Any]]
) -> dict[str, Any]:
    predictions = []
    for row in transfers:
        if row.get("status") == "worker_failure":
            predictions.append(
                {"seed": row["seed"], "status": "not_tested", "error_type": row["error_type"]}
            )
            continue
        for case in row["cases"]:
            reference = history_reference(case)
            if reference != case["ordinary_complete_history"]:
                raise ValueError("history replay differs")
            answers = case["captured_answers"]
            before, after = case["native_packets"]
            if case["same_entire_probability_output"] != (
                before["call"]["output"] == after["call"]["output"]
            ):
                raise ValueError("behavioral counterpair differs")
            false_conclusions = [
                query
                for query in QUERIES
                if answers[query] in {"compliant", "violation"}
                and answers[query] != reference[query]
            ]
            predictions.append(
                {
                    "seed": case["seed"],
                    "arm": case["arm"],
                    "forecast": case["forecast"],
                    "observed": answers,
                    "native_history": reference,
                    "historical_fit_disjoint": historical_membership(case),
                    "status": "supported" if answers == case["forecast"] else "contradicted",
                    "false_conclusions": false_conclusions,
                    "same_entire_probability_output": case["same_entire_probability_output"],
                }
            )
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in costs:
        key = f"{row['mode']}:{row['count']}:{row['deadline_ms']}"
        groups.setdefault(key, []).append(row)
    frontier = []
    for key, rows in groups.items():
        successful = [row for row in rows if row["status"] == "complete"]
        fulfilled = successful and all(
            row["service"]["complete"] == row["count"] for row in successful
        )
        frontier.append(
            {
                "configuration": key,
                "planned_processes": len(rows),
                "completed_processes": len(successful),
                "same_service_eligible": bool(
                    fulfilled
                    and len(successful) == len(rows)
                    and rows[0]["mode"] in {"raw", "compact", "whole"}
                ),
                "offered": sum(row["count"] for row in rows),
                "census": dict(sum((Counter(row.get("service", {})) for row in rows), Counter())),
                "median_workload_ms": median(row["workload_ns"] / 1e6 for row in successful)
                if successful
                else None,
                "median_closed_db_bytes": median(row["closed_storage_bytes"] for row in successful)
                if successful
                else None,
                "median_peak_db_wal_shm_bytes": median(
                    row["peak_storage_bytes"] for row in successful
                )
                if successful
                else None,
            }
        )
    return {
        "prediction_table": predictions,
        "prediction_status_counts": dict(Counter(row["status"] for row in predictions)),
        "frontier": frontier,
        "controls": [
            {
                key: value
                for key, value in control.items()
                if key
                not in {
                    "before_recovery",
                    "after_recovery",
                    "original_packet",
                    "fetched_packet",
                    "expected_packet",
                    "reopened_witness",
                }
            }
            for control in controls
        ],
        "false_conclusions": sum(len(row.get("false_conclusions", [])) for row in predictions)
        + sum(row.get("service", {}).get("wrong", 0) for row in costs),
        "interpretation": "new controlled lifecycle; computationally independent honest-host reference; no natural deployment, global optimum or algorithm-superiority claim",
        "paper_b": "retain ordinary baseline unless a same-service residual gap is measured; authored deadline sensitivity is not operator demand",
    }
