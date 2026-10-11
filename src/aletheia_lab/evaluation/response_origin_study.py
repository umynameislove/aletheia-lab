"""Source-informed native response-origin transfer and ordinary repair comparison.

Fresh processes are robustness replicates nested in one cache implementation.
This is not untouched incident validation, throughput, or a new cache algorithm.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_audit import digest
from aletheia_lab.evaluation.response_origin_audit import (
    collisions,
    direct_only,
    explicit_join,
    native_identity,
    resolve,
    summary,
)
from aletheia_lab.evaluation.response_origin_reference import planned_tokens, rebuild
from aletheia_lab.evaluation.response_origin_source import ARMS, WORKFLOWS
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

FILES = (
    "scripts/response_origin_validation.py",
    "src/aletheia_lab/evaluation/response_origin_audit.py",
    "src/aletheia_lab/evaluation/response_origin_source.py",
    "src/aletheia_lab/evaluation/response_origin_reference.py",
    "src/aletheia_lab/evaluation/response_origin_study.py",
    "src/aletheia_lab/evaluation/request_model_audit.py",
    "src/aletheia_lab/evaluation/model_load_application.py",
    "src/aletheia_lab/evaluation/model_load_retention.py",
    "src/aletheia_lab/filesystem.py",
    "src/aletheia_lab/project/identity.py",
)


def design(root: Path) -> dict[str, Any]:
    cells: list[dict[str, Any]] = [
        {"arm": arm, "workflow": workflow, "replicate": repeat}
        for repeat in range(2)
        for arm in ARMS
        for workflow in WORKFLOWS
    ]
    random.Random(1907).shuffle(cells)
    return {
        "schema": "response-origin-transfer-development/v1",
        "cells": cells,
        "source": "https://raw.githubusercontent.com/SeldonIO/MLServer/1.7.1/mlserver/handlers/dataplane.py",
        "baseline_source": "https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/response_cache.html",
        "fence_prior": "https://www.usenix.org/system/files/conference/nsdi13/nsdi13-final170.pdf",
        "native_version": "1.7.1",
        "model_version_contract": "requested route name/version",
        "generation_contract": "resident selected at request entry; old in-flight completion legitimate; not an OIP immutable-byte requirement",
        "hypotheses": [
            "route namespace prevents cross-version cache collision but alone does not enforce fresh generation",
            "post-reload flush prevents serial staleness but delayed old insertion may refill stale data",
            "ordinary generation key or epoch fence prevents stale origin in all offered lifecycles",
            "direct-predict-only evidence misses legitimate and violating cache reuse; origin join restores correspondence",
        ],
        "ordinary_repairs": list(ARMS),
        "comparator": "explicit graph join, same capture",
        "planned_operation_count": sum(len(planned_tokens(cell["workflow"])) for cell in cells),
        "planned_inference_count": 5 * len(cells),
        "process_timeout_seconds": 90,
        "scope": "source-informed prospective lifecycle development; one MLServer cache source; two nested process replicates; not unseen incidents",
        "precision": "finite census verdicts; timings descriptive only, no inferential latency/gain claim",
        "failures": "all planned cells/requests retained; unattempted or failed workers unknown, never excluded",
        "provider_calls": 0,
        "protected_runs": 0,
        "source_bindings": {name: content_sha256((root / name).read_bytes()) for name in FILES},
    }


def _execute(root: Path, executable: Path, directory: Path, cell: dict[str, Any]) -> dict[str, Any]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root / "src")
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        environment[name] = "1"
    command = [
        str(executable),
        str(root / "scripts/response_origin_validation.py"),
        "worker",
        "--study-dir",
        str(directory),
        "--arm",
        cell["arm"],
        "--workflow",
        cell["workflow"],
    ]
    failure = None
    try:
        result = subprocess.run(
            command, cwd=root, env=environment, capture_output=True, timeout=90, check=False
        )
        stdout, stderr, returncode = result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, returncode = exc.stdout or b"", exc.stderr or b"", -1
        failure = "TimeoutExpired"
    directory.mkdir(parents=True, exist_ok=True)
    write_new_file(directory / "stdout.log", stdout)
    write_new_file(directory / "stderr.log", stderr)
    source = directory / "source.json"
    return {
        **cell,
        "returncode": returncode,
        "failure": failure,
        "source_sha256": content_sha256(source.read_bytes()) if source.is_file() else None,
    }


def analyze(source: dict[str, Any]) -> dict[str, Any]:
    frames, truth, native = rebuild(source)
    answers = {
        "origin": [resolve(frame) for frame in frames],
        "ordinary_join": [explicit_join(frame) for frame in frames],
        "direct_only": [direct_only(frame) for frame in frames],
        "native_body": [native_identity(row) for row in native],
        "native_headers": [native_identity(row, header=True) for row in native],
    }
    missing = 5 - len(truth)
    if missing < 0:
        raise ValueError("native inference census exceeded")
    truth += ["unknown"] * missing
    for values in answers.values():
        values.extend(["unknown"] * missing)
    summaries = {key: summary(truth, values) for key, values in answers.items()}
    if answers["origin"] != answers["ordinary_join"]:
        raise ValueError("same-access ordinary baseline disagrees")
    if summaries["origin"]["false_conclusive"] or summaries["origin"]["missing_conclusive"]:
        raise ValueError("admitted origin evidence does not reproduce raw reference")
    observable = [
        (status, row)
        for status, row in zip(truth, native, strict=False)
        if row.get("status") == 200 and row.get("response") and "outputs" in row["response"]
    ]
    return {
        "truth": truth,
        "answers": answers,
        "summaries": summaries,
        "planned_operations": len(planned_tokens(source["workflow"])),
        "attempted_operations": len(source["rows"]),
        "missing_inferences": missing,
        "http_status_counts": dict(
            Counter(str(row.get("status", "failed")) for row in source["rows"])
        ),
        "compute_count": sum(event["kind"] == "compute_return" for event in source["events"]),
        "cache_hit_count": sum(
            event["kind"] == "lookup" and event["hit"] for event in source["events"]
        ),
        "discarded_old_insertions": sum(
            event["kind"] == "insert" and not event["accepted"] for event in source["events"]
        ),
        "zero_output_violations": sum(
            status == "violation" and row["response"]["outputs"][0]["data"] == [0.0]
            for status, row in zip(truth, native, strict=False)
        ),
        "generation_hidden_collisions": collisions(
            [
                {
                    "model": row["response"].get("model_name"),
                    "version": row["response"].get("model_version"),
                    "input": row["body"]["inputs"],
                    "output": row["response"]["outputs"],
                }
                for _, row in observable
            ],
            [status for status, _ in observable],
        ),
        "operation_elapsed_ns": [row["elapsed_ns"] for row in source["rows"]],
        "source_terminal": source["terminal"],
    }


def _cell_result(directory: Path, execution: dict[str, Any]) -> dict[str, Any]:
    path = directory / "source.json"
    if execution["source_sha256"] is None:
        truth = ["unknown"] * 5
        return {
            **execution,
            "truth": truth,
            "answers": {
                key: list(truth)
                for key in (
                    "origin",
                    "ordinary_join",
                    "direct_only",
                    "native_body",
                    "native_headers",
                )
            },
            "source_terminal": "missing",
            "planned_operations": len(planned_tokens(execution["workflow"])),
            "attempted_operations": 0,
        }
    raw = path.read_bytes()
    if content_sha256(raw) != execution["source_sha256"]:
        raise ValueError("native source bytes changed")
    source = json.loads(raw)
    if (source["arm"], source["workflow"]) != (execution["arm"], execution["workflow"]):
        raise ValueError("worker identity differs from planned cell")
    if source["environment"]["packages"]["mlserver"] != "1.7.1":
        raise ValueError("native MLServer version differs")
    return {**execution, **analyze(source)}


def aggregate(cells: list[dict[str, Any]]) -> dict[str, Any]:
    by_arm = {}
    for arm in ARMS:
        own = [cell for cell in cells if cell["arm"] == arm]
        truth = [value for cell in own for value in cell["truth"]]
        by_arm[arm] = {
            "reference_counts": dict(Counter(truth)),
            "by_workflow": {
                workflow: dict(
                    Counter(
                        value
                        for cell in own
                        if cell["workflow"] == workflow
                        for value in cell["truth"]
                    )
                )
                for workflow in WORKFLOWS
            },
            "capture_comparators": {
                key: summary(truth, [value for cell in own for value in cell["answers"][key]])
                for key in own[0]["answers"]
            },
            "cache_hits": sum(cell.get("cache_hit_count", 0) for cell in own),
            "computations": sum(cell.get("compute_count", 0) for cell in own),
        }
    return {
        "cell_count": len(cells),
        "failed_or_missing_cells": sum(cell["source_terminal"] != "complete" for cell in cells),
        "planned_operations": sum(cell["planned_operations"] for cell in cells),
        "attempted_operations": sum(cell["attempted_operations"] for cell in cells),
        "offered_inferences": sum(len(cell["truth"]) for cell in cells),
        "by_arm": by_arm,
        "decision": (
            "incomplete native census; no efficacy conclusion"
            if any(cell["source_terminal"] != "complete" for cell in cells)
            else "retain ordinary origin join and standard repairs; no new checker/cache-policy superiority claimed"
        ),
        "scientific_scope": "bounded transfer across three controlled lifecycles of one new cache mechanism; not natural incident prevalence or unseen external validation",
        "provider_calls": 0,
        "protected_runs": 0,
    }


def run(root: Path, directory: Path, executable: Path) -> dict[str, Any]:
    if directory.exists() or directory.is_symlink():
        raise ValueError("fresh owned study directory required")
    plan = design(root)
    directory.mkdir(parents=True)
    write_new_file(directory / "plan.json", encode(plan).encode())
    cells = []
    for index, cell in enumerate(plan["cells"]):
        target = directory / f"cell-{index:02d}"
        execution = _execute(root, executable, target, cell)
        write_new_file(target / "execution.json", encode(execution).encode())
        cells.append(_cell_result(target, execution))
        print(
            json.dumps(
                {
                    "status": "response_origin_progress",
                    "completed_cells": index + 1,
                    "maximum_cells": len(plan["cells"]),
                }
            ),
            flush=True,
        )
    result = {"plan_sha256": digest(plan), "cells": cells, "analysis": aggregate(cells)}
    result["results_sha256"] = digest(result)
    write_new_file(directory / "results.json", encode(result).encode())
    return {
        "status": "response_origin_development_complete",
        "plan_sha256": result["plan_sha256"],
        "results_sha256": result["results_sha256"],
        "analysis": result["analysis"],
    }


def verify(root: Path, directory: Path) -> dict[str, Any]:
    plan = json.loads((directory / "plan.json").read_bytes())
    if plan != design(root):
        raise ValueError("bound implementation or design changed")
    result = json.loads((directory / "results.json").read_bytes())
    expected_hash = result.pop("results_sha256")
    if digest(result) != expected_hash or result["plan_sha256"] != digest(plan):
        raise ValueError("result/plan identity differs")
    cells = []
    for index, expected in enumerate(plan["cells"]):
        target = directory / f"cell-{index:02d}"
        execution = json.loads((target / "execution.json").read_bytes())
        if any(execution[key] != value for key, value in expected.items()):
            raise ValueError("planned execution identity differs")
        cells.append(_cell_result(target, execution))
    if cells != result["cells"] or aggregate(cells) != result["analysis"]:
        raise ValueError("raw independent replay differs from stored analysis")
    return {
        "status": "response_origin_read_only_replay_pass",
        "results_sha256": expected_hash,
        "analysis": result["analysis"],
        "native_execution_repeated": False,
    }
