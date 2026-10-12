"""Killed stages remain indeterminate and complete journal damage fails closed."""

from __future__ import annotations

import hashlib
import json
from time import perf_counter_ns

import pytest

from aletheia_lab.evaluation.audit_stage_journal import (
    StageJournal,
    read_progress,
    roster,
    token_progress,
)
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
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    with pytest.raises(ValueError, match="prefix"):
        read_progress(path, config(), interrupted=True)


@pytest.mark.parametrize("interrupted", [False, True])
def test_crlf_translation_is_not_a_canonical_journal(tmp_path, interrupted):
    path = tmp_path / "progress.jsonl"
    prefix(path)
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert len(read_progress(path, config(), interrupted=True)["records"]) == 4
    path.write_bytes(raw.replace(b"\n", b"\r\n"))
    with pytest.raises(ValueError, match="journal prefix identity/encoding differs"):
        read_progress(path, config(), interrupted=interrupted)


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


@pytest.mark.parametrize("count", [True, 1.0, 0, -1, 65])
def test_planned_roster_is_bounded_integer_census(count):
    with pytest.raises(ValueError, match="bounded nonempty"):
        roster({"count": count})
    assert roster({"count": 64})[-1] == "call-063"


def test_empty_progress_is_not_completed(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_bytes(b"")
    assert read_progress(path, config(), interrupted=True)["records"] == []
    with pytest.raises(ValueError, match="empty completed"):
        read_progress(path, config(), interrupted=False)


def test_owned_symlink_boundaries_fail_closed(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_bytes(b"")
    link = tmp_path / "linked.jsonl"
    try:
        link.symlink_to(path)
    except OSError:
        pytest.skip("host does not permit creating the symlink capability fixture")
    with pytest.raises(ValueError, match="owned journal"):
        StageJournal(link, config())
    with pytest.raises(ValueError, match="owned journal"):
        read_progress(link, config(), interrupted=True)


def test_real_stage_writer_returns_a_hash_linked_owned_prefix(tmp_path):
    path = tmp_path / "progress.jsonl"
    writer = StageJournal(path, config())
    try:
        writer.origin = perf_counter_ns()
        writer.append("setup", "start", None, {})
        writer.append("setup", "complete", None, {"duration_ns": 0})
    finally:
        writer.close()
    encoded = [json.loads(line) for line in path.read_bytes().splitlines()]
    previous = "0" * 64
    for index, record in enumerate(encoded):
        checksum = record.pop("sha256")
        raw = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        assert record["seq"] == index
        assert record["previous"] == previous
        assert checksum == hashlib.sha256(raw).hexdigest()
        previous = checksum
    observed = read_progress(path, config(), interrupted=True)
    assert len(observed["records"]) == 3
    assert token_progress(observed["records"], config())[0]["stages"]["reserve"]["status"] == (
        "not_started"
    )
