"""Short PostgreSQL leases around filesystem publication, reads and cleanup.

An object write is not part of the metadata transaction. A committed lease
protects staging; ready metadata and its canonical record commit together.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import SupportsFloat, cast
from uuid import uuid4

from aletheia_lab.hosted_auth.postgres_sessions import Connection, Pool

from .async_work import run_blocking, run_blocking_nowait
from .object_store import (
    FilesystemObjectStore,
    ObjectCandidate,
    ObjectDescriptor,
    ObjectIntegrityError,
)


@dataclass(frozen=True)
class StagedObject:
    descriptor: ObjectDescriptor
    lease_id: str


@dataclass(frozen=True)
class ReconcileReport:
    examined: int
    removed: int
    retained: int
    next_cursor: str | None
    expired_leases_removed: int = 0
    orphan_metadata_removed: int = 0
    metadata_next_cursor: str | None = None
    deferred_for_backup: bool = False


@dataclass(frozen=True)
class ScrubReport:
    examined: int
    bytes_checked: int
    corrupt_sha256: tuple[str, ...]
    next_cursor: str | None


def _matches(row: tuple[object, ...] | None, descriptor: ObjectDescriptor) -> bool:
    # Schema is a nullable validation hint; domain schema stays on each record.
    return (row is not None and row[:2] == (descriptor.byte_size, descriptor.media_type)
            and (row[2] is None or descriptor.schema_version is None or row[2] == descriptor.schema_version))


class PostgresObjects:
    def __init__(self, pool: Pool, store: FilesystemObjectStore):
        self.pool, self.store = pool, store

    @staticmethod
    async def _gc_available(conn: Connection) -> bool:
        from .recovery_points import BACKUP_ADVISORY_KEY

        return await (await conn.execute('SELECT pg_try_advisory_xact_lock_shared(%s)',
            (BACKUP_ADVISORY_KEY,))).fetchone() == (True,)

    @staticmethod
    async def lock(conn: Connection, sha256: str) -> None:
        if conn.autocommit:
            raise RuntimeError("Transactional connection required")
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("Lowercase object SHA-256 required")
        # Share gates across tenants in this catalog, not unrelated catalogs.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended("
                           "current_schema()||':k05-object:'||%s,0))", (sha256,))

    async def _lease(self, workspace_id: str, descriptor: ObjectDescriptor,
                     lease_seconds: int, *, require_ready: bool) -> StagedObject:
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("Bounded object lease required")
        staged = StagedObject(descriptor, uuid4().hex)
        async with self.pool.connection() as conn:
            await self.lock(conn, descriptor.sha256)
            if require_ready:
                row = await (await conn.execute(
                    "SELECT byte_size,media_type,schema_version FROM k05_objects "
                    "WHERE workspace_id=%s AND sha256=%s", (workspace_id, descriptor.sha256),
                )).fetchone()
                if not _matches(row, descriptor):
                    raise ObjectIntegrityError("Object reference no longer matches")
            await conn.execute(
                "INSERT INTO k05_object_leases(workspace_id,sha256,lease_id,expires_at) "
                "VALUES (%s,%s,%s,clock_timestamp()+%s*interval '1 second')",
                (workspace_id, descriptor.sha256, staged.lease_id, lease_seconds),
            )
        return staged

    async def stage(self, workspace_id: str, payload: bytes, *, media_type: str,
                    schema_version: str | None = None, lease_seconds: int = 300) -> StagedObject:
        descriptor = await run_blocking(self.store.describe, payload, media_type=media_type, schema_version=schema_version)
        staged = await self._lease(workspace_id, descriptor, lease_seconds, require_ready=False)
        # Keep the committed lease after cancellation/failure: a disk worker may
        # still be writing; expiry lets a subsequent reconcile recover it.
        actual = await run_blocking(self.store.publish, payload, media_type=media_type,
                                   schema_version=schema_version)
        if actual != descriptor:
            raise ObjectIntegrityError("Published object identity mismatch")
        return staged

    async def ready(self, conn: Connection, workspace_id: str, staged: StagedObject) -> None:
        """Enlist before the canonical record/ref INSERT in the same transaction."""
        value = staged.descriptor
        await self.lock(conn, value.sha256)
        lease = await (await conn.execute(
            "SELECT lease_id FROM k05_object_leases WHERE workspace_id=%s AND sha256=%s "
            "AND lease_id=%s AND expires_at>clock_timestamp()",
            (workspace_id, value.sha256, staged.lease_id),
        )).fetchone()
        if lease is None:
            raise ObjectIntegrityError("Object publication lease expired; stage again")
        await conn.execute(
            "INSERT INTO k05_objects(workspace_id,sha256,byte_size,media_type,schema_version) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (workspace_id,sha256) DO NOTHING",
            (workspace_id, value.sha256, value.byte_size, value.media_type, value.schema_version),
        )
        row = await (await conn.execute(
            "SELECT byte_size,media_type,schema_version FROM k05_objects "
            "WHERE workspace_id=%s AND sha256=%s", (workspace_id, value.sha256),
        )).fetchone()
        if not _matches(row, value):
            raise ObjectIntegrityError("Immutable object metadata conflicts")

    @staticmethod
    async def finish(conn: Connection, workspace_id: str, staged: StagedObject) -> None:
        """Release only after the same transaction attached all references."""
        if conn.autocommit:
            raise RuntimeError("Transactional connection required")
        await conn.execute("DELETE FROM k05_object_leases "
                           "WHERE workspace_id=%s AND sha256=%s AND lease_id=%s",
                           (workspace_id, staged.descriptor.sha256, staged.lease_id))

    async def read(self, workspace_id: str, descriptor: ObjectDescriptor, *,
                   lease_seconds: int = 300) -> bytes:
        staged = await self._lease(workspace_id, descriptor, lease_seconds, require_ready=True)
        cancelled = False
        reading = asyncio.create_task(run_blocking(self.store.read, descriptor))
        try:
            return await asyncio.shield(reading)
        except asyncio.CancelledError:
            # The filesystem worker can still be reading after cancellation.
            cancelled = True
            def finished(task: asyncio.Task[bytes]) -> None:
                if not task.cancelled():
                    task.exception()
            reading.add_done_callback(finished)
            raise
        finally:
            if not cancelled:
                async with self.pool.connection() as conn:
                    await self.finish(conn, workspace_id, staged)

    async def _reconcile_candidates(self, candidates: tuple[ObjectCandidate, ...], grace_seconds: int,
                                    after: str) -> tuple[int, int, bool, str]:
        removed = retained = 0
        deferred = False
        processed_cursor = after
        for candidate in candidates:
            async with self.pool.connection() as conn:
                if not await self._gc_available(conn):
                    deferred = True
                    break
                await self.lock(conn, candidate.sha256)
                # Block FK reference writers before the final reachability check.
                await conn.execute("SELECT sha256 FROM k05_objects WHERE sha256=%s "
                                   "ORDER BY workspace_id FOR UPDATE", (candidate.sha256,))
                row = await (await conn.execute(
                    "SELECT EXTRACT(EPOCH FROM clock_timestamp()),"
                    "EXISTS(SELECT 1 FROM k05_records WHERE object_sha256=%s),"
                    "EXISTS(SELECT 1 FROM k05_object_references WHERE sha256=%s),"
                    "EXISTS(SELECT 1 FROM k05_object_leases WHERE sha256=%s "
                    "AND expires_at>clock_timestamp())",
                    (candidate.sha256, candidate.sha256, candidate.sha256),
                )).fetchone()
                if row is None:
                    raise RuntimeError("Object reconciliation facts unavailable")
                if (float(cast(SupportsFloat, row[0])) - candidate.modified_at < grace_seconds or row[3]
                        or not candidate.staged and (row[1] or row[2])):
                    retained += 1
                    processed_cursor = candidate.relative_path
                    continue
                # A single unlink stays under the digest lock so a fresh lease
                # cannot race between reference-check and physical deletion.
                changed = await run_blocking_nowait(self.store.remove_candidate, candidate)
                removed += int(changed)
                retained += int(not changed)
                processed_cursor = candidate.relative_path
                if changed and not candidate.staged:
                    await conn.execute("DELETE FROM k05_object_leases WHERE sha256=%s "
                                       "AND expires_at<=clock_timestamp()", (candidate.sha256,))
                    await conn.execute("DELETE FROM k05_objects WHERE sha256=%s", (candidate.sha256,))
        return removed, retained, deferred, processed_cursor

    async def reconcile(self, *, grace_seconds: int = 3600, limit: int = 1000,
                        after: str = "", metadata_after: str = "") -> ReconcileReport:
        if type(grace_seconds) is not int or not 60 <= grace_seconds <= 2592000:
            raise ValueError("Object grace period must be between 60 seconds and 30 days")
        if metadata_after:
            self.store.path(metadata_after)
        async with self.pool.connection() as conn:
            if not await self._gc_available(conn):
                return ReconcileReport(0, 0, 0, after or None,
                    metadata_next_cursor=metadata_after or None, deferred_for_backup=True)
        candidates = await run_blocking(self.store.candidates, limit=limit, after=after)
        async with self.pool.connection() as conn:
            if not await self._gc_available(conn):
                return ReconcileReport(0, 0, 0, after or None,
                    metadata_next_cursor=metadata_after or None, deferred_for_backup=True)
            cursor = await conn.execute(
                "WITH expired AS (SELECT workspace_id,sha256,lease_id FROM k05_object_leases "
                "WHERE expires_at<=clock_timestamp() ORDER BY expires_at LIMIT %s FOR UPDATE SKIP LOCKED) "
                "DELETE FROM k05_object_leases l USING expired e WHERE l.workspace_id=e.workspace_id "
                "AND l.sha256=e.sha256 AND l.lease_id=e.lease_id RETURNING l.lease_id", (limit,))
            expired = 0
            while await cursor.fetchone() is not None:
                expired += 1
        removed, retained, deferred, processed_cursor = await self._reconcile_candidates(candidates, grace_seconds, after)
        if deferred:
            metadata_removed, metadata_next = 0, metadata_after or None
        else:
            metadata_removed, metadata_next, deferred = await self._missing_metadata(
                grace_seconds=grace_seconds, limit=limit, after=metadata_after)
        next_cursor = candidates[-1].relative_path if len(candidates) == limit else None
        if deferred:
            next_cursor = processed_cursor or None
        return ReconcileReport(removed + retained, removed, retained, next_cursor,
                               expired, metadata_removed, metadata_next, deferred)

    async def _missing_metadata(self, *, grace_seconds: int, limit: int,
                                after: str) -> tuple[int, str | None, bool]:
        # Recover unlink-success/metadata-rollback without discarding a missing
        # canonical object: references always retain its metadata for recovery.
        async with self.pool.connection() as conn:
            cursor = await conn.execute("SELECT DISTINCT sha256 FROM k05_objects "
                                        "WHERE sha256>%s ORDER BY sha256 LIMIT %s", (after, limit))
            digests: list[str] = []
            while (row := await cursor.fetchone()) is not None:
                if not isinstance(row[0], str):
                    raise ObjectIntegrityError("Invalid object metadata")
                digests.append(row[0])
        removed = 0
        processed = after
        for digest in digests:
            path = self.store.path(digest)
            if await run_blocking(path.exists):
                processed = digest
                continue
            async with self.pool.connection() as conn:
                if not await self._gc_available(conn):
                    return removed, processed or None, True
                await self.lock(conn, digest)
                await conn.execute("SELECT sha256 FROM k05_objects WHERE sha256=%s "
                                   "ORDER BY workspace_id FOR UPDATE", (digest,))
                facts = await (await conn.execute(
                    "SELECT EXTRACT(EPOCH FROM clock_timestamp())-COALESCE("
                    "(SELECT EXTRACT(EPOCH FROM max(ready_at)) FROM k05_objects WHERE sha256=%s),0),"
                    "EXISTS(SELECT 1 FROM k05_records WHERE object_sha256=%s),"
                    "EXISTS(SELECT 1 FROM k05_object_references WHERE sha256=%s),"
                    "EXISTS(SELECT 1 FROM k05_object_leases WHERE sha256=%s AND expires_at>clock_timestamp())",
                    (digest, digest, digest, digest),
                )).fetchone()
                if (facts is None or float(cast(SupportsFloat, facts[0])) < grace_seconds or any(facts[1:])
                        or path.exists() or path.is_symlink()):
                    processed = digest
                    continue
                await conn.execute("DELETE FROM k05_object_leases WHERE sha256=%s "
                                   "AND expires_at<=clock_timestamp()", (digest,))
                cursor = await conn.execute("DELETE FROM k05_objects WHERE sha256=%s RETURNING workspace_id",
                                            (digest,))
                while await cursor.fetchone() is not None:
                    removed += 1
                processed = digest
        return removed, digests[-1] if len(digests) == limit else None, False

    async def scrub(self, workspace_id: str, *, limit: int = 100, after: str = "") -> ScrubReport:
        """Bounded background scan; damaged canonical objects are never repaired."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Bounded scrub batch required")
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT sha256,byte_size,media_type,schema_version FROM k05_objects "
                "WHERE workspace_id=%s AND sha256>%s ORDER BY sha256 LIMIT %s",
                (workspace_id, after, limit),
            )
            rows = []
            while (row := await cursor.fetchone()) is not None:
                rows.append(row)
        corrupt, checked = [], 0
        for row in rows:
            descriptor = ObjectDescriptor(cast(str, row[0]), cast(int, row[1]),
                                          cast(str, row[2]), cast(str | None, row[3]))
            try:
                payload = await self.read(workspace_id, descriptor)
                checked += len(payload)
            except ObjectIntegrityError:
                corrupt.append(descriptor.sha256)
        return ScrubReport(len(rows), checked, tuple(corrupt), cast(str, rows[-1][0]) if len(rows) == limit else None)
