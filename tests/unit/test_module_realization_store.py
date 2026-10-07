"""Live transport faults, acknowledged frontier and actual-use limits."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aletheia_lab.evaluation.module_realization_store import Collector, audit, recovered


def stream() -> list[dict]:
    binding = {"coefficient": 1.0, "helper_sha256": "A", "code_sha256": "code"}
    return [
        {
            "kind": "load",
            "load_id": "l",
            "label": "B",
            "intended": 2.0,
            "expected_helper_sha256": "B",
            "binding": binding,
            "diagnostics": {"path": "owned"},
        },
        {
            "kind": "predict",
            "request_id": "r-011",
            "load_id": "l",
            "x": 0,
            "y": 0,
            "binding": binding,
            "diagnostics": {"source": "extra"},
        },
        {"kind": "closure", "requests": ["r-011"]},
    ]


@pytest.mark.parametrize("mode", ["compact", "full"])
def test_live_commit_recovers_exact_ack_frontier(tmp_path: Path, mode: str) -> None:
    collector = Collector(tmp_path, {"evidence": mode})
    for record in stream():
        collector.emit(record)
    status = collector.status()
    collector.close()
    records = recovered(tmp_path / "evidence.sqlite")
    assert status["durable_ack_sequences"] == [1, 2, 3]
    assert len(status["durable_ack_digests"]) == 3
    assert ("diagnostics" in records[0]) is (mode == "full")
    assert audit(records, ["r-011"]) == {"verdicts": {"r-011": "violation"}, "closure": True}


@pytest.mark.parametrize(
    "fault", ["drop_predict", "drop_load", "drop_closure", "delay", "duplicate"]
)
def test_fault_is_on_emit_not_historical_trace_deletion(tmp_path: Path, fault: str) -> None:
    collector = Collector(tmp_path, {"evidence": "compact", "fault": fault})
    for record in stream():
        collector.emit(record)
    before = recovered(tmp_path / "evidence.sqlite")
    if fault == "delay":
        assert before == []
        assert collector.status()["durable_ack_sequences"] == []
        collector.flush()
    records = recovered(tmp_path / "evidence.sqlite")
    answer = audit(records, ["r-011"])
    if fault in {"drop_predict", "drop_load"}:
        assert answer["verdicts"]["r-011"] == "unknown"
    elif fault == "drop_closure":
        assert not answer["closure"]
    else:
        assert answer["verdicts"]["r-011"] == "violation"
        assert len(records) == 3
    collector.close()


def test_drain_never_calls_pending_events_acknowledged(tmp_path: Path) -> None:
    collector = Collector(tmp_path, {"evidence": "compact", "durability": "drain"})
    collector.emit(stream()[0])
    assert collector.status()["durable_ack_sequences"] == []
    collector.close()
    assert recovered(tmp_path / "evidence.sqlite") == []


def test_same_numeric_coefficient_is_not_packaged_dependency_identity() -> None:
    records = stream()
    records[0]["intended"] = 1.0
    assert audit(records, ["r-011"])["verdicts"]["r-011"] == "violation"
    records[0]["binding"] = {**records[0]["binding"], "helper_sha256": "other"}
    assert audit(records, ["r-011"])["verdicts"]["r-011"] == "conflict"


def test_row_digest_tamper_fails_closed(tmp_path: Path) -> None:
    collector = Collector(tmp_path, {"evidence": "full"})
    collector.emit(stream()[0])
    collector.close()
    with sqlite3.connect(tmp_path / "evidence.sqlite") as db:
        db.execute("UPDATE events SET digest='changed'")
    with pytest.raises(ValueError, match="digest"):
        recovered(tmp_path / "evidence.sqlite")
