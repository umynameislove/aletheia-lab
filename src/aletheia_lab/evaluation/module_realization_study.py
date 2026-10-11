"""Bound additive incident-mechanism study, raw replay and complete planned census."""

from __future__ import annotations

import json
import random
import subprocess
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.cache_lifecycle_study import environment, read_sealed, seal
from aletheia_lab.evaluation.litserve_evidence_provenance import sign_bundle
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.module_realization_analysis import aggregate, analyze, payload_digest
from aletheia_lab.evaluation.module_realization_runtime import drive
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

PROTOCOL = "configs/evaluation/module_realization_protocol.json"
FILES = (
    PROTOCOL,
    "scripts/module_realization_validation.py",
    *(
        f"src/aletheia_lab/evaluation/module_realization_{name}.py"
        for name in ("source", "runtime", "store", "analysis", "study")
    ),
    "src/aletheia_lab/evaluation/cache_lifecycle_study.py",
    "src/aletheia_lab/evaluation/cache_lifecycle_materialization.py",
    "src/aletheia_lab/evaluation/litserve_evidence_provenance.py",
    "src/aletheia_lab/evaluation/model_load_provenance.py",
    "src/aletheia_lab/evaluation/model_load_retention.py",
    "src/aletheia_lab/filesystem.py",
    "src/aletheia_lab/project/identity.py",
)


def design(root: Path) -> dict[str, Any]:
    protocol = json.loads((root / PROTOCOL).read_bytes())
    cells = []
    for replicate in range(protocol["process_replicates"]):
        for arm in protocol["arms"]:
            for order in protocol["orders"]:
                for evidence in protocol["evidence_modes"]:
                    cells.append(
                        {
                            "slice": "repair_cost",
                            **arm,
                            "order": order,
                            "evidence": evidence,
                            "replicate": replicate,
                            "fault": "none",
                            "durability": "event",
                            "crash": "none",
                        }
                    )
    for evidence in ("compact", "full"):
        for fault in protocol["transport_faults"]:
            cells.append(
                {
                    "slice": "transport",
                    "variant": "collision",
                    "repair": "none",
                    "order": ["A", "B", "A"],
                    "evidence": evidence,
                    "replicate": 0,
                    "fault": fault,
                    "durability": "event",
                    "crash": "none",
                }
            )
        for durability in protocol["durability_modes"]:
            for crash in protocol["crash_frontiers"]:
                cells.append(
                    {
                        "slice": "crash",
                        "variant": "collision",
                        "repair": "none",
                        "order": ["A", "B", "A"],
                        "evidence": evidence,
                        "replicate": 0,
                        "fault": "none",
                        "durability": durability,
                        "crash": crash,
                    }
                )
    for cell in cells:
        cell["requests_per_stage"] = protocol["requests_per_stage"]
    random.Random(protocol["ordering_seed"]).shuffle(cells)
    return seal(
        {
            "schema": "module-realization-plan/v1",
            "protocol": protocol,
            "cells": cells,
            "bindings": {name: content_sha256((root / name).read_bytes()) for name in FILES},
        }
    )


def check(root: Path, plan: dict[str, Any]) -> None:
    if design(root) != plan:
        raise ValueError("executed source/design binding differs")


def _environment(root: Path, site: Path) -> dict[str, str]:
    return {
        **environment(root, site),
        "MLFLOW_DISABLE_TELEMETRY": "true",
        "MLFLOW_ENABLE_ASYNC_LOGGING": "false",
    }


def prepare(root: Path, directory: Path) -> dict[str, Any]:
    if directory.exists() or directory.is_symlink() or directory.is_relative_to(root):
        raise ValueError("fresh study outside public repository required")
    plan = design(root)
    directory.mkdir(parents=True)
    for name in FILES:
        write_new_file(directory / "code-snapshot" / name, (root / name).read_bytes())
    write_new_file(directory / "plan.json", encode(plan).encode())
    return {
        "status": "module_realization_plan_sealed",
        "plan_sha256": plan["sha256"],
        "cells": len(plan["cells"]),
        "provider_calls": 0,
    }


def _build(root: Path, directory: Path, executable: Path, site: Path) -> None:
    command = [
        str(executable),
        str(root / "scripts/module_realization_validation.py"),
        "build",
        "--study-dir",
        str(directory / "artifacts"),
    ]
    env = {
        **_environment(root, site),
        "MLFLOW_TRACKING_URI": f"sqlite:///{directory / 'owned-tracking.sqlite'}",
    }
    completed = subprocess.run(command, env=env, capture_output=True, timeout=90)
    write_new_file(directory / "build-stdout.log", completed.stdout)
    write_new_file(directory / "build-stderr.log", completed.stderr)
    if completed.returncode:
        raise ValueError("owned artifact construction failed; preserve attempt")


def _finding(directory: Path, execution: dict[str, Any], *, signing: bool) -> dict[str, Any]:
    if execution["failure"] is not None:
        return {"verification": "fail", "error_type": execution["failure"]}
    try:
        digest, sizes = payload_digest(directory)
        if sizes and signing:
            sign_bundle(digest, directory / "provenance")
        return analyze(directory, execution)
    except Exception as exc:
        return {"verification": "fail", "error_type": type(exc).__name__}


def run(root: Path, directory: Path, executable: Path, site: Path) -> dict[str, Any]:
    plan = read_sealed(directory / "plan.json")
    check(root, plan)
    write_new_file(
        directory / "execution-started.json", encode({"plan_sha256": plan["sha256"]}).encode()
    )
    _build(root, directory, executable, site)
    executions, findings = [], []
    for index, config in enumerate(plan["cells"]):
        path = directory / f"config-{index:03}.json"
        write_new_file(path, encode(config).encode())
        target = directory / f"cell-{index:03}"
        command = [
            str(executable),
            str(root / "scripts/module_realization_validation.py"),
            "worker",
            "--study-dir",
            str(target),
            "--artifacts",
            str(directory / "artifacts"),
            "--config",
            str(path),
        ]
        env = {
            **_environment(root, site),
            "MLFLOW_TRACKING_URI": f"sqlite:///{directory / 'owned-tracking.sqlite'}",
        }
        execution = drive(command, env, target, config)
        executions.append(execution)
        findings.append(_finding(target, execution, signing=True))
        print(
            json.dumps(
                {
                    "status": "module_realization_progress",
                    "completed": index + 1,
                    "maximum": len(plan["cells"]),
                }
            ),
            flush=True,
        )
    check(root, plan)
    report = seal(
        {
            "schema": "module-realization-results/v1",
            "plan_sha256": plan["sha256"],
            "executions": executions,
            "findings": findings,
            "analysis": aggregate(executions, findings),
        }
    )
    write_new_file(directory / "results.json", encode(report).encode())
    return {
        "status": "module_realization_complete",
        "results_sha256": report["sha256"],
        "analysis": report["analysis"],
    }


def verify(root: Path, directory: Path) -> dict[str, Any]:
    plan, report = read_sealed(directory / "plan.json"), read_sealed(directory / "results.json")
    check(root, plan)
    if (
        report["plan_sha256"] != plan["sha256"]
        or [row["config"] for row in report["executions"]] != plan["cells"]
    ):
        raise ValueError("executed plan census differs")
    findings = [
        _finding(directory / f"cell-{i:03}", row, signing=False)
        for i, row in enumerate(report["executions"])
    ]
    # Query timing is freshly measured, never used as a reproducibility verdict.
    for new, old in zip(findings, report["findings"], strict=True):
        new.pop("query_ns", None)
        old.pop("query_ns", None)
    if (
        findings != report["findings"]
        or aggregate(report["executions"], findings) != report["analysis"]
    ):
        raise ValueError("independent raw replay differs")
    return {
        "verification": "pass",
        "results_sha256": report["sha256"],
        "analysis": report["analysis"],
    }


def closeout(root: Path, directory: Path, *, save: bool = True) -> dict[str, Any]:
    """Analysis-only empty-WAL correction; never rewrite native outcomes."""
    plan = read_sealed(directory / "plan.json")
    original = read_sealed(directory / "results.json")
    check(directory / "code-snapshot", plan)
    # Executed runtime/source/store are checked in the immutable snapshot above.
    # Current runtime may add forward controls; it is not used to rerun this trace.
    if (
        original["plan_sha256"] != plan["sha256"]
        or [row["config"] for row in original["executions"]] != plan["cells"]
    ):
        raise ValueError("original plan/execution census differs")
    findings = [
        _finding(directory / f"cell-{i:03}", row, signing=False)
        for i, row in enumerate(original["executions"])
    ]
    raw_bindings = {
        path.relative_to(directory).as_posix(): content_sha256(path.read_bytes())
        for path in sorted(directory.glob("cell-*/reference.jsonl"))
    }
    raw_bindings.update(
        {
            path.relative_to(directory).as_posix(): content_sha256(path.read_bytes())
            for path in sorted(directory.glob("cell-*-parent/client.jsonl"))
        }
    )
    report = seal(
        {
            "schema": "module-realization-analysis-closeout/v1",
            "original_results_sha256": original["sha256"],
            "plan_sha256": plan["sha256"],
            "raw_bindings": raw_bindings,
            "findings": findings,
            "analysis": aggregate(original["executions"], findings),
            "analysis_bindings": {
                name: content_sha256((root / name).read_bytes())
                for name in FILES
                if "analysis" in name or "study" in name
            },
            "correction": "Ignore observer-created empty WAL in signed state; no native rerun or changed evidence",
        }
    )
    path = directory / "analysis-closeout-v2.json"
    if save:
        write_new_file(path, encode(report).encode())
    else:
        old = read_sealed(path)
        for new, previous in zip(report["findings"], old["findings"], strict=True):
            new.pop("query_ns", None)
            previous.pop("query_ns", None)
        report.pop("sha256")
        old.pop("sha256")
        if report != old:
            raise ValueError("closeout raw replay differs")
    return {"verification": "pass", "analysis": report["analysis"]}
