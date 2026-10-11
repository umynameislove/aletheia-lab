"""Prospectively bound local cache lifecycle, complete census and immutable replay."""

from __future__ import annotations

import json
import os
import random
import subprocess
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

PROTOCOL = "configs/evaluation/cache_lifecycle_validation_protocol.json"
FILES = (
    PROTOCOL,
    "scripts/cache_lifecycle_validation.py",
    "src/aletheia_lab/evaluation/cache_lifecycle_source.py",
    "src/aletheia_lab/evaluation/cache_lifecycle_analysis.py",
    "src/aletheia_lab/evaluation/cache_lifecycle_study.py",
    "src/aletheia_lab/evaluation/litserve_evidence_provenance.py",
    "src/aletheia_lab/evaluation/model_load_provenance.py",
    "src/aletheia_lab/evaluation/model_load_retention.py",
    "src/aletheia_lab/filesystem.py",
    "src/aletheia_lab/project/identity.py",
)


def environment(root: Path, native_site: Path) -> dict[str, str]:
    allowed = ("PATH", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "WINDIR", "LOCALAPPDATA")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update(
        PYTHONPATH=os.pathsep.join((str(root / "src"), str(native_site))),
        PYTHONUNBUFFERED="1",
        OTEL_SDK_DISABLED="true",
        OMP_NUM_THREADS="1",
        OPENBLAS_NUM_THREADS="1",
        MKL_NUM_THREADS="1",
    )
    return env


def seal(value: dict[str, Any]) -> dict[str, Any]:
    if "sha256" in value:
        raise ValueError("cannot reseal an already sealed document")
    return {**value, "sha256": content_sha256(encode(value).encode())}


def read_sealed(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError("immutable study document must not be a symlink")
    value: dict[str, Any] = json.loads(path.read_bytes())
    observed = value.pop("sha256")
    if seal(value)["sha256"] != observed:
        raise ValueError("study document digest differs")
    return {**value, "sha256": observed}


def runtime_identity(root: Path, executable: Path, site: Path) -> dict[str, Any]:
    code = (
        "import importlib.metadata as m,json,hashlib,sys; from pathlib import Path; "
        "files={'cachetools':('cachetools/__init__.py','cachetools/_cached.py'),"
        "'aiohttp':('aiohttp/web_app.py','aiohttp/web_server.py')}; "
        "print(json.dumps({'python':sys.version.split()[0],"
        "'packages':{n:m.version(n) for n in ('cachetools','aiohttp','in-toto')},"
        "'sources':{f:hashlib.sha256(Path(m.distribution(n).locate_file(f)).read_bytes()).hexdigest() "
        "for n,fs in files.items() for f in fs}}))"
    )
    completed = subprocess.run(
        [str(executable), "-c", code],
        env=environment(root, site),
        capture_output=True,
        timeout=20,
        check=False,
    )
    if completed.returncode:
        raise ValueError("explicit runtime metadata unavailable")
    result: dict[str, Any] = json.loads(completed.stdout)
    return result


def design(root: Path, identity: dict[str, Any]) -> dict[str, Any]:
    protocol = json.loads((root / PROTOCOL).read_bytes())
    if any(identity["packages"][name] != wanted for name, wanted in protocol["runtime"].items()):
        raise ValueError("native versions differ from the forward protocol")
    cells = [
        {
            "arm": arm,
            "order": order,
            "evidence": evidence,
            "replicate": replicate,
            "steady_requests": protocol["steady_requests"],
        }
        for replicate in range(protocol["process_replicates"])
        for arm in protocol["arms"]
        for order in protocol["orders"]
        for evidence in protocol["evidence_modes"]
    ]
    random.Random(protocol["ordering_seed"]).shuffle(cells)
    return seal(
        {
            "schema": "cache-lifecycle-plan/v1",
            "protocol": protocol,
            "cells": cells,
            "runtime_identity": identity,
            "planned_inferences": len(cells)
            * (protocol["lifecycle_requests"] + protocol["steady_requests"]),
            "planned_reload_operations": len(cells) * protocol["reload_requests"],
            "bindings": {name: content_sha256((root / name).read_bytes()) for name in FILES},
        }
    )


def check_bindings(root: Path, plan: dict[str, Any]) -> None:
    if set(plan["bindings"]) != set(FILES):
        raise ValueError("code binding census differs")
    for name, expected in plan["bindings"].items():
        if (root / name).is_symlink() or content_sha256((root / name).read_bytes()) != expected:
            raise ValueError("executed code differs from the bound snapshot")
    if design(root, plan["runtime_identity"]) != plan:
        raise ValueError("rehashed plan changes the canonical design")


def prepare(root: Path, directory: Path, executable: Path, site: Path) -> dict[str, Any]:
    if directory.exists() or directory.is_symlink() or directory.is_relative_to(root):
        raise ValueError("fresh private study directory outside the public repository required")
    plan = design(root, runtime_identity(root, executable, site))
    directory.mkdir(parents=True)
    for name in FILES:
        write_new_file(directory / "code-snapshot" / name, (root / name).read_bytes())
    write_new_file(directory / "plan.json", encode(plan).encode())
    return {
        "status": "cache_lifecycle_plan_sealed",
        "plan_sha256": plan["sha256"],
        "cell_count": len(plan["cells"]),
        "planned_inferences": plan["planned_inferences"],
        "runtime_outcomes_observed": False,
        "provider_calls": 0,
    }


def _execute(
    root: Path,
    directory: Path,
    index: int,
    cell: dict[str, Any],
    executable: Path,
    site: Path,
    timeout: int,
) -> dict[str, Any]:
    config = directory / f"config-{index:03}.json"
    write_new_file(config, encode(cell).encode())
    target = directory / f"cell-{index:03}"
    command = [
        str(executable),
        str(root / "scripts/cache_lifecycle_validation.py"),
        "worker",
        "--study-dir",
        str(target),
        "--config",
        str(config),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=environment(root, site),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, returncode = exc.stdout or b"", exc.stderr or b"", -1
    except OSError as exc:
        stdout, stderr, returncode = b"", type(exc).__name__.encode(), -2
    target.mkdir(parents=True, exist_ok=True)
    write_new_file(target / "stdout.log", stdout)
    write_new_file(target / "stderr.log", stderr)
    source = target / "source.json"
    return {
        "index": index,
        "config": cell,
        "returncode": returncode,
        "source_sha256": content_sha256(source.read_bytes()) if source.is_file() else None,
    }


def run(root: Path, directory: Path, executable: Path, site: Path) -> dict[str, Any]:
    from aletheia_lab.evaluation.cache_lifecycle_analysis import aggregate, analyze

    plan = read_sealed(directory / "plan.json")
    check_bindings(root, plan)
    if (directory / "execution-started.json").exists():
        raise ValueError("never silently resume or rerun a started execution")
    if runtime_identity(root, executable, site) != plan["runtime_identity"]:
        raise ValueError("native runtime changed after preparation")
    write_new_file(
        directory / "execution-started.json", encode({"plan_sha256": plan["sha256"]}).encode()
    )
    executions, findings = [], []
    for index, cell in enumerate(plan["cells"]):
        execution = _execute(
            root, directory, index, cell, executable, site, plan["protocol"]["cell_timeout_seconds"]
        )
        executions.append(execution)
        finding = None
        if execution["source_sha256"] is not None:
            try:
                finding = analyze(
                    json.loads((directory / f"cell-{index:03}" / "source.json").read_bytes()),
                    directory / f"cell-{index:03}",
                    plan["protocol"],
                )
            except Exception as exc:
                # Third-party signature errors do not all derive from ValueError.
                # Preserve a failed cell rather than aborting the planned census.
                finding = {"verification": "fail", "error_type": type(exc).__name__}
        findings.append(finding)
        print(
            json.dumps(
                {
                    "status": "cache_lifecycle_progress",
                    "completed_cells": index + 1,
                    "maximum_cells": len(plan["cells"]),
                }
            ),
            flush=True,
        )
    check_bindings(root, plan)
    report = seal(
        {
            "schema": "cache-lifecycle-results/v1",
            "plan_sha256": plan["sha256"],
            "executions": executions,
            "findings": findings,
            "analysis": aggregate(plan, executions, findings),
        }
    )
    write_new_file(directory / "results.json", encode(report).encode())
    return {
        "status": "cache_lifecycle_executed",
        "results_sha256": report["sha256"],
        "analysis": report["analysis"],
    }


def verify(root: Path, directory: Path) -> dict[str, Any]:
    from aletheia_lab.evaluation.cache_lifecycle_analysis import aggregate, analyze

    plan, report = read_sealed(directory / "plan.json"), read_sealed(directory / "results.json")
    check_bindings(root, plan)
    if report["plan_sha256"] != plan["sha256"] or len(report["executions"]) != len(plan["cells"]):
        raise ValueError("execution census differs from bound plan")
    findings: list[dict[str, Any] | None] = []
    for index, (cell, execution) in enumerate(
        zip(plan["cells"], report["executions"], strict=True)
    ):
        if execution["config"] != cell or execution["index"] != index:
            raise ValueError("cell configuration differs")
        target = directory / f"cell-{index:03}"
        source = target / "source.json"
        if execution["source_sha256"] is None:
            if source.exists():
                raise ValueError("missing source inserted after execution")
            findings.append(None)
            continue
        if source.is_symlink() or content_sha256(source.read_bytes()) != execution["source_sha256"]:
            raise ValueError("raw source changed")
        try:
            finding = analyze(json.loads(source.read_bytes()), target, plan["protocol"])
        except Exception as exc:
            finding = {"verification": "fail", "error_type": type(exc).__name__}
        findings.append(finding)
    rebuilt = aggregate(plan, report["executions"], findings)
    if report["findings"] != findings or report["analysis"] != rebuilt:
        raise ValueError("independent raw/database replay disagrees with stored analysis")
    return {"verification": "pass", "results_sha256": report["sha256"], "analysis": rebuilt}


def _unchanged_primary(current: dict[str, Any], old: dict[str, Any]) -> None:
    for key in (
        "truth_counts",
        "violated_tokens",
        "compute_count",
        "forecasts",
        "measurements",
        "complete",
        "offered_inferences",
        "offered_reloads",
        "steady_latency_median_ns",
        "physical_bytes_live",
        "physical_bytes_closed",
        "logical_bytes",
        "commit_count",
        "signed_status",
    ):
        if current[key] != old[key]:
            raise ValueError("analysis correction changed a primary outcome or cost")
    for key in ("correct", "false", "unknown_or_unserved", "records"):
        if current["service"][key] != old["service"][key]:
            raise ValueError("analysis correction changed materialized service")


def _closeout_findings(
    directory: Path, plan: dict[str, Any], original: dict[str, Any]
) -> list[dict[str, Any]]:
    from aletheia_lab.evaluation.cache_lifecycle_analysis import analyze

    findings = []
    for index, (cell, execution) in enumerate(
        zip(plan["cells"], original["executions"], strict=True)
    ):
        target = directory / f"cell-{index:03}"
        if cell != execution["config"] or execution["index"] != index:
            raise ValueError("original cell identity differs")
        path = target / "source.json"
        if path.is_symlink() or content_sha256(path.read_bytes()) != execution["source_sha256"]:
            raise ValueError("original source digest differs")
        current = analyze(json.loads(path.read_bytes()), target, plan["protocol"])
        old = original["findings"][index]
        _unchanged_primary(current, old)
        findings.append(current)
    return findings


def closeout(root: Path, directory: Path) -> dict[str, Any]:
    """Analysis-only correction over bound immutable raw data; never serve again."""
    from aletheia_lab.evaluation import cache_lifecycle_analysis as analysis

    plan, original = read_sealed(directory / "plan.json"), read_sealed(directory / "results.json")
    check_bindings(root, plan)
    if (
        original["plan_sha256"] != plan["sha256"]
        or len(original["executions"]) != len(plan["cells"])
        or len(original["findings"]) != len(plan["cells"])
    ):
        raise ValueError("original execution census differs")
    findings = _closeout_findings(directory, plan, original)
    result = seal(
        {
            "schema": "cache-lifecycle-analysis-closeout/v1",
            "original_results_sha256": original["sha256"],
            "plan_sha256": plan["sha256"],
            "analysis_code_bindings": {
                path.name: content_sha256(path.read_bytes())
                for path in (Path(__file__), Path(analysis.__file__))
            },
            "correction": "Unsafe shared-key/clear conditional native comparator cannot use raw selected_generation as uncharged client evidence. Both remain unknown in this conservative tier, not an exhaustively strongest body/client-order comparator. It is excluded from strongest-native gap claims. Strong generation-lifetime source implications are a separate service. Primary physical-evidence service, predictions and measured costs are unchanged.",
            "analysis": analysis.aggregate(plan, original["executions"], findings),
            "conditional_native": {
                mode: {
                    "correct": sum(
                        f["service"]["conditional_native_correct"]
                        for f in findings
                        if f["config"]["evidence"] == mode
                    ),
                    "unknown": sum(
                        f["service"]["conditional_native_unknown"]
                        for f in findings
                        if f["config"]["evidence"] == mode
                    ),
                }
                for mode in plan["protocol"]["evidence_modes"]
            },
        }
    )
    path = directory / "analysis-closeout-v2.json"
    if path.exists():
        if read_sealed(path) != result:
            raise ValueError("immutable closeout differs")
    else:
        write_new_file(path, encode(result).encode())
    return {
        "verification": "pass",
        "closeout_sha256": result["sha256"],
        "analysis": result["analysis"],
        "conditional_native": result["conditional_native"],
    }
