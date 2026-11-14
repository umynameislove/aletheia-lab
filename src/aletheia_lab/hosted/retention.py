"""Bounded owner maintenance and authorized, truthful deletion progress.

Project deletion never deletes workspace upload sources or user source folders.
A purge keeps the project/deletion fence, removes dependent private rows, then
checks every captured digest under the existing shared-object/lease gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

from aletheia_lab.hosted_auth.postgres_sessions import Connection, Pool, _digest
from aletheia_lab.hosted_contract.deletions import DeletionStatus
from aletheia_lab.hosted_contract.runtime_management import ProjectId
from aletheia_lab.hosted_contract.session_capabilities import Identifier
from aletheia_lab.project.identity import content_hasher

from .async_work import run_blocking_nowait
from .authorization import AuthorizationDenied, PostgresAuthorization
from .job_lifecycle import expire_job
from .object_store import ObjectCandidate
from .postgres_catalog import PostgresCatalog
from .postgres_objects import PostgresObjects
from .resource_admission import ResourceBusy
from .retention_schema import RETENTION_DDL
from .transactions import run_transaction

if TYPE_CHECKING:
    from .deletion_checkpoint import DeletionCheckpointPage


class RetentionRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Retention state or owner preconditions did not match.")


@dataclass(frozen=True)
class RetentionBatch:
    workspace_id: str
    project_id: str
    status: str
    action: str
    changed: int


def _bound(value: int, maximum: int = 1000) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("A bounded retention batch is required.")
    return value


async def _rows(
    conn: Connection, query: str, params: tuple[object, ...] = ()
) -> list[tuple[Any, ...]]:
    cursor = await conn.execute(query, params)
    result = []
    while (row := await cursor.fetchone()) is not None:
        result.append(row)
    return result


async def require_owner(conn: Connection) -> None:
    row = await (
        await conn.execute(
            "SELECT n.nspname NOT IN ('public','information_schema') "
            "AND n.nspname NOT LIKE 'pg_%' AND (r.rolname=current_user OR "
            "(SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user)) "
            "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "JOIN pg_catalog.pg_roles r ON r.oid=c.relowner "
            "WHERE c.oid=to_regclass(format('%I.k13_deletions',current_schema())) "
            "AND c.relkind='r' AND c.relpersistence='p'"
        )
    ).fetchone()
    if row != (True,):
        raise RetentionRejected()


# Every target is static trusted SQL. One call deletes at most limit rows in
# one dependent table; the canonical-record step additionally removes its refs.
_DEPENDENTS = (
    ("annotations", "j01_annotations", "project_id=%s"),
    ("exports", "k02_exports", "project_id=%s"),
    (
        "projection_items",
        "k10_result_items",
        "result_id IN (SELECT record_id FROM k05_records WHERE workspace_id=%s AND project_id=%s)",
    ),
    (
        "projection_links",
        "k10_evidence_links",
        "result_id IN (SELECT record_id FROM k05_records WHERE workspace_id=%s AND project_id=%s)",
    ),
    ("projection_headers", "k10_result_views", "project_id=%s"),
    ("current_pointers", "k05_current_pointers", "project_id=%s"),
    ("confirmations", "k05_confirmations", "project_id=%s"),
    (
        "upload_snapshots",
        "j01_upload_snapshots",
        "canonical_record_id IN (SELECT record_id FROM k05_records WHERE workspace_id=%s AND project_id=%s)",
    ),
    ("parent_links", "k05_record_parents", "project_id=%s"),
)


class PostgresRetention:
    def __init__(self, pool: Pool, catalog: PostgresCatalog) -> None:
        if pool is not catalog.pool:
            raise ValueError("Retention must use the canonical catalog pool.")
        self.pool, self.objects = pool, catalog.objects
        self.policy = PostgresAuthorization()

    async def is_ready(self) -> bool:
        try:
            async with self.pool.connection() as conn:
                row = await (
                    await conn.execute(
                        "SELECT checksum FROM k05_persistence_migrations WHERE version=15"
                    )
                ).fetchone()
                await conn.execute("SELECT eligible_at,state FROM k13_deletions LIMIT 0")
            return row == (content_hasher(RETENTION_DDL.encode()).hexdigest(),)
        except Exception:
            return False

    async def status(
        self, *, credential_digest: SecretStr, workspace_id: str, project_id: str
    ) -> DeletionStatus:
        async with self.pool.connection() as conn:
            auth = await self.policy.authorize(
                conn, _digest(credential_digest), workspace_id, "read"
            )
            await self.policy.authorize_project(
                conn, auth, workspace_id, project_id, "read", allow_deleted=True
            )
            row = await (
                await conn.execute(
                    "SELECT d.tombstone_id,d.revision,d.state,d.requested_at,d.eligible_at,"
                    "d.project_days,d.time_basis,d.purged_at,d.records_removed,"
                    "(SELECT count(*) FROM k13_project_objects o WHERE o.workspace_id=d.workspace_id AND o.project_id=d.project_id AND o.state='pending'),"
                    "(SELECT count(*) FROM k13_project_objects o WHERE o.workspace_id=d.workspace_id AND o.project_id=d.project_id AND o.state='shared') "
                    "FROM k13_deletions d WHERE d.workspace_id=%s AND d.project_id=%s",
                    (workspace_id, project_id),
                )
            ).fetchone()
            if row is None:
                raise AuthorizationDenied()
            return DeletionStatus.model_validate(
                dict(
                    zip(
                        (
                            "tombstone_id",
                            "revision",
                            "status",
                            "requested_at",
                            "eligible_at",
                            "project_retention_days",
                            "time_basis",
                            "purged_at",
                            "records_removed",
                            "pending_objects",
                            "shared_objects_retained",
                        ),
                        row,
                        strict=True,
                    ),
                    schema_version="product-api/v1-draft",
                    workspace_id=workspace_id,
                    project_id=project_id,
                )
            )

    async def configure(
        self,
        *,
        project_days: int = 30,
        terminal_job_days: int = 7,
        object_grace_seconds: int = 3600,
    ) -> None:
        if (
            any(type(v) is not int or not 0 <= v <= 3650 for v in (project_days, terminal_job_days))
            or type(object_grace_seconds) is not int
            or not 60 <= object_grace_seconds <= 2592000
        ):
            raise ValueError("Retention policy is outside its supported bounds.")
        async with self.pool.connection() as conn:
            await require_owner(conn)
            row = await (
                await conn.execute(
                    "UPDATE k13_retention_policy SET project_days=%s,terminal_job_days=%s,"
                    "object_grace_seconds=%s WHERE singleton RETURNING singleton",
                    (project_days, terminal_job_days, object_grace_seconds),
                )
            ).fetchone()
            if row != (True,):
                raise RetentionRejected()

    async def due(
        self, *, limit: int = 100, after: tuple[datetime, str, str] | None = None
    ) -> tuple[tuple[datetime, str, str], ...]:
        from pydantic import AwareDatetime, TypeAdapter

        _bound(limit)
        if after is not None:
            after = TypeAdapter(tuple[AwareDatetime, Identifier, ProjectId]).validate_python(after)
        async with self.pool.connection() as conn:
            await require_owner(conn)
            cursor = "AND (eligible_at,workspace_id,project_id)>(%s,%s,%s) " if after else ""
            return tuple(
                await _rows(
                    conn,
                    "SELECT eligible_at,workspace_id,project_id FROM k13_deletions "
                    "WHERE state<>'purged' AND eligible_at<=clock_timestamp() "
                    + cursor
                    + "ORDER BY eligible_at,workspace_id,project_id LIMIT %s",
                    (*after, limit) if after else (limit,),
                )
            )

    async def expire(self, *, limit: int = 100) -> dict[str, int]:
        """Remove expired previews and old terminal private jobs; retain replay receipts.

        Canonical exported bytes remain referenced until their result is purged;
        deleting a replay handle is not a claim that its artifact bytes vanished.
        Append-only action-audit IDs are deliberately retained, not payload logs.
        """
        _bound(limit)
        async with self.pool.connection() as conn:
            await require_owner(conn)
            # Expired immutable handles are replay tombstones. Removing them here
            # would let the same idempotency key mint a new fifteen-minute expiry.
            exports = await _rows(
                conn,
                "SELECT export_id FROM k02_exports WHERE expires_at<=clock_timestamp() "
                "ORDER BY expires_at,workspace_id,export_id LIMIT %s",
                (limit,),
            )
            previews: list[tuple[Any, ...]] = []
            if await (
                await conn.execute("SELECT to_regclass('k07_import_previews') IS NOT NULL")
            ).fetchone() == (True,):
                previews = await _rows(
                    conn,
                    "WITH chosen AS (SELECT preview_id FROM k07_import_previews p "
                    "WHERE expires_at<=clock_timestamp() AND NOT EXISTS(SELECT 1 FROM j01_jobs j WHERE j.workspace_id=p.workspace_id "
                    "AND j.request->'mapping'->>'_preview_id'=p.preview_id AND j.state IN ('queued','running','cancel_requested')) "
                    "ORDER BY expires_at,preview_id LIMIT %s FOR UPDATE SKIP LOCKED) "
                    "DELETE FROM k07_import_previews p USING chosen c WHERE p.preview_id=c.preview_id RETURNING p.preview_id",
                    (limit,),
                )
            jobs = await self._old_jobs(conn, limit=limit)
            return {
                "expired_export_receipts_retained": len(exports),
                "expired_previews": len(previews),
                "terminal_jobs": jobs,
            }

    async def _old_jobs(
        self,
        conn: Connection,
        *,
        limit: int,
        workspace: str | None = None,
        project: str | None = None,
    ) -> int:
        params: tuple[object, ...] = (limit,)
        scope = ""
        if workspace is not None and project is not None:
            scope = "AND j.workspace_id=%s AND (" + self._job_scope() + ") "
            params = (workspace, project, project, project, project, project, project, limit)
        rows = await _rows(
            conn,
            "SELECT j.workspace_id,j.job_id FROM j01_jobs j CROSS JOIN k13_retention_policy p "
            "WHERE j.state IN ('succeeded','failed','cancelled','expired') AND j.terminal_at IS NOT NULL "
            "AND j.terminal_at+p.terminal_job_days*interval '24 hours'<=clock_timestamp() "
            + scope
            + "AND NOT EXISTS(SELECT 1 FROM j01_jobs newer WHERE newer.workspace_id=j.workspace_id AND newer.retry_of_job_id=j.job_id) "
            "ORDER BY j.terminal_at,j.workspace_id,j.job_id LIMIT %s FOR UPDATE OF j SKIP LOCKED",
            params,
        )
        for work, job in rows:
            await conn.execute(
                "DELETE FROM k09_job_attempts WHERE workspace_id=%s AND job_id=%s", (work, job)
            )
            if await (
                await conn.execute("SELECT to_regclass('k07_import_failures') IS NOT NULL")
            ).fetchone() == (True,):
                await conn.execute(
                    "DELETE FROM k07_import_failures WHERE workspace_id=%s AND job_id=%s",
                    (work, job),
                )
            await conn.execute(
                "DELETE FROM j01_jobs WHERE workspace_id=%s AND job_id=%s", (work, job)
            )
        return len(rows)

    @staticmethod
    def _job_scope() -> str:
        return (
            "j.canonical_project_id=%s OR j.refresh_project_id=%s OR j.request->>'project_id'=%s "
            "OR j.canonical_result_id IN (SELECT record_id FROM k05_records WHERE workspace_id=j.workspace_id AND project_id=%s) "
            "OR j.snapshot_id IN (SELECT record_id FROM k05_records WHERE workspace_id=j.workspace_id AND project_id=%s) "
            "OR j.result->>'snapshot_id' IN (SELECT record_id FROM k05_records WHERE workspace_id=j.workspace_id AND project_id=%s)"
        )

    async def _purge_dependents(self, conn: Connection, workspace_id: str,
                                project_id: str, limit: int) -> RetentionBatch | None:
        for action, table, condition in _DEPENDENTS:
            exists = await (
                await conn.execute("SELECT to_regclass(%s) IS NOT NULL", (table,))
            ).fetchone()
            if exists != (True,):
                continue
            scoped_params = (
                (workspace_id, workspace_id, project_id, limit)
                if "SELECT record_id" in condition
                else (workspace_id, project_id, limit)
            )
            rows = await _rows(
                conn,
                "WITH chosen AS (SELECT ctid FROM "
                + table
                + " WHERE workspace_id=%s AND "
                + condition
                + " LIMIT %s FOR UPDATE SKIP LOCKED) DELETE FROM "
                + table
                + " t USING chosen c WHERE t.ctid=c.ctid RETURNING 1",
                scoped_params,
            )
            if rows:
                return RetentionBatch(workspace_id, project_id, "purging", action, len(rows))
        return None

    async def _purge_records(self, conn: Connection, workspace_id: str,
                             project_id: str, limit: int) -> RetentionBatch | None:
        references = await _rows(
            conn,
            "SELECT x.record_id,x.sha256 FROM k05_object_references x JOIN k05_records r "
            "ON r.workspace_id=x.workspace_id AND r.record_id=x.record_id WHERE x.workspace_id=%s AND r.project_id=%s "
            "AND r.record_type<>'deletion' ORDER BY x.record_id,x.sha256 LIMIT %s FOR UPDATE OF x SKIP LOCKED",
            (workspace_id, project_id, limit),
        )
        if references:
            for record, digest in references:
                await conn.execute(
                    "INSERT INTO k13_project_objects(workspace_id,project_id,sha256) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                    (workspace_id, project_id, digest),
                )
                await conn.execute(
                    "DELETE FROM k05_object_references WHERE workspace_id=%s AND record_id=%s AND sha256=%s",
                    (workspace_id, record, digest),
                )
            return RetentionBatch(
                workspace_id, project_id, "purging", "artifact_references", len(references)
            )
        records = await _rows(
            conn,
            "SELECT r.record_id,r.object_sha256 FROM k05_records r WHERE workspace_id=%s AND project_id=%s "
            "AND record_type<>'deletion' AND NOT EXISTS(SELECT 1 FROM k05_records child WHERE child.workspace_id=r.workspace_id "
            "AND child.project_id=r.project_id AND (child.snapshot_id=r.record_id OR child.parent_result_id=r.record_id)) "
            "ORDER BY record_id LIMIT %s FOR UPDATE OF r SKIP LOCKED",
            (workspace_id, project_id, limit),
        )
        if records:
            for record, digest in records:
                await conn.execute(
                    "INSERT INTO k13_project_objects(workspace_id,project_id,sha256) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                    (workspace_id, project_id, digest),
                )
                await conn.execute(
                    "DELETE FROM k05_records WHERE workspace_id=%s AND record_id=%s",
                    (workspace_id, record),
                )
            await conn.execute(
                "UPDATE k13_deletions SET records_removed=records_removed+%s WHERE workspace_id=%s AND project_id=%s",
                (len(records), workspace_id, project_id),
            )
            return RetentionBatch(
                workspace_id, project_id, "purging", "canonical_records", len(records)
            )
        remaining = await (
            await conn.execute(
                "SELECT 1 FROM k05_records WHERE workspace_id=%s AND project_id=%s AND record_type<>'deletion' LIMIT 1",
                (workspace_id, project_id),
            )
        ).fetchone()
        if remaining:
            return RetentionBatch(
                workspace_id, project_id, "purging", "record_dependency_wait", 0
            )
        return None

    async def _expire_project_jobs(self, conn: Connection, workspace_id: str,
                                   project_id: str, limit: int) -> RetentionBatch | None:
        # Bound expiration work before canonical FK cleanup; non-expired jobs
        # hold the project until their current durable outcome is established.
        scoped = await _rows(
            conn,
            "SELECT job_id FROM j01_jobs j WHERE workspace_id=%s AND ("
            + self._job_scope()
            + ") AND state IN ('queued','running','cancel_requested') ORDER BY job_id LIMIT %s",
            (workspace_id, *((project_id,) * 6), limit),
        )
        expired = 0
        for (job,) in scoped:
            expired += int(await expire_job(conn, workspace_id, job, execution=True))
        if scoped:
            return RetentionBatch(
                workspace_id,
                project_id,
                "purging",
                "job_deadline" if expired else "active_job_wait",
                expired,
            )
        return None

    async def _purge_transaction(self, conn: Connection, workspace_id: str,
                                 project_id: str, limit: int) -> RetentionBatch:
        from psycopg import sql

        await require_owner(conn)
        row = await (
            await conn.execute(
                "SELECT project_id FROM k05_projects WHERE workspace_id=%s AND project_id=%s FOR UPDATE",
                (workspace_id, project_id),
            )
        ).fetchone()
        if row is None:
            raise RetentionRejected()
        deletion = await (
            await conn.execute(
                "SELECT state,eligible_at<=clock_timestamp(),tombstone_id FROM k13_deletions "
                "WHERE workspace_id=%s AND project_id=%s FOR UPDATE",
                (workspace_id, project_id),
            )
        ).fetchone()
        if deletion is None:
            raise RetentionRejected()
        state, eligible, marker = deletion
        if state == "purged" or not eligible:
            return RetentionBatch(
                workspace_id,
                project_id,
                str(state),
                "complete" if state == "purged" else "retention_wait",
                0,
            )
        marker_row = await (
            await conn.execute(
                "SELECT record_id FROM k05_records WHERE workspace_id=%s AND project_id=%s "
                "AND record_id=%s AND record_type='deletion'",
                (workspace_id, project_id, marker),
            )
        ).fetchone()
        if marker_row != (marker,):
            raise RetentionRejected()
        await conn.execute(
            "UPDATE k13_deletions SET state='purging' WHERE workspace_id=%s AND project_id=%s",
            (workspace_id, project_id),
        )
        await conn.execute(
            "UPDATE k05_projects SET display_name=NULL WHERE workspace_id=%s AND project_id=%s AND display_name IS NOT NULL",
            (workspace_id, project_id),
        )
        batch = await self._expire_project_jobs(conn, workspace_id, project_id, limit)
        if batch is not None:
            return batch
        jobs = await self._old_jobs(
            conn, limit=limit, workspace=workspace_id, project=project_id
        )
        if jobs:
            return RetentionBatch(workspace_id, project_id, "purging", "terminal_jobs", jobs)
        held = await (
            await conn.execute(
                sql.SQL(
                    'SELECT 1 FROM j01_jobs j WHERE workspace_id=%s AND ({}) LIMIT 1'
                ).format(sql.SQL(self._job_scope())).as_string(),
                (workspace_id, *((project_id,) * 6)),
            )
        ).fetchone()
        if held:
            return RetentionBatch(
                workspace_id, project_id, "purging", "job_retention_or_retry_wait", 0
            )
        batch = await self._purge_dependents(conn, workspace_id, project_id, limit)
        if batch is not None:
            return batch
        batch = await self._purge_records(conn, workspace_id, project_id, limit)
        if batch is not None:
            return batch
        return await self._objects(conn, workspace_id, project_id, limit)


    async def purge(
        self, *, workspace_id: str, project_id: str, limit: int = 100
    ) -> RetentionBatch:
        """One bounded unit of work; repeat until the returned status is purged."""
        from pydantic import TypeAdapter

        TypeAdapter(Identifier).validate_python(workspace_id)
        TypeAdapter(ProjectId).validate_python(project_id)
        _bound(limit)

        async def operation(conn: Connection) -> RetentionBatch:
            return await self._purge_transaction(conn, workspace_id, project_id, limit)

        return await run_transaction(self.pool, operation)

    async def _object_outcome(self, conn: Connection, digest: str, grace_seconds: int) -> str:
        await PostgresObjects.lock(conn, digest)
        await conn.execute(
            "SELECT sha256 FROM k05_objects WHERE sha256=%s ORDER BY workspace_id FOR UPDATE",
            (digest,),
        )
        facts = await (
            await conn.execute(
                "SELECT extract(epoch FROM clock_timestamp()),"
                "EXISTS(SELECT 1 FROM k05_records WHERE object_sha256=%s),"
                "EXISTS(SELECT 1 FROM k05_object_references WHERE sha256=%s),"
                "EXISTS(SELECT 1 FROM k05_object_leases WHERE sha256=%s AND expires_at>clock_timestamp())",
                (digest, digest, digest),
            )
        ).fetchone()
        if facts is None or not isinstance(facts[0], (int, float, Decimal)):
            raise RetentionRejected()
        if facts[1] or facts[2]:
            return "shared"
        if facts[3]:
            return "pending"
        try:
            removed = await self._remove_object_file(digest, facts[0], grace_seconds)
        except ResourceBusy:
            return "resource_wait"
        if not removed:
            return "pending"
        await conn.execute(
            "DELETE FROM k05_object_leases WHERE sha256=%s AND expires_at<=clock_timestamp()",
            (digest,),
        )
        await conn.execute("DELETE FROM k05_objects WHERE sha256=%s", (digest,))
        return "removed"

    async def _remove_object_file(self, digest: str, now: int | float | Decimal,
                                  grace_seconds: int) -> bool:
        path = self.objects.store.path(digest)
        try:
            info = await run_blocking_nowait(path.stat)
        except FileNotFoundError:
            return True
        if float(now) - info.st_mtime < grace_seconds:
            return False
        candidate = ObjectCandidate(digest, path.relative_to(self.objects.store.root).as_posix(),
                                    info.st_mtime, False)
        return await run_blocking_nowait(self.objects.store.remove_candidate, candidate)

    async def _objects(
        self, conn: Connection, workspace: str, project: str, limit: int
    ) -> RetentionBatch:
        from .recovery_points import BACKUP_ADVISORY_KEY

        barrier = await (
            await conn.execute(
                "SELECT pg_try_advisory_xact_lock_shared(%s)", (BACKUP_ADVISORY_KEY,)
            )
        ).fetchone()
        if barrier != (True,):
            return RetentionBatch(workspace, project, "purging", "backup_wait", 0)
        grace_row = await (
            await conn.execute(
                "SELECT object_grace_seconds FROM k13_retention_policy WHERE singleton"
            )
        ).fetchone()
        if grace_row is None or type(grace_row[0]) is not int:
            raise RetentionRejected()
        digests = await _rows(
            conn,
            "SELECT sha256 FROM k13_project_objects WHERE workspace_id=%s AND project_id=%s AND state='pending' ORDER BY sha256 LIMIT %s",
            (workspace, project, limit),
        )
        changed = 0
        for (digest,) in digests:
            outcome = await self._object_outcome(conn, digest, grace_row[0])
            if outcome == "resource_wait":
                return RetentionBatch(workspace, project, "purging", "resource_wait", changed)
            if outcome == "pending":
                continue
            await conn.execute(
                "UPDATE k13_project_objects SET state=%s WHERE workspace_id=%s AND project_id=%s AND sha256=%s",
                (outcome, workspace, project, digest),
            )
            changed += 1
        pending = await (
            await conn.execute(
                "SELECT 1 FROM k13_project_objects WHERE workspace_id=%s AND project_id=%s AND state='pending' LIMIT 1",
                (workspace, project),
            )
        ).fetchone()
        if pending is None:
            await conn.execute(
                "UPDATE k13_deletions SET state='purged',purged_at=clock_timestamp() WHERE workspace_id=%s AND project_id=%s",
                (workspace, project),
            )
            return RetentionBatch(workspace, project, "purged", "complete", changed)
        return RetentionBatch(workspace, project, "purging", "objects_or_lease_wait", changed)

    async def export_checkpoint_page(
        self,
        *,
        limit: int = 1000,
        after: tuple[str, str] | None = None,
        connection: Connection | None = None,
    ) -> DeletionCheckpointPage:
        from .deletion_checkpoint import export_page

        if connection is not None:
            return await export_page(self, connection, limit=limit, after=after)
        async with self.pool.connection() as conn:
            return await export_page(self, conn, limit=limit, after=after)

    async def apply_checkpoint_page(self, page: DeletionCheckpointPage) -> int:
        from .deletion_checkpoint import apply_page

        return await apply_page(self, page)
