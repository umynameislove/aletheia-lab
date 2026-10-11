"""Online promises, complete dependencies and irreversible historical loss."""

from __future__ import annotations

import copy
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation.incident_audit_archive import POLICIES, IncidentAuditArchive
from aletheia_lab.evaluation.request_model_audit import digest


def frame(token: str, generation: str = "g", *, failed: bool = False) -> dict[str, Any]:
    output = None if failed else digest([2])
    return {
        "token": token,
        "requested": "aaa",
        "kind": "non_batched",
        "input": digest([1]),
        "output": output,
        "closed": True,
        "failed": failed,
        "loads": {}
        if failed
        else {
            generation: {
                "model": "aaa",
                "artifact": digest("asset"),
                "fingerprint": digest("weights"),
            }
        },
        "uses": []
        if failed
        else [
            {
                "token": token,
                "batch": token,
                "index": 0,
                "generation": generation,
                "input": digest([1]),
                "output": output,
                "fingerprint": digest("weights"),
            }
        ],
    }


@pytest.mark.parametrize("policy", POLICIES)
def test_reserved_completion_and_reopen_protect_all_dependencies(
    tmp_path: Path, policy: str
) -> None:
    path = tmp_path / "archive.sqlite"
    store = IncidentAuditArchive(path, policy, 16384)
    assert store.reserve("a", now=0, until=20, bound=4096)
    assert store.put(frame("a"), now=1)
    assert store.put(frame("b"), now=2)
    assert store.demand("first", ["a", "b"], now=3, until=12)
    assert store.demand("second", ["b"], now=4, until=15)
    store.drain("first", now=5)
    expected = store.snapshot()["state_sha256"]
    assert len(store.state["atoms"]) == 3  # Shared load + two request atoms.
    store.close()
    recovered = IncidentAuditArchive(path, policy, 16384, reopen=True)
    assert recovered.snapshot()["state_sha256"] == expected
    assert recovered.query(["a", "b"], now=6) == {"a": "compliant", "b": "compliant"}
    assert recovered.state["leases"]["second"]["scopes"] == ["b"]
    recovered.close()


@pytest.mark.parametrize("policy", POLICIES)
def test_late_pins_never_restore_evicted_prefix(tmp_path: Path, policy: str) -> None:
    store = IncidentAuditArchive(tmp_path / "archive.sqlite", policy, 1500)
    for ordinal in range(16):
        store.put(frame(f"r{ordinal}"), now=ordinal)
    missing = [f"r{i}" for i in range(16) if f"r{i}" not in store.state["entries"]]
    assert missing and store.snapshot()["evicted"] > 0
    assert not store.demand("late", missing, now=16, until=20)
    assert all(value is None for value in store.query(missing, now=17).values())
    store.close()


def test_pending_namespace_cannot_overwrite_an_existing_promise(tmp_path: Path) -> None:
    store = IncidentAuditArchive(tmp_path / "archive.sqlite", "static", 16384)
    store.put(frame("old"), now=0)
    assert store.demand("late", ["old"], now=1, until=30)
    assert store.reserve("future", now=1, until=10, bound=4096)
    before = store.snapshot()["state_sha256"]
    with pytest.raises(ValueError, match="identifier"):
        store.demand("pre:future", ["old"], now=2, until=30)
    assert store.snapshot()["state_sha256"] == before
    assert store.put(frame("future"), now=2)
    assert store.state["leases"]["late"]["scopes"] == ["old"]
    store.close()


def test_unknown_after_admission_is_not_retroactively_refused(tmp_path: Path) -> None:
    store = IncidentAuditArchive(tmp_path / "archive.sqlite", "lru", 8192)
    assert store.reserve("failed", now=0, until=10, bound=4096)
    assert store.put(frame("failed", failed=True), now=1)
    assert store.query(["failed"], now=2) == {"failed": "unknown"}
    assert store.state["accepted"] == 1 and store.state["refused"] == 0
    store.close()


def test_growth_overrun_is_recorded_as_a_broken_admission(tmp_path: Path) -> None:
    store = IncidentAuditArchive(tmp_path / "archive.sqlite", "static", 8192)
    assert store.reserve("a", now=0, until=10, bound=512)
    assert not store.put(frame("a"), now=1)
    assert store.state["accepted"] == 1
    assert store.state["overruns"] == 1 and store.state["refused"] == 0
    assert "a" not in store.state["pending"]
    store.close()


def test_failed_write_rolls_back_but_telemetry_cannot_revoke_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = IncidentAuditArchive(tmp_path / "archive.sqlite", "static", 8192)
    original = copy.deepcopy(store.state)
    store.db.execute(
        "CREATE TRIGGER abort_write BEFORE INSERT ON state BEGIN SELECT RAISE(ABORT,'test'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.reserve("a", now=1, until=10, bound=4096)
    assert store.state == original and store._read() == original
    store.db.execute("DROP TRIGGER abort_write")

    def fail() -> int:
        raise OSError("injected telemetry failure")

    monkeypatch.setattr(store, "storage_bytes", fail)
    assert store.reserve("a", now=1, until=10, bound=4096)
    assert store.state == store._read() and store.metrics["storage_sample_failures"] == 1
    store.close()


def test_fixed_width_bookkeeping_does_not_break_a_tight_pinned_query(tmp_path: Path) -> None:
    store = IncidentAuditArchive(tmp_path / "archive.sqlite", "static", 8192)
    store.put(frame("a"), now=0)
    assert store.demand("d", ["a"], now=0, until=100)
    original = store._charge(store.state)
    for tick in range(30):
        assert store.query(["a"], now=tick) == {"a": "compliant"}
        assert store._charge(store.state) == original
    store.close()


def test_corrupt_dependency_or_manifest_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "archive.sqlite"
    store = IncidentAuditArchive(path, "static", 8192)
    store.put(frame("a"), now=0)
    store.state["entries"]["a"]["seal"] = "0" * 64
    with pytest.raises(ValueError, match="outside"):
        store.query(["a"], now=1)
    store.close()


def test_refused_reservation_does_not_cancel_existing_accepted_lease(tmp_path: Path) -> None:
    store = IncidentAuditArchive(tmp_path / "archive.sqlite", "size_cost", 8192)
    store.put(frame("a"), now=0)
    store.demand("d", ["a"], now=0, until=100)
    assert not store.reserve("b", now=1, until=10, bound=16384)
    assert store.query(["a"], now=2) == {"a": "compliant"}
    assert store.state["refused"] == 1
    store.close()


@pytest.mark.parametrize("policy", POLICIES)
def test_required_recovery_union_displaces_hot_optional_evidence_atomically(
    tmp_path: Path, policy: str
) -> None:
    store = IncidentAuditArchive(tmp_path / "archive.sqlite", policy, 2400)
    store.put(frame("unrelated"), now=0)
    for tick in range(40):
        store.query(["unrelated"], now=tick)
    assert store.restore_union([frame("needed-a"), frame("needed-b")], now=40)
    assert store.demand("audit", ["needed-a", "needed-b"], now=40, until=50)
    before = store.snapshot()["state_sha256"]
    assert not store.restore_union([frame(f"oversized-{i}") for i in range(10)], now=40)
    assert store.snapshot()["state_sha256"] == before
    assert store.query(["needed-a", "needed-b"], now=41) == {
        "needed-a": "compliant",
        "needed-b": "compliant",
    }
    store.close()
