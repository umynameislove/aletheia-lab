"""Prospective source-informed inference transfer, not natural deployment validation.

Original maintainer test bodies and assertions remain unchanged. Study-authored
dependency replacement, restoration and capture omission are separate controls.
Saved external-data graphs differ from self-contained effective loaded models.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import os
import runpy
import sqlite3
import subprocess
import sys
from pathlib import Path
from time import perf_counter_ns
from typing import Any
from unittest.mock import patch

from aletheia_lab.evaluation.cache_lifecycle_study import read_sealed, seal
from aletheia_lab.evaluation.incident_audit_archive import IncidentAuditArchive
from aletheia_lab.evaluation.incident_audit_study import FILES
from aletheia_lab.evaluation.incident_audit_verification import witness_answers
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_audit import digest, resolve
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

SOURCE_FILE = "onnx/test/model_container_refeval_test.py"
SOURCE_HASH = "ae3dca01f2cf67c2bf6628aade91a5b6dc1515f082e66403987386af4a4fc366"
METHODS = ("test_large_multi_files", "test_large_one_weight_file")
ARMS = ("lawful", "dependency_replaced", "restore_dependency", "missing_capture")
IMPLEMENTATION = "src/aletheia_lab/evaluation/incident_audit_transfer.py"


def dependency_span(descriptor: dict[str, Any], file_length: int) -> tuple[int, int]:
    offset = int(descriptor.get("offset", 0))
    length = int(descriptor.get("length", file_length - offset))
    if offset < 0 or length != 36 or offset + length > file_length:
        raise ValueError("bounded owned external dependency required")
    return offset, length


def source_identity() -> tuple[Path, dict[str, Any]]:
    distribution = importlib.metadata.distribution("onnx")
    path = Path(str(distribution.locate_file(SOURCE_FILE)))
    if (
        distribution.version != "1.19.1"
        or path.is_symlink()
        or content_sha256(path.read_bytes()) != SOURCE_HASH
    ):
        raise ValueError("pinned regular maintainer source required")
    return path, {
        "package": "onnx",
        "version": distribution.version,
        "source_file": SOURCE_FILE,
        "source_sha256": SOURCE_HASH,
        "license": "Apache-2.0",
        "source_class": "maintainer-authored workflow; outcome exposure recorded in execution closeout",
        "upstream_commit": "b751946c3d59a3c8358abcc0569b59e6ddb08cdd",
    }


def matmul(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    return [
        [sum(x * right[k][j] for k, x in enumerate(row)) for j in range(len(right[0]))]
        for row in left
    ]


def expected_weights() -> dict[str, list[list[float]]]:
    values = [[float(i * 3 + j) for j in range(3)] for i in range(3)]
    return {
        "A": [[x * 100 for x in row] for row in values],
        "B": values,
        "C": [[x + 10 for x in row] for row in values],
    }


def matrix_output(values: list[list[float]], weights: dict[str, Any]) -> list[list[float]]:
    for name in ("A", "B", "C"):
        values = matmul(values, weights[name])
    return values


def make_frame(ordinal: int, raw: dict[str, Any], captured: bool) -> dict[str, Any]:
    token = f"inference-{ordinal}"
    model = "aaa" if raw["weights"] == expected_weights() else "bbb"
    fingerprint = digest(raw["weights"])
    frame = {
        "token": token,
        "requested": "aaa",
        "kind": "non_batched",
        "input": digest(raw["input"]),
        "output": digest(raw["output"]),
        "closed": True,
        "failed": False,
        "loads": {},
        "uses": [],
    }
    if captured:
        frame["loads"] = {
            token: {
                "model": model,
                "artifact": raw["effective_proto_sha256"],
                "fingerprint": fingerprint,
            }
        }
        frame["uses"] = [
            {
                "token": token,
                "batch": token,
                "index": 0,
                "generation": token,
                "input": frame["input"],
                "output": frame["output"],
                "fingerprint": fingerprint,
            }
        ]
    return frame


class TransferCapture:
    def __init__(self, directory: Path, arm: str) -> None:
        self.directory, self.arm = directory, arm
        self.rows: list[dict[str, Any]] = []
        self.saved: list[dict[str, Any]] = []

    def record(self, evaluator: Any, inputs: dict[str, Any], outputs: Any, elapsed: int) -> None:
        ordinal = len(self.rows)
        raw = {
            "input": inputs["X"].tolist(),
            "output": outputs[0].tolist(),
            "weights": {name: evaluator.rt_inits_[name].tolist() for name in ("A", "B", "C")},
            "native_ns": elapsed,
            "effective_proto_sha256": content_sha256(evaluator.proto_.SerializeToString()),
            "effective_has_external_descriptors": any(t.external_data for t in evaluator.inits_),
        }
        if raw["output"] != matrix_output(raw["input"], raw["weights"]):
            raise ValueError("native inference differs from independent matrix arithmetic")
        captured = not (self.arm == "missing_capture" and ordinal == 1)
        frame = make_frame(ordinal, raw, captured)
        self.rows.append(
            {
                **raw,
                "frame": frame,
                "captured": captured,
                "reference": "compliant" if raw["weights"] == expected_weights() else "violation",
                "capture_answer": resolve(frame),
            }
        )

    def saved_model(self, filename: str) -> None:
        onnx = importlib.import_module("onnx")
        path = Path(filename)
        original = path.read_bytes()
        graph = onnx.load_model(str(path), load_external_data=False)
        descriptors = []
        for tensor in graph.graph.initializer:
            if tensor.external_data:
                info = {entry.key: entry.value for entry in tensor.external_data}
                descriptors.append({"name": tensor.name, **info})
        model_dir = self.directory / "native-files"
        write_new_file(model_dir / path.name, original)
        mutations = []
        for desc in descriptors:
            location = Path(desc["location"])
            if location.name != str(location):
                raise ValueError("bounded owned external dependency required")
            dependency = path.parent / location
            original_dependency = dependency.read_bytes()
            offset, length = dependency_span(desc, len(original_dependency))
            target = model_dir / location
            if not target.exists():
                write_new_file(target, original_dependency)
            if desc["name"] == "A" and self.arm in {"dependency_replaced", "restore_dependency"}:
                changed = (
                    original_dependency[:offset]
                    + bytes(length)
                    + original_dependency[offset + length :]
                )
                dependency.write_bytes(
                    changed
                )  # Owned upstream-generated native fixture, not user data.
                write_new_file(model_dir / (location.name + ".changed"), changed)
                if self.arm == "restore_dependency":
                    dependency.write_bytes(original_dependency)
                mutations.append(
                    {
                        "before": content_sha256(original_dependency),
                        "changed": content_sha256(changed),
                        "final": content_sha256(dependency.read_bytes()),
                        "name": desc["name"],
                    }
                )
        if path.read_bytes() != original:
            raise ValueError("dependency control changed origin graph")
        self.saved.append(
            {
                "origin_graph_sha256": content_sha256(original),
                "descriptors": descriptors,
                "mutations": mutations,
                "origin_graph_unchanged": True,
            }
        )


def worker(directory: Path, method: str, arm: str) -> dict[str, Any]:
    if method not in METHODS or arm not in ARMS or directory.exists():
        raise ValueError("fresh owned candidate cell required")
    directory.mkdir()
    path, identity = source_identity()
    native = runpy.run_path(str(path))
    onnx = importlib.import_module("onnx")
    run_native, save_native = (
        onnx.reference.ReferenceEvaluator.run,
        onnx.model_container.ModelContainer.save,
    )
    capture = TransferCapture(directory, arm)

    def observed_run(
        evaluator: Any, outputs: Any, feeds: dict[str, Any], *args: Any, **kwargs: Any
    ) -> Any:
        started = perf_counter_ns()
        result = run_native(evaluator, outputs, feeds, *args, **kwargs)
        capture.record(evaluator, feeds, result, perf_counter_ns() - started)
        return result

    def observed_save(container: Any, filename: str, *args: Any, **kwargs: Any) -> Any:
        result = save_native(container, filename, *args, **kwargs)
        capture.saved_model(filename)
        return result

    error = None
    with (
        patch.object(onnx.reference.ReferenceEvaluator, "run", observed_run),
        patch.object(onnx.model_container.ModelContainer, "save", observed_save),
    ):
        case = native["TestLargeOnnxReferenceEvaluator"](method)
        try:
            getattr(case, method)()
        except (AssertionError, ValueError, OSError, RuntimeError) as exc:
            error = type(exc).__name__  # Original assertion failure remains terminal.
    stores = {}
    for policy in ("static", "union_density"):
        archive = IncidentAuditArchive(directory / f"{policy}.sqlite", policy, 65536)
        for ordinal, row in enumerate(capture.rows):
            if not archive.put(row["frame"], now=ordinal):
                raise ValueError("provisioned transfer archive unexpectedly refused")
        scopes = [row["frame"]["token"] for row in capture.rows]
        answers = archive.query(scopes, now=len(capture.rows))
        witness = archive.witness(scopes)
        snapshot = archive.snapshot()
        archive.close()
        stores[policy] = {
            "answers": answers,
            "witness": witness,
            "storage": snapshot,
            "closed_db_bytes": archive.path.stat().st_size,
            "db_sha256": content_sha256(archive.path.read_bytes()),
        }
    return {
        "status": "candidate_executed",
        "method": method,
        "arm": arm,
        "source_identity": identity,
        "planned_native_calls": 3 if method == METHODS[0] else 2,
        "actual_native_calls": len(capture.rows),
        "native_test_error": error,
        "rows": capture.rows,
        "saved_models": capture.saved,
        "archives": stores,
        "native_files": {
            p.name: content_sha256(p.read_bytes()) for p in (directory / "native-files").iterdir()
        },
        "provider_calls": 0,
    }


def _child(root: Path, directory: Path, method: str, arm: str) -> dict[str, Any]:
    name = method.removeprefix("test_large_") + "-" + arm
    env = {
        k: v
        for k, v in os.environ.items()
        if k in {"PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TMP", "TEMP"}
    }
    env.update(PYTHONPATH=str(root / "src"), OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    try:
        child = subprocess.run(
            [
                sys.executable,
                "-m",
                "aletheia_lab.evaluation.incident_audit_transfer",
                "worker",
                "--root",
                str(root),
                "--study-dir",
                str(directory / name),
                "--method",
                method,
                "--arm",
                arm,
            ],
            capture_output=True,
            env=env,
            timeout=60,
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
    for suffix, value in (("stdout", stdout), ("stderr", stderr)):
        write_new_file(directory / f"{name}.{suffix}", value)
    report_path = directory / name / "report.json"
    report = (
        read_sealed(report_path)
        if failure is None
        else seal({**failure, "method": method, "arm": arm})
    )
    return {
        **report,
        "stream_hashes": {"stdout": content_sha256(stdout), "stderr": content_sha256(stderr)},
    }


def run(root: Path, directory: Path) -> dict[str, Any]:
    if directory.exists() or directory.is_symlink() or directory.is_relative_to(root):
        raise ValueError("fresh private transfer output outside repository required")
    source, identity = source_identity()
    files = (*FILES, IMPLEMENTATION)
    plan = seal(
        {
            "schema": "incident-audit-transfer-plan/v1",
            "source_identity": identity,
            "methods": METHODS,
            "arms": ARMS,
            "outcome_exposure": "record externally in the closeout; inspecting source is not untouched validation; technical corrections do not regain unused status",
            "classification": "prospective source-informed control transfer; source bodies already inspected",
            "forecasts": {
                "lawful": "all upstream assertions pass",
                "dependency_replaced": "saved graph unchanged; post-save inference changes and original assertion fails",
                "restore_dependency": "restoring A before load restores original inference assertions",
                "missing_capture": "native assertions pass; second linked-use audit unknown, retention cannot restore it",
                "baseline": "effective loaded ModelProto embeds dependencies; source/history is sufficient under fixture assumptions",
            },
            "code": {p: content_sha256((root / p).read_bytes()) for p in files},
        }
    )
    directory.mkdir(parents=True)
    write_new_file(directory / "source-snapshot.py", source.read_bytes())
    for name in files:
        write_new_file(directory / "code-snapshot" / name, (root / name).read_bytes())
    write_new_file(directory / "plan.json", encode(plan).encode())
    cells = [_child(root, directory, method, arm) for method in METHODS for arm in ARMS]
    result = seal(
        {
            "schema": "incident-audit-transfer-results/v1",
            "plan_sha256": plan["sha256"],
            "cells": cells,
            "planned_cells": 8,
            "failed_processes": sum(c["status"] != "candidate_executed" for c in cells),
            "provider_calls": 0,
        }
    )
    write_new_file(directory / "results.json", encode(result).encode())
    return {
        "status": "source_informed_transfer_executed",
        "planned_cells": 8,
        "results_sha256": result["sha256"],
        "failed_processes": result["failed_processes"],
        "provider_calls": 0,
    }


def verify_rows(rows: list[dict[str, Any]], arm: str) -> None:
    for ordinal, row in enumerate(rows):
        weights = expected_weights()
        if arm == "dependency_replaced" and ordinal > 0:
            weights["A"] = [[0.0] * 3 for _ in range(3)]
        values = [[float(i * 3 + j) for j in range(3)] for i in range(3)]
        if (
            row["weights"] != weights
            or row["input"] != values
            or row["output"] != matrix_output(values, weights)
        ):
            raise ValueError("candidate native arithmetic differs")
        captured = not (arm == "missing_capture" and ordinal == 1)
        if row["captured"] != captured or row["frame"] != make_frame(ordinal, row, captured):
            raise ValueError("candidate capture differs")
        if row["reference"] != (
            "violation" if weights != expected_weights() else "compliant"
        ) or row["capture_answer"] != resolve(row["frame"]):
            raise ValueError("candidate raw/capture reference differs")


def verify_cell(cell: dict[str, Any], directory: Path) -> dict[str, Any]:
    arm, rows = cell["arm"], cell["rows"]
    planned = 3 if cell["method"] == METHODS[0] else 2
    actual = 2 if arm == "dependency_replaced" else planned
    if (
        len(rows) != actual
        or cell["actual_native_calls"] != actual
        or cell["planned_native_calls"] != planned
    ):
        raise ValueError("planned/actual candidate census differs")
    if cell["native_test_error"] != ("AssertionError" if arm == "dependency_replaced" else None):
        raise ValueError("original assertion/error forecast differs")
    verify_rows(rows, arm)
    for policy, archive in cell["archives"].items():
        answers = {r["frame"]["token"]: r["capture_answer"] for r in rows}
        if archive["answers"] != answers or witness_answers(archive["witness"]) != answers:
            raise ValueError("candidate retained answers differ")
        path = directory / f"{policy}.sqlite"
        if (
            content_sha256(path.read_bytes()) != archive["db_sha256"]
            or path.stat().st_size != archive["closed_db_bytes"]
        ):
            raise ValueError("candidate durable database differs")
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as db:
            raw = bytes(db.execute("SELECT payload FROM state WHERE id=1").fetchone()[0])
            if content_sha256(raw) != archive["storage"]["state_sha256"]:
                raise ValueError("candidate durable frontier differs")
    for name, expected_hash in cell["native_files"].items():
        if (
            Path(name).name != name
            or content_sha256((directory / "native-files" / name).read_bytes()) != expected_hash
        ):
            raise ValueError("candidate native file differs")
    return {
        "native_calls": len(rows),
        "correct_capture": sum(r["capture_answer"] == r["reference"] for r in rows),
        "unknown_capture": sum(r["capture_answer"] == "unknown" for r in rows),
        "violations": sum(r["reference"] == "violation" for r in rows),
    }


def verify(root: Path, directory: Path) -> dict[str, Any]:
    plan, result = read_sealed(directory / "plan.json"), read_sealed(directory / "results.json")
    if (
        result["plan_sha256"] != plan["sha256"]
        or content_sha256((directory / "source-snapshot.py").read_bytes()) != SOURCE_HASH
    ):
        raise ValueError("candidate source/plan differs")
    for name, expected_hash in plan["code"].items():
        if (
            content_sha256((root / name).read_bytes()) != expected_hash
            or content_sha256((directory / "code-snapshot" / name).read_bytes()) != expected_hash
        ):
            raise ValueError("candidate code snapshot differs")
    if [(c["method"], c["arm"]) for c in result["cells"]] != [
        (m, a) for m in METHODS for a in ARMS
    ]:
        raise ValueError("candidate cell census differs")
    cells = []
    for cell in result["cells"]:
        name = cell["method"].removeprefix("test_large_") + "-" + cell["arm"]
        for stream, expected_hash in cell["stream_hashes"].items():
            if content_sha256((directory / f"{name}.{stream}").read_bytes()) != expected_hash:
                raise ValueError("candidate process stream differs")
        if cell["status"] != "candidate_executed":
            cells.append({"status": cell["status"], "method": cell["method"], "arm": cell["arm"]})
            continue
        report = read_sealed(directory / name / "report.json")
        if {k: v for k, v in cell.items() if k != "stream_hashes"} != report:
            raise ValueError("candidate raw report differs")
        cells.append(
            {"method": cell["method"], "arm": cell["arm"], **verify_cell(cell, directory / name)}
        )
    return {
        "verification": "pass",
        "failed_processes": result["failed_processes"],
        "cells": cells,
        "classification": "source-informed inference transfer; outcome exposure and technical corrections require their original closeout; not natural deployment or novel method",
        "provider_calls": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "worker", "verify"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--method", choices=METHODS, default=METHODS[0])
    parser.add_argument("--arm", choices=ARMS, default=ARMS[0])
    args = parser.parse_args()
    try:
        if args.command == "worker":
            result = seal(worker(args.study_dir.resolve(), args.method, args.arm))
            write_new_file(args.study_dir / "report.json", encode(result).encode())
        else:
            result = (run if args.command == "run" else verify)(
                args.root.resolve(), args.study_dir.resolve()
            )
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        print(encode({"status": "candidate_failed_closed", "error_type": type(exc).__name__}))
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
