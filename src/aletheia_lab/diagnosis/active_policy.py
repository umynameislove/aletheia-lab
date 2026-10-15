"""Finite active-diagnosis comparators, not a replacement for frozen A3/P5.

The known hypothesis table is a declared model, not an oracle of current state.
An identified row is catalogue-relative; matching a row cannot prove that the
catalogue is complete. Real observations come only from a caller-owned probe.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Any


@dataclass(frozen=True)
class Hypothesis:
    name: str
    site: str
    harmful: bool
    outcomes: Mapping[str, str]


def declared_catalogue() -> tuple[Hypothesis, ...]:
    """Mechanistic expectations fixed before fresh outcomes, never case lookup."""
    specifications = (
        ("healthy", "none", False, ("same", "same", "same"), ("restored",) * 3),
        (
            "input_sign",
            "input",
            True,
            ("changed", "same", "same"),
            ("restored", "not_restored", "not_restored"),
        ),
        (
            "resident_sign",
            "resident",
            True,
            ("same", "changed", "same"),
            ("not_restored", "restored", "restored"),
        ),
        (
            "target_roll",
            "training",
            True,
            ("same", "changed", "changed"),
            ("not_restored", "restored", "restored"),
        ),
        (
            "double_sign",
            "input+resident",
            False,
            ("changed", "changed", "same"),
            ("not_restored", "not_restored", "not_restored"),
        ),
    )
    actions = (
        "read_input",
        "read_model",
        "read_training",
        "restore_input",
        "restore_model",
        "refit",
    )
    return tuple(
        Hypothesis(name, site, harmful, dict(zip(actions, (*reads, *repairs), strict=True)))
        for name, site, harmful, reads, repairs in specifications
    )


def compatible(
    hypotheses: Sequence[Hypothesis], observations: Mapping[str, str]
) -> tuple[Hypothesis, ...]:
    return tuple(
        h for h in hypotheses if all(h.outcomes.get(a) == v for a, v in observations.items())
    )


def _entropy(hypotheses: Sequence[Hypothesis]) -> float:
    if not hypotheses:
        return 0.0
    masses: dict[str, int] = {}
    for h in hypotheses:
        masses[h.site] = masses.get(h.site, 0) + 1
    return -sum((n / len(hypotheses)) * math.log2(n / len(hypotheses)) for n in masses.values())


def discriminating_gain(hypotheses: Sequence[Hypothesis], action: str, *, rule: str) -> float:
    """Uniform-prior IG or exact expected edge cut on a tiny deterministic model.

    This implementation does not extend EC²'s guarantee to unavailable probes,
    uncertain costs, unknown hypotheses or arbitrary learned test laws.
    """
    if rule not in {"ig", "ec2"}:
        raise ValueError("unknown selection objective")
    if len(hypotheses) < 2:
        return 0.0
    groups: dict[str, list[Hypothesis]] = {}
    for h in hypotheses:
        groups.setdefault(h.outcomes[action], []).append(h)
    if rule == "ig":
        return _entropy(hypotheses) - sum(
            len(g) / len(hypotheses) * _entropy(g) for g in groups.values()
        )
    denominator = len(hypotheses) ** 2
    edge_weight = sum(a.site != b.site for a, b in combinations(hypotheses, 2)) / denominator
    remaining = sum(
        len(g)
        / len(hypotheses)
        * sum(a.site != b.site for a, b in combinations(g, 2))
        / denominator
        for g in groups.values()
    )
    return edge_weight - remaining


def choose_probe(
    hypotheses: Sequence[Hypothesis],
    actions: Sequence[str],
    costs: Mapping[str, float],
    *,
    rule: str,
) -> str | None:
    if not actions:
        return None
    if any(a not in costs or not math.isfinite(costs[a]) or costs[a] <= 0 for a in actions):
        raise ValueError("costs must be measured, finite and positive")
    if rule in {"full", "cheap"}:
        return min(actions, key=lambda a: (costs[a], a))
    gains = {a: discriminating_gain(hypotheses, a, rule=rule) for a in actions}
    positive = [a for a in actions if gains[a] > 1e-15]
    return min(positive, key=lambda a: (-gains[a] / costs[a], costs[a], a)) if positive else None


def policy_payload(
    hypotheses: Sequence[Hypothesis],
    observations: Mapping[str, str],
    available: Sequence[str],
    remaining_seconds: float,
    *,
    costs: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Explicit allowlist at the model boundary: no fixture, reference or path."""
    return {
        "catalogue_scope": "declared_finite_model_not_exhaustive_world",
        "hypotheses": [
            {"name": h.name, "site": h.site, "expected": dict(h.outcomes)} for h in hypotheses
        ],
        "observations": dict(observations),
        "available_actions": list(available),
        "cost_estimates_seconds": {a: costs[a] for a in available} if costs else {},
        "remaining_seconds": remaining_seconds,
    }


def _select_action(
    policy: str,
    hypotheses: Sequence[Hypothesis],
    observations: Mapping[str, str],
    eligible: list[str],
    costs: Mapping[str, float],
    remaining: float,
    selector: Callable[[dict[str, Any]], list[str]] | None,
) -> tuple[str | None, str]:
    if policy not in {"llm", "hybrid"}:
        return choose_probe(hypotheses, eligible, costs, rule=policy), "no_discriminating_probe"
    if selector is None:
        raise ValueError("model selector must be an actual separately qualified adapter")
    try:
        proposals = selector(
            policy_payload(hypotheses, observations, eligible, remaining, costs=costs)
        )
    except (ValueError, RuntimeError, OSError, TimeoutError):
        return None, "selector_failure"
    if not proposals or proposals == ["stop"]:
        return None, "model_stop"
    if (
        not isinstance(proposals, list)
        or len(proposals) > 3
        or any(not isinstance(a, str) or a not in eligible for a in proposals)
    ):
        return None, "invalid_model_action"
    if len(set(proposals)) != len(proposals):
        return None, "invalid_model_action"
    action = (
        choose_probe(hypotheses, proposals, costs, rule="ec2")
        if policy == "hybrid"
        else proposals[0]
    )
    return action, "no_discriminating_probe"


def measured_probe(probe: Callable[[str], dict[str, Any]], action: str) -> dict[str, Any]:
    """Retain failed work and validate the complete projected observation."""
    started = time.perf_counter()
    try:
        event = probe(action)
        if set(event) != {"value", "seconds", "evidence_sha256"}:
            raise ValueError("probe projection contains unexpected fields")
        if event["value"] not in {"same", "changed", "restored", "not_restored"}:
            raise ValueError("invalid measurement")
        if (
            type(event["seconds"]) not in {int, float}
            or not math.isfinite(event["seconds"])
            or event["seconds"] < 0
        ):
            raise ValueError("invalid timer")
        identity = event["evidence_sha256"]
        if (
            not isinstance(identity, str)
            or len(identity) != 64
            or any(c not in "0123456789abcdef" for c in identity)
        ):
            raise ValueError("invalid evidence identity")
        return {
            "action": action,
            "status": "observed",
            **event,
            "attempt_seconds": time.perf_counter() - started,
        }
    except (ValueError, RuntimeError, OSError, TimeoutError) as error:
        return {
            "action": action,
            "status": "probe_failure",
            "error_type": type(error).__name__,
            "attempt_seconds": time.perf_counter() - started,
        }


def _validate_run(
    harmful: bool,
    available: Sequence[str],
    costs: Mapping[str, float],
    budget: float,
    policy: str,
    max_calls: int,
) -> None:
    if policy not in {"static", "full", "cheap", "ig", "ec2", "llm", "hybrid"}:
        raise ValueError("unknown policy")
    if type(harmful) is not bool or not math.isfinite(budget) or budget < 0:
        raise ValueError("invalid initial status or budget")
    if type(max_calls) is not int or not 0 <= max_calls <= 6:
        raise ValueError("invalid call cap")
    catalogue = declared_catalogue()
    if len(set(available)) != len(available) or any(
        a not in catalogue[0].outcomes for a in available
    ):
        raise ValueError("invalid capabilities")
    if any(a not in costs or not math.isfinite(costs[a]) or costs[a] <= 0 for a in available):
        raise ValueError("invalid cost envelope")


def run_policy(
    *,
    probe: Callable[[str], dict[str, Any]],
    harmful: bool,
    available: Sequence[str],
    costs: Mapping[str, float],
    budget_seconds: float,
    policy: str,
    selector: Callable[[dict[str, Any]], list[str]] | None = None,
    max_calls: int = 6,
) -> dict[str, Any]:
    """Same actual executor and budget for full/cheap/IG/EC²/model selectors.

    Reservations use development cost estimates; realized wall-clock, including
    selection, can exceed them. Such runs are late, not successful on-budget.
    Failed attempts are retained; no retries, compensation or hidden shortcuts.
    """
    _validate_run(harmful, available, costs, budget_seconds, policy, max_calls)
    catalogue = declared_catalogue()
    hypotheses = tuple(h for h in catalogue if h.harmful == harmful)
    observations: dict[str, str] = {}
    ledger: list[dict[str, Any]] = []
    start = time.perf_counter()
    selection_seconds = 0.0
    stop = "static" if policy == "static" else "call_cap"
    for _ in range(0 if policy == "static" else max_calls):
        if not hypotheses:
            stop = "outside_declared_model"
            break
        if policy != "full" and len({h.site for h in hypotheses}) == 1:
            stop = "catalogue_identified"
            break
        remaining = budget_seconds - (time.perf_counter() - start)
        eligible = [a for a in available if a not in observations and costs[a] <= remaining]
        if not eligible:
            stop = "budget_or_capability_exhausted"
            break
        selected_at = time.perf_counter()
        action, reason = _select_action(
            policy, hypotheses, observations, eligible, costs, remaining, selector
        )
        selection_seconds += time.perf_counter() - selected_at
        if action is None:
            stop = reason
            if reason in {"selector_failure", "invalid_model_action"}:
                ledger.append({"action": None, "status": reason})
            break
        if action not in eligible:
            stop = "invalid_model_action"
            ledger.append({"action": action, "status": "invalid_model_action"})
            break
        if time.perf_counter() - start + costs[action] > budget_seconds:
            stop = "selection_exhausted_budget"
            break
        event = measured_probe(probe, action)
        ledger.append(event)
        if event["status"] != "observed":
            stop = "probe_failure"
            break
        observations[action] = event["value"]
        hypotheses = compatible(hypotheses, observations)
    elapsed = time.perf_counter() - start
    sites = sorted({h.site for h in hypotheses})
    terminal_ok = stop not in {
        "selector_failure",
        "probe_failure",
        "invalid_model_action",
        "outside_declared_model",
    }
    return {
        "policy": policy,
        "scope": "catalogue_relative_not_global_cause_uniqueness",
        "observations": observations,
        "compatible": [h.name for h in hypotheses],
        "sites": sites,
        "identified": terminal_ok and len(sites) == 1,
        "on_budget": elapsed <= budget_seconds,
        "stop": stop,
        "ledger": ledger,
        "total_seconds": elapsed,
        "selection_seconds": selection_seconds,
        "probe_seconds": sum(e.get("attempt_seconds", 0.0) for e in ledger),
        "budget_seconds": budget_seconds,
    }
