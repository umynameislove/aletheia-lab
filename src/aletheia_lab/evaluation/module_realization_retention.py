"""Offline ordinary interval pins after incident demand; no online admission claim."""

from __future__ import annotations

import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.cache_lifecycle_study import read_sealed, seal
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.module_realization_store import audit, recovered
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

LIMITATIONS = (
    "offline rematerialization only after incident horizon; no pre-demand pruning, "
    "online prediction benefit, capacity pressure, future-growth guarantee, scheduling "
    "superiority or B-method admission; physical pages may not shrink with payload"
)
DURABILITY = "SQLite WAL/FULL; one commit per retained record in both projections"


def _index(
    records: list[dict[str, Any]], requests: list[str]
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    if len(set(requests)) != len(requests):
        raise ValueError("duplicate independent request census")
    sequences = [row["seq"] for row in records]
    if any(type(seq) is not int or seq <= 0 for seq in sequences):
        raise ValueError("invalid recovered event sequence")
    if len(set(sequences)) != len(sequences):
        raise ValueError("duplicate recovered event sequence")
    loads: dict[str, dict[str, Any]] = {}
    predictions: dict[str, dict[str, Any]] = {}
    for row in records:
        kind = row["kind"]
        target = loads if kind == "load" else predictions if kind == "predict" else None
        if target is None:
            continue
        identity = row["load_id"] if kind == "load" else row["request_id"]
        if identity in target:
            raise ValueError("duplicate recovered lifecycle identity")
        target[identity] = row
    if set(predictions) - set(requests):
        raise ValueError("prediction outside independent request census")
    return loads, predictions


def _materialize(path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    # Exact Collector compact schema and projection; no extra pin/index table.
    write_new_file(path, b"")
    payload_bytes = 0
    peak_bytes = 0
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute(
            "CREATE TABLE events(seq INTEGER PRIMARY KEY,payload TEXT NOT NULL,"
            "digest TEXT NOT NULL)"
        )
        db.commit()
        peak_bytes = sum(p.stat().st_size for p in (path, Path(f"{path}-wal")) if p.exists())
        for row in rows:
            payload = encode(row)
            db.execute(
                "INSERT INTO events VALUES(?,?,?)",
                (row["seq"], payload, content_sha256(payload.encode())),
            )
            db.commit()
            payload_bytes += len(payload.encode())
            peak_bytes = max(
                peak_bytes,
                sum(p.stat().st_size for p in (path, Path(f"{path}-wal")) if p.exists()),
            )
    restored = recovered(path)
    if restored != rows:
        raise ValueError("ordinary projection recovery differs")
    return {
        "path": path.name,
        "records": len(rows),
        "record_commits": len(rows),
        "payload_bytes": payload_bytes,
        "peak_database_and_wal_bytes": peak_bytes,
        "closed_database_bytes": path.stat().st_size,
        "sha256": content_sha256(path.read_bytes()),
    }


def _derive(
    records: list[dict[str, Any]], requests: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Derive pins and claims only from recovered raw evidence and a caller census."""
    loads, predictions = _index(records, requests)
    compact = sorted(
        ({key: value for key, value in row.items() if key != "diagnostics"} for row in records),
        key=lambda row: row["seq"],
    )
    full_audit = audit(compact, requests)
    complete = full_audit["closure"] and all(
        request in predictions and predictions[request]["load_id"] in loads for request in requests
    )
    demand = (
        [
            request
            for request in requests
            if loads[predictions[request]["load_id"]]["label"] == "B"
            and loads[predictions[request]["load_id"]]["intended"] == 2.0
        ]
        if complete
        else list(requests)
    )
    refs = Counter(predictions[request]["load_id"] for request in demand if request in predictions)
    kept = [
        row
        for row in compact
        if not complete
        or row["kind"] == "closure"
        or row["kind"] == "load"
        and row["load_id"] in refs
        or row["kind"] == "predict"
        and row["request_id"] in demand
    ]
    interval_audit = audit(kept, requests)
    demanded_verdicts = {request: interval_audit["verdicts"][request] for request in demand}
    adequate = (
        bool(demand)
        and complete
        and interval_audit["closure"]
        and all(
            verdict in {"compliant", "violation"} and verdict == full_audit["verdicts"][request]
            for request, verdict in demanded_verdicts.items()
        )
    )
    result = {
        "disposition": "NARROW",
        "demand": {
            "rule": "all requests bound to B-labeled intended-2 loads, regardless of verdict",
            "derivation": "complete" if complete else "unknown_conservative_full_census",
            "request_ids": demand,
            "load_reference_counts": dict(sorted(refs.items())),
            "union_load_ids": sorted(refs),
        },
        "all_compact": {"audit": full_audit},
        "interval_compact": {
            "demand_verdicts": demanded_verdicts,
            "demand_adequate": adequate,
            "closure": interval_audit["closure"],
            "full_service_verdicts": interval_audit["verdicts"],
        },
        "full_request_census": list(requests),
        "removed_sequences": [row["seq"] for row in compact if row not in kept],
        "limitations": LIMITATIONS,
        "durability": DURABILITY,
    }
    return compact, kept, result


def evaluate(records: list[dict[str, Any]], requests: list[str], directory: Path) -> dict[str, Any]:
    """Materialize the actual recovered union required by B-deployment demand.

    All records existed before demand was derived. Missing observations cannot
    narrow the demand: pin the entire census and mark derivation unknown instead.
    A retained closure still names *all* client requests; it is not a claim that
    predictions removed outside the incident window remain auditable.
    """
    compact, kept, result = _derive(records, requests)
    if directory.is_symlink():
        raise ValueError("ordinary projection directory cannot be a symlink")
    directory.mkdir(parents=True, exist_ok=True)
    paths = [directory / f"{name}.sqlite" for name in ("all-compact", "interval-compact")]
    if any(path.exists() or path.is_symlink() for path in paths):
        raise ValueError("ordinary projections require fresh destinations")
    for name, path, rows in zip(
        ("all_compact", "interval_compact"), paths, (compact, kept), strict=True
    ):
        result[name] = {**_materialize(path, rows), **result[name]}
    return result


def _summary(findings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "cells": len(findings),
        "materialized_databases": 2 * len(findings),
        "complete_demand_cells": sum(row["demand"]["derivation"] == "complete" for row in findings),
        "demand_adequate_cells": sum(
            row["interval_compact"]["demand_adequate"] for row in findings
        ),
        "disposition": "NARROW_postincident_subset_service_not_online_admission",
    }


def _source(study: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = read_sealed(study / "plan.json")
    original = read_sealed(study / "results.json")
    if (
        original["plan_sha256"] != plan["sha256"]
        or [row["config"] for row in original["executions"]] != plan["cells"]
    ):
        raise ValueError("retention source census differs")
    return plan, original


def _requests(config: dict[str, Any]) -> list[str]:
    count = config["requests_per_stage"]
    if type(count) is not int or count <= 0:
        raise ValueError("invalid independent request census configuration")
    return [f"r-{i:03}" for i in range(3 * count + 1)]


def run(study: Path, destination: Path) -> dict[str, Any]:
    """All executed store cells, including loss/crash; no favourable-arm selection."""
    plan, original = _source(study)
    if destination.exists() or destination.is_symlink():
        raise ValueError("fresh retention directory required")
    destination.mkdir(parents=True)
    rows = []
    for index, config in enumerate(plan["cells"]):
        if config["evidence"] == "native":
            continue
        records = recovered(study / f"cell-{index:03}" / "evidence.sqlite")
        requests = _requests(config)
        result = evaluate(records, requests, destination / f"cell-{index:03}")
        rows.append(
            {
                "index": index,
                "config": config,
                "records_sha256": content_sha256(encode(records).encode()),
                "result": result,
            }
        )
    report = seal(
        {
            "schema": "module-realization-retention-premise/v1",
            "source_plan_sha256": plan["sha256"],
            "source_results_sha256": original["sha256"],
            "code_sha256": content_sha256(Path(__file__).read_bytes()),
            "cells": rows,
            "analysis": _summary([row["result"] for row in rows]),
        }
    )
    write_new_file(destination / "results.json", encode(report).encode())
    return {
        "verification": "pass",
        "results_sha256": report["sha256"],
        "analysis": report["analysis"],
    }


def _verify_projection(path: Path, rows: list[dict[str, Any]], cost: dict[str, Any]) -> None:
    if path.parent.is_symlink() or path.is_symlink() or not path.is_file():
        raise ValueError("retention physical projection differs")
    if recovered(path) != rows:
        raise ValueError("retention physical projection differs")
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA table_info(events)").fetchall() != [
            (0, "seq", "INTEGER", 0, None, 1),
            (1, "payload", "TEXT", 1, None, 0),
            (2, "digest", "TEXT", 1, None, 0),
        ] or db.execute("PRAGMA journal_mode").fetchone() != ("wal",):
            raise ValueError("retention physical projection differs")
        payloads = [row[0] for row in db.execute("SELECT payload FROM events ORDER BY seq")]
    if payloads != [encode(row) for row in rows]:
        raise ValueError("retention physical projection differs")
    expected: dict[str, Any] = {
        "path": path.name,
        "records": len(payloads),
        "record_commits": len(rows),
        "payload_bytes": sum(len(payload.encode()) for payload in payloads),
        "closed_database_bytes": path.stat().st_size,
        "sha256": content_sha256(path.read_bytes()),
    }
    # A closed database cannot replay its historical WAL peak. Check its bound,
    # but do not present this retained measurement as an independently replayed cost.
    peak = cost["peak_database_and_wal_bytes"]
    if (
        any(encode(cost[key]) != encode(value) for key, value in expected.items())
        or type(peak) is not int
        or peak < expected["closed_database_bytes"]
    ):
        raise ValueError("retention physical projection cost differs")


def verify(study: Path, destination: Path) -> dict[str, Any]:
    report = read_sealed(destination / "results.json")
    plan, original = _source(study)
    indices = [i for i, config in enumerate(plan["cells"]) if config["evidence"] != "native"]
    if (
        report["schema"] != "module-realization-retention-premise/v1"
        or report["source_plan_sha256"] != plan["sha256"]
        or report["source_results_sha256"] != original["sha256"]
        or report["code_sha256"] != content_sha256(Path(__file__).read_bytes())
        or [row["index"] for row in report["cells"]] != indices
    ):
        raise ValueError("retention bound source differs")
    for row in report["cells"]:
        index, result = row["index"], row["result"]
        records = recovered(study / f"cell-{index:03}" / "evidence.sqlite")
        if row["config"] != plan["cells"][index] or row["records_sha256"] != content_sha256(
            encode(records).encode()
        ):
            raise ValueError("retention raw records differ")
        compact, kept, expected = _derive(records, _requests(plan["cells"][index]))
        for key, value in expected.items():
            observed = (
                {field: result[key][field] for field in value}
                if key in {"all_compact", "interval_compact"}
                else result[key]
            )
            if encode(observed) != encode(value):
                raise ValueError("retention demand or audit replay differs")
        for name, rows in (("all_compact", compact), ("interval_compact", kept)):
            path = destination / f"cell-{index:03}" / f"{name.replace('_', '-')}.sqlite"
            _verify_projection(path, rows, result[name])
    if _summary([row["result"] for row in report["cells"]]) != report["analysis"]:
        raise ValueError("retention aggregate differs")
    return {"verification": "pass", "analysis": report["analysis"]}
