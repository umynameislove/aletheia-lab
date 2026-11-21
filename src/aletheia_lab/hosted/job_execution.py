"""Bounded durable-queue policy and execution-profile checks; no startup DDL."""
from __future__ import annotations

from dataclasses import dataclass

from aletheia_lab.hosted_auth.postgres_sessions import Connection

from .transactions import TransactionUnavailable


@dataclass(frozen=True)
class QueuePolicy:
    pending_per_workspace: int = 32
    pending_per_class: int = 8
    running_per_workspace: int = 2
    running_per_class: int = 1

    def __post_init__(self) -> None:
        if (any(type(value) is not int or not 1 <= value <= 10000 for value in (
                self.pending_per_workspace, self.pending_per_class,
                self.running_per_workspace, self.running_per_class))
                or self.pending_per_class > self.pending_per_workspace
                or self.running_per_class > self.running_per_workspace):
            raise ValueError('Invalid bounded queue policy.')


class QueueFull(TransactionUnavailable):
    """No job was admitted; the caller can retain its key and retry later."""


async def base_job_schema_ready(conn: Connection) -> bool:
    await conn.execute(
        "SELECT j.job_id,j.workspace_id,j.creator_id,j.credential_digest,j.operation,j.key_hash,"
        "j.request_hash,j.request,j.request_id,j.state,j.lease_digest,j.lease_until,j.result,j.created_at,"
        "s.source_id,s.workspace_id,s.creator_id,s.request_key_hash,s.sha256,s.payload,s.manifest,s.created_at,"
        "u.snapshot_id,u.workspace_id,u.source_id,u.payload,g.workspace_id,g.source_id,g.user_id,g.actions,"
        "g.revision,m.version,m.checksum,m.applied_at FROM j01_jobs j CROSS JOIN j01_sources s "
        "CROSS JOIN j01_upload_snapshots u CROSS JOIN j01_source_grants g "
        "CROSS JOIN k04_workspace_migrations m LIMIT 0")
    constraint = await (await conn.execute("SELECT convalidated FROM pg_constraint WHERE "
        "conrelid='j01_upload_snapshots'::regclass AND conname='j01_snapshots_source_scope'")).fetchone()
    return constraint == (True,)


async def execution_profile(conn: Connection) -> bool:
    row = await (await conn.execute("SELECT to_regclass('k09_job_attempts'),to_regclass('k09_queue_turns')")).fetchone()
    if row is None or len(row) != 2 or (row[0] is None) != (row[1] is None):
        raise TimeoutError('Job execution schema is unavailable.')
    return row[0] is not None


async def queue_lock(conn: Connection, workspace: str) -> None:
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema()||':k09-queue:'||%s,0))",
                       (workspace,))
