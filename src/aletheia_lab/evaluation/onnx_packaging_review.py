"""Rebuild a retained public packaging census without downloads or model loads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.onnx_packaging_exposure import (
    CONTRACT,
    digest,
    encode,
    inspect_onnx,
    packaging_metadata,
    select_frame,
    summarize,
)


def _read(root: Path, name: str) -> bytes:
    if Path(name).name != name or root.is_symlink() or (root / name).is_symlink():
        raise ValueError("retained artifact must be a direct nonsymlink file")
    return (root / name).read_bytes()


def _sealed(root: Path, name: str, field: str) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(_read(root, name))
    expected = value.pop(field)
    if digest(encode(value)) != expected:
        raise ValueError("retained content hash differs")
    return {**value, field: expected}


def _plan_and_frame(root: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    result = _sealed(root, "results.json", "results_sha256")
    plan = _sealed(root, "plan.json", "plan_sha256")
    if plan["contract"] != CONTRACT or result["plan_sha256"] != plan["plan_sha256"]:
        raise ValueError("retained contract or plan binding differs")
    current = Path(__file__).with_name("onnx_packaging_exposure.py").read_bytes()
    code = _read(root, "executed-code.py") if (root / "executed-code.py").exists() else current
    if digest(code) != plan["code_sha256"]:
        raise ValueError("executed source identity differs")
    frame = _read(root, "frame.json")
    sample = select_frame(json.loads(frame))
    if (
        digest(frame) != result["frame_sha256"]
        or len(json.loads(frame)) != result["frame_count"]
        or json.loads(_read(root, "sample.json")) != sample
        or len(result["rows"]) != len(sample)
    ):
        raise ValueError("retained frame, selection or census differs")
    return result, sample


def _graph_bytes(root: Path, index: int, graph_index: int, graph: dict[str, Any]) -> int:
    if "retained_file" not in graph:
        if graph["status"] == "parsed":
            raise ValueError("parsed graph bytes missing")
        return 0
    filename = f"graph-{index:02d}-{graph_index}.onnx"
    if graph["retained_file"] != filename:
        raise ValueError("retained graph name differs")
    payload = _read(root, filename)
    if (
        digest(payload) != graph["sha256"]
        or len(payload) != graph["bytes"]
        or len(payload) != graph["size"]
    ):
        raise ValueError("retained graph bytes differ")
    if graph["status"] == "parsed" and any(graph[k] != v for k, v in inspect_onnx(payload).items()):
        raise ValueError("static tensor reference census differs")
    return len(payload)


def _row_resources(
    root: Path, index: int, item: dict[str, str], row: dict[str, Any], reserved: int
) -> tuple[int, int]:
    if (
        row["index"] != index
        or row["repo"] != item["repo"]
        or row["sha"] != item["sha"]
        or json.loads(_read(root, f"row-{index:02d}.json")) != row
    ):
        raise ValueError("sample row identity differs")
    if row["metadata_status"] != "complete":
        return reserved, 0
    raw = _read(root, f"metadata-{index:02d}.json")
    expected = packaging_metadata(json.loads(raw), item["sha"])
    if digest(raw) != row["metadata_sha256"] or any(row[k] != v for k, v in expected.items()):
        raise ValueError("metadata or filename classification differs")
    if len(row["graphs"]) != len(expected["selected_graphs"]):
        raise ValueError("selected graph census differs")
    retained = 0
    for graph_index, (graph, selected) in enumerate(
        zip(row["graphs"], expected["selected_graphs"], strict=True)
    ):
        if any(graph[k] != v for k, v in selected.items()):
            raise ValueError("graph selection or declared size differs")
        size = selected["size"]
        eligible = (
            size is not None
            and size <= CONTRACT["graph_byte_limit"]
            and size + 1 <= CONTRACT["total_graph_attempt_byte_budget"] - reserved
        )
        if eligible:
            reserved += size + 1
            if graph["status"] not in ("parsed", "fetch_or_parse_failed"):
                raise ValueError("eligible graph attempt missing")
        elif graph["status"] != "unverified_size_or_budget":
            raise ValueError("unqualified graph attempt")
        retained += _graph_bytes(root, index, graph_index, graph)
    return reserved, retained


def verify_retained(root: Path) -> dict[str, Any]:
    result, sample = _plan_and_frame(root)
    reserved = 0
    retained = 0
    for index, (item, row) in enumerate(zip(sample, result["rows"], strict=True)):
        reserved, amount = _row_resources(root, index, item, row, reserved)
        retained += amount
    if (
        reserved != result["graph_attempt_reserved_bytes"]
        or retained != result["retained_graph_bytes"]
        or summarize(result["rows"]) != result["analysis"]
    ):
        raise ValueError("resource or analysis reconstruction differs")
    return {
        "verification": "pass",
        "results_sha256": result["results_sha256"],
        "analysis": result["analysis"],
        "graph_attempt_reserved_bytes": reserved,
        "retained_graph_bytes": retained,
        "downloads": 0,
        "model_executions": 0,
        "scope": "retained public bytes and deterministic census; not capture authentication",
    }
