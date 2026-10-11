"""Exposed-development retention screen on fresh real serial serving evidence.

Two authored audit schedules are stress inputs, not observed customer demand.
Native capture runs once per fanout; each policy replays the same arrivals through
its own durable archive, without access to hidden truth or later audit targets.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from aletheia_lab.evaluation.audit_bundle_archive import AuditArchive, Bundle
from aletheia_lab.evaluation.audit_bundle_policy import exact_snapshot
from aletheia_lab.evaluation.model_load_application_analysis import environment
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.model_load_serving_analysis import validate_worker
from aletheia_lab.evaluation.model_load_serving_study import (
    WORKER,
    child_environment,
    write_new,
)
from aletheia_lab.evaluation.model_load_serving_workload import prepare_models
from aletheia_lab.project.identity import content_sha256

POLICIES = ("static", "ttl", "lru", "lfu", "size_cost", "union_density", "union_exchange")
BUDGETS = (2048, 4096, 8192, 16384)
SCHEDULES = ("last_child", "first_child")
CODE = (
    "src/aletheia_lab/evaluation/audit_bundle_archive.py",
    "src/aletheia_lab/evaluation/audit_bundle_policy.py",
    "src/aletheia_lab/evaluation/audit_bundle_screen.py",
    "src/aletheia_lab/evaluation/audit_bundle_verify.py",
    "src/aletheia_lab/evaluation/model_load_serving_workload.py",
    "src/aletheia_lab/evaluation/model_load_serving_capture.py",
    "src/aletheia_lab/evaluation/model_load_serving_store.py",
    "src/aletheia_lab/evaluation/model_load_serving_analysis.py",
    "src/aletheia_lab/evaluation/model_load_application.py",
    "src/aletheia_lab/evaluation/model_load_contract.py",
    "src/aletheia_lab/evaluation/model_load_retention.py",
    "scripts/audit_bundle_screen.py",
)


def _hash(value: object) -> str:
    return content_sha256(encode(value).encode())


def design(root: Path) -> dict[str, Any]:
    return {
        "schema": "audit-bundle-development-plan/v2",
        "revision": "event_clock_and_read_only_safety_checks",
        "development_only": True,
        "environment": environment(),
        "policies": list(POLICIES),
        "budgets": list(BUDGETS),
        "schedules": list(SCHEDULES),
        "fanouts": [1, 6],
        "load_slots": 12,
        "model_depth": 6,
        "hard_lease_age": 2,
        "optional_audit_ages": [0, 2, 8],
        "code_sha256": {name: content_sha256((root / name).read_bytes()) for name in CODE},
        "source_cluster_count": 1,
        "provider_calls": 0,
        "concurrent_capture": False,
        "budget_unit": "canonical_persisted_archive_blob_bytes_including_metadata",
        "inflight_reservation_implemented": False,
        "native_contract": False,
        "future_information_visible_to_policy": False,
        "internal_safety_checks_update_popularity": False,
        "evidence_refetch_permitted": False,
        "reference": "post-request reconstructed object and inference sink; shared trusted hook",
    }


def _capture(root: Path, directory: Path, index: int, fanout: int) -> dict[str, Any]:
    config = {"repeat": 0, "depth": 6, "inferences": fanout, "arm": "hash", "horizon": 0}
    command = [
        sys.executable,
        "-c",
        WORKER,
        encode(config),
        str(directory / f"native-{index}"),
        str(directory / "artifacts"),
        str(directory / "models.json"),
    ]
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
        return {"status": "timeout", "config": config, "rows": []}
    if completed.returncode != 0:
        return {"status": "native_failure", "config": config, "rows": []}
    try:
        value = json.loads(completed.stdout.splitlines()[-1])
        models = json.loads((directory / "models.json").read_text(encoding="utf-8"))
        validate_worker(value, models)
    except (ValueError, KeyError, TypeError, IndexError):
        return {"status": "invalid_native_result", "config": config, "rows": []}
    return {**value, "status": "complete"}


def offers(rows: list[dict[str, Any]], schedule: str) -> list[dict[str, Any]]:
    """All operations immediate; load and first/last child additionally at ages2/8."""
    if schedule not in SCHEDULES:
        raise ValueError("unknown declared schedule")
    targets = []
    for step in range(12):
        batch = [row for row in rows if row["step"] == step]
        children = [row for row in batch if row["kind"] == "infer"]
        chosen = children[-1] if schedule == "last_child" else children[0]
        targets.extend([next(row for row in batch if row["kind"] == "load"), chosen])
    result = [{"scope": row["scope"], "due": row["step"], "age": 0} for row in rows]
    result.extend(
        {"scope": row["scope"], "due": row["step"] + age, "age": age}
        for row in targets
        for age in (2, 8)
    )
    return sorted(result, key=lambda item: (item["due"], item["scope"], item["age"]))


def replay(
    path: Path,
    rows: list[dict[str, Any]],
    policy: str,
    budget: int,
    schedule: str,
) -> dict[str, Any]:
    """Evaluator owns the tape/reference; the archive receives only current evidence."""
    offered = offers(rows, schedule)
    truth = {row["scope"]: row["truth"] for row in rows}
    snapshots: list[dict[str, Any]] = []

    def inspect(
        pool: Any,
        mandatory: Any,
        cost: Any,
        cap: int,
        now: int,
        sequence: int,
        selected: Any,
        basis: Any,
    ) -> None:
        # Fixed early points keep independent exact checks cheap, outside policy timings.
        if sequence in (2, 5, 8):
            optimum = exact_snapshot(pool, mandatory, cost, cap, max_optional=10)
            snapshots.append(
                {
                    "sequence": sequence,
                    "now": now,
                    "optional_count": len(pool) - len(mandatory),
                    "pool": pool,
                    "mandatory": sorted(mandatory),
                    "selected": sorted(selected),
                    "selected_utility": sum(1 + pool[key]["hits"] for key in selected),
                    "selected_cost": cost(selected),
                    "cost_basis": basis,
                    "oracle": None
                    if optimum is None
                    else {**optimum, "selected": sorted(optimum["selected"])},
                }
            )

    archive = AuditArchive(path, policy, budget, observer=inspect)
    queries: list[dict[str, Any]] = []
    leases: list[dict[str, Any]] = []
    sequence = 0
    failure = None
    failure_at = None
    started = perf_counter_ns()
    try:
        for now in range(20):
            archive.advance(now)
            for row in [item for item in rows if item["step"] == now]:
                frame = row["frame"]
                parent = (
                    archive.retained_frame(frame["generation"]) if row["kind"] == "infer" else None
                )
                hard = row["kind"] == "load" or row["scope"].endswith("infer-0")
                accepted = archive.offer(
                    Bundle.build(frame, parent),
                    sequence=sequence,
                    now=now,
                    resident=frame["generation"],
                    lease_until=now + 2 if hard else None,
                )
                sequence += 1
                if hard:
                    leases.append({"scope": row["scope"], "due": now + 2, "accepted": accepted})
            for query in [item for item in offered if item["due"] == now]:
                observed = archive.query(query["scope"])
                correct = observed == truth[query["scope"]]
                queries.append(
                    {
                        **query,
                        "decision": observed,
                        "correct": correct,
                        "false": observed is not None and not correct,
                    }
                )
            for lease in [item for item in leases if item["due"] == now and item["accepted"]]:
                lease["decision"] = archive.query(lease["scope"], record_access=False)
                lease["fulfilled"] = lease["decision"] == truth[lease["scope"]]
        # An explicit late extension after possible loss; no source bytes supplied.
        before = len(archive.entries)
        extended = archive.lease(rows[0]["scope"], 21)
        extension = {
            "accepted": extended,
            "retained_before": before,
            "retained_after": len(archive.entries),
        }
    except (ValueError, OSError, sqlite3.Error) as exc:
        failure = type(exc).__name__
        failure_at = {
            "sequence": sequence,
            "now": archive.now,
            "reason_code": "mandatory_capacity_exceeded"
            if str(exc).startswith("active/accepted obligation cannot fit")
            else "service_error",
        }
        extension = {"accepted": False, "not_attempted_after_failure": True}
    elapsed = perf_counter_ns() - started
    metrics = archive.metrics()
    archive.close()
    return {
        "policy": policy,
        "budget": budget,
        "schedule": schedule,
        "status": "complete" if failure is None else "service_failure",
        "error_type": failure,
        "failure_at": failure_at,
        "native_operation_count": len(rows),
        "offered_queries": len(offered),
        "processed_queries": len(queries),
        "correct": sum(query["correct"] for query in queries),
        "false": sum(query["false"] for query in queries),
        "unknown_or_unprocessed": len(offered)
        - sum(query["correct"] for query in queries)
        - sum(query["false"] for query in queries),
        "hard_lease_offers": 24,
        "hard_accepted": sum(item["accepted"] is True for item in leases),
        "hard_refused_or_unprocessed": 24 - sum(item["accepted"] is True for item in leases),
        "hard_failures": sum(
            item["accepted"] is True and not item.get("fulfilled", False) for item in leases
        ),
        "queries": queries,
        "leases": leases,
        "extension": extension,
        "oracle_snapshots": snapshots,
        "metrics": metrics,
        "replay_wall_ns": elapsed,
    }


def analyze(results: list[dict[str, Any]]) -> dict[str, Any]:
    table = []
    for policy in POLICIES:
        rows = [item for item in results if item["policy"] == policy]
        table.append(
            {
                "policy": policy,
                **{
                    key: sum(item[key] for item in rows)
                    for key in (
                        "offered_queries",
                        "correct",
                        "false",
                        "unknown_or_unprocessed",
                        "hard_lease_offers",
                        "hard_accepted",
                        "hard_refused_or_unprocessed",
                        "hard_failures",
                    )
                },
                "service_failures": sum(item["status"] != "complete" for item in rows),
            }
        )
    matched: list[dict[str, Any]] = []
    for fanout in (1, 6):
        for budget in BUDGETS:
            for schedule in SCHEDULES:
                group = [
                    item
                    for item in results
                    if item["fanout"] == fanout
                    and item["budget"] == budget
                    and item["schedule"] == schedule
                ]
                safe = [
                    item
                    for item in group
                    if item["status"] == "complete" and item["false"] == item["hard_failures"] == 0
                ]
                ordinary = [item for item in safe if not item["policy"].startswith("union_")]
                candidate = next(
                    (item for item in safe if item["policy"] == "union_exchange"), None
                )
                best = max((item["correct"] for item in ordinary), default=None)
                matched.append(
                    {
                        "fanout": fanout,
                        "budget": budget,
                        "schedule": schedule,
                        "best_ordinary_correct": best,
                        "candidate_correct": candidate["correct"] if candidate else None,
                        "candidate_delta": candidate["correct"] - best
                        if candidate is not None and best is not None
                        else None,
                    }
                )
    deltas = Counter(
        "unavailable"
        if item["candidate_delta"] is None
        else "win"
        if item["candidate_delta"] > 0
        else "loss"
        if item["candidate_delta"] < 0
        else "tie"
        for item in matched
    )
    return {
        "policy_totals": table,
        "matched_frontier": matched,
        "candidate_comparisons": dict(deltas),
        "disposition": "development_gap_candidate_requires_fresh_validation"
        if deltas["win"] and not deltas["unavailable"]
        else "narrow_no_demonstrated_general_method_advantage",
        "limitations": "one trusted serial source; authored audit schedules; development, "
        "not natural demand, competitive proof, physical cap or latency superiority",
    }


def _reuse(directory: Path) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    original = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    identity = original.pop("plan_sha256")
    if _hash(original) != identity or original.get("development_only") is not True:
        raise ValueError("source development plan changed")
    native = [
        json.loads((directory / f"native-{index}.json").read_text(encoding="utf-8"))
        for index in range(2)
    ]
    models = json.loads((directory / "models.json").read_text(encoding="utf-8"))
    for capture, fanout in zip(native, (1, 6), strict=True):
        if capture["status"] != "complete" or capture["config"] != {
            "repeat": 0,
            "depth": 6,
            "inferences": fanout,
            "arm": "hash",
            "horizon": 0,
        }:
            raise ValueError("incompatible source capture")
        validate_worker(capture, models)
    return native, models, identity


def run(root: Path, directory: Path, capture_dir: Path | None = None) -> dict[str, Any]:
    if directory.exists() or directory.is_symlink() or directory.is_relative_to(root):
        raise ValueError("fresh private directory outside public repository required")
    plan = design(root)
    reused = _reuse(capture_dir) if capture_dir else None
    if reused is not None:
        plan["capture_replay"] = {
            "original_plan_sha256": reused[2],
            "native_sha256": [_hash(item) for item in reused[0]],
            "models_sha256": _hash(reused[1]),
        }
    directory.mkdir(parents=True, exist_ok=False)
    write_new(directory / "plan.json", {**plan, "plan_sha256": _hash(plan)})
    models = reused[1] if reused else prepare_models(directory / "artifacts")
    write_new(directory / "models.json", models)
    native = []
    for index, fanout in enumerate((1, 6)):
        captured = reused[0][index] if reused else _capture(root, directory, index, fanout)
        write_new(directory / f"native-{index}.json", captured)
        native.append(captured)
    results = []
    for captured in native:
        fanout = captured["config"]["inferences"]
        if captured["status"] != "complete":
            raise ValueError("native failure preserved; no policy result synthesized")
        for budget in BUDGETS:
            for schedule in SCHEDULES:
                for policy in POLICIES:
                    result = replay(
                        directory / f"{fanout}-{budget}-{schedule}-{policy}.sqlite",
                        captured["rows"],
                        policy,
                        budget,
                        schedule,
                    )
                    result["fanout"] = fanout
                    results.append(result)
                    print(
                        encode(
                            {
                                "status": "audit_bundle_development_progress",
                                "completed": len(results),
                                "maximum": 112,
                                "policy": policy,
                                "run_status": result["status"],
                            }
                        ),
                        flush=True,
                    )
    analysis = analyze(results)
    payload = {
        "schema": "audit-bundle-development-results/v2",
        "plan_sha256": _hash(plan),
        "native_sha256": [_hash(item) for item in native],
        "models_sha256": _hash(models),
        "results": results,
        "analysis": analysis,
    }
    write_new(directory / "results.json", {**payload, "results_sha256": _hash(payload)})
    return {
        "status": "development_screen_complete",
        "provider_calls": 0,
        "native_operations": sum(len(item["rows"]) for item in native),
        "native_capture_reused": reused is not None,
        "native_calls_executed": 0 if reused else sum(len(item["rows"]) for item in native),
        "policy_runs": len(results),
        "analysis": analysis,
    }


def verify(root: Path, directory: Path) -> dict[str, Any]:
    """Independent record/reference checks without native rerun or archive writes."""
    from aletheia_lab.evaluation.audit_bundle_verify import validate_records

    plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    plan_hash = plan.pop("plan_sha256")
    base_plan = {key: value for key, value in plan.items() if key != "capture_replay"}
    if _hash(plan) != plan_hash or base_plan != design(root):
        raise ValueError("development design/code binding changed")
    payload = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    result_hash = payload.pop("results_sha256")
    if _hash(payload) != result_hash or payload["plan_sha256"] != plan_hash:
        raise ValueError("development result identity changed")
    native = [
        json.loads((directory / f"native-{index}.json").read_text(encoding="utf-8"))
        for index in range(2)
    ]
    models = json.loads((directory / "models.json").read_text(encoding="utf-8"))
    if payload["native_sha256"] != [_hash(item) for item in native] or payload[
        "models_sha256"
    ] != _hash(models):
        raise ValueError("source capture/model identity changed")
    if "capture_replay" in plan and (
        plan["capture_replay"]["native_sha256"] != payload["native_sha256"]
        or plan["capture_replay"]["models_sha256"] != payload["models_sha256"]
    ):
        raise ValueError("reused capture differs from the declared revision")
    validate_records(native, models, payload["results"])
    if payload["analysis"] != analyze(payload["results"]):
        raise ValueError("aggregate replay differs")
    return {
        "status": "development_reference_and_aggregate_replay_pass",
        "results_sha256": result_hash,
        "provider_calls": 0,
        "native_calls_executed": 0,
        "analysis": payload["analysis"],
    }
