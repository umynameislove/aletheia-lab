"""Complete same-service census and a finite, descriptive cost frontier."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.serving_audit_cost_store import read_records
from aletheia_lab.evaluation.serving_audit_cost_workload import AUDIT_ARMS, audit_one, encoded
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256


def census(worker: dict[str, Any] | None, audit_arm: bool) -> dict[str, Any]:
    planned = 64 if audit_arm else 0
    if worker is None:
        return {
            "planned_offered": planned,
            "offered": None,
            "accepted": None,
            "acceptance_unknown": planned,
            "refused": None,
            "complete": 0,
            "unknown": planned,
            "conflict": 0,
            "incorrect": 0,
            "late": 0,
            "accepted_but_unserved": None,
            "qualified": False,
        }
    accepted = worker["accepted"] if audit_arm else 0
    audits = worker["audits"]
    outcomes = Counter(row["verdict"] for row in audits)
    complete = sum(row["verdict"] == "correct" and not row["late"] for row in audits)
    late = sum(row["late"] for row in audits)
    unknown = outcomes["unknown"] + max(0, accepted - len(audits))
    qualified = (
        audit_arm
        and worker["terminal"] == "complete"
        and worker["native_calls"] == 64
        and accepted == len(audits) == complete == 64
        and worker["refused"] == 0
        and len({row["request_id"] for row in audits}) == 64
        and worker["native_call_attempts"] == 64
        and len(worker["loads"]) == worker["planned_loads"]
    )
    return {
        "planned_offered": planned,
        "offered": accepted,
        "accepted": accepted,
        "acceptance_unknown": 0,
        "refused": worker["refused"],
        "complete": complete,
        "unknown": unknown,
        "conflict": outcomes["conflict"],
        "incorrect": outcomes["incorrect"],
        "late": late,
        "accepted_but_unserved": accepted - complete,
        "qualified": qualified,
    }


def _retained_check(directory: Path, worker: dict[str, Any], plan: dict[str, Any]) -> None:
    if worker["config"]["arm"] not in AUDIT_ARMS or worker["terminal"] != "complete":
        return
    caller = json.loads((directory / "caller-journal.json").read_bytes())
    calls = {row["request_id"]: row for row in caller["calls"]}
    loads = {row["generation_id"]: row for row in caller["loads"]}
    actual = {row["request_id"]: row for row in worker["audits"]}
    retained = read_records(directory / "store", worker["config"]["arm"])
    if len(retained) != len(calls) or len(actual) != len(calls):
        raise ValueError("retained complete census differs")
    for generation, record, operand, output, commit_ns in retained:
        key = record["request_id"]
        acknowledged = caller["acks"].get(key)
        reference = np.load(
            Path(plan["qualified_directory"])
            / f"outputs/qualification-torch-{record['input_id']}.npy",
            allow_pickle=False,
        )
        verdict, _ = audit_one(
            generation,
            record,
            operand,
            output,
            calls[key],
            loads[record["generation_id"]],
            commit_ns
            if acknowledged is not None and commit_ns is not None and acknowledged >= commit_ns
            else None,
            reference,
            plan["artifact"]["closure_sha256"],
        )
        row = actual[key]
        if (
            row["verdict"] != verdict
            or row["ack_ns"] != acknowledged
            or row["commit_return_ns"] != commit_ns
            or row["late"] != (row["audit_ns"] > record["deadline_ns"])
            or record["deadline_ns"] != record["offered_ns"] + plan["deadline_ns"]
        ):
            raise ValueError("retained verdict/ACK/deadline reconstruction differs")


def _vector(worker: dict[str, Any], common_bytes: int) -> dict[str, Any]:
    calls, loads = worker["calls"], worker["loads"]
    return {
        "workload_ns": worker["workload_through_final_ack_ns"],
        "end_to_end_ns": worker["end_to_end_service_ns"],
        "cpu_ns": worker["cpu_ns"],
        "load_ns": sum(row["load_ns"] for row in loads),
        "signature_including_hash_ns": sum(row["signature_including_hash_ns"] for row in loads),
        "hash_only_ns": sum(row["hash_only_ns"] for row in loads),
        "inference_ns": sum(row["inference_end_ns"] - row["inference_start_ns"] for row in calls),
        "serving_latencies_ns": [row["serving_latency_ns"] for row in calls],
        "capture_ns": sum(row.get("capture_ns", 0) for row in calls),
        "write_ns": sum(row.get("write_ns", 0) for row in calls),
        "write_ack_ns": sum(row.get("write_ack_ns", 0) for row in calls),
        "retrieval_ns": sum(row.get("store_retrieval_ns", 0) for row in worker["audits"]),
        "query_ns": sum(row["query_ns"] for row in worker["audits"]),
        "verify_ns": sum(row["verify_ns"] for row in worker["audits"]),
        "recovery_ns": sum(row.get("shared_recovery_ns", 0) for row in worker["audits"]),
        "store_closed_bytes": worker["closed_storage"]["logical_bytes"],
        "shared_recovery_bytes": common_bytes,
        "total_retained_bytes": worker["closed_storage"]["logical_bytes"] + common_bytes,
        "sampled_storage_peak": worker["sampled_storage_peak"],
        "peak_rss_bytes": worker["peak_rss_bytes"],
    }


def paired_cost(rows: list[dict[str, Any]], pattern: str) -> dict[str, Any]:
    selected = [row for row in rows if row["config"]["pattern"] == pattern]
    index = {(row["config"]["block"], row["config"]["arm"]): row for row in selected}
    values: list[int | None] = []
    for block in range(5):
        full, compact = index[(block, "full")], index[(block, "compact")]
        if not full["census"]["qualified"] or not compact["census"]["qualified"]:
            values.append(None)
        else:
            values.append(full["cost"]["workload_ns"] - compact["cost"]["workload_ns"])
    if any(value is None for value in values):
        return {
            "forecast": "unidentified",
            "reason": "paired service qualification failed",
            "paired_differences_ns": values,
        }
    difference = median(value for value in values if value is not None)
    return {
        "forecast": "supported" if difference <= 0 else "contradicted",
        "paired_differences_ns": values,
        "median_paired_difference_ns": difference,
        "estimand": "full minus compact workload through final ACK",
    }


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for pattern in ("reuse", "reload"):
        for arm in ("native", "hash_only", *AUDIT_ARMS):
            selected = [
                row
                for row in rows
                if row["config"]["pattern"] == pattern and row["config"]["arm"] == arm
            ]
            qualified = (
                arm in AUDIT_ARMS
                and len(selected) == 5
                and all(row["census"]["qualified"] for row in selected)
            )
            cost = [
                row["cost"]
                for row in selected
                if row.get("cost") is not None
                and row["returncode"] == 0
                and (arm not in AUDIT_ARMS or row["census"]["qualified"])
            ]
            metrics = {}
            for key in (
                "workload_ns",
                "end_to_end_ns",
                "total_retained_bytes",
                "store_closed_bytes",
                "peak_rss_bytes",
            ):
                values = [row[key] for row in cost if row[key] is not None]
                metrics[key] = (
                    {"median": median(values), "minimum": min(values), "maximum": max(values)}
                    if values
                    else None
                )
            result.append(
                {
                    "pattern": pattern,
                    "arm": arm,
                    "same_service_qualified": qualified,
                    "completed_workers": len(cost),
                    "metrics": metrics,
                }
            )
    return result


def rebuild(directory: Path, plan: dict[str, Any]) -> dict[str, Any]:
    execution = json.loads((directory / "execution.json").read_bytes())
    if (
        execution["plan_sha256"] != plan["plan_sha256"]
        or [row["config"] for row in execution["workers"]] != plan["configs"]
    ):
        raise ValueError("supervisor census differs from seal")
    rows = []
    for observation in execution["workers"]:
        config = observation["config"]
        worker_dir = directory / "workers" / config["worker_id"]
        path = worker_dir / "worker.json"
        worker = None
        if observation["worker_report_sha256"] is not None:
            if file_sha256(path) != observation["worker_report_sha256"]:
                raise ValueError("retained worker report changed")
            worker = json.loads(path.read_bytes())
            if worker["config"] != config:
                raise ValueError("worker configuration differs")
            _retained_check(worker_dir, worker, plan)
        row = {
            **observation,
            "census": census(worker, config["arm"] in AUDIT_ARMS),
            "cost": _vector(worker, plan["common_recovery_bytes"]) if worker is not None else None,
            "native_calls": worker["native_calls"] if worker else None,
            "native_attempts": worker["native_call_attempts"] if worker else None,
        }
        if observation["returncode"] != 0:
            row["census"]["qualified"] = False
        rows.append(row)
    summary = summarize(rows)
    service = [row for row in rows if row["config"]["arm"] in AUDIT_ARMS]
    keys = (
        "planned_offered",
        "offered",
        "accepted",
        "acceptance_unknown",
        "refused",
        "complete",
        "unknown",
        "conflict",
        "incorrect",
        "late",
        "accepted_but_unserved",
    )
    counts = {
        key: None
        if any(row["census"][key] is None for row in service)
        else sum(row["census"][key] for row in service)
        for key in keys
    }
    known_counts = {
        key: sum(row["census"][key] for row in service if row["census"][key] is not None)
        for key in keys
    }
    result = {
        "plan_sha256": plan["plan_sha256"],
        "planned_workers": 50,
        "planned_native_calls": 3200,
        "known_native_calls": sum(row["native_calls"] or 0 for row in rows),
        "missing_worker_reports": sum(row["native_calls"] is None for row in rows),
        "service_census": counts,
        "known_service_subtotals": known_counts,
        "workers": rows,
        "summary": summary,
        "forecasts": {pattern: paired_cost(rows, pattern) for pattern in ("reuse", "reload")},
        "limits": "one machine/model; fresh process not cold page cache; honest capture; no power-loss proof or global optimum",
    }
    result["analysis_sha256"] = content_sha256(encoded(result))
    return result


def analyze(directory: Path, plan: dict[str, Any]) -> dict[str, Any]:
    result = rebuild(directory, plan)
    write_new_file(directory / "analysis.json", encoded(result))
    return result
