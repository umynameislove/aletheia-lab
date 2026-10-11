"""Serial serving receipts with matched durability and dependency retention.

Both arms commit one completed operation at a time using WAL and FULL sync.
The caller admits typed checker frames; serialization freezes their contents.
Only the full arm additionally retains ordinary application detail, never model
buffers. Resource peaks are sampled logical/file sizes, not allocator or RSS.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, cast

from aletheia_lab.evaluation.model_load_retention import encode


def _scope(value: str) -> None:
    if type(value) is not str or not value or len(value) > 100:
        raise ValueError("invalid serving scope")


def _document(value: dict[str, Any]) -> str:
    if type(value) is not dict:
        raise ValueError("serving frame/detail must be a JSON object")
    try:
        return encode(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("serving frame/detail is not finite JSON") from exc


def _decode(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("stored serving frame is invalid JSON") from exc
    if type(value) is not dict or _document(value) != raw:
        raise ValueError("stored serving frame is not a canonical JSON object")
    return cast(dict[str, Any], value)


class ServingStore:
    """Long-lived collector for one serial workload and one active resident pin.

    Audit service is inclusive through ``step + horizon``. Retire only after
    current-step queries; expired ancestors remain while a surviving descendant
    or the active resident object needs them. Query returns detached JSON values.
    """

    def __init__(self, path: Path, arm: str, horizon: int) -> None:
        if arm not in {"static", "full"} or type(horizon) is not int or horizon not in {0, 2, 8}:
            raise ValueError("invalid serving arm/horizon")
        if path.exists() or path.is_symlink() or not path.parent.is_dir():
            raise ValueError("serving collector requires a new local database")
        self.path, self.arm, self.horizon = path, arm, horizon
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA foreign_keys=ON")
        if self.db.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
            self.db.close()
            raise ValueError("serving collector requires WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE receipt(scope TEXT PRIMARY KEY, step INTEGER NOT NULL, "
            "expires INTEGER NOT NULL, frame TEXT NOT NULL, parent TEXT, detail TEXT NOT NULL, "
            "FOREIGN KEY(parent) REFERENCES receipt(scope) DEFERRABLE INITIALLY DEFERRED)"
        )
        self.db.execute("CREATE INDEX receipt_parent ON receipt(parent)")
        self.db.execute("CREATE INDEX receipt_expiry ON receipt(expires)")
        self.db.commit()
        self.active_parent: str | None = None
        self.latest_step = -1
        self.retired_step = -1
        self.closed = False
        self.metrics = dict.fromkeys(
            (
                "appended_rows",
                "deleted_rows",
                "append_commits",
                "retire_commits",
                "queries",
                "samples",
                "peak_rows",
                "peak_frame_bytes",
                "peak_detail_bytes",
                "peak_database_bytes",
                "peak_wal_bytes",
                "peak_shm_bytes",
                "peak_storage_bytes",
                "checkpoint_ns",
            ),
            0,
        )
        self.last_stats: dict[str, Any] = {}
        self.stats()

    def _open(self) -> None:
        if self.closed:
            raise ValueError("serving collector is closed")

    def append(
        self,
        scope: str,
        step: int,
        frame: dict[str, Any],
        parent: str | None,
        detail: dict[str, Any],
    ) -> None:
        """Commit one immutable completed-operation frame in either arm."""
        self._open()
        _scope(scope)
        if type(step) is not int or step < self.latest_step or step <= self.retired_step:
            raise ValueError("serving operations require nondecreasing unretired steps")
        raw = _document(frame)
        if type(detail) is not dict:
            raise ValueError("serving frame/detail must be a JSON object")
        details = _document(detail) if self.arm == "full" else "{}"
        if parent is not None:
            _scope(parent)
            if self.db.execute("SELECT 1 FROM receipt WHERE scope=?", (parent,)).fetchone() is None:
                raise ValueError("serving frame has no retained parent")
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO receipt VALUES(?,?,?,?,?,?)",
                    (
                        scope,
                        step,
                        step + self.horizon,
                        raw,
                        parent,
                        details,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("duplicate scope or invalid serving parent") from exc
        self.latest_step = step
        self.metrics["appended_rows"] += 1
        self.metrics["append_commits"] += 1

    def keep_parent(self, scope: str | None) -> None:
        """Pin the current resident object's load evidence before retirement."""
        self._open()
        if scope is not None:
            _scope(scope)
            if self.db.execute("SELECT 1 FROM receipt WHERE scope=?", (scope,)).fetchone() is None:
                raise ValueError("active resident parent is unavailable")
        self.active_parent = scope

    def _parents(self, scope: str) -> tuple[str, str | None]:
        row = self.db.execute("SELECT frame,parent FROM receipt WHERE scope=?", (scope,)).fetchone()
        if row is None:
            raise ValueError("retained serving frame has a missing parent")
        return cast(tuple[str, str | None], row)

    def query(self, scope: str) -> dict[str, Any] | None:
        self._open()
        _scope(scope)
        self.metrics["queries"] += 1
        row = self.db.execute("SELECT frame,parent FROM receipt WHERE scope=?", (scope,)).fetchone()
        if row is None:
            return None
        raw, parent = row
        frame = _decode(raw)
        ancestors: set[str] = {scope}
        current, parent_frame = parent, None
        while current is not None:
            if current in ancestors:
                raise ValueError("retained serving frames have cyclic parents")
            ancestors.add(current)
            ancestor_raw, next_parent = self._parents(current)
            ancestor = _decode(ancestor_raw)
            if current == parent:
                parent_frame = ancestor
            current = next_parent
        return {"frame": frame, "parent": parent, "parent_frame": parent_frame}

    def retire(self, current_step: int) -> None:
        """Delete expired rows except the complete ancestry of live rows/pin."""
        self._open()
        if type(current_step) is not int or current_step < max(
            self.latest_step, self.retired_step, 0
        ):
            raise ValueError("retirement must follow current-step queries")
        # External tampering must never turn a missing ancestor into a false binding.
        if (
            self.db.execute(
                "SELECT 1 FROM receipt child LEFT JOIN receipt parent ON child.parent=parent.scope "
                "WHERE child.parent IS NOT NULL AND parent.scope IS NULL LIMIT 1"
            ).fetchone()
            is not None
        ):
            raise ValueError("retained serving frame has a missing parent")
        if self.active_parent is not None:
            self._parents(self.active_parent)
        with self.db:
            self.db.execute(
                "WITH RECURSIVE needed(scope) AS ("
                "SELECT scope FROM receipt WHERE expires>=? UNION SELECT ? WHERE ? IS NOT NULL "
                "UNION SELECT r.parent FROM receipt r JOIN needed n ON r.scope=n.scope "
                "WHERE r.parent IS NOT NULL) "
                "DELETE FROM receipt WHERE scope NOT IN (SELECT scope FROM needed)",
                (current_step, self.active_parent, self.active_parent),
            )
            deleted = self.db.execute("SELECT changes()").fetchone()[0]
        self.retired_step = current_step
        self.metrics["deleted_rows"] += deleted
        self.metrics["retire_commits"] += 1

    def _storage(self) -> dict[str, int]:
        sizes: dict[str, int] = {}
        for name, suffix in (("database", ""), ("wal", "-wal"), ("shm", "-shm")):
            path = Path(str(self.path) + suffix)
            sizes[f"{name}_bytes"] = path.stat().st_size if path.exists() else 0
            key = f"peak_{name}_bytes"
            self.metrics[key] = max(self.metrics[key], sizes[f"{name}_bytes"])
        sizes["storage_bytes"] = sum(sizes.values())
        self.metrics["peak_storage_bytes"] = max(
            self.metrics["peak_storage_bytes"], sizes["storage_bytes"]
        )
        return sizes

    def stats(self) -> dict[str, Any]:
        """Sample resource usage separately from timed append/query/retire work."""
        self._open()
        rows, frames, details, keys = self.db.execute(
            "SELECT count(*),coalesce(sum(length(CAST(frame AS BLOB))),0),"
            "coalesce(sum(length(CAST(detail AS BLOB))),0),"
            "coalesce(sum(length(CAST(scope AS BLOB))+coalesce(length(CAST(parent AS BLOB)),0)+16),0) "
            "FROM receipt"
        ).fetchone()
        for name, value in (("rows", rows), ("frame_bytes", frames), ("detail_bytes", details)):
            key = f"peak_{name}"
            self.metrics[key] = max(self.metrics[key], value)
        self.metrics["samples"] += 1
        self.last_stats = {
            "arm": self.arm,
            "horizon": self.horizon,
            "rows": rows,
            "logical_frame_bytes": frames,
            "logical_detail_bytes": details,
            "logical_payload_bytes": frames + details,
            "bookkeeping_key_bytes": keys,
            "bookkeeping_index_rows": 3 * rows,
            "active_parent": self.active_parent,
            **self._storage(),
            **self.metrics,
        }
        return dict(self.last_stats)

    def checkpoint(self) -> int:
        """Return actual FULL checkpoint duration; checkpoint policy is shared."""
        self._open()
        self._storage()
        started = time.perf_counter_ns()
        result = self.db.execute("PRAGMA wal_checkpoint(FULL)").fetchone()
        elapsed = time.perf_counter_ns() - started
        self.metrics["checkpoint_ns"] += elapsed
        if result[0] != 0:
            raise ValueError("serving checkpoint is blocked by a reader")
        self._storage()
        return elapsed

    def close(self) -> int:
        """Return measured checkpoint plus connection-close cost."""
        self._open()
        self.stats()
        elapsed = self.checkpoint()
        started = time.perf_counter_ns()
        self.db.close()
        elapsed += time.perf_counter_ns() - started
        self.closed = True
        self.last_stats.update(self._storage())
        self.last_stats.update(self.metrics)
        return elapsed
