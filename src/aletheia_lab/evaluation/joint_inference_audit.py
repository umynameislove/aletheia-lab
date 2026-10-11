"""Ordinary fixed-revision inference certificates and independently drained leases.

Trusted capture supplies the admitted request/object/load bindings. Packing is
reversible materialization, not a new inference or caching algorithm. The cap
charges the complete canonical SQLite BLOB, not physical pages or process RSS.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import zlib
from pathlib import Path
from typing import Any, cast

from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.project.identity import content_sha256

ROOT = (
    "request",
    "attempt",
    "revision",
    "expected",
    "input",
    "output",
    "declared",
    "closed",
    "failed",
)
LOAD = ("role", "artifact", "selected", "fingerprint", "count", "closed")
USE = (
    "request",
    "attempt",
    "ordinal",
    "role",
    "generation",
    "artifact",
    "fingerprint",
    "input",
    "output",
)
ROLES = ("encoder", "classifier")
LIMIT = 65536


def _integer(value: object) -> bool:
    return type(value) is int and 0 <= value < 2**32


def _name(value: object) -> bool:
    return isinstance(value, str) and value.isascii() and 0 < len(value) <= 128


def _digest(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def _fields(value: object, fields: tuple[str, ...]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(fields):
        raise ValueError("unsupported certificate schema or extension")
    return cast(dict[str, Any], value)


def _validate_load(generation: object, value: object) -> None:
    load = _fields(value, LOAD)
    if not _name(generation) or load["role"] not in ROLES or not _integer(load["count"]):
        raise ValueError("invalid load generation")
    if type(load["closed"]) is not bool or any(
        not _digest(load[k]) for k in ("artifact", "selected", "fingerprint")
    ):
        raise ValueError("invalid load binding")


def _validate_use(value: object) -> None:
    use = _fields(value, USE)
    if any(not _name(use[k]) for k in ("request", "generation")) or use["role"] not in ROLES:
        raise ValueError("invalid stage identity")
    if any(not _integer(use[k]) for k in ("attempt", "ordinal")) or any(
        not _digest(use[k]) for k in ("artifact", "fingerprint", "input", "output")
    ):
        raise ValueError("invalid stage binding")


def _validate(frame: dict[str, Any]) -> None:
    _fields(frame, (*ROOT, "loads", "uses"))
    if not _name(frame["request"]) or any(
        not _integer(frame[k]) for k in ("attempt", "revision", "declared")
    ):
        raise ValueError("invalid request identity or census")
    if any(type(frame[k]) is not bool for k in ("closed", "failed")):
        raise ValueError("invalid closure or failure marker")
    if not _digest(frame["input"]) or (
        frame["output"] is not None and not _digest(frame["output"])
    ):
        raise ValueError("invalid request dataflow identity")
    expected = frame["expected"]
    if type(expected) is not list or len(expected) != 2 or not all(_digest(v) for v in expected):
        raise ValueError("ordered two-stage authorization required")
    if type(frame["loads"]) is not dict or type(frame["uses"]) is not list:
        raise ValueError("invalid load/use census")
    if len(frame["loads"]) > 64 or len(frame["uses"]) > 64 or len(encode(frame).encode()) > LIMIT:
        raise ValueError("oversized admitted frame")
    for generation, value in frame["loads"].items():
        _validate_load(generation, value)
    for value in frame["uses"]:
        _validate_use(value)


def resolve(frame: dict[str, Any]) -> dict[str, Any]:
    """Four verdicts under the fixed two-stage, trusted-capture contract."""
    _validate(frame)
    uses, loads = frame["uses"], frame["loads"]
    conflict = frame["declared"] != 2 or len(uses) > frame["declared"]
    missing = frame["failed"] or not frame["closed"] or frame["output"] is None
    observed: list[str] = []
    for ordinal, use in enumerate(uses):
        conflict |= use["request"] != frame["request"] or use["attempt"] != frame["attempt"]
        conflict |= (
            use["ordinal"] != ordinal or ordinal >= 2 or use["role"] != ROLES[min(ordinal, 1)]
        )
        previous_output = frame["input"] if ordinal == 0 else uses[ordinal - 1]["output"]
        conflict |= use["input"] != previous_output
        load = loads.get(use["generation"])
        if load is None:
            missing = True
        else:
            conflict |= any(use[k] != load[k] for k in ("role", "artifact", "fingerprint"))
            conflict |= load["closed"] and load["count"] != 1
            missing |= not load["closed"]
        observed.append(use["artifact"])
    if frame["closed"] and not frame["failed"]:
        conflict |= len(uses) != frame["declared"]
    if len(uses) == 2 and frame["output"] is not None:
        conflict |= uses[-1]["output"] != frame["output"]
    if conflict:
        verdict = "conflict"
    elif missing or len(uses) != 2:
        verdict = "unknown"
    else:
        verdict = "compliant" if observed == frame["expected"] else "violation"
    reasons = {
        "conflict": "inconsistent_binding_or_census",
        "unknown": "failed_or_incomplete",
        "compliant": "authorized_ordered_generations",
        "violation": "unauthorized_ordered_generations",
    }
    return {
        "request": frame["request"],
        "attempt": frame["attempt"],
        "revision": frame["revision"],
        "verdict": verdict,
        "reason": reasons[verdict],
        "expected": frame["expected"],
        "observed": observed,
    }


def materialize(frame: dict[str, Any]) -> dict[str, Any]:
    """Losslessly pack every admitted field, including conflict/unknown basis."""
    _validate(frame)
    return {
        "root": [frame[k] for k in ROOT],
        "loads": {g: [v[k] for k in LOAD] for g, v in frame["loads"].items()},
        "uses": [[v[k] for k in USE] for v in frame["uses"]],
    }


def _unpack(values: object, fields: tuple[str, ...]) -> dict[str, Any]:
    if type(values) is not list or len(values) != len(fields):
        raise ValueError("invalid positional certificate")
    return dict(zip(fields, values, strict=True))


def expand(certificate: dict[str, Any]) -> dict[str, Any]:
    _fields(certificate, ("root", "loads", "uses"))
    if type(certificate["loads"]) is not dict or type(certificate["uses"]) is not list:
        raise ValueError("invalid positional load/use census")
    frame = _unpack(certificate["root"], ROOT)
    frame["loads"] = {g: _unpack(v, LOAD) for g, v in certificate["loads"].items()}
    frame["uses"] = [_unpack(v, USE) for v in certificate["uses"]]
    _validate(frame)
    return frame


def _atom(value: dict[str, Any]) -> tuple[str, str]:
    raw = encode(value).encode()
    if len(raw) > LIMIT:
        raise ValueError("oversized certificate atom")
    return content_sha256(raw), base64.b64encode(zlib.compress(raw, 6)).decode("ascii")


def _decode(key: str, value: str) -> dict[str, Any]:
    decoder = zlib.decompressobj()
    raw = decoder.decompress(base64.b64decode(value, validate=True), LIMIT + 1)
    if len(raw) > LIMIT or not decoder.eof or decoder.unused_data or content_sha256(raw) != key:
        raise ValueError("corrupt or oversized certificate atom")
    document = json.loads(raw)
    if type(document) is not dict or encode(document).encode() != raw:
        raise ValueError("noncanonical certificate atom")
    return cast(dict[str, Any], document)


def _fixed(value: int) -> str:
    if not _integer(value):
        raise ValueError("bounded logical event/counter required")
    return f"{value:08x}"


class JointInferenceAudit:
    """Post-completion admission with atomic certificates and inclusive pins.

    TTL expires optional certificates after age two; active hard leases override
    expiry. Request IDs must be unique ingress identities in the source census.
    Crash recovery, future policy queries and external refetch are unsupported.
    """

    def __init__(self, path: Path, policy: str, budget: int, mode: str = "compact") -> None:
        if policy not in {"static", "lru", "ttl"} or mode not in {"full", "compact"}:
            raise ValueError("unsupported ordinary policy or representation")
        if type(budget) is not int or not 512 <= budget <= 1_000_000:
            raise ValueError("bounded logical budget required")
        if path.exists() or path.is_symlink() or not path.parent.is_dir():
            raise ValueError("fresh owned database required")
        self.path, self.policy, self.budget, self.mode = path, policy, budget, mode
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE state(id INTEGER PRIMARY KEY CHECK(id=1), payload BLOB)")
        self.state: dict[str, Any] = {}
        self._commit(
            {
                "schema": "joint-inference-audit/v1",
                "mode": mode,
                "policy": policy,
                "budget": budget,
                "entries": {},
                "atoms": {},
                **{
                    k: _fixed(0) for k in ("now", "clock", "accepted", "refused", "evicted", "peak")
                },
            }
        )

    @staticmethod
    def _needed(entries: dict[str, Any]) -> set[str]:
        return {
            key for entry in entries.values() for key in [entry["target"], *entry["loads"].values()]
        }

    def _frame(self, entry: dict[str, Any], atoms: dict[str, str]) -> dict[str, Any]:
        identity = [entry["request"], entry["target"], entry["loads"]]
        if content_sha256(encode(identity).encode()) != entry["seal"]:
            raise ValueError("changed certificate manifest")
        target = _decode(entry["target"], atoms[entry["target"]])
        loads = {g: _decode(key, atoms[key]) for g, key in entry["loads"].items()}
        if self.mode == "compact":
            frame = expand({**target, "loads": {g: value["load"] for g, value in loads.items()}})
        else:
            frame = {**target, "loads": {g: value["load"] for g, value in loads.items()}}
        if frame["request"] != entry["request"]:
            raise ValueError("certificate request misjoin")
        resolve(frame)
        return frame

    def _commit(self, state: dict[str, Any]) -> None:
        state = {**state, "peak": _fixed(max(int(state["peak"], 16), len(encode(state).encode())))}
        raw = encode(state).encode()
        if len(raw) > self.budget or self._needed(state["entries"]) != set(state["atoms"]):
            raise ValueError("certificate union budget/dependency invariant violated")
        for entry in state["entries"].values():
            self._frame(entry, state["atoms"])
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES(1,?)", (raw,))
        self.state = state

    def _read(self) -> None:
        row = self.db.execute("SELECT payload FROM state WHERE id=1").fetchone()
        if row is None or bytes(row[0]) != encode(self.state).encode():
            raise ValueError("durable certificate state changed")

    def _subset(self, state: dict[str, Any], entries: dict[str, Any]) -> dict[str, Any]:
        needed = self._needed(entries)
        return {
            **state,
            "entries": entries,
            "atoms": {k: state["atoms"][k] for k in sorted(needed)},
        }

    def advance(self, now: int) -> None:
        self._read()
        if not _integer(now) or now < int(self.state["now"], 16):
            raise ValueError("monotonic bounded logical event required")
        entries = dict(self.state["entries"])
        if self.policy == "ttl":
            entries = {
                k: v
                for k, v in entries.items()
                if int(v["until"], 16) >= now or now - int(v["created"], 16) <= 2
            }
        state = self._subset({**self.state, "now": _fixed(now)}, entries)
        state["evicted"] = _fixed(
            int(state["evicted"], 16) + len(self.state["entries"]) - len(entries)
        )
        self._commit(state)

    def put(self, frame: dict[str, Any], now: int, until: int) -> bool:
        decision = resolve(frame)
        if not _integer(until) or until < now:
            raise ValueError("bounded inclusive lease deadline required")
        self.advance(now)
        request = frame["request"]
        if request in self.state["entries"]:
            raise ValueError("duplicate request ingress")
        conclusive = bool(decision["verdict"] != "unknown")
        packed = materialize(frame) if self.mode == "compact" else frame
        target, value = _atom({k: v for k, v in packed.items() if k != "loads"})
        atoms = {**self.state["atoms"], target: value}
        capsules: dict[str, str] = {}
        for generation, load in packed["loads"].items():
            key, value = _atom({"load": load})
            capsules[generation], atoms[key] = key, value
        clock = int(self.state["clock"], 16) + 1
        entry = {
            "request": request,
            "target": target,
            "loads": capsules,
            "seal": content_sha256(encode([request, target, capsules]).encode()),
            "created": _fixed(now),
            "touch": _fixed(clock),
            "until": _fixed(until) if conclusive else "-1",
        }
        entries = {**self.state["entries"], request: entry}
        state = {**self.state, "atoms": atoms, "clock": _fixed(clock)}
        field = "touch" if self.policy == "lru" else "created"
        optional = sorted(
            (k for k, v in entries.items() if k != request and int(v["until"], 16) < now),
            key=lambda k: (int(entries[k][field], 16), k),
        )
        while len(encode(self._subset(state, entries)).encode()) > self.budget and optional:
            del entries[optional.pop(0)]
        if len(encode(self._subset(state, entries)).encode()) > self.budget:
            self._commit(
                {
                    **self.state,
                    "clock": _fixed(clock),
                    "refused": _fixed(self.refused + int(conclusive)),
                }
            )
            return False
        state = self._subset(state, entries)
        state["accepted"] = _fixed(self.accepted + int(conclusive))
        state["evicted"] = _fixed(self.evicted + len(set(self.state["entries"]) - set(entries)))
        self._commit(state)
        return conclusive

    def query(self, request: str, now: int) -> dict[str, Any] | None:
        self.advance(now)
        entry = self.state["entries"].get(request)
        if entry is None:
            return None
        frame = self._frame(entry, self.state["atoms"])
        clock = int(self.state["clock"], 16) + 1
        entries = {**self.state["entries"], request: {**entry, "touch": _fixed(clock)}}
        self._commit({**self.state, "entries": entries, "clock": _fixed(clock)})
        return {**resolve(frame), "frame": frame}

    @property
    def accepted(self) -> int:
        return int(self.state["accepted"], 16)

    @property
    def refused(self) -> int:
        return int(self.state["refused"], 16)

    @property
    def evicted(self) -> int:
        return int(self.state["evicted"], 16)

    @property
    def peak(self) -> int:
        return int(self.state["peak"], 16)

    def snapshot(self) -> dict[str, Any]:
        self._read()
        raw = encode(self.state)
        return {
            "logical_bytes": len(raw.encode()),
            "accepted": self.accepted,
            "refused": self.refused,
            "evicted": self.evicted,
            "peak": self.peak,
            "raw": raw,
            "state": json.loads(raw),
        }

    def close(self) -> None:
        self.db.close()
