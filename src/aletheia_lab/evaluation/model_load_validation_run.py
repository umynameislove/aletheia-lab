"""Bounded single-attempt execution, separate from the retained design lock."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation import model_load_validation as design
from aletheia_lab.evaluation.model_load_provenance import document_digest
from aletheia_lab.evaluation.model_load_validation_adapters import environment
from aletheia_lab.evaluation.model_load_validation_lifecycle import BoundedCapture, finish_captures
from aletheia_lab.evaluation.model_load_validation_replay import (
    annotate,
    reference,
    summarize,
    verify_rows,
)
from aletheia_lab.filesystem import publish_immutable_file

CLI = "scripts/run_model_load_validation.py"
EXTRA_CODE = (
    CLI,
    "src/aletheia_lab/evaluation/model_load_validation_run.py",
    "src/aletheia_lab/evaluation/model_load_validation_worker.py",
    "src/aletheia_lab/evaluation/model_load_validation_adapters.py",
    "src/aletheia_lab/evaluation/model_load_validation_lifecycle.py",
    "src/aletheia_lab/evaluation/model_load_validation_replay.py",
    "src/aletheia_lab/project/identity.py",
)
MAX_OUTPUT = 33_554_432


def publish(path: Path, value: dict[str, Any]) -> None:
    design._no_symlinks(path)
    payload = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    if len(payload) > MAX_OUTPUT:
        raise ValueError("private result exceeds frozen budget")
    if _private_budget(path.parent) + len(payload) > MAX_OUTPUT:
        raise ValueError("publication would exceed the private study budget")
    if publish_immutable_file(path, payload) != "created":
        raise FileExistsError("single-attempt output already exists")


def signed(value: dict[str, Any], field: str) -> dict[str, Any]:
    return {**value, field: document_digest(value)}


def require_document(
    value: dict[str, Any], expected: dict[str, Any], field: str, label: str = "scoped authority"
) -> None:
    """Compare the complete typed JSON, not Python bool/int-coercing equality."""
    if document_digest(value) != document_digest(signed(expected, field)):
        raise ValueError(label + " differs from its complete contract")


def lease_document(seal_hash: str) -> dict[str, Any]:
    return {
        "schema_version": "model-load-execution-lease/v1",
        "seal_sha256": seal_hash,
        "maximum_reserved_native_entries": 72,
        "provider_calls": 0,
    }


def read_signed(path: Path, field: str) -> dict[str, Any]:
    design._no_symlinks(path)
    if not path.is_file() or path.stat().st_size > MAX_OUTPUT:
        raise ValueError("missing or excessive private document")
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=design._object,
        parse_constant=design._constant,
    )
    if not isinstance(value, dict) or value.get(field) != document_digest(
        {k: v for k, v in value.items() if k != field}
    ):
        raise ValueError("private document identity mismatch")
    return value


def code_hashes(root: Path) -> dict[str, str]:
    result = {}
    for name in (*design.CODE_PATHS, *EXTRA_CODE):
        design._no_symlinks(root / name)
        result[name] = file_sha256(root / name)
    return result


def context(
    root: Path, plan: Path, study: Path, *, clean: bool = True
) -> tuple[Path, Path, dict[str, Any]]:
    root = design._root(root)
    study = design._private(study, root)
    report = design.preflight(root, plan)
    if clean and design._git(root, "status", "--porcelain"):
        raise ValueError("commit and merge the implementation before sealed preparation")
    return root, study, report


def worker(root: Path, arguments: list[str], timeout: float, directory: Path) -> dict[str, Any]:
    """OS-process timeout: kill and reap; never resume or retry a timed-out slot."""
    env = dict(os.environ)
    for key in tuple(env):
        if key.startswith(("OPENAI_", "AZURE_OPENAI_")) or key == "PYTEST_CURRENT_TEST":
            env.pop(key)
    env.update(
        PYTHONPATH=str(root / "src"),
        PYTHONDONTWRITEBYTECODE="1",
        OMP_NUM_THREADS="1",
        OPENBLAS_NUM_THREADS="1",
        MKL_NUM_THREADS="1",
    )
    out, err = BoundedCapture(4096), BoundedCapture(262144)
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, str(root / CLI), *arguments],
            cwd=root,
            env=env,
            stdout=out.writer,
            stderr=err.writer,
        )
        out.close_writer()
        err.close_writer()
        outcome = _wait_worker(process, timeout, out, err)
        return outcome or _worker_envelope(process, out, err)
    except subprocess.TimeoutExpired:
        if process is not None:
            process.kill()
            process.wait(timeout=10)
        return {"status": "slot_timeout", "error_type": "TimeoutExpired"}
    except (OSError, ValueError):
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        return {"status": "invalid_worker_output", "error_type": "CaptureFailure"}
    finally:
        try:
            finish_captures(out, err)
        except ValueError:
            out.failed.set()
            err.failed.set()


def _worker_envelope(
    process: subprocess.Popen[bytes], out: BoundedCapture, err: BoundedCapture
) -> dict[str, Any]:
    finish_captures(out, err)
    if out.exceeded.is_set() or err.exceeded.is_set():
        return {"status": "invalid_worker_output", "error_type": "OutputLimit"}
    if process.returncode:
        return {"status": "worker_failure", "error_type": "WorkerExit"}
    try:
        value = json.loads(out.data.decode("utf-8"), object_pairs_hook=design._object)
        if not isinstance(value, dict):
            raise ValueError("worker envelope is not an object")
        return value
    except (ValueError, UnicodeError):
        return {"status": "invalid_worker_output", "error_type": "InvalidEnvelope"}


def _wait_worker(
    process: subprocess.Popen[bytes], timeout: float, out: BoundedCapture, err: BoundedCapture
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while True:
        failed = out.failed.is_set() or err.failed.is_set()
        if failed or out.exceeded.is_set() or err.exceeded.is_set():
            process.kill()
            process.wait(timeout=10)
            return {
                "status": "invalid_worker_output",
                "error_type": "CaptureFailure" if failed else "OutputLimit",
            }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout)
        try:
            process.wait(timeout=min(0.1, remaining))
            return None
        except subprocess.TimeoutExpired:
            continue


def artifact_inventory(study: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for backend in ("onnxruntime", "skops"):
        pair = {}
        for name in ("A", "B"):
            path = study / "artifacts" / f"{backend}-{name}.buffer"
            design._no_symlinks(path)
            if not path.is_file() or not 0 < path.stat().st_size <= 262144:
                raise ValueError("missing or out-of-budget sealed artifact")
            pair[name] = {"sha256": file_sha256(path), "size": path.stat().st_size}
        if pair["A"]["sha256"] == pair["B"]["sha256"]:
            raise ValueError("A/B buffers must be distinct")
        result[backend] = pair
    return result


def prepare(root: Path, plan: Path, study: Path, confirmation: str) -> dict[str, Any]:
    root, study, report = context(root, plan, study)
    if confirmation != report["plan_sha256"]:
        raise ValueError("explicit preparation confirmation does not match design")
    inventory = environment()  # Metadata only, before consuming preparation authority.
    if study.exists():
        raise FileExistsError("retained preparation directory cannot be replaced")
    study.mkdir(mode=0o700)
    request = signed(
        {
            "schema_version": "model-load-preparation-authority/v1",
            "plan_sha256": confirmation,
            "code_sha256": code_hashes(root),
            "environment": inventory,
            "git_head": design._git(root, "rev-parse", "HEAD"),
            "max_local_fits": 2,
            "native_entries_authorized": 0,
        },
        "request_sha256",
    )
    publish(study / "preparation.json", request)
    outcome = worker(
        root,
        [
            "_prepare",
            "--root",
            str(root),
            "--plan",
            str(plan.absolute()),
            "--study-dir",
            str(study),
        ],
        60,
        study,
    )
    if outcome.get("status") != "artifacts_prepared_without_native_load":
        publish(study / "preparation-failure.json", outcome)
        return {
            "status": "preparation_failed_closed",
            "preparation_consumed": True,
            "provider_calls": 0,
            "native_loader_entries": 0,
        }
    if code_hashes(root) != request["code_sha256"] or environment() != inventory:
        raise ValueError("preparation code or environment changed")
    seal = signed(
        {
            "schema_version": "model-load-execution-seal/v1",
            "plan_sha256": confirmation,
            "protocol_sha256": report["protocol_sha256"],
            "code_sha256": request["code_sha256"],
            "environment": inventory,
            "creation_git_head": request["git_head"],
            "artifacts": artifact_inventory(study),
            "preparation_sha256": request["request_sha256"],
            "local_fits_completed": 2,
            "native_loader_entries": 0,
            "provider_calls": 0,
            "authority": "readonly_single_attempt_requires_exact_seal_confirmation",
        },
        "seal_sha256",
    )
    publish(study / "seal.json", seal)
    return seal_summary(seal, "artifacts_sealed_no_native_load")


def check_seal(
    root: Path, plan: Path, study: Path, *, clean: bool = True
) -> tuple[Path, Path, dict[str, Any]]:
    root, study, report = context(root, plan, study, clean=clean)
    seal = read_signed(study / "seal.json", "seal_sha256")
    request = read_signed(study / "preparation.json", "request_sha256")
    if not isinstance(seal["creation_git_head"], str) or not re.fullmatch(
        r"[0-9a-f]{40}", seal["creation_git_head"]
    ):
        raise ValueError("invalid execution creation commit")
    require_document(
        request,
        {
            "schema_version": "model-load-preparation-authority/v1",
            "plan_sha256": report["plan_sha256"],
            "code_sha256": seal["code_sha256"],
            "environment": seal["environment"],
            "git_head": seal["creation_git_head"],
            "max_local_fits": 2,
            "native_entries_authorized": 0,
        },
        "request_sha256",
        "execution seal no longer matches preparation authority",
    )
    require_document(
        seal,
        {
            "schema_version": "model-load-execution-seal/v1",
            "plan_sha256": report["plan_sha256"],
            "protocol_sha256": report["protocol_sha256"],
            "code_sha256": code_hashes(root),
            "environment": environment(),
            "creation_git_head": request["git_head"],
            "artifacts": artifact_inventory(study),
            "preparation_sha256": request["request_sha256"],
            "local_fits_completed": 2,
            "native_loader_entries": 0,
            "provider_calls": 0,
            "authority": "readonly_single_attempt_requires_exact_seal_confirmation",
        },
        "seal_sha256",
        "execution seal no longer matches code, environment and artifacts",
    )
    design._git(root, "merge-base", "--is-ancestor", seal["creation_git_head"], "HEAD")
    return root, study, seal


def seal_summary(seal: dict[str, Any], status: str) -> dict[str, Any]:
    return {
        "status": status,
        "seal_sha256": seal["seal_sha256"],
        "plan_sha256": seal["plan_sha256"],
        "planned_slots": 48,
        "maximum_native_entries": 72,
        "maximum_local_fits": 2,
        "local_fits_completed": seal["local_fits_completed"],
        "provider_calls": 0,
        "native_loader_entries": 0,
        "runner_implemented": True,
        "runtime_compatibility_verified": False,
        "execution_authorized": False,
    }


def preflight(root: Path, plan: Path, study: Path) -> dict[str, Any]:
    _, study, seal = check_seal(root, plan, study)
    result = seal_summary(seal, "sealed_validation_execution_preflight_pass")
    result["execution_ready"] = not (study / "lease.json").exists()
    result["attempt_consumed"] = (study / "lease.json").exists()
    return result


def _private_budget(study: Path) -> int:
    paths = list(study.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("private study contains a symlink")
    return sum(path.stat().st_size for path in paths if path.is_file())


def _slot_row(
    study: Path, index: int, slot: dict[str, Any], outcome: dict[str, Any]
) -> dict[str, Any]:
    if outcome.get("status") != "slot_worker_complete":
        raw_path = study / f"slot-raw-{index:02d}.json"
        if raw_path.exists():
            try:
                captured = read_signed(raw_path, "row_sha256")
                if captured["slot"] != slot:
                    raise ValueError("retained raw worker row belongs to another slot")
                return {**captured, **outcome}
            except (ValueError, KeyError, OSError):
                return {
                    "slot": slot,
                    "status": "invalid_worker_output",
                    "error_type": "InvalidRawRow",
                }
        return {"slot": slot, **outcome}
    try:
        row = read_signed(study / f"slot-{index:02d}.json", "row_sha256")
        if row["slot"] != slot:
            raise ValueError("worker returned another slot")
        return row
    except (ValueError, KeyError, OSError):
        return {"slot": slot, "status": "invalid_worker_output", "error_type": "InvalidRow"}


def _storage_ready(
    study: Path, rows: list[dict[str, Any]], slot: dict[str, Any], seal: dict[str, Any]
) -> bool:
    """Reserve the worst next row plus terminal census before authorizing native work.

    Exact JSON escaping can expand message bytes sixfold. Reference and frame
    each retain them; raw, scored, analysis and terminal rows are four persistent copies.
    Existing results are not discarded if the next slot cannot fit.
    """
    size = max(v["size"] for v in seal["artifacts"][slot["backend"]].values())
    entries = slot["planned_target_entries"] + slot["planned_auxiliary_entries"]
    row_bound = 2 * size * (entries + 3) + 2 * 2 * 262144 * 6 + 65536
    retained_terminal = len(json.dumps(rows, sort_keys=True, allow_nan=False).encode())
    # Spool/observer/provenance files plus fixed endpoints and all unexecuted rows.
    required = _private_budget(study) + retained_terminal + 4 * row_bound + size + 1_048_576
    return bool(required <= MAX_OUTPUT)


def _accept_row(row: dict[str, Any], digests: dict[str, str], directory: Path) -> dict[str, Any]:
    if row["status"] in {"completed", "technical_failure"}:
        if reference(row, digests) != row["reference"]:
            raise ValueError("worker reference changed before terminalization")
        return row
    return annotate(row, digests, directory)


def execute(root: Path, plan: Path, study: Path, confirmation: str) -> dict[str, Any]:
    root, study, seal = check_seal(root, plan, study)
    if confirmation != seal["seal_sha256"]:
        raise ValueError("exact native-execution seal confirmation is required")
    lease = signed(lease_document(confirmation), "lease_sha256")
    publish(study / "lease.json", lease)  # Never accept an identical racing lease.
    protocol = design.load_protocol(root)
    slots = design.census(protocol)
    digests = {
        b: {n: v["sha256"] for n, v in pair.items()} for b, pair in seal["artifacts"].items()
    }
    started, reserved = time.monotonic(), 0
    rows: list[dict[str, Any]] = []
    stop: str | None = None
    for index, slot in enumerate(slots):
        allowance = slot["planned_target_entries"] + slot["planned_auxiliary_entries"]
        remaining = 1800 - (time.monotonic() - started)
        if stop is None and (remaining <= 0 or not _storage_ready(study, rows, slot, seal)):
            stop = "ResourceLimit"
        if stop is not None:
            row = {"slot": slot, "status": "unexecuted", "error_type": stop}
        else:
            reserved += allowance  # Failed/killed worker never releases native authority.
            if reserved > 72:
                raise ValueError("frozen native reservation exceeded")
            outcome = worker(
                root,
                [
                    "_slot",
                    "--root",
                    str(root),
                    "--plan",
                    str(plan.absolute()),
                    "--study-dir",
                    str(study),
                    "--slot-index",
                    str(index),
                ],
                min(30, remaining),
                study,
            )
            row = _slot_row(study, index, slot, outcome)
            if row["status"] in {"slot_timeout", "invalid_worker_output", "worker_failure"}:
                stop = "UnavailableNativeCensus"
        try:
            row = _accept_row(row, digests[slot["backend"]], study / f"provenance-{index:02d}")
        except (ValueError, KeyError, TypeError, OSError) as exc:
            stop = "ReferenceIntegrityFailure"
            row = annotate(
                {
                    "slot": slot,
                    "status": "reference_integrity_failure",
                    "error_type": type(exc).__name__,
                    "retained_worker_row": row,
                },
                digests[slot["backend"]],
                study / f"failed-{index:02d}",
            )
        rows.append(row)
        publish(study / f"analysis-{index:02d}.json", row)
        if _private_budget(study) > MAX_OUTPUT:
            stop = "ResourceLimit"
    analysis = summarize(rows)
    result = signed(
        {
            "schema_version": "model-load-validation-results/v1",
            "seal_sha256": confirmation,
            "lease_sha256": lease["lease_sha256"],
            "reserved_native_entries": reserved,
            "rows": rows,
            "analysis": analysis,
            "provider_calls": 0,
        },
        "results_sha256",
    )
    publish(study / "results.json", result)
    return result_summary(result)


def result_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "model_load_validation_terminalized",
        "results_sha256": result["results_sha256"],
        "seal_sha256": result["seal_sha256"],
        "analysis": result["analysis"],
        "reserved_native_entries": result["reserved_native_entries"],
        "provider_calls": 0,
    }


def verify(root: Path, plan: Path, study: Path) -> dict[str, Any]:
    root, study, seal = check_seal(root, plan, study, clean=False)
    result = read_signed(study / "results.json", "results_sha256")
    lease = read_signed(study / "lease.json", "lease_sha256")
    require_document(lease, lease_document(seal["seal_sha256"]), "lease_sha256")
    if (
        result["seal_sha256"] != seal["seal_sha256"]
        or result["lease_sha256"] != lease["lease_sha256"]
        or lease["seal_sha256"] != seal["seal_sha256"]
        or lease["maximum_reserved_native_entries"] != 72
        or lease["provider_calls"] != 0
        or result["provider_calls"] != 0
    ):
        raise ValueError("result authority binding mismatch")
    slots = design.census(design.load_protocol(root))
    digests = {
        b: {n: v["sha256"] for n, v in pair.items()} for b, pair in seal["artifacts"].items()
    }
    analysis = verify_rows(result["rows"], slots, digests)
    if document_digest(analysis) != document_digest(result["analysis"]):
        raise ValueError("descriptive endpoints changed")
    reservation = sum(
        s["planned_target_entries"] + s["planned_auxiliary_entries"]
        for s, row in zip(slots, result["rows"], strict=True)
        if row["status"] != "unexecuted"
    )
    if result["reserved_native_entries"] != reservation or reservation > 72:
        raise ValueError("native reservation census changed")
    require_document(
        result,
        {
            "schema_version": "model-load-validation-results/v1",
            "seal_sha256": seal["seal_sha256"],
            "lease_sha256": lease["lease_sha256"],
            "reserved_native_entries": reservation,
            "rows": result["rows"],
            "analysis": analysis,
            "provider_calls": 0,
        },
        "results_sha256",
        "result authority",
    )
    for index, row in enumerate(result["rows"]):
        if document_digest(
            read_signed(study / f"analysis-{index:02d}.json", "row_sha256")
        ) != document_digest(row):
            raise ValueError("terminal row differs from retained per-slot analysis")
    return {**result_summary(result), "verification": "pass", "native_loads_replayed": 0}
