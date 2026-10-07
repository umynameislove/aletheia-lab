"""Declared recovery tier and complete offered-obligation accounting.

Producer ticks order authored arrivals, not wall deadlines. Actual synchronous
audit latency is compared to a prospectively fixed development response budget.
No observer timing is represented as a production SLA or concurrent throughput.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from aletheia_lab.evaluation.incident_audit_archive import IncidentAuditArchive
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_audit import resolve
from aletheia_lab.project.identity import content_sha256


class RecoveryTier:
    """Measured secondary capture store; never a free reference oracle."""

    def __init__(self, path: Path, quota: int) -> None:
        self.path, self.quota = path, quota
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE evidence(scope TEXT PRIMARY KEY, payload TEXT, digest TEXT)")
        self.db.commit()
        self.bytes = self.write_ns = self.read_ns = self.reads = self.retrieved_bytes = 0
        self.peak_physical = 0

    def put(self, frame: dict[str, Any]) -> None:
        raw = encode(frame)
        size = len(raw.encode()) + len(frame["token"].encode()) + 64
        if self.bytes + size > self.quota:
            raise ValueError("declared secondary quota exhausted")
        started = perf_counter_ns()
        with self.db:
            self.db.execute(
                "INSERT INTO evidence VALUES(?,?,?)",
                (frame["token"], raw, content_sha256(raw.encode())),
            )
        self.write_ns += perf_counter_ns() - started
        self.bytes += size
        self.peak_physical = max(
            self.peak_physical,
            sum(
                p.stat().st_size
                for suffix in ("", "-wal", "-shm")
                if (p := Path(str(self.path) + suffix)).exists()
            ),
        )

    def get(self, scope: str) -> dict[str, Any] | None:
        started = perf_counter_ns()
        row = self.db.execute(
            "SELECT payload,digest FROM evidence WHERE scope=?", (scope,)
        ).fetchone()
        self.read_ns += perf_counter_ns() - started
        self.reads += 1
        if row is None:
            return None
        raw, digest = row
        if content_sha256(raw.encode()) != digest:
            raise ValueError("secondary revision digest differs")
        self.retrieved_bytes += len(raw.encode())
        frame: dict[str, Any] = json.loads(raw)
        resolve(frame)
        return frame

    def meters(self) -> dict[str, int]:
        return {
            "read_ns": self.read_ns,
            "reads": self.reads,
            "retrieved_bytes": self.retrieved_bytes,
        }

    def finish(self) -> dict[str, Any]:
        self.db.close()
        return {
            "logical_evidence_and_key_bytes": self.bytes,
            "write_ns": self.write_ns,
            **self.meters(),
            "peak_db_wal_shm_bytes": self.peak_physical,
            "closed_db_bytes": self.path.stat().st_size,
            "sha256": content_sha256(self.path.read_bytes()),
        }


def offer(
    archive: IncidentAuditArchive,
    tier: str,
    recovery: RecoveryTier,
    item: dict[str, Any],
    response_budget_ns: int,
) -> dict[str, Any]:
    now, scopes = item["offered_at"], item["scopes"]
    started = perf_counter_ns()
    before = recovery.meters()
    initial = archive.query(scopes, now=now)
    missing = [scope for scope, verdict in initial.items() if verdict is None]
    recovered: list[str] = []
    refetch_failed: list[str] = []
    frames = []
    for scope in missing:
        frame = recovery.get(scope) if tier == "durable_secondary" else None
        if frame is None:
            refetch_failed.append(scope)
        else:
            frames.append(frame)
    # A strong ordinary baseline installs the entire required union atomically.
    if frames and not refetch_failed:
        if archive.restore_union(frames, now=now, required=scopes):
            recovered = [frame["token"] for frame in frames]
        else:
            refetch_failed = [frame["token"] for frame in frames]
    elif frames:
        refetch_failed.extend(frame["token"] for frame in frames)
    accepted = archive.demand(item["id"], scopes, now=now, until=item["deadline"])
    answers = archive.query(scopes, now=now)
    witness = archive.witness(scopes)
    if accepted:
        archive.drain(item["id"], now=now)
    duration = perf_counter_ns() - started
    after = recovery.meters()
    return {
        **item,
        "accepted": accepted,
        "answers": answers,
        "queried_at": now,
        "query_wall_ns": duration,
        "response_budget_ns": response_budget_ns,
        "refetched": recovered,
        "refetch_failed": refetch_failed,
        "recovery_meter": {key: after[key] - value for key, value in before.items()},
        "witness": witness,
    }


def summarize(offers: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, int]:
    truth = {row["frame"]["token"]: row["reference"] for row in rows}
    result: Counter[str] = Counter()
    for item in offers:
        result["offered"] += 1
        result["accepted" if item["accepted"] else "refused"] += 1
        values = item["answers"]
        wrong = any(
            answer not in {None, "unknown", truth[scope]} for scope, answer in values.items()
        )
        conclusive = all(
            answer in {"compliant", "violation"} and answer == truth[scope]
            for scope, answer in values.items()
        )
        on_time = (
            item["queried_at"] <= item["deadline"]
            and item["query_wall_ns"] <= item["response_budget_ns"]
        )
        result["wrong"] += int(wrong)
        result["correct_complete"] += int(conclusive)
        result["on_time_correct"] += int(conclusive and on_time)
        result["deadline_misses"] += int(not on_time)
        result["unknown_or_missing"] += int(not conclusive and not wrong)
        result["accepted_but_unserved"] += int(item["accepted"] and not (conclusive and on_time))
        result["refetched_scopes"] += len(item["refetched"])
        result["refetch_failures"] += len(item["refetch_failed"])
    return dict(result)
