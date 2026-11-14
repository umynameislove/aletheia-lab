"""Explicit additive workspace migration; never runs at application startup."""

from __future__ import annotations

import json
import re
from pathlib import Path

from aletheia_lab.hosted_auth.postgres_sessions import Connection
from aletheia_lab.project.identity import content_hasher

_SCHEMA = re.compile(r"[a-z][a-z0-9_]{0,62}")


class WorkspaceMigrationRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Workspace migration preconditions were not satisfied.")


async def _signature(connection: Connection) -> str:
    """Fingerprint installed policy structure, excluding mutable application rows."""
    queries = (
        "SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,"
        "pg_get_expr(d.adbin,d.adrelid),c.relkind,c.relrowsecurity,c.relforcerowsecurity "
        "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid LEFT JOIN pg_attrdef d "
        "ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE a.attrelid IN "
        "('j01_source_grants'::regclass,'j01_invitations'::regclass) AND a.attnum>0 AND NOT a.attisdropped "
        "ORDER BY c.relname,a.attnum",
        "SELECT conrelid::regclass::text,conname,contype,pg_get_constraintdef(oid),convalidated "
        "FROM pg_constraint WHERE conrelid IN ('j01_source_grants'::regclass,'j01_invitations'::regclass) "
        "OR (conrelid='j01_sources'::regclass AND conname='j01_sources_workspace_source') "
        "OR (conrelid='j01_upload_snapshots'::regclass AND conname='j01_snapshots_source_scope') "
        "ORDER BY conrelid::regclass::text,conname",
        "SELECT tgrelid::regclass::text,tgname,tgtype,tgenabled,tgdeferrable,tginitdeferred,"
        "tgfoid::regprocedure::text,pg_get_triggerdef(oid) FROM pg_trigger WHERE tgrelid IN "
        "('j01_workspaces'::regclass,'j01_memberships'::regclass) AND tgname IN "
        "('j01_memberships_owner_guard','j01_workspaces_owner_guard','j01_memberships_revision_guard') ORDER BY tgname",
        "SELECT p.proname,p.prosrc,p.proconfig,p.prosecdef,p.provolatile,l.lanname FROM pg_proc p "
        "JOIN pg_language l ON l.oid=p.prolang WHERE p.oid IN "
        "('j01_workspace_owner_guard()'::regprocedure,'j01_membership_revision_guard()'::regprocedure) ORDER BY p.proname",
        "SELECT c.relname,i.indisunique,i.indisvalid,i.indisready,pg_get_indexdef(i.indexrelid) "
        "FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid WHERE i.indrelid IN "
        "('j01_source_grants'::regclass,'j01_invitations'::regclass) ORDER BY c.relname,pg_get_indexdef(i.indexrelid)",
    )
    facts = []
    for query in queries:
        cursor = await connection.execute(query)
        rows = []
        while (row := await cursor.fetchone()) is not None:
            rows.append(row)
        facts.append(rows)
    return content_hasher(json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def verify(connection: Connection) -> bool:
    checksum = content_hasher(Path(__file__).with_name("workspace_schema.sql").read_bytes()).hexdigest()
    row = await (await connection.execute("SELECT version,checksum,signature FROM k04_workspace_migrations")).fetchone()
    count = await (await connection.execute("SELECT count(*) FROM k04_workspace_migrations")).fetchone()
    return (count == (1,) and row is not None and row[:2] == (1, checksum)
            and row[2] == await _signature(connection))


async def _preflight(connection: Connection) -> None:
    await connection.execute("SELECT workspace_id,name FROM j01_workspaces LIMIT 0")
    await connection.execute("SELECT workspace_id,user_id,role,active,revision FROM j01_memberships LIMIT 0")
    await connection.execute("SELECT source_id,workspace_id FROM j01_sources LIMIT 0")
    await connection.execute("SELECT snapshot_id,workspace_id,source_id FROM j01_upload_snapshots LIMIT 0")
    ownerless = await (await connection.execute("SELECT 1 FROM j01_workspaces w WHERE NOT EXISTS "
        "(SELECT 1 FROM j01_memberships m JOIN hosted_identity_bindings a ON a.user_id=m.user_id "
        "WHERE m.workspace_id=w.workspace_id AND m.active AND m.role='owner' AND a.enabled AND a.email_verified) LIMIT 1")).fetchone()
    mismatch = await (await connection.execute("SELECT 1 FROM j01_upload_snapshots s LEFT JOIN j01_sources p "
        "ON p.source_id=s.source_id AND p.workspace_id=s.workspace_id WHERE p.source_id IS NULL LIMIT 1")).fetchone()
    if ownerless is not None or mismatch is not None:
        raise WorkspaceMigrationRejected()


async def migrate(connection: Connection, schema: str) -> str:
    """Apply within a caller-owned transaction, preserving existing rows.

    Caller must commit before reporting success; any error requires rollback.
    Ownerless or mismatched legacy data needs operator repair before adoption.
    """
    if (connection.autocommit or not isinstance(schema, str) or not _SCHEMA.fullmatch(schema)
            or schema in ("public", "information_schema") or schema.startswith("pg_")):
        raise WorkspaceMigrationRejected()
    exists = await (await connection.execute("SELECT nspname FROM pg_namespace WHERE nspname=%s", (schema,))).fetchone()
    if exists != (schema,):
        raise WorkspaceMigrationRejected()
    # The schema identifier has been strictly validated; never interpolate user SQL.
    await connection.execute('SET LOCAL search_path TO "' + schema + '",pg_catalog')
    await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("aletheia-k04-workspace-v1:" + schema,))
    await connection.execute("LOCK TABLE j01_workspaces,j01_memberships,j01_sources,j01_upload_snapshots "
                             "IN SHARE ROW EXCLUSIVE MODE")
    await _preflight(connection)
    raw_ddl = Path(__file__).with_name("workspace_schema.sql").read_bytes()
    ddl, checksum = raw_ddl.decode("utf-8"), content_hasher(raw_ddl).hexdigest()
    ledger = await (await connection.execute("SELECT to_regclass('k04_workspace_migrations')")).fetchone()
    if ledger is not None and ledger[0] is not None:
        if not await verify(connection):
            raise WorkspaceMigrationRejected()
        await connection.execute("SELECT workspace_id,source_id,user_id,actions,revision FROM j01_source_grants LIMIT 0")
        await connection.execute("SELECT invitation_id,workspace_id,user_id,creator_id,creator_revision,token_digest,"
                                 "role,expires_at,revoked,accepted_at FROM j01_invitations LIMIT 0")
        guards = await (await connection.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid IN "
            "('j01_workspaces'::regclass,'j01_memberships'::regclass) AND tgenabled='O' AND tgname IN "
            "('j01_memberships_owner_guard','j01_workspaces_owner_guard','j01_memberships_revision_guard')")).fetchone()
        scope = await (await connection.execute("SELECT count(*) FROM pg_constraint WHERE "
            "conrelid='j01_upload_snapshots'::regclass AND conname='j01_snapshots_source_scope' AND convalidated")).fetchone()
        if guards != (3,) or scope != (1,):
            raise WorkspaceMigrationRejected()
        return "unchanged"
    await connection.execute(ddl)
    await connection.execute("CREATE TABLE k04_workspace_migrations(version integer PRIMARY KEY CHECK(version>0),"
                             "checksum text NOT NULL CHECK(checksum ~ '^[0-9a-f]{64}$'),"
                             "signature text NOT NULL CHECK(signature ~ '^[0-9a-f]{64}$'),"
                             "applied_at timestamptz NOT NULL DEFAULT clock_timestamp())")
    await connection.execute("INSERT INTO k04_workspace_migrations(version,checksum,signature) VALUES (1,%s,%s)",
                             (checksum, await _signature(connection)))
    return "adopted"


migrate_workspace_schema = migrate
