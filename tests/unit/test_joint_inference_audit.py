from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation.joint_inference_audit import (
    JointInferenceAudit,
    expand,
    materialize,
    resolve,
)
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.project.identity import content_sha256


def digest(label: str) -> str:
    return content_sha256(label.encode())


def frame(request: str = "request-0", *, classifier: str = "c1") -> dict[str, Any]:
    encoder = "e1"
    uses = []
    for ordinal, (role, generation, source, target) in enumerate(
        (("encoder", encoder, "input", "encoded"), ("classifier", classifier, "encoded", "output"))
    ):
        uses.append(
            {
                "request": request,
                "attempt": 0,
                "ordinal": ordinal,
                "role": role,
                "generation": generation,
                "artifact": digest(generation),
                "fingerprint": digest("object-" + generation),
                "input": digest(source),
                "output": digest(target),
            }
        )
    return {
        "request": request,
        "attempt": 0,
        "revision": 0,
        "expected": [digest("e1"), digest("c1")],
        "input": digest("input"),
        "output": digest("output"),
        "declared": 2,
        "closed": True,
        "failed": False,
        "loads": {
            use["generation"]: {
                "role": use["role"],
                "artifact": use["artifact"],
                "selected": use["artifact"],
                "fingerprint": use["fingerprint"],
                "count": 1,
                "closed": True,
            }
            for use in uses
        },
        "uses": uses,
    }


def test_individually_bound_loads_do_not_establish_authorized_request_tuple() -> None:
    mixed = frame(classifier="c2")
    assert all(v["artifact"] == v["selected"] for v in mixed["loads"].values())
    assert resolve(mixed)["verdict"] == "violation"
    mixed["expected"] = [digest("e1"), digest("c2")]
    assert resolve(mixed)["verdict"] == "compliant"
    # Current publication is outside this fixed request contract: old use stays legal.
    assert resolve(frame())["verdict"] == "compliant"


@pytest.mark.parametrize(
    "change", ["scope", "attempt", "order", "chain", "fingerprint", "count", "census", "output"]
)
def test_binding_and_census_contradictions_remain_conflicts(change: str) -> None:
    value = frame()
    if change == "scope":
        value["uses"][0]["request"] = "foreign"
    elif change == "attempt":
        value["uses"][0]["attempt"] = 1
    elif change == "order":
        value["uses"].reverse()
    elif change == "chain":
        value["uses"][1]["input"] = digest("foreign")
    elif change == "fingerprint":
        value["loads"]["e1"]["fingerprint"] = digest("foreign")
    elif change == "count":
        value["loads"]["e1"]["count"] = 2
    elif change == "census":
        value["uses"].pop()
    else:
        value["output"] = digest("foreign")
    assert resolve(value)["verdict"] == "conflict"
    assert expand(materialize(value)) == value
    assert resolve(expand(materialize(value))) == resolve(value)


def test_incomplete_and_native_failure_are_unknown_without_false_lease(tmp_path: Path) -> None:
    value = frame("failed")
    value.update(failed=True, output=None)
    value["uses"] = value["uses"][:1]
    service = JointInferenceAudit(tmp_path / "unknown.sqlite", "static", 8192)
    try:
        assert resolve(value)["verdict"] == "unknown"
        assert not service.put(value, 0, 4)
        assert service.accepted == service.refused == 0
        answer = service.query("failed", 4)
        assert answer is not None and answer["verdict"] == "unknown"
        del value["loads"]["e1"]
        value.update(failed=False, closed=False)
        assert resolve(value)["verdict"] == "unknown"
    finally:
        service.close()


def test_materialization_roundtrip_and_unknown_extensions_rejected() -> None:
    value = frame()
    packed = materialize(value)
    assert expand(packed) == value
    assert len(encode(packed)) < len(encode(value))
    value["truth"] = True
    with pytest.raises(ValueError, match="schema"):
        materialize(value)
    packed["verdict"] = "compliant"
    with pytest.raises(ValueError, match="schema"):
        expand(packed)


@pytest.mark.parametrize("mode", ["full", "compact"])
@pytest.mark.parametrize("policy", ["static", "lru", "ttl"])
def test_inclusive_pin_survives_pressure_refusal_and_later_native_failure(
    tmp_path: Path, policy: str, mode: str
) -> None:
    service = JointInferenceAudit(tmp_path / "pressure.sqlite", policy, 3072, mode)
    try:
        assert service.put(frame("first", classifier="c2"), 0, 4)
        for index in range(1, 5):
            service.put(frame(f"other-{index}", classifier=f"c{index + 2}"), index, index + 4)
            assert service.peak <= service.budget
        answer = service.query("first", 4)
        assert answer is not None and answer["verdict"] == "violation"
        assert service.refused > 0
        if policy == "ttl":
            assert service.query("first", 5) is None
    finally:
        service.close()


@pytest.mark.parametrize("mode", ["full", "compact"])
def test_shared_capsules_and_exact_canonical_blob_charge(tmp_path: Path, mode: str) -> None:
    service = JointInferenceAudit(tmp_path / "shared.sqlite", "static", 8192, mode)
    try:
        assert service.put(frame("first"), 0, 0)
        assert service.put(frame("second"), 1, 1)
        state = service.snapshot()
        entries = state["state"]["entries"]
        assert entries["first"]["loads"] == entries["second"]["loads"]
        assert len(state["state"]["atoms"]) == 4  # two targets, two shared loads
        row = service.db.execute("SELECT payload FROM state").fetchone()
        assert state["logical_bytes"] == len(row[0]) == len(encode(state["state"]).encode())
        assert service.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert service.db.execute("PRAGMA synchronous").fetchone()[0] == 2
    finally:
        service.close()


def test_durable_tampering_is_detected_before_new_ingress_overwrites_it(tmp_path: Path) -> None:
    service = JointInferenceAudit(tmp_path / "tamper.sqlite", "static", 8192)
    try:
        assert service.put(frame(), 0, 4)
        before = copy.deepcopy(service.state)
        forged = json.loads(encode(before))
        forged["now"] = "00000001"
        service.db.execute("UPDATE state SET payload=?", (encode(forged).encode(),))
        service.db.commit()
        with pytest.raises(ValueError, match="durable"):
            service.query("request-0", 1)
        with pytest.raises(ValueError, match="durable"):
            service.put(frame("new"), 1, 5)
        assert service.state == before
    finally:
        service.close()


def test_durability_failure_never_creates_a_promise_or_removes_old_pins(tmp_path: Path) -> None:
    service = JointInferenceAudit(tmp_path / "failure.sqlite", "static", 8192)
    try:
        assert service.put(frame("old"), 0, 4)
        before = copy.deepcopy(service.snapshot())
        service.db.execute(
            "CREATE TRIGGER deny BEFORE INSERT ON state BEGIN SELECT RAISE(FAIL,'deny'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            service.put(frame("new"), 1, 5)
        assert service.snapshot() == before
        service.db.execute("DROP TRIGGER deny")
        answer = service.query("old", 4)
        assert answer is not None and answer["verdict"] == "compliant"
    finally:
        service.close()


def test_invalid_bounds_duplicate_and_oversized_frames_fail_explicitly(tmp_path: Path) -> None:
    path = tmp_path / "invalid.sqlite"
    service = JointInferenceAudit(path, "static", 8192)
    try:
        assert service.put(frame(), 0, 4)
        with pytest.raises(ValueError, match="duplicate"):
            service.put(frame(), 0, 4)
        with pytest.raises(ValueError, match="monotonic"):
            service.advance(-1)
        value = frame("bad")
        value["expected"][0] = "not-a-hash"
        with pytest.raises(ValueError):
            service.put(value, 1, 5)
        value = frame("big")
        value["uses"] *= 33
        with pytest.raises(ValueError, match="oversized"):
            service.put(value, 1, 5)
        with pytest.raises(ValueError, match="fresh"):
            JointInferenceAudit(path, "static", 8192)
    finally:
        service.close()
