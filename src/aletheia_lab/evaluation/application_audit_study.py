"""Bounded opt-in local development execution and read-only aggregate verification."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

from aletheia_lab.evaluation.application_audit_analysis import summarize
from aletheia_lab.evaluation.application_audit_sources import PACKAGES
from aletheia_lab.evaluation.application_audit_workload import BUDGETS, REPEATS, STEPS, worker
from aletheia_lab.evaluation.audit_obligation_service import (
    FRAME_BOUND,
    POLICIES,
    RESERVATION_BYTES,
)
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

CODE = tuple(
    "src/aletheia_lab/evaluation/" + name + ".py"
    for name in (
        "application_audit_sources",
        "application_audit_workload",
        "application_audit_analysis",
        "application_audit_study",
        "audit_obligation_service",
    )
) + ("scripts/application_audit_development.py",)


def execute_worker(stack: str, directory: Path) -> None:
    """Own one serial process. Bootstrap asyncio before blocking application sockets."""
    for key in tuple(os.environ):
        if key.startswith(("MLSERVER_", "OTEL_", "PROMETHEUS_", "BENTOCLOUD_", "MLFLOW_")):
            os.environ.pop(key)
    os.environ.update(
        OTEL_SDK_DISABLED="true",
        BENTOML_DO_NOT_TRACK="true",
        MLFLOW_ALLOW_PICKLE_DESERIALIZATION="true",
    )
    logging.disable(logging.CRITICAL)

    def deny(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("application development cannot use network sockets")

    runner = asyncio.Runner()
    runner.get_loop()
    try:
        with (
            patch.object(socket.socket, "connect", deny),
            patch.object(socket.socket, "connect_ex", deny),
            patch.object(socket, "create_connection", deny),
        ):
            try:
                result = runner.run(worker(stack, directory))
            finally:
                runner.close()
    finally:
        runner.close()
    write_new_file(directory / "result.json", encode(result).encode())


def design(root: Path) -> dict[str, Any]:
    """Selection uses native lifecycle/source criteria, not observed positive gaps."""
    return {
        "schema": "application-audit-development/v1",
        "packages": PACKAGES,
        "source_criteria": [
            "externally_authored_native_ASGI",
            "native_selection_and_cached_object",
            "local_owned_artifacts",
            "observable_startup_use_failure",
        ],
        "steps": list(STEPS),
        "policies": list(POLICIES),
        "budgets": list(BUDGETS),
        "repeats": REPEATS,
        "frame_bound": FRAME_BOUND,
        "reservation_bytes": RESERVATION_BYTES,
        "logical_lease_events": 4,
        "serial_only": True,
        "provider_calls": 0,
        "protected_historical_execution": False,
        "code_sha256": {path: content_sha256((root / path).read_bytes()) for path in CODE},
    }


def child_environment(root: Path, dependencies: list[Path]) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not any(word in key.lower() for word in ("secret", "token", "key", "password"))
        and not key.startswith(("MLFLOW_", "BENTOCLOUD_", "OTEL_"))
    }
    environment.update(
        PYTHONPATH=os.pathsep.join(str(path) for path in (root / "src", *dependencies)),
        PYTHONHASHSEED="1",
        OTEL_SDK_DISABLED="true",
        BENTOML_DO_NOT_TRACK="true",
        OPENBLAS_NUM_THREADS="1",
        OMP_NUM_THREADS="1",
        MKL_NUM_THREADS="1",
    )
    return environment


def run(
    root: Path, directory: Path, bento: Path, mlflow: Path, asgi: Path, python: Path
) -> dict[str, Any]:
    if directory.exists() or directory.is_symlink():
        raise ValueError(
            "new private study directory required; existing outcomes are never replaced"
        )
    if (
        any(not path.is_dir() or path.is_symlink() for path in (bento, mlflow, asgi))
        or not python.is_file()
    ):
        raise ValueError("explicit installed isolated runtimes required")
    directory.mkdir(parents=True)
    plan = design(root)
    write_new_file(directory / "plan.json", encode(plan).encode())
    configurations = (
        ("bentoml", Path(sys.executable), [bento, mlflow]),
        ("mlflow", python, [asgi, mlflow]),
    )
    for stack, executable, dependencies in configurations:
        command = [
            str(executable),
            str(root / "scripts/application_audit_development.py"),
            "worker",
            "--stack",
            stack,
            "--study-dir",
            str(directory / stack),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                env=child_environment(root, dependencies),
                capture_output=True,
                timeout=900,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            write_new_file(
                directory / f"{stack}-failure.json",
                encode({"status": "worker_timeout", "timeout_seconds": exc.timeout}).encode(),
            )
            raise RuntimeError("planned application worker timed out; failure retained") from exc
        write_new_file(directory / f"{stack}-stdout.log", completed.stdout)
        write_new_file(directory / f"{stack}-stderr.log", completed.stderr)
        if completed.returncode != 0:
            raise RuntimeError("planned application worker failed; complete log retained privately")
    data = [json.loads((directory / stack / "result.json").read_bytes()) for stack in PACKAGES]
    result = {
        "schema": plan["schema"],
        "plan_sha256": content_sha256(encode(plan).encode()),
        "source_result_sha256": {
            stack: content_sha256((directory / stack / "result.json").read_bytes())
            for stack in PACKAGES
        },
        "analysis": summarize(data),
    }
    result["results_sha256"] = content_sha256(encode(result).encode())
    write_new_file(directory / "results.json", encode(result).encode())
    return verify(root, directory)


def verify(root: Path, directory: Path) -> dict[str, Any]:
    """No loaders, application, signatures, provider, or native work are rerun."""
    plan = json.loads((directory / "plan.json").read_bytes())
    if plan != design(root):
        raise ValueError("design or executable source bindings changed")
    result = json.loads((directory / "results.json").read_bytes())
    unsigned = {key: value for key, value in result.items() if key != "results_sha256"}
    if result["results_sha256"] != content_sha256(encode(unsigned).encode()):
        raise ValueError("result identity changed")
    data = []
    for stack in PACKAGES:
        raw = (directory / stack / "result.json").read_bytes()
        if content_sha256(raw) != result["source_result_sha256"][stack]:
            raise ValueError("native source result changed")
        data.append(json.loads(raw))
    if result["plan_sha256"] != content_sha256(encode(plan).encode()) or result[
        "analysis"
    ] != summarize(data):
        raise ValueError("aggregate differs from independent raw evidence replay")
    return {
        "status": "application_audit_development_verified",
        "verification": "pass",
        "plan_sha256": result["plan_sha256"],
        "results_sha256": result["results_sha256"],
        "analysis": result["analysis"],
        "provider_calls": 0,
        "protected_runs": 0,
    }
