"""Matched ordinary retention of closed request/model certificates.

Whole-union heuristics reuse the existing policy implementation. No novel online
algorithm, future-query access, native backpressure or physical byte cap is claimed.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter_ns
from typing import Any, cast

from aletheia_lab.evaluation.audit_bundle_policy import POLICIES, select
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_audit import expand, materialize, resolve
from aletheia_lab.project.identity import content_sha256

LIMIT = 65536


def _counter(value: int) -> str:
    if type(value) is not int or not 0 <= value < 2**32:
        raise ValueError("bounded nonnegative service counter required")
    return f"{value:08x}"


def _atom(value: dict[str, Any]) -> tuple[str, str]:
    raw = encode(value).encode()
    if len(raw) > LIMIT:
        raise ValueError("oversized request certificate")
    return content_sha256(raw), base64.b64encode(zlib.compress(raw, 6)).decode("ascii")


def _decode(key: str, value: str) -> dict[str, Any]:
    compressed = base64.b64decode(value, validate=True)
    decoder = zlib.decompressobj()
    raw = decoder.decompress(compressed, LIMIT + 1)
    if len(raw) > LIMIT or not decoder.eof or decoder.unused_data:
        raise ValueError("corrupt or oversized archived atom")
    if content_sha256(raw) != key:
        raise ValueError("archived atom identity changed")
    document = json.loads(raw)
    if type(document) is not dict or encode(document).encode() != raw:
        raise ValueError("noncanonical archived atom")
    return cast(dict[str, Any], document)


class RequestModelArchive:
    """One durable BLOB charging atoms and metadata, with inclusive hard pins.

    All arms share compression, dependency deduplication and durable writes.
    The external source supplies unique admitted ingress identities. No crash
    recovery, global token ledger or refetch from discarded source is provided.
    """

    def __init__(self, path: Path, policy: str, budget: int, mode: str = "compact") -> None:
        if policy not in POLICIES or mode not in {"full", "compact"}:
            raise ValueError("unsupported policy or representation")
        if type(budget) is not int or not 512 <= budget <= 1_000_000:
            raise ValueError("bounded logical archive budget required")
        if path.exists() or path.is_symlink() or not path.parent.is_dir():
            raise ValueError("fresh owned archive database required")
        self.path, self.policy, self.budget, self.mode = path, policy, budget, mode
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE state(id INTEGER PRIMARY KEY CHECK(id=1), payload BLOB)")
        self.entries: dict[str, Any] = {}
        self.atoms: dict[str, str] = {}
        self.now = self.clock = self.accepted = self.refused = self.evicted = 0
        self.peak = self.storage_peak = self.write_ns = self.selection_ns = self.query_ns = 0
        self.storage_sample_failures = 0
        self._commit({}, {})

    @contextmanager
    def _transition(self) -> Iterator[None]:
        previous = dict(vars(self))
        try:
            yield
        except Exception:
            self.__dict__.update(previous)
            raise

    @staticmethod
    def _needed(entries: dict[str, Any]) -> set[str]:
        return {
            atom
            for entry in entries.values()
            for atom in (entry["target"], *entry["loads"].values())
        }

    def _document(self, entries: dict[str, Any], atoms: dict[str, str]) -> bytes:
        return encode(
            {
                "schema": "request-model-archive/v1",
                "mode": self.mode,
                "policy": self.policy,
                "budget": _counter(self.budget),
                "now": _counter(self.now),
                "clock": _counter(self.clock),
                "accepted": _counter(self.accepted),
                "refused": _counter(self.refused),
                "evicted": _counter(self.evicted),
                "entries": entries,
                "atoms": atoms,
            }
        ).encode()

    def _subset(self, entries: dict[str, Any], atoms: dict[str, str]) -> dict[str, str]:
        return {key: atoms[key] for key in sorted(self._needed(entries))}

    def _frame(self, token: str, entry: dict[str, Any], atoms: dict[str, str]) -> dict[str, Any]:
        if entry["seal"] != content_sha256(
            encode([token, entry["target"], entry["loads"]]).encode()
        ):
            raise ValueError("request certificate manifest changed")
        target = _decode(entry["target"], atoms[entry["target"]])
        loads = {
            generation: _decode(key, atoms[key])["load"]
            for generation, key in entry["loads"].items()
        }
        frame = (
            expand({**target, "loads": loads})
            if self.mode == "compact"
            else {**target, "loads": loads}
        )
        if frame["token"] != token:
            raise ValueError("retained ingress token misjoin")
        resolve(frame)
        return frame

    def _commit(self, entries: dict[str, Any], atoms: dict[str, str]) -> None:
        raw = self._document(entries, atoms)
        if len(raw) > self.budget or self._needed(entries) != set(atoms):
            raise ValueError("archive budget/dependency union violation")
        for token, entry in entries.items():
            self._frame(token, entry, atoms)
        started = perf_counter_ns()
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES(1,?)", (raw,))
        self.write_ns += perf_counter_ns() - started
        self.entries, self.atoms = entries, atoms
        self.peak = max(self.peak, len(raw))
        try:
            self.storage_peak = max(self.storage_peak, self.storage_bytes())
        except OSError:
            # The durable transition succeeded; failed telemetry cannot undo it.
            self.storage_sample_failures += 1

    def storage_bytes(self) -> int:
        return sum(
            candidate.stat().st_size
            for suffix in ("", "-wal", "-shm")
            if (candidate := Path(str(self.path) + suffix)).exists()
        )

    def _read(self) -> None:
        row = self.db.execute("SELECT payload FROM state WHERE id=1").fetchone()
        if row is None or bytes(row[0]) != self._document(self.entries, self.atoms):
            raise ValueError("durable archive changed outside its owner")

    def advance(self, now: int) -> None:
        with self._transition():
            self._read()
            self._advance(now)
            self._commit(self.entries, self.atoms)

    def _advance(self, now: int) -> None:
        _counter(now)
        if now < self.now:
            raise ValueError("monotonic archive event required")
        self.now = now
        entries = self.entries
        if self.policy == "ttl":
            entries = {
                token: entry
                for token, entry in entries.items()
                if int(entry["until"], 16) >= now or now - int(entry["arrival"], 16) <= 2
            }
        self.evicted += len(self.entries) - len(entries)
        self.entries, self.atoms = entries, self._subset(entries, self.atoms)

    def _select(
        self, entries: dict[str, Any], atoms: dict[str, str], mandatory: frozenset[str]
    ) -> frozenset[str]:
        pool = {
            token: {
                **{field: int(entry[field], 16) for field in ("created", "touch", "hits")},
                "ttl_age": self.now - int(entry["arrival"], 16),
            }
            for token, entry in entries.items()
        }

        def cost(tokens: frozenset[str]) -> int:
            chosen = {token: entries[token] for token in sorted(tokens)}
            return len(self._document(chosen, self._subset(chosen, atoms)))

        started = perf_counter_ns()
        result = select(self.policy, pool, mandatory, cost, self.budget, self.clock)
        self.selection_ns += perf_counter_ns() - started
        return result

    def put(self, frame: dict[str, Any], now: int, until: int | None) -> bool:
        with self._transition():
            self._read()
            return self._put(frame, now, until)

    def _put(self, frame: dict[str, Any], now: int, until: int | None) -> bool:
        verdict = resolve(frame)
        self._advance(now)
        token = frame["token"]
        if token in self.entries:
            raise ValueError("duplicate retained ingress token")
        if until is not None:
            _counter(until)
            if until < now or verdict not in {"compliant", "violation"}:
                raise ValueError("hard audit requires a conclusive frame and future deadline")
        packed = materialize(frame) if self.mode == "compact" else frame
        target, raw = _atom({key: value for key, value in packed.items() if key != "loads"})
        atoms = {**self.atoms, target: raw}
        capsules = {}
        for generation, load in packed["loads"].items():
            key, raw = _atom({"load": load})
            capsules[generation], atoms[key] = key, raw
        self.clock += 1
        entry = {
            "target": target,
            "loads": capsules,
            "seal": content_sha256(encode([token, target, capsules]).encode()),
            "created": _counter(self.clock),
            "touch": _counter(self.clock),
            "arrival": _counter(now),
            "hits": _counter(0),
            "until": _counter(until) if until is not None else "-1",
        }
        pool = {**self.entries, token: entry}
        mandatory = frozenset(key for key, value in pool.items() if int(value["until"], 16) >= now)
        mandatory_entries = {key: pool[key] for key in mandatory}
        if (
            len(self._document(mandatory_entries, self._subset(mandatory_entries, atoms)))
            > self.budget
        ):
            self.refused += int(until is not None)
            self._commit(self.entries, self.atoms)
            return False
        chosen = self._select(pool, atoms, mandatory)
        entries = {key: pool[key] for key in sorted(chosen)}
        self.evicted += len(set(self.entries) - set(entries))
        accepted = token in chosen
        self.accepted += int(accepted and until is not None)
        self._commit(entries, self._subset(entries, atoms))
        return accepted

    def query(self, token: str, now: int) -> dict[str, Any] | None:
        with self._transition():
            self._read()
            return self._query(token, now)

    def _query(self, token: str, now: int) -> dict[str, Any] | None:
        started = perf_counter_ns()
        self._advance(now)
        entry = self.entries.get(token)
        if entry is None:
            self._commit(self.entries, self.atoms)
            self.query_ns += perf_counter_ns() - started
            return None
        frame = self._frame(token, entry, self.atoms)
        self.clock += 1
        entries = {
            **self.entries,
            token: {
                **entry,
                "touch": _counter(self.clock),
                "hits": _counter(int(entry["hits"], 16) + 1),
            },
        }
        self._commit(entries, self.atoms)
        self.query_ns += perf_counter_ns() - started
        return {"verdict": resolve(frame), "frame": frame}

    def snapshot(self) -> dict[str, Any]:
        self._read()
        return {
            "logical_bytes": len(self._document(self.entries, self.atoms)),
            "peak_logical_bytes": self.peak,
            "sampled_storage_peak_bytes": self.storage_peak,
            "storage_sample_failures": self.storage_sample_failures,
            "accepted": self.accepted,
            "refused": self.refused,
            "evicted": self.evicted,
            "selection_ns": self.selection_ns,
            "write_ns": self.write_ns,
            "query_ns": self.query_ns,
            "raw": self._document(self.entries, self.atoms).decode(),
        }

    def close(self) -> None:
        self.db.close()
