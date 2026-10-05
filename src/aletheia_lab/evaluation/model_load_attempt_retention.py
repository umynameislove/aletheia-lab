"""Bounded multi-attempt receipt retention with atomic snapshot/lifetime admission.

All query attempts are declared before capture. The local caller is trusted to
close producers before draining a request; this is not a distributed watermark.
One lock serializes SQLite and lifecycle state, not native model deserializers.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.model_load_contract import LoadContract, Observation, Record, Scope
from aletheia_lab.evaluation.model_load_evidence_analysis import sufficient_records
from aletheia_lab.evaluation.model_load_retention import Selection, encode
from aletheia_lab.evaluation.model_load_validation_lifecycle import observation
from aletheia_lab.project.identity import content_sha256


class AttemptReceiptStore:
    """A new development collector; historical single-attempt stores are unchanged."""

    def __init__(self, path: Path, *, selection: Selection, horizon: int = 2) -> None:
        if selection not in {"full", "static_sufficient"} or type(horizon) is not int:
            raise ValueError("invalid selection/horizon")
        if not 0 <= horizon <= 8 or path.exists() or path.is_symlink():
            raise ValueError("new database and bounded horizon required")
        self.path, self.selection, self.horizon = path, selection, horizon
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE receipt(owner TEXT, payload TEXT, delayed INTEGER, UNIQUE(owner,payload))"
        )
        self.scopes: dict[str, tuple[Scope, ...]] = {}
        self.queries: dict[str, tuple[Scope, ...]] = {}
        self.closed: set[Scope] = set()
        self.drained: dict[str, int] = {}
        self.retired: set[str] = set()
        self.pending: set[tuple[str, str]] = set()
        self.completions = 0
        self.metrics: dict[str, int] = dict.fromkeys(
            (
                "captures",
                "captured_bytes",
                "inserts",
                "written_bytes",
                "duplicates",
                "filtered",
                "reads",
                "query_bytes",
                "deleted",
                "peak_payload_bytes",
                "payload_byte_steps",
                "peak_bookkeeping_bytes",
                "bookkeeping_byte_steps",
                "samples",
                "peak_database_bytes",
            ),
            0,
        )

    def register(self, scopes: tuple[Scope, ...], queries: tuple[Scope, ...]) -> None:
        """Declare an immutable query service and producer census, including root."""
        with self.lock:
            if type(scopes) is not tuple or type(queries) is not tuple:
                raise ValueError("immutable scope/query tuples required")
            if not scopes or not queries or len(set(scopes)) != len(scopes):
                raise ValueError("nonempty unique attempt census required")
            request = scopes[0].request
            if request in self.scopes or len(self.scopes) >= 64:
                raise ValueError("duplicate/out-of-budget request registration")
            if any(scope.request != request for scope in scopes) or Scope(request, 0) not in scopes:
                raise ValueError("all attempts must share a declared root")
            if len(set(queries)) != len(queries) or not set(queries) <= set(scopes):
                raise ValueError("queries must be a unique subset of registered attempts")
            if sum(map(len, self.scopes.values())) + len(scopes) > 128:
                raise ValueError("attempt census exceeded")
            self.scopes[request], self.queries[request] = scopes, queries

    def _live(self, request: str) -> None:
        if request not in self.scopes or request in self.drained:
            raise ValueError("request is unregistered or authoritatively drained")

    def submit(self, owner: str, record: Record, *, delayed: bool = False) -> None:
        with self.lock:
            self._live(owner)
            raw = encode(asdict(record))
            size = len(raw.encode())
            if type(delayed) is not bool or size > 8192 or self.metrics["captures"] >= 4096:
                raise ValueError("bounded trusted delivery allowance exceeded")
            self.metrics["captures"] += 1
            self.metrics["captured_bytes"] += size
            delivery = (owner, content_sha256(raw.encode()))
            # Queue completion is separate from receipt availability: even an
            # already visible duplicate needs its explicit release barrier.
            if delayed:
                self.pending.add(delivery)
            else:
                self.pending.discard(delivery)
            keep = self.selection == "full" or record.scope in self.queries[owner]
            keep = keep or (record.scope == Scope(owner, 0) and record.kind == "selection")
            if not keep:
                self.metrics["filtered"] += 1
                return
            with self.db:
                row = self.db.execute(
                    "SELECT delayed FROM receipt WHERE owner=? AND payload=?", (owner, raw)
                ).fetchone()
                if row is None:
                    self.db.execute("INSERT INTO receipt VALUES(?,?,?)", (owner, raw, int(delayed)))
                    self.metrics["inserts"] += 1
                    self.metrics["written_bytes"] += size
                else:
                    self.metrics["duplicates"] += 1
                    if row[0] and not delayed:
                        self.db.execute(
                            "UPDATE receipt SET delayed=0 WHERE owner=? AND payload=?", (owner, raw)
                        )

    def release(self, request: str) -> None:
        with self.lock, self.db:
            self._live(request)
            self.db.execute("UPDATE receipt SET delayed=0 WHERE owner=?", (request,))
            self.pending = {entry for entry in self.pending if entry[0] != request}

    def settle(self, scope: Scope) -> None:
        """Producer completion does not delete roots or forbid delayed delivery."""
        with self.lock:
            self._live(scope.request)
            if scope not in self.scopes[scope.request] or scope in self.closed:
                raise ValueError("unknown/already settled attempt")
            self.closed.add(scope)

    def snapshot(self, contract: LoadContract, scope: Scope) -> Observation:
        """Copy all rows under the same lock used for retirement; no live cursor escapes."""
        with self.lock:
            if scope.request in self.retired:
                raise ValueError("audit horizon expired")
            if scope not in self.queries.get(scope.request, ()):
                raise ValueError("query outside declared attempt service")
            rows = self.db.execute(
                "SELECT payload FROM receipt WHERE owner=? AND delayed=0 ORDER BY rowid",
                (scope.request,),
            ).fetchall()
            self.metrics["reads"] += 1
            self.metrics["query_bytes"] += sum(len(raw.encode()) for (raw,) in rows)
            obs = observation(
                {
                    "contract": asdict(contract),
                    "scope": asdict(scope),
                    "records": [json.loads(raw) for (raw,) in rows],
                }
            )
            return (
                Observation(contract, scope, sufficient_records(obs))
                if self.selection != "full"
                else obs
            )

    def drain(self, request: str) -> None:
        """Trusted no-future-delivery barrier; expire after subsequent drained requests."""
        with self.lock, self.db:
            self._live(request)
            if not set(self.scopes[request]) <= self.closed:
                raise ValueError("request still has unsettled attempts")
            if any(owner == request for owner, _ in self.pending):
                raise ValueError("request still has delayed deliveries")
            self.completions += 1
            self.drained[request] = self.completions
            for owner, completed in self.drained.items():
                if owner not in self.retired and self.completions - completed >= self.horizon:
                    cursor = self.db.execute("DELETE FROM receipt WHERE owner=?", (owner,))
                    self.metrics["deleted"] += cursor.rowcount
                    self.retired.add(owner)

    def sample(self) -> dict[str, int]:
        """Untimed logical/storage instrumentation, not allocator or RSS measurement."""
        with self.lock:
            payload = int(
                self.db.execute(
                    "SELECT coalesce(sum(length(CAST(payload AS BLOB))),0) FROM receipt"
                ).fetchone()[0]
            )
            state = len(
                encode(
                    {
                        "scopes": {k: [asdict(s) for s in v] for k, v in self.scopes.items()},
                        "queries": {k: [asdict(s) for s in v] for k, v in self.queries.items()},
                        "closed": sorted((s.request, s.attempt) for s in self.closed),
                        "pending": sorted(self.pending),
                        "drained": self.drained,
                        "retired": sorted(self.retired),
                    }
                ).encode()
            )
            for name, size in (("payload", payload), ("bookkeeping", state)):
                key = f"peak_{name}_bytes"
                self.metrics[key] = max(self.metrics[key], size)
                self.metrics[f"{name}_byte_steps"] += size
            self.metrics["samples"] += 1
            self.metrics["peak_database_bytes"] = max(
                self.metrics["peak_database_bytes"], self.path.stat().st_size
            )
            return {**self.metrics, "live_payload_bytes": payload, "bookkeeping_bytes": state}

    def finish(self) -> dict[str, Any]:
        with self.lock:
            result = {
                "selection": self.selection,
                "horizon": self.horizon,
                **self.sample(),
                "request_count": len(self.scopes),
                "attempt_count": sum(map(len, self.scopes.values())),
                "query_attempt_count": sum(map(len, self.queries.values())),
                "retired_request_count": len(self.retired),
            }
            self.db.close()
            return result
