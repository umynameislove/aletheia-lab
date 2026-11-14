"""Additive project policy extension after the canonical K-05 schema is installed."""
from __future__ import annotations

import json
import re
from pathlib import Path

from aletheia_lab.hosted.content_hashing import content_hasher
from aletheia_lab.hosted_auth.postgres_sessions import Connection

from .read_indexes import ReadIndexesRejected, install_if_possible, verified_extension

_SCHEMA = re.compile(r"[a-z][a-z0-9_]{0,62}")
_LEDGER = "k04_project_policy_migrations"


class ProjectPolicyMigrationRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Project policy migration preconditions were not satisfied.")


async def _context(conn: Connection, schema: str | None) -> str:
    if conn.autocommit:
        raise ProjectPolicyMigrationRejected()
    if schema is None:
        row = await (await conn.execute("SELECT current_schema()")).fetchone()
        schema = str(row[0]) if row is not None else ""
    if (not isinstance(schema, str) or _SCHEMA.fullmatch(schema) is None
            or schema in ("public", "information_schema") or schema.startswith("pg_")):
        raise ProjectPolicyMigrationRejected()
    if await (await conn.execute("SELECT nspname FROM pg_namespace WHERE nspname=%s", (schema,))).fetchone() != (schema,):
        raise ProjectPolicyMigrationRejected()
    # Schema is a validated identifier; context is local to this transaction.
    await conn.execute('SET LOCAL search_path TO "' + schema + '",pg_catalog')
    return schema


async def _rows(conn: Connection, query: str) -> list[tuple[object, ...]]:
    cursor = await conn.execute(query)
    rows = []
    while (row := await cursor.fetchone()) is not None:
        rows.append(row)
    return rows


async def _signature(conn: Connection) -> str:
    indexed = await verified_extension(conn)
    queries = (
        "SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,"
        "pg_get_expr(d.adbin,d.adrelid),c.relkind,c.relrowsecurity,c.relforcerowsecurity "
        "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid LEFT JOIN pg_attrdef d "
        "ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE a.attrelid IN "
        "('j01_project_grants'::regclass,'k04_project_policy_migrations'::regclass) "
        "AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum",
        "SELECT conrelid::regclass::text,conname,contype,pg_get_constraintdef(oid),convalidated FROM pg_constraint "
        "WHERE conrelid IN ('j01_project_grants'::regclass,'k04_project_policy_migrations'::regclass) "
        "ORDER BY conrelid::regclass::text,conname",
        "SELECT tgname,tgtype,tgenabled,tgdeferrable,tginitdeferred,tgfoid::regprocedure::text,pg_get_triggerdef(oid) "
        "FROM pg_trigger WHERE tgrelid='j01_project_grants'::regclass AND NOT tgisinternal ORDER BY tgname",
        "SELECT p.proname,p.prosrc,p.proconfig,p.prosecdef,p.provolatile,l.lanname FROM pg_proc p "
        "JOIN pg_language l ON l.oid=p.prolang WHERE p.oid='j01_project_grant_revision_guard()'::regprocedure",
        "SELECT c.relname,i.indisunique,i.indisprimary,i.indisvalid,i.indisready,pg_get_indexdef(i.indexrelid) "
        "FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid WHERE i.indrelid IN "
        "('j01_project_grants'::regclass,'k04_project_policy_migrations'::regclass) "
        + ("AND i.indexrelid<>to_regclass('k10_project_grants_reader') " if indexed else "") +
        "ORDER BY c.relname,pg_get_indexdef(i.indexrelid)",
    )
    return content_hasher(json.dumps([await _rows(conn, query) for query in queries],
                                    sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def _preflight(conn: Connection) -> None:
    await conn.execute("SELECT workspace_id,project_id,creator_id,revision FROM k05_projects LIMIT 0")
    await conn.execute("SELECT workspace_id,project_id,record_type FROM k05_records LIMIT 0")
    await conn.execute("SELECT workspace_id,user_id,role,active,revision FROM j01_memberships LIMIT 0")
    await conn.execute("SELECT version,checksum,signature FROM k05_persistence_migrations LIMIT 0")


async def verify(conn: Connection, schema: str | None = None) -> bool:
    """Verify this extension only; canonical K-05 verification remains its owner's job."""
    await _context(conn, schema)
    exists = await (await conn.execute("SELECT to_regclass('k04_project_policy_migrations')")).fetchone()
    if exists is None or exists[0] is None:
        return False
    checksum = content_hasher(Path(__file__).with_name("project_policy_schema.sql").read_bytes()).hexdigest()
    row = await (await conn.execute("SELECT version,checksum,signature FROM k04_project_policy_migrations")).fetchone()
    count = await (await conn.execute("SELECT count(*) FROM k04_project_policy_migrations")).fetchone()
    try:
        return (count == (1,) and row is not None and row[:2] == (1, checksum)
                and row[2] == await _signature(conn))
    except ReadIndexesRejected:
        return False


async def migrate(conn: Connection, schema: str) -> str:
    """Explicit transaction-owned extension; commit success, roll back any failure."""
    schema = await _context(conn, schema)
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("aletheia-k04-project-policy:" + schema,))
    await _preflight(conn)
    await conn.execute("LOCK TABLE k05_projects,j01_memberships IN SHARE ROW EXCLUSIVE MODE")
    exists = await (await conn.execute("SELECT to_regclass('k04_project_policy_migrations'),to_regclass('j01_project_grants')")).fetchone()
    if exists is not None and exists[0] is not None:
        if not await verify(conn, schema):
            raise ProjectPolicyMigrationRejected()
        await install_if_possible(conn, schema)
        return "unchanged"
    if exists is None or exists[1] is not None:
        raise ProjectPolicyMigrationRejected()
    ddl = Path(__file__).with_name("project_policy_schema.sql").read_bytes()
    await conn.execute(ddl.decode("utf-8"))
    await conn.execute("CREATE TABLE " + _LEDGER + "(version integer PRIMARY KEY CHECK(version>0),"
        "checksum text NOT NULL CHECK(checksum~'^[0-9a-f]{64}$'),"
        "signature text NOT NULL CHECK(signature~'^[0-9a-f]{64}$'),"
        "applied_at timestamptz NOT NULL DEFAULT clock_timestamp())")
    await install_if_possible(conn, schema)
    await conn.execute("INSERT INTO k04_project_policy_migrations(version,checksum,signature) VALUES(1,%s,%s)",
                       (content_hasher(ddl).hexdigest(), await _signature(conn)))
    return "created"
