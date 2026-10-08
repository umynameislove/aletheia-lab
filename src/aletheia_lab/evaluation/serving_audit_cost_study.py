"""Fixed fresh-process cost census; no optional repeats or hidden failed cells."""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.official_model_signing import artifact_closure, verify_local
from aletheia_lab.evaluation.serving_audit_cost_workload import ARMS, INPUTS, encoded
from aletheia_lab.evaluation.serving_large_model_review import verify_large_development
from aletheia_lab.filesystem import fsync_directory_tree, write_new_file
from aletheia_lab.project.identity import content_sha256

QUALIFIED_REPORT = "41511135a63de8bc5375e348872d28dea930d7e580faea76a317316a5b9a991a"
RUNTIME = {"numpy": "2.3.5", "onnxruntime": "1.23.2", "model-signing": "1.1.1"}
CODE = (
    "scripts/serving_audit_cost.py",
    "src/aletheia_lab/evaluation/serving_audit_cost_study.py",
    "src/aletheia_lab/evaluation/serving_audit_cost_workload.py",
    "src/aletheia_lab/evaluation/serving_audit_cost_store.py",
    "src/aletheia_lab/evaluation/serving_audit_cost_analysis.py",
    "src/aletheia_lab/evaluation/official_model_signing.py",
    "src/aletheia_lab/evaluation/serving_large_model_review.py",
    "src/aletheia_lab/evaluation/model_load_retention.py",
    "src/aletheia_lab/evaluation/request_model_audit.py",
    "src/aletheia_lab/content_hashing.py",
    "src/aletheia_lab/filesystem.py",
    "src/aletheia_lab/project/identity.py",
)


def configurations() -> list[dict[str, Any]]:
    rows = []
    for pattern in ("reuse", "reload"):
        for block in range(5):
            for arm in (*ARMS[block:], *ARMS[:block]):
                rows.append(
                    {
                        "worker_id": f"{pattern}-{block}-{arm}",
                        "pattern": pattern,
                        "block": block,
                        "arm": arm,
                    }
                )
    return rows


def _regular(path: Path) -> None:
    if not path.is_file() or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("owned regular file without symbolic ancestors required")


def _runtime() -> dict[str, str]:
    observed = {name: importlib.metadata.version(name) for name in RUNTIME}
    if observed != RUNTIME:
        raise ValueError("qualified runtime differs")
    return observed


def _copy(source: Path, destination: Path) -> dict[str, Any]:
    _regular(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("snapshot target already exists")
    shutil.copyfile(source, destination)
    if file_sha256(source) != file_sha256(destination):
        raise ValueError("snapshot bytes differ")
    with destination.open("rb") as handle:
        os.fsync(handle.fileno())
    return {"bytes": destination.stat().st_size, "sha256": file_sha256(destination)}


def prepare(root: Path, qualified: Path, design: Path, directory: Path) -> dict[str, Any]:
    _runtime()
    _regular(qualified / "results.json")
    if file_sha256(qualified / "results.json") != QUALIFIED_REPORT:
        raise ValueError("large model qualification owner differs")
    original = json.loads((qualified / "results.json").read_bytes())
    verify_large_development(qualified)  # Retained numeric/input/receipt checks, not another run.
    closure = artifact_closure(qualified / "model")
    if closure != original["artifact"] or closure["total_bytes"] != 178_047_016:
        raise ValueError("qualified full closure differs")
    if (
        verify_local(
            qualified / "model", qualified / "model.sig.json", qualified / "verification-public.pem"
        )
        != closure
    ):
        raise ValueError("qualified signature scope differs")
    _regular(design)
    if directory.exists() or any(part.is_symlink() for part in (directory, *directory.parents)):
        raise FileExistsError("fresh owned study directory required")
    directory.mkdir()
    recovery = directory / "common-recovery"
    relatives = [f"model/{row['path']}" for row in closure["files"]]
    relatives += ["model.sig.json", "verification-public.pem"]
    relatives += [f"inputs/{name}.npy" for name in INPUTS]
    relatives += [f"outputs/qualification-torch-{name}.npy" for name in INPUTS]
    recovery_files = {name: _copy(qualified / name, recovery / name) for name in relatives}
    code = {name: _copy(root / name, directory / "executed-code" / name)["sha256"] for name in CODE}
    _copy(design, directory / "design.md")
    plan = {
        "schema": "serving-audit-cost/v1",
        "root": str(root.absolute()),
        "qualified_directory": str(recovery.absolute()),
        "artifact": closure,
        "qualified_report_sha256": QUALIFIED_REPORT,
        "runtime": _runtime(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "design_sha256": file_sha256(design),
        "code_sha256": code,
        "recovery_files": recovery_files,
        "common_recovery_bytes": sum(row["bytes"] for row in recovery_files.values()),
        "configs": configurations(),
        "calls_per_worker": 64,
        "worker_timeout_seconds": 180,
        "batch": 8,
        "deadline_ns": 30_000_000_000,
        "post_workload_delay_seconds": 1,
        "forecast_cost_estimand": "median paired full-minus-compact workload_through_final_ack_ns",
        "outcome_selection": "no retries, reranking or extra blocks",
    }
    plan["plan_sha256"] = content_sha256(encoded(plan))
    write_new_file(directory / "plan.json", encoded(plan))
    fsync_directory_tree(directory)
    return plan


def _check_recovery(plan: dict[str, Any]) -> None:
    for name, expected in plan["recovery_files"].items():
        artifact = Path(plan["qualified_directory"]) / name
        _regular(artifact)
        if (
            artifact.stat().st_size != expected["bytes"]
            or file_sha256(artifact) != expected["sha256"]
        ):
            raise ValueError("retained recovery bytes differ")


def check_plan(path: Path, *, recovery: bool = True) -> dict[str, Any]:
    _regular(path)
    plan: dict[str, Any] = json.loads(path.read_bytes())
    claimed = plan.pop("plan_sha256")
    if content_sha256(encoded(plan)) != claimed:
        raise ValueError("execution seal differs")
    plan["plan_sha256"] = claimed
    if plan["configs"] != configurations() or plan["calls_per_worker"] != 64:
        raise ValueError("planned worker census differs")
    if plan["runtime"] != _runtime():
        raise ValueError("runtime seal differs")
    root = Path(plan["root"])
    if set(plan["code_sha256"]) != set(CODE):
        raise ValueError("executable seal membership differs")
    relatives = {f"model/{row['path']}" for row in plan["artifact"]["files"]}
    relatives.update(("model.sig.json", "verification-public.pem"))
    relatives.update(f"inputs/{name}.npy" for name in INPUTS)
    relatives.update(f"outputs/qualification-torch-{name}.npy" for name in INPUTS)
    if set(plan["recovery_files"]) != relatives:
        raise ValueError("common recovery seal membership differs")
    for name, expected in plan["code_sha256"].items():
        _regular(root / name)
        if file_sha256(root / name) != expected:
            raise ValueError("executed code changed after seal")
    if recovery:
        _check_recovery(plan)
    return plan


def execute(path: Path) -> dict[str, Any]:
    plan = check_plan(path)
    study = path.parent
    workers = study / "workers"
    workers.mkdir(exist_ok=False)  # One fixed comparison, not a retry/resume loop.
    observations = []
    for config in plan["configs"]:
        directory = workers / config["worker_id"]
        start = perf_counter_ns()
        error, returncode = None, None
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(plan["root"]) / CODE[0]),
                    "worker",
                    "--plan",
                    str(path.absolute()),
                    "--worker-id",
                    config["worker_id"],
                    "--output",
                    str(directory.absolute()),
                ],
                cwd=plan["root"],
                env=dict(os.environ, PYTHONPATH=str(Path(plan["root"]) / "src")),
                capture_output=True,
                timeout=plan["worker_timeout_seconds"],
                check=False,
            )
            returncode = completed.returncode
            stdout, stderr = completed.stdout, completed.stderr
        except subprocess.TimeoutExpired as exc:
            error, stdout, stderr = "TimeoutExpired", exc.stdout or b"", exc.stderr or b""
        except OSError as exc:
            error, stdout, stderr = type(exc).__name__, b"", b"subprocess unavailable"
        row = {
            "config": config,
            "returncode": returncode,
            "error_type": error,
            "supervisor_elapsed_ns": perf_counter_ns() - start,
        }
        if directory.exists():
            write_new_file(directory / "stdout.txt", stdout)
            write_new_file(directory / "stderr.txt", stderr)
        else:
            directory.mkdir()
            write_new_file(directory / "stdout.txt", stdout)
            write_new_file(directory / "stderr.txt", stderr)
        report = directory / "worker.json"
        row["worker_report_sha256"] = file_sha256(report) if report.is_file() else None
        observations.append(row)
        print(
            json.dumps(
                {
                    "status": "serving_audit_cost_progress",
                    "completed_workers": len(observations),
                    "planned_workers": 50,
                    "worker_id": config["worker_id"],
                    "returncode": returncode,
                }
            ),
            flush=True,
        )
    check_plan(path)
    report = {"plan_sha256": plan["plan_sha256"], "workers": observations}
    write_new_file(study / "execution.json", encoded(report))
    return report
