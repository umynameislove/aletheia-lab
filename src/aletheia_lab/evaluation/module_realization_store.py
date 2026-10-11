"""Scoped live transport and durable evidence; never substitute reference for capture."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.project.identity import content_sha256


def read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.is_symlink():
        raise ValueError("owned journal cannot be a symlink")
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def recovered(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.is_symlink():
        raise ValueError("owned database cannot be a symlink")
    # Read-only connection must not checkpoint or silently repair a damaged store.
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("evidence database integrity differs")
        rows = db.execute("SELECT seq,payload,digest FROM events ORDER BY seq").fetchall()
    records = []
    for seq, payload, digest in rows:
        if content_sha256(payload.encode()) != digest:
            raise ValueError("evidence row digest differs")
        event = json.loads(payload)
        if event["seq"] != seq:
            raise ValueError("evidence sequence differs")
        records.append(event)
    return records


class Collector:
    """One persistent connection per workload, matched FULL durability for both modes.

    Faults operate on the live emit path, before persistence. Drain has explicitly
    weaker acknowledgement semantics: pending observations are not audit promises.
    """

    def __init__(self, directory: Path, config: dict[str, Any]) -> None:
        self.mode = config["evidence"]
        self.fault = config.get("fault", "none")
        self.durability = config.get("durability", "event")
        self.sequence = 0
        self.pending: list[dict[str, Any]] = []
        self.acks: list[int] = []
        self.ack_digests: dict[int, str] = {}
        self.offered = 0
        self.dropped = 0
        self.duplicates = 0
        self.payload_bytes = 0
        self.write_ns = 0
        self.project_ns = 0
        self.db: sqlite3.Connection | None = None
        if self.mode != "native":
            self.db = sqlite3.connect(directory / "evidence.sqlite", check_same_thread=False)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute(
                "CREATE TABLE events(seq INTEGER PRIMARY KEY,payload TEXT NOT NULL,"
                "digest TEXT NOT NULL)"
            )
            self.db.commit()

    def _project(self, value: dict[str, Any]) -> dict[str, Any]:
        if self.mode == "full":
            return dict(value)
        # Diagnostic paths/source dumps do not answer the scoped binding query.
        return {key: item for key, item in value.items() if key not in {"diagnostics"}}

    def emit(self, value: dict[str, Any]) -> None:
        self.offered += 1
        self.sequence += 1
        if self.mode == "native":
            return
        kind = value["kind"]
        cut = {
            "drop_predict": kind == "predict" and value.get("request_id") == "r-011",
            "drop_load": kind == "load" and value.get("label") == "B",
            "drop_closure": kind == "closure",
        }.get(self.fault, False)
        if cut:
            self.dropped += 1
            return
        started = perf_counter_ns()
        record = {**self._project(value), "seq": self.sequence}
        self.project_ns += perf_counter_ns() - started
        self.pending.append(record)
        if self.fault == "duplicate" and self.sequence == 2:
            self.pending.append(record)
            self.duplicates += 1
        if self.durability == "event" and self.fault != "delay":
            self.flush()

    def flush(self) -> None:
        if self.db is None or not self.pending:
            return
        started = perf_counter_ns()
        committed: dict[int, str] = {}
        added_bytes = 0
        with self.db:
            for record in self.pending:
                payload = encode(record)
                digest = content_sha256(payload.encode())
                old = self.db.execute(
                    "SELECT digest FROM events WHERE seq=?", (record["seq"],)
                ).fetchone()
                if old is not None:
                    if old != (digest,):
                        raise ValueError("conflicting duplicate live event")
                    continue
                self.db.execute(
                    "INSERT INTO events VALUES(?,?,?)", (record["seq"], payload, digest)
                )
                added_bytes += len(payload.encode())
                committed[record["seq"]] = digest
        # Publish a durability promise only after the transaction commits.
        self.payload_bytes += added_bytes
        self.ack_digests.update(committed)
        self.write_ns += perf_counter_ns() - started
        self.acks.extend(committed)
        self.pending.clear()

    def status(self) -> dict[str, Any]:
        return {
            "offered_events": self.offered,
            "durable_ack_sequences": list(self.acks),
            "durable_ack_digests": dict(self.ack_digests),
            "pending_events": len(self.pending),
            "dropped_events": self.dropped,
            "duplicate_deliveries": self.duplicates,
            "payload_bytes": self.payload_bytes,
            "write_ns": self.write_ns,
            "projection_ns": self.project_ns,
        }

    def close(self) -> None:
        if self.db is not None:
            self.db.close()


def _unique(records: list[dict[str, Any]], kind: str, key: str) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    for row in records:
        if row["kind"] != kind:
            continue
        identity = row[key]
        if identity in selected and selected[identity] != row:
            raise ValueError("conflicting audit identity")
        selected[identity] = row
    return selected


def audit(records: list[dict[str, Any]], requests: list[str]) -> dict[str, Any]:
    """Conservative actual-use service, not a completeness claim for hidden work."""
    if len(set(requests)) != len(requests):
        raise ValueError("duplicate parent audit request identity")
    loads = _unique(records, "load", "load_id")
    predictions = _unique(records, "predict", "request_id")
    closures = [row for row in records if row["kind"] == "closure"]
    verdicts = {}
    for request_id in requests:
        row = predictions.get(request_id)
        load = loads.get(row["load_id"]) if row else None
        if not row or not load:
            verdicts[request_id] = "unknown"
        elif row["binding"] != load["binding"]:
            verdicts[request_id] = "conflict"
        else:
            verdicts[request_id] = (
                "compliant"
                if row["binding"]["coefficient"] == load["intended"]
                and row["binding"]["helper_sha256"] == load["expected_helper_sha256"]
                else "violation"
            )
    # Exact parent census and terminal request census are separate obligations.
    closure = len(closures) == 1 and closures[0]["requests"] == requests
    return {"verdicts": verdicts, "closure": closure}
