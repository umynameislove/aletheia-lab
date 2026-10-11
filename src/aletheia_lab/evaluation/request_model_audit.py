"""Finite-frame request/model audit with an explicit trusted-capture boundary.

Native task context is an observation, never the authority for item identity.
This ordinary correspondence check is not a new tracing algorithm or host
attestation. The source adapter must capture actual batch operands and closure.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.project.identity import content_sha256

ROOT = ("token", "requested", "kind", "input", "output", "closed", "failed")
USE = ("token", "batch", "index", "generation", "input", "output", "fingerprint")
LOAD = ("model", "artifact", "fingerprint")
VERDICTS = frozenset({"compliant", "violation", "unknown", "conflict"})


def digest(value: Any) -> str:
    return content_sha256(encode(value).encode())


def _fields(value: Any, keys: tuple[str, ...]) -> None:
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError("unsupported request certificate fields")


def _hash(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def validate(frame: dict[str, Any]) -> None:
    _fields(frame, (*ROOT, "uses", "loads"))
    if not isinstance(frame["token"], str) or not 0 < len(frame["token"]) <= 128:
        raise ValueError("bounded ingress token required")
    if frame["requested"] not in {"aaa", "bbb"}:
        raise ValueError("unsupported model selection contract")
    if frame["kind"] not in {"batched", "non_batched"}:
        raise ValueError("unsupported native invocation kind")
    if any(type(frame[key]) is not bool for key in ("closed", "failed")):
        raise ValueError("explicit closure and failure markers required")
    if not _hash(frame["input"]) or (frame["output"] is not None and not _hash(frame["output"])):
        raise ValueError("invalid operand identity")
    if type(frame["uses"]) is not list or type(frame["loads"]) is not dict:
        raise ValueError("invalid invocation/dependency census")
    if len(frame["uses"]) > 16 or len(frame["loads"]) > 16 or len(encode(frame)) > 65536:
        raise ValueError("oversized admitted request frame")
    for use in frame["uses"]:
        _validate_use(use)
    for generation, load in frame["loads"].items():
        _validate_load(generation, load)


def _validate_use(use: dict[str, Any]) -> None:
    _fields(use, USE)
    if type(use["index"]) is not int or use["index"] < 0:
        raise ValueError("invalid batch member index")
    if not all(isinstance(use[k], str) and use[k] for k in ("token", "batch", "generation")):
        raise ValueError("invalid batch/generation identity")
    if not all(_hash(use[k]) for k in ("input", "output", "fingerprint")):
        raise ValueError("invalid invocation operand/object identity")


def _validate_load(generation: str, load: dict[str, Any]) -> None:
    _fields(load, LOAD)
    if not isinstance(generation, str) or not generation or load["model"] not in {"aaa", "bbb"}:
        raise ValueError("invalid loaded model identity")
    if not all(_hash(load[k]) for k in ("artifact", "fingerprint")):
        raise ValueError("invalid loaded artifact/object identity")


def resolve(frame: dict[str, Any]) -> str:
    """Decide selected-model compliance, not prediction quality or exact-byte policy."""
    validate(frame)
    if len(frame["uses"]) > 1:
        return "conflict"
    if frame["failed"] or not frame["closed"] or frame["output"] is None:
        return "unknown"
    if not frame["uses"]:
        return "unknown"
    use = frame["uses"][0]
    load = frame["loads"].get(use["generation"])
    if load is None:
        return "unknown"
    if (
        use["token"] != frame["token"]
        or use["input"] != frame["input"]
        or use["output"] != frame["output"]
        or use["fingerprint"] != load["fingerprint"]
    ):
        return "conflict"
    return "compliant" if load["model"] == frame["requested"] else "violation"


def materialize(frame: dict[str, Any]) -> dict[str, Any]:
    """Reversible field packing; no minimum-certificate theorem is implied."""
    validate(frame)
    return {
        "root": [frame[key] for key in ROOT],
        "uses": [[use[key] for key in USE] for use in frame["uses"]],
        "loads": {key: [load[field] for field in LOAD] for key, load in frame["loads"].items()},
    }


def _unpack(value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if type(value) is not list or len(value) != len(fields):
        raise ValueError("invalid positional certificate")
    return dict(zip(fields, value, strict=True))


def expand(document: dict[str, Any]) -> dict[str, Any]:
    _fields(document, ("root", "uses", "loads"))
    if type(document["uses"]) is not list or type(document["loads"]) is not dict:
        raise ValueError("invalid positional dependency census")
    frame = {
        **_unpack(document["root"], ROOT),
        "uses": [_unpack(value, USE) for value in document["uses"]],
        "loads": {key: _unpack(value, LOAD) for key, value in document["loads"].items()},
    }
    validate(frame)
    return frame


def _members(source: dict[str, Any], row: dict[str, Any]) -> list[tuple[dict[str, Any], int]]:
    batches = [batch for batch in source["batches"] if batch["scenario"] == row["scenario"]]
    if source["mode"] == "original":
        # The unchanged toy input has no explicit token. Only completed serial
        # singleton requests admit this harness-order correspondence.
        rows = [item for item in source["rows"] if item["scenario"] == row["scenario"]]
        if len(rows) != len(batches) or any(
            len(batch["members"]) != 1 or not batch["completed"] for batch in batches
        ):
            return []
        if [item["ordinal"] for item in rows] != list(range(len(rows))) or any(
            item["http_status"] != 200 for item in rows
        ):
            return []
        members = [(batch, 0) for batch in batches]
        ordinal = row["ordinal"]
        return members[ordinal : ordinal + 1]
    return [
        (batch, index)
        for batch in batches
        for index, member in enumerate(batch["members"])
        if member["token"] == row["token"]
    ]


def frames(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Admit source observations without using native context as the joining key."""
    tokens = [row["token"] for row in source["rows"]]
    if len(tokens) != len(set(tokens)):
        raise ValueError("duplicate independent ingress identity")
    result = []
    for row in source["rows"]:
        loads, uses = {}, []
        for batch, index in _members(source, row):
            member = batch["members"][index]
            generation = batch["actual_generation"]
            load = source["loads"].get(generation)
            if load is None or not batch["completed"]:
                continue
            raw = load["raw_hex"]
            loads[generation] = {
                "model": load["model"],
                "artifact": content_sha256(bytes.fromhex(raw))
                if raw
                else digest(load["parameters"]),
                "fingerprint": load["object_fingerprint"],
            }
            uses.append(
                {
                    "token": row["token"],
                    "batch": batch["batch"],
                    "index": index,
                    "generation": generation,
                    "input": digest(member["arg"]),
                    "output": digest(member["output"]),
                    "fingerprint": batch["actual_object"]["object_fingerprint"],
                }
            )
        frame = {
            "token": row["token"],
            "requested": row["requested_model"],
            "kind": row["kind"],
            "input": digest(row["arg"]),
            "output": digest(row["output"]) if row["http_status"] == 200 else None,
            "closed": row["http_status"] is not None,
            "failed": row["http_status"] != 200,
            "uses": uses,
            "loads": loads,
        }
        validate(frame)
        result.append(frame)
    return result


def analyze(source: dict[str, Any]) -> dict[str, Any]:
    admitted = frames(source)
    outcomes = []
    for row, frame in zip(source["rows"], admitted, strict=True):
        verdict = resolve(frame)
        original_output = "unknown"
        if source["mode"] == "original" and row["http_status"] == 200:
            recognized = next(
                (
                    model
                    for model in ("aaa", "bbb")
                    if row["output"] == f"Response from model_obj_for_{model} {row['arg']}"
                ),
                None,
            )
            if recognized is not None:
                original_output = (
                    "compliant" if recognized == row["requested_model"] else "violation"
                )
        contexts = [batch["native_context"]["model"] for batch, _ in _members(source, row)]
        outcomes.append(
            {
                "token": row["token"],
                "kind": row["kind"],
                "scenario": row["scenario"],
                "receipt": verdict,
                "original_output": original_output,
                "context_matches_requested": contexts == [row["requested_model"]],
                # Equivalent explicit item links have exactly the same access;
                # this is a correspondence baseline, not an executed OTel exporter.
                "explicit_item_links": correspondence(frame),
                "load_only": "unknown",
            }
        )
    return {
        "denominator": len(outcomes),
        "receipt_counts": dict(Counter(r["receipt"] for r in outcomes)),
        "output_counts": dict(Counter(r["original_output"] for r in outcomes)),
        "context_mismatch_count": sum(not row["context_matches_requested"] for row in outcomes),
        "outcomes": outcomes,
        "provider_calls": 0,
    }


def correspondence(frame: dict[str, Any]) -> str:
    """Ordinary explicit-link baseline with the same admitted facts.

    A direct relational join, not the tested resolver or an OTel SDK execution.
    Native context is deliberately not needed for this baseline either.
    """
    validate(frame)
    candidates = frame["uses"]
    if len(candidates) > 1:
        return "conflict"
    if frame["failed"] or not frame["closed"] or frame["output"] is None:
        return "unknown"
    if not candidates or candidates[0]["generation"] not in frame["loads"]:
        return "unknown"
    link = candidates[0]
    model = frame["loads"][link["generation"]]
    expected = (frame["token"], frame["input"], frame["output"], model["fingerprint"])
    actual = (link["token"], link["input"], link["output"], link["fingerprint"])
    if actual != expected:
        return "conflict"
    return "compliant" if model["model"] == frame["requested"] else "violation"
