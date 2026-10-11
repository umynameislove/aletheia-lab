"""Forward no-observer cost floor and actual early/late audit controls."""

from __future__ import annotations

import json
import random
import statistics
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.cache_lifecycle_study import read_sealed, seal
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.module_realization_analysis import _census, _client_intents, analyze
from aletheia_lab.evaluation.module_realization_runtime import drive
from aletheia_lab.evaluation.module_realization_store import read_records, recovered
from aletheia_lab.evaluation.module_realization_study import _environment
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

FILES = tuple(
    f"src/aletheia_lab/evaluation/module_realization_{name}.py"
    for name in ("source", "runtime", "store", "analysis", "floor", "forward")
) + ("scripts/module_realization_validation.py",)


def cells() -> list[dict[str, Any]]:
    result = [
        {
            "slice": "no_observer_floor",
            "variant": variant,
            "repair": repair,
            "order": order,
            "replicate": repeat,
            "evidence": "native",
            "fault": "none",
            "durability": "event",
            "crash": "none",
            "requests_per_stage": 12,
        }
        for repeat in range(2)
        for variant, repair in (("collision", "none"), ("collision", "evict"), ("unique", "none"))
        for order in (["A", "B", "A"], ["B", "A", "B"])
    ]
    result.extend(
        {
            "slice": "early_late_audit",
            "variant": "collision",
            "repair": "none",
            "order": ["A", "B", "A"],
            "replicate": 0,
            "evidence": evidence,
            "fault": "delay",
            "durability": "event",
            "crash": "none",
            "requests_per_stage": 12,
            "audit_queries": True,
        }
        for evidence in ("compact", "full")
    )
    return result


def amortization_cells() -> list[dict[str, Any]]:
    result = [
        {
            "slice": "binding_amortization",
            "variant": variant,
            "repair": repair,
            "order": order,
            "replicate": repeat,
            "evidence": "compact",
            "fault": "none",
            "durability": "event",
            "crash": "none",
            "requests_per_stage": 12,
            "binding_mode": mode,
        }
        for repeat in range(2)
        for variant, repair in (("collision", "none"), ("collision", "evict"), ("unique", "none"))
        for order in (["A", "B", "A"], ["B", "A", "B"])
        for mode in ("scan", "load_cache", "guarded_cache")
    ]
    random.Random(2026100702).shuffle(result)
    return result


def _expected(responses: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, str]:
    intents = _client_intents(responses)
    first = 1 if config["order"][0] == "A" else 2
    result = {}
    for row in responses:
        if row["route"] != "predict":
            continue
        identity = row["value"]["request_id"]
        intended = intents[identity]
        coefficient = (
            first if config["variant"] == "collision" and config["repair"] == "none" else intended
        )
        if row["response"]["body"]["y"] != row["value"]["x"] * coefficient:
            raise ValueError("forward numeric source prediction contradicted")
        result[identity] = "compliant" if coefficient == intended else "violation"
    return result


def _audit_queries(
    responses: list[dict[str, Any]], expected: dict[str, str], config: dict[str, Any]
) -> list[dict[str, Any]]:
    results = [row["response"]["audit"] for row in responses if row["route"] == "audit"]
    if config["slice"] == "early_late_audit" and results != [
        {"verdicts": dict.fromkeys(expected, "unknown"), "closure": False},
        {"verdicts": expected, "closure": True},
    ]:
        raise ValueError("actual early/late audit transition differs")
    return results


def _analyze(directory: Path, execution: dict[str, Any]) -> dict[str, Any]:
    client = read_records(directory.parent / f"{directory.name}-parent" / "client.jsonl")
    config = execution["config"]
    responses, _ = _census(client, config)
    predictions = [row for row in responses if row["route"] == "predict"]
    if execution["failure"] or execution["returncode"] != 0 or client[-1]["returncode"] != 0:
        raise ValueError("forward lifecycle census failed")
    statuses = [row["response"]["status"] for row in responses if row["route"] == "load"]
    if statuses != [200, 200, 200, 400] or any(
        row["response"]["status"] != 200 for row in predictions
    ):
        raise ValueError("forward native response status differs")
    native = execution["last_response"]["native"]
    if native["prediction_calls"] != 37 or native["actual_native_loads"] != 4:
        raise ValueError("forward actual call count differs")
    expected = _expected(responses, config)
    intents = _client_intents(responses)
    mismatch = sum(
        row["response"]["body"]["y"] != row["value"]["x"] * intents[row["value"]["request_id"]]
        for row in predictions
    )
    audit_results = _audit_queries(responses, expected, config)
    if config["slice"] == "no_observer_floor" and (
        (directory / "reference.jsonl").exists() or (directory / "evidence.sqlite").exists()
    ):
        raise ValueError("no-observer floor unexpectedly captured evidence")
    return {
        "verification": "pass",
        "predictions": 37,
        "numeric_changed": mismatch,
        "predict_latency_ns": [row["elapsed_ns"] for row in predictions],
        "load_latency_ns": [row["elapsed_ns"] for row in responses if row["route"] == "load"],
        "early_late_audits": audit_results,
        "native": native,
        "parent_ledger_bytes": sum(
            p.stat().st_size
            for p in directory.parent.glob(f"{directory.name}-parent/*")
            if p.is_file()
        ),
    }


def _finding(directory: Path, execution: dict[str, Any]) -> dict[str, Any]:
    try:
        result = _analyze(directory, execution)
        if execution["config"]["slice"] == "binding_amortization":
            raw = read_records(directory / "reference.jsonl")
            reference = {
                row["request_id"]: row["binding"] for row in raw if row["kind"] == "predict"
            }
            records = recovered(directory / "evidence.sqlite")
            actual = {
                row["request_id"]: row["binding"] for row in records if row["kind"] == "predict"
            }
            if actual != reference:
                raise ValueError("cached binding differs from fresh reference")
            checked = analyze(directory, execution, signature=False)
            result["audit_answers"] = checked["actual_use_answers"]
            result["audit_fulfilled"] = checked["audit_fulfilled"]
            result["audit_query_ns"] = checked["query_ns"]
            result["collector"] = checked["collector"]
            result["physical_bytes"] = checked["physical_bytes"]
            if result["audit_answers"] != {"correct": 37} or not result["audit_fulfilled"]:
                raise ValueError("amortization changed audit service")
        return result
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"verification": "fail", "error_type": type(exc).__name__}


def _summary(executions: list[dict[str, Any]], findings: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for execution, finding in zip(executions, findings, strict=True):
        config = execution["config"]
        key = f"{config['slice']}/{config['variant']}/{config['repair']}/{config['evidence']}"
        if config.get("binding_mode"):
            key += f"/{config['binding_mode']}"
        groups.setdefault(key, []).append(finding)
    return {
        key: {
            "cells": len(rows),
            "verified_cells": sum(row["verification"] == "pass" for row in rows),
            "predictions": sum(row.get("predictions", 0) for row in rows),
            "numeric_changed": sum(row.get("numeric_changed", 0) for row in rows),
            "median_predict_ms": statistics.median(
                x for row in rows for x in row["predict_latency_ns"]
            )
            / 1e6
            if all(row["verification"] == "pass" for row in rows)
            else None,
        }
        for key, rows in groups.items()
    }


def run(
    root: Path,
    directory: Path,
    artifacts: Path,
    executable: Path,
    site: Path,
    *,
    amortization: bool = False,
) -> dict[str, Any]:
    if directory.exists() or directory.is_symlink() or directory.is_relative_to(root):
        raise ValueError("fresh forward control directory required")
    matrix = amortization_cells() if amortization else cells()
    files = (
        (*FILES, "src/aletheia_lab/evaluation/module_realization_binding_cache.py")
        if amortization
        else FILES
    )
    plan = seal(
        {
            "schema": "module-realization-forward-plan/v1",
            "cells": matrix,
            "bindings": {name: content_sha256((root / name).read_bytes()) for name in files},
            "manifest_sha256": content_sha256((artifacts / "manifest.json").read_bytes()),
            "scope": "post-result forward cost/audit controls; same owned artifacts; no field/throughput or new algorithm claim",
            "premises": "binding reuse requires immutable source bytes, supported callable/global footprint and no mutation during call; before/after scans also miss ABA; common fresh-reference scans are measured separately",
        }
    )
    directory.mkdir()
    for name in files:
        write_new_file(directory / "code-snapshot" / name, (root / name).read_bytes())
    write_new_file(directory / "plan.json", encode(plan).encode())
    executions, findings = [], []
    for index, config in enumerate(plan["cells"]):
        _check_current(root, plan["bindings"])
        path = directory / f"config-{index:03}.json"
        write_new_file(path, encode(config).encode())
        target = directory / f"cell-{index:03}"
        action = "floor-worker" if config["slice"] == "no_observer_floor" else "worker"
        command = [
            str(executable),
            str(root / "scripts/module_realization_validation.py"),
            action,
            "--study-dir",
            str(target),
            "--artifacts",
            str(artifacts),
            "--config",
            str(path),
        ]
        env = {
            **_environment(root, site),
            "MLFLOW_TRACKING_URI": f"sqlite:///{directory / 'owned-tracking.sqlite'}",
        }
        execution = drive(command, env, target, config)
        executions.append(execution)
        findings.append(_finding(target, execution))
        print(
            json.dumps(
                {
                    "status": "module_realization_forward_progress",
                    "completed": index + 1,
                    "maximum": len(matrix),
                }
            ),
            flush=True,
        )
    _check_current(root, plan["bindings"])
    report = seal(
        {
            "schema": "module-realization-forward-results/v1",
            "plan_sha256": plan["sha256"],
            "executions": executions,
            "findings": findings,
            "analysis": _summary(executions, findings),
        }
    )
    write_new_file(directory / "results.json", encode(report).encode())
    return {
        "verification": "pass"
        if all(row["verification"] == "pass" for row in findings)
        else "fail",
        "analysis": report["analysis"],
        "plan_sha256": plan["sha256"],
        "results_sha256": report["sha256"],
    }


def verify(directory: Path, *, amortization: bool = False) -> dict[str, Any]:
    plan, report = read_sealed(directory / "plan.json"), read_sealed(directory / "results.json")
    if (
        plan["cells"] != (amortization_cells() if amortization else cells())
        or [row["config"] for row in report["executions"]] != plan["cells"]
        or report["plan_sha256"] != plan["sha256"]
    ):
        raise ValueError("forward fixed census differs")
    for name, digest in plan["bindings"].items():
        if content_sha256((directory / "code-snapshot" / name).read_bytes()) != digest:
            raise ValueError("forward executed snapshot differs")
    findings = [
        _finding(directory / f"cell-{index:03}", row)
        for index, row in enumerate(report["executions"])
    ]
    for new, old in zip(findings, report["findings"], strict=True):
        new.pop("audit_query_ns", None)
        old.pop("audit_query_ns", None)
    if (
        findings != report["findings"]
        or _summary(report["executions"], findings) != report["analysis"]
    ):
        raise ValueError("forward raw replay differs")
    return {"verification": "pass", "analysis": report["analysis"]}


def _check_current(root: Path, bindings: dict[str, str]) -> None:
    if any(
        content_sha256((root / name).read_bytes()) != digest for name, digest in bindings.items()
    ):
        raise ValueError("forward implementation changed during execution")
