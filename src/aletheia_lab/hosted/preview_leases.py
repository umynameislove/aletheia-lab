"""Durable preview leases over the existing scoped source repository."""
from __future__ import annotations

import json
from uuid import uuid4

from pydantic import SecretStr

from aletheia_lab.hosted_auth.postgres_sessions import Connection, _digest
from aletheia_lab.hosted_contract.imports import PreviewImport

from .postgres_sources import PostgresSources, SourceNotFound
from .transactions import run_transaction


class PreviewExpired(ValueError):
    def __init__(self) -> None:
        super().__init__("The preview has expired. Create a new preview before confirming.")


class PreviewChanged(ValueError):
    def __init__(self) -> None:
        super().__init__("The source changed. Create a new preview before confirming.")


class PreviewLeases:
    def __init__(self, sources: PostgresSources, *, ttl_seconds: int = 900) -> None:
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError("A preview lifetime between one second and one hour is required.")
        self._sources, self._pool, self._ttl = sources, sources._pool, ttl_seconds

    async def is_ready(self) -> bool:
        from .preview_migration import verify

        try:
            async with self._pool.connection() as conn:
                return await verify(conn)
        except Exception:
            return False

    async def create(self, *, credential_digest: SecretStr, workspace_id: str, source_id: str,
                     preview_payload: dict[str, object]) -> PreviewImport:
        # Validate a worker's bounded projection before opening the transaction.
        raw = json.dumps(preview_payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        if len(raw.encode("utf-8")) > 1024 * 1024:
            raise ValueError("Preview exceeds the response budget.")
        preview_payload = json.loads(raw)
        if set(preview_payload) - {"source_sha256", "source_state_sha256", "mapping_candidates", "excluded_metrics",
                                   "excluded_metrics_total"}:
            raise ValueError("Invalid worker preview projection.")

        async def create(conn: Connection) -> PreviewImport:
            auth = await self._sources._access(conn, _digest(credential_digest), workspace_id, action="preview")
            await self._sources._authorization.authorize_source(conn, auth, workspace_id, source_id, "preview")
            source = await (await conn.execute("SELECT sha256 FROM j01_sources WHERE workspace_id=%s "
                "AND source_id=%s", (workspace_id, source_id))).fetchone()
            if source is None:
                raise SourceNotFound()
            if source[0] != preview_payload.get("source_sha256"):
                raise PreviewChanged()
            expiry = await (await conn.execute("SELECT clock_timestamp()+%s*interval '1 second'",
                                               (self._ttl,))).fetchone()
            if expiry is None:
                raise RuntimeError("Preview clock is unavailable.")
            preview = PreviewImport.model_validate({**preview_payload, "schema_version": "product-api/v1-draft",
                "preview_id": "prev_" + uuid4().hex, "workspace_id": workspace_id, "source_id": source_id,
                "excluded_metrics": preview_payload.get("excluded_metrics", []), "expires_at": expiry[0]})
            await conn.execute("INSERT INTO k07_import_previews(preview_id,workspace_id,source_id,creator_id,"
                "source_sha256,source_state_sha256,payload,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)",
                (preview.preview_id, workspace_id, source_id, auth.user_id, preview.source_sha256,
                 preview.source_state_sha256, preview.model_dump_json(), preview.expires_at))
            return preview

        return await run_transaction(self._pool, create)

    async def _read(self, *, credential_digest: SecretStr, workspace_id: str, preview_id: str,
                    source_id: str | None, confirming: bool, allow_stale: bool = False) -> PreviewImport:
        async def read(conn: Connection) -> PreviewImport:
            action = "confirm" if confirming else "preview"
            auth = await self._sources._access(conn, _digest(credential_digest), workspace_id, action=action)
            row = await (await conn.execute("SELECT p.creator_id,p.source_id,p.source_sha256,p.payload::text,"
                "p.expires_at>clock_timestamp(),s.sha256 FROM k07_import_previews p JOIN j01_sources s "
                "ON s.workspace_id=p.workspace_id AND s.source_id=p.source_id "
                "WHERE p.preview_id=%s AND p.workspace_id=%s FOR SHARE OF p,s",
                (preview_id, workspace_id))).fetchone()
            if row is None or not isinstance(row[1], str):
                raise SourceNotFound()
            await self._sources._authorization.authorize_source(conn, auth, workspace_id, row[1], action)
            if (confirming and row[0] != auth.user_id) or (
                    not confirming and row[0] != auth.user_id and auth.role not in ("owner", "admin")):
                raise SourceNotFound()
            if source_id is not None and source_id != row[1]:
                raise SourceNotFound()
            if not allow_stale and row[4] is not True:
                raise PreviewExpired()
            if not allow_stale and row[2] != row[5]:
                raise PreviewChanged()
            if not isinstance(row[3], str):
                raise RuntimeError("Preview integrity check failed.")
            preview = PreviewImport.model_validate_json(row[3])
            if (preview.preview_id != preview_id or preview.workspace_id != workspace_id
                    or preview.source_id != row[1] or preview.source_sha256 != row[2]):
                raise RuntimeError("Preview integrity check failed.")
            return preview

        return await run_transaction(self._pool, read)

    async def read(self, *, credential_digest: SecretStr, workspace_id: str,
                   preview_id: str) -> PreviewImport:
        return await self._read(credential_digest=credential_digest, workspace_id=workspace_id,
                                preview_id=preview_id, source_id=None, confirming=False)

    async def validate(self, *, credential_digest: SecretStr, workspace_id: str,
                       preview_id: str, source_id: str) -> PreviewImport:
        return await self._read(credential_digest=credential_digest, workspace_id=workspace_id,
                                preview_id=preview_id, source_id=source_id, confirming=True)

    async def for_submission(self, *, credential_digest: SecretStr, workspace_id: str,
                             preview_id: str) -> PreviewImport:
        """Resolve the original request for receipt replay; this never admits an effect."""
        return await self._read(credential_digest=credential_digest, workspace_id=workspace_id,
            preview_id=preview_id, source_id=None, confirming=True, allow_stale=True)

    async def cleanup_expired(self, *, grace_seconds: int = 3600, limit: int = 100) -> int:
        """Server maintenance only: retain every preview referenced by a durable confirm."""
        if (type(grace_seconds) is not int or not 1 <= grace_seconds <= 86400
                or type(limit) is not int or not 1 <= limit <= 1000):
            raise ValueError("Bounded preview cleanup and a positive grace period are required.")

        async def cleanup(conn: Connection) -> int:
            cursor = await conn.execute("WITH victims AS (SELECT p.preview_id FROM k07_import_previews p "
                "WHERE p.expires_at<clock_timestamp()-%s*interval '1 second' AND NOT EXISTS "
                "(SELECT 1 FROM j01_jobs j WHERE j.workspace_id=p.workspace_id AND j.operation='confirm' "
                "AND j.request->'mapping'->>'_preview_id'=p.preview_id) ORDER BY p.expires_at,p.preview_id "
                "LIMIT %s FOR UPDATE OF p SKIP LOCKED) DELETE FROM k07_import_previews p USING victims v "
                "WHERE p.preview_id=v.preview_id RETURNING p.preview_id", (grace_seconds, limit))
            removed = 0
            while await cursor.fetchone() is not None:
                removed += 1
            return removed

        return await run_transaction(self._pool, cleanup)
