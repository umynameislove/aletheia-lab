"""A separately charged durable tier for original captured audit capsules."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from time import perf_counter_ns
from typing import Any, cast

from aletheia_lab.evaluation.calibration_audit_archive import assess
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_audit import digest


class RecoveryTier:
    """No truth tape: store before eviction, reopen and fetch the original token."""

    def __init__(self, path: Path, *, reopen: bool = False) -> None:
        if path.exists() != reopen or path.parent.is_symlink() or not path.parent.is_dir():
            raise ValueError("owned tier creation/reopen required")
        if any(Path(str(path) + suffix).is_symlink() for suffix in ("", "-wal", "-shm")):
            raise ValueError("tier must not follow symlinks")
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.write_ns = self.read_ns = self.payload_bytes = self.peak_bytes = 0
        if not reopen:
            self.db.execute(
                "CREATE TABLE packets(token TEXT PRIMARY KEY,identity TEXT,payload BLOB)"
            )
            self.db.commit()

    def put(self, packet: dict[str, Any]) -> None:
        assess(packet)
        raw = encode(packet).encode()
        started = perf_counter_ns()
        with self.db:
            self.db.execute(
                "INSERT INTO packets VALUES(?,?,?)", (packet["token"], digest(packet), raw)
            )
        self.write_ns += perf_counter_ns() - started
        self.payload_bytes += len(raw)
        self.peak_bytes = max(self.peak_bytes, self.storage_bytes())

    def fetch(self, token: str) -> dict[str, Any] | None:
        started = perf_counter_ns()
        row = self.db.execute(
            "SELECT identity,payload FROM packets WHERE token=?", (token,)
        ).fetchone()
        self.read_ns += perf_counter_ns() - started
        if row is None:
            return None
        packet = json.loads(bytes(row[1]))
        if (
            packet["token"] != token
            or digest(packet) != row[0]
            or encode(packet).encode() != bytes(row[1])
        ):
            raise ValueError("recovery identity differs")
        assess(packet)
        return cast(dict[str, Any], packet)

    def storage_bytes(self) -> int:
        return sum(
            candidate.stat().st_size
            for suffix in ("", "-wal", "-shm")
            if (candidate := Path(str(self.path) + suffix)).exists()
        )

    def close(self) -> None:
        self.db.close()
