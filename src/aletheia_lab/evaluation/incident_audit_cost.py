"""Separate native/hash/capture/archive component probe on the same CPU calls.

This is a small development cost probe, not a serving benchmark or a causal
production overhead estimate. Common fixture setup and numerical checking remain
outside the native timer. Failed native calls and failed processes remain offered.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import subprocess
import sys
from pathlib import Path
from statistics import median
from time import perf_counter_ns
from typing import Any

from aletheia_lab.evaluation.cache_lifecycle_study import read_sealed, seal
from aletheia_lab.evaluation.incident_audit_archive import IncidentAuditArchive
from aletheia_lab.evaluation.incident_audit_source import InitializerWorkload, identity
from aletheia_lab.evaluation.incident_audit_study import FILES, memory_sample
from aletheia_lab.evaluation.incident_audit_verification import verify_native_floor
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_audit import digest, resolve
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

MODES = ("native_floor", "hash_only", "capture", "compact_static")
IMPLEMENTATION = "src/aletheia_lab/evaluation/incident_audit_cost.py"


def input_values(ordinal: int) -> list[list[float]]:
    if ordinal == 11:
        return [[1.0, 1.0], [1.0, 1.0]]
    if ordinal % 8 == 3 or ordinal == 15:
        return [[0.0, 0.0]] * 3
    return [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]


def linked_frame(
    source: InitializerWorkload, row: dict[str, Any], hashes: dict[str, Any]
) -> dict[str, Any]:
    ordinal = row["ordinal"]
    model = "aaa" if ordinal % 2 == 0 else "bbb"
    generation, token = f"session-{model}", f"request-{ordinal:03d}"
    failed = row["native_error"] is not None
    frame = {
        "token": token,
        "requested": model,
        "kind": "non_batched",
        **hashes,
        "closed": True,
        "failed": failed,
        "loads": {},
        "uses": [],
    }
    if not failed:
        frame["loads"] = {generation: source.loads[generation]}
        frame["uses"] = [
            {
                "token": token,
                "batch": token,
                "index": 0,
                "generation": generation,
                **hashes,
                "fingerprint": source.loads[generation]["fingerprint"],
            }
        ]
    return frame


def _physical(path: Path) -> int:
    return sum(
        p.stat().st_size
        for suffix in ("", "-wal", "-shm")
        if (p := Path(str(path) + suffix)).exists()
    )


def _persist(db: Any, archive: Any, frame: dict[str, Any], ordinal: int) -> str:
    if archive is not None:
        if not archive.put(frame, now=ordinal):
            raise ValueError("provisioned cost archive refused")
        return str(archive.snapshot()["state_sha256"])
    raw = encode(frame)
    with db:
        db.execute(
            "INSERT INTO evidence VALUES(?,?,?)",
            (frame["token"], raw, content_sha256(raw.encode())),
        )
    return content_sha256(raw.encode())


def _query(db: Any, archive: Any, frame: dict[str, Any], ordinal: int) -> str:
    if archive is not None:
        raw = bytes(archive.db.execute("SELECT payload FROM state WHERE id=1").fetchone()[0])
        state = archive._parse(json.loads(raw))
        return resolve(
            archive._frame(frame["token"], state["entries"][frame["token"]], state["atoms"])
        )
    saved = db.execute("SELECT payload FROM evidence WHERE token=?", (frame["token"],)).fetchone()[
        0
    ]
    return resolve(json.loads(saved))


def _check(db: Any, archive: Any, frame: dict[str, Any]) -> None:
    if archive is not None:
        observed = archive._frame(
            frame["token"], archive.state["entries"][frame["token"]], archive.state["atoms"]
        )
    else:
        saved, saved_hash = db.execute(
            "SELECT payload,digest FROM evidence WHERE token=?", (frame["token"],)
        ).fetchone()
        if saved_hash != content_sha256(saved.encode()):
            raise ValueError("capture reconstruction hash differs")
        observed = json.loads(saved)
    if observed != frame:
        raise ValueError("capture reconstruction differs")


def _capture(row: dict[str, Any], source: InitializerWorkload, db: Any, archive: Any) -> int:
    ordinal = row["ordinal"]
    started = perf_counter_ns()
    frame = linked_frame(source, row, row["hashes"])
    raw = encode(frame)
    answer = resolve(frame)
    row.update(frame_resolve_ns=perf_counter_ns() - started, frame=frame)
    started = perf_counter_ns()
    ack = _persist(db, archive, frame, ordinal)
    row.update(write_ack_ns=perf_counter_ns() - started, ack=ack)
    started = perf_counter_ns()
    observed = _query(db, archive, frame, ordinal)
    if observed != answer:
        raise ValueError("committed query differs")
    row.update(query_ns=perf_counter_ns() - started, answer=observed)
    started = perf_counter_ns()
    _check(db, archive, frame)
    row["verify_ns"] = perf_counter_ns() - started
    return len(raw.encode())


def worker(mode: str, directory: Path) -> dict[str, Any]:
    if mode not in MODES or directory.exists():
        raise ValueError("fresh bounded cost cell required")
    directory.mkdir()
    source = InitializerWorkload()
    path = directory / "capture.sqlite"
    db = None
    archive = None
    if mode == "capture":
        db = sqlite3.connect(path)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("CREATE TABLE evidence(token TEXT PRIMARY KEY,payload TEXT,digest TEXT)")
        db.commit()
    elif mode == "compact_static":
        archive = IncidentAuditArchive(path, "static", 1000000)
    rows = []
    peak, payload_bytes = 0, 0
    for ordinal in range(16):
        row = source.native_floor(ordinal)  # Identical SDK call path in all four modes.
        row.update(
            hash_ns=0,
            frame_resolve_ns=0,
            write_ack_ns=0,
            query_ns=0,
            verify_ns=0,
            hashes=None,
            frame=None,
            answer="unknown",
            ack=None,
        )
        if mode != "native_floor":
            started = perf_counter_ns()
            hashes = {
                "input": digest(input_values(ordinal)),
                "output": digest(row["output"]) if row["output"] is not None else None,
            }
            row.update(hash_ns=perf_counter_ns() - started, hashes=hashes)
        if mode in {"capture", "compact_static"}:
            payload_bytes += _capture(row, source, db, archive)
            peak = max(peak, _physical(path))
        rows.append(row)
    snapshot = archive.snapshot() if archive is not None else None
    if archive is not None:
        archive.close()
    if db is not None:
        db.close()
    storage = {
        "payload_bytes": payload_bytes,
        "peak_db_wal_shm_bytes": peak,
        "closed_db_bytes": path.stat().st_size if path.exists() else 0,
        "db_sha256": content_sha256(path.read_bytes()) if path.exists() else None,
        "compact_snapshot": snapshot,
    }
    return {
        "status": "cost_cell_executed",
        "mode": mode,
        "rows": rows,
        "source_identity": source.identity,
        "source_load_ns": source.load_ns,
        "storage": storage,
        "memory": memory_sample(),
        "provider_calls": 0,
    }


def _child(root: Path, directory: Path, mode: str, replicate: int) -> dict[str, Any]:
    name = f"{mode}-{replicate}"
    env = {
        k: v
        for k, v in os.environ.items()
        if k in {"PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP"}
    }
    env.update(
        PYTHONPATH=str(root / "src"),
        PYTHONHASHSEED=str(replicate + 1),
        OMP_NUM_THREADS="1",
        OPENBLAS_NUM_THREADS="1",
    )
    try:
        child = subprocess.run(
            [
                sys.executable,
                "-m",
                "aletheia_lab.evaluation.incident_audit_cost",
                "worker",
                "--root",
                str(root),
                "--study-dir",
                str(directory / name),
                "--mode",
                mode,
            ],
            capture_output=True,
            timeout=60,
            env=env,
            check=False,
        )
        stdout, stderr = child.stdout, child.stderr
        failure = (
            {"status": "worker_failed", "returncode": child.returncode}
            if child.returncode
            else None
        )
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = exc.stdout or b"", exc.stderr or b""
        failure = {"status": "worker_timeout", "timeout_seconds": 60}
    for stream, payload in (("stdout", stdout), ("stderr", stderr)):
        write_new_file(directory / f"{name}.{stream}", payload)
    result = read_sealed(directory / name / "report.json") if failure is None else seal(failure)
    return {
        "mode": mode,
        "replicate": replicate,
        "report": result,
        "streams": {"stdout": content_sha256(stdout), "stderr": content_sha256(stderr)},
    }


def run(root: Path, directory: Path) -> dict[str, Any]:
    if directory.exists() or directory.is_symlink() or directory.is_relative_to(root):
        raise ValueError("fresh private cost output outside repository required")
    order = [(mode, replicate) for replicate in range(3) for mode in MODES]
    random.Random(104729).shuffle(order)
    files = (*FILES, IMPLEMENTATION)
    plan = seal(
        {
            "schema": "incident-audit-cost-plan/v1",
            "order": order,
            "native_calls_per_cell": 16,
            "source_identity": identity(),
            "code": {p: content_sha256((root / p).read_bytes()) for p in files},
            "service": "capture and compact preserve same closed linked-frame read-only query; hashes alone do not identify effective session",
            "forecasts": "native calls identical in all modes; ordinary lossless storage preserves 15 conclusive and one native-failure unknown per cell",
            "timing": "separate disjoint stages; no production overhead, throughput or summed overlapping timers",
            "response_and_resource_scope": "provisioned small fixed fixture; no operational SLA or pressure claim",
        }
    )
    directory.mkdir(parents=True)
    for name in files:
        write_new_file(directory / "code-snapshot" / name, (root / name).read_bytes())
    write_new_file(directory / "plan.json", encode(plan).encode())
    cells = [_child(root, directory, mode, replicate) for mode, replicate in order]
    result = seal(
        {
            "schema": "incident-audit-cost-results/v1",
            "plan_sha256": plan["sha256"],
            "cells": cells,
            "failed_processes": sum(c["report"]["status"] != "cost_cell_executed" for c in cells),
        }
    )
    write_new_file(directory / "results.json", encode(result).encode())
    return {
        "status": "component_cost_executed",
        "planned_cells": 12,
        "failed_processes": result["failed_processes"],
        "results_sha256": result["sha256"],
        "provider_calls": 0,
    }


def verify_cell(report: dict[str, Any], directory: Path) -> None:
    rows, mode = report["rows"], report["mode"]
    verify_native_floor(rows, 16)
    for row in rows:
        hashes = {
            "input": digest(input_values(row["ordinal"])),
            "output": digest(row["output"]) if row["output"] is not None else None,
        }
        if mode != "native_floor" and row["hashes"] != hashes:
            raise ValueError("cost input/output hashes differ")
        if mode in {"capture", "compact_static"}:
            truth = "unknown" if row["native_error"] else "compliant"
            if resolve(row["frame"]) != truth or row["answer"] != truth:
                raise ValueError("cost capture answer differs")
            if (
                row["frame"]["input"] != hashes["input"]
                or row["frame"]["output"] != hashes["output"]
            ):
                raise ValueError("cost capture input/output differs")
        for key in ("hash_ns", "frame_resolve_ns", "write_ack_ns", "query_ns", "verify_ns"):
            if type(row[key]) is not int or row[key] < 0:
                raise ValueError("component timer differs")
    _verify_storage(report, directory)


def _verify_storage(report: dict[str, Any], directory: Path) -> None:
    mode, rows = report["mode"], report["rows"]
    path = directory / "capture.sqlite"
    if mode in {"capture", "compact_static"}:
        if content_sha256(path.read_bytes()) != report["storage"]["db_sha256"]:
            raise ValueError("cost durable database differs")
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as db:
            if mode == "capture":
                observed = {
                    token: (payload, saved_hash)
                    for token, payload, saved_hash in db.execute("SELECT * FROM evidence")
                }
                for row in rows:
                    raw = encode(row["frame"])
                    if observed.pop(row["frame"]["token"]) != (raw, content_sha256(raw.encode())):
                        raise ValueError("cost capture frontier differs")
                if observed:
                    raise ValueError("unexpected cost capture scopes")
            else:
                state_raw = bytes(db.execute("SELECT payload FROM state WHERE id=1").fetchone()[0])
                if (
                    content_sha256(state_raw)
                    != report["storage"]["compact_snapshot"]["state_sha256"]
                ):
                    raise ValueError("cost compact frontier differs")


def verify(root: Path, directory: Path) -> dict[str, Any]:
    plan, result = read_sealed(directory / "plan.json"), read_sealed(directory / "results.json")
    if result["plan_sha256"] != plan["sha256"] or [
        (c["mode"], c["replicate"]) for c in result["cells"]
    ] != [tuple(c) for c in plan["order"]]:
        raise ValueError("cost sealed census differs")
    for name, expected in plan["code"].items():
        if (
            content_sha256((root / name).read_bytes()) != expected
            or content_sha256((directory / "code-snapshot" / name).read_bytes()) != expected
        ):
            raise ValueError("cost execution code differs")
    groups: dict[str, list[dict[str, Any]]] = {}
    for cell in result["cells"]:
        name = f"{cell['mode']}-{cell['replicate']}"
        for stream, expected in cell["streams"].items():
            if content_sha256((directory / f"{name}.{stream}").read_bytes()) != expected:
                raise ValueError("cost child stream differs")
        report = cell["report"]
        if report["status"] != "cost_cell_executed":
            continue
        if (
            read_sealed(directory / name / "report.json") != report
            or report["source_identity"] != plan["source_identity"]
        ):
            raise ValueError("cost raw report/source differs")
        verify_cell(report, directory / name)
        groups.setdefault(cell["mode"], []).append(report)
    summary = {}
    for mode, reports in groups.items():
        summary[mode] = {
            "processes": len(reports),
            "native_calls": sum(len(r["rows"]) for r in reports),
            "native_failures": sum(
                row["native_error"] is not None for r in reports for row in r["rows"]
            ),
            "median_components_ns": {
                key: median(row[key] for r in reports for row in r["rows"])
                for key in (
                    "native_ns",
                    "hash_ns",
                    "frame_resolve_ns",
                    "write_ack_ns",
                    "query_ns",
                    "verify_ns",
                )
            },
            "closed_db_bytes": [r["storage"]["closed_db_bytes"] for r in reports],
            "peak_db_wal_shm_bytes": [r["storage"]["peak_db_wal_shm_bytes"] for r in reports],
            "whole_process_rss_bytes": [r["memory"]["peak_process_rss_bytes"] for r in reports],
        }
    return {
        "verification": "pass",
        "failed_processes": result["failed_processes"],
        "summary": summary,
        "qualification": "nested process component measurements; source setup and raw receipt/arithmetic checks shared; no production or total causal overhead claim",
        "provider_calls": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "worker", "verify"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=MODES, default=MODES[0])
    args = parser.parse_args()
    try:
        if args.command == "worker":
            result = seal(worker(args.mode, args.study_dir.resolve()))
            write_new_file(args.study_dir / "report.json", encode(result).encode())
        else:
            result = (run if args.command == "run" else verify)(
                args.root.resolve(), args.study_dir.resolve()
            )
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        print(encode({"status": "component_cost_failed_closed", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
    print(
        encode(
            result
            if args.command != "worker"
            else {"status": result["status"], "provider_calls": 0}
        )
    )


if __name__ == "__main__":
    main()
