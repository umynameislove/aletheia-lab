"""Bounded serial admission and independently drainable immutable audit leases.

Ordinary reservation/backpressure, not a new caching theorem. All arms charge
the same compressed dependency union and durable canonical ledger. Only logical
event deadlines are supported; physical storage, crashes and fairness are not bounded.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.audit_bundle_archive import Bundle, _decision, _frame, _seal
from aletheia_lab.evaluation.model_load_retention import encode

POLICIES = ("drain_static", "drain_lru", "reserve_static", "reserve_lru")
FRAME_BOUND = 1024
RESERVATION_BYTES = 2048


class ObligationService:
    """Accepted fixed-revision audits never depend on subsequent ingress progress.

    Reserve arms promise a bounded new closed load frame before dispatch. Drain arms
    dispatch without promising that new capture can be kept; they may refuse a NEW
    lease at completion. Every existing accepted lease remains protected in both.
    This is intentionally a capability difference, not a superior-method claim.
    """

    def __init__(self, path: Path, policy: str, budget: int) -> None:
        if policy not in POLICIES or type(budget) is not int or not 1024 <= budget <= 1000000:
            raise ValueError("supported policy and bounded budget required")
        if path.exists() or path.is_symlink() or not path.parent.is_dir():
            raise ValueError("fresh owned database required")
        self.policy, self.budget, self.now, self.sequence = policy, budget, 0, -1
        self.entries: dict[str, dict[str, Any]] = {}
        self.atoms: dict[str, str] = {}
        self.reservations: dict[str, dict[str, int]] = {}
        self.peak = self.accepted = self.refused = self.overruns = 0
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE state(id INTEGER PRIMARY KEY CHECK(id=1), payload BLOB)")
        self._commit({}, {}, {})

    def _raw(
        self,
        entries: dict[str, dict[str, Any]],
        atoms: dict[str, str],
        reservations: dict[str, dict[str, int]],
    ) -> bytes:
        return encode(
            {
                "schema": "audit-obligations/v1",
                "now": f"{self.now:08x}",
                "sequence": f"{self.sequence + 1:08x}",
                "entries": entries,
                "atoms": atoms,
                "reservations": reservations,
            }
        ).encode()

    @staticmethod
    def _needed(entries: dict[str, dict[str, Any]]) -> set[str]:
        return {
            key
            for entry in entries.values()
            for key in (entry["target"], entry["parent"])
            if key is not None
        }

    def _charge(
        self,
        entries: dict[str, dict[str, Any]],
        atoms: dict[str, str],
        reservations: dict[str, dict[str, int]],
    ) -> int:
        return len(self._raw(entries, atoms, reservations)) + sum(
            value["bytes"] for value in reservations.values()
        )

    def _commit(
        self,
        entries: dict[str, dict[str, Any]],
        atoms: dict[str, str],
        reservations: dict[str, dict[str, int]],
    ) -> None:
        if (
            self._needed(entries) != set(atoms)
            or self._charge(entries, atoms, reservations) > self.budget
        ):
            raise ValueError("complete dependency/reservation budget invariant violated")
        for entry in entries.values():
            _decision(entry, atoms)
        raw = self._raw(entries, atoms, reservations)
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES(1,?)", (raw,))
        self.entries, self.atoms, self.reservations = entries, atoms, reservations
        self.peak = max(self.peak, self._charge(entries, atoms, reservations))

    def _fit(
        self,
        entries: dict[str, dict[str, Any]],
        atoms: dict[str, str],
        reservations: dict[str, dict[str, int]],
        *,
        required: str | None = None,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, str]] | None:
        pool = {key: dict(value) for key, value in entries.items()}
        optional = [
            key
            for key, value in pool.items()
            if int(value["until"], 16) < self.now and key != required
        ]
        field = "touch" if self.policy.endswith("lru") else "created"
        optional.sort(key=lambda key: (int(pool[key][field], 16), key))
        while True:
            kept = {key: atoms[key] for key in sorted(self._needed(pool))}
            if self._charge(pool, kept, reservations) <= self.budget:
                return pool, kept
            if not optional:
                return None
            del pool[optional.pop(0)]

    def advance(self, now: int) -> None:
        if type(now) is not int or not self.now <= now < 2**32:
            raise ValueError("bounded monotonic logical event required")
        previous = self.now
        self.now = now
        try:
            self._commit(self.entries, self.atoms, self.reservations)
        except Exception:
            self.now = previous
            raise

    def begin(self, scope: str, *, sequence: int, now: int, until: int) -> bool:
        """Called BEFORE side effects. No future frame, predictions or gold enters."""
        if not scope.isascii() or not 0 < len(scope) <= 64 or sequence != self.sequence + 1:
            raise ValueError("bounded unique ordered ingress required")
        if scope in self.entries or scope in self.reservations or self.reservations:
            raise ValueError("duplicate or concurrent ingress is unsupported")
        if type(until) is not int or not now <= until < 2**32:
            raise ValueError("bounded inclusive deadline required")
        self.advance(now)
        previous = self.sequence
        self.sequence = sequence
        reservations = {scope: {"bytes": RESERVATION_BYTES, "until": until}}
        if self.policy.startswith("drain"):
            reservations[scope]["bytes"] = 0
        fitted = self._fit(self.entries, self.atoms, reservations)
        if fitted is None:
            self.refused += 1
            self._commit(self.entries, self.atoms, {})
            return False
        try:
            self._commit(*fitted, reservations)
        except Exception:
            self.sequence = previous
            raise
        return True

    def finish(self, bundle: Bundle) -> bool:
        """Convert reservation to charged evidence; overrun is visible, not overcommit."""
        reservation = self.reservations.get(bundle.scope)
        if reservation is None:
            raise ValueError("completion lacks pre-dispatch admission")
        entry = {
            "scope": bundle.scope,
            "target": bundle.target,
            "parent": bundle.parent,
            "seal": _seal(bundle.scope, bundle.target, bundle.parent),
            "created": f"{self.sequence:08x}",
            "touch": f"{self.now:08x}",
            "until": f"{reservation['until']:08x}",
        }
        atoms = {**self.atoms, **dict(bundle.atoms)}
        entries = {**self.entries, bundle.scope: entry}
        decision = _decision(entry, atoms)
        # This service admits completed LOAD revisions only. Inference dependency
        # bundles require a separately bounded generation/envelope, not a guessed cap.
        growth = len(self._raw(entries, atoms, {})) - len(self._raw(self.entries, self.atoms, {}))
        oversized = bundle.parent is not None or any(
            len(encode(_frame(key, value)).encode()) > FRAME_BOUND for key, value in bundle.atoms
        )
        if oversized or growth > RESERVATION_BYTES:
            self.overruns += 1
            self._commit(self.entries, self.atoms, {})
            return False
        if decision["verdict"] == "unknown" or decision["eligibility"] == "undetermined":
            self._commit(self.entries, self.atoms, {})
            return False
        fitted = self._fit(entries, atoms, {}, required=bundle.scope)
        if fitted is None:
            self.refused += 1
            self._commit(self.entries, self.atoms, {})
            return False
        self._commit(*fitted, {})
        self.accepted += 1
        return True

    def cancel(self, scope: str) -> None:
        if scope not in self.reservations:
            raise ValueError("unknown in-flight admission")
        self._commit(self.entries, self.atoms, {})

    def query(self, scope: str) -> dict[str, Any] | None:
        """Read actual durable bytes even if new native work was refused/failed."""
        row = self.db.execute("SELECT payload FROM state WHERE id=1").fetchone()
        raw = bytes(row[0])
        state = json.loads(raw)
        if (
            encode(state).encode() != raw
            or len(raw) > self.budget
            or raw != self._raw(self.entries, self.atoms, self.reservations)
        ):
            raise ValueError("noncanonical or oversized durable archive")
        entry = state["entries"].get(scope)
        if entry is None:
            return None
        result = _decision(entry, state["atoms"])
        entries = {key: dict(value) for key, value in self.entries.items()}
        entries[scope]["touch"] = f"{self.now:08x}"
        self._commit(entries, self.atoms, self.reservations)
        return result

    def close(self) -> None:
        self.db.close()
