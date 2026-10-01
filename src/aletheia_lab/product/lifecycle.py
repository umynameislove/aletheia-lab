"""Persistent, scoped lifecycle records stored beside the reused P3 records."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal, Self, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.project.identity import (
    PROJECT_ID_PATTERN,
    SNAPSHOT_ID_PATTERN,
    canonical_project_sha256,
)

PRODUCT_LIFECYCLE_SCHEMA_VERSION: Final[Literal["p6-lifecycle-record/v1"]] = (
    "p6-lifecycle-record/v1"
)
PRODUCT_LIFECYCLE_STORE_SCHEMA_VERSION: Final[int] = 4

ProductRecordKind: TypeAlias = Literal[
    "preview",
    "project_state",
    "result",
    "turn",
    "deletion",
]
ProductRecordDisposition: TypeAlias = Literal["created", "identical"]

_PRODUCT_RECORD_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^p6-(?:preview|project-state|result|turn|deletion)-[0-9a-f]{64}$"
)
_RESULT_ID_PATTERN: Final[str] = r"^p6-result-[0-9a-f]{64}$"
_UTC_TIMESTAMP_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z"
)
_RECORD_PREFIX: Final[dict[ProductRecordKind, str]] = {
    "preview": "p6-preview",
    "project_state": "p6-project-state",
    "result": "p6-result",
    "turn": "p6-turn",
    "deletion": "p6-deletion",
}
_RECORD_NOT_FOUND_MESSAGE: Final[str] = "The requested product record is not available."
_STORE_INTEGRITY_MESSAGE: Final[str] = "Stored product state failed its integrity check."
_STORE_UNAVAILABLE_MESSAGE: Final[str] = "The product store could not be opened safely."
_CONFIRMATION_CONFLICT_MESSAGE: Final[str] = "The preview was already confirmed differently."

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


def _canonical_json(payload: dict[str, object]) -> str:
    canonical_project_sha256(payload)
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _migration_sha256(sql: str) -> str:
    return hashlib.sha256(sql.strip().encode("utf-8")).hexdigest()


def _canonical_utc_timestamp(value: str) -> str:
    if not isinstance(value, str) or _UTC_TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise ValueError("timestamp must be canonical UTC text")
    try:
        timestamp = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("timestamp must be valid UTC text") from exc
    if timestamp.tzinfo != UTC:
        raise ValueError("timestamp must use UTC")
    return value


def _preview_lease_sha256(preview_id: str, leased_at: str) -> str:
    return canonical_project_sha256(
        {
            "schema_version": "p6-preview-lease/v1",
            "preview_id": preview_id,
            "leased_at": leased_at,
        }
    )


class ProductLifecycleRecord(BaseModel):
    """One immutable product record with explicit project and snapshot scope."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )

    schema_version: Literal["p6-lifecycle-record/v1"] = PRODUCT_LIFECYCLE_SCHEMA_VERSION
    record_id: str = Field(pattern=_PRODUCT_RECORD_ID_PATTERN.pattern)
    record_kind: ProductRecordKind
    project_id: str | None = Field(default=None, pattern=PROJECT_ID_PATTERN)
    snapshot_id: str | None = Field(default=None, pattern=SNAPSHOT_ID_PATTERN)
    parent_result_id: str | None = Field(default=None, pattern=_RESULT_ID_PATTERN)
    payload_json: str

    @field_validator("payload_json")
    @classmethod
    def _payload_is_canonical_json(cls, value: str) -> str:
        try:
            payload = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("lifecycle payload must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("lifecycle payload must be a JSON object")
        if _canonical_json(cast(dict[str, object], payload)) != value:
            raise ValueError("lifecycle payload must use canonical JSON encoding")
        return value

    @model_validator(mode="after")
    def _scope_and_identity_match(self) -> Self:
        if self.record_kind == "preview":
            if any(
                value is not None
                for value in (self.project_id, self.snapshot_id, self.parent_result_id)
            ):
                raise ValueError("preview records must not claim a confirmed scope")
        else:
            if self.project_id is None:
                raise ValueError("confirmed lifecycle records require a project scope")
            if self.record_kind in {"result", "turn"} and self.snapshot_id is None:
                raise ValueError("result and turn records require a snapshot scope")
            if self.record_kind in {"project_state", "deletion"} and self.snapshot_id is not None:
                raise ValueError("project-state and deletion records must not claim a snapshot")
            if self.record_kind not in {"result", "turn"} and self.parent_result_id is not None:
                raise ValueError("only result and turn records may reference a parent result")

        expected_id = _record_id(
            record_kind=self.record_kind,
            project_id=self.project_id,
            snapshot_id=self.snapshot_id,
            parent_result_id=self.parent_result_id,
            payload=self.payload(),
        )
        if self.record_id != expected_id:
            raise ValueError("product record ID does not match its canonical content")
        return self

    def payload(self) -> dict[str, object]:
        """Return a fresh decoded copy of the private lifecycle payload."""

        decoded = json.loads(self.payload_json)
        if not isinstance(decoded, dict):
            raise AssertionError("validated lifecycle payload changed type")
        return cast(dict[str, object], decoded)

    def canonical_sha256(self) -> str:
        """Return the digest embedded in the content-addressed record ID."""

        return self.record_id.rsplit("-", maxsplit=1)[-1]


def _record_id(
    *,
    record_kind: ProductRecordKind,
    project_id: str | None,
    snapshot_id: str | None,
    parent_result_id: str | None,
    payload: dict[str, object],
) -> str:
    identity = {
        "schema_version": PRODUCT_LIFECYCLE_SCHEMA_VERSION,
        "record_kind": record_kind,
        "project_id": project_id,
        "snapshot_id": snapshot_id,
        "parent_result_id": parent_result_id,
        "payload": payload,
    }
    return f"{_RECORD_PREFIX[record_kind]}-{canonical_project_sha256(identity)}"


def build_product_lifecycle_record(
    *,
    record_kind: ProductRecordKind,
    payload: dict[str, object],
    project_id: str | None = None,
    snapshot_id: str | None = None,
    parent_result_id: str | None = None,
) -> ProductLifecycleRecord:
    """Build an immutable record and derive its stable scoped identifier."""

    payload_json = _canonical_json(payload)
    checked_payload = cast(dict[str, object], json.loads(payload_json))
    return ProductLifecycleRecord(
        record_id=_record_id(
            record_kind=record_kind,
            project_id=project_id,
            snapshot_id=snapshot_id,
            parent_result_id=parent_result_id,
            payload=checked_payload,
        ),
        record_kind=record_kind,
        project_id=project_id,
        snapshot_id=snapshot_id,
        parent_result_id=parent_result_id,
        payload_json=payload_json,
    )


def validate_product_record_id(
    record_id: str,
    *,
    expected_kind: ProductRecordKind | None = None,
) -> str:
    """Validate syntax without reflecting the supplied identifier in an error."""

    if not isinstance(record_id, str) or _PRODUCT_RECORD_ID_PATTERN.fullmatch(record_id) is None:
        raise ProductError("invalid_id", "The supplied product identifier is invalid.")
    if expected_kind is not None and not record_id.startswith(f"{_RECORD_PREFIX[expected_kind]}-"):
        raise ProductError("invalid_id", "The supplied product identifier is invalid.")
    return record_id


def _validate_project_id(project_id: str) -> str:
    if not isinstance(project_id, str) or re.fullmatch(PROJECT_ID_PATTERN, project_id) is None:
        raise ProductError("invalid_id", "The supplied project identifier is invalid.")
    return project_id


def _record_from_row(row: sqlite3.Row) -> ProductLifecycleRecord | None:
    try:
        return ProductLifecycleRecord.model_validate(
            {
                "record_id": row["record_id"],
                "schema_version": row["schema_version"],
                "record_kind": row["record_kind"],
                "project_id": row["project_id"],
                "snapshot_id": row["snapshot_id"],
                "parent_result_id": row["parent_result_id"],
                "payload_json": row["payload_json"],
            }
        )
    except (TypeError, ValueError):
        return None


def _put_record(
    connection: sqlite3.Connection,
    record: ProductLifecycleRecord,
) -> ProductRecordDisposition:
    checked = ProductLifecycleRecord.model_validate(record.model_dump(mode="python"))
    values = (
        checked.record_id,
        checked.schema_version,
        checked.record_kind,
        checked.project_id,
        checked.snapshot_id,
        checked.parent_result_id,
        checked.payload_json,
        checked.canonical_sha256(),
    )
    existing = connection.execute(
        """
        SELECT record_id, schema_version, record_kind, project_id, snapshot_id,
               parent_result_id, payload_json, canonical_sha256
        FROM product_records WHERE record_id = ?
        """,
        (checked.record_id,),
    ).fetchone()
    if existing is not None:
        if tuple(existing) != values:
            raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
        return "identical"
    connection.execute(
        """
        INSERT INTO product_records(
            record_id, schema_version, record_kind, project_id, snapshot_id,
            parent_result_id, payload_json, canonical_sha256
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        values,
    )
    return "created"


def put_current_project_state(
    connection: sqlite3.Connection,
    record: ProductLifecycleRecord,
) -> ProductRecordDisposition:
    """Persist one immutable state and atomically make it current for its project."""

    checked_record = ProductLifecycleRecord.model_validate(record.model_dump(mode="python"))
    if checked_record.record_kind != "project_state" or checked_record.project_id is None:
        raise TypeError("a current project state requires a project-state record")
    disposition = _put_record(connection, checked_record)
    connection.execute(
        """
        INSERT INTO product_projects(project_id, project_state_record_id)
        VALUES (?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
            project_state_record_id = excluded.project_state_record_id
        """,
        (checked_record.project_id, checked_record.record_id),
    )
    return disposition


def put_product_confirmation(
    connection: sqlite3.Connection,
    preview_id: str,
    record: ProductLifecycleRecord,
) -> ProductRecordDisposition:
    """Write one preview-to-project binding inside a caller-owned transaction."""

    checked_preview_id = validate_product_record_id(preview_id, expected_kind="preview")
    checked_record = ProductLifecycleRecord.model_validate(record.model_dump(mode="python"))
    if checked_record.record_kind != "project_state":
        raise TypeError("a product confirmation requires a project-state record")
    existing = connection.execute(
        "SELECT project_state_record_id FROM product_confirmations WHERE preview_id = ?",
        (checked_preview_id,),
    ).fetchone()
    if existing is not None and str(existing[0]) != checked_record.record_id:
        raise ProductError("preview_already_confirmed", _CONFIRMATION_CONFLICT_MESSAGE)
    disposition = put_current_project_state(connection, checked_record)
    if existing is None:
        connection.execute(
            """
            INSERT INTO product_confirmations(preview_id, project_state_record_id)
            VALUES (?, ?)
            """,
            (checked_preview_id, checked_record.record_id),
        )
    return disposition


class ProductLifecycleStore:
    """Product-only metadata tables in the existing P3 SQLite database."""

    def __init__(self, root: Path) -> None:
        self._database_path = root / "project-store.sqlite3"
        if (
            root.is_symlink()
            or not root.is_dir()
            or self._database_path.is_symlink()
            or not self._database_path.is_file()
        ):
            raise ProductError("store_unavailable", _STORE_UNAVAILABLE_MESSAGE)
        self._connection = sqlite3.connect(self._database_path)
        try:
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._migrate()
        except BaseException:
            self._connection.close()
            raise

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> ProductLifecycleStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _migrate(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS product_migration_history (
                    version INTEGER PRIMARY KEY,
                    migration_sha256 TEXT NOT NULL CHECK(length(migration_sha256) = 64)
                )
                """
            )
            rows = self._connection.execute(
                "SELECT version, migration_sha256 FROM product_migration_history ORDER BY version"
            ).fetchall()
            for row in rows:
                version = int(row["version"])
                if (
                    version < 1
                    or version > PRODUCT_LIFECYCLE_STORE_SCHEMA_VERSION
                    or str(row["migration_sha256"])
                    != _migration_sha256(_MIGRATIONS[version - 1])
                ):
                    raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
            applied = {int(row["version"]) for row in rows}
            for version, migration in enumerate(_MIGRATIONS, start=1):
                if version in applied:
                    continue
                for statement in migration.split(";"):
                    if statement.strip():
                        self._connection.execute(statement)
                self._connection.execute(
                    """
                    INSERT INTO product_migration_history(version, migration_sha256)
                    VALUES (?, ?)
                    """,
                    (version, _migration_sha256(migration)),
                )

        columns = tuple(
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(product_records)")
        )
        if columns != (
            "record_id",
            "schema_version",
            "record_kind",
            "project_id",
            "snapshot_id",
            "parent_result_id",
            "payload_json",
            "canonical_sha256",
        ):
            raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
        confirmation_columns = tuple(
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(product_confirmations)")
        )
        if confirmation_columns != ("preview_id", "project_state_record_id"):
            raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
        lease_columns = tuple(
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(product_preview_leases)")
        )
        if lease_columns != ("preview_id", "leased_at", "canonical_sha256"):
            raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
        project_columns = tuple(
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(product_projects)")
        )
        if project_columns != ("project_id", "project_state_record_id"):
            raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)

    def put(self, record: ProductLifecycleRecord) -> ProductRecordDisposition:
        """Persist one immutable record or accept an exact idempotent replay."""

        with self._connection:
            return _put_record(self._connection, record)

    def put_preview(
        self,
        record: ProductLifecycleRecord,
        *,
        leased_at: str,
    ) -> ProductRecordDisposition:
        """Persist an immutable preview and renew its private confirmation lease."""

        checked_record = ProductLifecycleRecord.model_validate(record.model_dump(mode="python"))
        if checked_record.record_kind != "preview":
            raise TypeError("a preview lease requires a preview record")
        checked_time = _canonical_utc_timestamp(leased_at)
        with self._connection:
            disposition = _put_record(self._connection, checked_record)
            self._connection.execute(
                """
                INSERT INTO product_preview_leases(
                    preview_id, leased_at, canonical_sha256
                ) VALUES (?, ?, ?)
                ON CONFLICT(preview_id) DO UPDATE SET
                    leased_at = excluded.leased_at,
                    canonical_sha256 = excluded.canonical_sha256
                """,
                (
                    checked_record.record_id,
                    checked_time,
                    _preview_lease_sha256(checked_record.record_id, checked_time),
                ),
            )
        return disposition

    def preview_leased_at(self, preview_id: str) -> str | None:
        """Return the integrity-checked issue time for one staged preview."""

        checked_id = validate_product_record_id(preview_id, expected_kind="preview")
        row = self._connection.execute(
            """
            SELECT leased_at, canonical_sha256
            FROM product_preview_leases WHERE preview_id = ?
            """,
            (checked_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            leased_at = _canonical_utc_timestamp(str(row["leased_at"]))
        except (TypeError, ValueError):
            raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE) from None
        if str(row["canonical_sha256"]) != _preview_lease_sha256(checked_id, leased_at):
            raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
        return leased_at

    def get_confirmation(self, preview_id: str) -> ProductLifecycleRecord | None:
        """Return the integrity-checked project state bound to a preview, if any."""

        checked_id = validate_product_record_id(preview_id, expected_kind="preview")
        row = self._connection.execute(
            """
            SELECT record_id, schema_version, record_kind, project_id, snapshot_id,
                   parent_result_id, payload_json, canonical_sha256
            FROM product_records
            WHERE record_id = (
                SELECT project_state_record_id
                FROM product_confirmations
                WHERE preview_id = ?
            )
            """,
            (checked_id,),
        ).fetchone()
        if row is None:
            return None
        record = _record_from_row(row)
        if (
            record is None
            or record.record_kind != "project_state"
            or record.canonical_sha256() != str(row["canonical_sha256"])
            or record.payload().get("preview_id") != checked_id
        ):
            raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
        return record

    def get_project_state(self, project_id: str) -> ProductLifecycleRecord:
        """Return the integrity-checked current state for one product project."""

        checked_id = _validate_project_id(project_id)
        row = self._connection.execute(
            """
            SELECT record_id, schema_version, record_kind, project_id, snapshot_id,
                   parent_result_id, payload_json, canonical_sha256
            FROM product_records
            WHERE record_id = (
                SELECT project_state_record_id
                FROM product_projects
                WHERE project_id = ?
            )
            """,
            (checked_id,),
        ).fetchone()
        if row is None:
            raise ProductError("record_not_found", _RECORD_NOT_FOUND_MESSAGE)
        record = _record_from_row(row)
        if (
            record is None
            or record.record_kind != "project_state"
            or record.project_id != checked_id
            or record.canonical_sha256() != str(row["canonical_sha256"])
        ):
            raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
        return record

    def get(
        self,
        record_id: str,
        *,
        expected_kind: ProductRecordKind | None = None,
        project_id: str | None = None,
        snapshot_id: str | None = None,
    ) -> ProductLifecycleRecord:
        """Load and verify one record, failing closed for a foreign scope."""

        checked_id = validate_product_record_id(record_id, expected_kind=expected_kind)
        row = self._connection.execute(
            """
            SELECT record_id, schema_version, record_kind, project_id, snapshot_id,
                   parent_result_id, payload_json, canonical_sha256
            FROM product_records WHERE record_id = ?
            """,
            (checked_id,),
        ).fetchone()
        if row is None:
            raise ProductError("record_not_found", _RECORD_NOT_FOUND_MESSAGE)
        record = _record_from_row(row)
        if record is None:
            raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
        if record.canonical_sha256() != str(row["canonical_sha256"]):
            raise ProductError("store_integrity_error", _STORE_INTEGRITY_MESSAGE)
        if project_id is not None and record.project_id != project_id:
            raise ProductError("record_not_found", _RECORD_NOT_FOUND_MESSAGE)
        if snapshot_id is not None and record.snapshot_id != snapshot_id:
            raise ProductError("record_not_found", _RECORD_NOT_FOUND_MESSAGE)
        return record
