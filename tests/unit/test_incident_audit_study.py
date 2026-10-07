"""Offered-demand census, honest refusals, secondary recovery and native controls."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest
from test_incident_audit_archive import frame

from aletheia_lab.evaluation import incident_audit_service as service
from aletheia_lab.evaluation import incident_audit_study as study
from aletheia_lab.evaluation.incident_audit_archive import IncidentAuditArchive
from aletheia_lab.evaluation.incident_audit_service import RecoveryTier, summarize
from aletheia_lab.evaluation.incident_audit_verification import witness_answers


def test_offered_denominator_does_not_hide_unknown_refusal_or_deadline() -> None:
    rows = [
        {"frame": frame("a"), "reference": "compliant"},
        {"frame": frame("b", failed=True), "reference": "unknown"},
    ]
    offers = [
        {
            "accepted": False,
            "answers": {"a": None},
            "queried_at": 0,
            "deadline": 2,
            "refetched": [],
            "refetch_failed": ["a"],
        },
        {
            "accepted": True,
            "answers": {"b": "unknown"},
            "queried_at": 0,
            "deadline": 2,
            "refetched": [],
            "refetch_failed": [],
        },
        {
            "accepted": True,
            "answers": {"a": "compliant"},
            "queried_at": 3,
            "deadline": 2,
            "refetched": ["a"],
            "refetch_failed": [],
        },
    ]
    for item in offers:
        item["query_wall_ns"], item["response_budget_ns"] = 1, 50
    result = summarize(offers, rows)
    assert result["offered"] == 3 and result["accepted"] == 2 and result["refused"] == 1
    assert result["wrong"] == 0 and result["accepted_but_unserved"] == 2
    assert result["deadline_misses"] == 1 and result["on_time_correct"] == 0


def test_secondary_is_physically_retained_and_charged_not_a_truth_tape(tmp_path: Path) -> None:
    tier = RecoveryTier(tmp_path / "secondary.sqlite", 8192)
    value = frame("a")
    tier.put(value)
    assert tier.get("a") == value and tier.get("missing") is None
    result = tier.finish()
    assert result["reads"] == 2 and result["retrieved_bytes"] > 0
    assert result["logical_evidence_and_key_bytes"] > 0 and result["closed_db_bytes"] > 0


def test_actual_response_cutoff_keeps_accepted_unserved_and_recovery_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = IncidentAuditArchive(tmp_path / "primary.sqlite", "size_cost", 8192)
    tier = RecoveryTier(tmp_path / "secondary.sqlite", 8192)
    tier.put(frame("a"))
    readings = iter([0, 100])
    monkeypatch.setattr(service, "perf_counter_ns", lambda: next(readings))
    item = {"id": "late", "kind": "incident", "scopes": ["a"], "offered_at": 1, "deadline": 2}
    # Recovery uses the same timer module; temporarily preserve its measured methods.
    readings = iter(range(0, 1000, 100))
    result = service.offer(store, "durable_secondary", tier, item, 50)
    assert result["accepted"] and result["answers"] == {"a": "compliant"}
    assert result["queried_at"] == 1 and result["query_wall_ns"] > 50
    assert result["recovery_meter"]["reads"] == 1
    assert result["recovery_meter"]["retrieved_bytes"] > 0
    assert witness_answers(result["witness"]) == result["answers"]
    assert (
        summarize([result], [{"frame": frame("a"), "reference": "compliant"}])[
            "accepted_but_unserved"
        ]
        == 1
    )
    store.close()
    tier.finish()


def test_timeout_remains_a_terminal_cell_with_partial_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def timeout(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(["child"], 120, output=b"partial", stderr=b"failure")

    monkeypatch.setattr(study.subprocess, "run", timeout)
    result = study._child(tmp_path, tmp_path, "observed", 0)
    assert result["status"] == "worker_timeout"
    assert (tmp_path / "observed-0.stdout").read_bytes() == b"partial"
    assert study.aggregate([result])["failed_processes"] == 1


@pytest.mark.skipif(
    importlib.util.find_spec("onnxruntime") is None, reason="optional native runtime"
)
def test_actual_upstream_initializer_and_failed_call_preserve_strong_native() -> None:
    from aletheia_lab.evaluation.incident_audit_source import InitializerWorkload, source_summary

    workload = InitializerWorkload()
    rows = [workload.call(i, captured=i != 9) for i in range(16)]
    assert rows[1]["output"] == [[2, 2], [12, 12], [30, 30]]
    assert rows[0]["output"] == [[1, 4], [9, 16], [25, 36]]
    assert (
        rows[9]["capture_answer"] == "unknown" and rows[9]["strong_source_history"] == "compliant"
    )
    assert rows[11]["native_error"] and rows[11]["reference"] == "unknown"
    result = source_summary(rows)
    assert result["strong_source_history_correct"] == 15 and result["captured_false"] == 0
    assert rows[15]["reference"] == "violation" and rows[15]["output"] == [[0, 0]] * 3
    repaired = workload.call(15, route_repair=True)
    assert repaired["reference"] == "compliant" and repaired["output"] == rows[15]["output"]
    assert result["independent_operator_incident"] is False
