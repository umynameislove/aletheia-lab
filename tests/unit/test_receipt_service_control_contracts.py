"""Authored receipt replay and simulated exits; no native inference or crash claim."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_incident_audit_archive import frame

from aletheia_lab.evaluation import auditability_closeout_controls as controls


def test_retention_controls_have_identical_service_with_real_whole_and_incremental_sql(tmp_path):
    original = frame("original")
    result = controls.service_controls(tmp_path, original)
    assert original["token"] == "original"
    assert result["same_service_all"] and len(result["cells"]) == 12
    assert {(row["policy"], row["logical_budget"]) for row in result["cells"]} == {
        (policy, budget) for policy in controls.POLICIES for budget in (4096, 16384, 65536)
    }
    # Missing retained evidence is None at this archive API boundary, distinct
    # from a retained capsule whose query resolves to the string "unknown".
    assert all(row["unavailable_recovery"] is None for row in result["cells"])
    assert all(
        row["recovered_answer"] == ("compliant" if row["retained_tier_restore"] else None)
        for row in result["cells"]
    )
    assert all(row["recovery_receipt_bytes"] > 0 for row in result["cells"])
    assert len(list(tmp_path.glob("sensitivity-*.sqlite"))) == 24
    renamed = controls.renamed(original, "new")
    assert renamed["token"] == "new" and original["token"] == "original"
    assert all(use["token"] == use["batch"] == "new" for use in renamed["uses"])


class SimulatedExit(BaseException):
    def __init__(self, code):
        self.code = code


@pytest.mark.parametrize("name", ["whole", "incremental"])
@pytest.mark.parametrize("phase", ["before_commit", "after_ack"])
def test_control_driver_preserves_exit_code_and_committed_receipt_boundary(
    tmp_path, monkeypatch, name, phase
):
    packet = frame("source")
    (tmp_path / "transfer-original.json").write_text(
        json.dumps({"chain_rows": [{"frame": packet}]}), encoding="utf-8"
    )
    constructed = []
    original_whole, original_incremental = (
        controls.IncidentAuditArchive,
        controls.IncrementalAuditArchive,
    )

    def tracked(cls):
        def construct(*args, **kwargs):
            owner = cls(*args, **kwargs)
            constructed.append(owner)
            return owner

        return construct

    monkeypatch.setattr(controls, "IncidentAuditArchive", tracked(original_whole))
    monkeypatch.setattr(controls, "IncrementalAuditArchive", tracked(original_incremental))

    def exit_process(code):
        raise SimulatedExit(code)

    monkeypatch.setattr(controls.os, "_exit", exit_process)
    try:
        with pytest.raises(SimulatedExit) as stopped:
            controls.crash_worker(tmp_path, name, phase)
        assert stopped.value.code == (23 if phase == "before_commit" else 24)
    finally:
        # Closing rolls back any unfinished SQLite transaction in this simulation.
        for owner in constructed:
            owner.db.close()
    folder = tmp_path / f"crash-{name}-{phase}"
    cls = original_whole if name == "whole" else original_incremental
    reopened = cls(folder / "archive.sqlite", "static", 16384, reopen=True)
    try:
        checkpoint = folder / ("baseline.json" if phase == "before_commit" else "ack.json")
        assert (
            reopened.snapshot()["state_sha256"]
            == json.loads(checkpoint.read_bytes())["state_sha256"]
        )
        answer = reopened.query(["before", "acknowledged"], now=2)
        assert answer["before"] == "compliant"
        assert answer["acknowledged"] == (None if phase == "before_commit" else "compliant")
    finally:
        reopened.close()


def test_unplanned_exit_mode_is_rejected_before_receipt_or_database_access(tmp_path):
    with pytest.raises(ValueError, match="unplanned child exit"):
        controls.crash_worker(tmp_path, "unknown", "after_ack")
    assert list(tmp_path.iterdir()) == []


def test_crash_supervisor_checks_ack_snapshot_and_code_not_code_alone(tmp_path, monkeypatch):
    calls = []

    def synthetic_child(command, **kwargs):
        name, phase = command[command.index("--mode") + 1], command[command.index("--phase") + 1]
        calls.append((name, phase))
        folder = tmp_path / f"crash-{name}-{phase}"
        folder.mkdir()
        cls = controls.IncidentAuditArchive if name == "whole" else controls.IncrementalAuditArchive
        owner = cls(folder / "archive.sqlite", "static", 16384)
        try:
            owner.put(controls.renamed(frame("original"), "before"), now=0)
            expected = owner.snapshot()["state_sha256"]
            if phase == "after_ack":
                owner.put(controls.renamed(frame("original"), "acknowledged"), now=1)
                expected = owner.snapshot()["state_sha256"]
            path = folder / ("baseline.json" if phase == "before_commit" else "ack.json")
            path.write_text(json.dumps({"state_sha256": expected}), encoding="utf-8")
        finally:
            owner.close()
        return SimpleNamespace(returncode=23 if phase == "before_commit" else 24)

    monkeypatch.setattr(controls.subprocess, "run", synthetic_child)
    rows = controls.crash_controls(Path("owned-root"), tmp_path)
    assert calls == [
        (name, phase)
        for name in ("whole", "incremental")
        for phase in ("before_commit", "after_ack")
    ]
    assert len(rows) == 4 and all(row["pass"] for row in rows)
