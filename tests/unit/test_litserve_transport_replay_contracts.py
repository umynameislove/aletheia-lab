"""Synthetic HTTP and owned receipt replay, without a serving SDK or process."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from aletheia_lab.evaluation import litserve_evidence_study as study
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.project.identity import content_sha256

ROOT = Path(study.__file__).resolve().parents[3]


@pytest.fixture
def transport(monkeypatch):
    import sys

    class TransportError(Exception):
        pass

    class ReadTimeout(TransportError):
        pass

    module = SimpleNamespace(
        TransportError=TransportError,
        ReadTimeout=ReadTimeout,
        Timeout=lambda value: value,
    )
    monkeypatch.setitem(sys.modules, "httpx", module)
    return module


@pytest.mark.parametrize("outcome", ["success", "invalid_json", "timeout"])
def test_http_request_keeps_offer_raw_bytes_and_terminal_timer(transport, outcome):
    response = SimpleNamespace(status_code=200, content=b'{"y":7}', json=lambda: {"y": 7})
    if outcome == "invalid_json":
        response.json = Mock(side_effect=ValueError("synthetic malformed body"))
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    if outcome == "timeout":
        client.post.side_effect = transport.ReadTimeout("synthetic transport timeout")
    rows = []
    asyncio.run(study._request(client, 1234, "/a", "owned", "fixture", 7, rows))
    assert len(rows) == 1
    row = rows[0]
    assert row["token"] == "owned" and row["payload"]["x"] == 7
    assert row["payload"]["deadline_ns"] == row["start_ns"] + 150_000_000
    assert row["latency_ns"] == row["end_ns"] - row["start_ns"] >= 0
    assert client.post.call_args.args == ("http://127.0.0.1:1234/a",)
    assert client.post.call_args.kwargs == {"json": row["payload"], "timeout": 2.0}
    assert row["status"] == (None if outcome == "timeout" else 200)
    assert row["raw_response_hex"] == ("" if outcome == "timeout" else b'{"y":7}'.hex())
    assert row["body"] == ({"y": 7} if outcome == "success" else None)
    assert (
        row["error"]
        == {"success": None, "invalid_json": "JSONDecodeError", "timeout": "ReadTimeout"}[outcome]
    )


@pytest.mark.parametrize("alive", [True, False])
def test_ready_uses_owned_health_route_and_no_proxy(transport, alive):
    client = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(status_code=200)))

    class Context:
        async def __aenter__(self):
            return client

        async def __aexit__(self, *args):
            return False

    factory = Mock(return_value=Context())
    transport.AsyncClient = factory
    process = SimpleNamespace(poll=lambda: None if alive else 2)
    if alive:
        asyncio.run(study._ready(1234, process, 1))
        client.get.assert_awaited_once_with("http://127.0.0.1:1234/health", timeout=1)
    else:
        with pytest.raises(RuntimeError, match="before readiness"):
            asyncio.run(study._ready(1234, process, 1))
        client.get.assert_not_awaited()
    factory.assert_called_once_with(trust_env=False)


def test_closed_database_reader_decodes_actual_retained_payload(tmp_path):
    assert study._database_events(tmp_path) == {}
    event = {"pid": 1, "sequence": 0, "kind": "synthetic"}
    with closing(sqlite3.connect(tmp_path / "collector.sqlite")) as db, db:
        db.execute("CREATE TABLE events(pid INTEGER,seq INTEGER,payload TEXT)")
        db.execute("INSERT INTO events VALUES(?,?,?)", (1, 0, encode(event)))
    assert study._database_events(tmp_path) == {(1, 0): event}


@pytest.fixture
def replay(tmp_path):
    protocol = json.loads((ROOT / study.PROTOCOL).read_bytes())
    plan = {
        "protocol": protocol,
        "cells": study._cells(protocol),
        "bindings": {name: content_sha256((ROOT / name).read_bytes()) for name in study.FILES},
    }
    bindings, cells = {}, []
    for index, cell in enumerate(plan["cells"]):
        folder = tmp_path / f"cell-{index:02d}"
        folder.mkdir()
        original = {
            **cell,
            "rows": [],
            "events": [],
            "receipts": [],
            "failure": "SyntheticLaunchFailure",
            "costs": {"collector_scan_ns": 1},
        }
        assessed = {
            **original,
            "costs": {**original["costs"], "analysis_ns": 2},
            "signed_baseline": {"status": "failure", "error_type": "SyntheticSigningFailure"},
        }
        (folder / "source.json").write_bytes(encode(original).encode())
        (folder / "assessed-source.json").write_bytes(encode(assessed).encode())
        bindings[folder.name] = {
            "native": content_sha256((folder / "source.json").read_bytes()),
            "assessed": content_sha256((folder / "assessed-source.json").read_bytes()),
        }
        cells.append(study._cell_analysis(assessed))
    (tmp_path / "plan.json").write_bytes(encode(plan).encode())
    report = {
        "plan_sha256": content_sha256((tmp_path / "plan.json").read_bytes()),
        "source_bindings": bindings,
        "cells": cells,
        "summary": study.aggregate(cells),
    }
    report["results_sha256"] = content_sha256(encode(report).encode())
    (tmp_path / "results.json").write_bytes(encode(report).encode())
    return tmp_path


def test_immutable_replay_does_not_turn_failed_cells_into_complete_service(replay):
    before = {p: p.read_bytes() for p in replay.rglob("*") if p.is_file()}
    result = study.verify(ROOT, replay)
    assert result["verification"] == "pass" and result["cells"] == 8
    assert result["planned_requests"] == 88 and result["offered_requests"] == 0
    assert result["complete"] is False
    assert result["disposition"] == "incomplete_census_no_transfer_closeout"
    assert before == {p: p.read_bytes() for p in replay.rglob("*") if p.is_file()}


@pytest.mark.parametrize(
    "change,expected",
    [
        ("body", "native observations"),
        ("cost", "recorded cost"),
        ("cell", "producer journal"),
        ("collector", "receipt census"),
        ("signed_link", "provenance link"),
    ],
)
def test_rehashed_assessment_cannot_change_observation_cost_or_storage(replay, change, expected):
    folder = replay / "cell-00"
    path = folder / "assessed-source.json"
    assessed = json.loads(path.read_bytes())
    if change == "body":
        assessed["rows"] = [{"unexpected": True}]
    elif change == "cost":
        assessed["costs"]["collector_scan_ns"] += 1
    elif change == "cell":
        assessed["replicate"] += 1
        original_path = folder / "source.json"
        original = json.loads(original_path.read_bytes())
        original["replicate"] += 1
        original_path.write_bytes(encode(original).encode())
    elif change == "collector":
        event = {"pid": 1, "sequence": 0, "kind": "synthetic"}
        with closing(sqlite3.connect(folder / "collector.sqlite")) as db, db:
            db.execute("CREATE TABLE events(pid INTEGER,seq INTEGER,payload TEXT)")
            db.execute("INSERT INTO events VALUES(?,?,?)", (1, 0, encode(event)))
    else:
        (folder / "provenance").mkdir()
        (folder / "provenance/link.json").write_bytes(b"changed synthetic signature")
        assessed["signed_baseline"] = {"status": "pass", "link_bindings": {"link.json": "0" * 64}}
    path.write_bytes(encode(assessed).encode())
    binding = {
        "native": content_sha256((folder / "source.json").read_bytes()),
        "assessed": content_sha256(path.read_bytes()),
    }
    plan = json.loads((replay / "plan.json").read_bytes())
    with pytest.raises(ValueError, match=expected):
        study._rebuild_cell(folder, plan["cells"][0], binding)


@pytest.mark.parametrize(
    "change,expected",
    [("plan", "plan identity"), ("census", "protocol/census"), ("result", "read-only rebuild")],
)
def test_resealed_summary_or_plan_cannot_hide_fixed_offers(replay, change, expected):
    path = replay / "results.json"
    result = json.loads(path.read_bytes())
    if change == "plan":
        result["plan_sha256"] = "0" * 64
    elif change == "census":
        plan_path = replay / "plan.json"
        plan = json.loads(plan_path.read_bytes())
        plan["cells"].pop()
        plan_path.write_bytes(encode(plan).encode())
        result["plan_sha256"] = content_sha256(plan_path.read_bytes())
    else:
        result["summary"]["planned_requests"] = 0
    result.pop("results_sha256")
    result["results_sha256"] = content_sha256(encode(result).encode())
    path.write_bytes(encode(result).encode())
    with pytest.raises(ValueError, match=expected):
        study.verify(ROOT, replay)
