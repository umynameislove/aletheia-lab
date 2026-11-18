"""Safe numeric maintenance observations and explicit table maintenance.

Never exposes SQL text, DSNs, user bind values, or source payloads. Retention
and backup orchestration remain explicit operator/K-13/K-18 responsibilities.
"""
from __future__ import annotations

from typing import Any

from .persistence_migration import migration_schema

TABLES = (
    "k05_projects", "k05_records", "k05_objects", "k05_record_parents",
    "k05_object_references", "k05_object_leases", "k05_current_pointers",
    "k05_confirmations", "k05_preview_leases", "k05_imports", "j01_jobs",
)


async def _rows(conn: Any, query: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
    cursor = await conn.execute(query, params)
    return list(await cursor.fetchall())


async def observe(conn: Any, schema: str) -> dict[str, Any]:
    """Run on an operator-owned read-only transaction, without business writes."""
    schema = migration_schema(schema)
    current = await (await conn.execute("SELECT current_schema()" )).fetchone()
    if current != (schema,):
        raise ValueError("explicit matching maintenance schema required")
    database = await _rows(conn,
        "SELECT numbackends,xact_commit,xact_rollback,blks_read,blks_hit,deadlocks,temp_files,temp_bytes "
        "FROM pg_stat_database WHERE datname=current_database()")
    relations = await _rows(conn,
        "SELECT c.relname,pg_relation_size(c.oid),pg_indexes_size(c.oid),pg_total_relation_size(c.oid),"
        "s.n_live_tup,s.n_dead_tup,s.n_mod_since_analyze,s.last_vacuum,s.last_autovacuum,s.last_analyze,s.last_autoanalyze "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "LEFT JOIN pg_stat_user_tables s ON s.relid=c.oid WHERE n.nspname=%s AND c.relname=ANY(%s) "
        "ORDER BY c.relname", (schema, list(TABLES)))
    invalid_indexes = await _rows(conn,
        "SELECT c.relname,i.indisvalid,i.indisready FROM pg_index i "
        "JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND (NOT i.indisvalid OR NOT i.indisready) ORDER BY c.relname", (schema,))
    waits = await _rows(conn,
        "SELECT backend_type,state,wait_event_type,wait_event,count(*) FROM pg_stat_activity "
        "WHERE datname=current_database() GROUP BY backend_type,state,wait_event_type,wait_event")
    locks = await _rows(conn,
        "SELECT l.mode,l.granted,count(*) FROM pg_locks l JOIN pg_class c ON c.oid=l.relation "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s GROUP BY l.mode,l.granted", (schema,))
    wal = await _rows(conn, "SELECT wal_records,wal_fpi,wal_bytes,wal_buffers_full FROM pg_stat_wal")
    backlog = await _rows(conn,
        "SELECT state,count(*),extract(epoch FROM (clock_timestamp()-min(created_at))) "
        "FROM j01_jobs GROUP BY state")
    objects = await _rows(conn,
        "SELECT count(*),coalesce(sum(byte_size),0),count(*) FILTER(WHERE NOT EXISTS "
        "(SELECT 1 FROM k05_records r WHERE r.workspace_id=o.workspace_id AND r.object_sha256=o.sha256) "
        "AND NOT EXISTS (SELECT 1 FROM k05_object_references x WHERE x.workspace_id=o.workspace_id AND x.sha256=o.sha256)) "
        "FROM k05_objects o")
    leases = await _rows(conn,
        "SELECT count(*),count(*) FILTER(WHERE expires_at<=clock_timestamp()) FROM k05_object_leases")
    return {"format": "k05-maintenance/v1", "schema": schema,
            "database_scope": "database-wide counters include other clients; no per-run attribution",
            "wal_scope": "cluster-wide counters include other databases and clients; no per-run attribution",
            "database": database, "relations": relations, "invalid_indexes": invalid_indexes,
            "connection_waits": waits, "schema_locks": locks, "wal": wal,
            "jobs": backlog, "objects": objects, "leases": leases}


async def maintain(conn: Any, schema: str, *, vacuum: bool = False) -> None:
    """Maintenance only, not a migration/retention/GC; no VACUUM FULL or DDL."""
    from psycopg import sql

    schema = migration_schema(schema)
    if not conn.autocommit:
        raise ValueError("maintenance requires its own autocommit connection")
    await conn.execute("SET lock_timeout='1s'")
    await conn.execute("SET statement_timeout='30s'")
    try:
        for table in TABLES:
            command = "VACUUM (ANALYZE) {}.{}" if vacuum else "ANALYZE {}.{}"
            await conn.execute(sql.SQL(command).format(sql.Identifier(schema), sql.Identifier(table)))
    finally:
        await conn.execute("RESET lock_timeout")
        await conn.execute("RESET statement_timeout")