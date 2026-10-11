"""Bounded real SQLite receipt retention; no model loader or reference access.

The trusted request-drained barrier closes future delivery/descendants, not the
audit horizon. A finite horizon is counted in subsequently drained requests.
Exact buffer hashing is upstream and identical for every collector configuration.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from aletheia_lab.evaluation.model_load_contract import LoadContract, Observation, Record, Scope
from aletheia_lab.evaluation.model_load_evidence_analysis import sufficient_records
from aletheia_lab.evaluation.model_load_validation_lifecycle import observation

Selection = Literal["full", "static_sufficient"]
Routing = Literal["mailbox", "trusted_scope"]


def encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class ReceiptStore:
    """One fixed selector and lifecycle rule, not a purported adaptive optimizer.

    This store receives trusted Records, not arbitrary authenticated JSON. Scope
    indexing is a delivery capability distinct from mailbox-only retrieval. It
    cannot restore deleted receipts. Conflicting variants remain separate rows;
    identical duplicate delivery is deduplicated without merging occurrences.
    """

    def __init__(
        self,
        path: Path,
        *,
        selection: Selection = "static_sufficient",
        routing: Routing = "trusted_scope",
        root_lifetime: bool = True,
        horizon: int = 2,
    ) -> None:
        if (
            selection not in {"full", "static_sufficient"}
            or routing not in {"mailbox", "trusted_scope"}
            or type(root_lifetime) is not bool
            or type(horizon) is not int
            or not 0 <= horizon <= 8
        ):
            raise ValueError("collector configuration outside bounded development domain")
        if path.exists() or path.is_symlink() or not path.parent.is_dir():
            raise ValueError("collector needs a new local database")
        self.selection, self.routing = selection, routing
        self.root_lifetime, self.horizon = root_lifetime, horizon
        self.path, self.db = path, sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE receipt(seq INTEGER PRIMARY KEY, owner TEXT, request TEXT, attempt INTEGER,"
            "mailbox TEXT, delayed INTEGER, payload TEXT, UNIQUE(owner,payload,mailbox))"
        )
        self.db.execute("CREATE INDEX scope_index ON receipt(request,attempt)")
        self.drained: dict[str, int] = {}
        self.retired: set[str] = set()
        self.targets: dict[str, Scope] = {}
        self.tick = self.completions = 0
        self.metrics: dict[str, int] = dict.fromkeys(
            (
                "capture_events",
                "capture_payload_bytes",
                "inserted_records",
                "written_payload_bytes",
                "duplicate_deliveries",
                "filtered_records",
                "deleted_records",
                "sql_reads",
                "sql_writes",
                "query_payload_bytes",
                "peak_live_payload_bytes",
                "live_payload_byte_steps",
                "peak_database_bytes",
                "peak_journal_bytes",
                "collector_operation_ns",
                "peak_bookkeeping_bytes",
                "bookkeeping_byte_steps",
            ),
            0,
        )
        self.sample()

    def sample(self, *, advance: bool = False) -> None:
        # Resource instrumentation is not counted as application query work.
        size = int(
            self.db.execute(
                "SELECT coalesce(sum(length(CAST(payload AS BLOB))),0) FROM receipt"
            ).fetchone()[0]
        )
        state = len(
            encode(
                {
                    "drained": self.drained,
                    "retired": sorted(self.retired),
                    "targets": {key: asdict(value) for key, value in self.targets.items()},
                    "completions": self.completions,
                }
            ).encode()
        )
        self.metrics["peak_live_payload_bytes"] = max(self.metrics["peak_live_payload_bytes"], size)
        self.metrics["peak_bookkeeping_bytes"] = max(self.metrics["peak_bookkeeping_bytes"], state)
        self.metrics["peak_database_bytes"] = max(
            self.metrics["peak_database_bytes"], self.path.stat().st_size
        )
        journal = Path(str(self.path) + "-journal")
        if journal.exists():
            self.metrics["peak_journal_bytes"] = max(
                self.metrics["peak_journal_bytes"], journal.stat().st_size
            )
        if advance:
            self.metrics["live_payload_byte_steps"] += size
            self.metrics["bookkeeping_byte_steps"] += state
            self.tick += 1

    def submit(
        self, record: Record, target: Scope, *, delayed: bool = False, mailbox: str | None = None
    ) -> None:
        if record.scope.request in self.drained or record.scope.request in self.retired:
            raise ValueError("delivery after authoritative request drain")
        if target.request in self.drained or target.request in self.retired:
            raise ValueError("target request has drained")
        if target.request in self.targets and self.targets[target.request] != target:
            raise ValueError("pilot supports one declared query attempt per request")
        raw = encode(asdict(record))
        if (
            type(delayed) is not bool
            or mailbox is not None
            and (type(mailbox) is not str or not mailbox or len(mailbox) > 100)
        ):
            raise ValueError("invalid trusted delivery envelope")
        if len(raw.encode()) > 8192 or self.metrics["capture_events"] >= 4096:
            raise ValueError("bounded collector input allowance exceeded")
        if len(self.targets) + len(self.retired) + int(target.request not in self.targets) > 64:
            raise ValueError("bounded request census exceeded")
        self.targets[target.request] = target
        self.metrics["capture_events"] += 1
        self.metrics["capture_payload_bytes"] += len(raw.encode())
        # Retain roots before any later inheritance query is known. Only root
        # loads/closures outside the declared target attempt are irrelevant here.
        keep = (
            self.selection == "full"
            or record.scope == target
            or (
                record.scope.request == target.request
                and record.scope.attempt == 0
                and record.kind == "selection"
            )
        )
        started = time.perf_counter_ns()
        if keep:
            destination = record.scope.request if mailbox is None else mailbox
            existing = self.db.execute(
                "SELECT seq,delayed FROM receipt WHERE owner=? AND payload=? AND mailbox=?",
                (target.request, raw, destination),
            ).fetchone()
            self.metrics["sql_reads"] += 1
            if existing is None:
                self.db.execute(
                    "INSERT INTO receipt(owner,request,attempt,mailbox,delayed,payload) VALUES(?,?,?,?,?,?)",
                    (
                        target.request,
                        record.scope.request,
                        record.scope.attempt,
                        destination,
                        int(delayed),
                        raw,
                    ),
                )
                self.metrics["sql_writes"] += 1
                self.metrics["inserted_records"] += 1
                self.metrics["written_payload_bytes"] += len(raw.encode())
            else:
                self.metrics["duplicate_deliveries"] += 1
                if existing[1] and not delayed:
                    self.db.execute("UPDATE receipt SET delayed=0 WHERE seq=?", (existing[0],))
                    self.metrics["sql_writes"] += 1
            self.sample()  # Includes the actual rollback journal while open.
            self.db.commit()
        else:
            self.metrics["filtered_records"] += 1
            self.sample()
        self.metrics["collector_operation_ns"] += time.perf_counter_ns() - started

    def _delete(self, request: str, *, root_only: bool = False) -> None:
        started = time.perf_counter_ns()
        if root_only:
            cursor = self.db.execute(
                "DELETE FROM receipt WHERE request=? AND attempt=0", (request,)
            )
        else:
            cursor = self.db.execute("DELETE FROM receipt WHERE owner=?", (request,))
        self.metrics["deleted_records"] += cursor.rowcount
        self.metrics["sql_writes"] += 1
        self.sample()
        self.db.commit()
        self.metrics["collector_operation_ns"] += time.perf_counter_ns() - started

    def expire_root(self, scope: Scope) -> None:
        if scope.attempt != 0:
            raise ValueError("root expiry requires root scope")
        if self.root_lifetime:
            self.sample()  # A live request may still need its root after attempt closure.
        else:
            self._delete(scope.request, root_only=True)

    def release(self, request: str) -> None:
        if request in self.drained or request in self.retired:
            raise ValueError("release after request drain")
        started = time.perf_counter_ns()
        self.db.execute("UPDATE receipt SET delayed=0 WHERE owner=?", (request,))
        self.metrics["sql_writes"] += 1
        self.sample()
        self.db.commit()
        self.metrics["collector_operation_ns"] += time.perf_counter_ns() - started

    def read(self, contract: LoadContract, scope: Scope) -> Observation:
        if scope.request in self.retired:
            raise ValueError("requested evidence is outside the declared audit horizon")
        if self.targets.get(scope.request) != scope:
            raise ValueError("query outside the declared target attempt")
        started = time.perf_counter_ns()
        if self.routing == "mailbox":
            rows = self.db.execute(
                "SELECT payload FROM receipt WHERE mailbox=? AND delayed=0 ORDER BY seq",
                (scope.request,),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT payload FROM receipt WHERE request=? AND delayed=0 ORDER BY seq",
                (scope.request,),
            ).fetchall()
        self.metrics["sql_reads"] += 1
        self.metrics["query_payload_bytes"] += sum(len(raw.encode()) for (raw,) in rows)
        obs = observation(
            {
                "contract": asdict(contract),
                "scope": asdict(scope),
                "records": [json.loads(raw) for (raw,) in rows],
            }
        )
        if self.selection == "static_sufficient":
            obs = Observation(contract, scope, sufficient_records(obs))
        self.metrics["collector_operation_ns"] += time.perf_counter_ns() - started
        self.sample()
        return obs

    def drain(self, request: str) -> None:
        """Caller barrier: no further deliveries/descendants; queries have finite lifetime."""
        if request in self.drained or request in self.retired:
            raise ValueError("request drained twice")
        if request not in self.targets:
            raise ValueError("request drain lacks its declared query target")
        pending = self.db.execute(
            "SELECT count(*) FROM receipt WHERE owner=? AND delayed=1", (request,)
        ).fetchone()[0]
        self.metrics["sql_reads"] += 1
        if pending:
            raise ValueError("request has pending deliveries")
        self.completions += 1
        self.drained[request] = self.completions
        expired = [r for r, tick in self.drained.items() if self.completions - tick >= self.horizon]
        for retired in expired:
            self._delete(retired)
            self.retired.add(retired)
            del self.drained[retired]
            del self.targets[retired]
        self.sample()

    def finish(self) -> dict[str, Any]:
        self.db.commit()
        live = self.db.execute(
            "SELECT coalesce(sum(length(CAST(payload AS BLOB))),0) FROM receipt"
        ).fetchone()[0]
        result = {
            **self.metrics,
            "final_live_payload_bytes": int(live),
            "final_database_bytes": self.path.stat().st_size,
            "sample_count": self.tick,
            "query_horizon_drained_requests": self.horizon,
            "bookkeeping_included": True,
            "raw_buffer_archive_bytes": 0,
            "cost_scope": "collector receipts and serialized bookkeeping; Python allocator excluded",
        }
        return result

    def close(self) -> None:
        self.db.close()
