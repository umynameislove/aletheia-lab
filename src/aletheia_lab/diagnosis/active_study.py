"""Measured local closed-loop study with evaluator truth outside policy inputs."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from typing import Any

import numpy as np

from aletheia_lab.diagnosis.active_native import CONDITIONS, PROBES, NativeCase, create_case
from aletheia_lab.diagnosis.active_policy import declared_catalogue, measured_probe, run_policy

POLICIES = ("static", "full", "cheap", "ig", "ec2")
DEVELOPMENT_SEEDS = (42001, 42002)
TRANSFER_SEEDS = (43001, 43002, 43003, 43004, 43005)
CAPABILITIES = {"all": PROBES, "restoration_only": PROBES[3:]}


def study_design() -> dict[str, Any]:
    return {
        "version": "actual-active-diagnosis/1",
        "development": {
            "source": "iris",
            "seeds": list(DEVELOPMENT_SEEDS),
            "conditions": list(CONDITIONS[:5]),
        },
        "transfer": {
            "source": "breast_cancer",
            "seeds": list(TRANSFER_SEEDS),
            "conditions": list(CONDITIONS),
        },
        "policies": list(POLICIES),
        "capabilities": {k: list(v) for k, v in CAPABILITIES.items()},
        "envelopes": ["tight", "relaxed"],
        "relaxed_seconds": 1.0,
        "tight_formula": "max(0.001,sum(2*p95_development_read_costs))",
        "calibration": "2 Iris seeds x 5 declared cases x 6 actual operations;2*p95 per action",
        "max_total_probe_calls": 6,
        "harm_delta": 0.01,
        "restoration_tolerance": 1e-10,
        "full_policy": "all available read probes; if none available, all restoration probes; then chosen repair",
        "repair": "one diagnosed-site replacement or common functional recovery, original snapshot branch",
        "catalogue": [
            {"name": h.name, "site": h.site, "harmful": h.harmful, "outcomes": dict(h.outcomes)}
            for h in declared_catalogue()
        ],
        "scope": "descriptive_controlled_new_method_transfer_not_natural_incidents_or_global_cause_uniqueness",
        "source_exposure": "Iris exposed; breast_cancer public source docs; excluded operators engineering-exposed on Iris",
        "unknown_operator_claim": "outside_declared_catalogue_not_unseen_pretraining",
        "failures": "retain every planned opportunity and every attempt; no automatic retries",
    }


def calibrate_costs() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for seed in DEVELOPMENT_SEEDS:
        for condition in CONDITIONS[:5]:
            case = create_case("iris", seed, condition)
            for action in PROBES:
                raw: list[dict[str, Any]] = []
                event = case.probe(action, recorder=raw.append)
                rows.append(
                    {
                        "seed": seed,
                        "condition": condition,
                        "action": action,
                        "receipt": event,
                        "raw_evidence": raw[0],
                    }
                )
    costs = {
        a: max(
            1e-9,
            2
            * float(np.percentile([r["receipt"]["seconds"] for r in rows if r["action"] == a], 95)),
        )
        for a in PROBES
    }
    return {
        "rows": rows,
        "costs": costs,
        "budgets": {"tight": max(0.001, sum(costs[a] for a in PROBES[:3])), "relaxed": 1.0},
        "meaning": "warm measured reservation estimates;actual overruns retained;not operator SLO",
    }


def _recovery_action(result: dict[str, Any]) -> tuple[str | None, str]:
    if not result["compatible"]:
        return None, "no_supported_recovery"
    hypotheses = [h for h in declared_catalogue() if h.name in result["compatible"]]
    sites = {h.site for h in hypotheses}
    actions = {"input": "restore_input", "resident": "restore_model", "training": "refit"}
    if len(sites) == 1:
        return actions.get(next(iter(sites))), "diagnosis_selected"
    common = [a for a in PROBES[3:] if all(h.outcomes[a] == "restored" for h in hypotheses)]
    return (common[0], "common_functional_recovery") if common else (None, "unresolved")


def close_loop(
    case: NativeCase,
    *,
    policy: str,
    available: tuple[str, ...],
    costs: dict[str, float],
    budget_seconds: float,
    selector: Callable[[dict[str, Any]], list[str]] | None = None,
) -> dict[str, Any]:
    """Actual selected repair after acquisition; proof is recorded, not inferred."""
    episode_started = time.perf_counter()
    raw: list[dict[str, Any]] = []
    initial = case.initial()

    def acquire(action: str) -> dict[str, Any]:
        return case.probe(action, recorder=raw.append)

    # A sufficient full baseline does not waste time on redundant repair tests
    # when all cheap state reads are available. Rights remain identical.
    diagnostic_actions = available
    if policy == "full" and any(a.startswith("read_") for a in available):
        diagnostic_actions = tuple(a for a in available if a.startswith("read_"))
    result = run_policy(
        probe=acquire,
        harmful=initial["harmful"],
        available=diagnostic_actions,
        costs=costs,
        budget_seconds=budget_seconds,
        policy=policy,
        selector=selector,
    )
    action, basis = _recovery_action(result)
    repair: dict[str, Any] = {"action": action, "basis": basis, "status": "not_attempted"}
    remaining = budget_seconds - (time.perf_counter() - episode_started)
    if (
        initial["harmful"]
        and action in available
        and len(result["ledger"]) < 6
        and action is not None
        and costs[action] <= remaining
    ):
        event = measured_probe(acquire, action)
        repair = {"action": action, "basis": basis, **event}
        result["total_seconds"] += event["attempt_seconds"]
        result["probe_seconds"] += event["attempt_seconds"]
        if event["status"] == "observed":
            repair["functional_restored"] = raw[-1]["functional_restored"]
            repair["state_restored"] = raw[-1]["state_restored"]
    result["repair"] = repair
    result["total_seconds"] = time.perf_counter() - episode_started
    result["on_budget"] = result["total_seconds"] <= budget_seconds
    return {"initial": initial, "result": result, "raw_probe_evidence": raw}


def _grade(reference: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    known = reference["condition"] in {h.name for h in declared_catalogue()}
    identified = result["identified"]
    exact = identified and result["compatible"] == [reference["condition"]]
    # This tests an explicit finite-classification shortcut, not an unqualified
    # claim emitted by the guarded runtime. Do not call it observed LLM belief.
    specificity_failure = identified and not exact
    repair = result["repair"]
    return {
        "in_catalogue": known,
        "exact_mechanism_match": exact,
        "catalogue_specificity_failure": specificity_failure,
        "supported_catalogue_identification": known and exact and result["on_budget"],
        "functional_recovery": repair.get("functional_restored", False),
        "intended_state_recovery": repair.get("state_restored", False),
        "fulfilled_recovery": repair.get("state_restored", False) and result["on_budget"],
    }


def run_study(phase: str, calibration: dict[str, Any]) -> dict[str, Any]:
    if phase not in {"development", "transfer"}:
        raise ValueError("unknown study phase")
    frame = study_design()[phase]
    rows: list[dict[str, Any]] = []
    worlds: list[dict[str, Any]] = []
    for seed in frame["seeds"]:
        for condition in frame["conditions"]:
            try:
                setup_started = time.perf_counter()
                case = create_case(frame["source"], seed, condition)
                reference = case.reference()
                setup_seconds = time.perf_counter() - setup_started
                worlds.append(
                    {
                        "reference": reference,
                        "setup_seconds": setup_seconds,
                        "compensation": case.compensation_control(),
                    }
                )
            except (ValueError, RuntimeError, OSError) as error:
                rows.extend(_failed_world(frame["source"], seed, condition, type(error).__name__))
                continue
            for capability, available in CAPABILITIES.items():
                for envelope, budget in calibration["budgets"].items():
                    # Rotate order prospectively; measurements are paired on the
                    # same immutable faulted snapshot, not fresh random incidents.
                    shift = seed % len(POLICIES)
                    order = POLICIES[shift:] + POLICIES[:shift]
                    for policy in order:
                        started = time.perf_counter()
                        execution = close_loop(
                            case,
                            policy=policy,
                            available=available,
                            costs=calibration["costs"],
                            budget_seconds=budget,
                        )
                        rows.append(
                            {
                                "source": frame["source"],
                                "seed": seed,
                                "condition": condition,
                                "capability": capability,
                                "envelope": envelope,
                                "policy": policy,
                                "status": "executed",
                                **execution,
                                "grade": _grade(reference, execution["result"]),
                                "episode_seconds": time.perf_counter() - started,
                            }
                        )
    planned = (
        len(frame["seeds"])
        * len(frame["conditions"])
        * len(CAPABILITIES)
        * len(calibration["budgets"])
        * len(POLICIES)
    )
    if len(rows) != planned:
        raise ValueError("study census is incomplete")
    return {
        "phase": phase,
        "planned": planned,
        "rows": rows,
        "worlds": worlds,
        "summary": summarize(rows),
        "independence": "one dataset;overlapping splits;paired policies",
    }


def _failed_world(source: str, seed: int, condition: str, error: str) -> list[dict[str, Any]]:
    return [
        {
            "source": source,
            "seed": seed,
            "condition": condition,
            "capability": capability,
            "envelope": envelope,
            "policy": policy,
            "status": "qualification_failure",
            "error_type": error,
        }
        for capability in CAPABILITIES
        for envelope in ("tight", "relaxed")
        for policy in POLICIES
    ]


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    groups = sorted({(r["capability"], r["envelope"], r["policy"]) for r in rows})
    for capability, envelope, policy in groups:
        selected = [
            r
            for r in rows
            if (r["capability"], r["envelope"], r["policy"]) == (capability, envelope, policy)
        ]
        valid = [r for r in selected if r["status"] == "executed"]
        timings = [r["result"]["total_seconds"] for r in valid]
        tables.append(
            {
                "capability": capability,
                "envelope": envelope,
                "policy": policy,
                "planned": len(selected),
                "executed": len(valid),
                "qualification_failure": len(selected) - len(valid),
                "identified": sum(r["result"]["identified"] for r in valid),
                "on_budget": sum(r["result"]["on_budget"] for r in valid),
                "exact_mechanism_match": sum(r["grade"]["exact_mechanism_match"] for r in valid),
                "catalogue_specificity_failure": sum(
                    r["grade"]["catalogue_specificity_failure"] for r in valid
                ),
                "supported_catalogue_identification": sum(
                    r["grade"]["supported_catalogue_identification"] for r in valid
                ),
                "functional_recovery": sum(r["grade"]["functional_recovery"] for r in valid),
                "intended_state_recovery": sum(
                    r["grade"]["intended_state_recovery"] for r in valid
                ),
                "fulfilled_recovery": sum(r["grade"]["fulfilled_recovery"] for r in valid),
                "probe_calls": sum(
                    sum(
                        e.get("action") in PROBES and e["status"] in {"observed", "probe_failure"}
                        for e in r["result"]["ledger"]
                    )
                    + int(r["result"]["repair"]["status"] in {"observed", "probe_failure"})
                    for r in valid
                ),
                "stop_counts": dict(Counter(r["result"]["stop"] for r in valid)),
                "seconds_median": float(np.median(timings)) if timings else None,
                "seconds_range": [min(timings), max(timings)] if timings else None,
            }
        )
    return tables
