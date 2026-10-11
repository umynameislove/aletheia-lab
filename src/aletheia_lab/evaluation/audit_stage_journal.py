"""Durable single-worker progress, not an alternative audit evidence provider.

Only completed records are observations. A killed operation is indeterminate;
the original planned census comes from the parent's fixed configuration.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_audit import digest

STAGES = ("reserve", "native", "retain", "audit", "witness", "drain")


class StageJournal:
    """Flush and fsync each hash-linked record before returning to the worker."""

    def __init__(self, path: Path, config: dict[str, Any]) -> None:
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("owned journal required")
        self.stream = path.open("xb")
        self.sequence, self.previous, self.write_ns = 0, "0" * 64, 0
        self.origin = 0
        self.append("header", "complete", None, {"config": config, "tokens": roster(config)})

    def clock_ns(self) -> int:
        return perf_counter_ns() - self.origin if self.origin else 0

    def append(self, stage: str, event: str, token: str | None, payload: dict[str, Any]) -> None:
        started = perf_counter_ns()
        record = {
            "seq": self.sequence,
            "previous": self.previous,
            "stage": stage,
            "event": event,
            "token": token,
            "elapsed_ns": self.clock_ns(),
            "journal_write_ns_before_record": self.write_ns,
            "payload": payload,
        }
        identity = digest(record)
        self.stream.write((encode({**record, "sha256": identity}) + "\n").encode())
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.sequence += 1
        self.previous = identity
        self.write_ns += perf_counter_ns() - started

    def close(self) -> None:
        self.stream.close()


def roster(config: dict[str, Any]) -> list[str]:
    count = config["count"]
    if type(count) is not int or not 1 <= count <= 64:
        raise ValueError("bounded nonempty planned roster required")
    return [f"call-{index:03d}" for index in range(count)]


def read_progress(path: Path, config: dict[str, Any], *, interrupted: bool) -> dict[str, Any]:
    """Accept an incomplete final line only for abnormal termination, never interior damage."""
    if not path.exists():
        if interrupted:
            return {"records": [], "trailing_bytes": 0, "trailing_sha256": digest("")}
        raise ValueError("completed worker has no progress journal")
    if path.is_symlink() or path.parent.is_symlink() or path.stat().st_size > 128_000_000:
        raise ValueError("bounded owned journal required")
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    trailing = b""
    if lines and not lines[-1].endswith(b"\n"):
        if not interrupted:
            raise ValueError("completed journal has an unfinished record")
        trailing = lines.pop()
    records, previous = [], "0" * 64
    for index, line in enumerate(lines):
        record = json.loads(line)
        identity = record.pop("sha256")
        if (
            record["seq"] != index
            or record["previous"] != previous
            or digest(record) != identity
            or (encode({**record, "sha256": identity}) + "\n").encode() != line
        ):
            raise ValueError("journal prefix identity/encoding differs")
        records.append(record)
        previous = identity
    validate_records(records, config, interrupted=interrupted)
    return {
        "records": records,
        "trailing_bytes": len(trailing),
        "trailing_sha256": digest(trailing.hex()),
    }


def validate_records(
    records: list[dict[str, Any]], config: dict[str, Any], *, interrupted: bool
) -> None:
    tokens = roster(config)
    if not records:
        if not interrupted:
            raise ValueError("empty completed journal")
        return
    header = records[0]
    if (header["stage"], header["event"], header["token"], header["payload"]) != (
        "header",
        "complete",
        None,
        {"config": config, "tokens": tokens},
    ):
        raise ValueError("parent/worker planned census differs")
    expected = _expected_stages(config, tokens)
    cursor, active, last_ns, start_ns = 0, False, 0, 0
    terminal = False
    for record in records[1:]:
        elapsed = record["elapsed_ns"]
        if type(elapsed) is not int or elapsed < last_ns:
            raise ValueError("journal monotonic clock differs")
        last_ns = elapsed
        if record["event"] == "start":
            start_ns = elapsed
        elif record["stage"] not in {"setup", "terminal"}:
            _clock_bounds(record, start_ns)
        if terminal:
            raise ValueError("record after terminal boundary")
        cursor, active, terminal = _advance(record, expected, cursor, active, interrupted)
    if not interrupted and not terminal:
        raise ValueError("completed worker lacks terminal completion")


def _expected_stages(config: dict[str, Any], tokens: list[str]) -> list[tuple[str, str | None]]:
    expected: list[tuple[str, str | None]] = [("setup", None)]
    if config["mode"] in {"native", "hash"}:
        return expected + [("native", token) for token in tokens]
    return (
        expected
        + [(stage, token) for token in tokens for stage in STAGES[:3]]
        + [(stage, token) for token in tokens for stage in STAGES[3:]]
    )


def _clock_bounds(record: dict[str, Any], start_ns: int) -> None:
    payload, end_ns = record["payload"], record["elapsed_ns"]
    if payload.get("duration_ns", 0) > end_ns - start_ns:
        raise ValueError("operation duration exceeds observed journal interval")
    for key in ("offered_ms", "returned_ms", "ack_ms", "finished_ms", "sampled_ms"):
        if key in payload and (
            type(payload[key]) is not int or not 0 <= payload[key] <= end_ns // 1_000_000
        ):
            raise ValueError("operation clock outside observed journal interval")
    finish_key = {
        "reserve": "returned_ms",
        "retain": "ack_ms",
        "audit": "finished_ms",
        "drain": "sampled_ms",
    }.get(record["stage"])
    if finish_key in payload and payload[finish_key] < start_ns // 1_000_000:
        raise ValueError("operation return clock precedes its start")


def _advance(
    record: dict[str, Any],
    expected: list[tuple[str, str | None]],
    cursor: int,
    active: bool,
    interrupted: bool,
) -> tuple[int, bool, bool]:
    if record["stage"] == "terminal":
        if record["event"] != "complete" or active or cursor != len(expected):
            raise ValueError("premature completed terminal record")
        return cursor, active, True
    if cursor >= len(expected) or (record["stage"], record["token"]) != expected[cursor]:
        raise ValueError("journal stage/token order differs")
    if record["event"] == "start" and not active:
        return cursor, True, False
    if record["event"] not in {"complete", "error"} or not active:
        raise ValueError("duplicate or unstarted operation result")
    error = record["event"] == "error"
    if error and (not interrupted or not record["payload"].get("error_type")):
        raise ValueError("operation error disposition differs")
    duration = record["payload"].get("duration_ns")
    if type(duration) is not int or duration < 0:
        raise ValueError("completed operation duration missing")
    return cursor + 1, False, error


def token_progress(records: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Materialize every planned token without inventing an unobserved operation."""
    rows: dict[str, dict[str, Any]] = {
        token: {"token": token, "stages": {}} for token in roster(config)
    }
    for record in records:
        if record["token"] is None:
            continue
        stages = rows[record["token"]]["stages"]
        stages[record["stage"]] = {
            "status": "indeterminate" if record["event"] == "start" else record["event"],
            "elapsed_ns": record["elapsed_ns"],
            "payload": record["payload"],
        }
    for row in rows.values():
        for stage in STAGES:
            row["stages"].setdefault(stage, {"status": "not_started", "payload": {}})
    return list(rows.values())
