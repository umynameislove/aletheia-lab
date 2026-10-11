"""Independent raw-census replay and ordinary cost envelope for bounded module realization."""

from __future__ import annotations

import statistics
from collections import Counter
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from aletheia_lab.evaluation.cache_lifecycle_materialization import _verify_digest
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.module_realization_store import audit, read_records, recovered
from aletheia_lab.project.identity import content_sha256


def payload_digest(directory: Path) -> tuple[str, dict[str, int]]:
    names = ("evidence.sqlite", "evidence.sqlite-wal")
    files = {
        name: directory / name
        for name in names
        if (directory / name).exists()
        and (name == "evidence.sqlite" or (directory / name).stat().st_size)
    }
    if any(path.is_symlink() for path in files.values()):
        raise ValueError("evidence file symlink")
    digests = {name: content_sha256(path.read_bytes()) for name, path in files.items()}
    sizes = {name: path.stat().st_size for name, path in files.items()}
    return content_sha256(encode(digests).encode()), sizes


def _reference(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = read_records(path)
    loads = {row["load_id"]: row for row in rows if row["kind"] == "load"}
    predictions = {row["request_id"]: row for row in rows if row["kind"] == "predict"}
    if len(predictions) != sum(row["kind"] == "predict" for row in rows):
        raise ValueError("duplicate native request identity")
    return loads, predictions


def _frontier(client: list[dict[str, Any]]) -> dict[int, str]:
    frontier: dict[int, str] = {}
    for row in client:
        if row["kind"] != "response":
            continue
        for seq, digest in row["response"]["collector"]["durable_ack_digests"].items():
            index = int(seq)
            if index in frontier and frontier[index] != digest:
                raise ValueError("parent acknowledgement conflicts")
            frontier[index] = digest
    return frontier


def _census(
    client: list[dict[str, Any]], config: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    if not client or client[-1]["kind"] != "process_exit":
        raise ValueError("supervisor terminal missing")
    offers = [row for row in client if row["kind"] == "offer"]
    responses = [row for row in client if row["kind"] == "response"]
    if len(offers) != len(responses):
        raise ValueError("offered but unserved request remains in failed census")
    for offer, response in zip(offers, responses, strict=True):
        if offer["route"] != response["route"] or offer["value"] != response["value"]:
            raise ValueError("parent request-response binding differs")
    offered = [row["value"]["request_id"] for row in offers if row["route"] == "predict"]
    expected = [f"r-{index:03}" for index in range(3 * config["requests_per_stage"] + 1)]
    if offered != expected or [
        row["value"]["label"] for row in offers if row["route"] == "load"
    ] != [*config["order"], "C"]:
        raise ValueError("fixed lifecycle census differs")
    return responses, expected


def _client_intents(responses: list[dict[str, Any]]) -> dict[str, float]:
    resident: float | None = None
    result = {}
    for row in responses:
        if row["route"] == "load" and row["response"]["status"] == 200:
            resident = {"A": 1.0, "B": 2.0}[row["value"]["label"]]
        elif row["route"] == "predict":
            if resident is None:
                raise ValueError("client source history has no successful load")
            result[row["value"]["request_id"]] = resident
    return result


def _truth(
    responses: list[dict[str, Any]],
    loads: dict[str, Any],
    predictions: dict[str, Any],
    config: dict[str, Any],
) -> tuple[dict[str, str], Counter[str], Counter[str]]:
    truth: dict[str, str] = {}
    body: Counter[str] = Counter()
    native_history: Counter[str] = Counter()
    first_coefficient = 1.0 if config["order"][0] == "A" else 2.0
    intents = _client_intents(responses)
    for row in responses:
        if row["route"] != "predict":
            continue
        identity = row["value"]["request_id"]
        native = predictions[identity]
        load = loads[native["load_id"]]
        if row["response"]["body"]["y"] != native["y"] or row["value"]["x"] != native["x"]:
            raise ValueError("client delivery differs from independent native return")
        truth[identity] = (
            "compliant"
            if native["binding"]["coefficient"] == load["intended"]
            and native["binding"]["helper_sha256"] == load["expected_helper_sha256"]
            else "violation"
        )
        # Native comparator consumes parent inputs/status/body + known source,
        # never raw-reference load ID/selection for its expected generation.
        intended = intents[identity]
        numeric = (
            "compliant"
            if row["response"]["body"]["y"] == row["value"]["x"] * intended
            else "violation"
        )
        body[
            "unknown" if native["x"] == 0 else "correct" if numeric == truth[identity] else "false"
        ] += 1
        predicted_coefficient = (
            first_coefficient
            if config["variant"] == "collision" and config["repair"] == "none"
            else intended
        )
        history_answer = "compliant" if predicted_coefficient == intended else "violation"
        native_history["correct" if history_answer == truth[identity] else "false"] += 1
    return truth, body, native_history


def analyze(
    directory: Path, execution: dict[str, Any], *, signature: bool = True
) -> dict[str, Any]:
    client = read_records(directory.parent / f"{directory.name}-parent" / "client.jsonl")
    responses, expected = _census(client, execution["config"])
    loads, predictions = _reference(directory / "reference.jsonl")
    if set(predictions) != set(expected):
        raise ValueError("independent native reference missing; do not invent truth")
    started = perf_counter_ns()
    records = recovered(directory / "evidence.sqlite")
    replay = audit(records, expected)
    query_ns = perf_counter_ns() - started
    durable = {row["seq"]: content_sha256(encode(row).encode()) for row in records}
    frontier = _frontier(client)
    if any(durable.get(seq) != digest for seq, digest in frontier.items()):
        raise ValueError("acknowledged payload lost or changed after process exit")
    truth, body, native_history = _truth(responses, loads, predictions, execution["config"])
    answers: Counter[str] = Counter()
    for identity, result in replay["verdicts"].items():
        answers[
            "unknown"
            if result == "unknown"
            else "correct"
            if result == truth[identity]
            else "false"
        ] += 1
    digest, sizes = payload_digest(directory)
    if signature and records:
        _verify_digest(directory / "provenance", digest)
    latencies = {
        route: [row["elapsed_ns"] for row in responses if row["route"] == route]
        for route in ("load", "predict", "closure", "flush")
    }
    status = execution["last_response"].get("collector", {})
    return {
        "verification": "pass",
        "requests": len(expected),
        "violations": sum(value == "violation" for value in truth.values()),
        "numeric_changed": sum(
            predictions[k]["y"]
            != predictions[k]["x"] * loads[predictions[k]["load_id"]]["intended"]
            for k in truth
        ),
        "body_only": dict(body),
        "conditional_serial_source_history": dict(native_history),
        "actual_use_answers": dict(answers),
        "closure": replay["closure"],
        "acknowledged_records": len(frontier),
        "recovered_records": len(records),
        "all_acknowledged_recovered": True,
        "audit_fulfilled": replay["closure"] and answers["unknown"] == 0 and answers["false"] == 0,
        "collector": status,
        "physical_bytes": sizes,
        "query_ns": query_ns,
        "latency_ns": latencies,
        "early_pending_events": next(
            (
                row["response"]["collector"]["pending_events"]
                for row in responses
                if row["route"] == "closure"
            ),
            None,
        ),
        "native": next(
            (row["response"]["native"] for row in responses if row["route"] == "status"), {}
        ),
        "signature_bytes": sum(
            p.stat().st_size for p in (directory / "provenance").glob("*") if p.is_file()
        ),
    }


def aggregate(executions: list[dict[str, Any]], findings: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for execution, finding in zip(executions, findings, strict=True):
        config = execution["config"]
        key = "/".join(
            str(config[k])
            for k in ("slice", "variant", "repair", "evidence", "fault", "durability", "crash")
        )
        groups.setdefault(key, []).append(finding)
    output = {}
    for key, rows in groups.items():
        passed = [row for row in rows if row.get("verification") == "pass"]
        latencies = [x for row in passed for x in row["latency_ns"]["predict"]]
        output[key] = {
            "cells": len(rows),
            "verified_cells": len(passed),
            "requests": sum(row["requests"] for row in passed),
            "violations": sum(row["violations"] for row in passed),
            "correct": sum(row["actual_use_answers"].get("correct", 0) for row in passed),
            "unknown": sum(row["actual_use_answers"].get("unknown", 0) for row in passed),
            "false": sum(row["actual_use_answers"].get("false", 0) for row in passed),
            "fulfilled_cells": sum(row["audit_fulfilled"] for row in passed),
            "acknowledged_records": sum(row["acknowledged_records"] for row in passed),
            "median_predict_ms": statistics.median(latencies) / 1e6 if latencies else None,
            "mean_cell_database_bytes": statistics.mean(
                sum(row["physical_bytes"].values()) for row in passed
            )
            if passed
            else None,
        }
    return {
        "planned_cells": len(executions),
        "verified_cells": sum(row.get("verification") == "pass" for row in findings),
        "groups": output,
        "disposition": "bounded_external_mechanism_transfer_no_new_optimizer_admission",
        "limitations": "source-informed owned shared-process service; trusted host; common reference overhead; serial requests; process crash only; ordinary-candidate envelope, not global minimum",
    }
