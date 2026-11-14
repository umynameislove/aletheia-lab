"""Job readiness checks source, parent constraints and policy migration columns."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from aletheia_lab.hosted.postgres_jobs import PostgresJobs

SOURCE_COLUMNS = (
    'source_id', 'workspace_id', 'creator_id', 'request_key_hash',
    'sha256', 'payload', 'manifest', 'created_at',
)
SNAPSHOT_COLUMNS = ('snapshot_id', 'workspace_id', 'source_id', 'payload')
GRANT_COLUMNS = ('workspace_id', 'source_id', 'user_id', 'actions', 'revision')
MIGRATION_COLUMNS = ('version', 'checksum', 'applied_at')


class SchemaPool:
    def __init__(self):
        self.schema = {
            'j01_jobs': set(('job_id', 'workspace_id', 'creator_id', 'credential_digest',
                             'operation', 'key_hash', 'request_hash', 'request', 'request_id',
                             'state', 'lease_digest', 'lease_until', 'result', 'created_at')),
            'j01_sources': set(SOURCE_COLUMNS),
            'j01_upload_snapshots': set(SNAPSHOT_COLUMNS),
            'j01_source_grants': set(GRANT_COLUMNS),
            'k04_workspace_migrations': set(MIGRATION_COLUMNS),
        }
        self.projections = {}
        self.cancel_table = None
        self.closed = False
        self.constraint = (True,)

    @asynccontextmanager
    async def connection(self):
        try:
            yield self
        finally:
            self.closed = True

    async def execute(self, query):
        if query.startswith('SELECT convalidated FROM pg_constraint'):
            assert "conrelid='j01_upload_snapshots'::regclass" in query
            assert "conname='j01_snapshots_source_scope'" in query
            if self.cancel_table == 'pg_constraint':
                raise asyncio.CancelledError()
            return self
        projection, relation = query.removeprefix('SELECT ').split(' FROM ')
        table, limit = relation.split(' ', 1)
        assert limit == 'LIMIT 0'
        columns = set(projection.split(','))
        self.projections[table] = columns
        if table == self.cancel_table:
            raise asyncio.CancelledError()
        if table not in self.schema or not columns <= self.schema[table]:
            raise RuntimeError('synthetic missing schema')
        return self

    async def fetchone(self):
        return self.constraint


def test_ready_checks_full_source_and_snapshot_projections():
    pool = SchemaPool()
    assert asyncio.run(PostgresJobs(pool, object()).is_ready()) is True
    assert list(pool.projections) == ['j01_jobs', 'j01_sources', 'j01_upload_snapshots',
                                      'j01_source_grants', 'k04_workspace_migrations']
    assert pool.projections['j01_sources'] == set(SOURCE_COLUMNS)
    assert pool.projections['j01_upload_snapshots'] == set(SNAPSHOT_COLUMNS)
    assert pool.projections['j01_source_grants'] == set(GRANT_COLUMNS)
    assert pool.closed


def test_missing_snapshots_table_is_unready():
    pool = SchemaPool()
    del pool.schema['j01_upload_snapshots']
    assert asyncio.run(PostgresJobs(pool, object()).is_ready()) is False
    assert pool.closed


@pytest.mark.parametrize(
    'table,column',
    [('j01_sources', column) for column in SOURCE_COLUMNS]
    + [('j01_upload_snapshots', column) for column in SNAPSHOT_COLUMNS]
    + [('j01_source_grants', column) for column in GRANT_COLUMNS]
    + [('k04_workspace_migrations', column) for column in MIGRATION_COLUMNS],
)
def test_missing_required_source_or_snapshot_column_is_unready(table, column):
    pool = SchemaPool()
    pool.schema[table].remove(column)
    assert asyncio.run(PostgresJobs(pool, object()).is_ready()) is False
    assert pool.closed


@pytest.mark.parametrize('constraint', [None, (False,)])
def test_missing_or_unvalidated_parent_constraint_is_unready(constraint):
    pool = SchemaPool()
    pool.constraint = constraint
    assert asyncio.run(PostgresJobs(pool, object()).is_ready()) is False
    assert pool.closed


@pytest.mark.parametrize('table', ['j01_upload_snapshots', 'pg_constraint'])
def test_snapshot_readiness_cancellation_propagates_and_closes_connection(table):
    pool = SchemaPool()
    pool.cancel_table = table
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(PostgresJobs(pool, object()).is_ready())
    assert pool.closed
