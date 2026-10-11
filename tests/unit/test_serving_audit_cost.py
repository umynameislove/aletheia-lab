"""Lightweight same-service/failure contracts, not the native cost experiment."""

from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from aletheia_lab.evaluation import serving_audit_cost_workload as workload
from aletheia_lab.evaluation.serving_audit_cost_analysis import census, paired_cost
from aletheia_lab.evaluation.serving_audit_cost_store import Store, read_ack, read_records
from aletheia_lab.evaluation.serving_audit_cost_study import configurations
from aletheia_lab.project.identity import content_sha256


def record(index: int = 0) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
    operand, output = bytes([index % 3]), np.zeros((1, 1000), dtype=np.float32).tobytes()
    generation = {
        "generation_id": "generation-0",
        "closure_sha256": "expected",
        "session_object": 1,
        "signature_verified": True,
    }
    row = {
        "request_id": f"request-{index}",
        "generation_id": "generation-0",
        "index": index,
        "session_object": 1,
        "input_sha256": content_sha256(operand),
        "output_sha256": content_sha256(output),
        "offered_ns": 1,
        "deadline_ns": 30_000_000_001,
        "closed": True,
        "failed": False,
    }
    return generation, row, operand, output


@pytest.mark.parametrize("arm", ("static", "compact", "full"))
def test_three_representations_same_witness_reopen_partial_batch(tmp_path: Path, arm: str) -> None:
    root = tmp_path / arm
    store = Store(root, arm)
    rows = [record(index) for index in range(17)]
    for index, row in enumerate(rows):
        store.add(*row)
        if (index + 1) % 8 == 0:
            assert len(store.flush()) == 8
    assert len(store.flush()) == 1
    store.close()
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    retained = read_records(root, arm)
    assert [row[:4] for row in retained] == rows
    assert all(row[4] is not None for row in retained)
    assert len(read_ack(root)) == 17
    assert before == {path.name: path.read_bytes() for path in root.iterdir()}
    with pytest.raises(ValueError, match="representation"):
        read_records(root, "full" if arm != "full" else "static")


@pytest.mark.parametrize("arm", ("static", "compact", "full"))
def test_post_commit_without_ack_is_not_durable_service(tmp_path: Path, arm: str) -> None:
    store = Store(tmp_path / arm, arm)
    store.add(*record())
    with pytest.raises(RuntimeError, match="before ACK"):
        store.flush(fail_after_commit=True)
    store.close()
    assert read_records(tmp_path / arm, arm)[0][4] is None


@pytest.mark.parametrize("arm", ("static", "compact", "full"))
def test_generation_conflict_and_duplicate_roll_back_batch(tmp_path: Path, arm: str) -> None:
    root = tmp_path / arm
    store = Store(root, arm)
    store.add(*record())
    store.flush()
    store.add(*record(1))
    generation, row, operand, output = record(2)
    generation["session_object"] = 2
    store.add(generation, row, operand, output)
    with pytest.raises(ValueError, match="conflicting"):
        store.flush()
    store.add(*record())
    with pytest.raises(sqlite3.IntegrityError):
        store.flush()
    store.close()
    assert len(read_records(root, arm)) == 1


@pytest.mark.parametrize("arm", ("static", "compact", "full"))
def test_corrupted_binary_is_rejected(tmp_path: Path, arm: str) -> None:
    root = tmp_path / arm
    store = Store(root, arm)
    store.add(*record())
    store.flush()
    store.close()
    with sqlite3.connect(root / "receipts.sqlite3") as db:
        if arm == "compact":
            db.execute(
                "UPDATE blobs SET payload=? WHERE digest=?", (b"bad", record()[1]["input_sha256"])
            )
        else:
            db.execute("UPDATE receipts SET input_payload=?", (b"bad",))
    db.close()
    with pytest.raises(ValueError, match="digest"):
        read_records(root, arm)


def test_ack_schema_and_duplicate_links_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "acks.jsonl"
    path.write_text(
        json.dumps(
            {"request_ids": ["r"], "database_commit_return_ns": -1, "timestamp_semantics": "commit"}
        )
        + "\n"
    )
    with pytest.raises(ValueError, match="schema"):
        read_ack(tmp_path)
    row = {"request_ids": ["r"], "database_commit_return_ns": 1, "timestamp_semantics": "commit"}
    path.write_text((json.dumps(row) + "\n") * 2)
    with pytest.raises(ValueError, match="duplicate"):
        read_ack(tmp_path)


def test_actual_use_caller_association_is_required() -> None:
    generation, row, operand, output = record()
    caller = {
        "request_id": row["request_id"],
        "input_sha256": row["input_sha256"],
        "session_object": 1,
        "generation_id": "generation-0",
        "offered_ns": 1,
    }
    reference = np.zeros((1, 1000), dtype=np.float32)
    assert (
        workload.audit_one(
            generation, row, operand, output, caller, generation, 2, reference, "expected"
        )[0]
        == "correct"
    )
    caller["session_object"] = 2
    assert (
        workload.audit_one(
            generation, row, operand, output, caller, generation, 2, reference, "expected"
        )[0]
        == "conflict"
    )
    caller["session_object"] = 1
    assert (
        workload.audit_one(
            generation, row, operand, output, caller, generation, None, reference, "expected"
        )[0]
        == "unknown"
    )


def test_authenticated_other_closure_cannot_inherit_expected_label(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(workload, "verify_local", lambda *args: {"closure_sha256": "other"})
    with pytest.raises(ValueError, match="locked expected"):
        workload._verify_artifact(tmp_path, {"closure_sha256": "expected"})


def test_missing_workers_not_zero_unserved_or_cost_winner() -> None:
    observed = census(None, True)
    assert observed["accepted"] is None and observed["acceptance_unknown"] == 64
    assert observed["accepted_but_unserved"] is None and observed["qualified"] is False
    rows = [{"config": config, "census": observed, "cost": None} for config in configurations()]
    assert paired_cost(rows, "reuse")["forecast"] == "unidentified"


def test_sealed_rotation_census_and_signed_paired_estimand() -> None:
    configs = configurations()
    assert len(configs) == len({row["worker_id"] for row in configs}) == 50
    assert sum(row["arm"] in workload.AUDIT_ARMS for row in configs) * 64 == 1920
    rows = []
    for config in configs:
        ns = 100 if config["arm"] == "compact" else 101 + config["block"]
        rows.append({"config": config, "census": {"qualified": True}, "cost": {"workload_ns": ns}})
    assert paired_cost(rows, "reuse")["median_paired_difference_ns"] == 3
    assert paired_cost(rows, "reuse")["forecast"] == "contradicted"


def test_synthetic_closed_consumer_not_writer_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qualified = tmp_path / "qualified"
    (qualified / "inputs").mkdir(parents=True)
    (qualified / "outputs").mkdir()
    for index, name in enumerate(workload.INPUTS):
        np.save(qualified / f"inputs/{name}.npy", np.array([index], dtype=np.float32))
        np.save(
            qualified / f"outputs/qualification-torch-{name}.npy",
            np.full((1, 1000), index, dtype=np.float32),
        )

    class FakeSession:
        def get_inputs(self) -> list[Any]:
            return [type("Input", (), {"name": "x"})()]

        def run(self, _: Any, inputs: dict[str, np.ndarray]) -> list[np.ndarray]:
            return [np.full((1, 1000), inputs["x"][0], dtype=np.float32)]

    monkeypatch.setattr(workload, "session", lambda *args: FakeSession())
    monkeypatch.setattr(
        workload, "verify_local", lambda *args: {"closure_sha256": "expected", "files": []}
    )
    monkeypatch.setattr(workload.time, "sleep", lambda *args: None)
    plan = {
        "qualified_directory": str(qualified),
        "artifact": {"closure_sha256": "expected", "files": []},
    }
    result = workload.run_worker(plan, {"arm": "compact", "pattern": "reload"}, tmp_path / "worker")
    assert result["terminal"] == "complete" and result["native_calls"] == 64
    assert len(result["loads"]) == 8 and result["accepted"] == 64
    assert all(
        row["verdict"] == "correct" and row["ack_ns"] is not None for row in result["audits"]
    )
    broken = copy.deepcopy(result)
    broken["audits"] = broken["audits"][:1]
    broken["terminal"] = "failed"
    assert census(broken, True)["accepted_but_unserved"] == 63
