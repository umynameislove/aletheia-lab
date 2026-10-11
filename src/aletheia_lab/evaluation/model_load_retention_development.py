"""Collector-only development on actual SQLite and inert hashed producer buffers.

This is not a new native loader validation or a rerun of the protected census.
The common producer tape is made once; candidates see Records/caller lifecycle,
never the independent outcome ledger. Timing is local descriptive instrumentation.
"""

from __future__ import annotations

import sqlite3
import statistics
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, cast

from aletheia_lab.evaluation.model_load_contract import (
    LoadContract,
    Record,
    Scope,
    receipt_checker,
)
from aletheia_lab.evaluation.model_load_evidence_analysis import count_decisions
from aletheia_lab.evaluation.model_load_provenance import document_digest
from aletheia_lab.evaluation.model_load_retention import ReceiptStore, encode
from aletheia_lab.project.identity import content_sha256

BUFFERS = {"A": b"collector-inert-A" * 2048, "B": b"collector-inert-B" * 2048}
DIGESTS = {key: content_sha256(raw) for key, raw in BUFFERS.items()}


@dataclass(frozen=True)
class Delivery:
    record: Record
    delayed: bool = False
    mailbox: str | None = None


@dataclass(frozen=True)
class Episode:
    target: Scope
    contract: LoadContract
    deliveries: tuple[Delivery, ...]
    expire_root_after: int | None
    # Reference-only: not passed to ReceiptStore or sufficient_records.
    required_digest: str
    produced_digests: tuple[str, ...]
    producer_hash_bytes: int
    producer_hash_ns: int
    producer_hash_calls: int

    def truth(self) -> dict[str, Any]:
        loads = self.produced_digests
        violation = len(loads) > 1 or any(value != self.required_digest for value in loads)
        return {
            "assessable": True,
            "verdict": "violation" if violation else "compliant" if loads else None,
        }


def _selection(scope: Scope, digest: str, phase: str, root: Record | None = None) -> Record:
    return Record(
        "select",
        scope,
        "selection",
        digest,
        f"{scope.request}:{scope.attempt}",
        1,
        phase,
        parent_scope=None if root is None else root.scope,
        parent_selection=None if root is None else root.selection,
    )


def make_episodes() -> tuple[Episode, ...]:
    specs = (
        # name, attempt, retry, child selection, produced target buffers, transport
        ("single", 0, "inherit", "A", ("A",), "plain"),
        ("wrong", 0, "inherit", "A", ("B",), "plain"),
        ("extra-delayed", 0, "inherit", "A", ("A", "A"), "delay-second"),
        ("inherit-healthy", 1, "inherit", "A", ("A",), "plain"),
        ("wrong-mailbox", 1, "inherit", "A", ("B",), "wrong-mailbox"),
        ("root-expired", 1, "inherit", "B", ("B",), "expire-root"),
        ("reselect-delayed", 1, "reselect", "B", ("B",), "delay-all"),
        ("cache", 1, "inherit", "A", (), "plain"),
        ("duplicate", 0, "inherit", "A", ("A",), "duplicate"),
        ("foreign-scope", 1, "inherit", "A", ("B",), "foreign-scope"),
    )
    episodes = []
    for name, attempt, retry, selected, loaded, transport in specs:
        target = Scope(name, attempt)
        contract = LoadContract(
            "pin_at_acceptance",
            tuple(DIGESTS.values()),
            cast(Literal["inherit", "reselect"], retry),
        )
        root = _selection(Scope(name, 0), DIGESTS["A"], contract.policy)
        chosen = _selection(
            target,
            DIGESTS[selected],
            retry if attempt else contract.policy,
            root if attempt and retry == "inherit" else None,
        )
        deliveries: list[Delivery] = []
        hash_bytes = hash_ns = hash_calls = 0
        if attempt:
            started = time.perf_counter_ns()
            if content_sha256(BUFFERS["A"]) != root.digest:
                raise ValueError("inert root buffer hash changed")
            hash_ns += time.perf_counter_ns() - started
            hash_bytes += len(BUFFERS["A"])
            hash_calls += 1
            deliveries += [
                Delivery(root),
                Delivery(Record("root-entry", root.scope, "load", DIGESTS["A"], root.selection)),
                Delivery(Record("root-close", root.scope, "closure", load_count=1)),
            ]
        deliveries.append(Delivery(chosen))
        expire_after = len(deliveries) if transport == "expire-root" else None
        produced = []
        # Upstream production/hash work does not depend on the selector or routing.
        for index, artifact in enumerate(loaded):
            raw = BUFFERS[artifact]
            started = time.perf_counter_ns()
            digest = content_sha256(raw)
            hash_ns += time.perf_counter_ns() - started
            hash_bytes += len(raw)
            hash_calls += 1
            produced.append(digest)
            scope = Scope("foreign", attempt) if transport == "foreign-scope" else target
            record = Record(f"entry-{index}", scope, "load", digest, chosen.selection)
            delayed = transport == "delay-all" or transport == "delay-second" and index == 1
            mailbox = "wrong-destination" if transport == "wrong-mailbox" else target.request
            deliveries.append(Delivery(record, delayed, mailbox))
            if transport == "duplicate":
                deliveries.append(Delivery(record, delayed, mailbox))
        if not loaded:
            deliveries.append(Delivery(Record("cache", target, "cache_hit", DIGESTS["A"])))
        deliveries.append(Delivery(Record("close", target, "closure", load_count=len(loaded))))
        required = DIGESTS["A"] if attempt and retry == "inherit" else chosen.digest
        assert required is not None
        episodes.append(
            Episode(
                target,
                contract,
                tuple(deliveries),
                expire_after,
                required,
                tuple(produced),
                hash_bytes,
                hash_ns,
                hash_calls,
            )
        )
    return tuple(episodes)


def configuration_census() -> tuple[dict[str, Any], ...]:
    # First four isolate root lifetime and same-store routing on full retention.
    mechanisms = [
        dict(
            id=f"routing-{routing}-root-{int(root)}",
            selection="full",
            routing=routing,
            root_lifetime=root,
            horizon=2,
        )
        for routing in ("mailbox", "trusted_scope")
        for root in (False, True)
    ]
    # Matched-capability static selector and horizon comparisons, no oracle parameters.
    frontiers = [
        dict(
            id=f"{selection}-horizon-{horizon}",
            selection=selection,
            routing="trusted_scope",
            root_lifetime=True,
            horizon=horizon,
        )
        for selection in ("full", "static_sufficient")
        for horizon in (0, 2, 8)
    ]
    return tuple(mechanisms + frontiers)


def _run_configuration(
    path: Path, config: dict[str, Any], episodes: tuple[Episode, ...]
) -> dict[str, Any]:
    collector = ReceiptStore(
        path, **{key: config[key] for key in ("selection", "routing", "root_lifetime", "horizon")}
    )
    choices: dict[str, list[Any]] = {"before_release": [], "after_release": []}
    evidence_counts = []
    audit_queries = audit_available = audit_preserved = 0
    retained_decisions = {}
    try:
        for position, episode in enumerate(episodes):
            for index, delivery in enumerate(episode.deliveries, 1):
                collector.submit(
                    delivery.record,
                    episode.target,
                    delayed=delivery.delayed,
                    mailbox=delivery.mailbox,
                )
                collector.sample(advance=True)
                if index == episode.expire_root_after:
                    collector.expire_root(Scope(episode.target.request, 0))
                    collector.sample(advance=True)
            before = collector.read(episode.contract, episode.target)
            choices["before_release"].append((receipt_checker(before), episode.truth()))
            collector.sample(advance=True)
            collector.release(episode.target.request)
            collector.sample(advance=True)
            after = collector.read(episode.contract, episode.target)
            choices["after_release"].append((receipt_checker(after), episode.truth()))
            retained_decisions[episode.target.request] = receipt_checker(after)
            evidence_counts.append(len(after.records))
            collector.sample(advance=True)
            collector.drain(episode.target.request)
            collector.sample(advance=True)
            # Same retrospective requests at ages 0/1/2, irrespective of policy.
            # An expired service horizon is unavailable, never an incorrect guess.
            for historical in episodes[max(0, position - 2) : position + 1]:
                audit_queries += 1
                if historical.target.request not in collector.retired:
                    audited = receipt_checker(
                        collector.read(historical.contract, historical.target)
                    )
                    audit_available += 1
                    expected = retained_decisions[historical.target.request]
                    audit_preserved += int(
                        (audited.verdict, audited.reason, audited.eligibility)
                        == (expected.verdict, expected.reason, expected.eligibility)
                    )
                collector.sample(advance=True)
        return {
            "configuration": config,
            "queries": {key: count_decisions(value) for key, value in choices.items()},
            "after_query_record_counts": evidence_counts,
            "retrospective_queries": {
                "planned_queries": audit_queries,
                "available": audit_available,
                "unavailable": audit_queries - audit_available,
                "decision_preserved_when_available": audit_preserved,
                "requested_ages_drained_requests": [0, 1, 2],
            },
            "resources": collector.finish(),
        }
    finally:
        collector.close()


def run_pilot(*, repeats: int = 3) -> dict[str, Any]:
    if type(repeats) is not int or not 1 <= repeats <= 5:
        raise ValueError("pilot repetitions must be between one and five")
    episodes = make_episodes()
    tape = [[asdict(delivery) for delivery in episode.deliveries] for episode in episodes]
    configs = configuration_census()
    runs: dict[str, list[dict[str, Any]]] = {config["id"]: [] for config in configs}
    with tempfile.TemporaryDirectory(prefix="model-load-collector-") as directory:
        for repeat in range(repeats):
            # Rotate execution order to avoid confounding one selector with run position.
            order = configs[repeat:] + configs[:repeat]
            for config in order:
                try:
                    run = _run_configuration(
                        Path(directory) / f"run-{repeat}-{config['id']}.sqlite", config, episodes
                    )
                    run["status"] = "completed"
                except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
                    run = {
                        "configuration": config,
                        "status": "technical_failure",
                        "error_type": type(exc).__name__,
                        "planned_episode_count": len(episodes),
                    }
                runs[config["id"]].append(run)
    summaries = []
    for config in configs:
        values = runs[config["id"]]
        if any(value["status"] != "completed" for value in values):
            summaries.append(
                {
                    "configuration": config,
                    "status": "technical_failure",
                    "planned_repeats": repeats,
                    "retained_runs": values,
                }
            )
            continue
        deterministic = [{**value["resources"], "collector_operation_ns": 0} for value in values]
        if any(value["queries"] != values[0]["queries"] for value in values):
            raise ValueError("collector verdicts are not reproducible")
        if any(value != deterministic[0] for value in deterministic):
            raise ValueError("collector resource census is not reproducible")
        durations = [value["resources"]["collector_operation_ns"] for value in values]
        summaries.append(
            {
                **values[0],
                "timing_repeats": repeats,
                "collector_operation_ns_median": statistics.median(durations),
                "collector_operation_ns_min": min(durations),
                "collector_operation_ns_max": max(durations),
            }
        )
    return {
        "schema_version": "model-load-collector-development/v1",
        "status": "collector_development_complete"
        if all(row["status"] == "completed" for row in summaries)
        else "collector_development_incomplete",
        "source_class": "authored inert producer plus actual SQLite; not native loader transfer",
        "source_cluster_count": 1,
        "independent_deployments": 0,
        "episode_count": len(episodes),
        "configuration_count": len(configs),
        "planned_episode_runs": len(episodes) * len(configs) * repeats,
        "executed_episode_runs": sum(
            len(episodes)
            for values in runs.values()
            for value in values
            if value["status"] == "completed"
        ),
        "failed_configuration_runs": sum(
            value["status"] != "completed" for values in runs.values() for value in values
        ),
        "tape_sha256": document_digest(tape),
        "upstream_hash_input_bytes": sum(e.producer_hash_bytes for e in episodes),
        "upstream_hash_calls": sum(e.producer_hash_calls for e in episodes),
        "upstream_hash_ns": sum(e.producer_hash_ns for e in episodes),
        "common_capture_payload_bytes": sum(
            len(encode(asdict(d.record)).encode()) for e in episodes for d in e.deliveries
        ),
        "summaries": summaries,
        "native_loader_entries": 0,
        "local_model_fits": 0,
        "provider_calls": 0,
        "protected_validation_rerun": False,
        "adaptive_method_promoted": False,
        "limitations": "one query attempt per request, no crashes/concurrent descendants; logical byte-steps are not wall-clock byte-time; SQLite file bytes are not disk-write bytes; timing includes instrumentation, not production load overhead",
    }
