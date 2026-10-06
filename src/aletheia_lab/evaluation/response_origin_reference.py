"""Rebuild cache/computation correspondence from the raw native event tape.

No candidate resolver imports or artifact deserialization. Arithmetic, cache
insertion/lookup, object identity and native response operands are cross-checked.
This is a second implementation, not an independent hostile-host observation.
"""

from __future__ import annotations

import json
import math
from typing import Any

from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_audit import digest
from aletheia_lab.project.identity import content_sha256


def planned_tokens(workflow: str) -> list[str]:
    if workflow == "version_routes":
        return ["initial-load", "v1-first", "v1-repeat", "v2-same-body", "v2-zero", "v1-zero"]
    if workflow not in {"serial_replacement", "delayed_fill"}:
        raise ValueError("unsupported lifecycle")
    return [
        "initial-load",
        "same-generation",
        "old-inflight" if workflow == "delayed_fill" else "before-reload",
        "successful-reload",
        "fresh-after-reload",
        "equal-output-after-reload",
        "failed-reload",
        "fresh-after-failed-reload",
    ]


def operand(payload: dict[str, Any], field: str) -> list[Any]:
    values = payload[field]
    if len(values) != 1 or values[0]["shape"] not in ([1], [1, 1]):
        raise ValueError("bounded native tensor required")
    data = values[0]["data"]
    if not isinstance(data, list) or len(data) != 1:
        raise ValueError("one numeric tensor element required")
    return data


def _resident(source: dict[str, Any], actual: dict[str, Any]) -> None:
    fields = {"generation", "name", "version", "runtime_id", "object_id", "parameters", "pid"}
    load = source["loads"].get(actual.get("generation"))
    if set(actual) != fields or load is None or actual != {key: load[key] for key in fields}:
        raise ValueError("capture not bound to the observed resident object")


def _response_label(response: dict[str, Any], actual: dict[str, Any]) -> None:
    if (response.get("model_name"), response.get("model_version")) != (
        actual["name"],
        actual["version"],
    ):
        raise ValueError("native response labels disagree with producing object")


def _computations(source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    starts: dict[str, dict[str, Any]] = {}
    computed = {}
    for event in source["events"]:
        if event["kind"] == "compute_enter":
            identifier = event["identifier"]
            if identifier in starts:
                raise ValueError("duplicate computation identity")
            starts[identifier] = event
        if event["kind"] != "compute_return":
            continue
        entry = starts.get(event["identifier"])
        if (
            entry is None
            or entry["actual"] != event["actual"]
            or entry["token"] != event["token"]
            or event["identifier"] in computed
        ):
            raise ValueError("actual object changed within native predict")
        actual = event["actual"]
        _resident(source, actual)
        _response_label(event["response"], actual)
        x, y = operand(entry["payload"], "inputs"), operand(event["response"], "outputs")
        parameters = actual["parameters"]
        expected = float(x[0]) * parameters["coef"][0] + parameters["intercept"]
        if not math.isclose(float(y[0]), expected, abs_tol=1e-12, rel_tol=1e-12):
            raise ValueError("raw native output disagrees with captured numerical model")
        computed[event["identifier"]] = {
            "kind": "compute",
            "model": actual["name"],
            "version": actual["version"],
            "generation": actual["generation"],
            "input": digest(x),
            "output": digest(y),
            "fingerprint": digest(parameters),
            "closed": True,
        }
    return computed


def _row_responses(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        if "status" not in row:
            continue
        raw = row["raw_response"]
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            if row.get("response_decode_error") is not True or row.get("response") is not None:
                raise ValueError("unacknowledged native response decoding failure") from None
            continue
        if parsed != row.get("response") or row.get("response_decode_error"):
            raise ValueError("structured response differs from raw native bytes")


def _key_value(
    event: dict[str, Any], row: dict[str, Any], selected: dict[str, Any], arm: str
) -> None:
    try:
        original = json.loads(event["key"])
    except ValueError:
        raise ValueError("native cache key is not payload JSON") from None
    # Inputs are independently retained at ingress. Headers were captured only
    # inside the native key; this validates their shape, not independent wire bytes.
    if (
        not isinstance(original, dict)
        or set(original) != {"inputs", "parameters"}
        or original["inputs"] != row["body"]["inputs"]
        or not isinstance(original["parameters"], dict)
        or set(original["parameters"]) != {"headers"}
        or not isinstance(original["parameters"]["headers"], dict)
        or set(original["parameters"]["headers"])
        != {
            "host",
            "accept",
            "accept-encoding",
            "connection",
            "user-agent",
            "content-length",
            "content-type",
        }
    ):
        raise ValueError("native key differs from offered inputs or contains added identity")
    actual = selected["actual"]
    effective = event["key"]
    if arm in {"route_key", "fenced", "generation_key"}:
        prefix = [actual["name"], actual["version"]]
        if arm == "generation_key":
            prefix.append(actual["generation"])
        effective = encode([prefix, effective])
    if event["effective"] != effective:
        raise ValueError("cache repair key differs from selected resident namespace")
    if (
        event["kind"] == "insert"
        and arm == "fenced"
        and (
            event["entry_epoch"] != selected["epoch"]
            or event["accepted"] != (event["entry_epoch"] == event["current_epoch"])
        )
    ):
        raise ValueError("fenced insertion differs from selected epoch")


def _selected_keys(source: dict[str, Any]) -> None:
    rows = {row["token"]: row for row in source["rows"]}
    selected: dict[str, dict[str, Any]] = {}
    for event in source["events"]:
        kind, token = event["kind"], event["token"]
        if kind == "selected":
            _resident(source, event["actual"])
            if token in selected or token not in rows:
                raise ValueError("duplicate or unoffered selection")
            selected[token] = event
        elif kind in {"compute_enter", "compute_return"}:
            if token not in selected or event["actual"] != selected[token]["actual"]:
                raise ValueError("computation differs from request-selected object")
        elif kind in {"lookup", "insert"}:
            if token not in selected or source["arm"] == "disabled":
                raise ValueError("cache event lacks a lawful selection")
            _key_value(event, rows[token], selected[token], source["arm"])


def _cache_census(events: list[dict[str, Any]]) -> None:
    entries: dict[str, tuple[str, str]] = {}
    for event in events:
        if event["kind"] == "flush":
            entries.clear()
        elif event["kind"] == "insert" and event["accepted"]:
            entries[event["effective"]] = (event["value"], event["producer"])
        elif event["kind"] == "lookup":
            expected = entries.get(event["effective"])
            if event["hit"]:
                if expected != (event["value"], event["producer"]):
                    raise ValueError("cache hit has no matching accepted insertion")
            elif event["value"] or expected is not None:
                raise ValueError("cache miss contradicts native insertion census")


def check_source(source: dict[str, Any]) -> None:
    expected = planned_tokens(source["workflow"])
    rows, events = source["rows"], source["events"]
    if [row["token"] for row in rows] != expected[: len(rows)]:
        raise ValueError("offered native operation census differs")
    if source["terminal"] == "complete" and len(rows) != len(expected):
        raise ValueError("completed lifecycle has missing operations")
    if source["provider_calls"] != 0 or len(events) > 256:
        raise ValueError("native execution bound exceeded")
    if [event["sequence"] for event in events] != list(range(len(events))):
        raise ValueError("native event tape sequence differs")
    if any(a["time_ns"] > b["time_ns"] for a, b in zip(events, events[1:], strict=False)):
        raise ValueError("native event clock reversed")
    artifacts = source["artifacts"]
    for artifact in artifacts.values():
        raw = bytes.fromhex(artifact["raw_hex"])
        if not 0 < len(raw) <= 262144 or content_sha256(raw) != artifact["sha256"]:
            raise ValueError("owned artifact bytes changed")
    for load in source["loads"].values():
        if not any(
            load["artifact"] == value["sha256"] and load["parameters"] == value["parameters"]
            for value in artifacts.values()
        ):
            raise ValueError("resident load not bound to an owned artifact")
    _row_responses(rows)
    _selected_keys(source)
    _computations(source)
    _cache_census(events)


def _admit_row(
    row: dict[str, Any],
    events: list[dict[str, Any]],
    computed: dict[str, dict[str, Any]],
    contract: str,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    own = [event for event in events if event["token"] == row["token"]]
    selected = [event for event in own if event["kind"] == "selected"]
    hits = [event for event in own if event["kind"] == "lookup" and event["hit"]]
    direct = [event for event in own if event["kind"] == "compute_return"]
    if len(selected) > 1 or len(hits) > 1 or len(direct) > 1 or (hits and direct):
        raise ValueError("ambiguous native response correspondence")
    current = selected[0]["actual"]["generation"] if selected else None
    number = row["route"].split("/")[5]
    response = row.get("response") or {}
    success = row.get("status") == 200 and "outputs" in response
    x = operand(row["body"], "inputs")
    y = operand(response, "outputs") if success else None
    nodes = dict(computed)
    identifier = None
    producer: str | None = None
    if success and hits:
        hit = hits[0]
        producer = hit["producer"]
        cached = json.loads(hit["value"])
        if operand(cached, "outputs") != y or (
            cached.get("model_name"),
            cached.get("model_version"),
        ) != (response.get("model_name"), response.get("model_version")):
            raise ValueError("served operands differ from native cache value")
        identifier = f"cache-{hit['sequence']}"
        nodes[identifier] = {
            "kind": "cache",
            "producer": producer,
            "input": digest(x),
            "output": digest(y),
            "closed": True,
        }
    elif success and direct:
        producer = direct[0]["identifier"]
        identifier = producer
    if success and row.get("origin") != producer:
        raise ValueError("returned response has conflicting producer capture")
    root = {
        "token": row["token"],
        "model": "model",
        "version": number,
        "generation": current,
        "contract": contract,
        "input": digest(x),
        "output": digest(y) if success else None,
        "closed": "status" in row,
        "failed": not success,
        "origin": identifier,
    }
    actual = computed.get(producer) if producer is not None else None
    truth = "unknown"
    if success and actual is not None and current is not None:
        if (response.get("model_name"), response.get("model_version")) != (
            actual["model"],
            actual["version"],
        ):
            raise ValueError("served response labels disagree with producing computation")
        if actual["input"] != root["input"] or actual["output"] != root["output"]:
            raise ValueError("producer computation operands differ from response")
        compliant = actual["model"] == "model" and actual["version"] == number
        if contract == "selected_generation":
            compliant = compliant and actual["generation"] == current
        truth = "compliant" if compliant else "violation"
    native = {**row, **{key: root[key] for key in ("model", "version", "contract")}}
    return {"request": root, "nodes": nodes}, truth, native


def rebuild(source: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    check_source(source)
    computed = _computations(source)
    contract = "route" if source["workflow"] == "version_routes" else "selected_generation"
    frames, truth, native = [], [], []
    for row in source["rows"]:
        if not row["route"].endswith("/infer"):
            continue
        frame, status, observation = _admit_row(row, source["events"], computed, contract)
        frames.append(frame)
        truth.append(status)
        native.append(observation)
    return frames, truth, native


def bentoml_closure(source: dict[str, Any]) -> dict[str, Any]:
    """Offline probe replay: HTTP error completion does not certify worker exit."""
    if source["terminal"] != "complete" or source["pending_workers_at_finish"]:
        raise ValueError("native timeout prototype did not close its full census")
    events, requests = source["events"], source["requests"]
    if len(requests) != source["plan"]["planned_requests"]:
        raise ValueError("native timeout request census differs")
    results = []
    for row in requests:
        token = row["token"]
        own = [event for event in events if event.get("token") == token]
        entered = [event for event in own if event["kind"] == "worker_enter"]
        closed = [event for event in own if event["kind"] == "worker_closed"]
        if len(entered) != 1 or len(closed) != 1:
            raise ValueError("unresolved native worker correspondence")
        ingress = entered[0]["ingress_token"]
        http = [
            event
            for event in events
            if event["ingress_token"] == ingress and event["kind"] == "http_response_closed"
        ]
        if len(http) != 1:
            raise ValueError("unresolved native HTTP closure")
        for event in own:
            if event["kind"] == "model_computed":
                model = event["model"]
                if event["output"] != model["coef"] * event["input"] + model["intercept"]:
                    raise ValueError("timeout native numerical reference differs")
        results.append(
            {
                "case": row["case"],
                "status": row["status"],
                "http_before_worker_exit": http[0]["time_ns"] < closed[0]["time_ns"],
                "late_failure_observed": any(
                    event["kind"] == "worker_raise" and event["time_ns"] > http[0]["time_ns"]
                    for event in own
                ),
                "response_verdict": "compliant" if row["status"] == 200 else "unknown",
            }
        )
    return {
        "offered": len(requests),
        "cases": results,
        "scope": "one source-informed BentoML TCP prototype; not a repair test or independent incident",
    }
