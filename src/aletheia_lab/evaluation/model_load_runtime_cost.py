"""Local native-load costs, separate from sealed validation and concurrency.

Fresh workers import the SDK before timing. First-use is not OS-cold I/O;
warm samples deserialize fresh objects, not cache hits. All arms use the same
adapter shell; the disabled baseline omits observer work, not adapter hooks.
"""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from itertools import product
from pathlib import Path
from typing import Any, cast

from aletheia_lab.evaluation.model_load_attempt_retention import AttemptReceiptStore
from aletheia_lab.evaluation.model_load_contract import LoadContract, Record, Scope, receipt_checker
from aletheia_lab.evaluation.model_load_provenance import document_digest
from aletheia_lab.evaluation.model_load_retention import Selection
from aletheia_lab.evaluation.model_load_validation_adapters import (
    NativeAdapter,
    build_artifacts,
    environment,
    no_network,
)
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.project.identity import content_sha256 as _digest

MODES = ("observer_disabled", "hash_only", "full", "static_sufficient")
BACKENDS = ("onnxruntime", "skops")
SAMPLES = 3
REPEATS = 3
PHASE_KEYS = frozenset(
    (
        "read_ns",
        "hash_ns",
        "capture_ns",
        "write_ns",
        "query_ns",
        "checker_ns",
        "native_entry_ns",
        "sdk_load_ns",
        "object_release_ns",
        "store_setup_ns",
        "control_validation_hash_ns",
    )
)


def _capture_record(store: AttemptReceiptStore, value: Record, phases: dict[str, int]) -> None:
    write = time.perf_counter_ns()
    store.submit(value.scope.request, value)
    phases["write_ns"] += time.perf_counter_ns() - write


def _callbacks(
    payload: bytes,
    mode: str,
    scope: Scope,
    domain: tuple[str, ...],
    store: AttemptReceiptStore | None,
    phases: dict[str, int],
    counts: dict[str, int],
) -> tuple[Callable[[bytes], None], Callable[[bool], None]]:
    def before(offered: bytes) -> None:
        if offered is not payload:
            raise ValueError("native entry did not receive the measured buffer")
        counts["offered"] += 1
        capture_started = time.perf_counter_ns()
        old_hash, old_write = phases["hash_ns"], phases["write_ns"]
        if mode != "observer_disabled":
            hashed = time.perf_counter_ns()
            observed = _digest(offered)
            phases["hash_ns"] += time.perf_counter_ns() - hashed
            if store is not None:
                _capture_record(
                    store,
                    Record(
                        "selection", scope, "selection", domain[0], "pin", 0, "pin_at_acceptance"
                    ),
                    phases,
                )
                _capture_record(store, Record("load", scope, "load", observed, "pin"), phases)
        phases["capture_ns"] += (
            time.perf_counter_ns()
            - capture_started
            - (phases["hash_ns"] - old_hash)
            - (phases["write_ns"] - old_write)
        )
        counts["native_started"] = time.perf_counter_ns()

    def after(success: bool) -> None:
        phases["native_entry_ns"] += time.perf_counter_ns() - counts["native_started"]
        counts["entered"] += 1
        counts["completed"] += int(success)

    return before, after


def _measure(
    backend: str, mode: str, payload_path: Path, domain: tuple[str, ...], index: int
) -> dict[str, Any]:
    scope = Scope(f"cost-{index}", 0)
    contract = LoadContract("pin_at_acceptance", domain)
    store = None
    setup = time.perf_counter_ns()
    if mode in {"full", "static_sufficient"}:
        store = AttemptReceiptStore(
            payload_path.parent / f"{mode}-{index}.sqlite", selection=cast(Selection, mode)
        )
        store.register((scope,), (scope,))
    phases = {
        name: 0
        for name in (
            "read_ns",
            "hash_ns",
            "capture_ns",
            "write_ns",
            "query_ns",
            "checker_ns",
            "native_entry_ns",
            "sdk_load_ns",
            "object_release_ns",
        )
    }
    phases["store_setup_ns"] = time.perf_counter_ns() - setup
    read = time.perf_counter_ns()
    payload = payload_path.read_bytes()
    phases["read_ns"] = time.perf_counter_ns() - read
    counts = {"offered": 0, "entered": 0, "completed": 0, "native_started": 0}
    before, after = _callbacks(payload, mode, scope, domain, store, phases, counts)

    start = time.perf_counter_ns()
    status, error = "completed", None
    try:
        sdk_started = time.perf_counter_ns()
        try:
            model = NativeAdapter(backend, before, after).load(payload)
        finally:
            phases["sdk_load_ns"] = time.perf_counter_ns() - sdk_started
        release = time.perf_counter_ns()
        del model
        phases["object_release_ns"] = time.perf_counter_ns() - release
        if store is not None:
            capture = time.perf_counter_ns()
            closure = Record("closure", scope, "closure", load_count=counts["entered"])
            phases["capture_ns"] += time.perf_counter_ns() - capture
            _capture_record(store, closure, phases)
            query = time.perf_counter_ns()
            obs = store.snapshot(contract, scope)
            phases["query_ns"] = time.perf_counter_ns() - query
            check = time.perf_counter_ns()
            decision = receipt_checker(obs)
            phases["checker_ns"] = time.perf_counter_ns() - check
            if decision.verdict != "compliant":
                raise ValueError("measured receipt query failed its declared contract")
    except (ValueError, RuntimeError, OSError, TypeError, sqlite3.Error) as exc:
        status, error = "failed", type(exc).__name__
    elapsed = time.perf_counter_ns() - start
    # Common control verification, excluded from the measured observer endpoint.
    control = time.perf_counter_ns()
    actual_digest = _digest(payload)
    phases["control_validation_hash_ns"] = time.perf_counter_ns() - control
    if actual_digest != domain[0]:
        status, error = "failed", "ArtifactChanged"
    resources = store.finish() if store is not None else None
    return {
        "sample": index,
        "temperature": "first_use_after_import" if index == 0 else "warm_fresh_object",
        "status": status,
        "error_type": error,
        "artifact_bytes": len(payload),
        "artifact_sha256": actual_digest,
        "native_entries": counts["entered"],
        "offered_boundaries": counts["offered"],
        "native_completed": counts["completed"],
        "load_and_observer_ns": elapsed,
        "read_to_decision_ns": phases["read_ns"] + elapsed,
        "phases": phases,
        "resources": resources,
    }


def worker(backend: str, mode: str, directory: Path) -> dict[str, Any]:
    """Worker gets only fresh local exposed-development artifacts, never study data."""
    if backend not in BACKENDS or mode not in MODES:
        raise ValueError("worker configuration outside fixed census")
    started = time.perf_counter_ns()
    env = environment()
    manifest = json.loads((directory / "artifacts.json").read_text())
    domain = tuple(manifest[backend][label]["sha256"] for label in ("A", "B"))
    path = directory / f"{backend}-A.bin"
    payload = path.read_bytes()
    if not 0 < len(payload) <= 262_144 or _digest(payload) != domain[0]:
        raise ValueError("fresh artifact does not match preparation receipt")
    with no_network():
        importlib.import_module("onnxruntime" if backend == "onnxruntime" else "skops.io")
        setup_ns = time.perf_counter_ns() - started
        samples = [_measure(backend, mode, path, domain, index) for index in range(SAMPLES)]
    return {
        "backend": backend,
        "mode": mode,
        "setup_and_import_ns": setup_ns,
        "environment": env,
        "samples": samples,
    }


def _prepare(directory: Path) -> dict[str, Any]:
    with no_network():
        artifacts = build_artifacts()
    manifest: dict[str, Any] = {}
    for backend, pair in artifacts.items():
        manifest[backend] = {}
        for label, payload in pair.items():
            publish_immutable_file(directory / f"{backend}-{label}.bin", payload)
            manifest[backend][label] = {"sha256": _digest(payload), "bytes": len(payload)}
    publish_immutable_file(directory / "artifacts.json", json.dumps(manifest).encode())
    return manifest


def summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Rebuild local descriptive cost comparisons; failed samples stay in census."""
    groups: dict[str, Any] = {}
    for backend in BACKENDS:
        for temperature in ("first_use_after_import", "warm_fresh_object"):
            key = f"{backend}/{temperature}"
            base = [
                row["load_and_observer_ns"]
                for row in samples
                if row["backend"] == backend
                and row["temperature"] == temperature
                and row["mode"] == MODES[0]
                and row["status"] == "completed"
            ]
            rows: dict[str, Any] = {}
            for mode in MODES:
                values = [
                    row
                    for row in samples
                    if row["backend"] == backend
                    and row["temperature"] == temperature
                    and row["mode"] == mode
                ]
                completed = [row for row in values if row["status"] == "completed"]
                elapsed = [row["load_and_observer_ns"] for row in completed]
                median = statistics.median(elapsed) if elapsed else None
                baseline = statistics.median(base) if base else None
                rows[mode] = {
                    "planned_samples": REPEATS
                    * (1 if temperature == "first_use_after_import" else SAMPLES - 1),
                    "status_counts": dict(Counter(row["status"] for row in values)),
                    "median_ns": median,
                    "range_ns": [min(elapsed), max(elapsed)] if elapsed else None,
                    "delta_from_disabled_ns": median - baseline
                    if median is not None and baseline is not None
                    else None,
                    "ratio_to_disabled": median / baseline
                    if median is not None and baseline
                    else None,
                    "median_phases_ns": {
                        name: statistics.median(row["phases"][name] for row in completed)
                        for name in completed[0]["phases"]
                    }
                    if completed
                    else {},
                }
            groups[key] = rows
    return groups


def run_native_cost(root: Path) -> dict[str, Any]:
    """24 fresh workers × three sequential native loads; no silent retries."""
    samples: list[dict[str, Any]] = []
    workers: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="model-load-cost-") as temporary:
        directory = Path(temporary)
        prepared = time.perf_counter_ns()
        manifest = _prepare(directory)
        preparation_ns = time.perf_counter_ns() - prepared
        for repeat in range(REPEATS):
            for backend in BACKENDS:
                order = MODES[repeat:] + MODES[:repeat]
                for mode in order:
                    task = directory / f"worker-{repeat}-{backend}-{mode}"
                    task.mkdir()
                    for filename in ("artifacts.json", f"{backend}-A.bin"):
                        publish_immutable_file(task / filename, (directory / filename).read_bytes())
                    env = {
                        key: value
                        for key, value in os.environ.items()
                        if key in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}
                    }
                    env["PYTHONPATH"] = str(root / "src")
                    command = [
                        sys.executable,
                        str(root / "scripts/model_load_runtime_development.py"),
                        "worker",
                        "--root",
                        str(root),
                        "--directory",
                        str(task),
                        "--backend",
                        backend,
                        "--mode",
                        mode,
                    ]
                    try:
                        process = subprocess.run(
                            command,
                            capture_output=True,
                            text=True,
                            env=env,
                            timeout=90,
                            check=False,
                        )
                        result = json.loads(process.stdout) if process.returncode == 0 else None
                    except (subprocess.TimeoutExpired, json.JSONDecodeError):
                        result = None
                    if result is None:
                        workers.append(
                            {
                                "repeat": repeat,
                                "backend": backend,
                                "mode": mode,
                                "status": "worker_failed_entry_count_unknown",
                            }
                        )
                        for index in range(SAMPLES):
                            samples.append(
                                {
                                    "repeat": repeat,
                                    "backend": backend,
                                    "mode": mode,
                                    "sample": index,
                                    "temperature": "first_use_after_import"
                                    if index == 0
                                    else "warm_fresh_object",
                                    "status": "worker_failed_unknown",
                                }
                            )
                    else:
                        workers.append(
                            {key: value for key, value in result.items() if key != "samples"}
                            | {"repeat": repeat, "status": "completed"}
                        )
                        samples.extend(
                            {"repeat": repeat, "backend": backend, "mode": mode, **row}
                            for row in result["samples"]
                        )
                    print(
                        json.dumps(
                            {
                                "status": "runtime_cost_progress",
                                "completed_workers": len(workers),
                                "planned_workers": REPEATS * len(BACKENDS) * len(MODES),
                            }
                        ),
                        flush=True,
                    )
    return {
        "planned_workers": 24,
        "planned_native_loads": 72,
        "preparation_fits": 2,
        "preparation_ns": preparation_ns,
        "artifacts": manifest,
        "workers": workers,
        "samples": samples,
        "summary": summarize(samples),
        "known_native_entries": sum(row.get("native_entries", 0) for row in samples),
        "known_native_completed": sum(row.get("native_completed", 0) for row in samples),
        "status_counts": dict(Counter(row["status"] for row in samples)),
        "all_native_entry_counts_known": all(
            row["status"] != "worker_failed_unknown" for row in samples
        ),
        "timing_interpretation": "local descriptive; adapter-work-disabled baseline; first use after SDK import is not OS-cold; warm creates fresh objects; setup/measurement sampling excluded; nested phases must not be summed; tiny locally constructed models; no throughput/RSS/production claim",
    }


def code_bindings(root: Path) -> dict[str, str]:
    paths = (
        "scripts/model_load_runtime_development.py",
        "src/aletheia_lab/evaluation/model_load_runtime_cost.py",
        "src/aletheia_lab/evaluation/model_load_attempt_retention.py",
        "src/aletheia_lab/evaluation/model_load_retention_concurrency.py",
        "src/aletheia_lab/evaluation/model_load_validation_adapters.py",
        "src/aletheia_lab/evaluation/model_load_contract.py",
        "src/aletheia_lab/evaluation/model_load_evidence_analysis.py",
        "src/aletheia_lab/evaluation/model_load_validation_lifecycle.py",
        "src/aletheia_lab/evaluation/model_load_retention.py",
        "src/aletheia_lab/evaluation/model_load_provenance.py",
        "src/aletheia_lab/project/identity.py",
        "src/aletheia_lab/filesystem.py",
    )
    return {path: _digest((root / path).read_bytes()) for path in paths}


def verify_report(root: Path, path: Path) -> dict[str, Any]:
    from aletheia_lab.evaluation.model_load_retention_concurrency import verify_concurrency

    report: dict[str, Any] = json.loads(path.read_text())
    claimed = report.pop("report_sha256")
    if claimed != document_digest(report) or report["code_bindings"] != code_bindings(root):
        raise ValueError("report/source binding differs")
    native = report["native_cost"]
    if (
        report["schema_version"] != "model-load-runtime-development/v1"
        or report["protected_validation_executed"] is not False
    ):
        raise ValueError("development envelope changed")
    if report["status"] not in {
        "development_complete",
        "development_incomplete_failures_preserved",
        "source_changed_partial_evidence",
    }:
        raise ValueError("unknown development disposition")
    if native["summary"] != summarize(native["samples"]) or len(native["samples"]) != 72:
        raise ValueError("native cost summary/census differs")
    if len(native["workers"]) != 24 or report["provider_calls"] != 0:
        raise ValueError("worker census/destination differs")
    _verify_native_census(native)
    verify_concurrency(report["concurrency"])
    if report["status"] == "development_complete" and (
        native["status_counts"] != {"completed": 72}
        or report["concurrency"]["status"] != "concurrency_development_complete"
    ):
        raise ValueError("incomplete work mislabeled complete")
    report["report_sha256"] = claimed
    return report


def _verify_native_census(native: dict[str, Any]) -> None:
    if (native["planned_workers"], native["planned_native_loads"], native["preparation_fits"]) != (
        24,
        72,
        2,
    ):
        raise ValueError("fixed native allowance changed")
    actual = [
        (row["backend"], row["mode"], row["repeat"], row["sample"]) for row in native["samples"]
    ]
    if sorted(actual) != sorted(product(BACKENDS, MODES, range(REPEATS), range(SAMPLES))):
        raise ValueError("duplicate or missing native sample")
    counts = dict(Counter(row["status"] for row in native["samples"]))
    if native["status_counts"] != counts or native["known_native_entries"] != sum(
        row.get("native_entries", 0) for row in native["samples"]
    ):
        raise ValueError("native terminal counters differ")
    if native["known_native_completed"] != sum(
        row.get("native_completed", 0) for row in native["samples"]
    ):
        raise ValueError("native completion counters differ")
    known = all(row["status"] != "worker_failed_unknown" for row in native["samples"])
    if native["all_native_entry_counts_known"] is not known:
        raise ValueError("unknown worker entries hidden")
    _verify_workers(native)
    for row in native["samples"]:
        if row["temperature"] != (
            "first_use_after_import" if row["sample"] == 0 else "warm_fresh_object"
        ):
            raise ValueError("native temperature changed")
        if row["status"] == "completed" and (row["native_entries"], row["native_completed"]) != (
            1,
            1,
        ):
            raise ValueError("successful sample lacks one completed native entry")
        _verify_sample(row, native["artifacts"])


def _verify_workers(native: dict[str, Any]) -> None:
    actual = [(row["backend"], row["mode"], row["repeat"]) for row in native["workers"]]
    if sorted(actual) != sorted(product(BACKENDS, MODES, range(REPEATS))):
        raise ValueError("duplicate or missing worker")
    for worker_row in native["workers"]:
        identity = (worker_row["backend"], worker_row["mode"], worker_row["repeat"])
        rows = [
            row
            for row in native["samples"]
            if (row["backend"], row["mode"], row["repeat"]) == identity
        ]
        statuses = {row["status"] for row in rows}
        expected = (
            {"worker_failed_unknown"}
            if worker_row["status"] == "worker_failed_entry_count_unknown"
            else {"completed", "failed"}
        )
        if (
            worker_row["status"] not in {"completed", "worker_failed_entry_count_unknown"}
            or not statuses <= expected
        ):
            raise ValueError("worker/sample failure census differs")


def _verify_sample(row: dict[str, Any], artifacts: dict[str, Any]) -> None:
    if row["status"] not in {"completed", "failed", "worker_failed_unknown"}:
        raise ValueError("unknown sample terminal status")
    if row["status"] == "worker_failed_unknown":
        if any(key in row for key in ("native_entries", "native_completed", "phases")):
            raise ValueError("unknown worker cannot supply measured entry counters")
        return
    if set(row["phases"]) != PHASE_KEYS:
        raise ValueError("measured endpoint census differs")
    counts = (row["native_completed"], row["native_entries"], row["offered_boundaries"])
    if (
        not all(type(value) is int for value in counts)
        or not 0 <= counts[0] <= counts[1] <= counts[2] <= 1
    ):
        raise ValueError("sample entry counters outside allowance")
    values = (*row["phases"].values(), row["load_and_observer_ns"], row["read_to_decision_ns"])
    if not all(type(value) is int and value >= 0 for value in values):
        raise ValueError("invalid measured duration")
    if row["read_to_decision_ns"] != row["load_and_observer_ns"] + row["phases"]["read_ns"]:
        raise ValueError("end-to-end timing decomposition differs")
    expected = artifacts[row["backend"]]["A"]
    if row["status"] == "completed" and (row["artifact_sha256"], row["artifact_bytes"]) != (
        expected["sha256"],
        expected["bytes"],
    ):
        raise ValueError("successful sample changed its offered artifact")
