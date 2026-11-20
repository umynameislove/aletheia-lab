"""Fixed-cardinality operator alerts over measured windows, never a capacity verdict."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AlertPolicy:
    queue_age_seconds: float = 30.0
    pool_waiting: int = 1
    event_loop_lag_ms: float = 250.0
    error_budget_fraction: float = 0.01
    burn_multiple: float = 5.0
    minimum_http_requests: int = 20

    def __post_init__(self) -> None:
        if (
            any(
                isinstance(v, bool) or not math.isfinite(v) or v <= 0
                for v in (
                    self.queue_age_seconds,
                    self.event_loop_lag_ms,
                    self.error_budget_fraction,
                    self.burn_multiple,
                )
            )
            or self.error_budget_fraction >= 1
            or type(self.pool_waiting) is not int
            or self.pool_waiting < 1
            or type(self.minimum_http_requests) is not int
            or self.minimum_http_requests < 1
        ):
            raise ValueError("Finite positive alert thresholds required.")


_ACTIONS = {
    "queue_age": "Check worker readiness and oldest pending job; do not reset accepted deadlines.",
    "pool_wait": "Inspect pool/DB lock snapshot; reduce admission before raising connection limits.",
    "event_loop_lag": "Inspect bounded CPU workers and blocking stages; preserve health reserve.",
    "http_burn": "Join request IDs to action audit; inspect release and dependency readiness.",
    "worker_error": "Join job ID to durable audit/attempts before deciding a retry.",
    "log_drops": "Restore log collector throughput; counters remain complete but sampled logs are incomplete.",
}


def _totals(snapshot: dict[str, Any]) -> tuple[int, int, int]:
    requests = failures = worker_errors = 0
    for item in snapshot.get("items", []):
        count = item.get("count")
        if type(count) is not int or count < 0:
            raise ValueError("Nonnegative cumulative counters required.")
        if item.get("kind") == "http":
            requests += count
            if item.get("status") == "5xx" or item.get("outcome") == "error":
                failures += count
        elif item.get("kind") == "job" and item.get("outcome") == "error":
            worker_errors += count
    return requests, failures, worker_errors


class AlertEvaluator:
    """A process identity change or counter reset starts a new baseline window."""

    def __init__(self, policy: AlertPolicy | None = None) -> None:
        self.policy = policy if policy is not None else AlertPolicy()
        self._baseline: tuple[object, object, float, tuple[int, int, int], int] | None = None
        self._active: set[str] = set()

    def evaluate(
        self, snapshot: dict[str, Any], *, observed_at: float, log_dropped: int = 0
    ) -> dict[str, Any]:
        if (
            isinstance(observed_at, bool)
            or not math.isfinite(observed_at)
            or observed_at < 0
            or type(log_dropped) is not int
            or log_dropped < 0
        ):
            raise ValueError("Finite observation time and nonnegative drop counter required.")
        current = _totals(snapshot)
        identity = snapshot.get("process_id"), snapshot.get("release_id")
        previous = self._baseline
        self._baseline = *identity, observed_at, current, log_dropped
        window_valid = bool(
            previous
            and previous[:2] == identity
            and observed_at > previous[2]
            and all(now >= old for now, old in zip(current, previous[3], strict=True))
            and log_dropped >= previous[4]
        )
        delta = (
            tuple(now - old for now, old in zip(current, previous[3], strict=True))
            if window_valid and previous
            else (0, 0, 0)
        )
        seconds = observed_at - previous[2] if window_valid and previous else None
        gauges = snapshot.get("gauges", {})
        active = set()
        for code, name, threshold in (
            ("queue_age", "queue_oldest_seconds", self.policy.queue_age_seconds),
            ("pool_wait", "pool_waiting", self.policy.pool_waiting),
            ("event_loop_lag", "event_loop_lag_ms", self.policy.event_loop_lag_ms),
        ):
            value = gauges.get(name)
            if value is not None:
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value < 0
                ):
                    raise ValueError("Finite nonnegative measured gauges required.")
                if value >= threshold:
                    active.add(code)
        error_rate = delta[1] / delta[0] if delta[0] else None
        burn = error_rate / self.policy.error_budget_fraction if error_rate is not None else None
        if (
            burn is not None
            and delta[0] >= self.policy.minimum_http_requests
            and burn >= self.policy.burn_multiple
        ):
            active.add("http_burn")
        if delta[2]:
            active.add("worker_error")
        if (previous is None and log_dropped > 0) or (
            window_valid and previous and log_dropped > previous[4]
        ):
            active.add("log_drops")
        baseline_reset = previous is not None and not window_valid
        if baseline_reset:
            # A replacement process/counter reset is not proof an old incident recovered.
            self._active.clear()
        transitions = [
            {"code": code, "state": "firing" if code in active else "resolved"}
            for code in sorted(active ^ self._active)
        ]
        self._active = active
        return {
            "schema_version": "hosted-alerts/v1",
            "scope": "One observed process; policy thresholds are operational rules, not verified SLO/capacity.",
            "window": {
                "valid": window_valid,
                "baseline_reset": baseline_reset,
                "seconds": seconds,
                "http_requests": delta[0],
                "http_server_failures": delta[1],
                "error_rate": error_rate,
                "error_budget_burn": burn,
                "worker_errors": delta[2],
            },
            "active": [
                {"code": code, "owner": "operator", "runbook": _ACTIONS[code]}
                for code in sorted(active)
            ],
            "transitions": transitions,
        }
