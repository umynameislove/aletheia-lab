"""Explicit, strictly verified actor indexes over the unchanged K-04 policy DDL."""
from __future__ import annotations

import json

from aletheia_lab.hosted_auth.postgres_sessions import Connection
from aletheia_lab.hosted_auth.postgres_sessions import _operator_namespace as _owned_namespace
from aletheia_lab.project.identity import content_hasher

_INDEXES = (
    ('k10_projects_creator', 'k05_projects', '(workspace_id, creator_id, project_id)'),
    ('k10_project_grants_reader', 'j01_project_grants', "(workspace_id, user_id, project_id) WHERE ('read'::text = ANY (actions))"),
)
_LEDGER_DDL = ('CREATE TABLE k10_read_indexes(version integer PRIMARY KEY CHECK(version=1),'
               "checksum text NOT NULL CHECK(checksum~'^[0-9a-f]{64}$'))")
_CHECKSUM = content_hasher(json.dumps({'indexes': _INDEXES, 'ledger': _LEDGER_DDL},
    separators=(',', ':'), sort_keys=True).encode()).hexdigest()


async def _verify_ledger(conn: Connection) -> None:
    behavior = await (await conn.execute(
        "SELECT c.relkind,c.relpersistence,c.relrowsecurity,c.relforcerowsecurity,c.relispartition,"
        '(SELECT count(*) FROM pg_inherits WHERE inhrelid=c.oid OR inhparent=c.oid),'
        '(SELECT count(*) FROM pg_trigger WHERE tgrelid=c.oid AND NOT tgisinternal) '
        "FROM pg_class c WHERE c.oid='k10_read_indexes'::regclass")).fetchone()
    if behavior != ('r', 'p', False, False, False, 0, 0):
        raise ReadIndexesRejected()
    cursor = await conn.execute('SELECT attname,format_type(atttypid,atttypmod),attnotnull,atthasdef,attidentity,attgenerated '
        "FROM pg_attribute WHERE attrelid='k10_read_indexes'::regclass AND attnum>0 AND NOT attisdropped ORDER BY attnum")
    columns = [await cursor.fetchone(), await cursor.fetchone(), await cursor.fetchone()]
    if columns != [('version', 'integer', True, False, '', ''), ('checksum', 'text', True, False, '', ''), None]:
        raise ReadIndexesRejected()
    cursor = await conn.execute("SELECT contype,pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid='k10_read_indexes'::regclass ORDER BY contype,pg_get_constraintdef(oid)")
    constraints = [await cursor.fetchone() for _ in range(4)]
    if constraints != [('c', "CHECK ((checksum ~ '^[0-9a-f]{64}$'::text))"),
                        ('c', 'CHECK ((version = 1))'), ('p', 'PRIMARY KEY (version)'), None]:
        raise ReadIndexesRejected()
    cursor = await conn.execute('SELECT indisunique,indisprimary,indisvalid,indisready,indislive,indkey::text '
        "FROM pg_index WHERE indrelid='k10_read_indexes'::regclass ORDER BY indexrelid")
    if await cursor.fetchone() != (True, True, True, True, True, '1') or await cursor.fetchone() is not None:
        raise ReadIndexesRejected()


class ReadIndexesRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__('The bounded read index extension is incomplete or changed.')


async def verified_extension(conn: Connection) -> bool:
    """False only for a completely absent legacy extension; partial/drift rejects."""
    row = await (await conn.execute("SELECT to_regclass('k10_read_indexes'),to_regclass('k10_projects_creator'),"
                                   "to_regclass('k10_project_grants_reader')")).fetchone()
    if row == (None, None, None):
        return False
    if row is None or len(row) != 3 or any(value is None for value in row):
        raise ReadIndexesRejected()
    await _verify_ledger(conn)
    rows = await conn.execute('SELECT version,checksum FROM k10_read_indexes ORDER BY version')
    if await rows.fetchone() != (1, _CHECKSUM) or await rows.fetchone() is not None:
        raise ReadIndexesRejected()
    for name, table, suffix in _INDEXES:
        actual = await (await conn.execute(
            'SELECT pg_get_indexdef(i.indexrelid),i.indisvalid,i.indisready,i.indislive,'
            'n.nspname=current_schema(),quote_ident(n.nspname) FROM pg_index i '
            'JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace '
            'WHERE i.indexrelid=to_regclass(%s)', (name,))).fetchone()
        if (actual is None or len(actual) != 6 or actual[1:5] != (True, True, True, True)
                or actual[0] != f'CREATE INDEX {name} ON {actual[5]}.{table} USING btree {suffix}'):
            raise ReadIndexesRejected()
    return True


async def migrate(conn: Connection, schema: str) -> str:
    """Owner-only explicit DDL, after both canonical v13 and K-04 policy exist."""
    if conn.autocommit:
        raise ReadIndexesRejected()
    namespace = await _owned_namespace(conn, schema)
    await conn.execute('SET LOCAL search_path TO ' + namespace[:-1] + ',pg_catalog')
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ('aletheia-k10-read-indexes:' + schema,))
    present = await (await conn.execute("SELECT to_regclass('k10_result_views'),to_regclass('k04_project_policy_migrations')")).fetchone()
    if present is None or len(present) != 2 or any(value is None for value in present):
        raise ReadIndexesRejected()
    if await verified_extension(conn):
        return 'unchanged'
    await conn.execute('LOCK TABLE k05_projects,j01_project_grants IN SHARE ROW EXCLUSIVE MODE')
    for name, table, suffix in _INDEXES:
        await conn.execute(f'CREATE INDEX {name} ON {table} USING btree {suffix}')
    await conn.execute(_LEDGER_DDL)
    await conn.execute('INSERT INTO k10_read_indexes(version,checksum) VALUES(1,%s)', (_CHECKSUM,))
    if not await verified_extension(conn):
        raise ReadIndexesRejected()
    return 'created'


async def install_if_possible(conn: Connection, schema: str) -> None:
    """Only called by explicit migrators, so either prerequisite install order works."""
    present = await (await conn.execute("SELECT to_regclass('k10_result_views'),to_regclass('k04_project_policy_migrations')")).fetchone()
    if present is not None and len(present) == 2 and all(value is not None for value in present):
        await migrate(conn, schema)


async def ready(conn: Connection) -> bool:
    try:
        return await verified_extension(conn)
    except Exception:
        return False
