"""SDK-free forward census, live audit checkpoint and client-only intent guards."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation import module_realization_forward as forward
from aletheia_lab.evaluation.module_realization_analysis import _client_intents, payload_digest
from aletheia_lab.evaluation.module_realization_runtime import _control
from aletheia_lab.evaluation.module_realization_store import Collector


def test_forward_matrix_separates_floor_from_certificate_queries() -> None:
    cells = forward.cells()
    floor = [cell for cell in cells if cell["slice"] == "no_observer_floor"]
    queries = [cell for cell in cells if cell["slice"] == "early_late_audit"]
    assert len(cells) == 14 and len(floor) == 12 and len(queries) == 2
    assert (
        len({(c["variant"], c["repair"], tuple(c["order"]), c["replicate"]) for c in floor}) == 12
    )
    assert all(c["evidence"] == "native" and not c.get("audit_queries") for c in floor)
    assert {c["evidence"] for c in queries} == {"compact", "full"}
    assert all(c["audit_queries"] and c["fault"] == "delay" for c in queries)


def test_control_audits_committed_store_before_and_after_flush(tmp_path: Path) -> None:
    binding = {"coefficient": 1.0, "helper_sha256": "A"}
    collector = Collector(tmp_path, {"evidence": "compact", "fault": "delay"})
    try:
        for event in (
            {
                "kind": "load",
                "load_id": "l",
                "intended": 2.0,
                "expected_helper_sha256": "B",
                "binding": binding,
            },
            {"kind": "predict", "request_id": "r", "load_id": "l", "binding": binding},
            {"kind": "closure", "requests": ["r"]},
        ):
            collector.emit(event)
        early = _control("/audit", None, collector, ["r"], None)
        assert early["audit"] == {"verdicts": {"r": "unknown"}, "closure": False}
        assert collector.status()["pending_events"] == 3
        assert collector.status()["durable_ack_sequences"] == []
        collector.flush()
        late = _control("/audit", None, collector, ["r"], None)
        assert late["audit"] == {"verdicts": {"r": "violation"}, "closure": True}
        assert collector.status()["durable_ack_sequences"] == [1, 2, 3]
        assert early["query_ns"] >= 0 and late["query_ns"] >= 0
    finally:
        collector.close()


def test_native_audit_does_not_read_app_snapshot(tmp_path: Path) -> None:
    collector = Collector(tmp_path, {"evidence": "native"})
    assert _control("/audit", None, collector, ["r"], None)["audit"] == {
        "verdicts": {"r": "unknown"},
        "closure": False,
    }
    assert not (tmp_path / "evidence.sqlite").exists()


@pytest.mark.parametrize("kind", ["load", "predict"])
def test_live_audit_rejects_conflicting_identity(tmp_path: Path, kind: str) -> None:
    binding = {"coefficient": 1.0, "helper_sha256": "A"}
    load = {
        "kind": "load",
        "load_id": "l",
        "intended": 1.0,
        "expected_helper_sha256": "A",
        "binding": binding,
    }
    prediction = {"kind": "predict", "request_id": "r", "load_id": "l", "binding": binding}
    collector = Collector(tmp_path, {"evidence": "compact"})
    try:
        collector.emit(load)
        collector.emit(prediction)
        conflict = deepcopy(load if kind == "load" else prediction)
        conflict["binding"] = {"coefficient": 2.0, "helper_sha256": "B"}
        collector.emit(conflict)
        with pytest.raises(ValueError, match="conflicting audit identity"):
            _control("/audit", None, collector, ["r"], None)
    finally:
        collector.close()


def test_live_audit_rejects_duplicate_parent_obligation(tmp_path: Path) -> None:
    collector = Collector(tmp_path, {"evidence": "native"})
    with pytest.raises(ValueError, match="duplicate parent audit request"):
        _control("/audit", None, collector, ["r", "r"], None)


def test_failed_flush_cannot_publish_uncommitted_acknowledgements(tmp_path: Path) -> None:
    collector = Collector(tmp_path, {"evidence": "compact", "durability": "drain"})
    try:
        collector.emit({"kind": "closure", "requests": ["r"]})
        collector.pending.append({"kind": "closure", "requests": ["different"], "seq": 1})
        with pytest.raises(ValueError, match="conflicting duplicate live event"):
            collector.flush()
        assert collector.db is not None
        assert collector.db.execute("SELECT COUNT(*) FROM events").fetchone() == (0,)
        assert collector.status()["durable_ack_sequences"] == []
        assert collector.status()["durable_ack_digests"] == {}
        assert collector.status()["payload_bytes"] == 0
    finally:
        collector.close()


def test_parent_intents_preserve_resident_after_failed_reload() -> None:
    rows = [
        {"route": "load", "value": {"label": "A"}, "response": {"status": 200}},
        {"route": "predict", "value": {"request_id": "a"}},
        {"route": "load", "value": {"label": "B"}, "response": {"status": 200}},
        {"route": "load", "value": {"label": "C"}, "response": {"status": 400}},
        {"route": "predict", "value": {"request_id": "b"}},
    ]
    assert _client_intents(rows) == {"a": 1.0, "b": 2.0}
    with pytest.raises(ValueError, match="no successful load"):
        _client_intents([rows[1]])


def test_empty_readonly_wal_does_not_change_signed_payload_state(tmp_path: Path) -> None:
    (tmp_path / "evidence.sqlite").write_bytes(b"owned database fixture")
    before = payload_digest(tmp_path)
    (tmp_path / "evidence.sqlite-wal").write_bytes(b"")
    assert payload_digest(tmp_path) == before
    (tmp_path / "evidence.sqlite-wal").write_bytes(b"committed WAL fixture")
    after = payload_digest(tmp_path)
    assert after[0] != before[0]
    assert after[1]["evidence.sqlite-wal"] == len(b"committed WAL fixture")


def transcript(config: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def offer(route: str, value: dict[str, Any], response: dict[str, Any]) -> None:
        rows.extend(
            [
                {"kind": "offer", "route": route, "value": deepcopy(value)},
                {
                    "kind": "response",
                    "route": route,
                    "value": deepcopy(value),
                    "response": response,
                    "elapsed_ns": 100,
                },
            ]
        )

    for stage, label in enumerate(config["order"]):
        offer("load", {"label": label}, {"status": 200, "body": {"loaded": label}})
        for index in range(12):
            offer(
                "predict",
                {"request_id": f"r-{stage * 12 + index:03}", "x": index % 3},
                {"status": 200, "body": {"y": index % 3}},
            )
    offer("load", {"label": "C"}, {"status": 400, "body": {"error": "invalid artifact"}})
    offer("predict", {"request_id": "r-036", "x": 7}, {"status": 200, "body": {"y": 7}})
    if config["slice"] == "early_late_audit":
        offer(
            "audit",
            {},
            {"audit": {"verdicts": {f"r-{i:03}": "unknown" for i in range(37)}, "closure": False}},
        )
        offer(
            "audit",
            {},
            {
                "audit": {
                    "verdicts": {
                        f"r-{i:03}": "violation" if 12 <= i < 24 else "compliant" for i in range(37)
                    },
                    "closure": True,
                }
            },
        )
    rows.append({"kind": "process_exit", "returncode": 0, "failure": None})
    return rows


@pytest.fixture
def forward_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[dict[str, Any], list[dict[str, Any]], Path]:
    config = deepcopy(forward.cells()[12])
    execution = {
        "config": config,
        "failure": None,
        "returncode": 0,
        "last_response": {"native": {"prediction_calls": 37, "actual_native_loads": 4}},
    }
    rows = transcript(config)
    monkeypatch.setattr(forward, "read_records", lambda path: rows)
    return execution, rows, tmp_path / "cell"


def test_forward_analyzer_accepts_complete_early_late_control(forward_fixture) -> None:
    execution, _, directory = forward_fixture
    result = forward._analyze(directory, execution)
    assert result["predictions"] == 37 and result["numeric_changed"] == 8


@pytest.mark.parametrize("filename", ["reference.jsonl", "evidence.sqlite"])
def test_floor_rejects_unexpected_child_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str
) -> None:
    config = deepcopy(forward.cells()[0])
    execution = {
        "config": config,
        "failure": None,
        "returncode": 0,
        "last_response": {"native": {"prediction_calls": 37, "actual_native_loads": 4}},
    }
    rows = transcript(config)
    monkeypatch.setattr(forward, "read_records", lambda path: rows)
    directory = tmp_path / "cell"
    directory.mkdir()
    (directory / filename).write_bytes(b"unexpected capture fixture")
    with pytest.raises(ValueError, match="unexpectedly captured"):
        forward._analyze(directory, execution)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_query_ids",
        "wrong_late_verdict",
        "duplicate_request",
        "missing_exit",
        "wrong_failed_load",
        "wrong_predict_status",
        "early_closure",
        "offer_binding",
    ],
)
def test_forward_analyzer_rejects_incomplete_or_incorrect_service(
    forward_fixture, mutation: str
) -> None:
    execution, rows, directory = forward_fixture
    responses = [row for row in rows if row["kind"] == "response"]
    if mutation == "missing_query_ids":
        for row in responses:
            if row["route"] == "audit":
                row["response"]["audit"]["verdicts"].pop("r-000")
    elif mutation == "wrong_late_verdict":
        queries = [row for row in responses if row["route"] == "audit"]
        queries[-1]["response"]["audit"]["verdicts"]["r-012"] = "compliant"
    elif mutation == "duplicate_request":
        for row in rows:
            if row.get("route") == "predict" and row["value"]["request_id"] == "r-001":
                row["value"]["request_id"] = "r-000"
    elif mutation == "missing_exit":
        rows.pop()
    elif mutation == "wrong_failed_load":
        next(row for row in responses if row["route"] == "load" and row["value"]["label"] == "C")[
            "response"
        ]["status"] = 200
    elif mutation == "wrong_predict_status":
        next(row for row in responses if row["route"] == "predict")["response"]["status"] = 500
    elif mutation == "early_closure":
        next(row for row in responses if row["route"] == "audit")["response"]["audit"][
            "closure"
        ] = True
    else:
        next(row for row in rows if row["kind"] == "offer" and row["route"] == "predict")["value"][
            "x"
        ] = 99
    with pytest.raises(ValueError):
        forward._analyze(directory, execution)


def test_forward_failed_finding_retains_planned_census(forward_fixture) -> None:
    execution, rows, directory = forward_fixture
    rows.pop()
    failure = forward._finding(directory, execution)
    assert failure == {"verification": "fail", "error_type": "ValueError"}
    executions = [{"config": cell} for cell in forward.cells()]
    findings = [failure for _ in executions]
    summary = forward._summary(executions, findings)
    assert sum(group["cells"] for group in summary.values()) == 14
    assert all(group["verified_cells"] == 0 for group in summary.values())
    assert all(group["median_predict_ms"] is None for group in summary.values())
