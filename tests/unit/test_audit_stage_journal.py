"""Killed stages remain indeterminate and complete journal damage fails closed."""

from __future__ import annotations

import json

import pytest

from aletheia_lab.evaluation.audit_stage_journal import StageJournal, read_progress, token_progress
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_audit import digest


def config():
    return {"mode": "compact", "count": 2}


def prefix(path):
    journal = StageJournal(path, config())
    journal.append("setup", "start", None, {})
    journal.append("setup", "complete", None, {"duration_ns": 1})
    journal.append("reserve", "start", "call-000", {})
    journal.close()


def test_all_planned_tokens_survive_inflight_interruption(tmp_path):
    path = tmp_path / "progress.jsonl"
    prefix(path)
    result = read_progress(path, config(), interrupted=True)
    rows = token_progress(result["records"], config())
    assert rows[0]["stages"]["reserve"]["status"] == "indeterminate"
    assert rows[0]["stages"]["native"]["status"] == "not_started"
    assert all(stage["status"] == "not_started" for stage in rows[1]["stages"].values())
    with pytest.raises(ValueError, match="terminal"):
        read_progress(path, config(), interrupted=False)


def test_only_abnormal_incomplete_last_line_may_be_retained(tmp_path):
    path = tmp_path / "progress.jsonl"
    prefix(path)
    with path.open("ab") as stream:
        stream.write(b'{"seq":')
    result = read_progress(path, config(), interrupted=True)
    assert result["trailing_bytes"] == 7
    with pytest.raises(ValueError, match="unfinished"):
        read_progress(path, config(), interrupted=False)
    with path.open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(json.JSONDecodeError):
        read_progress(path, config(), interrupted=True)


@pytest.mark.parametrize("field,value", [("previous", "f" * 64), ("seq", 99)])
def test_even_rehashed_interior_damage_is_rejected(tmp_path, field, value):
    path = tmp_path / "progress.jsonl"
    prefix(path)
    lines = path.read_text().splitlines()
    record = json.loads(lines[1])
    record.pop("sha256")
    record[field] = value
    lines[1] = encode({**record, "sha256": digest(record)})
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError, match="prefix"):
        read_progress(path, config(), interrupted=True)


def test_parent_config_is_authoritative_and_missing_file_is_not_success(tmp_path):
    path = tmp_path / "progress.jsonl"
    assert read_progress(path, config(), interrupted=True)["records"] == []
    with pytest.raises(ValueError, match="no progress"):
        read_progress(path, config(), interrupted=False)
    prefix(path)
    with pytest.raises(ValueError, match="parent/worker"):
        read_progress(path, {"mode": "compact", "count": 3}, interrupted=True)


def test_wrong_stage_order_and_false_terminal_are_rejected(tmp_path):
    path = tmp_path / "progress.jsonl"
    journal = StageJournal(path, config())
    journal.append("native", "start", "call-000", {})
    journal.close()
    with pytest.raises(ValueError, match="order"):
        read_progress(path, config(), interrupted=True)


def test_error_is_not_a_completed_worker(tmp_path):
    path = tmp_path / "progress.jsonl"
    journal = StageJournal(path, config())
    journal.append("setup", "start", None, {})
    journal.append("setup", "error", None, {"duration_ns": 3, "error_type": "RuntimeError"})
    journal.close()
    assert read_progress(path, config(), interrupted=True)["records"][-1]["event"] == "error"
    with pytest.raises(ValueError, match="disposition"):
        read_progress(path, config(), interrupted=False)
