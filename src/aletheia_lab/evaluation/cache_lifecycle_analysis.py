"""Independent arithmetic/state reference and same-access ordinary evidence joins.

Reference uses the raw application boundary. Audit joins read only physically
retained records. Both trust the host; neither is a hostile-host attestation.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path
from statistics import median
from typing import Any

from aletheia_lab.project.identity import content_sha256


def _one(events: list[dict[str, Any]], kind: str, token: str) -> dict[str, Any]:
    found = [event for event in events if event["kind"] == kind and event["token"] == token]
    if len(found) != 1:
        raise ValueError("expected one scoped boundary witness")
    return found[0]


def _loaded_objects(source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    loads: dict[str, dict[str, Any]] = {}
    for event in source["events"]:
        if event["kind"] != "load_return":
            continue
        artifact = source["artifacts"][event["artifact"]]
        raw = bytes.fromhex(artifact["raw_hex"])
        if not 0 < len(raw) <= 16384 or content_sha256(raw) != event["digest"]:
            raise ValueError("loaded owned bytes differ from actual binding")
        model = json.loads(raw)
        if (model["coefficient"], model["intercept"]) != (event["coefficient"], event["intercept"]):
            raise ValueError("object parameters do not match loaded JSON")
        if event["generation"] in loads:
            raise ValueError("duplicate generation load")
        loads[event["generation"]] = event
    return loads


def _computation(entry: dict[str, Any], event: dict[str, Any], load: dict[str, Any]) -> None:
    if any(entry[key] != event[key] for key in ("generation", "object_id", "digest", "x")):
        raise ValueError("computation changed its selected object or operand")
    if event["object_id"] != load["object_id"] or event["digest"] != load["digest"]:
        raise ValueError("computation not bound to actual loaded object")
    expected = float(event["x"]) * load["coefficient"] + load["intercept"]
    if not math.isclose(event["y"], expected, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError("raw numerical computation disagrees with independent arithmetic")


def _selection(event: dict[str, Any], published: str, loads: dict[str, dict[str, Any]]) -> None:
    load = loads[event["generation"]]
    if event["generation"] != published or event["object_id"] != load["object_id"]:
        raise ValueError("selection differs from published object")


def _chronology(
    events: list[dict[str, Any]], loads: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    published = "A"
    starts: dict[str, dict[str, Any]] = {}
    computations: dict[str, dict[str, Any]] = {}
    for event in events:
        kind = event["kind"]
        if kind == "publish":
            if event["previous_generation"] != published or event["generation"] not in loads:
                raise ValueError("publication state differs from native object chronology")
            published = event["generation"]
        elif kind == "load_failure":
            if event["preserved_generation"] != published:
                raise ValueError("failed reload changed published generation")
        elif kind == "select":
            _selection(event, published, loads)
        elif kind == "compute_enter":
            cid = f"compute-{event['sequence']}"
            starts[cid] = event
        elif kind == "compute_return":
            _computation(starts[event["cid"]], event, loads[event["generation"]])
            if event["cid"] in computations:
                raise ValueError("duplicate producer identity")
            computations[event["cid"]] = event
    return computations


def _response_reference(
    row: dict[str, Any], events: list[dict[str, Any]], computations: dict[str, dict[str, Any]]
) -> tuple[str, str | None, str]:
    if row.get("status") != 200:
        return "unknown", None, "unknown"
    selected = _one(events, "select", row["token"])
    returned = _one(events, "wrapper_return", row["token"])
    terminal = _one(events, "handler_terminal", row["token"])
    value = returned["returned"]
    computation = computations[value["cid"]]
    if any(value[key] != computation[key] for key in ("generation", "digest", "x", "y")):
        raise ValueError("actual wrapper return has no matching producer")
    if value["x"] != row["body"]["x"] or json.loads(row["raw_response"]) != {"y": value["y"]}:
        raise ValueError("delivered response bytes disagree with actual wrapper value")
    if returned["selected_generation"] != selected["generation"]:
        raise ValueError("wrapper request selection differs")
    if terminal["returned_producer"] != value or terminal["time_ns"] > row["completion_ns"]:
        raise ValueError("handler terminal differs or follows delivered completion")
    if selected["sequence"] >= returned["sequence"] or returned["sequence"] >= terminal["sequence"]:
        raise ValueError("operation boundary order differs")
    verdict = "compliant" if selected["generation"] == computation["generation"] else "violation"
    return verdict, value["cid"], "closed"


def reference(source: dict[str, Any]) -> dict[str, Any]:
    events, rows = source["events"], source["rows"]
    if len(events) > 2048 or [e["sequence"] for e in events] != list(range(len(events))):
        raise ValueError("raw event census is not bounded and contiguous")
    if any(a["time_ns"] > b["time_ns"] for a, b in zip(events, events[1:], strict=False)):
        raise ValueError("raw event chronology reverses")
    loads = _loaded_objects(source)
    computations = _chronology(events, loads)
    truth: dict[str, str] = {}
    producers: dict[str, str | None] = {}
    closed: dict[str, str] = {}
    inference_rows = [row for row in rows if row["route"] == "/infer"]
    expected_tokens = [
        "warm0",
        "repeat0",
        "old",
        "after_reload0",
        "new1",
        "repeat1",
        "after_failed2",
        "repeat2",
    ] + [f"steady-{i}" for i in range(64)]
    if [row["token"] for row in inference_rows] != expected_tokens[: len(inference_rows)]:
        raise ValueError("offered inference identity/order differs")
    if [row["offered_index"] for row in inference_rows] != list(range(len(inference_rows))):
        raise ValueError("offered inference census differs")
    for row in inference_rows:
        token = row["token"]
        truth[token], producers[token], closed[token] = _response_reference(
            row, events, computations
        )
    return {
        "truth": truth,
        "producers": producers,
        "closure": closed,
        "compute_count": len(computations),
        "loads": len(loads),
    }


def _join(
    values: list[dict[str, Any]],
    computes: dict[str, list[dict[str, Any]]],
    loads: dict[str, list[dict[str, Any]]],
    ends_by_token: dict[str, list[dict[str, Any]]],
    token: str,
) -> dict[str, Any]:
    unknown: dict[str, Any] = {"verdict": "unknown", "producer": None, "closure": "unknown"}
    if len(values) != 1:
        return {**unknown, "verdict": "conflict"}
    root = values[0]
    value = root["returned"]
    candidates = computes.get(value["cid"], [])
    bindings = loads.get(value["generation"], [])
    ends = ends_by_token.get(token, [])
    if len(candidates) > 1 or len(ends) > 1 or len(bindings) > 1:
        return {**unknown, "verdict": "conflict"}
    if not (candidates and ends and bindings):
        return unknown
    compute = candidates[0]
    if (
        any(value[key] != compute[key] for key in ("generation", "digest", "x", "y"))
        or ends[0]["returned_producer"] != value
        or bindings[0]["digest"] != compute["digest"]
    ):
        return {**unknown, "verdict": "conflict"}
    if root["sequence"] >= ends[0]["sequence"]:
        return unknown
    return {
        "verdict": "compliant"
        if root["selected_generation"] == compute["generation"]
        else "violation",
        "producer": value["cid"],
        "closure": "closed",
    }


def audit_answers(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Ordinary keyed join on retained records, never raw reference or caller truth."""
    computes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    loads: dict[str, list[dict[str, Any]]] = defaultdict(list)
    roots: dict[str, list[dict[str, Any]]] = defaultdict(list)
    terminals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record["kind"] == "load_return":
            loads[record["generation"]].append(record)
        elif record["kind"] == "compute_return":
            computes[record["cid"]].append(record)
        elif record["kind"] == "wrapper_return":
            roots[record["token"]].append(record)
        elif record["kind"] == "handler_terminal":
            terminals[record["token"]].append(record)
    return {
        token: _join(values, computes, loads, terminals, token) for token, values in roots.items()
    }


def retained_records(directory: Path, source: dict[str, Any]) -> list[dict[str, Any]]:
    path = directory / "audit.sqlite"
    if source["config"]["evidence"] == "none":
        if path.exists() or source["audit_query"]["records"]:
            raise ValueError("none mode exposed a materialized service")
        return []
    if path.is_symlink():
        raise ValueError("audit database must not be a symlink")
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        records = [
            json.loads(bytes(row[0]))
            for row in connection.execute("SELECT payload FROM audit ORDER BY sequence")
        ]
    finally:
        connection.close()
    expected = (
        source["events"]
        if source["config"]["evidence"] == "full"
        else [
            e
            for e in source["events"]
            if e["kind"]
            in {
                "load_return",
                "compute_return",
                "wrapper_return",
                "handler_terminal",
                "response",
                "publish",
                "load_failure",
            }
        ]
    )
    if records != expected or records != source["audit_query"]["records"]:
        raise ValueError("physical retained evidence differs from declared capture")
    return records


def analyze(source: dict[str, Any], directory: Path, protocol: dict[str, Any]) -> dict[str, Any]:
    raw = reference(source)
    records = retained_records(directory, source)
    answers = audit_answers(records)
    correct, false, missing = 0, 0, 0
    for token, verdict in raw["truth"].items():
        answer = answers.get(token, {"verdict": "unknown", "producer": None, "closure": "unknown"})
        observed = (answer["verdict"], answer["producer"], answer["closure"])
        expected_answer = (verdict, raw["producers"][token], raw["closure"][token])
        if answer["verdict"] in {"compliant", "violation"} and observed != expected_answer:
            false += 1
        if observed == expected_answer and verdict != "unknown":
            correct += 1
        else:
            missing += 1
    config = source["config"]
    inference_rows = [r for r in source["rows"] if r["route"] == "/infer"]
    reloads = [r for r in source["rows"] if r["route"] == "/reload"]
    violated = [token for token, truth in raw["truth"].items() if truth == "violation"]
    expected = protocol["predictions"]["violation_counts"][config["arm"]][config["order"]]
    failed = [e for e in source["events"] if e["kind"] == "load_failure"]
    reload_preserved = len(failed) == 1 and failed[0]["preserved_generation"] == "B"
    complete = (
        source["terminal"] == "complete"
        and len(inference_rows) == 72
        and len(reloads) == 2
        and [r["status"] for r in reloads] == [200, 400]
    )
    # Conditional native implications are separate from the named-producer service.
    conditional_correct = 0
    conditional_unknown = 0
    for row in inference_rows:
        token = row["token"]
        if row.get("status") != 200:
            conditional_unknown += 1
        elif config["arm"] in {"isolated", "generation_key"}:
            conditional_correct += int(raw["truth"][token] == "compliant")
        else:
            # Raw selected_generation is not native client telemetry. Do not use
            # that reference fact to create an uncharged numeric-body oracle.
            conditional_unknown += 1
    if records:
        from aletheia_lab.evaluation.litserve_evidence_provenance import verify_bundle

        verify_bundle(directory / "provenance")
        if source["signed_status"] != "pass" or source["signed_digest"] != content_sha256(
            (directory / "audit.sqlite").read_bytes()
        ):
            raise ValueError("signed database digest changed")
    measurements = source["measurements"]
    steady = [r["elapsed_ns"] for r in inference_rows if r["phase"] == "steady"]
    return {
        "verification": "pass",
        "complete": complete,
        "config": config,
        "offered_inferences": len(inference_rows),
        "offered_reloads": len(reloads),
        "truth_counts": dict(Counter(raw["truth"].values())),
        "violated_tokens": violated,
        "compute_count": raw["compute_count"],
        "forecasts": {
            "violation_count": complete and len(violated) == expected,
            "failed_reload_preserves_generation": complete and reload_preserved,
            "materialized_join": config["evidence"] == "none"
            or (complete and correct == 72 and false == 0),
        },
        "service": {
            "correct": correct,
            "false": false,
            "unknown_or_unserved": missing,
            "records": len(records),
            "conditional_native_correct": conditional_correct,
            "conditional_native_unknown": conditional_unknown,
        },
        "measurements": measurements,
        "steady_latency_median_ns": median(steady) if steady else None,
        "physical_bytes_live": source["audit_query"]["physical_bytes"],
        "logical_bytes": source["audit_query"]["logical_bytes"],
        "physical_bytes_closed": (directory / "audit.sqlite").stat().st_size if records else 0,
        "commit_count": source["audit_query"]["commit_batches"],
        "signed_status": source["signed_status"],
    }


def aggregate(
    plan: dict[str, Any],
    executions: list[dict[str, Any]],
    findings: Sequence[dict[str, Any] | None],
) -> dict[str, Any]:
    if len(executions) != len(plan["cells"]) or len(findings) != len(executions):
        raise ValueError("all planned cells must remain in the analysis")
    failures: list[int] = []
    forecast_errors: list[dict[str, Any]] = []
    by_mode: dict[str, dict[str, Any]] = {}
    costs: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    planned_groups: Counter[tuple[str, str]] = Counter()
    successful_groups: Counter[tuple[str, str]] = Counter()
    for index, (execution, finding) in enumerate(zip(executions, findings, strict=True)):
        mode = execution["config"]["evidence"]
        group = (execution["config"].get("arm", "missing"), mode)
        planned_groups[group] += 1
        item = by_mode.setdefault(
            mode,
            {
                "planned_inferences": 0,
                "correct_materialized": 0,
                "false": 0,
                "unknown_or_unserved": 0,
                "violations": 0,
                "cells_complete": 0,
            },
        )
        item["planned_inferences"] += 72
        if finding is None or finding.get("verification") != "pass":
            failures.append(index)
            item["unknown_or_unserved"] += 72
            continue
        service = finding["service"]
        item["correct_materialized"] += service["correct"]
        item["false"] += service["false"]
        item["unknown_or_unserved"] += (
            service["unknown_or_unserved"] + 72 - finding["offered_inferences"]
        )
        item["violations"] += finding["truth_counts"].get("violation", 0)
        item["cells_complete"] += int(finding["complete"])
        if execution["returncode"] != 0 or not finding["complete"]:
            failures.append(index)
        else:
            successful_groups[group] += 1
        forecast_errors.extend(
            {"cell": index, "prediction": name}
            for name, matched in finding["forecasts"].items()
            if not matched
        )
        costs[group].append(finding)
    frontier = []
    for (arm, mode), values in sorted(costs.items()):
        latencies = [
            v["steady_latency_median_ns"]
            for v in values
            if v["steady_latency_median_ns"] is not None
        ]
        frontier.append(
            {
                "arm": arm,
                "evidence": mode,
                "cells": len(values),
                "planned_cells": planned_groups[(arm, mode)],
                "complete_same_service": successful_groups[(arm, mode)]
                == planned_groups[(arm, mode)]
                and all(
                    v["complete"] and v["service"]["correct"] == 72 and v["service"]["false"] == 0
                    for v in values
                ),
                "steady_latency_median_ns": median(latencies) if latencies else None,
                "steady_latency_range_ns": [min(latencies), max(latencies)] if latencies else None,
                "physical_live_median_bytes": median(v["physical_bytes_live"] for v in values),
                "physical_closed_median_bytes": median(v["physical_bytes_closed"] for v in values),
                "logical_median_bytes": median(v["logical_bytes"] for v in values),
                "cost_medians_ns": {
                    key: median(v["measurements"][key] for v in values)
                    for key in (
                        "capture_ns",
                        "persist_ns",
                        "query_ns",
                        "hash_ns",
                        "process_cpu_ns",
                        "sign_verify_ns",
                    )
                },
            }
        )
    return {
        "planned_cells": len(executions),
        "planned_inferences": plan["planned_inferences"],
        "planned_reload_operations": plan["planned_reload_operations"],
        "failed_or_incomplete_cells": failures,
        "prediction_errors": forecast_errors,
        "by_evidence": by_mode,
        "bounded_cost_candidates": frontier,
        "provider_calls": 0,
        "protected_runs": 0,
        "disposition": "bounded_transfer_supported"
        if not failures and not forecast_errors
        else "narrow_or_incomplete",
        "scope": "One authored application, previously unused cache implementation, nested repeats. Cheapest measured candidates only; common raw reference hooks remain present; no novel algorithm, production minimum or host attestation.",
    }
