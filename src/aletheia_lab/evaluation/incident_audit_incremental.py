"""Ordinary delta persistence with the existing logical/service contract.

Global validation, charge computation and owner readback are deliberately retained.
This is an incremental SQL baseline, not an optimal incremental CPU algorithm.
"""

from __future__ import annotations

import json
from time import perf_counter_ns
from typing import Any

from aletheia_lab.evaluation.incident_audit_archive import IncidentAuditArchive
from aletheia_lab.evaluation.model_load_retention import encode

MAPS = ("atoms", "entries", "leases", "pending")


class IncrementalAuditArchive(IncidentAuditArchive):
    """Atomically persist only changed keyed rows; reuse admission and leases."""

    @staticmethod
    def _rows(state: dict[str, Any]) -> dict[tuple[str, str], bytes]:
        document = json.loads(IncidentAuditArchive._document(state))
        rows = {
            (group, key): encode(value).encode()
            for group in MAPS
            for key, value in document.pop(group).items()
        }
        rows[("metadata", "singleton")] = encode(document).encode()
        return rows

    def _read(self) -> dict[str, Any]:
        started = perf_counter_ns()
        rows = self.db.execute("SELECT family,key,payload FROM fragments").fetchall()
        document: dict[str, Any] = {group: {} for group in MAPS}
        metadata = False
        for family, key, raw in rows:
            value = json.loads(raw)
            if encode(value).encode() != bytes(raw):
                raise ValueError("incremental row is noncanonical")
            if family == "metadata" and key == "singleton" and not metadata:
                if set(value) & set(MAPS):
                    raise ValueError("metadata contains a fragment map")
                document.update(value)
                metadata = True
            elif family in MAPS:
                document[family][key] = value
            else:
                raise ValueError("unexpected incremental row")
        if not metadata:
            raise ValueError("incremental metadata missing")
        result = self._parse(document)
        self._validate(result)
        self.metrics["read_ns"] += perf_counter_ns() - started
        return result

    def _commit(self) -> None:
        self._validate(self.state)
        current = self._rows(self.state)
        exists = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fragments'"
        ).fetchone()
        previous = (
            {
                (family, key): bytes(raw)
                for family, key, raw in self.db.execute("SELECT family,key,payload FROM fragments")
            }
            if exists
            else {}
        )
        changed = [
            (family, key, raw)
            for (family, key), raw in current.items()
            if previous.get((family, key)) != raw
        ]
        removed = sorted(set(previous) - set(current))
        started = perf_counter_ns()
        with self.db:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS fragments("
                "family TEXT NOT NULL,key TEXT NOT NULL,payload BLOB NOT NULL,"
                "PRIMARY KEY(family,key)) WITHOUT ROWID"
            )
            self.db.executemany("DELETE FROM fragments WHERE family=? AND key=?", removed)
            self.db.executemany("INSERT OR REPLACE INTO fragments VALUES(?,?,?)", changed)
        self.metrics["write_ns"] += perf_counter_ns() - started
        self.metrics["commits"] += 1
        self.metrics["written_bytes"] += sum(len(raw) for _, _, raw in changed)
        self.metrics["peak_charge"] = max(self.metrics["peak_charge"], self._charge(self.state))
        try:
            self.metrics["peak_db_wal_shm_bytes"] = max(
                self.metrics["peak_db_wal_shm_bytes"], self.storage_bytes()
            )
        except OSError:
            self.metrics["storage_sample_failures"] += 1
