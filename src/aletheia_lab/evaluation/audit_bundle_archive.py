"""Single-tier, completed-revision audit service with a logical archive byte cap.

The caller admits closed serial serving frames and object-generation bindings.
This is a trusted service overlay, not native MLServer or hostile-host attestation.
No historical tape, truth label, future query or refetch facility enters this store.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns
from typing import Any, cast

from aletheia_lab.evaluation.audit_bundle_policy import select
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.model_load_serving_workload import decide
from aletheia_lab.project.identity import content_sha256


def _fixed(value: int) -> str:
    if type(value) is not int or not 0 <= value < 2**32:
        raise ValueError("service counter outside bounded domain")
    return f"{value:08x}"


def _atom(frame: dict[str, Any]) -> tuple[str, str]:
    if frame.get("closed") is not True:
        raise ValueError("only completed closed revisions can be sealed")
    if frame.get("kind") not in {"load", "infer"} or not frame.get("scope"):
        raise ValueError("unsupported admitted operation frame")
    raw = encode(frame).encode()
    if len(raw) > 8192:
        raise ValueError("admitted frame exceeds bound")
    return content_sha256(raw), base64.b64encode(zlib.compress(raw, 6)).decode("ascii")


def _frame(key: str, value: str) -> dict[str, Any]:
    compressed = base64.b64decode(value, validate=True)
    decoder = zlib.decompressobj()
    raw = decoder.decompress(compressed, 8193)
    if not decoder.eof or decoder.unused_data or len(raw) > 8192:
        raise ValueError("invalid/oversized compressed frame")
    if content_sha256(raw) != key:
        raise ValueError("archive frame identity mismatch")
    frame = json.loads(raw)
    if type(frame) is not dict or encode(frame).encode() != raw:
        raise ValueError("noncanonical archived frame")
    return cast(dict[str, Any], frame)


def _seal(scope: str, target: str, parent: str | None) -> str:
    return content_sha256(encode([scope, target, parent]).encode())


@dataclass(frozen=True)
class Bundle:
    """A full immutable operation revision, not an inclusion-minimal positive proof."""

    scope: str
    target: str
    parent: str | None
    atoms: tuple[tuple[str, str], ...]

    @classmethod
    def build(cls, frame: dict[str, Any], parent: dict[str, Any] | None = None) -> Bundle:
        target, raw = _atom(frame)
        atoms = {target: raw}
        parent_key = None
        if frame["kind"] == "infer":
            if parent is None or frame.get("generation") != parent.get("scope"):
                raise ValueError("inference requires its admitted resident-generation frame")
            if parent.get("kind") != "load":
                raise ValueError("generation dependency is not a load")
            parent_key, parent_raw = _atom(parent)
            atoms[parent_key] = parent_raw
        elif parent is not None:
            raise ValueError("load revision cannot have an inference parent")
        return cls(frame["scope"], target, parent_key, tuple(sorted(atoms.items())))


def _decision(entry: dict[str, Any], atoms: dict[str, str]) -> dict[str, Any]:
    if entry["seal"] != _seal(entry["scope"], entry["target"], entry["parent"]):
        raise ValueError("archive revision manifest changed")
    target = _frame(entry["target"], atoms[entry["target"]])
    parent = _frame(entry["parent"], atoms[entry["parent"]]) if entry["parent"] else None
    if target["scope"] != entry["scope"]:
        raise ValueError("archive scope misjoin")
    if target["kind"] == "infer" and (
        parent is None or target.get("generation") != parent["scope"]
    ):
        raise ValueError("archive generation misjoin")
    return decide(target, parent)


def _conclusive(decision: dict[str, Any], has_parent: bool) -> bool:
    return (
        decision["eligibility"] != "undetermined"
        and decision["verdict"] != "unknown"
        and (not has_parent or decision["resident"] not in {None, "unknown"})
    )


class AuditArchive:
    """Same compressed representation, lease admission and durability for all arms.

    Budget bounds the exact canonical persisted BLOB (including all service
    metadata and base64 overhead), not SQLite pages, WAL, RSS or transient input.
    Completed-revision leases expire inclusively in load-slot units. In-flight
    reservations, latest-revision queries and crash recovery are out of scope.
    The trusted ingress supplies each completed scope exactly once, as checked
    by the native source census; sequence alone is not a global identity ledger.
    """

    def __init__(
        self,
        path: Path,
        policy: str,
        budget: int,
        observer: Callable[..., None] | None = None,
    ) -> None:
        if path.exists() or path.is_symlink() or not path.parent.is_dir():
            raise ValueError("new owned archive database required")
        if type(budget) is not int or not 512 <= budget <= 1_000_000:
            raise ValueError("bounded logical budget required")
        self.policy, self.budget, self.path = policy, budget, path
        self.observer = observer
        self.entries: dict[str, dict[str, Any]] = {}
        self.atoms: dict[str, str] = {}
        self.leases: dict[str, int] = {}
        self.resident: str | None = None
        self.sequence, self.now, self.clock = -1, 0, 0
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE archive(id INTEGER PRIMARY KEY CHECK(id=1), payload BLOB)")
        self.db.commit()
        self.peak_logical_bytes = self.peak_storage_bytes = self.write_ns = 0
        self.query_ns = self.verify_ns = self.selection_ns = 0
        self.storage_sample_failures = 0
        self.evicted = self.refused = self.accepted = self.lease_offers = 0
        self._commit(self.entries, self.atoms, self.leases, None)

    def _document(
        self,
        entries: dict[str, dict[str, Any]],
        atoms: dict[str, str],
        leases: dict[str, int],
        resident: str | None,
    ) -> bytes:
        return encode(
            {
                "schema": "audit-bundle-archive/v2",
                "entries": entries,
                "atoms": atoms,
                "leases": {key: _fixed(value) for key, value in leases.items()},
                "resident": resident,
                "sequence": _fixed(self.sequence + 1),
                "now": _fixed(self.now),
                "clock": _fixed(self.clock),
            }
        ).encode()

    @staticmethod
    def _needed(entries: dict[str, dict[str, Any]]) -> set[str]:
        return {
            atom
            for entry in entries.values()
            for atom in (entry["target"], entry["parent"])
            if atom is not None
        }

    def _commit(
        self,
        entries: dict[str, dict[str, Any]],
        atoms: dict[str, str],
        leases: dict[str, int],
        resident: str | None,
    ) -> None:
        raw = self._document(entries, atoms, leases, resident)
        if len(raw) > self.budget or not set(leases) <= set(entries):
            raise ValueError("archive budget/lease invariant violated")
        if self._needed(entries) != set(atoms):
            raise ValueError("archive dependency union is incomplete or has unused atoms")
        for entry in entries.values():
            _decision(entry, atoms)
        if resident is not None and resident not in entries:
            raise ValueError("resident revision is unavailable")
        started = perf_counter_ns()
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO archive VALUES(1,?)", (raw,))
        self.write_ns += perf_counter_ns() - started
        self.entries, self.atoms, self.leases, self.resident = entries, atoms, leases, resident
        self.peak_logical_bytes = max(self.peak_logical_bytes, len(raw))
        try:
            self.peak_storage_bytes = max(self.peak_storage_bytes, self.storage_bytes())
        except OSError:
            self.storage_sample_failures += 1

    def storage_bytes(self) -> int:
        return sum(
            candidate.stat().st_size
            for suffix in ("", "-wal", "-shm")
            if (candidate := Path(str(self.path) + suffix)).exists()
        )

    def offer(
        self,
        bundle: Bundle,
        *,
        sequence: int,
        now: int,
        resident: str | None,
        lease_until: int | None = None,
    ) -> bool | None:
        """Common arrival/admission, then choose optional bundles without future access.

        Parent bytes supplied in an inference arrival must already be retained;
        carrying the source tape into a lost dependency cannot restore evidence.
        """
        if sequence != self.sequence + 1 or now < self.now or bundle.scope in self.entries:
            raise ValueError("nonmonotonic/duplicate completed revision")
        _fixed(sequence)
        _fixed(now)
        if bundle.parent is not None and bundle.parent not in self.atoms:
            raise ValueError("cannot resurrect a missing generation dependency")
        previous_sequence, previous_now, previous_clock = self.sequence, self.now, self.clock
        self.sequence, self.now = sequence, now
        self.clock += 1
        _fixed(self.clock)
        try:
            accepted = self._offer(bundle, resident, lease_until)
        except Exception:
            self.sequence, self.now, self.clock = previous_sequence, previous_now, previous_clock
            raise
        return accepted

    def _offer(self, bundle: Bundle, resident: str | None, until: int | None) -> bool | None:
        pool = {key: dict(value) for key, value in self.entries.items()}
        pool[bundle.scope] = {
            "scope": bundle.scope,
            "target": bundle.target,
            "parent": bundle.parent,
            "seal": _seal(bundle.scope, bundle.target, bundle.parent),
            "created": _fixed(self.clock),
            "slot": _fixed(self.now),
            "touch": _fixed(self.clock),
            "hits": _fixed(0),
        }
        blobs = {**self.atoms, **dict(bundle.atoms)}
        leases = {key: value for key, value in self.leases.items() if value >= self.now}
        mandatory = frozenset(leases) | (frozenset({resident}) if resident else frozenset())
        if not mandatory <= pool.keys():
            raise ValueError("active or leased revision unavailable")

        def cost(keys: frozenset[str]) -> int:
            entries = {key: pool[key] for key in sorted(keys)}
            atoms = {key: blobs[key] for key in sorted(self._needed(entries))}
            return len(self._document(entries, atoms, leases, resident))

        if cost(mandatory) > self.budget:
            raise ValueError("active/accepted obligation cannot fit; no silent downgrade")
        accepted = None
        if until is not None:
            _fixed(until)
            if until < self.now:
                raise ValueError("lease expires before admission")
            leases[bundle.scope] = until
            conclusive = _conclusive(
                _decision(pool[bundle.scope], blobs), bundle.parent is not None
            )
            if conclusive and cost(mandatory | {bundle.scope}) <= self.budget:
                mandatory |= {bundle.scope}
                accepted = True
            else:
                del leases[bundle.scope]
                accepted = False
        stats = {
            key: {
                "created": int(value["created"], 16),
                "touch": int(value["touch"], 16),
                "hits": int(value["hits"], 16),
                "ttl_age": self.now - int(value["slot"], 16),
            }
            for key, value in pool.items()
        }
        started = perf_counter_ns()
        kept = select(self.policy, stats, mandatory, cost, self.budget, self.clock)
        self.selection_ns += perf_counter_ns() - started
        if self.observer is not None:
            self.observer(
                stats,
                mandatory,
                cost,
                self.budget,
                self.now,
                self.sequence,
                kept,
                json.loads(self._document(pool, blobs, leases, resident)),
            )
        entries = {key: pool[key] for key in sorted(kept)}
        atoms = {key: blobs[key] for key in sorted(self._needed(entries))}
        self._commit(entries, atoms, leases, resident)
        self.evicted += len(set(pool) - set(entries))
        if accepted is not None:
            self.lease_offers += 1
            self.accepted += int(accepted)
            self.refused += int(not accepted)
        return accepted

    def lease(self, scope: str, until: int) -> bool:
        """A new/extended post-completion lease cannot bring a lost revision back."""
        _fixed(until)
        self.lease_offers += 1
        if until < self.now or scope not in self.entries:
            self.refused += 1
            return False
        decision = _decision(self.entries[scope], self.atoms)
        if not _conclusive(decision, self.entries[scope]["parent"] is not None):
            self.refused += 1
            return False
        leases = {**self.leases, scope: max(until, self.leases.get(scope, 0))}
        raw = self._document(self.entries, self.atoms, leases, self.resident)
        if len(raw) > self.budget:
            self.refused += 1
            return False
        self._commit(self.entries, self.atoms, leases, self.resident)
        self.accepted += 1
        return True

    def advance(self, now: int) -> None:
        """Expire only leases strictly before now; evidence is not refetched."""
        _fixed(now)
        if now < self.now:
            raise ValueError("archive clock moved backwards")
        previous = self.now
        self.now = now
        leases = {key: value for key, value in self.leases.items() if value >= now}
        try:
            self._commit(self.entries, self.atoms, leases, self.resident)
        except Exception:
            self.now = previous
            raise

    def query(self, scope: str, *, record_access: bool = True) -> dict[str, Any] | None:
        """Resolve only actual durable archive bytes; never consult the source tape."""
        started = perf_counter_ns()
        row = self.db.execute("SELECT payload FROM archive WHERE id=1").fetchone()
        self.query_ns += perf_counter_ns() - started
        started = perf_counter_ns()
        state = json.loads(bytes(row[0]))
        entry = state["entries"].get(scope)
        decision = _decision(entry, state["atoms"]) if entry is not None else None
        self.verify_ns += perf_counter_ns() - started
        if record_access and scope in self.entries:
            entries = {key: dict(value) for key, value in self.entries.items()}
            previous_clock = self.clock
            self.clock += 1
            entries[scope].update(
                touch=_fixed(self.clock), hits=_fixed(int(entries[scope]["hits"], 16) + 1)
            )
            try:
                self._commit(entries, self.atoms, self.leases, self.resident)
            except Exception:
                self.clock = previous_clock
                raise
        return decision

    def retained_frame(self, scope: str) -> dict[str, Any]:
        """Only already retained current-generation bytes may form a new dependency."""
        entry = self.entries.get(scope)
        if entry is None:
            raise ValueError("requested generation was lost")
        return _frame(entry["target"], self.atoms[entry["target"]])

    def metrics(self) -> dict[str, int]:
        return {
            "logical_budget_bytes": self.budget,
            "peak_logical_bytes": self.peak_logical_bytes,
            "peak_sqlite_wal_shm_bytes": self.peak_storage_bytes,
            "write_ns": self.write_ns,
            "query_ns": self.query_ns,
            "verify_ns": self.verify_ns,
            "selection_ns": self.selection_ns,
            "evicted_bundles": self.evicted,
            "lease_offers": self.lease_offers,
            "accepted_leases": self.accepted,
            "refused_leases": self.refused,
            "storage_sample_failures": self.storage_sample_failures,
        }

    def close(self) -> None:
        self.db.close()
