"""Sequential native ORT workload with separately reopened audit evidence.

Only locally qualified immutable artifacts are executable. Caller/load journals
are honest-host references, not attestation or proof of arbitrary completeness.
"""

from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path
from time import perf_counter_ns, process_time_ns
from typing import Any

import numpy as np

from aletheia_lab.evaluation.official_model_signing import artifact_closure, verify_local
from aletheia_lab.evaluation.serving_audit_cost_store import Store, read_records
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

ARMS = ("native", "hash_only", "static", "compact", "full")
AUDIT_ARMS = ARMS[2:]
INPUTS = ("zero", "checkerboard", "pcg64")


def encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def session(model: Path) -> Any:
    ort = importlib.import_module("onnxruntime")
    options = ort.SessionOptions()
    options.intra_op_num_threads = options.inter_op_num_threads = 1
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(str(model), options, providers=["CPUExecutionProvider"])


def rss_bytes() -> int | None:
    try:
        resource = importlib.import_module("resource")
        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return value if sys.platform == "darwin" else value * 1024
    except (ImportError, AttributeError):
        return None


def allocated(root: Path) -> dict[str, int]:
    result = {"logical_bytes": 0, "allocated_bytes": 0}
    for path in root.rglob("*"):
        if path.is_file():
            info = path.stat()
            result["logical_bytes"] += info.st_size
            result["allocated_bytes"] += getattr(info, "st_blocks", 0) * 512
    return result


def storage_files(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "logical_bytes": path.stat().st_size,
            "allocated_bytes": getattr(path.stat(), "st_blocks", 0) * 512,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _verify_artifact(qualified: Path, expected: dict[str, Any]) -> dict[str, Any]:
    observed = verify_local(
        qualified / "model", qualified / "model.sig.json", qualified / "verification-public.pem"
    )
    if observed != expected:
        raise ValueError("authenticated actual closure differs from locked expected closure")
    return observed


def _call(
    native: Any,
    array: np.ndarray,
    generation: dict[str, Any],
    index: int,
    offered_ns: int,
    capture: bool = True,
) -> tuple[np.ndarray, dict[str, Any]]:
    start = perf_counter_ns()
    observed = native.run(None, {native.get_inputs()[0].name: array})[0]
    end = perf_counter_ns()
    if not capture:
        return observed, {
            "request_id": f"request-{index}",
            "index": index,
            "inference_start_ns": start,
            "inference_end_ns": end,
        }
    capture_start = perf_counter_ns()
    result = np.ascontiguousarray(observed, dtype=np.float32)
    metadata = {
        "request_id": f"request-{index}",
        "generation_id": generation["generation_id"],
        "session_object": id(native),
        "index": index,
        "input_id": INPUTS[index % 3],
        "offered_ns": offered_ns,
        "deadline_ns": offered_ns + 30_000_000_000,
        "inference_start_ns": start,
        "inference_end_ns": end,
        "input_sha256": content_sha256(array.tobytes()),
        "output_sha256": content_sha256(result.tobytes()),
        "input_shape": list(array.shape),
        "output_shape": list(result.shape),
        "dtype": "float32",
        "closed": True,
        "failed": False,
    }
    metadata["capture_ns"] = perf_counter_ns() - capture_start
    return result, metadata


def _loads(
    qualified: Path, plan: dict[str, Any], arm: str, index: int
) -> tuple[Any, dict[str, Any]]:
    verification_start = perf_counter_ns()
    if arm == "hash_only":
        if artifact_closure(qualified / "model") != plan["artifact"]:
            raise ValueError("hash-only closure mismatch")
    elif arm in AUDIT_ARMS:
        observed_closure = _verify_artifact(qualified, plan["artifact"])
    verification_ns = perf_counter_ns() - verification_start
    load_start = perf_counter_ns()
    native = session(qualified / "model/model.onnx")
    load_ns = perf_counter_ns() - load_start
    return native, {
        "generation_id": f"generation-{index}",
        "load_index": index,
        "closure_sha256": observed_closure["closure_sha256"]
        if arm in AUDIT_ARMS
        else plan["artifact"]["closure_sha256"],
        "files": observed_closure["files"] if arm in AUDIT_ARMS else plan["artifact"]["files"],
        "session_object": id(native),
        "signature_verified": arm in AUDIT_ARMS,
        "load_ns": load_ns,
        "signature_including_hash_ns": verification_ns if arm in AUDIT_ARMS else 0,
        "hash_only_ns": verification_ns if arm == "hash_only" else 0,
    }


def audit_one(
    generation: dict[str, Any],
    record: dict[str, Any],
    operand: bytes,
    output: bytes,
    caller: dict[str, Any],
    load: dict[str, Any],
    ack: int | None,
    reference: np.ndarray,
    expected: str,
) -> tuple[str, dict[str, int]]:
    """Separated checks consume retained bytes, never writer output objects."""
    start = perf_counter_ns()
    digests_ok = (
        content_sha256(operand) == record["input_sha256"]
        and content_sha256(output) == record["output_sha256"]
    )
    verify_ns = perf_counter_ns() - start
    start = perf_counter_ns()
    if not digests_ok:
        verdict = "conflict"
    elif ack is None or not record["closed"] or record["failed"]:
        verdict = "unknown"
    elif (
        record["request_id"] != caller["request_id"]
        or record["input_sha256"] != caller["input_sha256"]
        or record["session_object"] != caller["session_object"]
        or record["generation_id"] != caller["generation_id"]
        or record["offered_ns"] != caller["offered_ns"]
        or record["session_object"] != load["session_object"]
        or generation["session_object"] != load["session_object"]
        or generation["generation_id"] != load["generation_id"]
    ):
        verdict = "conflict"
    elif generation["closure_sha256"] != expected or not generation["signature_verified"]:
        verdict = "incorrect"
    else:
        vector = np.frombuffer(output, dtype=np.float32)
        correct = vector.size == reference.size and bool(np.isfinite(vector).all())
        if correct:
            correct = bool(
                np.allclose(vector.reshape(reference.shape), reference, rtol=1e-4, atol=1e-5)
            )
        verdict = "correct" if correct else "incorrect"
    return verdict, {"verify_ns": verify_ns, "query_ns": perf_counter_ns() - start}


def _audit(
    directory: Path,
    arm: str,
    qualified: Path,
    plan: dict[str, Any],
    callers: list[dict[str, Any]],
    loads: list[dict[str, Any]],
    acks: dict[str, int],
) -> list[dict[str, Any]]:
    start = perf_counter_ns()
    retained = read_records(directory / "store", arm)
    retrieval_ns = perf_counter_ns() - start
    # These references are separated numerical qualification; model recovery is
    # exercised below on retained operands, not satisfied by the reference arrays.
    reference = {
        name: np.load(qualified / f"outputs/qualification-torch-{name}.npy", allow_pickle=False)
        for name in INPUTS
    }
    generation_by_id = {row["generation_id"]: row for row in loads}
    caller_by_id = {row["request_id"]: row for row in callers}
    # Recovery completes before an audit is declared fulfilled. This is native
    # replay against stored operands, not a free writer-memory/oracle refetch.
    start = perf_counter_ns()
    _verify_artifact(qualified, plan["artifact"])
    recovery = session(qualified / "model/model.onnx")
    unique = {
        record["input_sha256"]: (record, operand, output)
        for _, record, operand, output, _ in retained
    }
    for record, operand, output in unique.values():
        actual = recovery.run(
            None,
            {
                recovery.get_inputs()[0].name: np.frombuffer(operand, dtype=np.float32).reshape(
                    record["input_shape"]
                )
            },
        )[0]
        if not np.allclose(
            actual,
            np.frombuffer(output, dtype=np.float32).reshape(record["output_shape"]),
            rtol=1e-4,
            atol=1e-5,
        ):
            raise ValueError("retained native replay failed")
    recovery_ns = perf_counter_ns() - start
    audits = []
    for generation, record, operand, output, commit_ns in retained:
        verdict, timings = audit_one(
            generation,
            record,
            operand,
            output,
            caller_by_id[record["request_id"]],
            generation_by_id[record["generation_id"]],
            commit_ns
            if record["request_id"] in acks
            and commit_ns is not None
            and acks[record["request_id"]] >= commit_ns
            else None,
            reference[record["input_id"]],
            plan["artifact"]["closure_sha256"],
        )
        audit_ns = perf_counter_ns()
        late = audit_ns > record["deadline_ns"]
        audits.append(
            {
                "request_id": record["request_id"],
                "verdict": verdict,
                "late": late,
                "audit_ns": audit_ns,
                "ack_ns": acks.get(record["request_id"]),
                "commit_return_ns": commit_ns,
                **timings,
            }
        )
    if len(audits) != len(callers) or len({row["request_id"] for row in audits}) != len(callers):
        raise ValueError("retained audit census differs")
    audits[0]["shared_recovery_ns"] = recovery_ns
    audits[0]["store_retrieval_ns"] = retrieval_ns
    return audits


def _drain(store: Store | None) -> None:
    if store is not None:
        store.flush()
        store.close()


def run_worker(plan: dict[str, Any], config: dict[str, Any], directory: Path) -> dict[str, Any]:
    arm, pattern = config["arm"], config["pattern"]
    if arm not in ARMS or pattern not in ("reuse", "reload"):
        raise ValueError("unplanned workload configuration")
    directory.mkdir(exist_ok=False)
    qualified = Path(plan["qualified_directory"])
    arrays = [
        np.ascontiguousarray(
            np.load(qualified / f"inputs/{name}.npy", allow_pickle=False), dtype=np.float32
        )
        for name in INPUTS
    ]
    input_digests = (
        [content_sha256(array.tobytes()) for array in arrays] if arm in AUDIT_ARMS else []
    )
    store = Store(directory / "store", arm) if arm in AUDIT_ARMS else None
    rows: list[dict[str, Any]] = []
    loads: list[dict[str, Any]] = []
    callers: list[dict[str, Any]] = []
    acks: dict[str, int] = {}
    peaks = allocated(directory)
    storage_samples: list[dict[str, Any]] = []
    cpu_start, start = process_time_ns(), perf_counter_ns()
    native: Any = None
    generation: dict[str, Any] = {}
    terminal, error = "complete", None
    attempted = 0
    try:
        for index in range(64):
            offered = perf_counter_ns()
            if index == 0 or (pattern == "reload" and index % 8 == 0):
                native, generation = _loads(qualified, plan, arm, index)
                loads.append(generation)
            array = arrays[index % 3]
            # Independent caller intent/load association is retained outside
            # collector. It cannot prove a malicious host called this object.
            if store is not None:
                callers.append(
                    {
                        "request_id": f"request-{index}",
                        "input_sha256": input_digests[index % 3],
                        "session_object": id(native),
                        "generation_id": generation["generation_id"],
                        "offered_ns": offered,
                    }
                )
            attempted += 1
            output, row = _call(native, array, generation, index, offered, store is not None)
            rows.append(row)
            if store is not None:
                stage = perf_counter_ns()
                store.add(generation, row, array.tobytes(), output.tobytes())
                row["write_ns"] = perf_counter_ns() - stage
                if (index + 1) % 8 == 0:
                    stage = perf_counter_ns()
                    committed = store.flush()
                    observed_ack = perf_counter_ns()
                    acks.update({request_id: observed_ack for request_id in committed})
                    row["write_ack_ns"] = observed_ack - stage
                    sample = allocated(directory)
                    peaks = {key: max(peaks[key], sample[key]) for key in peaks}
                    storage_samples.append({"after_call": index, "files": storage_files(directory)})
            row["serving_return_ns"] = perf_counter_ns()
            row["serving_latency_ns"] = row["serving_return_ns"] - offered
        _drain(store)
        workload_end = perf_counter_ns()
        write_new_file(
            directory / "caller-journal.json",
            encoded({"calls": callers, "loads": loads, "acks": acks}),
        )
        del native
        audits: list[dict[str, Any]] = []
        if store is not None:
            time.sleep(1)
            # Re-read separated caller references; writer object maps are gone.
            caller = json.loads((directory / "caller-journal.json").read_bytes())
            audits = _audit(
                directory, arm, qualified, plan, caller["calls"], caller["loads"], caller["acks"]
            )
    except (ValueError, OSError, RuntimeError) as exc:
        terminal, error = "failed", type(exc).__name__
        workload_end = perf_counter_ns()
        audits = []
        if store is not None:
            store.close()
    end = perf_counter_ns()
    result = {
        "config": config,
        "terminal": terminal,
        "error_type": error,
        "planned_native_calls": 64,
        "native_calls": len(rows),
        "native_call_attempts": attempted,
        "planned_loads": 1 if pattern == "reuse" else 8,
        "loads": loads,
        "calls": rows,
        "audits": audits,
        "accepted": len(callers) if store is not None else 0,
        "refused": 0,
        "unattempted": 64 - len(rows),
        "workload_through_final_ack_ns": workload_end - start,
        "end_to_end_service_ns": end - start,
        "cpu_ns": process_time_ns() - cpu_start,
        "sampled_storage_peak": peaks,
        "storage_samples": storage_samples,
        "closed_storage_files": storage_files(directory),
        "closed_storage": allocated(directory),
        "peak_rss_bytes": rss_bytes(),
        "rss_scope": "fresh child high-water, not instantaneous",
        "recovery_native_calls": 3 if audits else 0,
    }
    write_new_file(directory / "worker.json", encoded(result))
    return result
