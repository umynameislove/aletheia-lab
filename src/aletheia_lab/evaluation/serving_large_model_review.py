"""Read-only checks of retained large-ONNX development evidence.

This reconstructs numerical comparisons and SQLite joins from saved records;
it does not independently authenticate the historic session or trusted host.
No checkpoint download, model load, native inference or receipt rewriting.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.official_model_signing import artifact_closure, verify_local

CHECKPOINT_SHA256 = "cd907fc2a0de2292b2ba2b27d2216bc672d84632d1cf20058d01c0c03b5e020e"
INPUTS = ("zero", "checkerboard", "pcg64")


def _owned(root: Path, relative: str) -> Path:
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("retained evidence path escapes study")
    if root.is_symlink() or any(
        part.is_symlink() for part in (path, *path.parents) if part != root.parent
    ):
        raise ValueError("retained evidence must not use symlinks")
    return path


def _read(root: Path, relative: str) -> Any:
    return json.loads(_owned(root, relative).read_text(encoding="utf-8"))


def numerical_comparison(reference: np.ndarray, observed: np.ndarray) -> dict[str, Any]:
    shape = reference.shape == observed.shape == (1, 1000)
    finite = bool(np.isfinite(reference).all() and np.isfinite(observed).all())
    return {
        "shape_ok": shape,
        "finite": finite,
        "full_vector_tolerance_passed": shape
        and finite
        and bool(np.allclose(observed, reference, rtol=1e-4, atol=1e-5, equal_nan=False)),
        "max_absolute_error": float(np.max(np.abs(observed - reference))) if shape else None,
    }


def _qualification(root: Path, report: dict[str, Any]) -> list[dict[str, Any]]:
    spec = _read(root, "qualification-spec.json")
    contract = spec["qualification"]
    if (
        contract["rtol"] != 1e-4
        or contract["atol"] != 1e-5
        or contract["expected_output_shape"] != [1, 1000]
    ):
        raise ValueError("qualification tolerance or output domain differs")
    rows = []
    for name in INPUTS:
        reference = np.load(
            _owned(root, f"outputs/qualification-torch-{name}.npy"), allow_pickle=False
        )
        observed = np.load(
            _owned(root, f"outputs/qualification-ort-{name}.npy"), allow_pickle=False
        )
        rows.append({"input_id": name, **numerical_comparison(reference, observed)})
    if rows != report["qualification"] or not all(
        row["full_vector_tolerance_passed"] for row in rows
    ):
        raise ValueError("retained qualification differs or fails")
    return rows


def _receipt_rows(root: Path) -> list[tuple[Any, ...]]:
    path = _owned(root, "receipts.sqlite3")
    # immutable mode avoids creating WAL/SHM sidecars; accepted only for this
    # closed database with no pending WAL. Do not generalize to a live collector.
    if Path(str(path) + "-wal").exists():
        raise ValueError("closed checkpointed receipt database required")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("receipt database integrity differs")
        if connection.execute("SELECT count(*) FROM calls").fetchone() != (4,):
            raise ValueError("unjoined calls differ from offered census")
        generations = connection.execute(
            "SELECT generation_id FROM generations ORDER BY generation_id"
        ).fetchall()
        if generations != [("generation-0",), ("generation-1",)]:
            raise ValueError("native load generation census differs")
        return connection.execute(
            "SELECT calls.*, generations.closure_sha256, generations.verified FROM calls JOIN generations USING(generation_id) ORDER BY request_id"
        ).fetchall()
    finally:
        connection.close()


def _check_calls(root: Path, report: dict[str, Any], closure: dict[str, Any]) -> int:
    rows = _receipt_rows(root)
    if len(rows) != 4 or len(report["calls"]) != 4:
        raise ValueError("four-call development census differs")
    for index, (row, call) in enumerate(zip(rows, report["calls"], strict=True)):
        generation = "generation-0" if index < 3 else "generation-1"
        name = INPUTS[index] if index < 3 else INPUTS[0]
        _check_output(root, index, name)
        if (
            row[0:3] != (f"request-{index}", generation, name)
            or row[3] != file_sha256(_owned(root, f"inputs/{name}.npy"))
            or row[4] != f"outputs/request-{index}.npy"
            or row[5] != file_sha256(_owned(root, row[4]))
            or row[7] - row[6] != 30_000_000_000
            or row[9] != closure["closure_sha256"]
            or row[10] != 1
        ):
            raise ValueError("retained request/generation/operand join differs")
        if (
            call["request_id"] != row[0]
            or call["generation_id"] != generation
            or call["input_id"] != name
            or call["infer_ns"] != row[8]
            or call["late"] != (call["actual_audit_delay_ns"] > 30_000_000_000)
            or call["conclusive_correct"] is not True
            or call["identity_join_passed"] is not True
            or call["output_digest_passed"] is not True
        ):
            raise ValueError("aggregate call observation differs")
    late = sum(row["late"] for row in report["calls"])
    expected = {
        "offered": 4,
        "accepted": 4,
        "refused": 0,
        "complete": 4 - late,
        "unknown": 0,
        "conflict": 0,
        "incorrect": 0,
        "late": late,
        "accepted_but_unserved": late,
    }
    if report["service_census"] != expected:
        raise ValueError("service census differs")
    return len(rows)


def _check_output(root: Path, index: int, name: str) -> None:
    observed = np.load(_owned(root, f"outputs/request-{index}.npy"), allow_pickle=False)
    reference = np.load(_owned(root, f"outputs/qualification-torch-{name}.npy"), allow_pickle=False)
    if not numerical_comparison(reference, observed)["full_vector_tolerance_passed"]:
        raise ValueError("retained serving output violates locked export tolerance")


def verify_large_development(root: Path) -> dict[str, Any]:
    root = root.absolute()
    report_path = _owned(root, "results.json")
    before = file_sha256(report_path)
    report = _read(root, "results.json")
    if report["terminal_state"] != "completed":
        raise ValueError("development execution incomplete")
    if file_sha256(_owned(root, "resnet101-cd907fc2.pth")) != CHECKPOINT_SHA256:
        raise ValueError("official checkpoint identity differs")
    closure = artifact_closure(_owned(root, "model"))
    if (
        closure != report["artifact"]
        or closure != _read(root, "artifact-closure.json")
        or closure["total_bytes"] < 100 * 1024**2
    ):
        raise ValueError("large artifact closure differs")
    qualification = _qualification(root, report)
    verified_calls = _check_calls(root, report, closure)
    verify_local(
        _owned(root, "model"),
        _owned(root, "model.sig.json"),
        _owned(root, "verification-public.pem"),
    )
    if file_sha256(report_path) != before:
        raise ValueError("report changed during read-only verification")
    return {
        "status": "large_development_read_only_verification_pass",
        "result_file_sha256": before,
        "artifact_closure_bytes": closure["total_bytes"],
        "qualification_vector_count": len(qualification),
        "verified_receipt_count": verified_calls,
        "native_inference_reexecuted": False,
        "scope": "retained numerical arrays, crypto closure and closed SQLite joins; not host attestation or matched cost frontier",
    }
