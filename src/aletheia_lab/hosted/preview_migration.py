"""Explicit additive preview migration; no startup DDL or historical checksum changes."""
from __future__ import annotations

import json
import re
from pathlib import Path

from aletheia_lab.hosted_auth.postgres_sessions import Connection
from aletheia_lab.project.identity import content_hasher


class PreviewMigrationRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Preview migration preconditions were not satisfied.")


async def _signature(conn: Connection) -> str:
    queries = (
        "SELECT a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid) "
        "FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
        "WHERE a.attrelid IN ('k07_import_previews'::regclass,'k07_import_failures'::regclass) "
        "AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attrelid::regclass::text,a.attnum",
        "SELECT conname,pg_get_constraintdef(oid),convalidated FROM pg_constraint "
        "WHERE conrelid IN ('k07_import_previews'::regclass,'k07_import_failures'::regclass) ORDER BY conname",
        "SELECT pg_get_indexdef(indexrelid),indisvalid,indisready FROM pg_index "
        "WHERE indrelid='k07_import_previews'::regclass ORDER BY indexrelid::regclass::text",
        "SELECT pg_get_indexdef(indexrelid),indisvalid,indisready FROM pg_index "
        "WHERE indexrelid=to_regclass('k07_jobs_preview_ref')",
        "SELECT tgname,tgenabled,pg_get_triggerdef(oid) FROM pg_trigger "
        "WHERE tgrelid='j01_jobs'::regclass AND tgname='k07_confirm_preview_guard'",
        "SELECT prosrc,proconfig,prosecdef FROM pg_proc WHERE oid='k07_confirm_preview_guard()'::regprocedure",
    )
    facts = []
    for query in queries:
        cursor = await conn.execute(query)
        rows = []
        while (row := await cursor.fetchone()) is not None:
            rows.append(row)
        facts.append(rows)
    return content_hasher(json.dumps(facts, separators=(",", ":")).encode()).hexdigest()


async def verify(conn: Connection, *, allow_previous: bool = False) -> bool:
    checksums = [content_hasher(Path(__file__).with_name(name).read_bytes()).hexdigest()
                 for name in ("preview_schema.sql", "preview_repeat_schema.sql")]
    rows = await conn.execute("SELECT version,checksum,signature FROM k07_preview_migrations ORDER BY version")
    first, second, third = await rows.fetchone(), await rows.fetchone(), await rows.fetchone()
    if first is None or first[:2] != (1, checksums[0]) or third is not None:
        return False
    if second is None:
        return allow_previous and first[2] == await _signature(conn)
    return second[:2] == (2, checksums[1]) and second[2] == await _signature(conn)


async def _upgrade(conn: Connection) -> None:
    raw = Path(__file__).with_name("preview_repeat_schema.sql").read_bytes()
    await conn.execute(raw.decode("utf-8"))
    await conn.execute("INSERT INTO k07_preview_migrations(version,checksum,signature) VALUES(2,%s,%s)",
                       (content_hasher(raw).hexdigest(), await _signature(conn)))


async def migrate(conn: Connection, schema: str) -> str:
    """Install in the named schema using the caller's transaction; caller commits."""
    if (conn.autocommit or not isinstance(schema, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema)
            or schema in ("public", "information_schema") or schema.startswith("pg_")):
        raise PreviewMigrationRejected()
    exists = await (await conn.execute("SELECT nspname FROM pg_namespace WHERE nspname=%s", (schema,))).fetchone()
    if exists != (schema,):
        raise PreviewMigrationRejected()
    await conn.execute('SET LOCAL search_path TO "' + schema + '",pg_catalog')
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("aletheia-k07-preview-v1:" + schema,))
    await conn.execute("LOCK TABLE j01_sources,j01_upload_snapshots,j01_jobs IN SHARE ROW EXCLUSIVE MODE")
    await conn.execute("SELECT source_id,workspace_id,creator_id,sha256 FROM j01_sources LIMIT 0")
    await conn.execute("SELECT job_id,workspace_id,creator_id,request,state,result FROM j01_jobs LIMIT 0")
    await conn.execute("SELECT snapshot_id,workspace_id,source_id,payload FROM j01_upload_snapshots LIMIT 0")
    installed = await (await conn.execute("SELECT to_regclass('k07_preview_migrations')")).fetchone()
    if installed is not None and installed[0] is not None:
        if await verify(conn):
            return "unchanged"
        if not await verify(conn, allow_previous=True):
            raise PreviewMigrationRejected()
        await _upgrade(conn)
        return "upgraded"
    raw = Path(__file__).with_name("preview_schema.sql").read_bytes()
    await conn.execute(raw.decode("utf-8"))
    await conn.execute("CREATE TABLE k07_preview_migrations(version integer PRIMARY KEY CHECK(version>0),"
        "checksum text NOT NULL CHECK(checksum ~ '^[0-9a-f]{64}$'),"
        "signature text NOT NULL CHECK(signature ~ '^[0-9a-f]{64}$'),"
        "applied_at timestamptz NOT NULL DEFAULT clock_timestamp())")
    await conn.execute("INSERT INTO k07_preview_migrations(version,checksum,signature) VALUES(1,%s,%s)",
                       (content_hasher(raw).hexdigest(), await _signature(conn)))
    await _upgrade(conn)
    return "adopted"


migrate_preview_schema = migrate
