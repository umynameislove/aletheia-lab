"""Independently rebuild worker progress and proof-backed service denominators."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.audit_stage_journal import read_progress, token_progress
from aletheia_lab.evaluation.calibration_audit_archive import assess
from aletheia_lab.evaluation.calibration_audit_verification import witnessed_packet


def rebuild(directory: Path, config: dict[str, Any], *, interrupted: bool) -> dict[str, Any]:
    progress = read_progress(directory / "progress.jsonl", config, interrupted=interrupted)
    records = progress["records"]
    rows = token_progress(records, config)
    setup = next(
        (r["payload"] for r in records if r["stage"] == "setup" and r["event"] == "complete"), None
    )
    terminal = next((r["payload"] for r in records if r["stage"] == "terminal"), None)
    census = Counter(
        {
            field: 0
            for field in (
                "offered",
                "accepted",
                "refused",
                "admission_indeterminate",
                "complete",
                "unknown",
                "late",
                "wrong",
                "accepted_but_unserved",
                "accepted_unverified",
                "accepted_service_indeterminate",
                "accepted_without_proven_service",
                "audit_unobserved",
                "native_completed",
                "native_failed",
                "retention_acknowledged",
                "accepted_retention_overruns",
                "native_indeterminate",
                "not_started",
            )
        }
    )
    observed = []
    previous_offer = 0
    for row in rows:
        stages = row["stages"]
        reserve, native = stages["reserve"], stages["native"]
        audit = stages["audit"]
        if stages["retain"]["status"] == "complete":
            census["retention_acknowledged"] += stages["retain"]["payload"]["retained_ack"]
        offered = reserve["status"] != "not_started"
        census["offered"] += offered
        census["not_started"] += all(s["status"] == "not_started" for s in stages.values())
        decision = reserve["payload"] if reserve["status"] == "complete" else None
        accepted = bool(decision and decision["accepted"])
        census["accepted_retention_overruns"] += bool(
            accepted
            and stages["retain"]["status"] == "complete"
            and not stages["retain"]["payload"]["retained_ack"]
        )
        census["accepted"] += accepted
        census["refused"] += bool(decision and not decision["accepted"])
        census["admission_indeterminate"] += offered and decision is None
        census["native_completed"] += native["status"] == "complete"
        census["native_indeterminate"] += native["status"] in {"indeterminate", "error"}
        if decision is not None:
            if (
                type(decision["accepted"]) is not bool
                or decision["until_ms"] != decision["offered_ms"] + config["deadline_ms"]
                or not previous_offer <= decision["offered_ms"] <= decision["returned_ms"]
            ):
                raise ValueError("observed admission clock/decision differs")
            previous_offer = decision["offered_ms"]
        complete, answer = _native_audit(config, row, setup, decision, census)
        census["complete"] += complete
        unverified = bool(
            accepted
            and decision is not None
            and answer is not None
            and set(answer.values()) == {"compliant"}
            and audit["payload"]["finished_ms"] <= decision["until_ms"]
            and not complete
        )
        indeterminate = bool(accepted and audit["status"] in {"indeterminate", "error"})
        census["accepted_unverified"] += unverified
        census["accepted_service_indeterminate"] += indeterminate
        census["accepted_but_unserved"] += (
            accepted and not complete and not unverified and not indeterminate
        )
        census["accepted_without_proven_service"] += accepted and not complete
        census["audit_unobserved"] += offered and audit["status"] != "complete"
        observed.append(
            {
                "token": row["token"],
                "accepted": accepted,
                "complete": complete,
                "answer": answer,
                "stages": {name: stage["status"] for name, stage in stages.items()},
            }
        )
    if terminal is not None:
        _terminal(config, terminal, census, rows)
        expected_timers = stage_timers(records, config)
        terminal_record = next(r for r in records if r["stage"] == "terminal")
        expected_timers["progress_journal_ns"] = terminal_record["journal_write_ns_before_record"]
        if terminal["timers"] != expected_timers:
            raise ValueError("terminal timers differ from observed operation durations")
    return {
        "service": dict(census),
        "token_census": observed,
        "terminal": terminal,
        "completed_stage_timers": stage_timers(records, config),
        "failed_operation_ns": sum(
            r["payload"].get("duration_ns", 0) for r in records if r["event"] == "error"
        ),
        "journal_bytes": (directory / "progress.jsonl").stat().st_size
        if (directory / "progress.jsonl").exists()
        else 0,
        "record_count": len(records),
        "trailing_bytes": progress["trailing_bytes"],
        "trailing_sha256": progress["trailing_sha256"],
        "partial_storage_reconstruction": "not used to infer ACK, original audit finish or service",
    }


def _native_audit(
    config: dict[str, Any],
    row: dict[str, Any],
    setup: dict[str, Any] | None,
    decision: dict[str, Any] | None,
    census: Counter[str],
) -> tuple[bool, Any]:
    from aletheia_lab.evaluation.pipeline_audit_source import history_reference

    stages = row["stages"]
    if stages["native"]["status"] != "complete" or config["mode"] in {"native", "hash"}:
        return False, None
    packet = stages["native"]["payload"]["packet"]
    if setup is None or packet["token"] != row["token"]:
        raise ValueError("native packet lacks fit/token association")
    reference = history_reference(
        {"fit_call_ledger": setup["fit_call_ledger"], "native_packets": [packet]}
    )
    census["native_failed"] += packet["call"]["failed"]
    retain = stages["retain"]
    if retain["status"] == "complete":
        retained = retain["payload"]
        if (
            type(retained["retained_ack"]) is not bool
            or decision is None
            or not decision["returned_ms"] <= retained["ack_ms"]
        ):
            raise ValueError("returned retention ACK clock differs")
    return _audit(config, row, decision, packet, reference, census)


def _audit(
    config: dict[str, Any],
    row: dict[str, Any],
    decision: dict[str, Any] | None,
    packet: dict[str, Any],
    reference: dict[str, str],
    census: Counter[str],
) -> tuple[bool, Any]:
    from aletheia_lab.evaluation.pipeline_audit_source import QUERIES

    stages = row["stages"]
    if stages["audit"]["status"] != "complete":
        return False, None
    answer, finish = stages["audit"]["payload"]["answer"], stages["audit"]["payload"]["finished_ms"]
    if set(answer) != set(QUERIES) or decision is None:
        raise ValueError("audit question census differs")
    if stages["retain"]["status"] != "complete" or finish < stages["retain"]["payload"]["ack_ms"]:
        raise ValueError("audit precedes retention return")
    late = finish > decision["until_ms"]
    census["late"] += late
    census["unknown"] += "unknown" in answer.values()
    census["wrong"] += any(answer[q] not in {"unknown", reference[q]} for q in QUERIES)
    if stages["witness"]["status"] != "complete":
        return False, answer
    saved = witnessed_packet(stages["witness"]["payload"]["witness"], row["token"], config["quota"])
    if saved is not None and (saved != packet or not stages["retain"]["payload"]["retained_ack"]):
        raise ValueError("retained audit proof differs from acknowledged native capsule")
    expected = assess(saved) if saved else dict.fromkeys(QUERIES, "unknown")
    if answer != expected:
        raise ValueError("audit answer differs from retained frontier")
    return bool(
        decision["accepted"]
        and answer == reference
        and set(answer.values()) == {"compliant"}
        and not late
    ), answer


def _terminal(
    config: dict[str, Any],
    terminal: dict[str, Any],
    census: Counter[str],
    rows: list[dict[str, Any]],
) -> None:
    timers = terminal["timers"]
    if min(timers.values()) < 0 or sum(timers.values()) > terminal["workload_ns"]:
        raise ValueError("overlapping/negative measured stage timers")
    if terminal["provider_calls"] != 0:
        raise ValueError("local workload provider boundary differs")
    if config["mode"] in {"native", "hash"}:
        return
    for field in ("accepted", "refused"):
        if terminal["archive_metrics"][field] != census[field]:
            raise ValueError("durable admission census differs")
    for row in rows:
        saved = witnessed_packet(terminal["final_witness"], row["token"], config["quota"])
        if saved is not None and saved != row["stages"]["native"]["payload"]["packet"]:
            raise ValueError("terminal retained native capsule differs")


def stage_timers(records: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, int]:
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
    fields = {
        "reserve": "reserve_ack_ns",
        "retain": "write_ack_ns",
        "audit": "query_verify_ns",
        "witness": "research_witness_ns",
        "drain": "drain_ns",
    }
    for record in records:
        if record["event"] != "complete" or record["stage"] in {"header", "setup", "terminal"}:
            continue
        stage, payload = record["stage"], record["payload"]
        if stage == "native":
            field = (
                "native_prediction_ns"
                if config["mode"] in {"native", "hash"}
                else "instrumented_prediction_ns"
            )
            timers[field] += payload["prediction_ns"]
            timers["hash_ns"] += payload.get("hash_ns", 0)
            overhead = payload["duration_ns"] - payload["prediction_ns"] - payload.get("hash_ns", 0)
            if overhead < 0:
                raise ValueError("nested predictor/hash timer exceeds its enclosing operation")
            timers["capture_ns"] += overhead
        else:
            timers[fields[stage]] += payload["duration_ns"]
    return timers
