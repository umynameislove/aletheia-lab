"""Synthetic component-contract checks; no optional runtime execution."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation import incident_audit_cost as cost
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
