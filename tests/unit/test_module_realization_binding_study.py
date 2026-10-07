"""Fixed binding-study census and recovery checks, without native SDK execution."""

from __future__ import annotations

import copy
import json
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation import module_realization_binding_study as study
from aletheia_lab.evaluation.cache_lifecycle_study import seal
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.project.identity import content_sha256


def _result(config: dict[str, Any]) -> dict[str, Any]:
    """Represent the specified finite workload, rather than invoke candidate code."""
    immutable = config["scenario"] == "immutable"
    count = 37 if immutable else 9
    records: list[dict[str, Any]] = []
    offers = []
    loads = []

    def binding(coefficient: float, generation: int, code: str = "original") -> dict[str, Any]:
        return {
            "coefficient": coefficient,
            "helper_sha256": str(int(coefficient)) if coefficient != 3.0 else "2",
            "code_sha256": code,
            "model_object_id": generation,
        }

    def emit(row: dict[str, Any]) -> None:
        records.append({**row, "seq": len(records) + 1})

    resident = None
    for index in range(count):
        generation = min(index // 12, 2) if immutable else 0
        if index == 0 or immutable and index in {12, 24}:
            label = config["order"][generation]
            intended = 1.0 if label == "A" else 2.0
            actual_label = (
                config["order"][0]
                if config["variant"] == "collision" and config["repair"] == "none"
                else label
            )
            actual = 1.0 if actual_label == "A" else 2.0
            resident = binding(actual, generation)
            loads.append({"status": 200, "body": {"loaded": label}})
            emit(
                {
                    "kind": "load",
                    "load_id": f"load-{generation}",
                    "label": label,
                    "intended": intended,
                    "expected_helper_sha256": str(int(intended)),
                    "binding": dict(resident),
                }
            )
        assert resident is not None
        phase = (
            "after_failed_load"
            if immutable and index == 36
            else "normal"
            if immutable or index < 3
            else "changed"
            if index < 6
            else "restored"
        )
        x = 7 if immutable and index == 36 else index % 3
        identity = f"r-{index:03}"
        if immutable and index == 36:
            loads.append({"status": 400, "body": {"error": "invalid artifact"}})
            emit({"kind": "load_failure", "label": "C", "preserved_load_id": "load-2"})
        fresh = dict(resident)
        actual_coefficient = resident["coefficient"]
        if phase == "changed":
            actual_coefficient = {
                "coefficient": 3.0,
                "function": 2.0,
                "float_shadow": 18.0,
                "module_lookup": 99.0,
            }[config["scenario"]]
            if config["scenario"] == "coefficient":
                fresh["coefficient"] = 3.0
            if config["scenario"] == "function":
                fresh["code_sha256"] = "changed"
        refused = (
            phase == "changed"
            and config["scenario"] in {"float_shadow", "module_lookup"}
            and config["binding_mode"] != "load_cache"
        )
        response = None if refused else {"status": 200, "body": {"y": x * actual_coefficient}}
        offers.append(
            {
                "request_id": identity,
                "x": x,
                "phase": phase,
                "response": response,
                "error": "unsupported owned predict footprint" if refused else None,
                "fresh_before": dict(fresh),
                "fresh_after": dict(fresh),
                "native_calls": 0 if refused else 1,
                "candidate_ns": 1000 + index,
            }
        )
        if not refused:
            emit(
                {
                    "kind": "predict",
                    "request_id": identity,
                    "load_id": f"load-{generation}",
                    "x": x,
                    "y": x * actual_coefficient,
                    "binding": dict(resident) if config["binding_mode"] == "load_cache" else fresh,
                }
            )
    emit({"kind": "closure", "requests": [row["request_id"] for row in offers]})
    native_calls = sum(row["native_calls"] for row in offers)
    return {
        "config": copy.deepcopy(config),
        "loads": loads,
        "offers": offers,
        "records": records,
        "reference_events": [],
        "native": {
            "prediction_calls": native_calls,
            "actual_native_loads": len(loads),
            "full_scan_count": 1,
            "cache_hit_count": 2 * native_calls,
            "hash_ns": 100,
            "guard_ns": 100,
            "reference_hash_ns": 100,
        },
        "collector": {
            "durable_ack_digests": {
                str(row["seq"]): content_sha256(encode(row).encode()) for row in records
            },
            "write_ns": 100,
        },
        "database_bytes": 0,
    }


def _acknowledge(result: dict[str, Any]) -> None:
    result["collector"]["durable_ack_digests"] = {
        str(row["seq"]): content_sha256(encode(row).encode()) for row in result["records"]
    }


def _reseal(path: Path, value: dict[str, Any]) -> None:
    value = copy.deepcopy(value)
    value.pop("sha256", None)
    path.write_text(encode(seal(value)))


def test_matrix_has_all_ordinary_arms_and_fixed_mutation_controls() -> None:
    cells = study.cells()
    assert len(cells) == 48
    assert Counter(row["scenario"] for row in cells) == {
        "immutable": 36,
        "coefficient": 3,
        "function": 3,
        "float_shadow": 3,
        "module_lookup": 3,
    }
    assert Counter(row["binding_mode"] for row in cells) == {
        "scan": 16,
        "load_cache": 16,
        "guarded_cache": 16,
    }
    assert len({encode(row) for row in cells}) == 48


@pytest.mark.parametrize("config", study.cells())
def test_specification_derived_census_is_analyzable(config: dict[str, Any]) -> None:
    finding = study.analyze(_result(config))
    assert finding["verification"] == "pass"
    assert finding["offered"] == (37 if config["scenario"] == "immutable" else 9)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "order", "x", "phase"])
def test_fixed_offer_census_rejects_tampering(mutation: str) -> None:
    result = _result(study.cells()[0])
    if mutation == "missing":
        result["offers"].pop()
    elif mutation == "duplicate":
        result["offers"][1]["request_id"] = result["offers"][0]["request_id"]
    elif mutation == "order":
        result["offers"][0], result["offers"][1] = result["offers"][1], result["offers"][0]
    elif mutation == "x":
        result["offers"][0]["x"] = 11
        result["offers"][0]["response"]["body"]["y"] = 11.0
        prediction = next(row for row in result["records"] if row.get("request_id") == "r-000")
        prediction.update(x=11, y=11.0)
        _acknowledge(result)
    else:
        result["offers"][0]["phase"] = "changed"
    with pytest.raises(ValueError, match="census|offer|phase|input"):
        study.analyze(result)


@pytest.mark.parametrize("mutation", ["x", "y", "duplicate", "load"])
def test_recovered_predictions_must_match_actual_offers(mutation: str) -> None:
    result = _result(study.cells()[0])
    prediction = next(row for row in result["records"] if row.get("request_id") == "r-001")
    if mutation == "x":
        prediction["x"] = 999
    elif mutation == "y":
        prediction["y"] = 999.0
    elif mutation == "duplicate":
        result["records"].append({**prediction, "seq": len(result["records"]) + 1})
    else:
        prediction["load_id"] = "not-the-resident"
    _acknowledge(result)
    with pytest.raises(ValueError, match="predict|response|load|identity|census"):
        study.analyze(result)


def test_acknowledged_payload_digest_is_exact() -> None:
    result = _result(study.cells()[0])
    result["collector"]["durable_ack_digests"]["1"] = "0" * 64
    with pytest.raises(ValueError, match="acknowledged"):
        study.analyze(result)


@pytest.fixture
def sealed_study(tmp_path: Path) -> Path:
    directory = tmp_path / "binding"
    directory.mkdir()
    bindings = {}
    for name in study.FILES:
        path = directory / "code-snapshot" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
        bindings[name] = content_sha256(path.read_bytes())
    plan = seal({"cells": study.cells(), "bindings": bindings})
    (directory / "plan.json").write_text(encode(plan))
    executions = []
    for index, config in enumerate(plan["cells"]):
        result = _result(config)
        target = directory / f"cell-{index:03}"
        target.mkdir()
        database = target / "evidence.sqlite"
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE events(seq INTEGER PRIMARY KEY,payload TEXT,digest TEXT)")
            db.executemany(
                "INSERT INTO events VALUES(?,?,?)",
                [
                    (row["seq"], encode(row), content_sha256(encode(row).encode()))
                    for row in result["records"]
                ],
            )
        result["database_bytes"] = database.stat().st_size
        _reseal(target / "results.json", result)
        executions.append({"config": config, "returncode": 0, "finding": study.analyze(result)})
    _reseal(
        directory / "results.json",
        {
            "plan_sha256": plan["sha256"],
            "executions": executions,
            "analysis": study._summary(executions),
        },
    )
    return directory


def test_complete_synthetic_recovery_verifies_without_native_calls(sealed_study: Path) -> None:
    assert study.verify(sealed_study)["verification"] == "pass"


@pytest.mark.parametrize("mutation", ["snapshot", "records", "aggregate", "worker", "cell"])
def test_verify_rejects_sealed_and_physical_tampering(sealed_study: Path, mutation: str) -> None:
    if mutation == "snapshot":
        (sealed_study / "code-snapshot" / study.FILES[0]).write_text("changed")
    elif mutation == "records":
        path = sealed_study / "cell-000" / "results.json"
        result = json.loads(path.read_text())
        result["records"][0]["binding"]["coefficient"] = 99.0
        _reseal(path, result)
    else:
        path = sealed_study / "results.json"
        report = json.loads(path.read_text())
        if mutation == "aggregate":
            next(iter(report["analysis"].values()))["offered"] += 1
        elif mutation == "worker":
            report["executions"][0]["returncode"] = 1
        else:
            report["executions"].pop()
        _reseal(path, report)
    with pytest.raises(ValueError, match="code|raw|aggregate|worker|census"):
        study.verify(sealed_study)


def test_failed_workers_and_parser_errors_preserve_the_full_planned_census(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, destination, artifacts = tmp_path / "root", tmp_path / "study", tmp_path / "artifacts"
    root.mkdir()
    artifacts.mkdir()
    (artifacts / "manifest.json").write_text("{}")
    for name in study.FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    invoked = []

    def execute(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        target = Path(command[command.index("--study-dir") + 1])
        config_path = Path(command[command.index("--config") + 1])
        config = json.loads(config_path.read_text())
        index = int(target.name.removeprefix("cell-"))
        invoked.append(index)
        if index == 0:
            raise subprocess.TimeoutExpired(command, 60, output=b"before timeout")
        target.mkdir()
        if index == 1:
            (target / "results.json").write_text("{invalid json")
        else:
            result = _result(config)
            if index == 2:
                result["offers"].pop()
            _reseal(target / "results.json", result)
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(study.subprocess, "run", execute)
    outcome = study.run(root, destination, artifacts, tmp_path / "python", tmp_path / "site")
    assert outcome["verification"] == "fail"
    assert invoked == list(range(48))
    report = json.loads((destination / "results.json").read_text())
    assert [row["config"] for row in report["executions"]] == study.cells()
    assert [row["finding"]["verification"] for row in report["executions"][:3]] == [
        "fail",
        "fail",
        "fail",
    ]
    assert sum(row["finding"]["verification"] == "pass" for row in report["executions"]) == 45
