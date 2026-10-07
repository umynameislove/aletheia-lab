"""Ordinary online retention of dependency-complete request certificates.

The quota covers the canonical compressed state plus future-growth reservations,
not SQLite allocation, RSS or a distributed guarantee. All policies share the
same durable representation, leases and irreversible eviction boundary.
"""

from __future__ import annotations

import copy
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from aletheia_lab.evaluation.audit_bundle_policy import select
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_audit import expand, materialize, resolve
from aletheia_lab.evaluation.request_model_retention import _atom, _decode
from aletheia_lab.project.identity import content_sha256

POLICIES = ("static", "union_density", "lru", "size_cost")


def _time(value: int) -> int:
    if type(value) is not int or not 0 <= value < 2**32:
        raise ValueError("bounded nonnegative time required")
    return value


class IncidentAuditArchive:
    """Persist before acknowledging admission; protect overlapping audit leases.

    Native failures or an envelope overrun after a prospective admission remain
    broken promises, not retroactive refusals. Refetch is a caller-supplied,
    separately charged recovery capability; this archive has no truth-tape access.
    """

    def __init__(self, path: Path, policy: str, budget: int, *, reopen: bool = False) -> None:
        if policy not in POLICIES or type(budget) is not int or not 1024 <= budget <= 1_000_000:
            raise ValueError("unsupported policy or logical quota")
        if path.is_symlink() or path.parent.is_symlink() or not path.parent.is_dir():
            raise ValueError("owned archive directory required")
        if path.exists() != reopen:
            raise ValueError("archive creation/recovery disposition differs")
        self.path, self.policy, self.budget = path, policy, budget
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.state: dict[str, Any] = {
            "schema": "incident-audit-archive/v1",
            "policy": policy,
            "budget": budget,
            "now": 0,
            "clock": 0,
            "entries": {},
            "atoms": {},
            "leases": {},
            "pending": {},
            "accepted": 0,
            "refused": 0,
            "evicted": 0,
            "overruns": 0,
        }
        self.metrics = dict.fromkeys(
            (
                "write_ns",
                "read_ns",
                "select_ns",
                "peak_charge",
                "peak_db_wal_shm_bytes",
                "written_bytes",
                "commits",
                "storage_sample_failures",
            ),
            0,
        )
        if reopen:
            self.state = self._read()
            if self.state["policy"] != policy or self.state["budget"] != budget:
                self.db.close()
                raise ValueError("recovered configuration differs")
            self._validate(self.state)
        else:
            self.db.execute("CREATE TABLE state(id INTEGER PRIMARY KEY CHECK(id=1),payload BLOB)")
            self._commit()

    @staticmethod
    def _needed(entries: dict[str, Any]) -> set[str]:
        return {
            key for entry in entries.values() for key in (entry["target"], *entry["loads"].values())
        }

    @staticmethod
    def _charge(state: dict[str, Any]) -> int:
        return len(IncidentAuditArchive._document(state)) + sum(
            int(p["bound"]) for p in state["pending"].values()
        )

    @staticmethod
    def _document(state: dict[str, Any]) -> bytes:
        document = copy.deepcopy(state)
        for key in ("budget", "now", "clock", "accepted", "refused", "evicted", "overruns"):
            document[key] = f"{_time(document[key]):08x}"
        for entry in document["entries"].values():
            for key in ("created", "touch", "hits"):
                entry[key] = f"{_time(entry[key]):08x}"
        for lease in document["leases"].values():
            lease["until"] = f"{_time(lease['until']):08x}"
        for pending in document["pending"].values():
            for key in ("until", "bound"):
                pending[key] = f"{_time(pending[key]):08x}"
        return encode(document).encode()

    @staticmethod
    def _parse(document: dict[str, Any]) -> dict[str, Any]:
        state = copy.deepcopy(document)
        for key in ("budget", "now", "clock", "accepted", "refused", "evicted", "overruns"):
            state[key] = int(state[key], 16)
        for entry in state["entries"].values():
            for key in ("created", "touch", "hits"):
                entry[key] = int(entry[key], 16)
        for lease in state["leases"].values():
            lease["until"] = int(lease["until"], 16)
        for pending in state["pending"].values():
            for key in ("until", "bound"):
                pending[key] = int(pending[key], 16)
        return state

    @staticmethod
    def _frame(token: str, entry: dict[str, Any], atoms: dict[str, str]) -> dict[str, Any]:
        if (
            content_sha256(encode([token, entry["target"], entry["loads"]]).encode())
            != entry["seal"]
        ):
            raise ValueError("certificate manifest differs")
        target = _decode(entry["target"], atoms[entry["target"]])
        loads = {key: _decode(atom, atoms[atom])["load"] for key, atom in entry["loads"].items()}
        frame = expand({**target, "loads": loads})
        if frame["token"] != token:
            raise ValueError("certificate scope differs")
        resolve(frame)
        return frame

    def _validate(self, state: dict[str, Any]) -> None:
        if state["schema"] != "incident-audit-archive/v1" or self._charge(state) > self.budget:
            raise ValueError("archive contract/quota differs")
        if self._needed(state["entries"]) != set(state["atoms"]):
            raise ValueError("dependency union differs")
        for token, entry in state["entries"].items():
            self._frame(token, entry, state["atoms"])
        for lease in state["leases"].values():
            if not set(lease["scopes"]) <= set(state["entries"]):
                raise ValueError("accepted lease lost a dependency-complete certificate")

    def _read(self) -> dict[str, Any]:
        started = perf_counter_ns()
        row = self.db.execute("SELECT payload FROM state WHERE id=1").fetchone()
        if row is None:
            raise ValueError("archive state missing")
        raw = bytes(row[0])
        result = self._parse(json.loads(raw))
        if self._document(result) != raw:
            raise ValueError("archive state is noncanonical")
        self.metrics["read_ns"] += perf_counter_ns() - started
        return result

    def _commit(self) -> None:
        self._validate(self.state)
        raw = self._document(self.state)
        started = perf_counter_ns()
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES(1,?)", (raw,))
        self.metrics["write_ns"] += perf_counter_ns() - started
        self.metrics["commits"] += 1
        self.metrics["written_bytes"] += len(raw)
        self.metrics["peak_charge"] = max(self.metrics["peak_charge"], self._charge(self.state))
        try:
            self.metrics["peak_db_wal_shm_bytes"] = max(
                self.metrics["peak_db_wal_shm_bytes"], self.storage_bytes()
            )
        except OSError:
            # Telemetry cannot revoke a successful durable admission.
            self.metrics["storage_sample_failures"] += 1

    @contextmanager
    def _transition(self, now: int) -> Iterator[None]:
        if self._read() != self.state:
            raise ValueError("durable state changed outside its owner")
        previous = copy.deepcopy(self.state)
        try:
            if _time(now) < self.state["now"]:
                raise ValueError("time moved backwards")
            self.state["now"] = now
            self.state["leases"] = {
                k: v for k, v in self.state["leases"].items() if v["until"] >= now
            }
            # Future-growth promises do not expire merely because capture is late.
            yield
            self._commit()
        except Exception:
            self.state = previous
            raise

    def _mandatory(self) -> frozenset[str]:
        return frozenset(
            scope for lease in self.state["leases"].values() for scope in lease["scopes"]
        )

    def _fit(self, required: frozenset[str] = frozenset()) -> bool:
        entries, atoms = self.state["entries"], self.state["atoms"]
        mandatory = self._mandatory() | required

        def cost(scopes: frozenset[str]) -> int:
            chosen = {k: entries[k] for k in sorted(scopes)}
            return self._charge(
                {
                    **self.state,
                    "entries": chosen,
                    "atoms": {k: atoms[k] for k in sorted(self._needed(chosen))},
                }
            )

        if cost(mandatory) > self.budget:
            return False
        if cost(frozenset(entries)) <= self.budget:
            # Ordinary fast path: do not solve optional selection when nothing
            # needs eviction. This is shared by static and all dynamic arms.
            return True
        started = perf_counter_ns()
        chosen = select(self.policy, entries, mandatory, cost, self.budget, self.state["clock"])
        self.metrics["select_ns"] += perf_counter_ns() - started
        self.state["evicted"] += len(set(entries) - set(chosen))
        self.state["entries"] = {k: entries[k] for k in sorted(chosen)}
        self.state["atoms"] = {k: atoms[k] for k in sorted(self._needed(self.state["entries"]))}
        return True

    def reserve(self, scope: str, *, now: int, until: int, bound: int) -> bool:
        """Reserve bounded complete-frame and lease growth before native dispatch."""
        if not isinstance(scope, str) or not scope.isascii() or not 0 < len(scope) <= 128:
            raise ValueError("bounded unique scope required")
        if _time(until) < now or type(bound) is not int or not 512 <= bound <= 65536:
            raise ValueError("invalid growth/deadline envelope")
        with self._transition(now):
            if scope in self.state["pending"] or scope in self.state["entries"]:
                raise ValueError("duplicate prospective scope")
            previous = copy.deepcopy(self.state)
            self.state["pending"][scope] = {"until": until, "bound": bound}
            if not self._fit():
                self.state = previous
                self.state["refused"] += 1
                return False
            self.state["accepted"] += 1
        return True

    def put(self, frame: dict[str, Any], *, now: int) -> bool:
        """Capture completed evidence; preserve a previously admitted growth promise."""
        resolve(frame)
        token = frame["token"]
        with self._transition(now):
            if token in self.state["entries"]:
                raise ValueError("duplicate completed scope")
            before = copy.deepcopy(self.state)
            reservation = self.state["pending"].pop(token, None)
            packed = materialize(frame)
            target, raw = _atom({k: v for k, v in packed.items() if k != "loads"})
            loads: dict[str, str] = {}
            self.state["atoms"][target] = raw
            for generation, load in packed["loads"].items():
                key, raw = _atom({"load": load})
                loads[generation], self.state["atoms"][key] = key, raw
            self.state["clock"] += 1
            self.state["entries"][token] = {
                "target": target,
                "loads": loads,
                "seal": content_sha256(encode([token, target, loads]).encode()),
                "created": self.state["clock"],
                "touch": self.state["clock"],
                "hits": 0,
            }
            if reservation:
                self.state["leases"][f"pre:{token}"] = {
                    "scopes": [token],
                    "until": reservation["until"],
                }
                base = {
                    **before,
                    "pending": {k: v for k, v in before["pending"].items() if k != token},
                }
                growth = self._charge(self.state) - self._charge(base)
                if growth > reservation["bound"]:
                    self.state = before
                    del self.state["pending"][token]
                    self.state["overruns"] += 1
                    return False
            if not self._fit(frozenset({token}) if reservation else frozenset()):
                self.state = before
                if reservation:
                    del self.state["pending"][token]
                    self.state["overruns"] += 1
                return False
            return token in self.state["entries"]

    def restore_union(
        self, frames: list[dict[str, Any]], *, now: int, required: list[str] | None = None
    ) -> bool:
        """Restore a requested dependency union atomically, not optional-cache roulette.

        Frames must come from the declared recovery tier. The caller charges that
        tier separately. Failed installation does not evict an existing promise.
        """
        if not frames or len(frames) > 64:
            raise ValueError("bounded nonempty recovery union required")
        scopes = [frame["token"] for frame in frames]
        if len(set(scopes)) != len(scopes):
            raise ValueError("duplicate recovery scope")
        for frame in frames:
            resolve(frame)
        required_scopes = frozenset(required if required is not None else scopes)
        if not required_scopes or not required_scopes <= set(scopes) | set(self.state["entries"]):
            raise ValueError("recovery required closure is unavailable")
        with self._transition(now):
            before = copy.deepcopy(self.state)
            for frame in frames:
                self._restore_frame(frame)
            if not self._fit(required_scopes):
                self.state = before
                return False
        return True

    def _restore_frame(self, frame: dict[str, Any]) -> None:
        token = frame["token"]
        if token in self.state["pending"]:
            raise ValueError("recovery cannot replace an uncompleted future promise")
        if token in self.state["entries"]:
            observed = self._frame(token, self.state["entries"][token], self.state["atoms"])
            if observed != frame:
                raise ValueError("recovery revision differs from resident certificate")
            return
        packed = materialize(frame)
        target, raw = _atom({k: v for k, v in packed.items() if k != "loads"})
        loads = {}
        self.state["atoms"][target] = raw
        for generation, load in packed["loads"].items():
            key, raw = _atom({"load": load})
            loads[generation], self.state["atoms"][key] = key, raw
        self.state["clock"] += 1
        self.state["entries"][token] = {
            "target": target,
            "loads": loads,
            "seal": content_sha256(encode([token, target, loads]).encode()),
            "created": self.state["clock"],
            "touch": self.state["clock"],
            "hits": 0,
        }

    def witness(self, scopes: list[str]) -> dict[str, Any]:
        """Read the actual committed query frontier, for private independent replay."""
        if self._read() != self.state:
            raise ValueError("witness durable frontier differs")
        return {
            "state": json.loads(self._document(self.state)),
            "scopes": scopes,
            "state_sha256": content_sha256(self._document(self.state)),
        }

    def demand(self, identifier: str, scopes: list[str], *, now: int, until: int) -> bool:
        """Late demand sees only current evidence; pins cannot resurrect history."""
        if (
            not isinstance(identifier, str)
            or not identifier
            or len(identifier) > 128
            or identifier.startswith("pre:")
        ):
            raise ValueError("bounded demand identifier required")
        if not scopes or len(scopes) > 64 or len(set(scopes)) != len(scopes) or _time(until) < now:
            raise ValueError("bounded unique scope/deadline required")
        with self._transition(now):
            if identifier in self.state["leases"]:
                raise ValueError("duplicate active demand")
            if not set(scopes) <= set(self.state["entries"]):
                self.state["refused"] += 1
                return False
            previous = copy.deepcopy(self.state)
            self.state["leases"][identifier] = {"scopes": sorted(scopes), "until": until}
            if not self._fit():
                self.state = previous
                self.state["refused"] += 1
                return False
            self.state["accepted"] += 1
        return True

    def query(self, scopes: list[str], *, now: int) -> dict[str, str | None]:
        result: dict[str, str | None] = {}
        with self._transition(now):
            for token in scopes:
                entry = self.state["entries"].get(token)
                result[token] = (
                    resolve(self._frame(token, entry, self.state["atoms"])) if entry else None
                )
                if entry:
                    self.state["clock"] += 1
                    entry["touch"], entry["hits"] = self.state["clock"], entry["hits"] + 1
        return result

    def drain(self, identifier: str, *, now: int) -> None:
        """Release one obligation independently of future native ingress."""
        with self._transition(now):
            if identifier not in self.state["leases"]:
                raise ValueError("unknown active obligation")
            del self.state["leases"][identifier]

    def storage_bytes(self) -> int:
        return sum(
            p.stat().st_size
            for suffix in ("", "-wal", "-shm")
            if (p := Path(str(self.path) + suffix)).exists()
        )

    def snapshot(self) -> dict[str, Any]:
        if self._read() != self.state:
            raise ValueError("durable state differs")
        return {
            **self.metrics,
            "logical_charge": self._charge(self.state),
            "state_sha256": content_sha256(self._document(self.state)),
            "accepted": self.state["accepted"],
            "refused": self.state["refused"],
            "evicted": self.state["evicted"],
            "overruns": self.state["overruns"],
            "retained_scopes": len(self.state["entries"]),
            "active_leases": len(self.state["leases"]),
        }

    def close(self) -> None:
        self.db.close()
