"""Capacity outcome histograms and report projection; no scheduling or HTTP effects."""

from __future__ import annotations

import copy
import math
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import import_module
from typing import TYPE_CHECKING

from .capacity_models import Actor, Journey

if TYPE_CHECKING:
    from .capacity import CapacityHarness

_BOUNDS = tuple(0.001 * 1.2**n for n in range(74)) + (float("inf"),)


@dataclass
class Histogram:
    count: int = 0
    seconds_sum: float = 0.0
    buckets: list[int] = field(default_factory=lambda: [0] * len(_BOUNDS))

    def add(self, seconds: float) -> None:
        self.count += 1
        self.seconds_sum += seconds
        for index, bound in enumerate(_BOUNDS):
            if seconds <= bound:
                self.buckets[index] += 1
                break

    def report(self) -> dict[str, object]:
        def quantile(fraction: float) -> float | str | None:
            if not self.count:
                return None
            target, cumulative = math.ceil(self.count * fraction), 0
            for bound, n in zip(_BOUNDS, self.buckets, strict=True):
                cumulative += n
                if cumulative >= target:
                    return bound if math.isfinite(bound) else "+Inf"
            return "+Inf"

        return {
            "n": self.count,
            "seconds_sum": self.seconds_sum,
            "mean_seconds": self.seconds_sum / self.count if self.count else None,
            "p95_upper_seconds": quantile(0.95),
            "p99_upper_seconds": quantile(0.99),
            "p99_precision_sufficient": self.count >= 10000,
            "buckets_disjoint": {
                str(b) if math.isfinite(b) else "+Inf": n
                for b, n in zip(_BOUNDS, self.buckets, strict=True)
            },
        }


@dataclass
class Stratum:
    offered: int = 0
    outcomes: Counter[str] = field(default_factory=Counter)
    inclusive: Histogram = field(default_factory=Histogram)
    responses: Histogram = field(default_factory=Histogram)


@dataclass(frozen=True)
class Offered:
    actor: Actor
    journey: Journey
    due: float
    sequence: int
    measured: bool


def build_report(harness: CapacityHarness, pending: int) -> dict[str, object]:
    strata = []
    journeys = {j.name: j for j in harness.profile.journeys}
    for key, value in sorted(harness.strata.items()):
        unfinished = value.offered - sum(value.outcomes.values())
        inclusive = copy.deepcopy(value.inclusive)
        for _ in range(unfinished):
            inclusive.add(journeys[key[0]].deadline(key[1]))
        budget = journeys[key[0]].size_budgets.get(key[1])
        strata.append(
            {
                "operation": key[0],
                "size": key[1],
                "expectation": key[2],
                "offered": value.offered,
                "pending": unfinished,
                "outcomes": dict(value.outcomes),
                "deadline_inclusive": inclusive.report(),
                "completed_response": value.responses.report(),
                "slo_targets": budget.model_dump() if budget else None,
                "deadline_seconds": journeys[key[0]].deadline(key[1]),
            }
        )
    positive = [s for key, s in harness.strata.items() if key[2] == "positive"]
    offered = sum(s.offered for s in positive)
    good = sum(s.outcomes["completed"] for s in positive)
    covered = set(harness.actual_operations)
    missing = [f"P{n:02}" for n in range(1, 39) if f"P{n:02}" not in covered]
    return {
        "schema_version": "k17-capacity-result/v1",
        "profile_sha256": harness._profile_identity,
        "profile_unchanged": harness.profile.fingerprint() == harness._profile_identity,
        "release_sha256": harness.profile.release_sha256,
        "dataset_sha256": harness.profile.dataset_sha256,
        "run_label": harness.profile.run_label,
        "scope": harness.profile.scope,
        "elapsed_seconds": time.monotonic() - harness._started if harness._started else 0.0,
        "started_at": harness._started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "shard": [harness.profile.shard_index, harness.profile.shard_count],
        "principal_count": harness._preflight["verified_principals"],
        "configured_actor_count": len(harness.actors),
        "authentication_preflight": harness._preflight,
        "session_maintenance": harness.sessions.report(),
        "counts": {**dict(harness.counts), "pending": pending},
        "reconciliation": {
            "offered_equals_terminal_plus_pending": harness.counts["offered"]
            == harness.counts["terminal"] + pending,
            "planned_equals_offered_plus_not_offered": harness.counts["planned"]
            == harness.counts["offered"] + harness.counts["not_offered_after_stop"],
            "terminal_outcomes": sum(
                harness.counts[k]
                for k in (
                    "completed",
                    "denied",
                    "failed",
                    "rejected",
                    "timeout",
                    "interrupted",
                    "dropped",
                    "outcome_unknown",
                    "conflict",
                )
            ),
            "terminal_outcomes_match": harness.counts["terminal"]
            == sum(
                harness.counts[k]
                for k in (
                    "completed",
                    "denied",
                    "failed",
                    "rejected",
                    "timeout",
                    "interrupted",
                    "dropped",
                    "outcome_unknown",
                    "conflict",
                )
            ),
        },
        "measured_positive": {
            "offered": offered,
            "correct_by_deadline": good,
            "success_fraction": good / offered if offered else None,
            "observed_subset_success_target_met": offered > 0
            and good / offered >= 0.999
            and pending == 0,
        },
        "fatal_correctness_code": harness._fatal,
        "first_unexpected_http": harness._unexpected_http,
        "http": dict(harness.http),
        "peak_action_inflight": harness._peak,
        "jobs_pending_terminal": harness.counts["jobs_admitted"] - harness.counts["jobs_terminal"],
        "actual_actor_size_counts": dict(Counter(a.size for a in harness.actors)),
        "workload": {
            "pinned_size_weights": harness.profile.size_weights,
            "offered_size_counts": dict(harness._offered_sizes),
            "offered_role_counts": dict(harness._offered_roles),
            "closed_think_seconds_by_size": harness._cadence,
            "policy": "Open: seeded weighted size choice then round-robin distinct principals within each size. Closed: cohort/weight-adjusted cadence; actual offered mix reported, not assumed.",
            "missing_size_strata": [s for s, group in harness._size_actors.items() if not group],
        },
        "mutation_reconciliation": {
            "unresolved": harness.counts["unknown_mutations"],
            "witness_limit": 100,
            "witnesses": harness._unknown_mutation_witnesses,
            "policy": "Timeout/interruption remains in its original SLO denominator. An in-flight mutation may have committed; use read-only operator lookup by scoped request-key hash. No fresh post-deadline mutation is submitted to manufacture certainty.",
        },
        "cache_state": harness.profile.cache_state,
        "strata": strata,
        "active_window_30s": harness.coverage,
        "generator_resources": harness._resources,
        "operation38": {
            "actual_step_counts": dict(harness.actual_operations),
            "not_executed": missing,
            "declared_dispositions": harness.profile.operation_dispositions,
        },
        "primary_campaign_passed": False,
        "open_gates": [
            "A single run does not establish three normal closed/open runs, 50k engaged coverage, 2h primary/8h extension, browser/IdP/fault/DR or full physical dataset qualification.",
            "Generator shares a host until topology evidence says otherwise; NIC/clock/headroom and SUT metrics require same-window external witnesses.",
            "Per-operation/size n below 10000 cannot support the primary p99 precision gate; preserve every failed/partial run.",
        ],
        "histogram_note": "Disjoint logarithmic bucket upper estimates. All nonresponse and pending observations are deadline-censored, not completed-response latency; queue/actor-lock/renewal wait is included.",
    }


def coverage_sample(harness: CapacityHarness, now: float) -> dict[str, object]:
    cutoff = now - 30
    sets = {
        name: {actor for actor, t in stamps.items() if t >= cutoff}
        for name, stamps in harness.last.items()
    }
    waiting = {actor for actor, deadline in harness.waiting.values() if deadline >= now}
    intended = sets["intended"] | waiting
    return {
        "elapsed_seconds": now - harness._started,
        "phase": "warmup"
        if now < harness._started + harness.profile.warmup_seconds
        else "steady"
        if now
        < harness._started + harness.profile.warmup_seconds + harness.profile.duration_seconds
        else "drain",
        "intended_engaged": len(intended),
        "actually_started": len(sets["started"]),
        "unique_completing": len(sets["completing"]),
        "valid_waiting": len(waiting),
        "idle": len(harness.actors) - len(intended),
        "http_actions_inflight": harness._active,
        "pending_queue": harness._queue.qsize(),
    }


def resource_sample(harness: CapacityHarness, now: float) -> dict[str, object]:
    import os

    try:
        resource = import_module("resource")

        usage = resource.getrusage(resource.RUSAGE_SELF)
        measured: dict[str, object] = {
            "user_cpu_seconds": usage.ru_utime,
            "system_cpu_seconds": usage.ru_stime,
            "max_rss_native_units": usage.ru_maxrss,
        }
    except ImportError:
        measured = {
            "process_cpu_seconds": time.process_time(),
            "rss_status": "external_windows_counter_required",
        }
    return {
        "elapsed_seconds": now - harness._started,
        **measured,
        "fd_count": len(os.listdir("/dev/fd")) if os.path.isdir("/dev/fd") else None,
    }
