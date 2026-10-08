"""Bounded public packaging census, not incident or unsigned-model prevalence.

Only public metadata and size-capped ONNX files are read. No referenced weight
files are fetched and no downloaded graph is executed. ONNX files themselves
can contain embedded weights; the byte cap applies to their entire contents.
"""

from __future__ import annotations

import importlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

FRAME_URL = (
    "https://huggingface.co/api/models?filter=onnx&sort=createdAt&direction=-1&limit=200&full=true"
)
CONTRACT: dict[str, Any] = {
    "schema": "onnx-packaging-exposure/v1",
    "frame_url": FRAME_URL,
    "frame_limit": 200,
    "sample_limit": 30,
    "sample_salt": "onnx-packaging-exposure-2026-10-08-v1",
    "graphs_per_repo": 2,
    "graph_byte_limit": 8 * 1024**2,
    "total_graph_attempt_byte_budget": 32 * 1024**2,
    "metadata_byte_limit": 8 * 1024**2,
    "http_timeout_seconds": 20,
    "retries": 0,
    "filename_proxy": "ONNX sibling and .onnx_data or .data sibling; not dependency truth",
    "reference_evidence": "TensorProto EXTERNAL with location in a parsed graph",
    "absence_scope": "only all graphs parsed can support no declared external reference",
    "signature_scope": "filename markers only; absence is not absence of signing",
    "inference": "descriptive selected recent tagged public repo frame; not population prevalence",
}


def encode(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value: bytes) -> str:
    return content_sha256(value)


def _write(path: Path, value: Any) -> None:
    write_new_file(path, encode(value))


def _allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return (
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and (
            host == "huggingface.co"
            or host.endswith(".huggingface.co")
            or host.endswith(".xethub.hf.co")
            or host.endswith(".cdn.hf.co")
        )
    )


class _PublicRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: str
    ) -> Any:
        if not _allowed_url(newurl):
            raise ValueError("public download redirect outside allowed hosts")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_public(url: str, limit: int) -> bytes:
    """No token discovery, cookies, authorization headers or SDK model loading."""
    if not _allowed_url(url):
        raise ValueError("public URL outside allowed hosts")
    request = Request(url, headers={"User-Agent": "bounded-onnx-packaging-study/1"})
    with build_opener(ProxyHandler({}), _PublicRedirect()).open(
        request, timeout=CONTRACT["http_timeout_seconds"]
    ) as response:
        length = response.headers.get("Content-Length")
        if length is not None and int(length) > limit:
            raise ValueError("response exceeds byte cap")
        payload: bytes = response.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("response exceeds byte cap")
    return payload


def select_frame(frame: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Fix repo unit and hash ranking before inspecting sibling/graph outcomes."""
    if len(frame) > CONTRACT["frame_limit"]:
        raise ValueError("frame exceeds declared single-page cap")
    selected: dict[str, str] = {}
    for row in frame:
        repo = row.get("id")
        sha = row.get("sha")
        if not isinstance(repo, str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            raise ValueError("invalid public repository identity")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            sha = ""  # keep unpinnable sampled repos, never substitute live main
        if repo in selected or row.get("private") is True:
            raise ValueError("duplicate or nonpublic frame repository")
        selected[repo] = sha
    ranked = sorted(selected, key=lambda repo: digest(encode([CONTRACT["sample_salt"], repo])))
    return [{"repo": repo, "sha": selected[repo]} for repo in ranked[: CONTRACT["sample_limit"]]]


def packaging_metadata(info: dict[str, Any], sha: str) -> dict[str, Any]:
    if info.get("sha") != sha:
        raise ValueError("metadata commit differs from frame snapshot")
    siblings = info.get("siblings")
    if not isinstance(siblings, list):
        raise ValueError("missing complete sibling listing")
    files: dict[str, int | None] = {}
    for item in siblings:
        name = item.get("rfilename")
        if (
            not isinstance(name, str)
            or PurePosixPath(name).is_absolute()
            or ".." in PurePosixPath(name).parts
            or "\\" in name
            or name in files
        ):
            raise ValueError("unsafe or duplicate sibling path")
        size = item.get("size")
        files[name] = (
            size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else None
        )
    graphs = sorted(name for name in files if name.lower().endswith(".onnx"))
    markers = sorted(name for name in files if name.lower().endswith((".onnx_data", ".data")))
    signatures = sorted(
        name
        for name in files
        if name.lower().endswith(
            (".sig", ".sigstore", ".sigstore.json", ".sig.json", "checksums.txt", "sha256sums")
        )
    )
    return {
        "graph_count": len(graphs),
        "filename_candidate": bool(graphs and markers),
        "external_filename_markers": markers,
        "signature_filename_markers": signatures,
        "selected_graphs": [
            {"path": name, "size": files[name]} for name in graphs[: CONTRACT["graphs_per_repo"]]
        ],
    }


def _safe_location(location: str) -> bool:
    return bool(location) and not (
        PurePosixPath(location).is_absolute()
        or ".." in PurePosixPath(location).parts
        or "\\" in location
        or ":" in location
        or "\x00" in location
    )


def inspect_onnx(payload: bytes) -> dict[str, Any]:
    """Static protobuf traversal includes tensor attributes and nested subgraphs."""
    onnx = importlib.import_module("onnx")  # optional, no model execution

    if len(payload) > CONTRACT["graph_byte_limit"]:
        raise ValueError("graph exceeds byte cap")
    model = onnx.ModelProto()
    model.ParseFromString(payload)
    if not model.HasField("graph") or model.ir_version <= 0:
        raise ValueError("not a declared ONNX model graph")
    stack: list[Any] = [model]
    references = []
    visited = 0
    while stack:
        message = stack.pop()
        visited += 1
        if visited > 200_000:
            raise ValueError("graph protobuf traversal exceeds cap")
        if (
            message.DESCRIPTOR.full_name == "onnx.TensorProto"
            and message.data_location == onnx.TensorProto.EXTERNAL
        ):
            locations = [entry.value for entry in message.external_data if entry.key == "location"]
            references.append(
                {
                    "tensor": message.name,
                    "locations": locations,
                    "well_formed_location": len(locations) == 1 and _safe_location(locations[0]),
                }
            )
        for field, value in message.ListFields():
            if field.type == field.TYPE_MESSAGE:
                stack.extend(value if field.is_repeated else [value])
    return {
        "external_tensor_count": len(references),
        "references": references,
        "valid_external_reference_count": sum(row["well_formed_location"] for row in references),
    }


def _frozen_inputs(source: Path) -> tuple[bytes, list[bytes], str]:
    """Reuse exactly the pre-error frame and metadata, not a new public sample."""
    result = json.loads((source / "results.json").read_bytes())
    claimed = result.pop("results_sha256")
    if result.get("retained_graph_bytes") != 0 or any(
        g["status"] == "parsed" for row in result["rows"] for g in row["graphs"]
    ):
        raise ValueError("transport recovery cannot reuse successful graph outcomes")
    plan = json.loads((source / "plan.json").read_bytes())
    plan_hash = plan.pop("plan_sha256")
    frame = (source / "frame.json").read_bytes()
    sample = select_frame(json.loads(frame))
    if (
        digest(encode(result)) != claimed
        or digest(encode(plan)) != plan_hash
        or result["plan_sha256"] != plan_hash
        or plan["contract"] != CONTRACT
        or digest(frame) != result["frame_sha256"]
        or json.loads((source / "sample.json").read_bytes()) != sample
        or len(result["rows"]) != len(sample)
    ):
        raise ValueError("frozen transport input identity differs")
    metadata = []
    for index, (item, row) in enumerate(zip(sample, result["rows"], strict=True)):
        raw = (source / f"metadata-{index:02d}.json").read_bytes()
        if (
            row["repo"] != item["repo"]
            or row["sha"] != item["sha"]
            or row["metadata_status"] != "complete"
            or digest(raw) != row["metadata_sha256"]
        ):
            raise ValueError("frozen metadata differs or is incomplete")
        packaging_metadata(json.loads(raw), item["sha"])
        metadata.append(raw)
    return frame, metadata, claimed


def prepare(output: Path, frozen_metadata: Path | None = None) -> dict[str, Any]:
    prior_hash = _frozen_inputs(frozen_metadata)[2] if frozen_metadata else None
    output.mkdir(parents=True, exist_ok=False)
    plan = {
        "contract": CONTRACT,
        "prepared_utc": datetime.now(UTC).isoformat(),
        "code_sha256": digest(Path(__file__).read_bytes()),
        "frozen_metadata": str(frozen_metadata.absolute()) if frozen_metadata else None,
        "prior_results_sha256": prior_hash,
    }
    plan["plan_sha256"] = digest(encode(plan))
    _write(output / "plan.json", plan)
    return plan


def check_plan(output: Path) -> dict[str, Any]:
    plan = json.loads((output / "plan.json").read_bytes())
    claimed = plan.pop("plan_sha256")
    if (
        digest(encode(plan)) != claimed
        or plan["contract"] != CONTRACT
        or plan["code_sha256"] != digest(Path(__file__).read_bytes())
    ):
        raise ValueError("prospective plan or executed code changed")
    return {**plan, "plan_sha256": claimed}


def _repo_row(
    output: Path,
    index: int,
    item: dict[str, str],
    remaining: int,
    frozen_metadata: bytes | None = None,
) -> tuple[dict[str, Any], int]:
    row: dict[str, Any] = {**item, "index": index, "metadata_status": "failed", "graphs": []}
    used = 0
    if not item["sha"]:
        row["error_type"] = "pin_unavailable"
        return row, used
    try:
        url = f"https://huggingface.co/api/models/{item['repo']}/revision/{item['sha']}?blobs=true"
        raw = (
            frozen_metadata
            if frozen_metadata is not None
            else fetch_public(url, CONTRACT["metadata_byte_limit"])
        )
        write_new_file(output / f"metadata-{index:02d}.json", raw)
        row.update(packaging_metadata(json.loads(raw), item["sha"]))
        row.update(metadata_status="complete", metadata_sha256=digest(raw))
        for graph_index, graph in enumerate(row["selected_graphs"]):
            observed = {**graph, "status": "unverified_size_or_budget"}
            size = graph["size"]
            if (
                size is not None
                and size <= CONTRACT["graph_byte_limit"]
                and size + 1 <= remaining - used
            ):
                # Charge the whole bounded attempt even when transport fails.
                used += size + 1
                try:
                    url = f"https://huggingface.co/{item['repo']}/resolve/{item['sha']}/{quote(graph['path'], safe='/')}"
                    payload = fetch_public(url, size)
                    if len(payload) != size:
                        raise ValueError("graph bytes differ from declared file size")
                    filename = f"graph-{index:02d}-{graph_index}.onnx"
                    write_new_file(output / filename, payload)
                    observed.update(
                        bytes=len(payload), sha256=digest(payload), retained_file=filename
                    )
                    observed.update(inspect_onnx(payload), status="parsed")
                except Exception as exc:
                    observed.update(status="fetch_or_parse_failed", error_type=type(exc).__name__)
            row["graphs"].append(observed)
    except Exception as exc:
        row["error_type"] = type(exc).__name__
    return row, used


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    complete = [row for row in rows if row["metadata_status"] == "complete"]
    confirmed = []
    negative = []
    for row in complete:
        graphs = row["graphs"]
        if any(g.get("valid_external_reference_count", 0) for g in graphs):
            confirmed.append(row["repo"])
        elif (
            row["graph_count"] > 0
            and len(graphs) == row["graph_count"]
            and all(g["status"] == "parsed" and g["external_tensor_count"] == 0 for g in graphs)
        ):
            negative.append(row["repo"])
    return {
        "sampled_repositories": len(rows),
        "metadata_complete": len(complete),
        "metadata_failed": len(rows) - len(complete),
        "filename_candidate_count": sum(row["filename_candidate"] for row in complete),
        "signature_filename_marker_count": sum(
            bool(row["signature_filename_markers"]) for row in complete
        ),
        "graph_confirmed_external_repositories": confirmed,
        "all_graphs_parsed_no_external_reference_repositories": negative,
        "external_reference_unverified_repositories": len(rows) - len(confirmed) - len(negative),
        "graph_status_counts": dict(Counter(g["status"] for row in rows for g in row["graphs"])),
        "scope": CONTRACT["inference"],
        "does_not_measure": [
            "signing omission",
            "unsigned models",
            "resident identity",
            "incident prevalence",
        ],
    }


def collect(output: Path) -> dict[str, Any]:
    plan = check_plan(output)
    _write(output / "execution-started.json", {"plan_sha256": plan["plan_sha256"]})
    result: dict[str, Any] = {
        "plan_sha256": plan["plan_sha256"],
        "rows": [],
        "started_utc": datetime.now(UTC).isoformat(),
    }
    try:
        metadata: list[bytes] | None = None
        if plan["frozen_metadata"]:
            raw, metadata, prior_hash = _frozen_inputs(Path(plan["frozen_metadata"]))
            if prior_hash != plan["prior_results_sha256"]:
                raise ValueError("prior transport result differs")
            result["prior_results_sha256"] = prior_hash
        else:
            raw = fetch_public(FRAME_URL, CONTRACT["metadata_byte_limit"])
        write_new_file(output / "frame.json", raw)
        sample = select_frame(json.loads(raw))
        _write(output / "sample.json", sample)
        result.update(frame_sha256=digest(raw), frame_count=len(json.loads(raw)))
        used = 0
        for index, item in enumerate(sample):
            row, downloaded = _repo_row(
                output,
                index,
                item,
                CONTRACT["total_graph_attempt_byte_budget"] - used,
                metadata[index] if metadata is not None else None,
            )
            used += downloaded
            result["rows"].append(row)
            _write(output / f"row-{index:02d}.json", row)
            print(
                json.dumps(
                    {
                        "status": "public_packaging_progress",
                        "completed": index + 1,
                        "sample_count": len(sample),
                    }
                ),
                flush=True,
            )
        result.update(
            status="bounded_census_complete",
            graph_attempt_reserved_bytes=used,
            retained_graph_bytes=sum(
                g.get("bytes", 0) for r in result["rows"] for g in r["graphs"]
            ),
        )
    except Exception as exc:
        result.update(status="census_incomplete", error_type=type(exc).__name__)
    result.update(finished_utc=datetime.now(UTC).isoformat(), analysis=summarize(result["rows"]))
    result["results_sha256"] = digest(encode(result))
    _write(output / "results.json", result)
    return result
