"""Independent additive commit-audit extension; existing migrations stay unchanged."""
from __future__ import annotations

import json
from pathlib import Path

from aletheia_lab.hosted.content_hashing import content_hasher
from aletheia_lab.hosted_auth.postgres_sessions import Connection

from .project_policy_migration import ProjectPolicyMigrationRejected, _context, _rows

ActionAuditMigrationRejected = ProjectPolicyMigrationRejected


async def _signature(conn: Connection, version: int = 2) -> str:
    # V1 is projected exactly so its frozen signature survives this additive V2.
    columns = (" AND NOT (a.attrelid='k06_action_audit'::regclass AND a.attname IN ('parent_id','subject_id'))"
               if version == 1 else "")
    constraints = (" AND conname NOT IN ('k06_audit_parent_id_v2','k06_audit_subject_id_v2')"
                   if version == 1 else "")
    queries = (
        "SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,"
        "pg_get_expr(d.adbin,d.adrelid),c.relkind,c.relrowsecurity,c.relforcerowsecurity "
        "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid LEFT JOIN pg_attrdef d "
        "ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE a.attrelid IN "
        "('k06_action_audit'::regclass,'k06_action_audit_migrations'::regclass) "
        "AND a.attnum>0 AND NOT a.attisdropped" + columns + " ORDER BY c.relname,a.attnum",
        "SELECT conrelid::regclass::text,conname,contype,pg_get_constraintdef(oid),convalidated FROM pg_constraint "
        "WHERE conrelid IN ('k06_action_audit'::regclass,'k06_action_audit_migrations'::regclass) "
        + constraints + " ORDER BY conrelid::regclass::text,conname",
        "SELECT tgname,tgtype,tgenabled,tgdeferrable,tginitdeferred,tgfoid::regprocedure::text,pg_get_triggerdef(oid) "
        "FROM pg_trigger WHERE tgrelid='k06_action_audit'::regclass AND NOT tgisinternal ORDER BY tgname",
        "SELECT p.proname,p.prosrc,p.proconfig,p.prosecdef,p.provolatile,l.lanname FROM pg_proc p "
        "JOIN pg_language l ON l.oid=p.prolang WHERE p.oid='k06_action_audit_immutable()'::regprocedure",
        "SELECT c.relname,i.indisunique,i.indisprimary,i.indisvalid,i.indisready,pg_get_indexdef(i.indexrelid) "
        "FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid WHERE i.indrelid IN "
        "('k06_action_audit'::regclass,'k06_action_audit_migrations'::regclass) "
        "ORDER BY c.relname,pg_get_indexdef(i.indexrelid)",
    )
    return content_hasher(json.dumps([await _rows(conn, query) for query in queries],
                                    sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def verify(conn: Connection, schema: str | None = None) -> bool:
    await _context(conn, schema)
    exists = await (await conn.execute("SELECT to_regclass('k06_action_audit_migrations'),"
        "to_regclass('k06_action_audit'),to_regprocedure('k06_action_audit_immutable()')")).fetchone()
    if exists is None or any(value is None for value in exists):
        return False
    rows = await _rows(conn, "SELECT version,checksum,signature FROM k06_action_audit_migrations ORDER BY version")
    if len(rows) != 2:
        return False
    for row, (version, name) in zip(rows, ((1, "action_audit_schema.sql"), (2, "action_audit_scope_schema.sql")), strict=True):
        checksum = content_hasher(Path(__file__).with_name(name).read_bytes()).hexdigest()
        if row[:2] != (version, checksum) or row[2] != await _signature(conn, version):
            return False
    return True


async def migrate(conn: Connection, schema: str) -> str:
    schema = await _context(conn, schema)
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("aletheia-k06-audit:" + schema,))
    await conn.execute("SELECT workspace_id,user_id FROM j01_memberships LIMIT 0")
    exists = await (await conn.execute("SELECT to_regclass('k06_action_audit_migrations'),"
        "to_regclass('k06_action_audit'),to_regprocedure('k06_action_audit_immutable()')")).fetchone()
    ddl = Path(__file__).with_name("action_audit_schema.sql").read_bytes()
    result = "created"
    if exists is not None and exists[0] is not None:
        rows = await _rows(conn, "SELECT version,checksum,signature FROM k06_action_audit_migrations ORDER BY version")
        if len(rows) == 1:
            if rows[0][:2] != (1, content_hasher(ddl).hexdigest()) or rows[0][2] != await _signature(conn, 1):
                raise ActionAuditMigrationRejected()
            result = "upgraded"
        elif await verify(conn, schema):
            return "unchanged"
        else:
            raise ActionAuditMigrationRejected()
    else:
        if exists is None or any(value is not None for value in exists[1:]):
            raise ActionAuditMigrationRejected()
        await conn.execute(ddl.decode("utf-8"))
        await conn.execute("CREATE TABLE k06_action_audit_migrations(version integer PRIMARY KEY CHECK(version>0),"
            "checksum text NOT NULL CHECK(checksum~'^[0-9a-f]{64}$'),"
            "signature text NOT NULL CHECK(signature~'^[0-9a-f]{64}$'),"
            "applied_at timestamptz NOT NULL DEFAULT clock_timestamp())")
        await conn.execute("INSERT INTO k06_action_audit_migrations(version,checksum,signature) VALUES(1,%s,%s)",
                           (content_hasher(ddl).hexdigest(), await _signature(conn, 1)))
    scope_ddl = Path(__file__).with_name("action_audit_scope_schema.sql").read_bytes()
    await conn.execute(scope_ddl.decode("utf-8"))
    await conn.execute("INSERT INTO k06_action_audit_migrations(version,checksum,signature) VALUES(2,%s,%s)",
                       (content_hasher(scope_ddl).hexdigest(), await _signature(conn, 2)))
    return result
