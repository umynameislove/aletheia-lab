"""Query-specific ordinary certificates over one admitted cell's trusted records.

A terminal already copies the actual returned producer. A separate wrapper
record is corroboration, not intrinsically necessary evidence for this service.
These are trace-consistency checks, not authenticity or a hostile-host proof.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

UNKNOWN: dict[str, Any] = {
    "verdict": "unknown",
    "producer": None,
    "closure": "unknown",
    "delivery": "unknown",
}
FIELDS = {
    "load_return": ("generation", "object_id", "digest"),
    "compute_return": ("cid", "generation", "object_id", "digest", "x", "y"),
    "handler_terminal": (
        "selected_generation",
        "selected_object_id",
        "returned_producer",
        "status",
        "raw_response",
    ),
    "response": ("route", "body", "status", "raw_response"),
    "publish": ("previous_generation", "generation", "object_id", "digest", "clear"),
    "load_failure": ("artifact", "error_type", "preserved_generation"),
}
VALUE_FIELDS = ("cid", "generation", "digest", "x", "y")


def project_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ordinary field projection; no raw reference or verdict is materialized."""
    result = []
    for record in records:
        kind = record["kind"]
        if kind not in FIELDS:
            continue
        fields = ("kind", "sequence", "time_ns", "token", *FIELDS[kind])
        value = {key: record[key] for key in fields}
        if kind == "handler_terminal":
            value["returned_producer"] = {
                key: record["returned_producer"][key] for key in VALUE_FIELDS
            }
        result.append(value)
    return result


def _index(records: list[dict[str, Any]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    index: dict[str, dict[str, list[dict[str, Any]]]] = {
        kind: defaultdict(list) for kind in (*FIELDS, "wrapper_return")
    }
    for row in records:
        kind = row["kind"]
        if kind not in index:
            continue
        key = row["generation"] if kind == "load_return" else row.get("cid", row["token"])
        index[kind][key].append(row)
    return index


def _ordered(*records: dict[str, Any]) -> bool:
    return all(
        first["sequence"] < second["sequence"] and first["time_ns"] <= second["time_ns"]
        for first, second in zip(records, records[1:], strict=False)
    )


def _producer_consistent(
    terminal: dict[str, Any],
    compute: dict[str, Any],
    load: dict[str, Any],
    selected: dict[str, Any],
) -> bool:
    value = terminal["returned_producer"]
    return (
        all(value[key] == compute[key] for key in VALUE_FIELDS)
        and (compute["object_id"], compute["digest"]) == (load["object_id"], load["digest"])
        and terminal["selected_object_id"] == selected["object_id"]
        and _ordered(load, compute, terminal)
        and _ordered(selected, terminal)
        and terminal["status"] == 200
        and json.loads(terminal["raw_response"]) == {"y": value["y"]}
    )


def _corroborated(terminal: dict[str, Any], roots: list[dict[str, Any]]) -> bool:
    if not roots:
        return True
    if len(roots) != 1:
        return False
    root = roots[0]
    return (
        root["returned"] == terminal["returned_producer"]
        and root["selected_generation"] == terminal["selected_generation"]
        and root["selected_object_id"] == terminal["selected_object_id"]
        and _ordered(root, terminal)
    )


def _delivery(terminal: dict[str, Any], responses: list[dict[str, Any]]) -> str:
    if not responses:
        return "unknown"
    if len(responses) != 1:
        return "conflict"
    response = responses[0]
    if response["status"] != 200:
        return "unknown"
    return (
        "observed"
        if (
            _ordered(terminal, response)
            and response["route"] == "/infer"
            and response["body"]["x"] == terminal["returned_producer"]["x"]
            and json.loads(response["raw_response"]) == json.loads(terminal["raw_response"])
        )
        else "conflict"
    )


def _answer(token: str, index: dict[str, dict[str, list[dict[str, Any]]]]) -> dict[str, Any]:
    terminals = index["handler_terminal"].get(token, [])
    if len(terminals) != 1:
        return {**UNKNOWN, "verdict": "conflict" if terminals else "unknown"}
    terminal = terminals[0]
    producer = terminal["returned_producer"]
    candidates = (
        index["compute_return"].get(producer["cid"], []),
        index["load_return"].get(producer["generation"], []),
        index["load_return"].get(terminal["selected_generation"], []),
    )
    if any(len(values) > 1 for values in candidates):
        return {**UNKNOWN, "verdict": "conflict"}
    if any(not values for values in candidates):
        return dict(UNKNOWN)
    compute, load, selected = [values[0] for values in candidates]
    if not _producer_consistent(terminal, compute, load, selected) or not _corroborated(
        terminal, index["wrapper_return"].get(token, [])
    ):
        return {**UNKNOWN, "verdict": "conflict"}
    return {
        "verdict": "compliant"
        if producer["generation"] == terminal["selected_generation"]
        else "violation",
        "producer": producer["cid"],
        "closure": "closed",
        "delivery": _delivery(terminal, index["response"].get(token, [])),
    }


def certificate_answers(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Local-cell IDs; cross-request producer references are intentionally legal."""
    try:
        index = _index(records)
        tokens = (
            set(index["handler_terminal"]) | set(index["wrapper_return"]) | set(index["response"])
        )
        return {token: _answer(token, index) for token in sorted(tokens)}
    except (KeyError, TypeError, ValueError):
        # Invalid shape admits no conclusive certificate, without guessing a key.
        return {}


def _resident_chronology(index: dict[str, dict[str, list[dict[str, Any]]]]) -> None:
    loads = [row for group in index["load_return"].values() for row in group]
    if not loads or any(len(group) != 1 for group in index["load_return"].values()):
        raise ValueError("unique startup/load bindings unavailable")
    previous = min(loads, key=lambda row: row["sequence"])
    resident = previous["generation"]
    transitions = [
        row
        for kind in ("publish", "load_failure")
        for group in index[kind].values()
        for row in group
    ]
    for event in sorted(transitions, key=lambda row: row["sequence"]):
        if not _ordered(previous, event):
            raise ValueError("resident transition chronology conflicts")
        if event["kind"] == "publish":
            if event["previous_generation"] != resident:
                raise ValueError("publication changed the wrong resident")
            resident = event["generation"]
        elif event["preserved_generation"] != resident:
            raise ValueError("failed construction did not preserve resident")
        previous = event


def lifecycle_answers(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Preserve charged reload offer/publication/failure queries as a separate ledger."""
    index = _index(records)
    _resident_chronology(index)
    results = {}
    for token, responses in index["response"].items():
        reloads = [row for row in responses if row["route"] == "/reload"]
        if not reloads:
            continue
        if len(reloads) != 1:
            raise ValueError("reload response identity conflict")
        row = reloads[0]
        candidate = row["body"]["artifact"]
        if row["status"] == 200:
            publication = index["publish"].get(token, [])
            if len(publication) != 1:
                raise ValueError("reload publication witness unavailable")
            event = publication[0]
            loaded = index["load_return"].get(event["generation"], [])
            if (
                len(loaded) != 1
                or not _ordered(loaded[0], event, row)
                or (loaded[0]["object_id"], loaded[0]["digest"])
                != (event["object_id"], event["digest"])
            ):
                raise ValueError("reload candidate not bound to actual published object")
            if json.loads(row["raw_response"]) != {"loaded_generation": event["generation"]}:
                raise ValueError("reload response differs from publication")
            results[token] = {
                "candidate": candidate,
                "status": 200,
                "resident": event["generation"],
            }
        elif row["status"] == 400:
            failure = index["load_failure"].get(token, [])
            if (
                len(failure) != 1
                or not _ordered(failure[0], row)
                or failure[0]["artifact"] != candidate
            ):
                raise ValueError("failed reload witness unavailable")
            results[token] = {
                "candidate": candidate,
                "status": 400,
                "resident": failure[0]["preserved_generation"],
            }
        else:
            results[token] = {
                "candidate": candidate,
                "status": row["status"],
                "resident": "unknown",
            }
    return results
