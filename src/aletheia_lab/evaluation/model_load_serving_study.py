"""Opt-in fresh serving cost study; immutable private plan/results and replay.

No provider, existing study rerun, public raw evidence, or manuscript output.
All worker failures and timeouts retain their original planned denominator.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from aletheia_lab.evaluation.model_load_application_analysis import environment
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.model_load_serving_analysis import analyze, validate_worker
from aletheia_lab.evaluation.model_load_serving_workload import prepare_models
from aletheia_lab.project.identity import content_sha256

PROTOCOL = "configs/evaluation/model_load_serving_protocol.json"
CODE = (
    "src/aletheia_lab/evaluation/model_load_serving_study.py",
    "src/aletheia_lab/evaluation/model_load_serving_analysis.py",
    "src/aletheia_lab/evaluation/model_load_serving_workload.py",
    "src/aletheia_lab/evaluation/model_load_serving_capture.py",
    "src/aletheia_lab/evaluation/model_load_serving_store.py",
    "src/aletheia_lab/evaluation/model_load_application.py",
    "src/aletheia_lab/evaluation/model_load_application_capture.py",
    "src/aletheia_lab/evaluation/model_load_contract.py",
    "src/aletheia_lab/evaluation/model_load_retention.py",
    "scripts/model_load_serving_study.py",
)
WORKER = (
    "import json,sys;from pathlib import Path;"
    "from aletheia_lab.evaluation.model_load_serving_workload import worker;"
    "print(json.dumps(worker(json.loads(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3]),"
    "json.loads(Path(sys.argv[4]).read_text(encoding='utf-8'))),allow_nan=False))"
)


def digest(value: dict[str, Any]) -> str:
    return content_sha256(encode(value).encode())


def write_new(path: Path, value: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(encode(value) + "\n")


def configurations() -> list[dict[str, Any]]:
    """Block-rotated fixed orders; never choose order by measured outcomes."""
    options = [
        ("native", 0),
        ("hash", 0),
        ("static", 0),
        ("full", 0),
        ("static", 2),
        ("full", 2),
        ("static", 8),
        ("full", 8),
    ]
    configs = []
    for repeat in range(3):
        for depth_index, depth in enumerate((2, 6, 10)):
            for ratio_index, ratio in enumerate((0, 16)):
                shift = (repeat * 3 + depth_index + ratio_index) % len(options)
                for arm, horizon in options[shift:] + options[:shift]:
                    configs.append(
                        {
                            "repeat": repeat,
                            "depth": depth,
                            "inferences": ratio,
                            "arm": arm,
                            "horizon": horizon,
                        }
                    )
    return configs


def bindings(root: Path) -> dict[str, str]:
    return {name: content_sha256((root / name).read_bytes()) for name in (*CODE, PROTOCOL)}


def protocol(root: Path) -> dict[str, Any]:
    value = json.loads((root / PROTOCOL).read_text(encoding="utf-8"))
    required = {
        "schema_version": "model-load-serving-protocol/v1",
        "model_depths": [2, 6, 10],
        "inferences_per_slot": [0, 16],
        "repetitions": 3,
        "load_slots": 12,
        "audit_ages": [0, 2, 8],
        "retention_horizons": [0, 2, 8],
        "model_seed": 106,
        "training_rows": 8192,
        "features": 8,
        "probe_rows": 12,
        "maximum_artifact_bytes": 262144,
        "failed_reload_slots": [3, 7, 11],
        "wrong_artifact_slots": [2, 6, 10],
        "sqlite_journal": "WAL",
        "sqlite_synchronous": "FULL",
        "worker_timeout_seconds": 90,
        "http_timeout_seconds": 10,
        "arms": ["native", "hash", "static", "full"],
        "provider_calls": 0,
        "development_only": True,
        "concurrent_native_capture": False,
    }
    if any(value.get(key) != expected for key, expected in required.items()):
        raise ValueError("fixed driver and serving protocol disagree")
    return dict(value)


def preflight(root: Path) -> dict[str, Any]:
    design = protocol(root)
    return {
        "status": "serving_workload_offline_ready",
        "environment": environment(),
        "protocol_sha256": digest(design),
        "worker_count": 144,
        "planned_load_operations": 1728,
        "planned_successful_reconstructions": 1296,
        "planned_failed_reload_operations": 432,
        "planned_inferences": 13824,
        "planned_audits": 5616,
        "provider_calls": 0,
        "native_calls_executed": 0,
        "execution_authorized_by_preflight": False,
    }


def child_environment(root: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(token in key.upper() for token in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))
    }
    env.update(
        PYTHONPATH=str(root / "src"),
        PYTHONHASHSEED="1",
        OMP_NUM_THREADS="1",
        OPENBLAS_NUM_THREADS="1",
        MKL_NUM_THREADS="1",
        OTEL_SDK_DISABLED="true",
    )
    return env


def execute_worker(root: Path, study: Path, index: int, config: dict[str, Any]) -> dict[str, Any]:
    command = [
        sys.executable,
        "-c",
        WORKER,
        encode(config),
        str(study / f"worker-{index:03d}"),
        str(study / "artifacts"),
        str(study / "models.json"),
    ]
    started = perf_counter_ns()
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=child_environment(root),
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "config": config,
            "status": "timeout",
            "error_type": "TimeoutExpired",
            "process_wall_ns": perf_counter_ns() - started,
        }
    if completed.returncode != 0:
        return {
            "config": config,
            "status": "worker_failure",
            "error_type": "NonzeroWorkerExit",
            "process_wall_ns": perf_counter_ns() - started,
        }
    try:
        result = json.loads(completed.stdout.splitlines()[-1])
        if not isinstance(result, dict) or result.get("config") != config:
            raise ValueError("invalid worker object/config")
    except (ValueError, IndexError):
        return {
            "config": config,
            "status": "invalid_worker_result",
            "error_type": "InvalidJSON",
            "process_wall_ns": perf_counter_ns() - started,
        }
    result.update(
        status=result.get("status", "complete"), process_wall_ns=perf_counter_ns() - started
    )
    return dict(result)


def run(root: Path, study: Path) -> dict[str, Any]:
    """Owner-authorized fresh local experiment, not a one-attempt protected test."""
    readiness = preflight(root)
    if study.exists() or study.is_symlink() or study.resolve().is_relative_to(root.resolve()):
        raise ValueError("fresh private experiment directory must be outside public repository")
    study.mkdir(parents=True, exist_ok=False)
    plan = {
        "schema_version": "model-load-serving-plan/v1",
        "protocol": protocol(root),
        "environment": readiness["environment"],
        "code_sha256": bindings(root),
        "configs": configurations(),
        "timing_outcomes_observed": False,
    }
    plan["plan_sha256"] = digest(plan)
    write_new(study / "plan.json", plan)
    models = prepare_models(study / "artifacts")
    write_new(study / "models.json", models)
    workers = []
    for index, config in enumerate(plan["configs"]):
        result = execute_worker(root, study, index, config)
        if result["status"] == "complete":
            try:
                validate_worker(result, models)
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                result.update(status="semantic_worker_failure", error_type=type(exc).__name__)
        workers.append(result)
        write_new(study / f"result-{index:03d}.json", result)
        print(
            json.dumps(
                {
                    "status": "serving_workload_progress",
                    "completed_processes": index + 1,
                    "maximum_processes": 144,
                    "worker_status": result["status"],
                }
            ),
            flush=True,
        )
    if bindings(root) != plan["code_sha256"]:
        raise ValueError("study implementation changed during measurement")
    results = {
        "schema_version": "model-load-serving-results/v1",
        "plan_sha256": plan["plan_sha256"],
        "models_sha256": digest(models),
        "workers": workers,
        "analysis": analyze(workers, models),
    }
    results["results_sha256"] = digest(results)
    write_new(study / "results.json", results)
    return verify(root, study)


def verify(root: Path, study: Path) -> dict[str, Any]:
    """No fit, native load, worker, socket or provider during independent replay."""
    plan = json.loads((study / "plan.json").read_text(encoding="utf-8"))
    plan_hash = plan.pop("plan_sha256")
    if digest(plan) != plan_hash or bindings(root) != plan["code_sha256"]:
        raise ValueError("plan or implementation binding changed")
    if plan["configs"] != configurations() or plan["protocol"] != protocol(root):
        raise ValueError("design census changed")
    models = json.loads((study / "models.json").read_text(encoding="utf-8"))
    verify_models(study, models)
    result = json.loads((study / "results.json").read_text(encoding="utf-8"))
    result_hash = result.pop("results_sha256")
    if (
        digest(result) != result_hash
        or result["plan_sha256"] != plan_hash
        or result["models_sha256"] != digest(models)
    ):
        raise ValueError("results binding changed")
    workers = result["workers"]
    if [w["config"] for w in workers] != plan["configs"] or len(workers) != 144:
        raise ValueError("worker failures omitted from original census")
    for index, worker_result in enumerate(workers):
        if (
            json.loads((study / f"result-{index:03d}.json").read_text(encoding="utf-8"))
            != worker_result
        ):
            raise ValueError("worker source result differs from aggregate")
    analysis = analyze(workers, models)
    if result["analysis"] != analysis:
        raise ValueError("independent analysis replay differs")
    return {
        "status": "serving_cost_coverage_audit_verified",
        "verification": "pass",
        "plan_sha256": plan_hash,
        "results_sha256": result_hash,
        "worker_counts": analysis["worker_counts"],
        "frontier": analysis["frontier"],
        "provider_calls": 0,
        "native_calls_during_verify": 0,
        "disposition": analysis["disposition"],
    }


def verify_models(study: Path, models: dict[str, Any]) -> None:
    for band in models["bands"].values():
        for model in band.values():
            payload = (study / "artifacts" / model["path"]).read_bytes()
            if len(payload) != model["bytes"] or content_sha256(payload) != model["digest"]:
                raise ValueError("owned source artifact bytes changed")
