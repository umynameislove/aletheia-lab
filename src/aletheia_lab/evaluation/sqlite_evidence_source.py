"""Controlled native SQLite BLOB reads, independent of the Joblib development source.

Only locally constructed in-memory tables are opened. No external database, SQL,
pickle, historical artifact or model is loaded. The observer hashes the exact
bytes returned by SQLite; query text and row declarations are not that observer.
"""

from __future__ import annotations

import json
import platform
import sqlite3
from pathlib import Path
from typing import Any

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.project.identity import content_sha256

CONTROLS = (
    ("healthy", "a", "a"),
    ("replacement", "a", "b"),
    ("restoration", "a", "a"),
    ("legitimate", "b", "b"),
    ("reverse", "b", "a"),
)
VIEWS = ("trace_only", "with_consumer")
EVALUATION_NAMESPACE = "sqlite-blob-transfer-evaluation-v1"
QUERY = "SELECT declared_sha256, payload FROM artifacts WHERE lookup_key = ?"


def producer_identity() -> dict[str, str]:
    connection = sqlite3.connect(":memory:")
    try:
        source_id = str(connection.execute("SELECT sqlite_source_id()").fetchone()[0])
    finally:
        connection.close()
    return {
        "name": "sqlite3 BLOB store",
        "sqlite_version": sqlite3.sqlite_version,
        "python_version": platform.python_version(),
        "sqlite_source_id": source_id,
        "python_wrapper_sha256": file_sha256(Path(str(sqlite3.__file__))),
        "license": "SQLite public domain; Python PSF license",
        "storage": "isolated in-memory database; no cache policy",
    }


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def artifact_bytes(namespace: str, variant: str) -> bytes:
    """Fixture namespaces differ from the prospectively fixed evaluation bytes."""
    if not namespace or variant not in {"a", "b"}:
        raise ValueError("invalid local artifact specification")
    return _json({"namespace": namespace, "weights": [2, 7, 11] if variant == "a" else [11, 7, 2]})


def document(
    identifier: str, kind: str, authority: str, value: object, *, scope: str = "attempt-0"
) -> dict[str, str]:
    raw = value.encode() if isinstance(value, str) else _json(value)
    return {
        "id": identifier,
        "kind": kind,
        "authority": authority,
        "scope": scope,
        "text": raw.decode(),
        "sha256": content_sha256(raw),
    }


def _read_cell(
    connection: sqlite3.Connection, *, key: str, requested: bytes
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    trace: list[str] = []
    # Install the callback only for SELECT. Expanded INSERT/UPDATE SQL could
    # otherwise expose BLOB literals, hidden control choices or raw payloads.
    connection.set_trace_callback(trace.append)
    try:
        rows = connection.execute(QUERY, (key,)).fetchall()
    finally:
        connection.set_trace_callback(None)
    if len(rows) != 1 or len(trace) != 1 or not isinstance(rows[0][1], bytes):
        raise ValueError("native query did not return one observed BLOB")
    declared, consumed = rows[0]
    observed = {
        "consumed_sha256": content_sha256(consumed),
        "byte_count": len(consumed),
        "returned_hex": consumed.hex(),
    }
    documents = [
        document("query", "sqlite-trace", "native-statement", trace[0]),
        document(
            "manifest",
            "sqlite-row-metadata",
            "row-declaration",
            {
                "schema_version": "sqlite-row-metadata/v1",
                "lookup": {"key": key},
                "manifest": {"declared_sha256": declared},
            },
        ),
        document(
            "selection",
            "sqlite-selection-pin",
            "caller-pin",
            {
                "schema_version": "sqlite-selection-pin/v1",
                "selection": {"key": key, "expected": {"sha256": content_sha256(requested)}},
            },
        ),
        document(
            "cell",
            "sqlite-blob-witness",
            "same-returned-buffer",
            {
                "schema_version": "sqlite-blob-witness/v1",
                "cell": {"column": "payload", "sha256": observed["consumed_sha256"]},
            },
        ),
    ]
    return documents, observed


def generate_source(*, namespace: str = EVALUATION_NAMESPACE) -> dict[str, Any]:
    """Execute the complete fixed schedule, never choose controls from outcomes."""
    blobs = {variant: artifact_bytes(namespace, variant) for variant in ("a", "b")}
    pins = {variant: content_sha256(raw) for variant, raw in blobs.items()}
    keys = {variant: content_sha256(_json([namespace, "lookup", variant])) for variant in blobs}
    cases = []
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE artifacts(lookup_key TEXT PRIMARY KEY, declared_sha256 TEXT, payload BLOB)"
        )
        connection.executemany(
            "INSERT INTO artifacts VALUES(?, ?, ?)",
            [(keys[variant], pins[variant], raw) for variant, raw in blobs.items()],
        )
        for index, (control, requested, installed) in enumerate(CONTROLS):
            connection.execute(
                "UPDATE artifacts SET payload = ? WHERE lookup_key = ?",
                (blobs[installed], keys[requested]),
            )
            docs, observed = _read_cell(connection, key=keys[requested], requested=blobs[requested])
            if bytes.fromhex(observed["returned_hex"]) != blobs[installed]:
                raise ValueError("native returned bytes differ from the frozen schedule")
            cases.append(
                {
                    "case_id": f"case-{index:02d}",
                    "control": control,
                    "documents": docs,
                    "observation": observed,
                    "reference": {
                        "requested_sha256": pins[requested],
                        "consumed_sha256": pins[installed],
                        "status": "no_binding_fault" if requested == installed else "binding_fault",
                    },
                }
            )
    finally:
        connection.close()
    return {
        "schema_version": "sqlite-evidence-source/v1",
        "namespace": namespace,
        "producer": producer_identity(),
        "source_cluster_count": 1,
        "frozen_artifacts_hex": {variant: raw.hex() for variant, raw in blobs.items()},
        "cases": cases,
    }


def visible_reference(case: dict[str, Any], view: str) -> dict[str, Any]:
    """Construct gold independently of parser/extractor/resolver implementation.

    Values come from pre-replacement pins and native returned bytes, not from
    parsing visible text. Hashes/citations identify the independently authored
    observer records. This is controlled instrumentation, not host attestation.
    """
    if view not in VIEWS:
        raise ValueError("unknown fixed visibility view")
    docs = {doc["id"]: doc for doc in case["documents"]}
    facts = [
        {
            "kind": "requested_endpoint",
            "digest": case["reference"]["requested_sha256"],
            "pointer": "/documents/selection/selection/expected/sha256",
            "document_sha256": docs["selection"]["sha256"],
        }
    ]
    if view == "with_consumer":
        facts.append(
            {
                "kind": "loaded_endpoint",
                "digest": content_sha256(bytes.fromhex(case["observation"]["returned_hex"])),
                "pointer": "/documents/cell/cell/sha256",
                "document_sha256": docs["cell"]["sha256"],
            }
        )
    return {
        "facts": facts,
        "resolution": {
            "state": "identified" if view == "with_consumer" else "ambiguous",
            "compatible": (
                [case["reference"]["status"]]
                if view == "with_consumer"
                else ["binding_fault", "no_binding_fault"]
            ),
        },
    }
