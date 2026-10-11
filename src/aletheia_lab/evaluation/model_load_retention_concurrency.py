"""Bounded threaded producer tape and matched SQLite multi-attempt retention.

Threads coordinate a deterministic event order, not a throughput benchmark.
The query service is fixed before capture; copied negative controls never alter
the collectors. No native model loader, protected study, timing or provider calls.
"""

from __future__ import annotations

import sqlite3
import tempfile
import threading
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.model_load_attempt_retention import AttemptReceiptStore
from aletheia_lab.evaluation.model_load_contract import (
    Decision,
    LoadContract,
    Observation,
    Record,
    Scope,
    receipt_checker,
)
from aletheia_lab.evaluation.model_load_evidence_analysis import sufficient_records
from aletheia_lab.evaluation.model_load_provenance import document_digest
from aletheia_lab.evaluation.model_load_retention import Selection

A, B = "a" * 64, "b" * 64
CONTRACT = LoadContract("pin_at_acceptance", (A, B))
REQUESTS = ("concurrent-a", "concurrent-b")
SERVICES = ("children_only", "root_and_children")


@dataclass(frozen=True)
class Event:
    owner: str
    scope: Scope
    record: Record | None = None
    delayed: bool = False


def _records(scope: Scope, digest: str = A) -> tuple[Record, Record, Record]:
    parent = Scope(scope.request, 0) if scope.attempt else None
    selected = Record(
        "select",
        scope,
        "selection",
        digest,
        f"token-{scope.attempt}",
        1,
        "inherit" if scope.attempt else CONTRACT.policy,
        parent_scope=parent,
        parent_selection="token-0" if parent else None,
    )
    return (
        selected,
        Record("entry", scope, "load", digest, selected.selection),
        Record(
            "close",
            scope,
            "closure",
            load_count=1,
        ),
    )


def _phase(request: str, attempt: int) -> tuple[Event, ...]:
    scope = Scope(request, attempt)
    digest = B if request == REQUESTS[0] and attempt == 2 else A
    selected, loaded, closed = _records(scope, digest)
    if request == REQUESTS[1] and attempt == 2:
        loaded = replace(loaded, scope=Scope("actual-foreign", 2), digest=B)
    events = [
        Event(request, scope, selected),
        Event(request, scope, loaded, attempt == 1),
        Event(request, scope, closed),
    ]
    if request == REQUESTS[0] and attempt == 0:
        events.append(
            Event(request, scope, replace(selected, identifier="root-conflict", digest=B), True)
        )
    if request == REQUESTS[0] and attempt == 2:
        events.append(Event(request, scope, loaded))  # Delivery duplicate, not another load.
    return (*events, Event(request, scope))


def _produce(
    index: int,
    tape: list[Event],
    errors: list[str],
    barrier: threading.Barrier,
    condition: threading.Condition,
    turn: list[int],
) -> None:
    try:
        for attempt in range(3):
            barrier.wait()
            batch = _phase(REQUESTS[index], attempt)
            with condition:
                while turn[0] != attempt * 2 + index:
                    if not condition.wait(timeout=2):
                        raise RuntimeError("producer coordination exceeded its bound")
                tape.extend(batch)
                turn[0] += 1
                condition.notify_all()
    except (RuntimeError, threading.BrokenBarrierError) as exc:
        with condition:
            errors.append(type(exc).__name__)
        barrier.abort()


def _thread_tape() -> tuple[Event, ...]:
    tape: list[Event] = []
    errors: list[str] = []
    barrier = threading.Barrier(2, timeout=2)
    condition = threading.Condition()
    turn = [0]
    threads = [
        threading.Thread(
            target=_produce, args=(index, tape, errors, barrier, condition, turn), daemon=True
        )
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    if errors or any(thread.is_alive() for thread in threads):
        raise RuntimeError("bounded producer tape incomplete")
    return tuple(tape)


def _scope_service(request: str, service: str) -> tuple[Scope, ...]:
    attempts = (1, 2) if service == "children_only" else (0, 1, 2)
    return tuple(Scope(request, attempt) for attempt in attempts)


def _query(store: AttemptReceiptStore, scope: Scope) -> tuple[Decision, Observation]:
    observation = store.snapshot(CONTRACT, scope)
    decision = receipt_checker(observation)
    store.sample()
    return decision, observation


def _frame_digest(observation: Observation) -> str:
    """Compare the admitted frame, not unrelated full-capture request receipts."""
    return document_digest([asdict(record) for record in sufficient_records(observation)])


def _negative_root_control(observation: Observation) -> dict[str, Any]:
    # Exposed copied-observation intervention, never production recovery or GC.
    without_root = replace(
        observation,
        records=tuple(
            record
            for record in observation.records
            if not (
                record.scope == Scope(observation.scope.request, 0) and record.kind == "selection"
            )
        ),
    )
    return {
        "intervention": "copied_observation_remove_root_selections",
        "before": asdict(receipt_checker(observation)),
        "after": asdict(receipt_checker(without_root)),
        "collector_mutated": False,
    }


def _audit(
    store: AttemptReceiptStore,
    service: str,
    ages: dict[str, int],
    references: dict[Scope, Decision],
) -> list[dict[str, Any]]:
    observations = []
    for request, age in ages.items():
        if age > 2:
            continue
        for scope in _scope_service(request, service):
            try:
                observation = store.snapshot(CONTRACT, scope)
                decision = receipt_checker(observation)
                outcome = {
                    "available": True,
                    "decision": asdict(decision),
                    "preserved": decision == references[scope],
                    "admitted_record_sha256": _frame_digest(observation),
                }
            except ValueError:
                if request not in store.retired:
                    raise
                outcome = {
                    "available": False,
                    "decision": None,
                    "preserved": None,
                    "admitted_record_sha256": None,
                }
            observations.append({"scope": asdict(scope), "age": age, **outcome})
            store.sample()
    return observations


def _advance_request(store: AttemptReceiptStore, request: str) -> None:
    scope = Scope(request, 0)
    store.register((scope,), (scope,))
    store.sample()
    for record in _records(scope):
        store.submit(request, record)
        store.sample()
    store.settle(scope)
    store.sample()
    store.drain(request)
    store.sample()


def _capture(store: AttemptReceiptStore, tape: tuple[Event, ...], service: str) -> None:
    for request in REQUESTS:
        store.register(tuple(Scope(request, a) for a in range(3)), _scope_service(request, service))
        store.sample()
    for event in tape:
        if event.record is None:
            store.settle(event.scope)
        else:
            store.submit(event.owner, event.record, delayed=event.delayed)
        store.sample()


def _immediate(
    store: AttemptReceiptStore,
    service: str,
) -> tuple[dict[str, list[dict[str, Any]]], dict[Scope, Decision], list[dict[str, Any]]]:
    references: dict[Scope, Decision] = {}
    immediate: dict[str, list[dict[str, Any]]] = {"before_release": [], "after_release": []}
    controls: list[dict[str, Any]] = []
    for cutoff in immediate:
        for request in REQUESTS:
            if cutoff == "after_release":
                store.release(request)
                store.sample()
            for scope in _scope_service(request, service):
                decision, observation = _query(store, scope)
                immediate[cutoff].append(
                    {
                        "scope": asdict(scope),
                        "decision": asdict(decision),
                        "admitted_record_sha256": _frame_digest(observation),
                    }
                )
                if cutoff == "after_release":
                    references[scope] = decision
                    if scope == Scope(REQUESTS[1], 1):
                        controls.append(_negative_root_control(observation))
    return immediate, references, controls


def _audit_schedule(
    store: AttemptReceiptStore,
    service: str,
    references: dict[Scope, Decision],
) -> list[dict[str, Any]]:
    ages: dict[str, int] = {}
    audit: list[dict[str, Any]] = []
    for request in REQUESTS:
        ages = {key: value + 1 for key, value in ages.items()}
        ages[request] = 0
        store.drain(request)
        store.sample()
        audit.extend(_audit(store, service, ages, references))
    for index in range(2):
        _advance_request(store, f"audit-advance-{index}")
        ages = {key: value + 1 for key, value in ages.items()}
        audit.extend(_audit(store, service, ages, references))
    return audit


def _replay(
    path: Path,
    tape: tuple[Event, ...],
    selection: Selection,
    service: str,
    horizon: int,
) -> dict[str, Any]:
    store = AttemptReceiptStore(path, selection=selection, horizon=horizon)
    try:
        _capture(store, tape, service)
        immediate, references, controls = _immediate(store, service)
        audit = _audit_schedule(store, service, references)
        return {
            "status": "completed",
            "selection": selection,
            "service": service,
            "horizon": horizon,
            "immediate_queries": immediate,
            "audit_queries": audit,
            "root_delete_controls": controls,
            "resources": store.finish(),
        }
    finally:
        store.db.close()


def _summarize(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    comparisons = []
    for service in SERVICES:
        for horizon in (0, 2, 8):
            pair = [row for row in runs if row["service"] == service and row["horizon"] == horizon]
            full, static = pair
            completed = all(row["status"] == "completed" for row in pair)
            comparisons.append(
                {
                    "service": service,
                    "horizon": horizon,
                    "comparison_available": completed,
                    "matched_immediate_decisions": completed
                    and full["immediate_queries"] == static["immediate_queries"],
                    "matched_audit_decisions_and_availability": completed
                    and full["audit_queries"] == static["audit_queries"],
                    "matched_capture": completed
                    and (full["resources"]["captures"], full["resources"]["captured_bytes"])
                    == (static["resources"]["captures"], static["resources"]["captured_bytes"]),
                    "matched_measurement_steps": completed
                    and full["resources"]["samples"] == static["resources"]["samples"],
                    "matched_bookkeeping_steps": completed
                    and full["resources"]["bookkeeping_byte_steps"]
                    == static["resources"]["bookkeeping_byte_steps"],
                    "static_written_bytes_no_greater": completed
                    and static["resources"]["written_bytes"] <= full["resources"]["written_bytes"],
                    "static_payload_steps_no_greater": completed
                    and static["resources"]["payload_byte_steps"]
                    <= full["resources"]["payload_byte_steps"],
                }
            )
    return comparisons


def _configurations() -> list[tuple[Selection, str, int]]:
    selections: tuple[Selection, ...] = ("full", "static_sufficient")
    return [
        (selection, service, horizon)
        for service in SERVICES
        for horizon in (0, 2, 8)
        for selection in selections
    ]


def run_concurrency() -> dict[str, Any]:
    """Execute one bounded tape and all twelve fixed matched collector configurations."""
    configurations = _configurations()
    try:
        tape = _thread_tape()
    except RuntimeError as exc:
        return {
            "schema_version": "model-load-concurrent-retention/v1",
            "status": "producer_tape_incomplete",
            "planned_configuration_runs": len(configurations),
            "executed_configuration_runs": 0,
            "failed_configuration_runs": len(configurations),
            "error_type": type(exc).__name__,
            "native_loader_entries": 0,
            "provider_calls": 0,
        }
    runs: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="model-load-concurrent-retention-") as directory:
        for index, (selection, service, horizon) in enumerate(configurations):
            try:
                row = _replay(
                    Path(directory) / f"collector-{index}.sqlite", tape, selection, service, horizon
                )
            except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
                row = {
                    "status": "technical_failure",
                    "selection": selection,
                    "service": service,
                    "horizon": horizon,
                    "error_type": type(exc).__name__,
                    "planned_primary_requests": 2,
                }
            runs.append(row)
    completed = sum(row["status"] == "completed" for row in runs)
    return {
        "schema_version": "model-load-concurrent-retention/v1",
        "status": "concurrency_development_complete"
        if completed == len(runs)
        else "concurrency_development_incomplete",
        "source_class": "coordinated threaded inert receipts and real SQLite replay",
        "producer_threads": 2,
        "coordinated_producer_phases": 3,
        "primary_requests": 2,
        "primary_attempts": 6,
        "auxiliary_audit_advance_requests": 2,
        "planned_configuration_runs": len(configurations),
        "executed_configuration_runs": completed,
        "failed_configuration_runs": len(runs) - completed,
        "planned_primary_request_runs": 24,
        "executed_primary_request_runs": completed * 2,
        "common_tape_events": len(tape),
        "common_receipt_deliveries": sum(e.record is not None for e in tape),
        "tape": [asdict(event) for event in tape],
        "tape_sha256": document_digest([asdict(event) for event in tape]),
        "runs": runs,
        "comparisons": _summarize(runs),
        "native_loader_entries": 0,
        "local_model_fits": 0,
        "provider_calls": 0,
        "protected_validation_rerun": False,
        "throughput_measured": False,
        "timing_measured": False,
        "limitations": "fixed upfront query census; coordinated threads create a serialized deterministic tape, not a contention benchmark; one-level root inheritance; collector payload and serialized bookkeeping measured, report references and Python allocator excluded; SQLite file bytes are not disk-write bytes",
    }


def _verify_queries(row: dict[str, Any], tape: tuple[Event, ...]) -> None:
    service, horizon = row["service"], row["horizon"]
    scopes = [scope for request in REQUESTS for scope in _scope_service(request, service)]
    expected: dict[Scope, dict[str, Any]] = {}
    frames: dict[Scope, str] = {}
    for cutoff in ("before_release", "after_release"):
        choices = []
        for scope in scopes:
            records = tuple(
                event.record
                for event in tape
                if event.owner == scope.request
                and event.record is not None
                and (cutoff == "after_release" or not event.delayed)
            )
            observation = Observation(CONTRACT, scope, tuple(dict.fromkeys(records)))
            decision = asdict(receipt_checker(observation))
            signature = _frame_digest(observation)
            choices.append(
                {
                    "scope": asdict(scope),
                    "decision": decision,
                    "admitted_record_sha256": signature,
                }
            )
            expected[scope] = decision
            frames[scope] = signature
        if document_digest(choices) != document_digest(row["immediate_queries"][cutoff]):
            raise ValueError("immediate decisions differ from the retained inert tape")
    audits: list[dict[str, Any]] = []
    for request, age in (
        (REQUESTS[0], 0),
        (REQUESTS[0], 1),
        (REQUESTS[1], 0),
        (REQUESTS[0], 2),
        (REQUESTS[1], 1),
        (REQUESTS[1], 2),
    ):
        available = age < horizon
        audits.extend(
            {
                "scope": asdict(scope),
                "age": age,
                "available": available,
                "decision": expected[scope] if available else None,
                "preserved": True if available else None,
                "admitted_record_sha256": frames[scope] if available else None,
            }
            for scope in _scope_service(request, service)
        )
    if document_digest(row["audit_queries"]) != document_digest(audits):
        raise ValueError("audit decision, census or availability changed")


def _verify_incomplete_producer(report: dict[str, Any]) -> None:
    if (
        report["planned_configuration_runs"],
        report["executed_configuration_runs"],
        report["failed_configuration_runs"],
    ) != (12, 0, 12):
        raise ValueError("incomplete producer census changed")


def verify_concurrency(report: dict[str, Any]) -> None:
    """Recompute report consistency from retained data; no threads, SQLite or native loads."""
    if report["status"] == "producer_tape_incomplete":
        _verify_incomplete_producer(report)
        return
    tape = tuple(
        event for attempt in range(3) for request in REQUESTS for event in _phase(request, attempt)
    )
    raw_tape = [asdict(event) for event in tape]
    if document_digest(report["tape"]) != document_digest(raw_tape) or report[
        "tape_sha256"
    ] != document_digest(raw_tape):
        raise ValueError("retained producer tape changed")
    runs = report["runs"]
    actual = [(row["selection"], row["service"], row["horizon"]) for row in runs]
    if actual != _configurations():
        raise ValueError("fixed configuration census changed")
    completed = sum(row["status"] == "completed" for row in runs)
    expected_status = (
        "concurrency_development_complete"
        if completed == 12
        else "concurrency_development_incomplete"
    )
    expected_census = (12, completed, 12 - completed, 24, completed * 2, len(tape), 20)
    keys = (
        "planned_configuration_runs",
        "executed_configuration_runs",
        "failed_configuration_runs",
        "planned_primary_request_runs",
        "executed_primary_request_runs",
        "common_tape_events",
        "common_receipt_deliveries",
    )
    if report["status"] != expected_status or tuple(report[key] for key in keys) != expected_census:
        raise ValueError("execution census changed")
    for row in runs:
        if row["status"] == "completed":
            _verify_queries(row, tape)
    if document_digest(report["comparisons"]) != document_digest(_summarize(runs)):
        raise ValueError("matched resource/decision comparisons changed")
