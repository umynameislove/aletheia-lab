"""Real SQLite reconstruction/census checks with explicitly local signing stubs."""

from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation import cache_lifecycle_materialization as materialization
from aletheia_lab.evaluation.cache_lifecycle_certificate import project_records
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file


@pytest.fixture(autouse=True)
def local_signing(monkeypatch: pytest.MonkeyPatch) -> None:
    def sign(digest: str, directory: Path) -> str:
        directory.mkdir()
        write_new_file(directory / "local-test-digest.json", encode({"sha256": digest}).encode())
        return "pass"

    def verify(directory: Path, digest: str) -> None:
        if json.loads((directory / "local-test-digest.json").read_bytes()) != {"sha256": digest}:
            raise ValueError("local test digest mismatch")

    monkeypatch.setattr(materialization, "sign_bundle", sign)
    monkeypatch.setattr(materialization, "_verify_digest", verify)


def tape() -> list[dict[str, Any]]:
    producer = {"cid": "compute-b", "generation": "B", "digest": "b" * 64, "x": 2, "y": 4}
    facts = [
        {
            "kind": "load_return",
            "token": "initial",
            "generation": "A",
            "object_id": 1,
            "digest": "a" * 64,
        },
        {
            "kind": "load_return",
            "token": "reload",
            "generation": "B",
            "object_id": 2,
            "digest": "b" * 64,
        },
        {
            "kind": "publish",
            "token": "reload",
            "previous_generation": "A",
            "generation": "B",
            "object_id": 2,
            "digest": "b" * 64,
            "clear": True,
        },
        {
            "kind": "response",
            "token": "reload",
            "route": "/reload",
            "body": {"artifact": "B"},
            "status": 200,
            "raw_response": '{"loaded_generation":"B"}',
        },
        {"kind": "compute_return", "token": "request", "object_id": 2, **producer},
        {
            "kind": "wrapper_return",
            "token": "request",
            "selected_generation": "B",
            "selected_object_id": 2,
            "returned": producer,
        },
        {
            "kind": "handler_terminal",
            "token": "request",
            "selected_generation": "B",
            "selected_object_id": 2,
            "returned_producer": producer,
            "status": 200,
            "raw_response": '{"y":4}',
        },
        {
            "kind": "response",
            "token": "request",
            "route": "/infer",
            "body": {"x": 2},
            "status": 200,
            "raw_response": '{"y":4}',
        },
        {
            "kind": "load_failure",
            "token": "failed",
            "artifact": "C",
            "error_type": "JSONDecodeError",
            "preserved_generation": "B",
        },
        {
            "kind": "response",
            "token": "failed",
            "route": "/reload",
            "body": {"artifact": "C"},
            "status": 400,
            "raw_response": '{"error":"invalid artifact"}',
        },
    ]
    return [
        {
            "sequence": index,
            "time_ns": index * 10,
            "unneeded_detail": "retained only in source",
            **fact,
        }
        for index, fact in enumerate(facts)
    ]


def binding() -> dict[str, Any]:
    return {"source_sha256": "c" * 64, "cell": {"arm": "clear"}}


def read_database(directory: Path) -> tuple[list[bytes], list[bytes]]:
    with sqlite3.connect(f"{(directory / 'audit.sqlite').as_uri()}?mode=ro", uri=True) as database:
        records = [
            bytes(row[0])
            for row in database.execute("SELECT payload FROM records ORDER BY ordinal")
        ]
        metadata = [bytes(row[0]) for row in database.execute("SELECT payload FROM binding")]
    return records, metadata


@pytest.mark.parametrize("candidate", materialization.CANDIDATES)
def test_actual_sqlite_service_ledger_and_byte_census(tmp_path: Path, candidate: str) -> None:
    directory = tmp_path / candidate
    report = materialization.materialize(tape(), candidate, directory, binding())
    expected = tape() if candidate == "source_sufficient" else project_records(tape())
    stored, metadata = read_database(directory)
    assert stored == [encode(value).encode() for value in expected]
    assert metadata == [encode(binding()).encode()]
    assert report["records"] == len(expected)
    assert report["commits"] == (2 if candidate == "projected_drain" else 1 + len(expected))
    assert report["logical_bytes"] == sum(map(len, stored)) + len(metadata[0])
    assert report["physical_closed_bytes"] == (directory / "audit.sqlite").stat().st_size
    assert report["physical_live_bytes"] >= report["physical_closed_bytes"]
    assert report["provenance_bytes"] == sum(
        path.stat().st_size for path in (directory / "provenance").iterdir()
    )
    unknown = {"verdict": "unknown", "producer": None, "closure": "unknown", "delivery": "unknown"}
    assert report["answers"] == {
        "reload": unknown,
        "failed": unknown,
        "request": {
            "verdict": "compliant",
            "producer": "compute-b",
            "closure": "closed",
            "delivery": "observed",
        },
    }
    assert report["reload_ledger"] == {
        "reload": {"candidate": "B", "status": 200, "resident": "B"},
        "failed": {"candidate": "C", "status": 400, "resident": "B"},
    }
    assert (
        materialization.verify_materialization(
            directory, report, expected_records=tape(), expected_binding=binding()
        )
        == report["answers"]
    )
    assert all(report["times_ns"][key] >= 0 for key in report["times_ns"])
    if candidate != "source_sufficient":
        assert all(record["kind"] != "wrapper_return" for record in map(json.loads, stored))
    assert ("prefix survival" in report["durability"]) == (candidate == "projected_drain")


def test_fresh_directory_and_candidate_boundary(tmp_path: Path) -> None:
    directory = tmp_path / "candidate"
    materialization.materialize(tape(), "projected_drain", directory, binding())
    with pytest.raises(ValueError, match="fresh owned"):
        materialization.materialize(tape(), "projected_drain", directory, binding())
    with pytest.raises(ValueError, match="fresh owned"):
        materialization.materialize(tape(), "unknown", tmp_path / "other", binding())


@pytest.mark.parametrize("changed", ["records", "binding"])
def test_changed_expected_source_or_binding_is_rejected(tmp_path: Path, changed: str) -> None:
    directory = tmp_path / "candidate"
    report = materialization.materialize(tape(), "projected_event", directory, binding())
    records, metadata = tape(), binding()
    if changed == "records":
        records[4]["x"] = 3
    else:
        metadata["source_sha256"] = "d" * 64
    with pytest.raises(ValueError):
        materialization.verify_materialization(
            directory, report, expected_records=records, expected_binding=metadata
        )


@pytest.mark.parametrize(
    "field", ["records", "logical_bytes", "physical_closed_bytes", "provenance_bytes"]
)
def test_changed_report_census_is_rejected(tmp_path: Path, field: str) -> None:
    directory = tmp_path / "candidate"
    report = materialization.materialize(tape(), "projected_drain", directory, binding())
    report[field] += 1
    with pytest.raises(ValueError):
        materialization.verify_materialization(directory, report)


def test_database_payload_mutation_is_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "candidate"
    report = materialization.materialize(tape(), "source_sufficient", directory, binding())
    changed = deepcopy(tape()[0])
    changed["digest"] = "e" * 64
    with sqlite3.connect(directory / "audit.sqlite") as database:
        database.execute(
            "UPDATE records SET payload=? WHERE ordinal=0", (encode(changed).encode(),)
        )
    database.close()
    with pytest.raises(ValueError, match="database changed"):
        materialization.verify_materialization(directory, report)


def test_expected_source_rejects_payload_changed_in_live_wal(tmp_path: Path) -> None:
    directory = tmp_path / "candidate"
    report = materialization.materialize(tape(), "source_sufficient", directory, binding())
    changed = deepcopy(tape()[0])
    changed["digest"] = "e" * 64
    database = sqlite3.connect(directory / "audit.sqlite")
    try:
        database.execute(
            "UPDATE records SET payload=? WHERE ordinal=0", (encode(changed).encode(),)
        )
        database.commit()
        with pytest.raises(ValueError):
            materialization.verify_materialization(
                directory, report, expected_records=tape(), expected_binding=binding()
            )
    finally:
        database.close()
