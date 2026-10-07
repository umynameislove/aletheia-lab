"""Fresh-process native cost comparison, with equal pure-read audit semantics.

The source-informed repeated schedule and quotas are authored sensitivities.
SQL payload counters are not disk I/O; samples are not a global optimum.
"""

from __future__ import annotations

import copy
import json
import sqlite3
from importlib import import_module
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from aletheia_lab.evaluation.incident_audit_archive import IncidentAuditArchive
from aletheia_lab.evaluation.incident_audit_incremental import IncrementalAuditArchive
from aletheia_lab.evaluation.neighbors_audit_transfer import array, footprint, make_frame
from aletheia_lab.evaluation.request_model_audit import digest, resolve

MODES = ("native", "hash_only", "raw_static", "whole_static", "incremental_static")


def physical(path: Path) -> int:
    return sum(
        p.stat().st_size
        for suffix in ("", "-wal", "-shm")
        if (p := Path(str(path) + suffix)).exists()
    )


def read_frames(store: IncidentAuditArchive, scopes: list[str]) -> dict[str, str | None]:
    # Both physical implementations use the same global committed integrity check.
    state = store._read()
    store._validate(state)
    return {
        token: resolve(store._frame(token, state["entries"][token], state["atoms"]))
        if token in state["entries"]
        else None
        for token in scopes
    }


def _open_archive(
    path: Path, mode: str
) -> tuple[IncidentAuditArchive | None, sqlite3.Connection | None]:
    if mode in {"whole_static", "incremental_static"}:
        cls = IncrementalAuditArchive if mode == "incremental_static" else IncidentAuditArchive
        store = cls(path, "static", 1_000_000)
        return store, store.db
    if mode == "raw_static":
        db = sqlite3.connect(path)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("CREATE TABLE frames(token TEXT PRIMARY KEY,payload BLOB)")
        return None, db
    return None, None


def _capture(
    mode: str, regressor: Any, fingerprint: str, ordinal: int, query: Any, output: Any
) -> dict[str, Any] | None:
    if mode == "native":
        return None
    observed = digest(footprint(regressor))
    if mode == "hash_only":
        return None
    frame = make_frame(
        f"r{ordinal}",
        query,
        output,
        observed,
        "compliant" if observed == fingerprint else "violation",
    )
    frame["loads"] = {"fitted": frame["loads"].pop(f"r{ordinal}")}
    frame["uses"][0]["generation"] = "fitted"
    return frame


def _save(
    frame: dict[str, Any] | None,
    store: IncidentAuditArchive | None,
    db: sqlite3.Connection | None,
    ordinal: int,
) -> int:
    if frame is None:
        return 0
    if store is not None:
        if not store.put(frame, now=ordinal):
            raise ValueError("sufficient fixed cost quota refused a frame")
        return 0
    if db is None:
        raise ValueError("captured cost frame has no archive")
    raw = json.dumps(frame, sort_keys=True, separators=(",", ":")).encode()
    with db:
        db.execute("INSERT INTO frames VALUES(?,?)", (frame["token"], raw))
    return len(raw)


def run_cost(directory: Path, mode: str, count: int, repeat: int) -> dict[str, Any]:
    np = import_module("numpy")
    KNeighborsRegressor = import_module("sklearn.neighbors").KNeighborsRegressor

    if mode not in MODES or count not in (16, 64) or repeat not in (0, 1, 2):
        raise ValueError("cost cell outside frozen scope")
    directory.mkdir()
    rng = np.random.RandomState(1701 + repeat)
    x, y = rng.rand(40, 5), rng.rand(40, 1)
    queries = rng.rand(count, 1, 5)
    started = perf_counter_ns()
    regressor = KNeighborsRegressor(n_neighbors=12).fit(x, y)
    fit_ns = perf_counter_ns() - started
    registry = footprint(regressor)
    fingerprint = digest(registry)
    path = directory / "archive.sqlite"
    store, db = _open_archive(path, mode)
    registry_raw = json.dumps(registry, sort_keys=True, separators=(",", ":")).encode()
    if db is not None:
        with db:
            db.execute("CREATE TABLE enrollment(id INTEGER PRIMARY KEY,payload BLOB)")
            db.execute("INSERT INTO enrollment VALUES(1,?)", (registry_raw,))
    rows: list[dict[str, Any]] = []
    peak = physical(path) if db else 0
    submitted = len(registry_raw) if db else 0
    total_started = perf_counter_ns()
    for ordinal, query in enumerate(queries):
        started = perf_counter_ns()
        output = regressor.predict(query)
        native_ns = perf_counter_ns() - started
        started = perf_counter_ns()
        frame = _capture(mode, regressor, fingerprint, ordinal, query, output)
        capture_ns = perf_counter_ns() - started
        started = perf_counter_ns()
        submitted += _save(frame, store, db, ordinal)
        write_ack_ns = perf_counter_ns() - started
        if db:
            peak = max(peak, physical(path))
        rows.append(
            {
                "ordinal": ordinal,
                "input": array(query),
                "output": array(output),
                "native_ns": native_ns,
                "capture_ns": capture_ns,
                "write_ack_ns": write_ack_ns,
                "frame": frame,
            }
        )
    serving_stage_ns = perf_counter_ns() - total_started
    query_started = perf_counter_ns()
    answers: dict[str, str | None] = {}
    if store is not None:
        answers = read_frames(store, [f"r{i}" for i in range(count)])
    elif db is not None:
        answers = {
            token: resolve(json.loads(raw))
            for token, raw in db.execute("SELECT token,payload FROM frames ORDER BY token")
        }
    query_ns = perf_counter_ns() - query_started
    verify_started = perf_counter_ns()
    distances = np.linalg.norm(queries[:, 0, None, :] - x[None, :, :], axis=2)
    references = y[np.argsort(distances, axis=1)[:, :12]].mean(axis=1)
    numerical_correct = all(
        np.allclose(row["output"]["values"], reference, rtol=1e-12, atol=1e-12)
        for row, reference in zip(rows, references, strict=True)
    )
    state_unchanged = registry == footprint(regressor)
    verify_ns = perf_counter_ns() - verify_started
    metrics = copy.deepcopy(store.metrics) if store else {}
    if store:
        submitted += metrics["written_bytes"]
        store.close()
    elif db:
        db.close()
    return {
        "mode": mode,
        "count": count,
        "repeat": repeat,
        "fit_ns": fit_ns,
        "rows": rows,
        "answers": answers,
        "numerical_correct": numerical_correct,
        "fitted_state_unchanged": state_unchanged,
        "query_ns": query_ns,
        "verify_ns": verify_ns,
        "serving_stage_ns": serving_stage_ns,
        "registry_bytes": len(registry_raw),
        "sql_submitted_payload_bytes": submitted,
        "peak_db_wal_shm_bytes": peak,
        "closed_db_bytes": physical(path),
        "archive_metrics": metrics,
        "logical_quota": 1_000_000,
        "service": "delayed pure committed read of every completed request",
        "cost_scope": "local sequential native calls; no throughput claim",
    }
