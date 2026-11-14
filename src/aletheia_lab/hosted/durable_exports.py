"""Expiring report handles over the canonical catalog and managed objects."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast
from uuid import uuid4

from pydantic import SecretStr

from aletheia_lab.hosted.content_hashing import content_hasher
from aletheia_lab.hosted_auth.postgres_sessions import Connection, _digest

from .authorization import Authorization, AuthorizationDenied
from .object_store import ObjectDescriptor
from .postgres_catalog import PostgresCatalog
from .request_boundary import RequestRejected
from .worker_limits import MAX_OUTPUT_BYTES

ExportFormat = Literal["json", "markdown", "pdf"]
EXPORT_HANDLE = re.compile(r"ex_(json|markdown|pdf)_(p6-result-[0-9a-f]{64})_[0-9a-f]{32}")
EXPORT_MEDIA = {"json": "application/json", "markdown": "text/markdown; charset=utf-8", "pdf": "application/pdf"}
_COLUMNS = ("e.project_id,e.result_id,e.creator_id,e.format,e.object_sha256,"
            "o.byte_size,o.media_type,o.schema_version,e.expires_at,e.expires_at<=clock_timestamp()")


@dataclass(frozen=True)
class StoredExport:
    workspace_id: str
    export_id: str
    project_id: str
    result_id: str
    creator_id: str
    format: ExportFormat
    descriptor: ObjectDescriptor
    expires_at: datetime


def _stored(workspace: str, export_id: str, row: tuple[object, ...]) -> StoredExport:
    match = EXPORT_HANDLE.fullmatch(export_id)
    if (match is None or len(row) != 10 or any(not isinstance(row[i], str) for i in range(5))
            or type(row[5]) is not int or not 0 <= row[5] <= MAX_OUTPUT_BYTES
            or (row[1], row[3]) != (match[2], match[1])
            or not isinstance(row[8], datetime) or row[8].utcoffset() is None or type(row[9]) is not bool):
        raise ValueError("Stored export metadata failed its integrity check.")
    media = EXPORT_MEDIA[cast(str, row[3])]
    if row[6] != media or row[7] is not None:
        raise ValueError("Stored export media failed its integrity check.")
    return StoredExport(workspace, export_id, cast(str, row[0]), cast(str, row[1]), cast(str, row[2]),
        cast(ExportFormat, row[3]), ObjectDescriptor(cast(str, row[4]), row[5], media), row[8])


def _unexpired(row: tuple[object, ...]) -> None:
    if row[9] is True:
        raise RequestRejected(410, "export_expired", "The export has expired. Create a new export.")


class DurableExports:
    def __init__(self, catalog: PostgresCatalog) -> None:
        if catalog.objects.pool is not catalog.pool:
            raise ValueError("Exports must use the canonical catalog pool and objects.")
        self.catalog = catalog

    async def pdf_ready(self) -> bool:
        from aletheia_lab.product.pdf import pdf_available

        from .persistence_migration import PDF_EXPORT_DDL

        if not pdf_available():
            return False
        try:
            async with self.catalog.pool.connection() as conn:
                row = await (await conn.execute("SELECT checksum,EXISTS(SELECT 1 FROM pg_constraint "
                    "WHERE conrelid=to_regclass('k02_exports') AND conname='k12_export_format' AND convalidated) "
                    "FROM k05_persistence_migrations WHERE version=11")).fetchone()
            return row == (content_hasher(PDF_EXPORT_DDL.encode()).hexdigest(), True)
        except Exception:
            return False

    async def _authorize(self, conn: Connection, digest: SecretStr, workspace: str,
                         project: str, result: str) -> Authorization:
        auth = await self.catalog.policy.authorize(conn, _digest(digest), workspace, "export")
        kind = await self.catalog.policy.authorize_record(conn, auth, workspace, project, result, "export")
        if kind not in ("result", "upload_result"):
            raise AuthorizationDenied()
        return auth

    @staticmethod
    async def _row(conn: Connection, workspace: str, export_id: str) -> tuple[object, ...] | None:
        from psycopg import sql

        return await (await conn.execute(sql.SQL(
                                             'SELECT {} FROM k02_exports e JOIN k05_objects o ON o.workspace_id=e.workspace_id AND '
                                             'o.sha256=e.object_sha256 WHERE e.workspace_id=%s AND e.export_id=%s'
                                         ).format(sql.SQL(_COLUMNS)).as_string(), (workspace, export_id))).fetchone()

    async def _keyed(self, conn: Connection, workspace: str, creator: str, result: str,
                     report_format: ExportFormat, key_hash: str | None) -> StoredExport | None:
        if key_hash is None:
            return None
        value = await (await conn.execute("SELECT export_id FROM k02_exports WHERE workspace_id=%s "
            "AND creator_id=%s AND request_key_hash=%s",
            (workspace, creator, key_hash))).fetchone()
        if value is None:
            return None
        if len(value) != 1 or not isinstance(value[0], str):
            raise ValueError("Stored export handle failed its integrity check.")
        row = await self._row(conn, workspace, value[0])
        if row is None:
            raise ValueError("Stored export disappeared.")
        item = _stored(workspace, value[0], row)
        if item.creator_id != creator:
            raise ValueError("Stored export replay scope failed its integrity check.")
        if (item.result_id, item.format) != (result, report_format):
            raise RequestRejected(409, "idempotency_conflict", "The request key was already used.")
        _unexpired(row)
        return item

    async def find(self, digest: SecretStr, workspace: str, project: str, result: str,
                   report_format: ExportFormat, key_hash: str | None) -> StoredExport | None:
        async with self.catalog.pool.connection() as conn:
            auth = await self._authorize(conn, digest, workspace, project, result)
            item = await self._keyed(conn, workspace, auth.user_id, result, report_format, key_hash)
            if item is not None and item.project_id != project:
                raise ValueError("Stored export replay parent failed its integrity check.")
            return item

    async def publish(self, digest: SecretStr, workspace: str, project: str, result: str,
                      report_format: ExportFormat, payload: bytes, key_hash: str | None) -> StoredExport:
        if not isinstance(payload, bytes) or len(payload) > MAX_OUTPUT_BYTES:
            raise ValueError("Export bytes exceed their bound.")
        if report_format == "pdf" and not await self.pdf_ready():
            raise RequestRejected(503, "service_unavailable", "PDF export is currently unavailable.")
        if report_format not in EXPORT_MEDIA:
            raise ValueError("Unsupported export format.")
        media = EXPORT_MEDIA[report_format]
        staged = await self.catalog.objects.stage(workspace, payload, media_type=media)
        async with self.catalog.pool.connection() as conn:
            if conn.autocommit:
                raise RuntimeError("Transactional export publication is required.")
            auth = await self._authorize(conn, digest, workspace, project, result)
            if key_hash is not None:
                scope = json.dumps([workspace, auth.user_id, key_hash], separators=(",", ":"))
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended("
                    "current_schema()||':k02-export:'||%s,0))", (scope,))
            existing = await self._keyed(conn, workspace, auth.user_id, result, report_format, key_hash)
            if existing is not None:
                await self.catalog.objects.finish(conn, workspace, staged)
                return existing
            await self.catalog.objects.ready(conn, workspace, staged)
            sha = staged.descriptor.sha256
            await conn.execute("INSERT INTO k05_object_references(workspace_id,record_id,sha256) "
                "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (workspace, result, sha))
            export_id = f"ex_{report_format}_{result}_{uuid4().hex}"
            await conn.execute("INSERT INTO k02_exports(workspace_id,export_id,project_id,creator_id,result_id,"
                "format,object_sha256,request_key_hash,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,"
                "clock_timestamp()+interval '15 minutes')",
                (workspace, export_id, project, auth.user_id, result, report_format, sha, key_hash))
            row = await self._row(conn, workspace, export_id)
            if row is None:
                raise ValueError("Stored export metadata is unavailable.")
            item = _stored(workspace, export_id, row)
            await self.catalog.objects.finish(conn, workspace, staged)
            return item

    async def get(self, digest: SecretStr, workspace: str, export_id: str) -> StoredExport:
        if EXPORT_HANDLE.fullmatch(export_id) is None:
            raise AuthorizationDenied()
        async with self.catalog.pool.connection() as conn:
            auth = await self.catalog.policy.authorize(conn, _digest(digest), workspace, "export")
            row = await self._row(conn, workspace, export_id)
            if row is None:
                raise AuthorizationDenied()
            item = _stored(workspace, export_id, row)
            await self._authorize(conn, digest, workspace, item.project_id, item.result_id)
            if item.creator_id != auth.user_id:
                raise AuthorizationDenied()
            _unexpired(row)
            return item

    async def read(self, digest: SecretStr, workspace: str, export_id: str) -> tuple[StoredExport, bytes]:
        item = await self.get(digest, workspace, export_id)
        payload = await self.catalog.objects.read(workspace, item.descriptor)
        if (not isinstance(payload, bytes) or len(payload) != item.descriptor.byte_size
                or content_hasher(payload).hexdigest() != item.descriptor.sha256):
            raise ValueError("Stored export bytes failed their integrity check.")
        if await self.get(digest, workspace, export_id) != item:
            raise ValueError("Stored export changed during read.")
        return item, payload
