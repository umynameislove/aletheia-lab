"""Synthetic component-contract checks; no optional runtime execution."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation import incident_audit_cost as cost
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_audit import digest


class FakeSource:
    def __init__(self) -> None:
        self.identity = {"source": "synthetic"}
        self.load_ns = {"aaa": 1, "bbb": 1}
        self.loads = {
            f"session-{name}": {"model": name, "artifact": "a" * 64, "fingerprint": digest(name)}
            for name in ("aaa", "bbb")
        }

    def native_floor(self, ordinal: int) -> dict[str, Any]:
        weights = [[1, 2], [3, 4], [5, 6]] if ordinal % 2 == 0 else [[2, 1], [4, 3], [6, 5]]
        output = (
            None
            if ordinal == 11
            else [
                [x * w for x, w in zip(xs, ws, strict=True)]
                for xs, ws in zip(cost.input_values(ordinal), weights, strict=True)
            ]
        )
        return {
            "ordinal": ordinal,
            "native_ns": 1,
            "native_error": "InvalidArgument" if ordinal == 11 else None,
            "output": output,
            "reference_arithmetic_checked": True,
        }


@pytest.mark.parametrize("mode", cost.MODES)
def test_component_modes_share_native_census_but_not_claimed_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    monkeypatch.setattr(cost, "InitializerWorkload", FakeSource)
    report = cost.worker(mode, tmp_path / mode)
    cost.verify_cell(report, tmp_path / mode)
    assert len(report["rows"]) == 16
    assert sum(row["native_error"] is not None for row in report["rows"]) == 1
    if mode in {"capture", "compact_static"}:
        assert sum(row["answer"] == "compliant" for row in report["rows"]) == 15
        assert report["rows"][11]["answer"] == "unknown"
        assert report["storage"]["closed_db_bytes"] > 0
        changed = copy.deepcopy(report)
        changed["rows"][0]["frame"]["output"] = "b" * 64
        with pytest.raises(ValueError, match="differs"):
            cost.verify_cell(changed, tmp_path / mode)
    else:
        assert not report["storage"]["closed_db_bytes"]
        assert all(row["frame"] is None and row["answer"] == "unknown" for row in report["rows"])


def test_read_only_compact_query_does_not_add_a_policy_commit(tmp_path: Path) -> None:
    from aletheia_lab.evaluation.incident_audit_archive import IncidentAuditArchive

    source = FakeSource()
    row = source.native_floor(0)
    hashes = {"input": digest(cost.input_values(0)), "output": digest(row["output"])}
    frame = cost.linked_frame(source, row, hashes)  # type: ignore[arg-type]
    archive = IncidentAuditArchive(tmp_path / "store.sqlite", "static", 1000000)
    cost._persist(None, archive, frame, 0)
    before = archive.snapshot()["state_sha256"]
    assert cost._query(None, archive, frame, 0) == "compliant"
    assert archive.snapshot()["state_sha256"] == before
    archive.close()


@pytest.fixture
def synthetic_cost_series(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Literal fake source and actual SQLite; never invoke a child/native runtime."""
    monkeypatch.setattr(cost, "InitializerWorkload", FakeSource)
    root = Path(__file__).resolve().parents[2]
    directory = tmp_path / "synthetic"
    directory.mkdir()
    relative = "src/aletheia_lab/evaluation/incident_audit_cost.py"
    snapshot = directory / "code-snapshot" / relative
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes((root / relative).read_bytes())
    order = [[mode, 0] for mode in cost.MODES] + [[cost.MODES[0], 1]]
    plan = cost.seal(
        {
            "code": {relative: hashlib.sha256(snapshot.read_bytes()).hexdigest()},
            "order": order,
            "source_identity": {"source": "synthetic"},
        }
    )
    (directory / "plan.json").write_bytes(encode(plan).encode())
    cells = []
    for mode, repeat in order:
        name = f"{mode}-{repeat}"
        if repeat == 0:
            report = cost.seal(cost.worker(mode, directory / name))
            (directory / name / "report.json").write_bytes(encode(report).encode())
        else:
            report = {"status": "worker_timeout"}
        streams = {}
        for suffix, raw in (("stdout", b"owned"), ("stderr", b"partial")):
            (directory / (name + "." + suffix)).write_bytes(raw)
            streams[suffix] = hashlib.sha256(raw).hexdigest()
        cells.append({"mode": mode, "replicate": repeat, "report": report, "streams": streams})
    result = cost.seal({"plan_sha256": plan["sha256"], "cells": cells, "failed_processes": 1})
    (directory / "results.json").write_bytes(encode(result).encode())
    return root, directory


def test_complete_synthetic_cost_replay_keeps_failed_process_without_mutation(
    synthetic_cost_series,
):
    root, directory = synthetic_cost_series
    before = {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    result = cost.verify(root, directory)
    assert result["verification"] == "pass" and result["failed_processes"] == 1
    assert set(result["summary"]) == set(cost.MODES)
    assert all(
        point["native_calls"] == 16 and point["native_failures"] == point["processes"] == 1
        for point in result["summary"].values()
    )
    assert before == {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}


@pytest.mark.parametrize("broken", [False, True])
def test_cost_verify_cli_keeps_public_status_and_scrubs_private_failure(
    synthetic_cost_series, monkeypatch, capsys, broken
):
    root, directory = synthetic_cost_series
    if broken:
        (directory / (cost.MODES[0] + "-0.stdout")).write_bytes(b"changed")
    monkeypatch.setattr(
        sys, "argv", ["owned", "verify", "--root", str(root), "--study-dir", str(directory)]
    )
    if broken:
        with pytest.raises(SystemExit) as stopped:
            cost.main()
        assert stopped.value.code == 1
        assert json.loads(capsys.readouterr().out) == {
            "status": "component_cost_failed_closed",
            "error_type": "ValueError",
        }
    else:
        cost.main()
        output = capsys.readouterr().out
        assert str(directory) not in output and json.loads(output)["verification"] == "pass"
