"""Serial native Pipeline calls with a durable, interrupted-worker census."""

from __future__ import annotations

import os
from pathlib import Path
from time import perf_counter_ns, sleep
from typing import Any, cast

from aletheia_lab.evaluation.audit_stage_journal import StageJournal, roster
from aletheia_lab.evaluation.calibration_audit_archive import assess
from aletheia_lab.evaluation.calibration_audit_study import _bytes, archive, drain_completed
from aletheia_lab.evaluation.request_model_audit import digest


def worker(config: dict[str, Any], directory: Path) -> dict[str, Any]:
    """Only owned generated models; the journal cannot be queried by the archive."""
    from aletheia_lab.evaluation.pipeline_audit_source import predict, setup

    directory.mkdir(exist_ok=False)
    journal = StageJournal(directory / "progress.jsonl", config)
    owner: Any = None

    def operation(stage: str, token: str | None, function: Any) -> dict[str, Any]:
        journal.append(stage, "start", token, {})
        started = perf_counter_ns()
        try:
            result = function()
        except (ValueError, OSError, RuntimeError, TimeoutError) as exc:
            journal.append(
                stage,
                "error",
                token,
                {
                    "duration_ns": perf_counter_ns() - started,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        result["duration_ns"] = perf_counter_ns() - started
        journal.append(stage, "complete", token, result)
        return cast(dict[str, Any], result)

    try:
        session: dict[str, Any] = {}

        def fit() -> dict[str, Any]:
            session.update(setup(config["seed"]))
            predict(session, "warmup")
            return {"fit_call_ledger": session["ledger"], "setup_predictions": 1}

        setup_result = operation("setup", None, fit)
        mode = config["mode"]
        if mode not in {"native", "hash"}:
            owner = archive(mode, directory / "archive.sqlite", config["quota"])
        journal.origin, journal.write_ns = perf_counter_ns(), 0
        timers = dict.fromkeys(
            (
                "native_prediction_ns",
                "instrumented_prediction_ns",
                "capture_ns",
                "reserve_ack_ns",
                "write_ack_ns",
                "query_verify_ns",
                "research_witness_ns",
                "drain_ns",
                "hash_ns",
            ),
            0,
        )
        _dispatch(config, session, owner, journal, timers, operation)
        if owner is not None:
            _audit_burst(config, owner, journal, timers, operation)
        workload_ns = journal.clock_ns()
        timers["progress_journal_ns"] = journal.write_ns
        metrics = owner.snapshot() if owner is not None else {}
        final_witness = owner.witness(roster(config)) if owner is not None else None
        if owner is not None:
            owner.close()
            owner = None
        terminal = {
            "status": "complete",
            "workload_ns": workload_ns,
            "timers": timers,
            "setup_ns": setup_result["duration_ns"],
            "archive_metrics": metrics,
            "final_witness": final_witness,
            "provider_calls": 0,
            "closed_storage_bytes": _bytes(directory / "archive.sqlite"),
            "timing_scope": "setup, final snapshot, close and terminal record excluded; all per-call journal/witness work consumes workload and deadlines; nested SQL metrics not additive",
        }
        journal.append("terminal", "complete", None, terminal)
        return {"status": "complete", "provider_calls": 0}
    finally:
        if owner is not None:
            owner.close()
        journal.close()


def _dispatch(
    config: dict[str, Any],
    session: dict[str, Any],
    owner: Any,
    journal: StageJournal,
    timers: dict[str, int],
    operation: Any,
) -> None:
    for token in roster(config):
        if owner is not None:
            offered = journal.clock_ns() // 1_000_000
            until = offered + config["deadline_ms"]

            def reserve(
                token: str = token, offered: int = offered, until: int = until
            ) -> dict[str, Any]:
                return {
                    "offered_ms": offered,
                    "until_ms": until,
                    "accepted": owner.reserve(
                        token, now=offered, until=until, bound=config["bound"]
                    ),
                    "returned_ms": journal.clock_ns() // 1_000_000,
                }

            result = operation("reserve", token, reserve)
            timers["reserve_ack_ns"] += result["duration_ns"]
            interrupt(config, "after_reserve")
        result = operation("native", token, lambda token=token: _native(config, session, token))
        field = "native_prediction_ns" if owner is None else "instrumented_prediction_ns"
        timers[field] += result["prediction_ns"]
        timers["hash_ns"] += result.get("hash_ns", 0)
        timers["capture_ns"] += (
            result["duration_ns"] - result["prediction_ns"] - result.get("hash_ns", 0)
        )
        if owner is not None:
            packet = result["packet"]

            def retain(packet: dict[str, Any] = packet) -> dict[str, Any]:
                retained = owner.put_evidence(packet, now=journal.clock_ns() // 1_000_000)
                return {"retained_ack": retained, "ack_ms": journal.clock_ns() // 1_000_000}

            ack = operation("retain", token, retain)
            timers["write_ack_ns"] += ack["duration_ns"]
            interrupt(config, "after_ack")


def _native(config: dict[str, Any], session: dict[str, Any], token: str) -> dict[str, Any]:
    from aletheia_lab.evaluation.pipeline_audit_source import predict

    if config["mode"] not in {"native", "hash"}:
        packet = predict(session, token)
        return {
            "packet": packet,
            "prediction_ns": session["last_prediction_ns"],
            "prediction_scope": "instrumented predictor including scoped transform hook and output conversion",
        }
    begin = perf_counter_ns()
    output = session["model"].predict_proba(session["query"])
    native_ns, hash_ns = perf_counter_ns() - begin, 0
    if config["mode"] == "hash":
        begin = perf_counter_ns()
        digest([session["query"].tolist(), output.tolist()])
        hash_ns = perf_counter_ns() - begin
    return {
        "prediction_ns": native_ns,
        "hash_ns": hash_ns,
        "output_sha256": digest(output.tolist()),
        "prediction_scope": "direct native predictor floor; no durable audit offered",
    }


def _audit_burst(
    config: dict[str, Any],
    owner: Any,
    journal: StageJournal,
    timers: dict[str, int],
    operation: Any,
) -> None:
    for token in roster(config):

        def audit(token: str = token) -> dict[str, Any]:
            owner.query([token], now=journal.clock_ns() // 1_000_000)
            stored = owner.evidence(token)
            answers = (
                assess(stored)
                if stored
                else dict.fromkeys(
                    ("authorized_state", "disjoint_membership", "sigmoid_arithmetic"), "unknown"
                )
            )
            return {"answer": answers, "finished_ms": journal.clock_ns() // 1_000_000}

        answer = operation("audit", token, audit)
        timers["query_verify_ns"] += answer["duration_ns"]
        witness = operation(
            "witness", token, lambda token=token: {"witness": owner.witness([token])}
        )
        timers["research_witness_ns"] += witness["duration_ns"]

        def drain(token: str = token) -> dict[str, Any]:
            sampled = journal.clock_ns() // 1_000_000
            return {"sampled_ms": sampled, "released": drain_completed(owner, token, now=sampled)}

        drained = operation("drain", token, drain)
        timers["drain_ns"] += drained["duration_ns"]


def interrupt(config: dict[str, Any], boundary: str) -> None:
    """Prespecified owned-child interruption controls, never production fault claims."""
    if config.get("interrupt") == boundary:
        os._exit(23)
    if config.get("interrupt") == "timeout_after_ack" and boundary == "after_ack":
        sleep(30)
