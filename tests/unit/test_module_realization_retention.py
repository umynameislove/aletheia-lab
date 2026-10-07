"""Ordinary late-demand pins: real projection bytes, shared roots, no false census."""

from __future__ import annotations

import copy
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation import module_realization_retention as retention
from aletheia_lab.evaluation.cache_lifecycle_study import read_sealed, seal
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.module_realization_retention import evaluate
from aletheia_lab.evaluation.module_realization_store import Collector, recovered
from aletheia_lab.project.identity import content_sha256


@pytest.fixture
def workload() -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    requests = ["ordinary-0", "ordinary-1", "deployment-0", "deployment-1", "later-0"]

    def load(identity: str, label: str, intended: float) -> dict[str, Any]:
        return {
            "kind": "load",
            "load_id": identity,
            "label": label,
            "intended": intended,
            "expected_helper_sha256": label,
            "binding": {"coefficient": intended, "helper_sha256": label},
        }

    def predict(identity: str, root: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": "predict",
            "request_id": identity,
            "load_id": root["load_id"],
            "binding": dict(root["binding"]),
            "x": 0,
            "y": 0,
            "diagnostics": {"not_a_retention_advantage": "large" * 30},
        }

    a, b, later = load("load-0", "A", 1.0), load("load-1", "B", 2.0), load("load-2", "A", 1.0)
    rows.extend(
        [
            a,
            predict(requests[0], a),
            predict(requests[1], a),
            b,
            predict(requests[2], b),
            predict(requests[3], b),
            later,
            predict(requests[4], later),
            {"kind": "closure", "requests": requests},
        ]
    )
    return [{**row, "seq": index + 1} for index, row in enumerate(rows)], requests


def test_actual_interval_union_uses_identical_compact_schema_and_shared_load(
    tmp_path: Path, workload: tuple[list[dict[str, Any]], list[str]]
) -> None:
    rows, requests = workload
    original = copy.deepcopy(rows)
    result = evaluate(rows, requests, tmp_path)
    assert rows == original
    assert result["demand"]["request_ids"] == requests[2:4]
    assert result["demand"]["load_reference_counts"] == {"load-1": 2}
    assert result["demand"]["union_load_ids"] == ["load-1"]
    assert result["interval_compact"]["demand_adequate"]
    kept = recovered(tmp_path / "interval-compact.sqlite")
    assert [row["kind"] for row in kept] == ["load", "predict", "predict", "closure"]
    assert kept[-1]["requests"] == result["full_request_census"] == requests
    for name in ("all-compact", "interval-compact"):
        restored = recovered(tmp_path / f"{name}.sqlite")
        assert all("diagnostics" not in row for row in restored)
        with sqlite3.connect(tmp_path / f"{name}.sqlite") as db:
            assert db.execute("PRAGMA table_info(events)").fetchall() == [
                (0, "seq", "INTEGER", 0, None, 1),
                (1, "payload", "TEXT", 1, None, 0),
                (2, "digest", "TEXT", 1, None, 0),
            ]
            assert db.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    assert result["interval_compact"]["payload_bytes"] < result["all_compact"]["payload_bytes"]
    assert result["all_compact"]["record_commits"] == len(rows)
    assert result["interval_compact"]["record_commits"] == len(kept)
    assert result["all_compact"]["closed_database_bytes"] > 0
    assert "no pre-demand pruning" in result["limitations"]


def test_removed_predictions_do_not_claim_full_service_adequacy(
    tmp_path: Path, workload: tuple[list[dict[str, Any]], list[str]]
) -> None:
    rows, requests = workload
    result = evaluate(rows, requests, tmp_path)
    answers = result["interval_compact"]["full_service_verdicts"]
    assert answers[requests[0]] == answers[requests[-1]] == "unknown"
    assert result["interval_compact"]["closure"]
    assert result["interval_compact"]["demand_verdicts"] == {
        identity: "compliant" for identity in requests[2:4]
    }


@pytest.mark.parametrize("missing", ["load", "predict", "closure"])
def test_missing_observation_cannot_shrink_demand_to_surviving_records(
    tmp_path: Path, workload: tuple[list[dict[str, Any]], list[str]], missing: str
) -> None:
    rows, requests = workload
    removed = next(
        row
        for row in rows
        if row["kind"] == missing and (missing == "closure" or row.get("load_id") == "load-1")
    )
    rows = [row for row in rows if row is not removed]
    result = evaluate(rows, requests, tmp_path)
    assert result["demand"]["derivation"] == "unknown_conservative_full_census"
    assert result["demand"]["request_ids"] == requests
    assert not result["interval_compact"]["demand_adequate"]
    assert result["removed_sequences"] == []
    assert result["all_compact"]["records"] == result["interval_compact"]["records"]
    if missing != "closure":
        assert result["interval_compact"]["demand_verdicts"][requests[2]] == "unknown"


def test_demand_is_deployment_based_not_selected_by_violation_or_operand(
    tmp_path: Path, workload: tuple[list[dict[str, Any]], list[str]]
) -> None:
    rows, requests = workload
    b = next(row for row in rows if row.get("load_id") == "load-1")
    b["binding"] = {"coefficient": 1.0, "helper_sha256": "A"}
    for row in rows:
        if row["kind"] == "predict" and row["load_id"] == "load-1":
            row["binding"] = dict(b["binding"])
    result = evaluate(rows, requests, tmp_path)
    assert result["demand"]["request_ids"] == requests[2:4]
    assert set(result["interval_compact"]["demand_verdicts"].values()) == {"violation"}
    assert result["interval_compact"]["demand_adequate"]


def test_late_request_after_failed_reload_keeps_resident_load_dependency(
    tmp_path: Path, workload: tuple[list[dict[str, Any]], list[str]]
) -> None:
    rows, requests = workload
    later_load = next(row for row in rows if row.get("load_id") == "load-2")
    later_load.update(kind="load_failure", label="C", preserved_load_id="load-1")
    last = next(row for row in rows if row.get("request_id") == requests[-1])
    last["load_id"] = "load-1"
    last["binding"] = {"coefficient": 2.0, "helper_sha256": "B"}
    result = evaluate(rows, requests, tmp_path)
    assert result["demand"]["request_ids"] == requests[2:]
    assert result["demand"]["load_reference_counts"] == {"load-1": 3}
    assert result["interval_compact"]["demand_adequate"]


def test_conflict_is_not_an_adequate_binding_answer(
    tmp_path: Path, workload: tuple[list[dict[str, Any]], list[str]]
) -> None:
    rows, requests = workload
    next(row for row in rows if row.get("request_id") == requests[2])["binding"] = {}
    result = evaluate(rows, requests, tmp_path)
    assert result["interval_compact"]["demand_verdicts"][requests[2]] == "conflict"
    assert not result["interval_compact"]["demand_adequate"]


def test_duplicate_census_or_evidence_and_existing_destination_are_rejected(
    tmp_path: Path, workload: tuple[list[dict[str, Any]], list[str]]
) -> None:
    rows, requests = workload
    with pytest.raises(ValueError, match="duplicate independent"):
        evaluate(rows, [*requests, requests[0]], tmp_path)
    with pytest.raises(ValueError, match="duplicate recovered event"):
        evaluate([*rows, rows[0]], requests, tmp_path)
    with pytest.raises(ValueError, match="duplicate recovered lifecycle"):
        evaluate([*rows, {**rows[0], "seq": 100}], requests, tmp_path)
    evaluate(rows, requests, tmp_path)
    with pytest.raises(ValueError, match="fresh destinations"):
        evaluate(rows, requests, tmp_path)


def _configs() -> list[dict[str, Any]]:
    """The 54 source cells include all 42 store cells, without running a native SDK."""
    cells = []
    for replicate in range(2):
        for variant, repair in (("collision", "none"), ("collision", "evict"), ("unique", "none")):
            for order in (["A", "B", "A"], ["B", "A", "B"]):
                for evidence in ("native", "compact", "full"):
                    cells.append(
                        {
                            "slice": "repair_cost",
                            "variant": variant,
                            "repair": repair,
                            "order": order,
                            "evidence": evidence,
                            "replicate": replicate,
                            "fault": "none",
                            "durability": "event",
                            "crash": "none",
                            "requests_per_stage": 12,
                        }
                    )
    for evidence in ("compact", "full"):
        base = {
            "variant": "collision",
            "repair": "none",
            "order": ["A", "B", "A"],
            "evidence": evidence,
            "replicate": 0,
            "requests_per_stage": 12,
        }
        for fault in ("drop_predict", "drop_load", "delay", "duplicate", "drop_closure"):
            cells.append(
                {
                    **base,
                    "slice": "transport",
                    "fault": fault,
                    "durability": "event",
                    "crash": "none",
                }
            )
        for durability in ("event", "drain"):
            for crash in ("before_flush", "after_ack"):
                cells.append(
                    {
                        **base,
                        "slice": "crash",
                        "fault": "none",
                        "durability": durability,
                        "crash": crash,
                    }
                )
    return cells


def _fixture_capture(directory: Path, config: dict[str, Any]) -> None:
    """Construct recovered fixtures, including loss and unflushed prefixes, offline."""
    directory.mkdir()
    collector = Collector(directory, config)
    requests = [f"r-{i:03}" for i in range(37)]
    try:
        for stage, label in enumerate(config["order"]):
            actual = (
                config["order"][0]
                if config["variant"] == "collision" and config["repair"] == "none"
                else label
            )
            binding = {"coefficient": 2.0 if actual == "B" else 1.0, "helper_sha256": actual}
            collector.emit(
                {
                    "kind": "load",
                    "load_id": f"load-{stage}",
                    "label": label,
                    "intended": 2.0 if label == "B" else 1.0,
                    "expected_helper_sha256": label,
                    "binding": binding,
                    "diagnostics": {"source": "fixture" * 20},
                }
            )
            for request in requests[12 * stage : 12 * (stage + 1)]:
                collector.emit(
                    {
                        "kind": "predict",
                        "request_id": request,
                        "load_id": f"load-{stage}",
                        "binding": binding,
                        "x": 0,
                        "y": 0,
                        "diagnostics": {"source": "fixture" * 20},
                    }
                )
        collector.emit({"kind": "load_failure", "label": "C", "preserved_load_id": "load-2"})
        collector.emit(
            {"kind": "predict", "request_id": requests[-1], "load_id": "load-2", "binding": binding}
        )
        if config["crash"] != "before_flush":
            collector.emit({"kind": "closure", "requests": requests})
            collector.flush()
    finally:
        collector.close()


def _reseal(path: Path, document: dict[str, Any]) -> dict[str, Any]:
    document = copy.deepcopy(document)
    document.pop("sha256", None)
    sealed = seal(document)
    path.write_bytes(encode(sealed).encode())
    return sealed


@pytest.fixture(scope="module")
def sealed_capture(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("module-retention-fixture")
    study, destination = root / "study", root / "retention"
    study.mkdir()
    cells = _configs()
    plan = _reseal(study / "plan.json", {"schema": "module-realization-plan/v1", "cells": cells})
    _reseal(
        study / "results.json",
        {
            "schema": "module-realization-results/v1",
            "plan_sha256": plan["sha256"],
            "executions": [{"config": config} for config in cells],
        },
    )
    for index, config in enumerate(cells):
        if config["evidence"] != "native":
            _fixture_capture(study / f"cell-{index:03}", config)
    assert retention.run(study, destination)["verification"] == "pass"
    return study, destination


@pytest.fixture
def retention_fixture(tmp_path: Path, sealed_capture: tuple[Path, Path]) -> tuple[Path, Path]:
    study, destination = sealed_capture
    copied_study, copied_destination = tmp_path / "study", tmp_path / "retention"
    shutil.copytree(study, copied_study)
    shutil.copytree(destination, copied_destination)
    return copied_study, copied_destination


def test_sealed_run_and_verify_materialize_every_store_cell_including_loss_and_crash(
    sealed_capture: tuple[Path, Path],
) -> None:
    study, destination = sealed_capture
    report = read_sealed(destination / "results.json")
    verification = retention.verify(study, destination)
    assert verification["verification"] == "pass"
    assert verification["analysis"]["cells"] == 42
    assert verification["analysis"]["materialized_databases"] == 84
    assert len(list(destination.glob("cell-*/*.sqlite"))) == 84
    expected_indices = [i for i, config in enumerate(_configs()) if config["evidence"] != "native"]
    assert [row["index"] for row in report["cells"]] == expected_indices
    for row in report["cells"]:
        result, config = row["result"], row["config"]
        census = [f"r-{i:03}" for i in range(37)]
        assert result["full_request_census"] == census
        assert result["disposition"] == "NARROW"
        assert "capacity pressure" in result["limitations"]
        if (
            config["fault"] in {"drop_load", "drop_predict", "drop_closure"}
            or config["crash"] == "before_flush"
        ):
            assert result["demand"]["derivation"] == "unknown_conservative_full_census"
            assert result["demand"]["request_ids"] == census
            assert not result["interval_compact"]["demand_adequate"]
            assert result["removed_sequences"] == []
        else:
            assert result["demand"]["derivation"] == "complete"
            assert result["interval_compact"]["demand_adequate"]
            assert len(result["demand"]["request_ids"]) == (12 if config["order"][0] == "A" else 25)


@pytest.mark.parametrize(
    "mutation",
    [
        "demand",
        "derivation",
        "references",
        "reference_type",
        "union",
        "adequacy",
        "adequacy_type",
        "removals",
        "full_audit",
        "demand_verdict",
        "service_verdict",
        "closure",
        "census",
        "disposition",
        "limitations",
        "durability",
        "schema",
        "config",
        "cell_census",
        "records",
        "record_type",
        "record_commits",
        "payload_bytes",
        "closed_database_bytes",
        "sha256",
        "path",
        "peak_database_and_wal_bytes",
    ],
)
def test_verify_rejects_rehashed_retention_claims(
    retention_fixture: tuple[Path, Path], mutation: str
) -> None:
    study, destination = retention_fixture
    report = read_sealed(destination / "results.json")
    result = report["cells"][0]["result"]
    request = result["demand"]["request_ids"][0]
    if mutation == "demand":
        result["demand"]["request_ids"].pop()
    elif mutation == "derivation":
        result["demand"]["derivation"] = "unknown_conservative_full_census"
    elif mutation == "references":
        result["demand"]["load_reference_counts"]["load-1"] += 1
    elif mutation == "reference_type":
        result["demand"]["load_reference_counts"]["load-1"] = 12.0
    elif mutation == "union":
        result["demand"]["union_load_ids"].append("unobserved-load")
    elif mutation == "adequacy":
        result["interval_compact"]["demand_adequate"] = False
        report["analysis"]["demand_adequate_cells"] -= 1
    elif mutation == "adequacy_type":
        result["interval_compact"]["demand_adequate"] = 1
    elif mutation == "removals":
        result["removed_sequences"].pop()
    elif mutation == "full_audit":
        result["all_compact"]["audit"]["verdicts"][request] = "unknown"
    elif mutation == "demand_verdict":
        result["interval_compact"]["demand_verdicts"][request] = "unknown"
    elif mutation == "service_verdict":
        result["interval_compact"]["full_service_verdicts"][request] = "unknown"
    elif mutation == "closure":
        result["interval_compact"]["closure"] = False
    elif mutation == "census":
        result["full_request_census"].pop()
    elif mutation in {"disposition", "limitations", "durability"}:
        result[mutation] = "online_capacity_admitted"
    elif mutation == "schema":
        report["schema"] = "unbound-retention/v1"
    elif mutation == "config":
        report["cells"][0]["config"]["requests_per_stage"] = 11
    elif mutation == "cell_census":
        report["cells"].pop()
    elif mutation == "sha256":
        result["interval_compact"][mutation] = "0" * 64
    elif mutation == "path":
        result["interval_compact"][mutation] = "all-compact.sqlite"
    elif mutation == "peak_database_and_wal_bytes":
        result["interval_compact"][mutation] = 0
    elif mutation == "record_type":
        result["interval_compact"]["records"] = float(result["interval_compact"]["records"])
    else:
        result["interval_compact"][mutation] += 1
    _reseal(destination / "results.json", report)
    with pytest.raises(ValueError, match="retention"):
        retention.verify(study, destination)


@pytest.mark.parametrize("mutation", ["missing_execution", "execution_config", "plan_binding"])
def test_run_and_verify_reject_rehashed_source_execution_census(
    retention_fixture: tuple[Path, Path], mutation: str
) -> None:
    study, destination = retention_fixture
    original = read_sealed(study / "results.json")
    if mutation == "missing_execution":
        original["executions"].pop()
    elif mutation == "execution_config":
        original["executions"][0]["config"]["requests_per_stage"] = 11
    else:
        original["plan_sha256"] = "0" * 64
    original = _reseal(study / "results.json", original)
    report = read_sealed(destination / "results.json")
    report["source_results_sha256"] = original["sha256"]
    _reseal(destination / "results.json", report)
    with pytest.raises(ValueError, match="source census"):
        retention.verify(study, destination)
    with pytest.raises(ValueError, match="source census"):
        retention.run(study, destination.parent / "fresh-retention")


@pytest.mark.parametrize("projection", ["all_compact", "interval_compact"])
@pytest.mark.parametrize("mutation", ["delete", "alter"])
def test_verify_rejects_resealed_altered_physical_rows(
    retention_fixture: tuple[Path, Path], projection: str, mutation: str
) -> None:
    study, destination = retention_fixture
    report = read_sealed(destination / "results.json")
    cell = report["cells"][0]
    cost = cell["result"][projection]
    path = destination / f"cell-{cell['index']:03}" / cost["path"]
    with closing(sqlite3.connect(path)) as db:
        seq, payload = db.execute("SELECT seq,payload FROM events ORDER BY seq LIMIT 1").fetchone()
        if mutation == "delete":
            db.execute("DELETE FROM events WHERE seq=?", (seq,))
        else:
            event = next(row for row in recovered(path) if row["seq"] == seq)
            event["label"] = "invented-deployment"
            payload = encode(event)
            db.execute(
                "UPDATE events SET payload=?,digest=? WHERE seq=?",
                (payload, content_sha256(payload.encode()), seq),
            )
        db.commit()
    with closing(sqlite3.connect(path)) as db:
        payloads = [row[0] for row in db.execute("SELECT payload FROM events")]
    cost.update(
        records=len(payloads),
        record_commits=len(payloads),
        payload_bytes=sum(len(payload.encode()) for payload in payloads),
        closed_database_bytes=path.stat().st_size,
        sha256=content_sha256(path.read_bytes()),
    )
    _reseal(destination / "results.json", report)
    with pytest.raises(ValueError, match="physical projection"):
        retention.verify(study, destination)
