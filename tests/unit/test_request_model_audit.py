"""Synthetic audit/service falsifiers; no Ray process or native model load."""

from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation.request_model_audit import (
    analyze,
    correspondence,
    digest,
    expand,
    frames,
    materialize,
    resolve,
)
from aletheia_lab.evaluation.request_model_retention import RequestModelArchive


def frame(token: str = "request-a", model: str = "aaa") -> dict[str, Any]:
    generation = "generation-" + model
    return {
        "token": token,
        "requested": "aaa",
        "kind": "batched",
        "input": digest([token, "input"]),
        "output": digest([token, "output"]),
        "closed": True,
        "failed": False,
        "uses": [
            {
                "token": token,
                "batch": "batch-" + token,
                "index": 0,
                "generation": generation,
                "input": digest([token, "input"]),
                "output": digest([token, "output"]),
                "fingerprint": digest([model, "object"]),
            }
        ],
        "loads": {
            generation: {
                "model": model,
                "artifact": digest([model, "artifact"]),
                "fingerprint": digest([model, "object"]),
            }
        },
    }


@pytest.mark.parametrize("actual,expected", [("aaa", "compliant"), ("bbb", "violation")])
def test_actual_model_binding_determines_selected_model_compliance(
    actual: str, expected: str
) -> None:
    value = frame(model=actual)
    assert resolve(value) == correspondence(value) == expected
    value["requested"] = actual
    assert resolve(value) == correspondence(value) == "compliant"


@pytest.mark.parametrize("field", ["token", "input", "output", "fingerprint"])
def test_foreign_invocation_or_operand_object_binding_is_conflict(field: str) -> None:
    value = frame()
    value["uses"][0][field] = "foreign-token" if field == "token" else digest("foreign")
    assert resolve(value) == correspondence(value) == "conflict"
    assert expand(materialize(value)) == value


@pytest.mark.parametrize("missing", ["load", "use", "closure", "output", "native_failure"])
def test_missing_or_failed_evidence_is_unknown(missing: str) -> None:
    value = frame()
    if missing == "load":
        value["loads"] = {}
    elif missing == "use":
        value["uses"] = []
    elif missing == "closure":
        value["closed"] = False
    elif missing == "output":
        value["output"] = None
    else:
        value["failed"] = True
    assert resolve(value) == correspondence(value) == "unknown"
    assert expand(materialize(value)) == value


def test_duplicate_contradiction_is_not_hidden_by_native_failure() -> None:
    value = frame()
    value["uses"].append(copy.deepcopy(value["uses"][0]))
    value.update(failed=True, output=None)
    assert resolve(value) == correspondence(value) == "conflict"


def test_original_ordinal_join_rejects_a_non_singleton_batch() -> None:
    source = {
        "mode": "original",
        "rows": [
            {
                "token": "original-request",
                "scenario": "serial",
                "ordinal": 0,
                "requested_model": "aaa",
                "kind": "batched",
                "arg": "1",
                "output": "Response from model_obj_for_aaa 1",
                "http_status": 200,
            }
        ],
        "loads": {
            "g": {
                "model": "aaa",
                "raw_hex": None,
                "parameters": {"value": "model_obj_for_aaa"},
                "object_fingerprint": digest("object"),
            }
        },
        "batches": [
            {
                "scenario": "serial",
                "batch": "not-singleton",
                "completed": True,
                "actual_generation": "g",
                "actual_object": {"object_fingerprint": digest("object")},
                "members": [
                    {"token": None, "arg": "1", "output": "Response from model_obj_for_aaa 1"},
                    {"token": None, "arg": "1", "output": "Response from model_obj_for_aaa 1"},
                ],
            }
        ],
    }
    assert resolve(frames(source)[0]) == "unknown"


def test_original_output_baseline_does_not_infer_wrong_model_from_arbitrary_text() -> None:
    source = {
        "mode": "original",
        "rows": [
            {
                "token": "unrecognized-response",
                "scenario": "serial",
                "ordinal": 0,
                "requested_model": "aaa",
                "kind": "batched",
                "arg": "1",
                "output": "successful response with no model identity",
                "http_status": 200,
            }
        ],
        "loads": {},
        "batches": [],
    }
    assert analyze(source)["outcomes"][0]["original_output"] == "unknown"


@pytest.mark.parametrize("mode", ["full", "compact"])
@pytest.mark.parametrize("policy", ["static", "lru", "ttl"])
def test_hard_pin_survives_pressure_refusal_at_inclusive_deadline(
    tmp_path: Path, policy: str, mode: str
) -> None:
    archive = RequestModelArchive(tmp_path / "pressure.sqlite", policy, 2048, mode)
    try:
        assert archive.put(frame("old", "bbb"), 0, 4)
        for index in range(1, 5):
            archive.put(frame(f"new-{index}"), index, index + 4)
            assert archive.snapshot()["logical_bytes"] <= archive.budget
        assert archive.refused > 0
        answer = archive.query("old", 4)
        assert answer is not None and answer["verdict"] == "violation"
        if policy == "ttl":
            assert archive.query("old", 5) is None
    finally:
        archive.close()


@pytest.mark.parametrize("mode", ["full", "compact"])
def test_exact_blob_cost_shared_dependencies_and_durable_tampering(
    tmp_path: Path, mode: str
) -> None:
    archive = RequestModelArchive(tmp_path / "cost.sqlite", "static", 8192, mode)
    try:
        assert archive.put(frame("first"), 0, 0)
        assert archive.put(frame("second"), 1, 1)
        snapshot = archive.snapshot()
        state = json.loads(snapshot["raw"])
        assert state["entries"]["first"]["loads"] == state["entries"]["second"]["loads"]
        assert len(state["atoms"]) == 3  # two requests, one shared loaded-object capsule
        raw = archive.db.execute("SELECT payload FROM state").fetchone()[0]
        assert snapshot["logical_bytes"] == len(raw) == len(snapshot["raw"].encode())
        state["now"] = "00000002"
        archive.db.execute("UPDATE state SET payload=?", (json.dumps(state).encode(),))
        archive.db.commit()
        with pytest.raises(ValueError, match="durable"):
            archive.query("first", 2)
    finally:
        archive.close()


@pytest.mark.parametrize("operation", ["put", "query", "advance"])
def test_failed_durable_transition_preserves_old_audit_and_state(
    tmp_path: Path, operation: str
) -> None:
    archive = RequestModelArchive(tmp_path / "failure.sqlite", "static", 8192)
    try:
        assert archive.put(frame("old"), 0, 4)
        before = archive.snapshot()["raw"]
        archive.db.execute(
            "CREATE TRIGGER deny BEFORE INSERT ON state BEGIN SELECT RAISE(FAIL,'deny'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            if operation == "put":
                archive.put(frame("new"), 1, 5)
            elif operation == "query":
                archive.query("old", 1)
            else:
                archive.advance(1)
        archive.db.execute("DROP TRIGGER deny")
        assert archive.snapshot()["raw"] == before
        answer = archive.query("old", 4)
        assert answer is not None and answer["verdict"] == "compliant"
    finally:
        archive.close()


def test_same_event_creation_and_access_preserve_actual_recency(tmp_path: Path) -> None:
    archive = RequestModelArchive(tmp_path / "recency.sqlite", "lru", 8192)
    try:
        assert archive.put(frame("z-first"), 0, None)
        assert archive.put(frame("a-second"), 0, None)
        assert int(archive.entries["a-second"]["created"], 16) > int(
            archive.entries["z-first"]["created"], 16
        )
        archive.query("a-second", 0)
        archive.query("z-first", 0)
        assert int(archive.entries["z-first"]["touch"], 16) > int(
            archive.entries["a-second"]["touch"], 16
        )
    finally:
        archive.close()


def test_discarded_evidence_cannot_be_refetched(tmp_path: Path) -> None:
    archive = RequestModelArchive(tmp_path / "expiry.sqlite", "ttl", 8192)
    try:
        assert archive.put(frame("discarded"), 0, None)
        assert archive.query("discarded", 3) is None
        assert archive.query("discarded", 4) is None
        assert not archive.entries and not archive.atoms
    finally:
        archive.close()
