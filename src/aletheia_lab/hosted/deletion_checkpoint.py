"""Typed minimal deletion history for an externally authenticated restore point.

Authentication, deployment/schema binding, complete page-chain verification and
keeping the restored deployment offline belong to the K18 recovery controller.
No caller-supplied SQL or filesystem path is accepted here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from aletheia_lab.hosted_auth.postgres_sessions import Connection
from aletheia_lab.hosted_contract.deletions import TombstoneId
from aletheia_lab.hosted_contract.runtime_management import ProjectId
from aletheia_lab.hosted_contract.session_capabilities import Identifier
from aletheia_lab.product.deletion import _deletion_state
from aletheia_lab.product.lifecycle import ProductLifecycleRecord
from aletheia_lab.project.identity import content_hasher
from aletheia_lab.project.persistence import _json_bytes

from .async_work import run_blocking
from .legacy_import import model_metadata
from .object_store import ObjectDescriptor
from .retention import PostgresRetention, RetentionRejected, _bound, _rows, require_owner

MAX_CHECKPOINT_PAGE_BYTES = 128 * 1024**2
Cursor = tuple[Identifier, ProjectId]


class DeletionFence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    workspace_id: Identifier
    project_id: ProjectId
    tombstone_id: TombstoneId
    revision: int = Field(ge=0, le=9223372036854775807)
    deleted_by: Identifier | None
    requested_at: AwareDatetime
    eligible_at: AwareDatetime
    project_days: int = Field(ge=0, le=3650)
    time_basis: Literal["delete_commit", "migration_first_seen", "restore_checkpoint"]
    record_json: Annotated[str, Field(max_length=64 * 1024**2)]

    @field_validator("requested_at", "eligible_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_validator("record_json")
    @classmethod
    def _safe_record(cls, value: str) -> str:
        try:
            record = ProductLifecycleRecord.model_validate_json(value)
            if record.record_kind != "deletion":
                raise ValueError
            _deletion_state(record)
            if _json_bytes(record.model_dump(mode="json")).decode() != value:
                raise ValueError
        except Exception:
            raise ValueError("Only a canonical, typed deletion record is permitted.") from None
        return value

    @model_validator(mode="after")
    def _scope(self) -> Self:
        record = ProductLifecycleRecord.model_validate_json(self.record_json)
        if (record.project_id, record.record_id) != (
            self.project_id,
            self.tombstone_id,
        ) or self.eligible_at != self.requested_at + timedelta(days=self.project_days):
            raise ValueError("Deletion checkpoint identity or time does not match.")
        return self

    def identity(self) -> str:
        return content_hasher(
            json.dumps(
                self.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()


class DeletionCheckpointPage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    schema_version: Literal["hosted-deletion-checkpoint/v1"] = "hosted-deletion-checkpoint/v1"
    after: Cursor | None
    items: tuple[DeletionFence, ...] = Field(max_length=1000)
    next_cursor: Cursor | None

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        keys = [(item.workspace_id, item.project_id) for item in self.items]
        if (
            keys != sorted(set(keys))
            or self.after is not None
            and any(key <= self.after for key in keys)
            or self.next_cursor is not None
            and (not keys or self.next_cursor != keys[-1])
        ):
            raise ValueError("Deletion checkpoint order or continuation does not match.")
        if len(self.model_dump_json().encode()) > MAX_CHECKPOINT_PAGE_BYTES:
            raise ValueError("Deletion checkpoint page exceeds its byte budget.")
        return self


async def export_page(
    retention: PostgresRetention,
    conn: Connection,
    *,
    limit: int = 1000,
    after: Cursor | None = None,
) -> DeletionCheckpointPage:
    """Use one caller-owned repeatable-read snapshot for a complete multipage export."""
    _bound(limit)
    await require_owner(conn)
    if after is not None:
        TypeAdapter(Cursor).validate_python(after)
    where = "" if after is None else "WHERE (d.workspace_id,d.project_id)>(%s,%s) "
    params: tuple[object, ...] = (limit + 1,) if after is None else (*after, limit + 1)
    rows = await _rows(
        conn,
        "SELECT d.workspace_id,d.project_id,d.tombstone_id,d.revision,p.deleted_by,"
        "d.requested_at,d.eligible_at,d.project_days,d.time_basis,r.object_sha256,o.byte_size,o.media_type,o.schema_version "
        "FROM k13_deletions d JOIN k05_projects p ON p.workspace_id=d.workspace_id AND p.project_id=d.project_id "
        "JOIN k05_records r ON r.workspace_id=d.workspace_id AND r.project_id=d.project_id AND r.record_id=d.tombstone_id "
        "JOIN k05_objects o ON o.workspace_id=r.workspace_id AND o.sha256=r.object_sha256 "
        + where
        + "ORDER BY d.workspace_id,d.project_id LIMIT %s",
        params,
    )
    items: list[DeletionFence] = []
    size = 4096
    for row in rows[:limit]:
        data = await run_blocking(retention.objects.store.read, ObjectDescriptor(*row[9:13]))
        item = DeletionFence.model_validate(
            dict(
                zip(
                    (
                        "workspace_id",
                        "project_id",
                        "tombstone_id",
                        "revision",
                        "deleted_by",
                        "requested_at",
                        "eligible_at",
                        "project_days",
                        "time_basis",
                    ),
                    row[:9],
                    strict=True,
                ),
                record_json=data.decode("utf-8"),
            )
        )
        length = len(item.model_dump_json().encode()) + 1
        if items and size + length > MAX_CHECKPOINT_PAGE_BYTES:
            break
        items.append(item)
        size += length
    more = len(rows) > len(items)
    next_cursor = (items[-1].workspace_id, items[-1].project_id) if more and items else None
    return DeletionCheckpointPage(after=after, items=tuple(items), next_cursor=next_cursor)


async def apply_page(retention: PostgresRetention, page: DeletionCheckpointPage) -> int:
    """Apply authenticated history before grants/readiness; no public restore endpoint.

    Independent fences also cover projects created+deleted after an older backup,
    which cannot have an FK to that absent project or workspace yet.
    """
    page = DeletionCheckpointPage.model_validate_json(page.model_dump_json())
    applied = 0
    for item in page.items:
        identity = item.identity()
        async with retention.pool.connection() as conn:
            await require_owner(conn)
            await conn.execute(
                "INSERT INTO k13_restore_fences(workspace_id,project_id,tombstone_id,checkpoint_sha256) "
                "VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (item.workspace_id, item.project_id, item.tombstone_id, identity),
            )
            saved = await (
                await conn.execute(
                    "SELECT tombstone_id,checkpoint_sha256 FROM k13_restore_fences WHERE workspace_id=%s AND project_id=%s",
                    (item.workspace_id, item.project_id),
                )
            ).fetchone()
            if saved != (item.tombstone_id, identity):
                raise RetentionRejected()
            project = await (
                await conn.execute(
                    "SELECT project_id FROM k05_projects WHERE workspace_id=%s AND project_id=%s",
                    (item.workspace_id, item.project_id),
                )
            ).fetchone()
        if project is None:
            applied += 1
            continue
        record = ProductLifecycleRecord.model_validate_json(item.record_json)
        staged = await retention.objects.stage(
            item.workspace_id,
            item.record_json.encode(),
            media_type="application/json",
            schema_version=record.schema_version,
        )
        async with retention.pool.connection() as conn:
            await require_owner(conn)
            project = await (
                await conn.execute(
                    "SELECT revision,deletion_tombstone_id FROM k05_projects WHERE workspace_id=%s AND project_id=%s FOR UPDATE",
                    (item.workspace_id, item.project_id),
                )
            ).fetchone()
            if (
                project is None
                or type(project[0]) is not int
                or project[1] not in (None, item.tombstone_id)
                or project[1] is None
                and project[0] > item.revision
            ):
                raise RetentionRejected()
            await retention.objects.ready(conn, item.workspace_id, staged)
            kind, record_id, project_id, version, canonical = model_metadata(record)
            values: tuple[object, ...] = (
                item.workspace_id,
                record_id,
                project_id,
                kind,
                version,
                canonical,
                staged.descriptor.sha256,
            )
            await conn.execute(
                "INSERT INTO k05_records(workspace_id,record_id,project_id,record_type,schema_version,canonical_sha256,object_sha256) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                values,
            )
            saved_record = await (
                await conn.execute(
                    "SELECT workspace_id,record_id,project_id,record_type,schema_version,canonical_sha256,object_sha256 FROM k05_records "
                    "WHERE workspace_id=%s AND record_id=%s",
                    (item.workspace_id, record_id),
                )
            ).fetchone()
            if saved_record != values:
                raise RetentionRejected()
            if item.revision > 0 and item.deleted_by is not None:
                await conn.execute(
                    "UPDATE k05_projects SET revision=GREATEST(revision,%s),deletion_revision=%s,deletion_tombstone_id=%s,deleted_by=%s "
                    "WHERE workspace_id=%s AND project_id=%s",
                    (
                        item.revision,
                        item.revision,
                        item.tombstone_id,
                        item.deleted_by,
                        item.workspace_id,
                        item.project_id,
                    ),
                )
            values = (
                item.workspace_id,
                item.project_id,
                item.tombstone_id,
                item.revision,
                item.requested_at,
                item.eligible_at,
                item.project_days,
                item.time_basis,
            )
            await conn.execute(
                "INSERT INTO k13_deletions(workspace_id,project_id,tombstone_id,revision,requested_at,eligible_at,project_days,time_basis) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                values,
            )
            saved_deletion = await (
                await conn.execute(
                    "SELECT workspace_id,project_id,tombstone_id,revision,requested_at,eligible_at,project_days,time_basis "
                    "FROM k13_deletions WHERE workspace_id=%s AND project_id=%s",
                    (item.workspace_id, item.project_id),
                )
            ).fetchone()
            if saved_deletion != values:
                raise RetentionRejected()
            await retention.objects.finish(conn, item.workspace_id, staged)
        applied += 1
    return applied
