"""Finite, source-conditioned evidence/repair forecasts on a new native runtime.

Reference arithmetic and attempt intervals are rebuilt from producer journals.
Collector receipt time is a different clocked fact. No missing terminal is
treated as proof of ongoing work, and no request refusal is a successful repair.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any

from aletheia_lab.project.identity import content_sha256

COEFFICIENTS = {"/a": 2.0, "/b": 3.0}


def validate_events(events: list[dict[str, Any]]) -> None:
    by_pid: dict[int, list[dict[str, Any]]] = {}
    for event in events:
        if type(event.get("pid")) is not int or type(event.get("time_ns")) is not int:
            raise ValueError("invalid producer identity/clock")
        by_pid.setdefault(event["pid"], []).append(event)
    for own in by_pid.values():
        own.sort(key=lambda item: item["sequence"])
        if [event["sequence"] for event in own] != list(range(len(own))):
            raise ValueError("producer sequence missing or duplicated")
        if any(a["time_ns"] > b["time_ns"] for a, b in zip(own, own[1:], strict=False)):
            raise ValueError("producer monotonic clock reversed")
    for event in events:
        if event["kind"] != "setup":
            continue
        raw = bytes.fromhex(event["artifact_hex"])
        params = json.loads(raw)
        if content_sha256(raw) != event["artifact_sha256"] or params != {
            "format": "owned-affine-json/v1",
            "coefficient": event["coefficient"],
            "intercept": event["intercept"],
        }:
            raise ValueError("owned numerical artifact not bound to actual setup")


def _own(events: list[dict[str, Any]], token: str, kind: str) -> list[dict[str, Any]]:
    return [event for event in events if event["kind"] == kind and event.get("token") == token]


def _transport(events: list[dict[str, Any]], token: str) -> list[dict[str, Any]]:
    submitted = _own(events, token, "submit")
    uids = {event["uid"] for event in submitted}
    return [event for event in events if event["kind"] == "transport" and event["uid"] in uids]


def _computation_check(
    row: dict[str, Any],
    entry: dict[str, Any],
    compute: dict[str, Any],
    events: list[dict[str, Any]],
) -> None:
    keys = ("pid", "token", "endpoint", "object_id", "batch", "slot", "x")
    if any(entry[key] != compute[key] for key in keys) or entry["x"] != row["x"]:
        raise ValueError("computation operand/attempt differs from ingress or entry")
    setups = [
        event
        for event in events
        if event["kind"] == "setup"
        and event["pid"] == entry["pid"]
        and event["object_id"] == entry["object_id"]
    ]
    if len(setups) != 1 or any(
        setups[0][key] != entry[key] for key in ("endpoint", "coefficient", "intercept")
    ):
        raise ValueError("actual entering object differs from owned setup")
    expected = entry["coefficient"] * compute["x"] + entry["intercept"]
    if not math.isclose(compute["output"], expected, abs_tol=1e-12, rel_tol=1e-12):
        raise ValueError("raw model arithmetic differs")


def reference(row: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Raw reference; intentionally independent of collector verdict functions."""
    token, cutoff = row["token"], row["end_ns"]
    entry = _own(events, token, "predict_enter")
    computed = _own(events, token, "computed")
    terminal = _own(events, token, "terminal")
    if len(entry) > 1 or len(computed) > 1 or len(terminal) > 1:
        raise ValueError("unexpected duplicate application attempt")
    submitted = _own(events, token, "submit")
    if "payload" in row and (
        len(submitted) != 1
        or submitted[0]["payload"] != row["payload"]
        or submitted[0]["endpoint"] != row["endpoint"]
    ):
        raise ValueError("native submission differs from offered raw payload")
    for compute in computed:
        if not entry:
            raise ValueError("computation not bound to entering object")
        _computation_check(row, entry[0], compute, events)
    if (
        entry
        and terminal
        and any(
            entry[0][key] != terminal[0][key]
            for key in ("pid", "object_id", "batch", "slot", "endpoint")
        )
    ):
        raise ValueError("terminal not bound to entering attempt")
    origin = "unavailable"
    if row["status"] == 200:
        value = row["body"].get("output") if type(row["body"]) is dict else None
        if not computed or value is None:
            origin = "unknown"
        else:
            origin = (
                "compliant"
                if (
                    math.isclose(value, computed[0]["output"], abs_tol=1e-12, rel_tol=1e-12)
                    and entry[0]["endpoint"] == row["endpoint"]
                    and entry[0]["coefficient"] == COEFFICIENTS[row["endpoint"]]
                )
                else "violation"
            )
    native = _transport(events, token)
    expired = any(
        item.get("response_data", {}).get("detail") == "Request timed out"
        for item in native
        if type(item.get("response_data")) is dict
    )
    at_cut, eventual = "unknown", "unknown"
    if entry and terminal:
        at_cut = "closed" if terminal[0]["time_ns"] <= cutoff else "open"
        eventual = "closed"
    elif not entry and expired:
        at_cut = "closed" if min(item["time_ns"] for item in native) <= cutoff else "unknown"
        eventual = "not_started"
    return {
        "origin": origin,
        "closure_at_client_cut": at_cut,
        "eventual": eventual,
        "entered": bool(entry),
        "computed": bool(computed),
        "application_outcome": terminal[0]["outcome"] if terminal else None,
        "terminal_ns": terminal[0]["time_ns"] if terminal else None,
    }


def native_uid(row: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, str]:
    """Strong ordinary native-source baseline; not merely response metadata."""
    transport = _transport(evidence, row["token"])
    submitted = _own(evidence, row["token"], "submit")
    origin = "unavailable" if row["status"] != 200 else "unknown"
    if len(submitted) == 1 and len(transport) == 1 and row["status"] == 200:
        message = transport[0]
        payload = submitted[0]["payload"]
        setup = [
            event
            for event in evidence
            if event["kind"] == "setup"
            and event["endpoint"] == row["endpoint"]
            and event["pid"] == message["pid"]
        ]
        if (
            len(setup) == 1
            and setup[0]["coefficient"] == COEFFICIENTS[row["endpoint"]]
            and message["endpoint"] == row["endpoint"]
            and submitted[0]["endpoint"] == row["endpoint"]
            and payload["x"] == row["x"]
            and message["response_data"] == row["body"]
        ):
            origin = "compliant"
        else:
            origin = "conflict"
    closure = "closed" if transport and transport[-1]["time_ns"] <= row["end_ns"] else "unknown"
    return {"origin": origin, "closure": closure}


def actual_use(row: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, str]:
    """Ordinary join over actual operands/terminal witnesses, without raw gold."""
    entries = _own(evidence, row["token"], "predict_enter")
    computed = _own(evidence, row["token"], "computed")
    terminals = _own(evidence, row["token"], "terminal")
    origin = "unavailable" if row["status"] != 200 else "unknown"
    if row["status"] == 200 and len(entries) == len(computed) == 1:
        entry, compute = entries[0], computed[0]
        if (
            entry["endpoint"] == row["endpoint"]
            and compute["output"] == row["body"].get("output")
            and entry["object_id"] == compute["object_id"]
        ):
            origin = "compliant"
        else:
            origin = "conflict"
    closure = "unknown"
    if len(terminals) == 1:
        closure = "closed" if terminals[0]["time_ns"] <= row["end_ns"] else "open"
    elif native_uid(row, evidence)["closure"] == "closed":
        closure = "closed"
    return {"origin": origin, "closure": closure}


def _scores(truth: list[str], answers: list[str]) -> dict[str, int]:
    pairs = list(zip(truth, answers, strict=True))
    return {
        "denominator": len(pairs),
        "correct": sum(a == b and a not in {"unknown", "unavailable"} for a, b in pairs),
        "false_conclusive": sum(b not in {"unknown", "unavailable"} and a != b for a, b in pairs),
        "unknown": sum(b == "unknown" for _, b in pairs),
        "unavailable": sum(b == "unavailable" for _, b in pairs),
    }


def analyze(
    rows: list[dict[str, Any]], events: list[dict[str, Any]], receipts: list[dict[str, Any]]
) -> dict[str, Any]:
    validate_events(events)
    if len({row["token"] for row in rows}) != len(rows):
        raise ValueError("duplicate offered request")
    keys = {(event["pid"], event["sequence"]): event for event in events}
    seen = set()
    for receipt in receipts:
        event = receipt["event"]
        key = event["pid"], event["sequence"]
        if key in seen or keys.get(key) != event or receipt["received_ns"] < event["time_ns"]:
            raise ValueError("collector receipt differs from producer event")
        seen.add(key)
    result = []
    for row in rows:
        truth = reference(row, events)
        timely = [r["event"] for r in receipts if r["received_ns"] <= row["end_ns"]]
        drained = [r["event"] for r in receipts]
        result.append(
            {
                "token": row["token"],
                "family": row["family"],
                "truth": truth,
                "first_query": {
                    "native_uid": native_uid(row, timely),
                    "actual_use": actual_use(row, timely),
                },
                "retrospective": {
                    "native_uid": native_uid(row, drained),
                    "ordinary_join": actual_use(row, drained),
                },
                "status": row["status"],
                "client_error": row["error"],
            }
        )
    summaries: dict[str, Any] = {}
    for horizon, names in (
        ("first_query", ("native_uid", "actual_use")),
        ("retrospective", ("native_uid", "ordinary_join")),
    ):
        summaries[horizon] = {}
        for name in names:
            summaries[horizon][name] = {
                "origin": _scores(
                    [r["truth"]["origin"] for r in result],
                    [r[horizon][name]["origin"] for r in result],
                ),
                "closure": _scores(
                    [r["truth"]["closure_at_client_cut"] for r in result],
                    [r[horizon][name]["closure"] for r in result],
                ),
            }
    return {
        "rows": result,
        "comparators": summaries,
        "http_status_counts": dict(Counter(str(row["status"]) for row in rows)),
        "origin_reference_counts": dict(Counter(r["truth"]["origin"] for r in result)),
        "closure_reference_counts": dict(
            Counter(r["truth"]["closure_at_client_cut"] for r in result)
        ),
        "application_outcomes": dict(
            Counter(str(r["truth"]["application_outcome"]) for r in result)
        ),
        "computed": sum(r["truth"]["computed"] for r in result),
        "entered": sum(r["truth"]["entered"] for r in result),
        "collateral_terminal_count": sum(
            e.get("collateral") is True for e in events if e["kind"] == "terminal"
        ),
        "collector_complete": len(keys) == len(seen),
        "producer_event_count": len(events),
        "receipt_count": len(receipts),
        "reference_boundary": "trusted producer hooks; second arithmetic/interval implementation, not independent host attestation",
    }


def forecasts(arm: str, delay: float, result: dict[str, Any]) -> dict[str, Any]:
    """Fixed pre-run predictions; contradictions reported, never exclude a cell."""
    rows = result["rows"]
    if not rows:
        return {"status": "unassessable", "checks": {}}
    queue = [r for r in rows if r["token"].startswith("queue-wait-")]
    abandon = [r for r in rows if r["token"].startswith("abandoned-")]
    checks = {
        "native_uid_origin_after_drain": all(
            r["retrospective"]["native_uid"]["origin"] == r["truth"]["origin"]
            for r in rows
            if r["truth"]["origin"] == "compliant"
        ),
        "actual_join_closure_after_drain": all(
            r["retrospective"]["ordinary_join"]["closure"] == r["truth"]["closure_at_client_cut"]
            for r in rows
            if r["truth"]["closure_at_client_cut"] != "unknown"
        ),
    }
    if arm == "native":
        checks["queued_rejection_before_prediction"] = len(queue) == 2 and all(
            r["status"] == 504 and not r["truth"]["entered"] for r in queue
        )
        checks["client_abandonment_is_not_attempt_closure"] = len(abandon) == 2 and all(
            r["client_error"] == "ReadTimeout" and r["truth"]["closure_at_client_cut"] == "open"
            for r in abandon
        )
    else:
        checks["cooperative_gate_closes_by_client_completion"] = len(abandon) == 2 and all(
            r["truth"]["application_outcome"] == "cancelled"
            and r["truth"]["closure_at_client_cut"] == "closed"
            for r in abandon
        )
        checks["cooperative_gate_is_refusal_not_success_gain"] = len(abandon) == 2 and all(
            r["truth"]["origin"] == "unavailable" and not r["truth"]["computed"] for r in abandon
        )
    if delay:
        checks["delayed_persistence_has_timely_evidence_gap"] = any(
            r["truth"]["origin"] == "compliant"
            and r["first_query"]["native_uid"]["origin"] == "unknown"
            for r in rows
        )
    return {
        "status": "assessed",
        "checks": checks,
        "supported": sum(checks.values()),
        "contradicted": sum(not v for v in checks.values()),
    }
