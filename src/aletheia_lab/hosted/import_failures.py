"""Safe mapping diagnostics attached to the existing job's current lease."""
from __future__ import annotations

import json

from pydantic import SecretStr

from aletheia_lab.hosted_auth.postgres_sessions import Connection, Pool, _digest

from .import_errors import MappingInvalid
from .postgres_jobs import JobClaim
from .transactions import run_transaction


class ImportFailures:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def record(self, *, claim: JobClaim, lease_digest: SecretStr, failure: MappingInvalid) -> None:
        if claim.operation != 'confirm':
            raise ValueError('Mapping diagnostics require a confirm operation.')

        async def record(conn: Connection) -> None:
            await conn.execute('INSERT INTO k07_import_failures(job_id,workspace_id,payload) '
                'SELECT job_id,workspace_id,%s::jsonb FROM j01_jobs WHERE job_id=%s AND workspace_id=%s '
                "AND operation='confirm' AND credential_digest=%s AND state='running' "
                'AND lease_digest=%s AND lease_until>clock_timestamp() '
                'ON CONFLICT(job_id) DO UPDATE SET payload=EXCLUDED.payload',
                (failure.failure.model_dump_json(exclude_none=True), claim.job_id, claim.workspace_id,
                 _digest(claim.credential_digest), _digest(lease_digest)))
        await run_transaction(self._pool, record)

    async def read(self, *, workspace_id: str, job_id: str) -> dict[str, object] | None:
        # Internal projection: the caller must check current job policy before and after I/O.
        async with self._pool.connection() as conn:
            row = await (await conn.execute('SELECT payload::text FROM k07_import_failures '
                'WHERE workspace_id=%s AND job_id=%s', (workspace_id, job_id))).fetchone()
        if row is None:
            return None
        if not isinstance(row[0], str):
            raise RuntimeError('Invalid mapping diagnostic record.')
        return MappingInvalid(json.loads(row[0])).failure.model_dump(mode='json', exclude_none=True)
