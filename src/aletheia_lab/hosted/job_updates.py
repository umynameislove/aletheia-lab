"""Authorized coalesced job polling: short transactions, no stream or event cache."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from pydantic import SecretStr

from aletheia_lab.hosted_auth.postgres_sessions import _digest
from aletheia_lab.hosted_contract.job_updates import JobUpdate

from .cursors import ScopedCursors
from .job_lifecycle import public_job
from .job_polling import SnapshotPolling
from .job_updates_schema import JOB_UPDATES_DDL
from .postgres_sources import SourceNotFound

if TYPE_CHECKING:
    from .postgres_jobs import PostgresJobs

_READ = """SELECT operation,request,status_revision,jsonb_build_object(
 'state',state,'operation',operation,'created_at',created_at,'deadline_at',deadline_at,
 'terminal_at',terminal_at,'attempt_count',attempt_count,'max_attempts',max_attempts)
 FROM j01_jobs WHERE workspace_id=%s AND job_id=%s"""


class PostgresJobUpdates:
    def __init__(self, jobs: PostgresJobs, cursors: ScopedCursors, *,
                 random_source: Callable[[], float] | None = None) -> None:
        self.jobs = jobs
        self.polling = SnapshotPolling(cursors) if random_source is None else SnapshotPolling(cursors, random_source=random_source)

    async def ready(self) -> bool:
        if not self.jobs.execution:
            return False
        try:
            async with self.jobs._pool.connection() as conn:
                row = await (await conn.execute("SELECT a.attnotnull,a.attgenerated,format_type(a.atttypid,a.atttypmod),"
                    "pg_get_expr(d.adbin,d.adrelid),t.tgenabled,t.tgtype,p.prosrc,p.prosecdef,p.proconfig "
                    "FROM pg_attribute a JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
                    "JOIN pg_trigger t ON t.tgrelid=a.attrelid AND t.tgname='k15_job_status_revision' "
                    "JOIN pg_proc p ON p.oid=t.tgfoid AND p.pronamespace=(SELECT relnamespace FROM pg_class WHERE oid=a.attrelid) "
                    "WHERE a.attrelid='j01_jobs'::regclass AND a.attname='status_revision' AND NOT a.attisdropped "
                    "AND NOT t.tgisinternal AND p.proname='k15_job_status_revision'")).fetchone()
            source = JOB_UPDATES_DDL.split('AS $$', 1)[1].split('$$;', 1)[0].strip()
            return row is not None and len(row) == 9 and row[:6] == (True, '', 'bigint', '1', 'O', 19) and isinstance(row[6], str) and row[6].strip() == source and row[7:] == (False, None)
        except Exception:
            return False

    async def read(self, *, credential_digest: SecretStr, workspace_id: str,
                   job_id: str, cursor: str | None) -> JobUpdate:
        if not self.jobs.execution:
            raise SourceNotFound()
        digest = _digest(credential_digest)
        async with self.jobs._pool.connection() as conn:
            # No row or continuation is disclosed before current actor authority.
            await self.jobs._access._access(conn, digest, workspace_id, action='read')
            row = await (await conn.execute(_READ, (workspace_id, job_id))).fetchone()
            if row is None or len(row) != 4 or not isinstance(row[0], str) or not isinstance(row[1], dict):
                raise SourceNotFound()
            auth = await self.jobs._authorize(conn, digest, workspace_id, row[0], row[1], reading=True)
            if await self.jobs._expire(conn, workspace_id, job_id):
                row = await (await conn.execute(_READ, (workspace_id, job_id))).fetchone()
            if row is None or len(row) != 4 or not isinstance(row[3], dict) or type(row[2]) is not int:
                raise ValueError('Invalid persisted job snapshot.')
            session = await (await conn.execute('SELECT context_id,generation FROM j01_session_credentials WHERE digest=%s',
                (digest,))).fetchone()
            authority = await (await conn.execute('SELECT revision FROM k10_catalog_revisions WHERE workspace_id=%s',
                (workspace_id,))).fetchone()
            if (session is None or len(session) != 2 or not isinstance(session[0], str) or type(session[1]) is not int
                    or authority is not None and (len(authority) != 1 or type(authority[0]) is not int)):
                raise ValueError('Invalid job authority snapshot.')
            binding: dict[str, object] = {'workspace_id': workspace_id, 'job_id': job_id,
                'user_id': auth.user_id, 'context_id': session[0], 'generation': session[1],
                'membership_revision': auth.revision}
            return self.polling.response(job=public_job(workspace_id, job_id, row[3], retryable=None),
                revision=row[2], binding=binding, authority_revision=0 if authority is None else cast(int, authority[0]), cursor=cursor)
