"""Inert graph descriptors and literal arithmetic, never a native transfer run."""

import hashlib
import sys
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from aletheia_lab.evaluation import incident_audit_transfer as transfer
from aletheia_lab.evaluation.incident_audit_archive import IncidentAuditArchive

VALUES = [[0.0, 1.0, 2.0], [3.0, 4.0, 5.0], [6.0, 7.0, 8.0]]
WEIGHTS = {
    "A": [[0.0, 100.0, 200.0], [300.0, 400.0, 500.0], [600.0, 700.0, 800.0]],
    "B": VALUES,
    "C": [[10.0, 11.0, 12.0], [13.0, 14.0, 15.0], [16.0, 17.0, 18.0]],
}
OUTPUT = [
    [945000.0, 1015200.0, 1085400.0],
    [2905200.0, 3121200.0, 3337200.0],
    [4865400.0, 5227200.0, 5589000.0],
]


def evaluator(weights):
    return SimpleNamespace(
        rt_inits_={key: np.asarray(value) for key, value in weights.items()},
        proto_=SimpleNamespace(SerializeToString=lambda: b"inert owned graph"),
        inits_=[SimpleNamespace(external_data=[])],
    )


@pytest.mark.parametrize("arm", ["lawful", "dependency_replaced", "missing_capture"])
def test_receipt_replay_matches_literal_reference_and_actual_sql_frontier(tmp_path, arm):
    capture = transfer.TransferCapture(tmp_path, arm)
    count = 2 if arm == "dependency_replaced" else 3
    for index in range(count):
        weights, output = deepcopy(WEIGHTS), deepcopy(OUTPUT)
        if arm == "dependency_replaced" and index == 1:
            weights["A"] = [[0.0] * 3 for _ in range(3)]
            output = [[0.0] * 3 for _ in range(3)]
        capture.record(evaluator(weights), {"X": np.asarray(VALUES)}, [np.asarray(output)], 17)
    rows = capture.rows
    assert [row["reference"] for row in rows] == (
        ["compliant", "violation"] if arm == "dependency_replaced" else ["compliant"] * 3
    )
    assert rows[0]["effective_proto_sha256"] == hashlib.sha256(b"inert owned graph").hexdigest()
    assert all(row["native_ns"] == 17 for row in rows)
    if arm == "missing_capture":
        assert rows[1]["capture_answer"] == "unknown" and not rows[1]["captured"]
    archives = {}
    for policy in ("static", "union_density"):
        owner = IncidentAuditArchive(tmp_path / (policy + ".sqlite"), policy, 65536)
        try:
            for index, row in enumerate(rows):
                assert owner.put(row["frame"], now=index)
            scopes = [row["frame"]["token"] for row in rows]
            archives[policy] = {
                "answers": owner.query(scopes, now=count),
                "witness": owner.witness(scopes),
                "storage": owner.snapshot(),
            }
        finally:
            owner.close()
        path = tmp_path / (policy + ".sqlite")
        archives[policy].update(
            closed_db_bytes=path.stat().st_size,
            db_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    cell = {
        "arm": arm,
        "method": transfer.METHODS[0],
        "rows": rows,
        "planned_native_calls": 3,
        "actual_native_calls": count,
        "native_test_error": "AssertionError" if arm == "dependency_replaced" else None,
        "archives": archives,
        "native_files": {},
    }
    before = {path: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    result = transfer.verify_cell(cell, tmp_path)
    assert result == {
        "native_calls": count,
        "correct_capture": count - int(arm == "missing_capture"),
        "unknown_capture": int(arm == "missing_capture"),
        "violations": int(arm == "dependency_replaced"),
    }
    assert before == {path: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    changed = deepcopy(cell)
    changed["archives"]["static"]["closed_db_bytes"] += 1
    with pytest.raises(ValueError, match="durable database differs"):
        transfer.verify_cell(changed, tmp_path)
    changed = deepcopy(cell)
    changed["native_files"] = {"../outside": "0" * 64}
    with pytest.raises(ValueError, match="native file differs"):
        transfer.verify_cell(changed, tmp_path)


def test_incorrect_arithmetic_never_emits_a_completed_receipt(tmp_path):
    capture = transfer.TransferCapture(tmp_path, "lawful")
    with pytest.raises(ValueError, match="independent matrix arithmetic"):
        capture.record(evaluator(WEIGHTS), {"X": np.asarray(VALUES)}, [np.zeros((3, 3))], 17)
    assert capture.rows == []


@pytest.mark.parametrize("arm", ["lawful", "dependency_replaced", "restore_dependency"])
def test_saved_dependency_span_preserves_original_graph_and_exact_bytes(tmp_path, monkeypatch, arm):
    native = tmp_path / "native"
    native.mkdir()
    graph, weights = native / "graph.onnx", native / "weights.data"
    graph.write_bytes(b"owned graph bytes")
    original = bytes(range(108))
    weights.write_bytes(original)

    def tensor(name, offset):
        fields = {"location": "weights.data", "offset": str(offset), "length": "36"}
        return SimpleNamespace(
            name=name,
            external_data=[SimpleNamespace(key=key, value=value) for key, value in fields.items()],
        )

    parsed = SimpleNamespace(graph=SimpleNamespace(initializer=[tensor("A", 36), tensor("B", 0)]))

    def load(path, **kwargs):
        assert path == str(graph) and kwargs == {"load_external_data": False}
        return parsed

    monkeypatch.setitem(sys.modules, "onnx", SimpleNamespace(load_model=load))
    capture = transfer.TransferCapture(tmp_path / "receipt", arm)
    capture.saved_model(str(graph))
    retained = capture.directory / "native-files"
    assert (retained / "graph.onnx").read_bytes() == graph.read_bytes() == b"owned graph bytes"
    assert (retained / "weights.data").read_bytes() == original
    changed = original[:36] + bytes(36) + original[72:]
    assert weights.read_bytes() == (changed if arm == "dependency_replaced" else original)
    assert capture.saved[0]["origin_graph_unchanged"]
    assert len(capture.saved[0]["mutations"]) == int(arm != "lawful")
    if arm != "lawful":
        assert (retained / "weights.data.changed").read_bytes() == changed
        assert capture.saved[0]["mutations"][0] == {
            "before": hashlib.sha256(original).hexdigest(),
            "changed": hashlib.sha256(changed).hexdigest(),
            "final": hashlib.sha256(weights.read_bytes()).hexdigest(),
            "name": "A",
        }


def test_escaping_dependency_location_is_rejected_before_outside_read(tmp_path, monkeypatch):
    path = tmp_path / "graph.onnx"
    path.write_bytes(b"owned graph")
    tensor = SimpleNamespace(
        name="A", external_data=[SimpleNamespace(key="location", value="../outside")]
    )
    monkeypatch.setitem(
        sys.modules,
        "onnx",
        SimpleNamespace(
            load_model=lambda *args, **kwargs: SimpleNamespace(
                graph=SimpleNamespace(initializer=[tensor])
            )
        ),
    )
    capture = transfer.TransferCapture(tmp_path / "receipt", "dependency_replaced")
    with pytest.raises(ValueError, match="bounded owned external"):
        capture.saved_model(str(path))
    assert capture.saved == [] and path.read_bytes() == b"owned graph"
