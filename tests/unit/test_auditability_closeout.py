"""Small controls for source ownership, immutable receipts and pure reads."""

from __future__ import annotations

import copy
import sys
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aletheia_lab.evaluation.auditability_closeout import analyze, check_plan, prepare, read, sealed
from aletheia_lab.evaluation.auditability_closeout_cost import read_frames
from aletheia_lab.evaluation.auditability_closeout_extension import service_census
from aletheia_lab.evaluation.incident_audit_incremental import IncrementalAuditArchive
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.neighbors_audit_transfer import Recorder
from aletheia_lab.evaluation.request_model_audit import digest
from aletheia_lab.filesystem import write_new_file


def test_stale_numeric_id_cannot_enroll_a_compact_comparator() -> None:
    np = import_module("numpy")

    class Compact:
        metric = "minkowski"
        _y = np.array([[1.0], [2.0]])

    regressor = Compact()
    recorder = Recorder("original")
    recorder.expected[id(regressor)] = {"old_owner": "not this native object"}
    output = recorder.predict(regressor, lambda self, x: np.array([[1.5]]), np.array([[0.0]]))
    assert output.tolist() == [[1.5]]
    assert recorder.rows == [] and len(recorder.native) == 1


def test_sealed_read_rejects_changed_contents(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    write_new_file(path, encode(sealed({"answer": "unknown"})).encode())
    assert read(path)["answer"] == "unknown"
    altered = sealed({"answer": "unknown"})
    altered["answer"] = "compliant"
    changed = tmp_path / "changed.json"
    write_new_file(changed, encode(altered).encode())
    with pytest.raises(ValueError, match="differs"):
        read(changed)


def test_pure_committed_query_does_not_change_bookkeeping(tmp_path: Path) -> None:
    value = digest("one")
    frame: dict[str, Any] = {
        "token": "r",
        "requested": "aaa",
        "kind": "non_batched",
        "input": value,
        "output": value,
        "closed": True,
        "failed": False,
        "loads": {"g": {"model": "aaa", "artifact": value, "fingerprint": value}},
        "uses": [
            {
                "token": "r",
                "batch": "r",
                "index": 0,
                "generation": "g",
                "input": value,
                "output": value,
                "fingerprint": value,
            }
        ],
    }
    store = IncrementalAuditArchive(tmp_path / "archive.sqlite", "static", 8192)
    store.put(frame, now=0)
    before = copy.deepcopy(store.state)
    assert read_frames(store, ["r", "absent"]) == {"r": "compliant", "absent": None}
    assert store.state == store._read() == before
    store.close()


def test_missing_scope_is_not_a_completed_or_false_success() -> None:
    document = {
        "chain_rows": [{"frame": {"token": "r"}, "reference": "compliant"}],
        "offers": [
            {"scope": ["r"], "admitted": True, "answers": {"r": "compliant"}},
            {"scope": ["missing"], "admitted": True, "answers": {"missing": None}},
            {"scope": ["missing"], "admitted": False, "answers": {"missing": None}},
            {"scope": ["r"], "admitted": True, "answers": {}},
        ],
    }
    assert service_census(document) == {
        "offered": 4,
        "accepted": 3,
        "refused": 1,
        "complete": 1,
        "unserved": 2,
        "false": 0,
    }


def test_conclusive_answer_without_reference_remains_false() -> None:
    document = {
        "chain_rows": [],
        "offers": [{"scope": ["missing"], "admitted": True, "answers": {"missing": "compliant"}}],
    }
    assert service_census(document)["false"] == 1
    assert service_census(document)["unserved"] == 1


def test_unexecuted_cost_census_cannot_pass_numerical_reference(tmp_path: Path) -> None:
    write_new_file(tmp_path / "plan.json", encode(sealed({"transfer_forecasts": {}})).encode())
    report = analyze(tmp_path)
    assert all(row["status"] == "insufficient_evidence" for row in report["predictions"])
    assert all(row["completed_workers"] == 0 for row in report["costs"])
    assert not any(row["numerical_reference_pass"] for row in report["costs"])


@pytest.mark.parametrize("action", [prepare, check_plan], ids=["prepare", "execute"])
def test_optimized_python_cannot_skip_scientific_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: Any
) -> None:
    monkeypatch.setattr(sys, "flags", SimpleNamespace(optimize=1))
    with pytest.raises(ValueError, match="assertions enabled"):
        action(tmp_path, tmp_path / "absent")
    assert not (tmp_path / "absent").exists()
