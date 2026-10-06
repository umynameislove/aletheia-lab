"""Contract-relative audit of a response's computation or cache origin.

Cache reuse is not a new model invocation. This bounded correspondence resolver
uses established provenance/identifiability ideas, not a new sufficiency theorem.
Its capture boundary trusts the application adapter, not a hostile serving host.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from aletheia_lab.evaluation.request_model_audit import digest

ROOT = (
    "token",
    "model",
    "version",
    "generation",
    "contract",
    "input",
    "output",
    "closed",
    "failed",
    "origin",
)
COMPUTE = ("kind", "model", "version", "generation", "input", "output", "fingerprint", "closed")
CACHE = ("kind", "producer", "input", "output", "closed")
MAX_NODES = 128


def _fields(value: Any, fields: tuple[str, ...]) -> None:
    if type(value) is not dict or set(value) != set(fields):
        raise ValueError("unsupported response-origin fields")


def _identity(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 128


def _hash(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def validate(frame: dict[str, Any]) -> None:
    _fields(frame, ("request", "nodes"))
    root, nodes = frame["request"], frame["nodes"]
    _fields(root, ROOT)
    if not _identity(root["token"]) or not _identity(root["model"]):
        raise ValueError("bounded independent request and model identity required")
    for field in ("version", "generation", "origin"):
        if root[field] is not None and not _identity(root[field]):
            raise ValueError("invalid optional request identity")
    if root["contract"] not in {"route", "selected_generation"}:
        raise ValueError("unsupported operational contract")
    if not _hash(root["input"]) or (root["output"] is not None and not _hash(root["output"])):
        raise ValueError("invalid request operand hash")
    if any(type(root[field]) is not bool for field in ("closed", "failed")):
        raise ValueError("explicit response closure and failure required")
    if type(nodes) is not dict or len(nodes) > MAX_NODES:
        raise ValueError("bounded origin census required")
    for identifier, node in nodes.items():
        if not _identity(identifier) or type(node) is not dict:
            raise ValueError("invalid origin node identity")
        _validate_node(node)


def _validate_node(node: dict[str, Any]) -> None:
    kind = node.get("kind")
    if kind not in {"compute", "cache"}:
        raise ValueError("unsupported origin node kind")
    _fields(node, COMPUTE if kind == "compute" else CACHE)
    if not all(_hash(node[field]) for field in ("input", "output")):
        raise ValueError("invalid origin operand hash")
    if type(node["closed"]) is not bool:
        raise ValueError("origin closure must be explicit")
    if kind == "cache":
        if not _identity(node["producer"]):
            raise ValueError("cache producer identity required")
    else:
        if not all(_identity(node[field]) for field in ("model", "generation")):
            raise ValueError("actual model generation required")
        if node["version"] is not None and not _identity(node["version"]):
            raise ValueError("invalid actual model version")
        if not _hash(node["fingerprint"]):
            raise ValueError("actual object fingerprint required")


def origin(frame: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """Follow a sealed cache chain; cycles/incompatible operands are conflicts."""
    validate(frame)
    root, nodes = frame["request"], frame["nodes"]
    if root["failed"] or not root["closed"] or root["output"] is None:
        return "unknown", None
    current, seen = root["origin"], set()
    while current is not None:
        if current in seen:
            return "conflict", None
        seen.add(current)
        node = nodes.get(current)
        if node is None or not node["closed"]:
            return "unknown", None
        if (node["input"], node["output"]) != (root["input"], root["output"]):
            return "conflict", None
        if node["kind"] == "compute":
            return "identified", node
        current = node["producer"]
    return "unknown", None


def resolve(frame: dict[str, Any]) -> str:
    state, computation = origin(frame)
    if computation is None:
        return state
    root = frame["request"]
    if computation["model"] != root["model"]:
        return "violation"
    if root["version"] is not None and computation["version"] != root["version"]:
        return "violation"
    if root["contract"] == "selected_generation":
        if root["generation"] is None:
            return "unknown"
        if computation["generation"] != root["generation"]:
            return "violation"
    return "compliant"


def explicit_join(frame: dict[str, Any]) -> str:
    """Ordinary graph/relational baseline, with the SAME capture capability.

    Independent traversal logic, not an executed OpenTelemetry SDK or attestation.
    Equality with this baseline is expected, not evidence of a novel checker.
    """
    validate(frame)
    root, nodes = frame["request"], frame["nodes"]
    if root["failed"] or not root["closed"] or root["output"] is None:
        return "unknown"
    candidates = [root["origin"]]
    visited = []
    computation = None
    while candidates:
        identifier = candidates.pop()
        if identifier is None or identifier not in nodes:
            return "unknown"
        if identifier in visited:
            return "conflict"
        visited.append(identifier)
        value = nodes[identifier]
        if not value["closed"]:
            return "unknown"
        if value["input"] != root["input"] or value["output"] != root["output"]:
            return "conflict"
        if value["kind"] == "cache":
            candidates.append(value["producer"])
        else:
            computation = value
    if computation is None:
        return "unknown"
    return _joined_verdict(root, computation)


def _joined_verdict(root: dict[str, Any], computation: dict[str, Any]) -> str:
    actual = (computation["model"], computation["version"], computation["generation"])
    expected = (root["model"], root["version"], root["generation"])
    if actual[0] != expected[0] or (expected[1] is not None and actual[1] != expected[1]):
        return "violation"
    if root["contract"] == "selected_generation":
        if expected[2] is None:
            return "unknown"
        return "compliant" if actual[2] == expected[2] else "violation"
    return "compliant"


def direct_only(frame: dict[str, Any]) -> str:
    """Ablation: a cache hit is not guessed to be a new invocation."""
    validate(frame)
    node = frame["nodes"].get(frame["request"]["origin"])
    return resolve(frame) if node is not None and node["kind"] == "compute" else "unknown"


def native_identity(row: dict[str, Any], *, header: bool = False) -> str:
    """Native claimed route consistency, never a generation/consumption proof."""
    if row.get("status") != 200 or not isinstance(row.get("response"), dict):
        return "unknown"
    if header:
        model = row["response_headers"].get("ce-modelid")
        version = row["response_headers"].get("ce-modelversion")
    else:
        model = row["response"].get("model_name")
        version = row["response"].get("model_version")
    if model is None or (row["version"] is not None and version is None):
        return "unknown"
    matches = model == row["model"] and (row["version"] is None or version == row["version"])
    if not matches:
        return "violation"
    return "unknown" if row["contract"] == "selected_generation" else "compliant"


def summary(truth: list[str], answers: list[str]) -> dict[str, Any]:
    if len(truth) != len(answers):
        raise ValueError("complete paired census required")
    conclusive = {"compliant", "violation"}
    return {
        "offered": len(truth),
        "reference_counts": dict(Counter(truth)),
        "answer_counts": dict(Counter(answers)),
        "correct_conclusive": sum(
            a == b and a in conclusive for a, b in zip(truth, answers, strict=True)
        ),
        "false_conclusive": sum(
            b in conclusive and b != a for a, b in zip(truth, answers, strict=True)
        ),
        "missing_conclusive": sum(
            a in conclusive and b not in conclusive for a, b in zip(truth, answers, strict=True)
        ),
    }


def collisions(observations: list[Any], truth: list[str]) -> dict[str, Any]:
    """Finite empirical observational collisions, NOT global sufficiency proof."""
    if len(observations) != len(truth):
        raise ValueError("observation/reference census differs")
    groups: dict[str, list[int]] = {}
    for index, observation in enumerate(observations):
        groups.setdefault(digest(observation), []).append(index)
    opposite = []
    for indices in groups.values():
        statuses = {truth[index] for index in indices} & {"compliant", "violation"}
        if len(statuses) > 1:
            opposite.append(indices)
    return {"finite_groups": len(groups), "opposite_status_groups": opposite}
