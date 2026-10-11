"""Bounded collector, census, source-binding and private replay checks; no SDK."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from aletheia_lab.evaluation import litserve_evidence_study as study
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256


def test_live_collector_really_delays_durable_receipts_and_drains(tmp_path):
    event = {"pid": 1, "sequence": 0, "time_ns": time.monotonic_ns(), "kind": "fixture"}
    write_new_file(tmp_path / "producer-1.jsonl", (encode(event) + "\n").encode())
    collector = study.Collector(tmp_path, 0.05)
    assert collector.receipts == []
    collector.thread.start()
    collector.close()
    assert collector.failure is None
    assert len(collector.receipts) == 1
    assert collector.receipts[0]["received_ns"] - event["time_ns"] >= 50_000_000
    with sqlite3.connect(tmp_path / "collector.sqlite") as connection:
        assert json.loads(connection.execute("SELECT payload FROM events").fetchone()[0]) == event


def test_truncated_live_tail_is_not_discarded_as_a_final_observation(tmp_path):
    write_new_file(tmp_path / "producer-1.jsonl", b'{"pid":1,"sequence":0}')
    assert study.producer_events(tmp_path) == []


def test_child_environment_does_not_copy_credentials_proxies_or_python_path(tmp_path, monkeypatch):
    for key in ("OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "HTTPS_PROXY", "PYTHONPATH"):
        monkeypatch.setenv(key, "sentinel")
    value = study.environment(tmp_path, tmp_path / "sdk")
    assert "OPENAI_API_KEY" not in value and "HTTPS_PROXY" not in value
    assert value["PYTHONPATH"] != "sentinel"
    assert "AWS_SECRET_ACCESS_KEY" not in value


def test_fresh_directory_gate_happens_before_metadata_or_any_model(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "design", lambda *a: pytest.fail("must not probe runtime"))
    with pytest.raises(ValueError, match="fresh"):
        study.run(tmp_path, tmp_path, Path("python"), tmp_path)


def test_missing_requests_and_failed_references_stay_in_planned_denominator():
    source = {
        "arm": "native",
        "collector_delay": 0.0,
        "replicate": 0,
        "failure": "TimeoutError",
        "rows": [],
        "events": [],
        "receipts": [],
    }
    result = study._cell_analysis(source)
    summary = study.aggregate([result])
    assert result["planned"] == result["missing"] == 11
    assert summary["planned_requests"] == 11 and summary["offered_requests"] == 0
    assert summary["complete"] is False
    assert summary["disposition"] == "incomplete_census_no_transfer_closeout"


def test_code_binding_includes_analysis_and_native_adapter():
    assert study.PROTOCOL in study.FILES
    assert "src/aletheia_lab/evaluation/litserve_evidence_source.py" in study.FILES
    assert "src/aletheia_lab/evaluation/litserve_evidence_analysis.py" in study.FILES


def test_replay_rejects_rehashed_result_with_changed_source(tmp_path):
    root, directory = tmp_path / "repo", tmp_path / "study"
    root.mkdir()
    directory.mkdir()
    plan = {"bindings": {}, "cells": [{"arm": "native", "collector_delay": 0.0, "replicate": 0}]}
    write_new_file(directory / "plan.json", encode(plan).encode())
    result = {
        "plan_sha256": content_sha256((directory / "plan.json").read_bytes()),
        "source_bindings": {"cell-00": "changed"},
        "cells": [],
        "summary": {},
    }
    result["results_sha256"] = content_sha256(encode(result).encode())
    write_new_file(directory / "results.json", encode(result).encode())
    (directory / "cell-00").mkdir()
    write_new_file(directory / "cell-00/source.json", b"{}")
    write_new_file(directory / "cell-00/assessed-source.json", b"{}")
    with pytest.raises(ValueError, match="raw source"):
        study._rebuild_cell(
            directory / "cell-00", plan["cells"][0], {"native": "changed", "assessed": "changed"}
        )


def test_final_reader_never_calls_equal_trimmed_prefix_complete(tmp_path):
    write_new_file(tmp_path / "producer-1.jsonl", b'{"pid":1,"sequence":0}')
    assert study.producer_events(tmp_path) == []
    with pytest.raises(ValueError, match="incomplete"):
        study.final_events(tmp_path)


def test_cleanup_failure_is_recorded_not_raised_before_source_publication(tmp_path, monkeypatch):
    collector = study.Collector(tmp_path, 0)
    monkeypatch.setattr(
        study, "_terminate", lambda p: (_ for _ in ()).throw(RuntimeError("cleanup"))
    )
    assert study._finish(object(), collector) == "cleanup:RuntimeError"


def test_launch_failure_keeps_planned_cell_and_does_not_open_socket(tmp_path, monkeypatch):
    monkeypatch.setattr(
        study, "execute_cell", lambda *a: (_ for _ in ()).throw(PermissionError("bind"))
    )
    cell = {"arm": "native", "collector_delay": 0.0, "replicate": 0}
    source = study._execute_or_failure(tmp_path, tmp_path / "cell", Path("python"), tmp_path, cell)
    assert source["failure"] == "PermissionError"
    assert study._cell_analysis(source)["missing"] == 11
    assert (tmp_path / "cell/source.json").is_file()


def test_protocol_separates_native_queue_timeout_from_operator_deadline():
    root = Path(__file__).resolve().parents[2]
    protocol = json.loads((root / study.PROTOCOL).read_bytes())
    assert protocol["version"] == "0.2.19"
    assert protocol["provider_calls"] == protocol["protected_runs"] == 0
    assert "not a native" in protocol["contracts"]["deadline"]
    assert len(protocol["families"]) == 3
    assert protocol["process_replicates"] == 2
