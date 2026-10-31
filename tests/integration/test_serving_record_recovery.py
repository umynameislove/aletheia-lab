"""Actual owner kill and fresh-process recovery; no server or provider required."""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts/serving_recovery/sqlite_trace.py"
_SPEC = importlib.util.spec_from_file_location("sqlite_trace", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
study = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(study)


def sample():
    return {
        "records": [
            {
                "id": f"row-{i}",
                "state": {"shared": 7},
                "record": {"output": i, "label": "authored fixture"},
            }
            for i in range(3)
        ]
    }


@pytest.mark.integration
@pytest.mark.skipif(os.name != "posix", reason="owned SIGKILL experiment requires POSIX")
def test_exact_records_survive_writer_kill_for_every_layout(tmp_path):
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps(sample()))
    destination = tmp_path / "results"
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            str(_SCRIPT),
            "execute",
            "--input",
            str(inputs),
            "--output",
            str(destination),
        ],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads((destination / "analysis.json").read_text())
    assert result["complete"] == result["planned"] == 15
    for summary in result["summaries"]:
        assert summary["accepted"] == summary["correct_on_time"] == 15
        assert summary["accepted_but_unserved"] == 0
    for row in result["results"]:
        assert row["exit_code"] == -9
        assert row["negative_queries_missing"] == {
            "__uncommitted_control__": True,
            "__never_captured__": True,
        }
        assert row["recovered_projection_sha256"] == result["source_projection_sha256"]
    repeated = subprocess.run(
        [
            sys.executable,
            "-B",
            str(_SCRIPT),
            "execute",
            "--input",
            str(inputs),
            "--output",
            str(destination),
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert repeated.returncode != 0
    assert json.loads((destination / "analysis.json").read_text()) == result


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"id": "a", "state": {}, "record": {}}] * 2,
        [{"id": "__uncommitted_control__", "state": {}, "record": {}}],
        [{"id": 1, "state": {}, "record": {}}],
    ],
)
def test_invalid_census_is_not_admitted(tmp_path, rows):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"records": rows}))
    with pytest.raises(ValueError):
        study.read_inputs(path)


@pytest.mark.parametrize("arm", study.ARMS)
def test_corrupt_recovery_never_falls_back_to_original_input(tmp_path, arm):
    db = tmp_path / "state.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE states (key TEXT PRIMARY KEY, body BLOB, checksum TEXT)")
        conn.execute(
            "CREATE TABLE requests (seq INTEGER PRIMARY KEY, id TEXT, state_key TEXT, body BLOB, checksum TEXT)"
        )
        if arm != "full":
            conn.execute("INSERT INTO states VALUES (?,?,?)", ("wrong", b"{}", "bad"))
        conn.execute("INSERT INTO requests VALUES (?,?,?,?,?)", (0, "row", "wrong", b"{}", "bad"))
    with pytest.raises((ValueError, KeyError)):
        study.recover(arm, db)


def test_no_db_or_log_output_inside_checkout(tmp_path):
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps(sample()))
    with pytest.raises(ValueError, match="outside the checkout"):
        study.execute(inputs, _SCRIPT.parent / "accidental-output")
    assert not (_SCRIPT.parent / "accidental-output").exists()
