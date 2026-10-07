"""Physical row deltas must not alter the inherited audit service."""

from __future__ import annotations

import copy
import sqlite3
from pathlib import Path

import pytest

from aletheia_lab.evaluation.auditability_model import check_model, determine
from aletheia_lab.evaluation.incident_audit_archive import POLICIES, IncidentAuditArchive
from aletheia_lab.evaluation.incident_audit_incremental import IncrementalAuditArchive
from aletheia_lab.evaluation.request_model_audit import digest


def frame(token: str, *, failed: bool = False) -> dict:
    incoming, output, state = digest([1]), digest([2]), digest("state")
    return {
        "token": token,
        "requested": "aaa",
        "kind": "non_batched",
        "input": incoming,
        "output": None if failed else output,
        "closed": True,
        "failed": failed,
        "loads": {}
        if failed
        else {"shared": {"model": "aaa", "artifact": state, "fingerprint": state}},
        "uses": []
        if failed
        else [
            {
                "token": token,
                "batch": token,
                "index": 0,
                "generation": "shared",
                "input": incoming,
                "output": output,
                "fingerprint": state,
            }
        ],
    }


@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize("budget", [1536, 4096, 65536])
def test_exact_transcript_and_reopen(tmp_path: Path, policy: str, budget: int) -> None:
    whole = IncidentAuditArchive(tmp_path / "whole.sqlite", policy, budget)
    delta = IncrementalAuditArchive(tmp_path / "delta.sqlite", policy, budget)
    for ordinal in range(12):
        assert whole.put(frame(f"r{ordinal}"), now=ordinal) == delta.put(
            frame(f"r{ordinal}"), now=ordinal
        )
        assert whole._document(whole.state) == delta._document(delta.state)
    assert whole.demand("late", ["r0", "r11"], now=12, until=20) == delta.demand(
        "late", ["r0", "r11"], now=12, until=20
    )
    assert whole.query(["r0", "r11"], now=13) == delta.query(["r0", "r11"], now=13)
    state = delta.snapshot()["state_sha256"]
    whole.close()
    delta.close()
    recovered = IncrementalAuditArchive(tmp_path / "delta.sqlite", policy, budget, reopen=True)
    assert recovered.snapshot()["state_sha256"] == state
    recovered.close()


def test_sql_error_rolls_back_memory_and_all_row_families(tmp_path: Path) -> None:
    store = IncrementalAuditArchive(tmp_path / "delta.sqlite", "static", 16384)
    before = copy.deepcopy(store.state)
    store.db.execute(
        "CREATE TRIGGER fail_entry BEFORE INSERT ON fragments "
        "WHEN NEW.family='entries' BEGIN SELECT RAISE(ABORT,'test'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.put(frame("r"), now=0)
    assert store.state == store._read() == before
    store.close()
    recovered = IncrementalAuditArchive(tmp_path / "delta.sqlite", "static", 16384, reopen=True)
    assert recovered.state == before
    recovered.close()


def test_unchanged_shared_atom_is_not_resubmitted(tmp_path: Path) -> None:
    store = IncrementalAuditArchive(tmp_path / "delta.sqlite", "static", 65536)
    assert store.put(frame("a"), now=0)
    changes: list[str] = []
    store.db.set_trace_callback(changes.append)
    assert store.put(frame("b"), now=1)
    shared = next(
        key
        for key in store.state["atoms"]
        if key not in {entry["target"] for entry in store.state["entries"].values()}
    )
    assert not any("INSERT OR REPLACE" in query and shared in query for query in changes)
    assert len(store.state["atoms"]) == 3
    store.close()


def test_admitted_native_unknown_and_growth_overrun_stay_unserved(tmp_path: Path) -> None:
    store = IncrementalAuditArchive(tmp_path / "delta.sqlite", "static", 8192)
    assert store.reserve("fail", now=0, until=20, bound=4096)
    assert store.put(frame("fail", failed=True), now=1)
    assert store.query(["fail"], now=2) == {"fail": "unknown"}
    assert store.reserve("growth", now=3, until=20, bound=512)
    assert not store.put(frame("growth"), now=4)
    assert store.state["accepted"] == 2 and store.state["overruns"] == 1
    assert store.state["refused"] == 0
    store.close()


def test_finite_projection_counterhistory_is_not_resolver_unknown() -> None:
    result = check_model()
    first, second = result["equal_entire_behavior_projection_counterpair"]
    assert first["used"] != second["used"]
    assert determine([first, second]) == "unknown"
    assert determine([]) == "inconsistent"
    assert result["history_count"] == 64
