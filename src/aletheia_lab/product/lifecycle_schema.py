"""Exact schema migrations for the private product lifecycle store."""

from __future__ import annotations

import sqlite3
from typing import Final

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.project.identity import content_sha256

PRODUCT_LIFECYCLE_STORE_SCHEMA_VERSION: Final[int] = 4
_STORE_INTEGRITY_MESSAGE: Final[str] = "Stored product state failed its integrity check."

_MIGRATION_V1: Final[str] = """
CREATE TABLE IF NOT EXISTS product_records (
    record_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    record_kind TEXT NOT NULL,
    project_id TEXT,
    snapshot_id TEXT,
    parent_result_id TEXT,
    payload_json TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL CHECK(length(canonical_sha256) = 64)
);
CREATE INDEX IF NOT EXISTS product_records_scope
    ON product_records(project_id, snapshot_id, record_kind, record_id);
"""
_MIGRATION_V2: Final[str] = """
CREATE TABLE IF NOT EXISTS product_confirmations (
    preview_id TEXT PRIMARY KEY,
    project_state_record_id TEXT NOT NULL UNIQUE,
    FOREIGN KEY(project_state_record_id)
        REFERENCES product_records(record_id) ON DELETE RESTRICT
);
"""
_MIGRATION_V3: Final[str] = """
CREATE TABLE IF NOT EXISTS product_preview_leases (
    preview_id TEXT PRIMARY KEY,
    leased_at TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL CHECK(length(canonical_sha256) = 64),
    FOREIGN KEY(preview_id)
        REFERENCES product_records(record_id) ON DELETE RESTRICT
);
"""
_MIGRATION_V4: Final[str] = """
CREATE TABLE IF NOT EXISTS product_projects (
    project_id TEXT PRIMARY KEY,
    project_state_record_id TEXT NOT NULL UNIQUE,
    FOREIGN KEY(project_state_record_id)
        REFERENCES product_records(record_id) ON DELETE RESTRICT
);
INSERT INTO product_projects(project_id, project_state_record_id)
SELECT records.project_id, records.record_id
FROM product_confirmations AS confirmations
JOIN product_records AS records
    ON records.record_id = confirmations.project_state_record_id
WHERE records.project_id IS NOT NULL
  AND records.rowid = (
      SELECT MAX(candidate_records.rowid)
      FROM product_confirmations AS candidate_confirmations
      JOIN product_records AS candidate_records
          ON candidate_records.record_id = candidate_confirmations.project_state_record_id
      WHERE candidate_records.project_id = records.project_id
  );
"""
_MIGRATIONS: Final[tuple[str, ...]] = (
    _MIGRATION_V1,
    _MIGRATION_V2,
    _MIGRATION_V3,
    _MIGRATION_V4,
)
_PRODUCT_TABLE_COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    "product_records": (
        "record_id",
        "schema_version",
        "record_kind",
        "project_id",
        "snapshot_id",
        "parent_result_id",
        "payload_json",
        "canonical_sha256",
    ),
    "product_confirmations": ("preview_id", "project_state_record_id"),
    "product_preview_leases": ("preview_id", "leased_at", "canonical_sha256"),
    "product_projects": ("project_id", "project_state_record_id"),
}


def _migration_sha256(sql: str) -> str:
    return content_sha256(sql.strip().encode("utf-8"))


def _validated_migration_versions(rows: list[sqlite3.Row]) -> set[int]:
    versions: set[int] = set()
    for row in rows:
        version = int(row["version"])
        if (
            version < 1
            or version > PRODUCT_LIFECYCLE_STORE_SCHEMA_VERSION
            or str(row["migration_sha256"]) != _migration_sha256(_MIGRATIONS[version - 1])
        ):
            raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
        versions.add(version)
    return versions


def _apply_pending_migrations(
    connection: sqlite3.Connection,
    applied_versions: set[int],
) -> None:
    for version, migration in enumerate(_MIGRATIONS, start=1):
        if version in applied_versions:
            continue
        for statement in migration.split(";"):
            if statement.strip():
                connection.execute(statement)
        connection.execute(
            """
            INSERT INTO product_migration_history(version, migration_sha256)
            VALUES (?, ?)
            """,
            (version, _migration_sha256(migration)),
        )


def _validate_product_table_columns(connection: sqlite3.Connection) -> None:
    for table_name, expected_columns in _PRODUCT_TABLE_COLUMNS.items():
        columns = tuple(
            str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table_name})")
        )
        if columns != expected_columns:
            raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
