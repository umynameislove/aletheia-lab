"""Scoped source storage with current role and parent grants in each transaction."""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass
from functools import partial
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from pydantic import SecretStr

from aletheia_lab.hosted_auth.postgres_sessions import Connection, Pool, _digest
from aletheia_lab.project.identity import content_hasher

from .action_audit import record_action
from .async_work import run_blocking
from .authorization import Authorization, AuthorizationDenied, PostgresAuthorization
from .resource_admission import SOURCE_COMPLETION_BYTES_PER_PROCESS, ByteAdmission, ResourceBusy
from .source_bundle import SourceEntry, SourceRejected, inspect_source_bundle
from .transactions import run_transaction
from .upload_snapshot import (
    AnyUploadSnapshot,
    UploadSnapshot,
    UploadSnapshotV2,
    parse_upload_snapshot,
)
from .upload_staging import (
    MAX_PART_BYTES,
    MAX_PARTS,
    MAX_UPLOAD_BYTES,
    UploadManifest,
    UploadRecord,
    UploadRejected,
    UploadStatus,
    _ValidatedUpload,
)

if TYPE_CHECKING:
    from .postgres_catalog import PostgresCatalog

# Reserve both the received chunks and their joined copy before fetching bytes.
# Expanded archive/worker RSS limits remain separate; this is not an RSS cap.
_COMPLETION_BYTES = ByteAdmission(SOURCE_COMPLETION_BYTES_PER_PROCESS)


def _verified_upload_bytes(chunks: list[bytes], manifest: UploadManifest) -> bytes:
    for chunk, expected in zip(chunks, manifest.parts, strict=True):
        if len(chunk) != expected.byte_size or content_hasher(chunk).hexdigest() != expected.sha256:
            raise UploadRejected('upload_integrity_failed', 422, 'The upload integrity check failed.')
    payload = b''.join(chunks)
    if len(payload) != manifest.byte_size or content_hasher(payload).hexdigest() != manifest.sha256:
        raise UploadRejected('upload_integrity_failed', 422, 'The upload integrity check failed.')
    return payload

SourceNotFound = AuthorizationDenied


class SourceConflict(ValueError):
    def __init__(self) -> None:
        super().__init__("Source request key was already used for different content.")


@dataclass(frozen=True)
class SourceReceipt:
    source_id: str
    workspace_id: str
    sha256: str


class _SourceCatalogPolicy(PostgresAuthorization):
    def __init__(self, sources: PostgresSources) -> None:
        self._sources = sources

    async def authorize(self, conn: Connection, digest: str, workspace_id: str, action: str) -> Authorization:
        return await self._sources._access(conn, digest, workspace_id, action=action)


class PostgresSources:
    def __init__(self, pool: Pool, *, maximum_upload_bytes: int, maximum_expanded_bytes: int,
                 maximum_entries: int, audit: bool = False, staging_ttl_seconds: int = 86400,
                 maximum_workspace_bytes: int = MAX_UPLOAD_BYTES * 8, maximum_actor_uploads: int = 4,
                 maximum_workspace_uploads: int = 16, catalog: PostgresCatalog | None = None) -> None:
        if any(type(value) is not int or value <= 0 for value in
               (maximum_upload_bytes, maximum_expanded_bytes, maximum_entries)):
            raise ValueError("positive source budgets required")
        if (any(type(value) is not int or value <= 0 for value in
                (staging_ttl_seconds, maximum_workspace_bytes, maximum_actor_uploads, maximum_workspace_uploads))
                or staging_ttl_seconds > 604800 or maximum_actor_uploads > maximum_workspace_uploads):
            raise ValueError("bounded upload staging budgets required")
        self._staging_ttl_seconds = staging_ttl_seconds
        self._maximum_workspace_bytes = maximum_workspace_bytes
        self._maximum_actor_uploads, self._maximum_workspace_uploads = maximum_actor_uploads, maximum_workspace_uploads
        self._pool, self._audit = pool, audit
        self._parse_future: asyncio.Future[tuple[SourceEntry, ...]] | None = None
        self._parse_owner: asyncio.Task[object] | None = None
        self._authorization = PostgresAuthorization()
        self._catalog: PostgresCatalog | None = None
        if catalog is not None:
            from .postgres_catalog import PostgresCatalog

            self._catalog = PostgresCatalog(pool, catalog.objects, policy=_SourceCatalogPolicy(self))
        self._budgets = dict(maximum_upload_bytes=maximum_upload_bytes,
                             maximum_expanded_bytes=maximum_expanded_bytes, maximum_entries=maximum_entries)

    async def _inspect(self, payload: bytes) -> tuple[SourceEntry, ...]:
        if self._parse_future is not None and not self._parse_future.done():
            raise TimeoutError("Source validation is temporarily busy.")
        # Keep ownership until the thread ends; cancellation only detaches its waiter.
        future = asyncio.create_task(run_blocking(partial(inspect_source_bundle, payload, **self._budgets)))
        self._parse_future = future
        self._parse_owner = asyncio.current_task()

        def finished(completed: asyncio.Future[tuple[SourceEntry, ...]]) -> None:
            if not completed.cancelled():
                completed.exception()
            if self._parse_future is completed:
                self._parse_future = None
                self._parse_owner = None

        future.add_done_callback(finished)
        return await asyncio.shield(future)

    @property
    def maximum_upload_bytes(self) -> int:
        return self._budgets["maximum_upload_bytes"]

    @property
    def maximum_workspace_bytes(self) -> int:
        return self._maximum_workspace_bytes

    async def _workspace_limits(self, conn: Connection, workspace: str) -> tuple[int, int]:
        row = await (await conn.execute("SELECT (to_jsonb(w)->>'maximum_upload_bytes')::bigint,"
            "(to_jsonb(w)->>'maximum_workspace_bytes')::bigint FROM j01_workspaces w "
            "WHERE workspace_id=%s FOR SHARE", (workspace,))).fetchone()
        if row is None or len(row) != 2 or any(value is not None and (type(value) is not int or value <= 0) for value in row):
            raise RuntimeError("Workspace upload policy is unavailable.")
        upload = self.maximum_upload_bytes if row[0] is None else min(self.maximum_upload_bytes, cast(int, row[0]))
        workspace_limit = self._maximum_workspace_bytes if row[1] is None else min(self._maximum_workspace_bytes, cast(int, row[1]))
        return min(upload, workspace_limit), workspace_limit

    async def _admit_source_bytes(self, conn: Connection, workspace: str, incoming: int) -> None:
        upload_limit, workspace_limit = await self._workspace_limits(conn, workspace)
        if incoming > upload_limit:
            raise UploadRejected("upload_too_large", 413, "The source exceeds the configured upload limit.")
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("aletheia-k07-quota:" + workspace,))
        stored = await (await conn.execute("SELECT COALESCE(sum(octet_length(payload)),0) FROM j01_sources "
                                           "WHERE workspace_id=%s", (workspace,))).fetchone()
        reserved = 0
        relation = await (await conn.execute("SELECT to_regclass('k07_uploads')")).fetchone()
        if relation is not None and relation[0] is not None:
            held = await (await conn.execute("SELECT COALESCE(sum(byte_size),0)::bigint FROM k07_uploads "
                "WHERE workspace_id=%s AND cleaned_at IS NULL", (workspace,))).fetchone()
            if held is None or type(held[0]) is not int:
                raise RuntimeError("Upload quota is unavailable.")
            reserved = held[0]
        if stored is None or type(stored[0]) is not int:
            raise RuntimeError("Upload quota is unavailable.")
        if stored[0] + reserved + incoming > workspace_limit:
            raise UploadRejected("upload_quota_exceeded", 413, "The workspace upload quota is exhausted.")

    async def _access(self, conn: Connection, digest: str, workspace_id: str, *, action: str) -> Authorization:
        return await self._authorization.authorize(conn, digest, workspace_id, action)

    async def put(self, *, credential_digest: SecretStr, workspace_id: str, request_key: SecretStr,
                  payload: bytes) -> SourceReceipt:
        digest = _digest(credential_digest)
        if not isinstance(request_key, SecretStr) or not 16 <= len(request_key.get_secret_value()) <= 128:
            raise ValueError("bounded source request key required")
        async with self._pool.connection() as conn:
            await self._access(conn, digest, workspace_id, action="import")
        entries = await self._inspect(payload)
        content_hash = await run_blocking(lambda: content_hasher(payload).hexdigest())
        key_hash = content_hasher(request_key.get_secret_value().encode("utf-8")).hexdigest()
        async def admit(conn: Connection) -> SourceReceipt:
            auth = await self._access(conn, digest, workspace_id, action="import")
            # A replay must observe the winner before charging incoming bytes again.
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                               ("aletheia-k07-quota:" + workspace_id,))
            previous = await (await conn.execute("SELECT source_id FROM j01_sources WHERE workspace_id=%s "
                "AND creator_id=%s AND request_key_hash=%s", (workspace_id, auth.user_id, key_hash))).fetchone()
            if previous is None:
                await self._admit_source_bytes(conn, workspace_id, len(payload))
            source_id = "src_" + uuid4().hex
            manifest = json.dumps([asdict(entry) for entry in entries], ensure_ascii=True)
            inserted = await (await conn.execute("INSERT INTO j01_sources(source_id,workspace_id,creator_id,request_key_hash,sha256,payload,manifest) "
                               "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb) "
                               "ON CONFLICT (workspace_id,creator_id,request_key_hash) DO NOTHING RETURNING source_id",
                               (source_id, workspace_id, auth.user_id, key_hash, content_hash, payload, manifest))).fetchone()
            existing = await (await conn.execute("SELECT source_id,sha256 FROM j01_sources WHERE workspace_id=%s "
                "AND creator_id=%s AND request_key_hash=%s", (workspace_id, auth.user_id, key_hash))).fetchone()
            if existing is None or not isinstance(existing[0], str):
                raise RuntimeError("Invalid source record")
            await self._authorization.authorize_source(conn, auth, workspace_id, existing[0], "import")
            if existing[1] != content_hash:
                raise SourceConflict()
            if self._audit and inserted is not None:
                await record_action(conn, actor_id=auth.user_id, workspace_id=workspace_id, action="source.put",
                                    effect_id=existing[0], operation_id=existing[0])
            return SourceReceipt(existing[0], workspace_id, content_hash)

        return await run_transaction(self._pool, admit)

    async def read(self, *, credential_digest: SecretStr, workspace_id: str, source_id: str) -> bytes:
        digest = _digest(credential_digest)
        async with self._pool.connection() as conn:
            auth = await self._access(conn, digest, workspace_id, action="read")
            await self._authorization.authorize_source(conn, auth, workspace_id, source_id, "read")
            row = await (await conn.execute("SELECT payload,sha256 FROM j01_sources WHERE source_id=%s AND workspace_id=%s",
                                            (source_id, workspace_id), binary=True)).fetchone()
            if row is None:
                raise SourceNotFound()
        payload, expected_hash = row
        if not isinstance(payload, bytes) or await run_blocking(lambda: content_hasher(payload).hexdigest()) != expected_hash:
            raise RuntimeError("Source integrity check failed")
        return payload

    async def publish_snapshot(self, *, credential_digest: SecretStr, snapshot: AnyUploadSnapshot) -> AnyUploadSnapshot:
        """Trusted worker output only; recheck current confirm and source grant."""
        digest = _digest(credential_digest)
        snapshot = parse_upload_snapshot(snapshot.model_dump(mode="python"))
        async def publish(conn: Connection) -> AnyUploadSnapshot:
            auth = await self._access(conn, digest, snapshot.workspace_id, action="confirm")
            await self._authorization.authorize_source(conn, auth, snapshot.workspace_id, snapshot.source_id, "confirm")
            row = await (await conn.execute("SELECT sha256 FROM j01_sources WHERE source_id=%s AND workspace_id=%s",
                (snapshot.source_id, snapshot.workspace_id))).fetchone()
            if row is None or row[0] != snapshot.source_sha256:
                raise SourceNotFound()
            inserted = await (await conn.execute("INSERT INTO j01_upload_snapshots(snapshot_id,workspace_id,source_id,payload) "
                "VALUES (%s,%s,%s,%s::jsonb) ON CONFLICT (snapshot_id) DO NOTHING RETURNING snapshot_id", (snapshot.snapshot_id, snapshot.workspace_id,
                                                snapshot.source_id, snapshot.model_dump_json()))).fetchone()
            saved = await (await conn.execute("SELECT payload::text FROM j01_upload_snapshots WHERE snapshot_id=%s "
                "AND workspace_id=%s AND source_id=%s", (snapshot.snapshot_id, snapshot.workspace_id,
                                                          snapshot.source_id))).fetchone()
            if saved is None or not isinstance(saved[0], str):
                raise RuntimeError("Invalid snapshot record")
            original = parse_upload_snapshot(saved[0])
            if original.state_payload() != snapshot.state_payload():
                raise SourceConflict()
            if self._audit and inserted is not None:
                await record_action(conn, actor_id=auth.user_id, workspace_id=snapshot.workspace_id,
                                    action="snapshot.publish", effect_id=snapshot.snapshot_id, operation_id=snapshot.snapshot_id)
            return original

        return await run_transaction(self._pool, publish)

    async def snapshot(self, *, credential_digest: SecretStr, workspace_id: str, snapshot_id: str) -> AnyUploadSnapshot:
        from .postgres_catalog import CatalogIntegrityError

        digest = _digest(credential_digest)
        async with self._pool.connection() as conn:
            auth = await self._access(conn, digest, workspace_id, action="read")
            row = await (await conn.execute("SELECT source_id,payload::text,"
                "to_jsonb(j01_upload_snapshots)->>'canonical_record_id' FROM j01_upload_snapshots "
                "WHERE snapshot_id=%s AND workspace_id=%s", (snapshot_id, workspace_id))).fetchone()
            if row is None or not isinstance(row[0], str):
                raise SourceNotFound()
            await self._authorization.authorize_source(conn, auth, workspace_id, row[0], "read")
            source = await (await conn.execute("SELECT sha256 FROM j01_sources WHERE source_id=%s AND workspace_id=%s",
                                               (row[0], workspace_id))).fetchone()
            if len(row) != 3 or not isinstance(row[1], str) or row[2] is not None and not isinstance(row[2], str):
                raise CatalogIntegrityError()
            if row[2] is None:
                relation = await (await conn.execute("SELECT to_regclass('k05_records')")).fetchone()
                if relation is not None and relation[0] is not None:
                    shadow = await (await conn.execute("SELECT 1 FROM k05_records WHERE workspace_id=%s AND record_id=%s",
                                                       (workspace_id, snapshot_id))).fetchone()
                    if shadow is not None:
                        raise CatalogIntegrityError()
        # Decode/hash canonical or legacy bytes only after releasing the read connection.
        result = await run_blocking(parse_upload_snapshot, row[1])
        if ((result.workspace_id, result.snapshot_id, result.source_id) != (workspace_id, snapshot_id, row[0])
                or source != (result.source_sha256,)):
            raise CatalogIntegrityError()
        canonical_id = row[2]
        result = await self._canonical_snapshot(credential_digest, result, canonical_id)
        async with self._pool.connection() as conn:
            auth = await self._access(conn, digest, workspace_id, action="read")
            await self._authorization.authorize_source(conn, auth, workspace_id, result.source_id, "read")
            current = await (await conn.execute("SELECT source_id,to_jsonb(j01_upload_snapshots)->>'canonical_record_id' "
                "FROM j01_upload_snapshots WHERE snapshot_id=%s AND workspace_id=%s", (snapshot_id, workspace_id))).fetchone()
            if current != (result.source_id, canonical_id):
                raise SourceNotFound()
        return result

    async def _canonical_snapshot(self, credential_digest: SecretStr, result: AnyUploadSnapshot,
                                  canonical_id: str | None) -> AnyUploadSnapshot:
        from .postgres_catalog import CatalogIntegrityError

        if canonical_id is None:
            return result
        if self._catalog is None:
            raise TimeoutError("Canonical snapshot storage is unavailable.")
        if canonical_id != result.snapshot_id:
            raise CatalogIntegrityError()
        canonical = await self._catalog.read_record(credential_digest=credential_digest,
            workspace_id=result.workspace_id, project_id=result.mapping.project_id, record_id=canonical_id)
        if (not isinstance(canonical, (UploadSnapshot, UploadSnapshotV2))
                or not await run_blocking(lambda: canonical == result)):
            raise CatalogIntegrityError()
        return canonical

    async def is_upload_ready(self) -> bool:
        from .upload_staging_migration import verify

        try:
            async with self._pool.connection() as conn:
                row = await (await conn.execute("SELECT current_schema()")).fetchone()
                return row is not None and isinstance(row[0], str) and await verify(conn, row[0])
        except Exception:
            return False

    async def _upload_record(self, conn: Connection, digest: str, workspace_id: str, upload_id: str,
                             *, cancel: bool = False) -> tuple[Authorization, UploadRecord]:
        auth = await self._access(conn, digest, workspace_id, action="import")
        row = await (await conn.execute("SELECT creator_id,state,manifest::text,expires_at,source_id,"
            "expires_at<=clock_timestamp(),cleaned_at IS NOT NULL FROM k07_uploads "
            "WHERE workspace_id=%s AND upload_id=%s FOR UPDATE", (workspace_id, upload_id))).fetchone()
        if row is None or (row[0] != auth.user_id and not (cancel and auth.role in ("owner", "admin"))):
            raise SourceNotFound()
        record = UploadRecord.from_row(row)
        if record.manifest.parent_source_id is not None:
            await self._authorization.authorize_source(conn, auth, workspace_id,
                                                       record.manifest.parent_source_id, "import")
        return auth, record

    @staticmethod
    def _upload_writable(record: UploadRecord) -> None:
        if record.expired or record.state == "expired":
            raise UploadRejected("upload_expired", 410, "The upload lease has expired.")
        if record.state != "active":
            raise UploadRejected("upload_conflict", 409, "The upload cannot be changed in its current state.")

    @staticmethod
    async def _upload_status(conn: Connection, workspace_id: str, upload_id: str,
                             record: UploadRecord) -> UploadStatus:
        rows = await conn.execute("SELECT part_number,byte_size FROM k07_upload_parts "
                                  "WHERE workspace_id=%s AND upload_id=%s ORDER BY part_number",
                                  (workspace_id, upload_id))
        received, size = [], 0
        while (part := await rows.fetchone()) is not None:
            if type(part[0]) is not int or type(part[1]) is not int:
                raise RuntimeError("Invalid confirmed upload progress")
            received.append(part[0])
            size += part[1]
        if record.state == "ready":
            received = [part.part_number for part in record.manifest.parts]
            size = record.manifest.byte_size
        return UploadStatus(workspace_id=workspace_id, upload_id=upload_id,
            state="expired" if record.state == "active" and record.expired else record.state,
            byte_size=record.manifest.byte_size, sha256=record.manifest.sha256, expires_at=record.expires_at,
            received_bytes=size, received_parts=received,
            missing_parts=[part.part_number for part in record.manifest.parts if part.part_number not in received],
            source_id=record.source_id)

    async def create_upload(self, *, credential_digest: SecretStr, workspace_id: str,
                            request_key: SecretStr, manifest: UploadManifest) -> UploadStatus:
        manifest = UploadManifest.model_validate(manifest.model_dump(mode="python"))
        if manifest.byte_size > self.maximum_upload_bytes:
            raise UploadRejected("upload_too_large", 413, "The source exceeds the configured upload limit.")
        key = request_key.get_secret_value()
        if re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key) is None:
            raise ValueError("A bounded upload request key is required.")
        digest = _digest(credential_digest)
        encoded = manifest.model_dump_json()
        request_hash = content_hasher(encoded.encode()).hexdigest()
        key_hash = content_hasher(key.encode()).hexdigest()

        async def create(conn: Connection) -> UploadStatus:
            auth = await self._access(conn, digest, workspace_id, action="import")
            if manifest.parent_source_id is not None:
                await self._authorization.authorize_source(conn, auth, workspace_id, manifest.parent_source_id, "import")
            # Serialize reservation across creators before checking the workspace aggregate.
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                               ("aletheia-k07-quota:" + workspace_id,))
            previous = await (await conn.execute("SELECT upload_id,request_hash FROM k07_uploads "
                "WHERE workspace_id=%s AND creator_id=%s AND key_hash=%s", (workspace_id, auth.user_id, key_hash))).fetchone()
            if previous is not None:
                if previous[1] != request_hash:
                    raise SourceConflict()
                upload_id = str(previous[0])
                _, record = await self._upload_record(conn, digest, workspace_id, upload_id)
                return await self._upload_status(conn, workspace_id, upload_id, record)
            upload_limit, workspace_limit = await self._workspace_limits(conn, workspace_id)
            if manifest.byte_size > upload_limit:
                raise UploadRejected("upload_too_large", 413, "The source exceeds the configured upload limit.")
            await self._cleanup_uploads(conn, limit=100, grace_seconds=300, workspace_id=workspace_id)
            quota = await (await conn.execute("SELECT COALESCE(sum(byte_size) FILTER(WHERE cleaned_at IS NULL),0)::bigint,"
                "count(*) FILTER(WHERE state='active' AND expires_at>clock_timestamp()),"
                "count(*) FILTER(WHERE state='active' AND expires_at>clock_timestamp() AND creator_id=%s) "
                "FROM k07_uploads WHERE workspace_id=%s", (auth.user_id, workspace_id))).fetchone()
            stored = await (await conn.execute("SELECT COALESCE(sum(octet_length(payload)),0) FROM j01_sources "
                                               "WHERE workspace_id=%s", (workspace_id,))).fetchone()
            if (quota is None or stored is None or len(quota) != 3 or len(stored) != 1
                    or any(type(value) is not int for value in (*quota, *stored))):
                raise RuntimeError("Upload quota is unavailable")
            reserved, active, actor_active = cast(tuple[int, int, int], quota)
            source_bytes = cast(int, stored[0])
            if (reserved + source_bytes + manifest.byte_size > workspace_limit
                    or active >= self._maximum_workspace_uploads
                    or actor_active >= self._maximum_actor_uploads):
                raise UploadRejected("upload_quota_exceeded", 413, "The workspace upload quota is exhausted.")
            upload_id = "upl_" + uuid4().hex
            await conn.execute("INSERT INTO k07_uploads(upload_id,workspace_id,creator_id,key_hash,request_hash,"
                "manifest,byte_size,sha256,parent_source_id,expires_at) VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,"
                "clock_timestamp()+%s*interval '1 second')", (upload_id, workspace_id, auth.user_id,
                key_hash, request_hash, encoded, manifest.byte_size, manifest.sha256, manifest.parent_source_id,
                self._staging_ttl_seconds))
            _, record = await self._upload_record(conn, digest, workspace_id, upload_id)
            return await self._upload_status(conn, workspace_id, upload_id, record)

        return await run_transaction(self._pool, create)

    async def upload_status(self, *, credential_digest: SecretStr, workspace_id: str,
                            upload_id: str) -> UploadStatus:
        async with self._pool.connection() as conn:
            _, record = await self._upload_record(conn, _digest(credential_digest), workspace_id, upload_id)
            return await self._upload_status(conn, workspace_id, upload_id, record)

    async def put_upload_part(self, *, credential_digest: SecretStr, workspace_id: str,
                              upload_id: str, part_number: int, payload: bytes) -> UploadStatus:
        if type(part_number) is not int or not 1 <= part_number <= MAX_PARTS:
            raise UploadRejected("upload_part_invalid", 422, "The upload part is outside the manifest.")
        if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_PART_BYTES:
            raise UploadRejected("upload_part_invalid", 422, "The upload part length does not match the manifest.")
        content_hash = await run_blocking(lambda: content_hasher(payload).hexdigest())

        async def put_part(conn: Connection) -> UploadStatus:
            _, record = await self._upload_record(conn, _digest(credential_digest), workspace_id, upload_id)
            if part_number > len(record.manifest.parts):
                raise UploadRejected("upload_part_invalid", 422, "The upload part is outside the manifest.")
            expected = record.manifest.parts[part_number - 1]
            if len(payload) != expected.byte_size or content_hash != expected.sha256:
                raise UploadRejected("upload_part_conflict", 409, "The upload part does not match its sealed manifest.")
            if record.state == "ready":
                return await self._upload_status(conn, workspace_id, upload_id, record)
            self._upload_writable(record)
            # The upload-row lock serializes writers; reconcile a committed part
            # before sending its potentially 4 MiB payload to PostgreSQL again.
            saved = await (await conn.execute("SELECT sha256,byte_size FROM k07_upload_parts WHERE workspace_id=%s "
                "AND upload_id=%s AND part_number=%s", (workspace_id, upload_id, part_number))).fetchone()
            if saved is not None:
                if saved != (content_hash, len(payload)):
                    raise UploadRejected("upload_part_conflict", 409, "The upload part does not match its sealed manifest.")
                return await self._upload_status(conn, workspace_id, upload_id, record)
            await conn.execute("INSERT INTO k07_upload_parts(workspace_id,upload_id,part_number,byte_size,sha256,payload) "
                "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(workspace_id,upload_id,part_number) DO NOTHING",
                (workspace_id, upload_id, part_number, len(payload), content_hash, payload))
            saved = await (await conn.execute("SELECT sha256,byte_size FROM k07_upload_parts WHERE workspace_id=%s "
                "AND upload_id=%s AND part_number=%s", (workspace_id, upload_id, part_number))).fetchone()
            if saved != (content_hash, len(payload)):
                raise UploadRejected("upload_part_conflict", 409, "The upload part does not match its sealed manifest.")
            return await self._upload_status(conn, workspace_id, upload_id, record)

        return await run_transaction(self._pool, put_part)

    async def complete_upload(self, *, credential_digest: SecretStr, workspace_id: str,
                              upload_id: str) -> UploadStatus:
        try:
            return await self._complete_upload(credential_digest=credential_digest, workspace_id=workspace_id,
                                               upload_id=upload_id)
        except (SourceRejected, UploadRejected) as error:
            if isinstance(error, SourceRejected) or error.code == "upload_integrity_failed":
                async def invalidate(conn: Connection) -> None:
                    # The previously authorized sealed bytes are irreparable; retain the terminal receipt.
                    await conn.execute("UPDATE k07_uploads SET state='cancelled',terminal_at=clock_timestamp() "
                        "WHERE workspace_id=%s AND upload_id=%s AND state='active' AND source_id IS NULL",
                        (workspace_id, upload_id))
                await run_transaction(self._pool, invalidate)
            raise

    async def _assemble_upload(self, digest: str, workspace_id: str, upload_id: str) -> tuple[UploadRecord, bytes]:
        chunks: list[bytes] = []
        try:
            async with self._pool.connection() as conn:
                _, record = await self._upload_record(conn, digest, workspace_id, upload_id)
                self._upload_writable(record)
                if self._parse_future is not None and not self._parse_future.done():
                    raise TimeoutError("Source validation is temporarily busy.")
                cursor = await conn.execute("SELECT part_number,byte_size,sha256,payload FROM k07_upload_parts "
                    "WHERE workspace_id=%s AND upload_id=%s ORDER BY part_number", (workspace_id, upload_id), binary=True)
                for expected in record.manifest.parts:
                    row = await cursor.fetchone()
                    if row is None or row[:3] != (expected.part_number, expected.byte_size, expected.sha256):
                        raise UploadRejected("upload_incomplete", 409, "The upload is missing one or more verified parts.")
                    chunk = row[3]
                    if not isinstance(chunk, bytes) or len(chunk) != expected.byte_size:
                        raise UploadRejected("upload_integrity_failed", 422, "The upload integrity check failed.")
                    chunks.append(chunk)
            # Hash/copy the large input after releasing the connection. The
            # final publication transaction still verifies actual PG bytes again.
            payload = await run_blocking(_verified_upload_bytes, chunks, record.manifest)
            return record, payload
        finally:
            chunks.clear()

    async def _complete_upload(self, *, credential_digest: SecretStr, workspace_id: str,
                               upload_id: str) -> UploadStatus:
        digest = _digest(credential_digest)
        async with self._pool.connection() as conn:
            _, current = await self._upload_record(conn, digest, workspace_id, upload_id)
            if current.state == 'ready':
                return await self._upload_status(conn, workspace_id, upload_id, current)
            weight = 2 * current.manifest.byte_size
        try:
            _COMPLETION_BYTES.acquire(weight)
        except ResourceBusy:
            # A winner may have committed after the first metadata read. Ready
            # replay/reconciliation remains available even under materialization load.
            async with self._pool.connection() as conn:
                _, current = await self._upload_record(conn, digest, workspace_id, upload_id)
                if current.state == 'ready':
                    return await self._upload_status(conn, workspace_id, upload_id, current)
            raise
        try:
            return await self._complete_upload_reserved(credential_digest=credential_digest,
                workspace_id=workspace_id, upload_id=upload_id)
        except asyncio.CancelledError:
            # Do not release the byte reservation while this caller's inspection
            # still holds the large assembled payload, including queued inspection.
            if self._parse_owner is asyncio.current_task() and self._parse_future is not None:
                future = self._parse_future
                future.cancel()
                cleanup = asyncio.gather(future, return_exceptions=True)
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        continue
            raise
        finally:
            _COMPLETION_BYTES.release(weight)

    async def _complete_upload_reserved(self, *, credential_digest: SecretStr, workspace_id: str,
                                       upload_id: str) -> UploadStatus:
        digest = _digest(credential_digest)
        async with self._pool.connection() as conn:
            _, current = await self._upload_record(conn, digest, workspace_id, upload_id)
            if current.state == "ready":
                return await self._upload_status(conn, workspace_id, upload_id, current)
        record, payload = await self._assemble_upload(digest, workspace_id, upload_id)
        try:
            entries = await self._inspect(payload)
        finally:
            del payload
        validated = _ValidatedUpload(workspace_id, upload_id, record.manifest.byte_size,
                                     record.manifest.sha256, len(record.manifest.parts))

        async def complete(conn: Connection) -> UploadStatus:
            await self._access(conn, digest, workspace_id, action="import")
            # Reservation-to-source conversion shares admission's lock and order.
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                               ("aletheia-k07-quota:" + workspace_id,))
            auth, current = await self._upload_record(conn, digest, workspace_id, upload_id)
            if current.state == "ready":
                return await self._upload_status(conn, workspace_id, upload_id, current)
            self._upload_writable(current)
            if current.manifest != record.manifest:
                raise UploadRejected("upload_conflict", 409, "The upload manifest has changed.")
            source_id = "src_" + uuid4().hex
            # Parts are already durable. Re-sending the full bundle can strand
            # a large libpq flush at the deadline, especially over a VM bridge.
            # Recheck actual ordered bytes here, under the upload lock, so a
            # changed/missing part after inspection cannot become a source.
            inserted = await (await conn.execute("WITH assembled AS MATERIALIZED ("
                "SELECT count(*) AS part_count,pg_catalog.string_agg(payload,''::bytea ORDER BY part_number) AS payload "
                "FROM k07_upload_parts WHERE workspace_id=%s AND upload_id=%s) "
                "INSERT INTO j01_sources(source_id,workspace_id,creator_id,request_key_hash,sha256,payload,manifest) "
                "SELECT %s,%s,%s,%s,%s,payload,%s::jsonb FROM assembled "
                "WHERE part_count=%s AND pg_catalog.octet_length(payload)=%s "
                "AND pg_catalog.encode(pg_catalog.sha256(payload),'hex')=%s RETURNING source_id",
                (validated.workspace_id, validated.upload_id, source_id, workspace_id, auth.user_id,
                 content_hasher(("k07:" + upload_id).encode()).hexdigest(), validated.sha256,
                 json.dumps([asdict(entry) for entry in entries], ensure_ascii=True),
                 validated.part_count, validated.byte_size, validated.sha256))).fetchone()
            if inserted != (source_id,):
                raise UploadRejected("upload_integrity_failed", 422, "The upload integrity check failed.")
            await conn.execute("UPDATE k07_uploads SET state='ready',source_id=%s,terminal_at=clock_timestamp(),"
                "cleaned_at=clock_timestamp() WHERE workspace_id=%s AND upload_id=%s", (source_id, workspace_id, upload_id))
            await conn.execute("DELETE FROM k07_upload_parts WHERE workspace_id=%s AND upload_id=%s", (workspace_id, upload_id))
            if self._audit:
                await record_action(conn, actor_id=auth.user_id, workspace_id=workspace_id, action="source.put",
                                    effect_id=source_id, operation_id=upload_id)
            _, saved = await self._upload_record(conn, digest, workspace_id, upload_id)
            return await self._upload_status(conn, workspace_id, upload_id, saved)

        return await run_transaction(self._pool, complete, timeout_seconds=60.0)

    async def cancel_upload(self, *, credential_digest: SecretStr, workspace_id: str,
                            upload_id: str) -> UploadStatus:
        async def cancel(conn: Connection) -> UploadStatus:
            _, record = await self._upload_record(conn, _digest(credential_digest), workspace_id, upload_id, cancel=True)
            if record.state == "active":
                await conn.execute("UPDATE k07_uploads SET state=%s,terminal_at=clock_timestamp() "
                    "WHERE workspace_id=%s AND upload_id=%s", ("expired" if record.expired else "cancelled", workspace_id, upload_id))
            _, saved = await self._upload_record(conn, _digest(credential_digest), workspace_id, upload_id, cancel=True)
            return await self._upload_status(conn, workspace_id, upload_id, saved)

        return await run_transaction(self._pool, cancel)

    @staticmethod
    async def _cleanup_uploads(conn: Connection, *, limit: int, grace_seconds: int,
                               workspace_id: str | None = None) -> int:
        rows = await conn.execute("SELECT workspace_id,upload_id FROM k07_uploads WHERE cleaned_at IS NULL "
            "AND source_id IS NULL AND (%s::text IS NULL OR workspace_id=%s) AND "
            "((state='active' AND expires_at<clock_timestamp()-%s*interval '1 second') OR "
            "(state IN ('cancelled','expired') AND terminal_at<clock_timestamp()-%s*interval '1 second')) "
            "ORDER BY expires_at,upload_id LIMIT %s FOR UPDATE SKIP LOCKED",
            (workspace_id, workspace_id, grace_seconds, grace_seconds, limit))
        keys = []
        while (row := await rows.fetchone()) is not None:
            keys.append((row[0], row[1]))
        for workspace, upload_id in keys:
            await conn.execute("DELETE FROM k07_upload_parts WHERE workspace_id=%s AND upload_id=%s", (workspace, upload_id))
            await conn.execute("UPDATE k07_uploads SET state=CASE WHEN state='active' THEN 'expired' ELSE state END,"
                "terminal_at=COALESCE(terminal_at,clock_timestamp()),cleaned_at=clock_timestamp() "
                "WHERE workspace_id=%s AND upload_id=%s AND source_id IS NULL", (workspace, upload_id))
        return len(keys)

    async def cleanup_uploads(self, *, limit: int = 100, grace_seconds: int = 300) -> int:
        if type(limit) is not int or not 1 <= limit <= 100 or type(grace_seconds) is not int or not 0 <= grace_seconds <= 86400:
            raise ValueError("Bounded upload cleanup and grace required")

        async def cleanup(conn: Connection) -> int:
            return await self._cleanup_uploads(conn, limit=limit, grace_seconds=grace_seconds)

        return await run_transaction(self._pool, cleanup)
