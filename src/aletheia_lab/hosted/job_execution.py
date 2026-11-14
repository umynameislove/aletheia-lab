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


async def execution_profile(conn: Connection) -> bool:
    row = await (await conn.execute("SELECT to_regclass('k09_job_attempts'),to_regclass('k09_queue_turns')")).fetchone()
    if row is None or len(row) != 2 or (row[0] is None) != (row[1] is None):
        raise TimeoutError('Job execution schema is unavailable.')
    return row[0] is not None


async def queue_lock(conn: Connection, workspace: str) -> None:
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema()||':k09-queue:'||%s,0))",
                       (workspace,))
