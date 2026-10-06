from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation.audit_bundle_archive import AuditArchive, Bundle, _decision

A, B = "a" * 64, "b" * 64


def load(scope: str, *, observed: str = A) -> dict[str, Any]:
    return {
        "scope": scope,
        "kind": "load",
        "step": 0,
        "domain": [A, B],
        "expected": A,
        "observed": [observed],
        "count": 1,
        "closed": True,
        "status": 200,
        "generation": scope,
    }


def infer(scope: str, parent: str) -> dict[str, Any]:
    return {
        "scope": scope,
        "kind": "infer",
        "step": 0,
        "domain": [A, B],
        "expected": None,
        "observed": [],
        "count": 0,
        "closed": True,
        "status": 200,
        "generation": parent,
    }


@pytest.mark.parametrize(
    "policy", ["static", "ttl", "lru", "lfu", "size_cost", "union_density", "union_exchange"]
)
def test_shared_parent_pins_and_inclusive_lease(tmp_path: Path, policy: str) -> None:
    store = AuditArchive(tmp_path / "store.sqlite", policy, 4096)
    root = load("root")
    assert store.offer(Bundle.build(root), sequence=0, now=0, resident="root", lease_until=2)
    for index in range(1, 5):
        assert (
            store.offer(
                Bundle.build(infer(f"child{index}", "root"), root),
                sequence=index,
                now=0,
                resident="root",
            )
            is None
        )
    # Parent atom is physically shared, but operation scopes remain distinct.
    assert len(store.atoms) == len(store.entries)
    store.advance(2)
    assert "root" in store.leases
    assert store.query("root")["verdict"] == "compliant"  # type: ignore[index]
    store.advance(3)
    assert "root" not in store.leases
    assert "root" in store.entries  # Current resident pin survives expiry.
    assert store.metrics()["peak_logical_bytes"] <= 4096
    store.close()


def test_full_conflicting_census_survives_compression(tmp_path: Path) -> None:
    store = AuditArchive(tmp_path / "store.sqlite", "static", 4096)
    conflict = {**load("root"), "observed": [A, B], "count": 1}
    assert store.offer(Bundle.build(conflict), sequence=0, now=0, resident="root", lease_until=2)
    assert store.query("root")["verdict"] == "conflict"  # type: ignore[index]
    # Two distinct loads with a consistent census are a violation, not conflict.
    extra = {**load("next"), "observed": [A, B], "count": 2}
    assert store.offer(Bundle.build(extra), sequence=1, now=1, resident="next", lease_until=3)
    assert store.query("next")["verdict"] == "violation"  # type: ignore[index]
    store.close()


@pytest.mark.parametrize("at_arrival", [True, False])
def test_same_conclusive_admission_rule(tmp_path: Path, at_arrival: bool) -> None:
    store = AuditArchive(tmp_path / "store.sqlite", "static", 4096)
    unknown = {**load("unknown"), "expected": None}
    accepted = store.offer(
        Bundle.build(unknown),
        sequence=0,
        now=0,
        resident="unknown",
        lease_until=2 if at_arrival else None,
    )
    if at_arrival:
        assert accepted is False
    else:
        assert store.lease("unknown", 2) is False
    assert store.query("unknown")["verdict"] == "unknown"  # type: ignore[index]
    assert not store.offer(
        Bundle.build(infer("child", "unknown"), unknown),
        sequence=1,
        now=0,
        resident="unknown",
        lease_until=2,
    )
    assert store.metrics()["accepted_leases"] == 0
    store.close()


def test_evicted_parent_cannot_be_rehydrated_from_tape(tmp_path: Path) -> None:
    store = AuditArchive(tmp_path / "store.sqlite", "ttl", 2500)
    old = load("old")
    store.offer(Bundle.build(old), sequence=0, now=0, resident="old")
    store.offer(Bundle.build(load("new")), sequence=1, now=3, resident="new")
    assert store.query("old") is None
    assert not store.lease("old", 8)
    with pytest.raises(ValueError, match="resurrect"):
        store.offer(Bundle.build(infer("child", "old"), old), sequence=2, now=3, resident="new")
    assert store.sequence == 1
    store.close()


def test_refusal_preserves_accepted_obligation(tmp_path: Path) -> None:
    store = AuditArchive(tmp_path / "store.sqlite", "static", 2048)
    root = load("root")
    assert store.offer(Bundle.build(root), sequence=0, now=0, resident="root", lease_until=8)
    refusals = 0
    for index in range(1, 7):
        accepted = store.offer(
            Bundle.build(infer(f"child{index}", "root"), root),
            sequence=index,
            now=0,
            resident="root",
            lease_until=8,
        )
        refusals += int(accepted is False)
    assert refusals > 0
    assert store.query("root")["verdict"] == "compliant"  # type: ignore[index]
    assert all(store.query(scope) is not None for scope in store.leases)
    assert store.metrics()["peak_logical_bytes"] <= store.budget
    store.close()


def test_sql_failure_does_not_advance_durable_revision(tmp_path: Path) -> None:
    store = AuditArchive(tmp_path / "store.sqlite", "static", 4096)
    store.db.execute("PRAGMA query_only=ON")
    with pytest.raises(sqlite3.OperationalError):
        store.offer(Bundle.build(load("root")), sequence=0, now=0, resident="root")
    assert store.sequence == -1
    assert store.entries == {}
    assert store.query("root") is None
    store.close()


def test_storage_diagnostic_failure_is_not_false_rollback(tmp_path: Path, monkeypatch: Any) -> None:
    store = AuditArchive(tmp_path / "store.sqlite", "static", 4096)

    def fail() -> int:
        raise OSError("diagnostic unavailable")

    monkeypatch.setattr(store, "storage_bytes", fail)
    store.offer(Bundle.build(load("root")), sequence=0, now=0, resident="root")
    assert store.sequence == 0 and store.now == 0
    assert store.query("root")["verdict"] == "compliant"  # type: ignore[index]
    assert store.metrics()["storage_sample_failures"] == 2
    store.close()


def test_missing_resident_fails_independently_of_selector(tmp_path: Path, monkeypatch: Any) -> None:
    import aletheia_lab.evaluation.audit_bundle_archive as module

    store = AuditArchive(tmp_path / "store.sqlite", "static", 4096)
    monkeypatch.setattr(module, "select", lambda *args: frozenset())
    with pytest.raises(ValueError, match="resident"):
        store.offer(Bundle.build(load("root")), sequence=0, now=0, resident="root")
    assert store.sequence == -1
    store.close()


def test_hash_binding_and_oversized_frame_fail_closed(tmp_path: Path) -> None:
    store = AuditArchive(tmp_path / "store.sqlite", "static", 4096)
    store.offer(Bundle.build(load("root")), sequence=0, now=0, resident="root")
    entry = dict(store.entries["root"])
    entry["scope"] = "foreign"
    with pytest.raises(ValueError, match="manifest"):
        _decision(entry, store.atoms)
    state = json.loads(bytes(store.db.execute("SELECT payload FROM archive").fetchone()[0]))
    state["atoms"][entry["target"]] = "bad"
    with store.db:
        store.db.execute("UPDATE archive SET payload=?", (json.dumps(state).encode(),))
    with pytest.raises(ValueError):
        store.query("root")
    with pytest.raises(ValueError, match="bound"):
        Bundle.build({**load("large"), "padding": "x" * 9000})
    store.close()


def test_incomplete_revision_does_not_seal() -> None:
    with pytest.raises(ValueError, match="closed"):
        Bundle.build({**load("root"), "closed": False})


def test_operation_and_access_order_not_scope_id_or_slot(tmp_path: Path, monkeypatch: Any) -> None:
    import aletheia_lab.evaluation.audit_bundle_archive as module

    recorded: list[tuple[Any, int]] = []
    original = module.select

    def capture(policy: Any, pool: Any, mandatory: Any, cost: Any, cap: Any, now: Any) -> Any:
        recorded.append((pool, now))
        return original(policy, pool, mandatory, cost, cap, now)

    monkeypatch.setattr(module, "select", capture)
    store = AuditArchive(tmp_path / "store.sqlite", "lru", 4096)
    root = load("z-root")
    store.offer(Bundle.build(root), sequence=0, now=100, resident="z-root")
    store.offer(
        Bundle.build(infer("a-child", "z-root"), root),
        sequence=1,
        now=100,
        resident="z-root",
    )
    store.query("z-root")
    store.offer(
        Bundle.build(infer("b-child", "z-root"), root),
        sequence=2,
        now=101,
        resident="z-root",
    )
    pool, clock = recorded[-1]
    assert pool["z-root"]["created"] < pool["a-child"]["created"] < pool["b-child"]["created"]
    assert pool["a-child"]["touch"] < pool["z-root"]["touch"] < pool["b-child"]["touch"]
    assert clock == 4
    assert pool["a-child"]["ttl_age"] == 1
    store.close()


def test_internal_safety_read_does_not_create_popularity(tmp_path: Path) -> None:
    store = AuditArchive(tmp_path / "store.sqlite", "static", 4096)
    store.offer(Bundle.build(load("root")), sequence=0, now=0, resident="root", lease_until=2)
    before = store.db.execute("SELECT payload FROM archive").fetchone()[0]
    assert store.query("root", record_access=False)["verdict"] == "compliant"  # type: ignore[index]
    assert store.db.execute("SELECT payload FROM archive").fetchone()[0] == before
    assert store.clock == 1 and int(store.entries["root"]["hits"], 16) == 0
    store.db.execute("PRAGMA query_only=ON")
    with pytest.raises(sqlite3.OperationalError):
        store.query("root")
    assert store.clock == 1 and int(store.entries["root"]["hits"], 16) == 0
    store.close()
