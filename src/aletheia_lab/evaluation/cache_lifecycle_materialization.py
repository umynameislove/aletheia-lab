"""Measured offline ordinary projection/batching on immutable execution records.

These replays measure persistence and reconstruction, not serving latency.
After-drain batching has a weaker prefix-durability contract than event commits.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from aletheia_lab.evaluation.cache_lifecycle_certificate import (
    certificate_answers,
    lifecycle_answers,
    project_records,
)
from aletheia_lab.evaluation.litserve_evidence_provenance import sign_bundle, verify_bundle
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.project.identity import content_sha256

CANDIDATES = ("source_sufficient", "projected_event", "projected_drain")


def _verify_digest(directory: Path, digest: str) -> None:
    verify_bundle(directory)
    links = list(directory.glob("*.link"))
    values = [json.loads(path.read_bytes())["signed"] for path in links]
    frames = [
        frame
        for value in values
        for field in ("materials", "products")
        for frame in value[field].values()
    ]
    if len(values) != 2 or any(frame != {"sha256": digest} for frame in frames) or len(frames) != 2:
        raise ValueError("public chain does not bind this actual database")


def materialize(
    records: list[dict[str, Any]], candidate: str, directory: Path, binding: dict[str, Any]
) -> dict[str, Any]:
    if candidate not in CANDIDATES or directory.exists() or directory.is_symlink():
        raise ValueError("fresh owned materialization candidate required")
    directory.mkdir(parents=True)
    started = perf_counter_ns()
    values = records if candidate == "source_sufficient" else project_records(records)
    payloads = [encode(value).encode() for value in values]
    encoding_ns = perf_counter_ns() - started
    path = directory / "audit.sqlite"
    write_start = perf_counter_ns()
    connection = sqlite3.connect(path)
    commits = 0
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE records (ordinal INTEGER PRIMARY KEY, payload BLOB NOT NULL)"
        )
        connection.execute("CREATE TABLE binding (payload BLOB NOT NULL)")
        connection.execute("INSERT INTO binding VALUES (?)", (encode(binding).encode(),))
        connection.commit()
        commits += 1
        for index, payload in enumerate(payloads):
            connection.execute("INSERT INTO records VALUES (?, ?)", (index, payload))
            if candidate != "projected_drain":
                connection.commit()
                commits += 1
        if candidate == "projected_drain":
            connection.commit()
            commits += 1
        persistence_ns = perf_counter_ns() - write_start
        sizes = [
            file.stat().st_size
            for file in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm"))
        ]
    finally:
        connection.close()
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    read_start = perf_counter_ns()
    try:
        stored = [
            bytes(row[0])
            for row in connection.execute("SELECT payload FROM records ORDER BY ordinal")
        ]
        metadata = [bytes(row[0]) for row in connection.execute("SELECT payload FROM binding")]
    finally:
        connection.close()
    query_ns = perf_counter_ns() - read_start
    if stored != payloads or metadata != [encode(binding).encode()]:
        raise ValueError("materialized bytes or scoped binding changed")
    started = perf_counter_ns()
    answers = certificate_answers([json.loads(row) for row in stored])
    reload_ledger = lifecycle_answers([json.loads(row) for row in stored])
    reconstruction_ns = perf_counter_ns() - started
    started = perf_counter_ns()
    digest = content_sha256(path.read_bytes())
    hashing_ns = perf_counter_ns() - started
    started = perf_counter_ns()
    status = sign_bundle(digest, directory / "provenance")
    _verify_digest(directory / "provenance", digest)
    signing_verification_ns = perf_counter_ns() - started
    provenance_bytes = sum(
        p.stat().st_size for p in (directory / "provenance").iterdir() if p.is_file()
    )
    if status != "pass":
        raise ValueError("actual signed materialization failed")
    return {
        "candidate": candidate,
        "records": len(payloads),
        "commits": commits,
        "logical_bytes": sum(map(len, payloads)) + len(encode(binding).encode()),
        "physical_live_bytes": sum(sizes),
        "physical_closed_bytes": path.stat().st_size,
        "provenance_bytes": provenance_bytes,
        "database_sha256": digest,
        "times_ns": {
            "encoding": encoding_ns,
            "persistence": persistence_ns,
            "query": query_ns,
            "reconstruction": reconstruction_ns,
            "hashing": hashing_ns,
            "signing_verification": signing_verification_ns,
        },
        "answers": answers,
        "reload_ledger": reload_ledger,
        "durability": "FULL per admitted record"
        if candidate != "projected_drain"
        else "FULL after complete drain; no earlier prefix survival promise",
    }


def verify_materialization(
    directory: Path,
    report: dict[str, Any],
    *,
    expected_records: list[dict[str, Any]] | None = None,
    expected_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = directory / "audit.sqlite"
    wal = Path(str(path) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("closed materialization has unbound live WAL")
    if path.is_symlink() or content_sha256(path.read_bytes()) != report["database_sha256"]:
        raise ValueError("offline materialization database changed")
    _verify_digest(directory / "provenance", report["database_sha256"])
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        payloads = [
            bytes(row[0])
            for row in connection.execute("SELECT payload FROM records ORDER BY ordinal")
        ]
        metadata = [bytes(row[0]) for row in connection.execute("SELECT payload FROM binding")]
    finally:
        connection.close()
    candidate = report["candidate"]
    if candidate not in CANDIDATES or len(metadata) != 1:
        raise ValueError("materialization identity differs")
    if expected_binding is not None and metadata != [encode(expected_binding).encode()]:
        raise ValueError("materialized scoped binding changed")
    if expected_records is not None:
        expected = (
            expected_records
            if candidate == "source_sufficient"
            else project_records(expected_records)
        )
        if payloads != [encode(row).encode() for row in expected]:
            raise ValueError("materialized source projection changed")
    provenance_bytes = sum(
        p.stat().st_size for p in (directory / "provenance").iterdir() if p.is_file()
    )
    if (
        report["records"] != len(payloads)
        or report["commits"] != (2 if candidate == "projected_drain" else len(payloads) + 1)
        or report["logical_bytes"] != sum(map(len, payloads)) + len(metadata[0])
        or report["physical_closed_bytes"] != path.stat().st_size
        or report["provenance_bytes"] != provenance_bytes
    ):
        raise ValueError("materialized cost census changed")
    rows = [json.loads(row) for row in payloads]
    answers = certificate_answers(rows)
    if answers != report["answers"] or lifecycle_answers(rows) != report["reload_ledger"]:
        raise ValueError("materialized certificate reconstruction changed")
    return answers
