"""Conditional generation compliance from HTTP clients and known source contracts.

The cache-history tier assumes the tested scalar-key LRU8/clear semantics,
immutable affine residents, a complete client census, initially empty caches and
no background inference. The driver tier additionally assumes reload dispatch
waits for the sole overlapping old computation to enter with A. These are source
and control premises, not observed selection witnesses. This endpoint returns no
actual-use certificate, named producer, or materialized handler closure.
"""

from __future__ import annotations

import json
import math
from typing import Any

ARMS = frozenset({"input_key", "clear", "generation_key", "isolated"})


def client_projection(source: dict[str, Any]) -> dict[str, Any]:
    """Copy only public HTTP intervals, operands and known affine parameters."""
    return {
        "arm": source["config"]["arm"],
        "parameters": {
            generation: {
                "coefficient": source["artifacts"][generation]["coefficient"],
                "intercept": source["artifacts"][generation]["intercept"],
            }
            for generation in ("A", "B")
        },
        "rows": [
            {
                "route": row["route"],
                "body": {key: row["body"][key] for key in ("x", "artifact") if key in row["body"]},
                "status": row["status"],
                "raw_response": row["raw_response"],
                "offered_ns": row["completion_ns"] - row["elapsed_ns"],
                "completion_ns": row["completion_ns"],
            }
            for row in source["rows"]
        ],
    }


def _body(row: dict[str, Any]) -> Any:
    try:
        return json.loads(row["raw_response"])
    except (ValueError, TypeError, KeyError):
        return None


def _selected(row: dict[str, Any], reload: dict[str, Any]) -> set[str]:
    if row["completion_ns"] < reload["offered_ns"]:
        return {"A"}
    if row["offered_ns"] > reload["completion_ns"]:
        return {"B"}
    return {"A", "B"}


def _valid(client: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    try:
        parameters = client["parameters"]
        values = [parameters[g][key] for g in ("A", "B") for key in ("coefficient", "intercept")]
        intervals = [row[key] for row in rows for key in ("offered_ns", "completion_ns")]
        return (
            client["arm"] in ARMS
            and all(type(value) in (int, float) and math.isfinite(value) for value in values)
            and all(type(value) is int for value in intervals)
            and all(row["offered_ns"] <= row["completion_ns"] for row in rows)
            and all(_valid_operand(row) for row in rows)
        )
    except (KeyError, TypeError, ValueError):
        return False


def _valid_operand(row: dict[str, Any]) -> bool:
    if row.get("route") != "/infer":
        return True
    body = row.get("body")
    return (
        isinstance(body, dict) and type(body.get("x")) in (int, float) and math.isfinite(body["x"])
    )


def _producers(client: dict[str, Any], row: dict[str, Any]) -> set[str] | None:
    x = row.get("body", {}).get("x")
    if type(x) not in (int, float) or not math.isfinite(x):
        return None
    body = _body(row)
    if not isinstance(body, dict) or set(body) != {"y"} or type(body["y"]) not in (int, float):
        return None
    parameters = client["parameters"]
    return {
        generation
        for generation in ("A", "B")
        if body["y"]
        == parameters[generation]["coefficient"] * x + parameters[generation]["intercept"]
    }


def _zero_history(
    client: dict[str, Any],
    row: dict[str, Any],
    reload: dict[str, Any],
    inference: list[dict[str, Any]],
    selected: set[str],
    producers: set[str],
) -> set[str]:
    if row["body"]["x"] != 0:
        return producers
    keys = [other.get("body", {}).get("x") for other in inference]
    if any(type(key) not in (int, float) for key in keys) or len(set(keys)) > 8:
        return producers
    overlapping = [
        other
        for other in inference
        if other is not row
        and other["body"]["x"] == 0
        and other["offered_ns"] <= row["completion_ns"]
        and other["completion_ns"] >= row["offered_ns"]
    ]
    if overlapping:
        return producers
    if selected == {"A"}:
        return producers & {"A"}
    if selected != {"B"}:
        return producers
    if client["arm"] == "clear":
        return producers if _spans_reload(row, reload, inference) else producers & {"B"}
    warmed = any(
        other["body"]["x"] == 0
        and other["status"] == 200
        and other["completion_ns"] < reload["offered_ns"]
        for other in inference
    )
    return producers & {"A"} if warmed else producers


def _spans_reload(
    row: dict[str, Any], reload: dict[str, Any], inference: list[dict[str, Any]]
) -> bool:
    return any(
        other["body"]["x"] == row["body"]["x"]
        and other["offered_ns"] <= reload["completion_ns"]
        and other["completion_ns"] >= reload["offered_ns"]
        for other in inference
    )


def _answer(
    client: dict[str, Any],
    row: dict[str, Any],
    reload: dict[str, Any],
    inference: list[dict[str, Any]],
    cache_history: bool,
    barrier_row: dict[str, Any] | None,
) -> str:
    if row.get("status") != 200:
        return "unknown"
    producers = _producers(client, row)
    if producers is None:
        return "unknown"
    if not producers:
        return "conflict"
    selected = {"A"} if row is barrier_row else _selected(row, reload)
    if client["arm"] in {"generation_key", "isolated"} or (
        cache_history and _cold_uncontended(row, inference)
    ):
        # Namespace repairs bind a cached result to the captured generation.
        # An initially cold, uncontended key returns this call's own producer.
        # Neither premise may override an incompatible numeric body.
        return "compliant" if producers & selected else "conflict"
    if cache_history:
        producers = _zero_history(client, row, reload, inference, selected, producers)
    if not producers:
        return "conflict"
    verdicts = {
        "compliant" if generation == producer else "violation"
        for generation in selected
        for producer in producers
    }
    return next(iter(verdicts)) if len(verdicts) == 1 else "unknown"


def _cold_uncontended(row: dict[str, Any], inference: list[dict[str, Any]]) -> bool:
    return not any(
        other is not row
        and other["body"]["x"] == row["body"]["x"]
        and other["offered_ns"] <= row["completion_ns"]
        for other in inference
    )


def infer_client_contract(
    client: dict[str, Any],
    *,
    cache_history: bool = False,
    driver_barrier: bool = False,
) -> list[str]:
    """Return generation verdicts only, with optional explicit source premises."""
    rows = client.get("rows", [])
    inference = [row for row in rows if row.get("route") == "/infer"]
    unknown = ["unknown"] * len(inference)
    if not _valid(client, rows):
        return unknown
    successful = [row for row in rows if row.get("route") == "/reload" and row.get("status") == 200]
    if len(successful) != 1 or _body(successful[0]) != {"loaded_generation": "B"}:
        return unknown
    reload = successful[0]
    overlapping = [row for row in inference if _selected(row, reload) == {"A", "B"}]
    barrier_row = None
    if (
        driver_barrier
        and len(overlapping) == 1
        and overlapping[0]["offered_ns"] < reload["offered_ns"]
    ):
        barrier_row = overlapping[0]
    return [
        _answer(client, row, reload, inference, cache_history, barrier_row) for row in inference
    ]
