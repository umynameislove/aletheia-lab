"""Forward composite-lifecycle forecasts and interrupted delayed-audit service."""

from __future__ import annotations

import base64
import copy
import os
import random
import subprocess
import sys
from collections import Counter
from pathlib import Path
from statistics import median
from time import perf_counter_ns
from typing import Any

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.calibration_audit_study import (
    FILES as SHARED_FILES,
)
from aletheia_lab.evaluation.calibration_audit_study import (
    archive,
    environment,
    read,
    sealed,
)
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.pipeline_audit_progress import rebuild
from aletheia_lab.evaluation.request_model_audit import digest
from aletheia_lab.filesystem import write_new_file

FILES = (
    *SHARED_FILES,
    *(
        f"src/aletheia_lab/evaluation/{name}.py"
        for name in (
            "audit_stage_journal",
            "pipeline_audit_source",
            "pipeline_audit_workload",
            "pipeline_audit_progress",
            "pipeline_audit_study",
        )
    ),
    "scripts/pipeline_audit_study.py",
)


def bindings(root: Path) -> dict[str, str]:
    return {name: file_sha256(root / name) for name in FILES}


def runtime() -> dict[str, Any]:
    from importlib import import_module

    result = environment()
    for name in ("sklearn.pipeline", "sklearn.preprocessing._data"):
        filename = import_module(name).__file__
        if filename is None:
            raise ValueError("native source identity missing")
        result["native_source_sha256"][name] = file_sha256(Path(filename))
    return result


def prepare(root: Path, directory: Path) -> dict[str, Any]:
    """Use development only to bound capsule growth, never evaluation outcomes."""
    from aletheia_lab.evaluation.pipeline_audit_source import ARMS, FORECASTS, predict, setup

    if directory.exists() or directory.parent.is_symlink():
        raise ValueError("fresh forward-study directory required")
    directory.mkdir()
    probe = directory / "development-charge"
    probe.mkdir()
    packet = predict(setup(4101), "probe")
    owner = archive("whole", probe / "archive.sqlite", 1_000_000)
    charges, growth = [], []
    # No evaluation inference: replay the one development capsule under new tokens.
    for index in range(64):
        token = f"call-{index:03d}"
        candidate = copy.deepcopy(packet)
        candidate["token"] = candidate["call"]["token"] = token
        candidate["call"]["transform_return"]["token"] = token
        before = owner._charge(owner.state)
        owner._install_packet(candidate)
        owner.state["leases"][f"pre:{token}"] = {"scopes": [token], "until": 30000}
        charge = owner._charge(owner.state)
        growth.append(charge - before)
        charges.append(charge)
    owner.close()
    bound = ((max(growth) + 4095) // 4096) * 4096
    last_reserve_32, last_reserve_64 = charges[30] + bound, charges[62] + bound
    pressure = (last_reserve_32 // 4096) * 4096 - 4096
    sufficient = ((max(charges[-1], last_reserve_64) * 5 // 4 + 4095) // 4096) * 4096
    plan = {
        "schema": "pipeline-audit-forward-study/v1",
        "execution_root": str(root),
        "study_directory": str(directory.absolute()),
        "code_sha256": bindings(root),
        "environment": runtime(),
        "development_seed": 4101,
        "evaluation_seeds": [5101, 5103],
        "cost_seed": 5401,
        "arms": list(ARMS),
        "forecasts": FORECASTS,
        "counts": [32, 64],
        "quotas": {"sufficient": sufficient, "pressure": pressure},
        "growth_bound": bound,
        "core_deadline_ms": 30000,
        "repeats": 2,
        "deadline_sensitivity_ms": [50, 2000],
        "timeout_seconds": 120,
        "process_order_seed": 9183,
        "floor_repeats": 1,
        "interruptions": ["after_reserve", "after_ack", "timeout_after_ack"],
        "interruption_timeout_seconds": 10,
        "interruption_seed": 5403,
        "charge_qualification": {
            "maximum_observed_growth": max(growth),
            "all_live_32": charges[31],
            "all_live_64": charges[63],
            "last_reserve_32": last_reserve_32,
            "last_reserve_64": last_reserve_64,
        },
        "service": "serial dispatch; all calls then delayed audit burst; inclusive deadline from offer; identical reserve/ACK/drain rules",
        "quota": "common canonical logical charge plus bounded future growth; NOT physical DB/WAL/SHM or journal quota",
        "comparison": "only all-planned same-service successes rank costs; native/hash are application floors with no audit offered",
        "timing": "all per-call journal fsync and research witness consume deadline/workload; measured separately; no concurrency/production overhead claim",
        "independence": "prospective generated-data composite transfer within sklearn; authored faults, capacity and deadlines, not natural deployment or operator SLO",
        "persistence": "WAL/FULL, owned process interruptions; no power-loss or hostile-host completeness guarantee",
        "stop": "one forward evaluation; retain contradictions, refusals, unknowns, partial worker failures and timeouts; do not tune outcomes",
        "transfer_interruption_limit": "cost workers have durable per-stage census; failed transfer workers mark every arm not tested and native census incomplete, never infer unrecorded calls",
        "provider_calls": 0,
    }
    write_new_file(directory / "plan.json", encode(sealed(plan)).encode())
    return {
        "status": "forward_pipeline_plan_sealed",
        "plan_sha256": digest(plan),
        "quotas": plan["quotas"],
        "growth_bound": bound,
        "charge_qualification": plan["charge_qualification"],
        "provider_calls": 0,
    }


def configurations(plan: dict[str, Any], *, development: bool = False) -> list[dict[str, Any]]:
    rows = [
        {
            "mode": mode,
            "count": count,
            "repeat": repeat,
            "quota_kind": kind,
            "deadline_ms": plan["core_deadline_ms"],
        }
        for mode in ("raw", "compact", "whole")
        for count in plan["counts"]
        for kind in plan["quotas"]
        for repeat in range(plan["repeats"])
    ]
    rows += [
        {
            "mode": mode,
            "count": count,
            "repeat": 0,
            "quota_kind": "pressure",
            "deadline_ms": deadline,
        }
        for mode in ("raw", "compact", "whole")
        for count in plan["counts"]
        for deadline in plan["deadline_sensitivity_ms"]
    ]
    rows += [
        {
            "mode": mode,
            "count": count,
            "repeat": 0,
            "quota_kind": "sufficient",
            "deadline_ms": plan["core_deadline_ms"],
        }
        for mode in ("native", "hash")
        for count in plan["counts"]
    ]
    if development:
        rows = [
            {
                "mode": mode,
                "count": 4,
                "repeat": 0,
                "quota_kind": "sufficient",
                "deadline_ms": 30000,
            }
            for mode in ("raw", "compact", "whole")
        ]
    result = [
        {
            **row,
            "quota": plan["quotas"][row["quota_kind"]],
            "bound": plan["growth_bound"],
            "seed": plan["development_seed"] if development else plan["cost_seed"],
        }
        for row in rows
    ]
    random.Random(plan["process_order_seed"]).shuffle(result)
    return result


def storage_bindings(directory: Path) -> dict[str, Any]:
    """Include partial WAL/SHM and journal bytes without checkpointing originals."""
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("owned study must not contain symlinks")
        if path.is_file() and (
            ".sqlite" in path.name
            or path.name == "progress.jsonl"
            or path.name.startswith(("config-", "diagnostics-", "transfer-"))
        ):
            result[str(path.relative_to(directory))] = {
                "sha256": file_sha256(path),
                "bytes": path.stat().st_size,
            }
    return result


def child(root: Path, arguments: list[str], *, timeout: int) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    env["OTEL_SDK_DISABLED"] = "true"
    command = [sys.executable, str(root / "scripts/pipeline_audit_study.py"), *arguments]
    started = perf_counter_ns()
    try:
        completed = subprocess.run(
            command, cwd=root, env=env, capture_output=True, timeout=timeout, check=False
        )
        returncode, stdout, stderr, timed_out = (
            completed.returncode,
            completed.stdout,
            completed.stderr,
            False,
        )
    except subprocess.TimeoutExpired as exc:
        returncode, stdout, stderr, timed_out = None, exc.stdout or b"", exc.stderr or b"", True
    return {
        "returncode": returncode,
        "timed_out": timed_out,
        "timeout_seconds": timeout,
        "command_sha256": digest(command),
        "parent_elapsed_ns": perf_counter_ns() - started,
        "stdout_base64": base64.b64encode(stdout).decode(),
        "stderr_base64": base64.b64encode(stderr).decode(),
    }


def execute_cost(
    root: Path, destination: Path, config: dict[str, Any], index: str, timeout: int
) -> dict[str, Any]:
    config_path, store = destination / f"config-{index}.json", destination / f"store-{index}"
    write_new_file(config_path, encode(sealed(config)).encode())
    diagnostics = child(
        root,
        ["cost-worker", "--config", str(config_path), "--directory", str(store)],
        timeout=timeout,
    )
    write_new_file(destination / f"diagnostics-{index}.json", encode(sealed(diagnostics)).encode())
    interrupted = diagnostics["timed_out"] or diagnostics["returncode"] != 0
    rebuilt = rebuild(store, config, interrupted=interrupted)
    row = {
        **config,
        "index": index,
        "status": "worker_failure" if interrupted else "complete",
        "diagnostics": diagnostics,
        **rebuilt,
    }
    if "interrupt" in config:
        row["control_disposition"] = interruption_disposition(row)
    return row


def transfer_worker(seed: int) -> dict[str, Any]:
    from aletheia_lab.evaluation.pipeline_audit_source import ARMS, execute

    return {"status": "complete", "seed": seed, "cases": [execute(arm, seed) for arm in ARMS]}


def run(root: Path, directory: Path, phase: str) -> dict[str, Any]:
    plan = read(directory / "plan.json")
    if plan["code_sha256"] != bindings(root) or plan["environment"] != runtime():
        raise ValueError("prospective code/runtime binding changed")
    destination = directory / phase
    destination.mkdir(exist_ok=False)
    seeds = [plan["development_seed"]] if phase == "development" else plan["evaluation_seeds"]
    transfers = []
    for seed in seeds:
        output = destination / f"transfer-{seed}.json"
        diagnostics = child(
            root,
            [
                "transfer-worker",
                "--directory",
                str(destination),
                "--seed",
                str(seed),
                "--output",
                str(output),
            ],
            timeout=plan["timeout_seconds"],
        )
        write_new_file(
            destination / f"diagnostics-transfer-{seed}.json", encode(sealed(diagnostics)).encode()
        )
        transfers.append(
            {
                "seed": seed,
                "diagnostics": diagnostics,
                "result": read(output) if diagnostics["returncode"] == 0 else None,
            }
        )
    configs = configurations(plan, development=phase == "development")
    costs = []
    for index, config in enumerate(configs):
        costs.append(execute_cost(root, destination, config, str(index), plan["timeout_seconds"]))
        print(
            encode(
                {
                    "status": "pipeline_audit_progress",
                    "completed_processes": index + 1,
                    "planned_processes": len(configs),
                }
            ),
            flush=True,
        )
    controls = []
    for index, config in enumerate(control_configurations(plan, phase)):
        controls.append(
            execute_cost(
                root,
                destination,
                config,
                f"interrupt-{index}",
                plan["interruption_timeout_seconds"],
            )
        )
    report = {
        "schema": plan["schema"],
        "plan_sha256": digest(plan),
        "phase": phase,
        "transfers": transfers,
        "costs": costs,
        "controls": controls,
        "storage_bindings": storage_bindings(destination),
    }
    report["analysis"] = summarize(plan, report)
    if plan["code_sha256"] != bindings(root) or plan["environment"] != runtime():
        raise ValueError("source/runtime changed during the owned execution")
    write_new_file(destination / "results.json", encode(sealed(report)).encode())
    return {
        "status": "forward_pipeline_execution_complete",
        "results_sha256": digest(report),
        "analysis": report["analysis"],
        "provider_calls": 0,
    }


def control_configurations(plan: dict[str, Any], phase: str) -> list[dict[str, Any]]:
    return [
        {
            "mode": "compact",
            "count": 4,
            "repeat": 0,
            "quota_kind": "sufficient",
            "quota": plan["quotas"]["sufficient"],
            "bound": plan["growth_bound"],
            "deadline_ms": 30000,
            "seed": plan["development_seed"]
            if phase == "development"
            else plan["interruption_seed"],
            "interrupt": interruption,
        }
        for interruption in plan["interruptions"]
    ]


def summarize(plan: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    from aletheia_lab.evaluation.pipeline_audit_source import QUERIES, assess, history_reference

    predictions: list[dict[str, Any]] = []
    for transfer in report["transfers"]:
        if transfer["result"] is None:
            predictions.extend(
                {"seed": transfer["seed"], "arm": arm, "status": "not_tested"}
                for arm in plan["arms"]
            )
            continue
        cases = transfer["result"]["cases"]
        if [case["arm"] for case in cases] != plan["arms"]:
            raise ValueError("locked arm census differs")
        for case in cases:
            answers, reference = assess(case["captured_packet"]), history_reference(case)
            expected = dict(zip(QUERIES, plan["forecasts"][case["arm"]], strict=True))
            if (
                case["forecast"] != expected
                or answers != case["captured_answers"]
                or reference != case["ordinary_complete_history"]
            ):
                raise ValueError("prospective or independent comparison differs")
            before, after = case["native_packets"]
            same = before["call"]["output"] == after["call"]["output"]
            if same != case["same_entire_probability_output"]:
                raise ValueError("native output counterpair differs")
            predictions.append(
                {
                    "seed": case["seed"],
                    "arm": case["arm"],
                    "status": "supported" if answers == expected else "contradicted",
                    "forecast": expected,
                    "observed": answers,
                    "native_history": reference,
                    "same_entire_probability_output": same,
                    "false_conclusions": [
                        q
                        for q in QUERIES
                        if answers[q] in {"compliant", "violation"} and answers[q] != reference[q]
                    ],
                }
            )
    groups: dict[str, list[dict[str, Any]]] = {}
    for cost in report["costs"]:
        key = f"{cost['mode']}:{cost['count']}:{cost['quota_kind']}:{cost['deadline_ms']}"
        groups.setdefault(key, []).append(cost)
    frontier = []
    for key, rows in groups.items():
        successful = [row for row in rows if row["status"] == "complete"]
        eligible = (
            len(successful) == len(rows)
            and rows[0]["mode"] in {"raw", "compact", "whole"}
            and all(row["service"]["complete"] == row["count"] for row in rows)
        )
        frontier.append(
            {
                "configuration": key,
                "planned_processes": len(rows),
                "completed_processes": len(successful),
                "same_service_eligible": eligible,
                "planned_calls": sum(r["count"] for r in rows),
                "census": dict(sum((Counter(r["service"]) for r in rows), Counter())),
                "median_workload_ms": median(r["terminal"]["workload_ns"] / 1e6 for r in successful)
                if successful
                else None,
                "median_peak_db_wal_shm_bytes": median(
                    r["terminal"]["archive_metrics"].get("peak_db_wal_shm_bytes", 0)
                    for r in successful
                )
                if successful
                else None,
                "median_closed_db_bytes": median(
                    r["terminal"]["closed_storage_bytes"] for r in successful
                )
                if successful
                else None,
                "median_progress_journal_bytes": median(r["journal_bytes"] for r in successful)
                if successful
                else None,
                "median_journal_ms": median(
                    r["terminal"]["timers"]["progress_journal_ns"] / 1e6 for r in successful
                )
                if successful
                else None,
            }
        )
    return {
        "prediction_table": predictions,
        "prediction_status_counts": dict(Counter(p["status"] for p in predictions)),
        "native_transfer_predictions": sum(
            c["native_top_level_predictions"]
            for t in report["transfers"]
            if t["result"]
            for c in t["result"]["cases"]
        ),
        "native_transfer_failures": sum(
            c["native_failed_predictions"]
            for t in report["transfers"]
            if t["result"]
            for c in t["result"]["cases"]
        ),
        "frontier": frontier,
        "interruption_census": [
            {
                "interrupt": r["interrupt"],
                "diagnostics": {
                    k: r["diagnostics"][k] for k in ("returncode", "timed_out", "timeout_seconds")
                },
                "service": r["service"],
                "control_disposition": r["control_disposition"],
            }
            for r in report["controls"]
        ],
        "false_conclusions": sum(len(p.get("false_conclusions", [])) for p in predictions)
        + sum(r["service"]["wrong"] for r in report["costs"]),
        "paper_b": "no method admitted; ordinary baselines, authored service sensitivities; reject any ranking of unequal or partially served obligations",
    }


def verify(root: Path, directory: Path, phase: str) -> dict[str, Any]:
    plan, report = read(directory / "plan.json"), read(directory / phase / "results.json")
    destination = directory / phase
    if plan["code_sha256"] != bindings(root) or report["plan_sha256"] != digest(plan):
        raise ValueError("sealed source/plan identity differs")
    _verify_transfers(plan, report, destination, phase)
    expected = configurations(plan, development=phase == "development")
    _verify_costs(plan, report["costs"], expected, destination, controls=False)
    _verify_costs(
        plan, report["controls"], control_configurations(plan, phase), destination, controls=True
    )
    if (
        storage_bindings(destination) != report["storage_bindings"]
        or summarize(plan, report) != report["analysis"]
    ):
        raise ValueError("retained files or analysis differs")
    return {
        "status": "forward_pipeline_read_only_replay_pass",
        "results_sha256": digest(report),
        "analysis": report["analysis"],
        "provider_calls": 0,
    }


def _verify_diagnostics(
    plan: dict[str, Any],
    diagnostic: dict[str, Any],
    retained: Path,
    arguments: list[str],
    timeout: int,
) -> bool:
    if diagnostic != read(retained):
        raise ValueError("parent diagnostics differ from retained terminal receipt")
    command = [
        sys.executable,
        str(Path(plan["execution_root"]) / "scripts/pipeline_audit_study.py"),
        *arguments,
    ]
    if diagnostic["command_sha256"] != digest(command) or diagnostic["timeout_seconds"] != timeout:
        raise ValueError("parent command/timeout differs from frozen execution")
    if type(diagnostic["timed_out"]) is not bool or (
        diagnostic["timed_out"] and diagnostic["returncode"] is not None
    ):
        raise ValueError("parent terminal status differs")
    for key in ("stdout_base64", "stderr_base64"):
        base64.b64decode(diagnostic[key], validate=True)
    return bool(diagnostic["timed_out"] or diagnostic["returncode"] != 0)


def _verify_transfers(
    plan: dict[str, Any], report: dict[str, Any], destination: Path, phase: str
) -> None:
    seeds = [plan["development_seed"]] if phase == "development" else plan["evaluation_seeds"]
    if [row["seed"] for row in report["transfers"]] != seeds:
        raise ValueError("planned transfer seeds differ")
    for row in report["transfers"]:
        seed = row["seed"]
        output = destination / f"transfer-{seed}.json"
        arguments = [
            "transfer-worker",
            "--directory",
            str(destination),
            "--seed",
            str(seed),
            "--output",
            str(output),
        ]
        interrupted = _verify_diagnostics(
            plan,
            row["diagnostics"],
            destination / f"diagnostics-transfer-{seed}.json",
            arguments,
            plan["timeout_seconds"],
        )
        if interrupted:
            if row["result"] is not None:
                raise ValueError("failed transfer presents completed output")
            continue
        result = read(output)
        if row["result"] != result or result["seed"] != seed or result["status"] != "complete":
            raise ValueError("retained transfer output differs")
        for case in result["cases"]:
            if (
                case["seed"] != seed
                or case["native_top_level_predictions"] != 2
                or case["native_failed_predictions"] != int(case["arm"] == "native_failure")
            ):
                raise ValueError("native transfer case census differs")


def _verify_costs(
    plan: dict[str, Any],
    rows: list[dict[str, Any]],
    expected: list[dict[str, Any]],
    destination: Path,
    *,
    controls: bool,
) -> None:
    for number, (row, config) in enumerate(zip(rows, expected, strict=True)):
        index = f"interrupt-{number}" if controls else str(number)
        config_path, store = destination / f"config-{index}.json", destination / f"store-{index}"
        if (
            row["index"] != index
            or read(config_path) != config
            or any(row[key] != value for key, value in config.items())
        ):
            raise ValueError("planned input/index configuration differs")
        arguments = ["cost-worker", "--config", str(config_path), "--directory", str(store)]
        timeout = plan["interruption_timeout_seconds"] if controls else plan["timeout_seconds"]
        interrupted = _verify_diagnostics(
            plan, row["diagnostics"], destination / f"diagnostics-{index}.json", arguments, timeout
        )
        if row["status"] != ("worker_failure" if interrupted else "complete"):
            raise ValueError("worker result and terminal disposition differ")
        rebuilt = rebuild(store, config, interrupted=interrupted)
        if any(row[key] != value for key, value in rebuilt.items()):
            raise ValueError("worker progress/service rebuild differs")
        if not interrupted and config["mode"] not in {"native", "hash"}:
            _verify_closed_archive(row, config, store)
        if controls and row["control_disposition"] != interruption_disposition(row):
            raise ValueError("interruption target disposition differs")


def _verify_closed_archive(row: dict[str, Any], config: dict[str, Any], store: Path) -> None:
    from aletheia_lab.evaluation.calibration_audit_verification import (
        _durable_state,
        _witness_state,
    )

    state = _durable_state(store / "archive.sqlite", config["mode"], config["quota"])
    if state != _witness_state(row["terminal"]["final_witness"], config["quota"]):
        raise ValueError("closed archive differs from final witnessed frontier")
    sizes = sum(p.stat().st_size for p in store.glob("archive.sqlite*"))
    if sizes != row["terminal"]["closed_storage_bytes"]:
        raise ValueError("closed database size differs")
    for field in ("accepted", "refused"):
        if state[field] != row["service"][field]:
            raise ValueError("closed archive admission census differs")
    if state["overruns"] != row["service"]["accepted_retention_overruns"]:
        raise ValueError("closed archive lost an accepted retention overrun")


def interruption_disposition(row: dict[str, Any]) -> str:
    """An expected child failure alone does not qualify its intended boundary."""
    census, diagnostic = row["service"], row["diagnostics"]
    kind = row["interrupt"]
    expected_native = 0 if kind == "after_reserve" else 1
    terminal = (
        diagnostic["timed_out"]
        if kind == "timeout_after_ack"
        else diagnostic["returncode"] == 23 and not diagnostic["timed_out"]
    )
    reached = (
        census["offered"] == census["accepted"] == 1
        and census["complete"] == 0
        and census["native_completed"] == expected_native
        and census["retention_acknowledged"] == expected_native
        and census["not_started"] == row["count"] - 1
    )
    return "target_reached" if terminal and reached else "qualification_failed"
