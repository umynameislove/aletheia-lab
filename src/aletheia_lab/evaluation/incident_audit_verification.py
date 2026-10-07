"""Read-only reconstruction from native receipts, committed frontiers and databases.

This validates a bounded study, not authenticity against a malicious host.
Reference arithmetic does not use the policy verdict or reconstruct new native
outcomes. Timings are descriptive observations, not deterministic replay targets.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from aletheia_lab.evaluation.cache_lifecycle_study import read_sealed
from aletheia_lab.evaluation.incident_audit_archive import IncidentAuditArchive
from aletheia_lab.evaluation.incident_audit_service import summarize
from aletheia_lab.evaluation.incident_audit_source import GRAPH_SHA256, source_summary
from aletheia_lab.evaluation.incident_audit_study import FILES, aggregate
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_audit import digest, resolve
from aletheia_lab.project.identity import content_sha256


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def reference_row(row: dict[str, Any], arm: str, *, capture_omission: int = 9) -> dict[str, Any]:
    ordinal = row["ordinal"]
    selected = "aaa" if ordinal % 2 == 0 else "bbb"
    requested = "aaa" if ordinal % 8 == 7 else selected
    if arm == "route_repair":
        selected = requested
    values = [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
    if ordinal % 8 == 3 or ordinal == 15:
        values = [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]]
    weights = [[1, 2], [3, 4], [5, 6]] if selected == "aaa" else [[2, 1], [4, 3], [6, 5]]
    failed = ordinal == 11
    expected = (
        None
        if failed
        else [
            [x * w for x, w in zip(xs, ws, strict=True)]
            for xs, ws in zip(values, weights, strict=True)
        ]
    )
    if failed:
        values = [[1.0, 1.0], [1.0, 1.0]]
    _require(row["input"] == values and row["output"] == expected, "native matrix receipt differs")
    _require(row["actual_session"] == selected, "native session route differs")
    _require((row["native_error"] is not None) == failed, "native failure census differs")
    truth = "unknown" if failed else "compliant" if requested == selected else "violation"
    _require(
        row["reference"] == truth and row["strong_source_history"] == truth,
        "independent source history differs",
    )
    token, frame = f"request-{ordinal:03d}", row["frame"]
    _require(
        frame["token"] == token and frame["requested"] == requested and frame["closed"],
        "request scope/closure differs",
    )
    _require(
        frame["input"] == digest(values)
        and frame["output"] == (digest(expected) if expected is not None else None),
        "input/output identity differs",
    )
    _require(frame["failed"] == failed, "frame terminal differs")
    captured = arm == "complete_capture" or ordinal != capture_omission
    if captured and not failed:
        generation = f"session-{selected}"
        # Numeric values in the native producer are floats; JSON hashing distinguishes them.
        float_weights = [[float(w) for w in ws] for ws in weights]
        fingerprint = digest(
            {
                "graph": GRAPH_SHA256,
                "W": float_weights,
                "dtype": "float32",
                "shape": [3, 2],
                "provider": "CPUExecutionProvider",
            }
        )
        _require(
            frame["loads"]
            == {
                generation: {
                    "model": selected,
                    "artifact": GRAPH_SHA256,
                    "fingerprint": fingerprint,
                }
            },
            "load dependency differs",
        )
        _require(
            len(frame["uses"]) == 1 and frame["uses"][0]["generation"] == generation,
            "actual-use join differs",
        )
    else:
        _require(not frame["loads"] and not frame["uses"], "uncaptured association fabricated")
    _require(
        row["caller_receipt_sha256"]
        == digest({"token": token, "output": expected, "error": row["native_error"]}),
        "caller receipt differs",
    )
    _require(row["capture_answer"] == resolve(frame), "live captured answer differs")
    return {**row, "reference": truth}


def witness_answers(witness: dict[str, Any]) -> dict[str, str | None]:
    state = IncidentAuditArchive._parse(witness["state"])
    raw = IncidentAuditArchive._document(state)
    _require(content_sha256(raw) == witness["state_sha256"], "query frontier differs")
    _require(IncidentAuditArchive._charge(state) <= state["budget"], "query quota differs")
    _require(
        IncidentAuditArchive._needed(state["entries"]) == set(state["atoms"]),
        "query dependency union differs",
    )
    for lease in state["leases"].values():
        _require(set(lease["scopes"]) <= set(state["entries"]), "accepted closure lost")
    return {
        scope: resolve(IncidentAuditArchive._frame(scope, state["entries"][scope], state["atoms"]))
        if scope in state["entries"]
        else None
        for scope in witness["scopes"]
    }


def verify_native_floor(rows: list[dict[str, Any]], count: int) -> None:
    _require(
        [r["ordinal"] for r in rows] == list(range(count)), "native floor ordinal census differs"
    )
    for row in rows:
        ordinal = row["ordinal"]
        values = [[1, 2], [3, 4], [5, 6]]
        if ordinal % 8 == 3 or ordinal == 15:
            values = [[0, 0], [0, 0], [0, 0]]
        weights = [[1, 2], [3, 4], [5, 6]] if ordinal % 2 == 0 else [[2, 1], [4, 3], [6, 5]]
        expected = (
            None
            if ordinal == 11
            else [
                [x * w for x, w in zip(xs, ws, strict=True)]
                for xs, ws in zip(values, weights, strict=True)
            ]
        )
        _require(
            row["output"] == expected and (row["native_error"] is not None) == (ordinal == 11),
            "native floor arithmetic/failure receipt differs",
        )
        _require(
            type(row["native_ns"]) is int and row["native_ns"] >= 0, "native floor clock differs"
        )


def _database(path: Path) -> sqlite3.Connection:
    _require(path.is_file() and not path.is_symlink(), "regular private database required")
    _require(not Path(str(path) + "-wal").exists(), "closed database required for immutable replay")
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    db.execute("PRAGMA query_only=ON")
    _require(
        db.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "database integrity differs"
    )
    return db


def verify_process(report: dict[str, Any], directory: Path, config: dict[str, Any]) -> int:
    rows = [
        reference_row(
            row, report["source_arm"], capture_omission=config["capture_omission_ordinal"]
        )
        for row in report["rows"]
    ]
    _require(
        [row["ordinal"] for row in rows] == list(range(config["native_calls_per_process"])),
        "offered native census differs",
    )
    _require(source_summary(rows) == report["source_summary"], "source summary differs")
    expected_names = {
        f"{b}-{p}-{t}"
        for b in config["logical_capacity_bytes"]
        for p in config["policies"]
        for t in config["recovery_tiers"]
    } | {"full-reference"}
    _require(set(report["archives"]) == expected_names, "archive arm census differs")
    queries = 0
    for name, arm in report["archives"].items():
        late, known = arm["offers"], arm["prospective_admissions"]
        _require(
            [item["id"] for item in late] == [item["id"] for item in report["schedule"]],
            "offered incident census differs",
        )
        expected_known = list(
            range(0, config["native_calls_per_process"], config["preknown_obligation_every_calls"])
        )
        _require(
            [item["scopes"] for item in known] == [[f"request-{i:03d}"] for i in expected_known],
            "preknown census differs",
        )
        for item in known + late:
            _require(item["answers"] == witness_answers(item["witness"]), "retained answer differs")
            _require(item["scopes"] == item["witness"]["scopes"], "query scope differs")
            _require(
                int(item["witness"]["state"]["now"], 16) == item["queried_at"],
                "query clock differs",
            )
            queries += 1
        _require(
            arm["summary"] == summarize(known + late, rows), "combined service summary differs"
        )
        _require(arm["late_summary"] == summarize(late, rows), "incident summary differs")
        _require(arm["preknown_summary"] == summarize(known, rows), "prospective summary differs")
        path = directory / f"{name}.sqlite"
        _require(
            path.stat().st_size == arm["closed_db_bytes"]
            and content_sha256(path.read_bytes()) == arm["db_sha256"],
            "closed archive bytes differ",
        )
        db = _database(path)
        try:
            raw = bytes(db.execute("SELECT payload FROM state WHERE id=1").fetchone()[0])
            state = IncidentAuditArchive._parse(json.loads(raw))
            _require(
                content_sha256(raw) == arm["storage"]["state_sha256"], "archive frontier differs"
            )
            witness_answers(
                {
                    "state": json.loads(raw),
                    "state_sha256": content_sha256(raw),
                    "scopes": list(state["entries"]),
                }
            )
            for key in ("accepted", "refused", "evicted", "overruns"):
                _require(state[key] == arm["storage"][key], "durable obligation counters differ")
            _require(
                state["accepted"] == arm["summary"].get("accepted", 0)
                and state["refused"] == arm["summary"].get("refused", 0),
                "admission census differs",
            )
        finally:
            db.close()
    path = directory / "secondary.sqlite"
    _require(
        content_sha256(path.read_bytes()) == report["secondary"]["sha256"], "secondary bytes differ"
    )
    db = _database(path)
    try:
        observed = {
            scope: (raw, digest) for scope, raw, digest in db.execute("SELECT * FROM evidence")
        }
        for row in rows:
            payload = encode(row["frame"])
            _require(
                observed.pop(row["frame"]["token"]) == (payload, content_sha256(payload.encode())),
                "secondary differs from actual capture path",
            )
        _require(not observed, "secondary hidden evidence differs")
    finally:
        db.close()
    return queries


def verify(root: Path, directory: Path) -> dict[str, Any]:
    started = perf_counter_ns()
    plan, results = read_sealed(directory / "plan.json"), read_sealed(directory / "results.json")
    _require(results["plan_sha256"] == plan["sha256"], "plan/results binding differs")
    _require(set(plan["code"]) == set(FILES), "source binding census differs")
    verification_path = "src/aletheia_lab/evaluation/incident_audit_verification.py"
    for name, file_hash in plan["code"].items():
        if name != verification_path:
            _require(
                content_sha256((root / name).read_bytes()) == file_hash,
                "current execution code differs from frozen run",
            )
        _require(
            content_sha256((directory / "code-snapshot" / name).read_bytes()) == file_hash,
            "code snapshot differs",
        )
    config, reports, queries = plan["protocol"], [], 0
    for arm in (*config["source_arms"], "native_floor"):
        for replicate in range(config["process_replicates"]):
            report = read_sealed(directory / f"{arm}-{replicate}.json")
            if report["status"] == "development_executed":
                _require(
                    report["source_arm"] == arm
                    and report["source_identity"] == plan["source_identity"],
                    "source identity/arm differs",
                )
                queries += verify_process(report, directory / f"{arm}-{replicate}", config)
            elif report["status"] == "native_control_executed":
                _require(
                    arm == "native_floor"
                    and len(report["rows"]) == config["native_calls_per_process"],
                    "native floor census differs",
                )
                _require(
                    report["source_identity"] == plan["source_identity"],
                    "native floor source identity differs",
                )
                verify_native_floor(report["rows"], config["native_calls_per_process"])
            else:
                _require(
                    report["status"] in {"worker_failed", "worker_timeout"},
                    "terminal worker disposition differs",
                )
                for stream in ("stdout", "stderr"):
                    _require(
                        content_sha256((directory / f"{arm}-{replicate}.{stream}").read_bytes())
                        == report[f"{stream}_sha256"],
                        "failure stream differs",
                    )
            reports.append(report)
    _require(
        reports == results["processes"] and aggregate(reports) == results["summary"],
        "aggregate reconstruction differs",
    )
    return {
        "status": "read_only_incident_audit_replay_pass",
        "verification": "pass",
        "processes": len(reports),
        "query_frontiers_verified": queries,
        "plan_sha256": plan["sha256"],
        "results_sha256": results["sha256"],
        "verification_wall_ns": perf_counter_ns() - started,
        "analysis_code_sha256": content_sha256(Path(__file__).read_bytes()),
        "frozen_analysis_code_sha256": plan["code"][verification_path],
        "analysis_evolved": content_sha256(Path(__file__).read_bytes())
        != plan["code"][verification_path],
        "provider_calls": 0,
    }
