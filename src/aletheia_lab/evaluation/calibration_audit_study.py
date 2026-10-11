"""Prospective controlled calibration transfer and durable same-service costs."""

from __future__ import annotations

import copy
import json
import os
import random
import subprocess
import sys
from importlib import import_module, metadata
from pathlib import Path
from time import perf_counter_ns
from typing import Any, cast

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.calibration_audit_analysis import summarize
from aletheia_lab.evaluation.calibration_audit_archive import FullCalibrationArchive
from aletheia_lab.evaluation.calibration_audit_recovery import RecoveryTier
from aletheia_lab.evaluation.calibration_audit_source import (
    ARMS,
    FORECASTS,
    assess,
    execute,
    predict,
    setup,
)
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_audit import digest
from aletheia_lab.filesystem import write_new_file

FILES = (
    "src/aletheia_lab/evaluation/calibration_audit_source.py",
    "src/aletheia_lab/evaluation/calibration_audit_archive.py",
    "src/aletheia_lab/evaluation/calibration_audit_storage.py",
    "src/aletheia_lab/evaluation/calibration_audit_recovery.py",
    "src/aletheia_lab/evaluation/calibration_audit_analysis.py",
    "src/aletheia_lab/evaluation/calibration_audit_verification.py",
    "src/aletheia_lab/evaluation/calibration_audit_study.py",
    "scripts/calibration_audit_study.py",
    "src/aletheia_lab/evaluation/incident_audit_archive.py",
    "src/aletheia_lab/evaluation/incident_audit_incremental.py",
    "src/aletheia_lab/evaluation/audit_bundle_policy.py",
    "src/aletheia_lab/evaluation/request_model_audit.py",
    "src/aletheia_lab/evaluation/request_model_retention.py",
    "src/aletheia_lab/evaluation/model_load_retention.py",
    "src/aletheia_lab/content_hashing.py",
)
MODES = ("native", "hash", "raw", "compact", "whole")


def sealed(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "sha256": digest(payload)}


def read(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError("owned evidence cannot be a symlink")
    result = json.loads(path.read_bytes())
    identity = result.pop("sha256")
    if digest(result) != identity:
        raise ValueError("evidence identity differs")
    return cast(dict[str, Any], result)


def bindings(root: Path) -> dict[str, str]:
    return {name: file_sha256(root / name) for name in FILES}


def environment() -> dict[str, Any]:
    upstream = {}
    for module in ("sklearn.calibration", "sklearn.frozen._frozen", "sklearn.linear_model._base"):
        filename = import_module(module).__file__
        if filename is None:
            raise ValueError("native source identity missing")
        upstream[module] = file_sha256(Path(filename))
    return {
        "python": sys.version.split()[0],
        "packages": {name: metadata.version(name) for name in ("scikit-learn", "numpy", "scipy")},
        "native_source_sha256": upstream,
    }


def prepare(root: Path, directory: Path) -> dict[str, Any]:
    if directory.exists() or directory.parent.is_symlink():
        raise ValueError("fresh owned study required")
    plan = {
        "schema": "calibration-audit-study/v1",
        "code_sha256": bindings(root),
        "environment": environment(),
        "development_seeds": [2301],
        "evaluation_seeds": [2801, 2803],
        "arms": list(ARMS),
        "forecasts": FORECASTS,
        "modes": list(MODES),
        "counts": [8, 24],
        "repeats": 3,
        "core_deadline_ms": 30000,
        "deadline_sensitivity_ms": [50, 2000],
        "logical_quota": 262144,
        "growth_bound": 32768,
        "cost_seed": 3401,
        "timeout_seconds": 120,
        "provider_calls": 0,
        "query": "certification of a successful closed call; historical membership also reported independently",
        "service": "serial dispatch; prospective complete-capsule retention reservation; delayed burst audit; deadline from offer on monotonic clock",
        "quota": "common canonical compressed logical charge, NOT equal physical allocation; physical DB/WAL/SHM and recovery tier charged separately",
        "durability": "WAL/FULL acknowledged transaction; process-exit controls, not total power-loss assurance",
        "independence": "source-informed new upstream lifecycle; generated data and authored interventions; not natural deployment or source-blind framework transfer",
        "deadline_grounding": "authored operational sensitivity; no operator SLO supplied",
        "comparison": "raw/compact/whole have identical capsules, reserve/lease/drain, quota semantics and durability; native/hash are insufficient-evidence ablations",
        "stop": "retain all planned failures and contradicted predictions; no outcome-based parameter changes or validation rerun",
    }
    write_new_file(directory / "plan.json", encode(sealed(plan)).encode())
    return {
        "status": "calibration_audit_plan_fixed",
        "plan_sha256": digest(plan),
        "provider_calls": 0,
    }


def archive(mode: str, path: Path, quota: int, *, reopen: bool = False) -> Any:
    from aletheia_lab.evaluation.calibration_audit_storage import (
        CompactEvidenceArchive,
        RawEvidenceArchive,
    )

    cls = {
        "raw": RawEvidenceArchive,
        "compact": CompactEvidenceArchive,
        "whole": FullCalibrationArchive,
    }[mode]
    return cls(path, "lru", quota, reopen=reopen)


def _bytes(path: Path) -> int:
    return sum(
        candidate.stat().st_size
        for suffix in ("", "-wal", "-shm")
        if (candidate := Path(str(path) + suffix)).exists()
    )


def cost_worker(config: dict[str, Any], directory: Path) -> dict[str, Any]:
    """One fresh process; no sockets, paid provider or throughput claim."""
    directory.mkdir(exist_ok=False)
    mode, count = config["mode"], config["count"]
    started = perf_counter_ns()
    session = setup(config["seed"])
    predict(session, "warmup")
    setup_ns = perf_counter_ns() - started
    if mode in {"native", "hash"}:
        return native_cost(config, session, setup_ns)
    owner = (
        archive(mode, directory / "archive.sqlite", config["quota"])
        if mode in {"raw", "compact", "whole"}
        else None
    )
    calls, audit_rows, offers = [], [], []
    timers = dict.fromkeys(
        (
            "serving_ns",
            "capture_ns",
            "reserve_ack_ns",
            "write_ack_ns",
            "query_verify_ns",
            "drain_ns",
            "hash_ns",
            "research_witness_ns",
        ),
        0,
    )
    origin = perf_counter_ns()

    def now() -> int:
        return (perf_counter_ns() - origin) // 1_000_000

    for index in range(count):
        token, offered = f"call-{index:03d}", now()
        until = offered + config["deadline_ms"]
        begin = perf_counter_ns()
        accepted = (
            owner.reserve(token, now=offered, until=until, bound=config["bound"])
            if owner
            else False
        )
        timers["reserve_ack_ns"] += perf_counter_ns() - begin
        begin = perf_counter_ns()
        packet = predict(session, token)
        elapsed = perf_counter_ns() - begin
        timers["serving_ns"] += session["last_prediction_ns"]
        timers["capture_ns"] += elapsed - session["last_prediction_ns"]
        if mode == "hash":
            begin = perf_counter_ns()
            digest([packet["call"]["input"], packet["call"]["output"]])
            timers["hash_ns"] += perf_counter_ns() - begin
        begin = perf_counter_ns()
        retained = owner.put_evidence(packet, now=now()) if owner else False
        timers["write_ack_ns"] += perf_counter_ns() - begin
        # Research truth is private verification output, not accessible to query.
        calls.append(packet)
        offers.append(
            {
                "token": token,
                "offered_ms": offered,
                "until_ms": until,
                "accepted": accepted,
                "retained_ack": retained,
                "ack_ms": now(),
            }
        )
    for offer in offers:
        begin = perf_counter_ns()
        token = offer["token"]
        if owner:
            owner.query([token], now=now())
            stored = owner.evidence(token)
            answer = (
                assess(stored)
                if stored
                else dict.fromkeys(
                    ("authorized_state", "disjoint_membership", "sigmoid_arithmetic"), "unknown"
                )
            )
        else:
            answer = dict.fromkeys(
                ("authorized_state", "disjoint_membership", "sigmoid_arithmetic"), "unknown"
            )
        finished = now()
        timers["query_verify_ns"] += perf_counter_ns() - begin
        correct = all(value == "compliant" for value in answer.values())
        late = finished > offer["until_ms"]
        audit_rows.append(
            {
                "token": token,
                "answer": answer,
                "finished_ms": finished,
                "correct": correct,
                "late": late,
                "complete": bool(offer["accepted"] and correct and not late),
            }
        )
        if owner:
            begin = perf_counter_ns()
            audit_rows[-1]["witness"] = owner.witness([token])
            timers["research_witness_ns"] += perf_counter_ns() - begin
        if owner:
            begin = perf_counter_ns()
            drain_completed(owner, token, now=now())
            timers["drain_ns"] += perf_counter_ns() - begin
    workload_ns = perf_counter_ns() - origin
    metrics = owner.snapshot() if owner else {}
    final_witness = owner.witness([offer["token"] for offer in offers]) if owner else None
    peak_bytes = metrics.get("peak_db_wal_shm_bytes", 0)
    if owner:
        owner.close()
        recovered = archive(mode, directory / "archive.sqlite", config["quota"], reopen=True)
        reopened = sum(recovered.evidence(item["token"]) is not None for item in offers)
        recovered.close()
    else:
        reopened = 0
    service = {
        "accepted": sum(item["accepted"] for item in offers),
        "refused": sum(not item["accepted"] for item in offers),
        "complete": sum(item["complete"] for item in audit_rows),
        "unknown": sum(
            any(value == "unknown" for value in item["answer"].values()) for item in audit_rows
        ),
        "late": sum(item["late"] for item in audit_rows),
        "wrong": sum(
            any(value == "violation" for value in item["answer"].values()) for item in audit_rows
        ),
        "accepted_but_unserved": sum(
            offer["accepted"] and not result["complete"]
            for offer, result in zip(offers, audit_rows, strict=True)
        ),
    }
    return {
        **config,
        "status": "complete",
        "setup_ns": setup_ns,
        "workload_ns": workload_ns,
        "timers": timers,
        "service": service,
        "offers": offers,
        "audits": audit_rows,
        "native_packets": calls,
        "fit_call_ledger": session["ledger"],
        "archive_metrics": metrics,
        "final_witness": final_witness,
        "closed_storage_bytes": _bytes(directory / "archive.sqlite"),
        "peak_storage_bytes": peak_bytes,
        "reopened_scopes": reopened,
        "native_predictions": count,
        "setup_predictions": 1,
        "provider_calls": 0,
        "timer_scope": "stage wall times are disjoint; nested archive read/write metrics MUST NOT be added to them; setup and reopen excluded from workload",
    }


def drain_completed(owner: Any, token: str, *, now: int) -> bool:
    """Do not release a lease which expires at the next transition's clock."""
    identifier = f"pre:{token}"
    lease = owner.state["leases"].get(identifier)
    if lease is None or lease["until"] < now:
        return False
    owner.drain(identifier, now=now)
    return True


def transfer_worker(seed: int) -> dict[str, Any]:
    cases = []
    for arm in ARMS:
        cases.append(execute(arm, seed))
    return {"status": "complete", "seed": seed, "cases": cases}


def native_cost(config: dict[str, Any], session: dict[str, Any], setup_ns: int) -> dict[str, Any]:
    """Actual direct predictor floor; no per-call membership/state capture."""
    native_ns = hash_ns = 0
    started = perf_counter_ns()
    output = None
    for _ in range(config["count"]):
        begin = perf_counter_ns()
        output = session["model"].predict_proba(session["query"])
        native_ns += perf_counter_ns() - begin
        if config["mode"] == "hash":
            begin = perf_counter_ns()
            digest([session["query"].tolist(), output.tolist()])
            hash_ns += perf_counter_ns() - begin
    elapsed = perf_counter_ns() - started
    if output is None:
        raise ValueError("nonempty native workload required")
    return {
        **config,
        "status": "complete",
        "setup_ns": setup_ns,
        "workload_ns": elapsed,
        "timers": {"serving_ns": native_ns, "hash_ns": hash_ns},
        "service": {
            "accepted": 0,
            "refused": config["count"],
            "complete": 0,
            "unknown": config["count"],
            "late": 0,
            "wrong": 0,
            "accepted_but_unserved": 0,
        },
        "offers": [],
        "audits": [],
        "native_packets": [],
        "archive_metrics": {},
        "closed_storage_bytes": 0,
        "peak_storage_bytes": 0,
        "reopened_scopes": 0,
        "native_predictions": config["count"],
        "setup_predictions": 1,
        "last_output_sha256": digest(output.tolist()),
        "provider_calls": 0,
        "audit_eligibility": "application floor or hash ablation; no durable audit service offered",
    }


def recovery_control(directory: Path, mode: str, seed: int) -> dict[str, Any]:
    directory.mkdir(exist_ok=False)
    session = setup(seed)
    primary, tier = (
        archive(mode, directory / "primary.sqlite", 24000),
        RecoveryTier(directory / "tier.sqlite"),
    )
    original = None
    for index in range(8):
        packet = predict(session, f"recover-{index}")
        if index == 0:
            original = copy.deepcopy(packet)
        tier.put(packet)  # Charged before primary eviction, including original token.
        primary.put_evidence(packet, now=index)
    absent = primary.evidence("recover-0") is None
    before = primary.witness(["recover-0"])
    tier_stats = {
        "write_ns": tier.write_ns,
        "payload_bytes": tier.payload_bytes,
        "peak_bytes": tier.peak_bytes,
    }
    tier.close()
    tier = RecoveryTier(directory / "tier.sqlite", reopen=True)
    missing = tier.fetch("unavailable-token") is None
    fetched = tier.fetch("recover-0")
    restored = fetched is not None and primary.restore_evidence([fetched], now=9)
    exact = primary.evidence("recover-0") == original
    result = {
        "mode": mode,
        "control": "original_token_eviction_recovery",
        "evicted_original": absent,
        "restored": restored,
        "exact_original": exact,
        "unavailable_fetch_returns_none": missing,
        "tier": {**tier_stats, "read_ns": tier.read_ns, "closed_bytes": 0},
        "primary_peak_bytes": primary.snapshot()["peak_db_wal_shm_bytes"],
        "before_recovery": before,
        "after_recovery": primary.witness(["recover-0"]),
        "original_packet": original,
        "fetched_packet": fetched,
        "status": "pass" if absent and restored and exact and missing else "fail",
    }
    tier.close()
    primary.close()
    result["tier"]["closed_bytes"] = _bytes(directory / "tier.sqlite")
    return result


def crash_worker(directory: Path, mode: str, phase: str) -> None:
    directory.mkdir(exist_ok=False)
    packet = predict(setup(4201), "crash-call")
    write_new_file(directory / "packet.json", encode(sealed(packet)).encode())
    owner = archive(mode, directory / "primary.sqlite", 131072)
    if phase == "before_commit":
        table = {"raw": "raw_fragments", "compact": "fragments", "whole": "state"}[mode]
        owner.db.create_function("exit_before_commit", 0, lambda: os._exit(19))
        owner.db.execute(
            f"CREATE TEMP TRIGGER owned_exit BEFORE INSERT ON {table} BEGIN SELECT exit_before_commit(); END"
        )
        owner.put_evidence(packet, now=0)
        raise RuntimeError("owned transaction exit was not reached")
    if phase == "after_ack":
        owner.put_evidence(packet, now=0)
        write_new_file(directory / "ack.json", b"true")
    os._exit(19)  # Owned child abrupt process exit, NOT host power loss.


def _child(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    env["OTEL_SDK_DISABLED"] = "true"
    return subprocess.run(
        [sys.executable, str(root / "scripts/calibration_audit_study.py"), *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def run(root: Path, directory: Path, phase: str) -> dict[str, Any]:
    plan = read(directory / "plan.json")
    if plan["code_sha256"] != bindings(root) or plan["environment"] != environment():
        raise ValueError("prospective code/native environment changed")
    destination = directory / phase
    destination.mkdir(exist_ok=False)
    seeds = plan["development_seeds"] if phase == "development" else plan["evaluation_seeds"]
    transfers = run_transfers(root, destination, seeds)
    costs = run_costs(root, destination, plan, phase)
    controls = run_controls(root, destination) if phase == "evaluation" else []
    payload = {
        "schema": plan["schema"],
        "plan_sha256": digest(plan),
        "phase": phase,
        "transfers": transfers,
        "costs": costs,
        "controls": controls,
    }
    payload["analysis"] = summarize(transfers, costs, controls)
    payload["database_sha256"] = database_bindings(destination)
    write_new_file(destination / "results.json", encode(sealed(payload)).encode())
    return {
        "status": "calibration_audit_execution_complete",
        "phase": phase,
        "results_sha256": digest(payload),
        "analysis": payload["analysis"],
        "provider_calls": 0,
    }


def run_transfers(root: Path, destination: Path, seeds: list[int]) -> list[dict[str, Any]]:
    transfers = []
    for seed in seeds:
        output = destination / f"transfer-{seed}.json"
        try:
            child = _child(root, ["transfer-worker", "--seed", str(seed), "--output", str(output)])
            transfers.append(
                read(output)
                if child.returncode == 0
                else {"status": "worker_failure", "seed": seed, "error_type": "child_failure"}
            )
        except subprocess.TimeoutExpired:
            transfers.append(
                {"status": "worker_failure", "seed": seed, "error_type": "TimeoutExpired"}
            )
    return transfers


def run_costs(
    root: Path, destination: Path, plan: dict[str, Any], phase: str
) -> list[dict[str, Any]]:
    from aletheia_lab.evaluation.calibration_audit_verification import (
        configurations as planned_configurations,
    )

    configurations = planned_configurations(plan, phase)
    random.Random(9181).shuffle(configurations)
    costs = []
    for index, config in enumerate(configurations):
        config.update(
            {
                "seed": plan["cost_seed"],
                "quota": plan["logical_quota"],
                "bound": plan["growth_bound"],
            }
        )
        config_path, output = (
            destination / f"config-{index}.json",
            destination / f"cost-{index}.json",
        )
        write_new_file(config_path, encode(sealed(config)).encode())
        try:
            child = _child(
                root,
                [
                    "cost-worker",
                    "--config",
                    str(config_path),
                    "--directory",
                    str(destination / f"cost-store-{index}"),
                    "--output",
                    str(output),
                ],
            )
            row = (
                read(output)
                if child.returncode == 0
                else {**config, "status": "worker_failure", "error_type": "child_failure"}
            )
        except subprocess.TimeoutExpired:
            row = {**config, "status": "worker_failure", "error_type": "TimeoutExpired"}
        costs.append(row)
        print(
            encode(
                {
                    "status": "calibration_audit_progress",
                    "completed_processes": index + 1,
                    "planned_processes": len(configurations),
                }
            ),
            flush=True,
        )
    return costs


def run_controls(root: Path, destination: Path) -> list[dict[str, Any]]:
    controls = []
    for mode in ("raw", "compact", "whole"):
        try:
            controls.append(recovery_control(destination / f"recovery-{mode}", mode, 4201))
        except (ValueError, OSError, RuntimeError) as exc:
            controls.append(
                {
                    "mode": mode,
                    "control": "original_token_eviction_recovery",
                    "status": "worker_failure",
                    "error_type": type(exc).__name__,
                }
            )
        for boundary in ("before_commit", "after_ack"):
            try:
                controls.append(exit_control(root, destination, mode, boundary))
            except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                controls.append(
                    {
                        "mode": mode,
                        "control": boundary,
                        "status": "worker_failure",
                        "error_type": type(exc).__name__,
                    }
                )
    return controls


def verify(root: Path, directory: Path, phase: str) -> dict[str, Any]:
    from aletheia_lab.evaluation.calibration_audit_verification import check_census

    plan, report = read(directory / "plan.json"), read(directory / phase / "results.json")
    if plan["code_sha256"] != bindings(root) or report["plan_sha256"] != digest(plan):
        raise ValueError("study binding differs")
    check_census(plan, report, directory / phase)
    if report["database_sha256"] != database_bindings(directory / phase):
        raise ValueError("retained durable database differs")
    rebuilt = summarize(report["transfers"], report["costs"], report["controls"])
    if rebuilt != report["analysis"]:
        raise ValueError("analysis differs from retained census")
    return {
        "status": "calibration_audit_read_only_replay_pass",
        "results_sha256": digest(report),
        "analysis": rebuilt,
        "provider_calls": 0,
    }


def database_bindings(directory: Path) -> dict[str, str]:
    result = {}
    for path in sorted(directory.rglob("*.sqlite")):
        if path.is_symlink():
            raise ValueError("owned study database must not be a symlink")
        result[str(path.relative_to(directory))] = file_sha256(path)
    return result


def exit_control(root: Path, directory: Path, mode: str, boundary: str) -> dict[str, Any]:
    owned = directory / f"exit-{mode}-{boundary}"
    child = _child(
        root, ["crash-worker", "--mode", mode, "--boundary", boundary, "--directory", str(owned)]
    )
    packet = read(owned / "packet.json")
    reopened = archive(mode, owned / "primary.sqlite", 131072, reopen=True)
    observed = reopened.evidence("crash-call")
    witness = reopened.witness(["crash-call"])
    reopened.close()
    ok = child.returncode == 19 and (
        observed == packet if boundary == "after_ack" else observed is None
    )
    return {
        "mode": mode,
        "control": boundary,
        "status": "pass" if ok else "fail",
        "returncode": child.returncode,
        "ack_observed": (owned / "ack.json").exists(),
        "expected_packet": packet,
        "reopened_witness": witness,
    }
