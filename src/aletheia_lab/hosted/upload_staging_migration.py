"""Explicit additive K-07 staging migration, separate from K-05's ledger."""
from __future__ import annotations

import json
from typing import Literal

from aletheia_lab.hosted_auth.postgres_sessions import Connection
from aletheia_lab.project.identity import content_hasher

from .persistence_migration import _context, _exists, _shape
from .persistence_migration import verify as verify_persistence

DDL = """
CREATE TABLE k07_uploads (
 upload_id text PRIMARY KEY,workspace_id text NOT NULL,creator_id text NOT NULL,
 key_hash text NOT NULL CHECK(key_hash~'^[0-9a-f]{64}$'),
 request_hash text NOT NULL CHECK(request_hash~'^[0-9a-f]{64}$'),
 manifest jsonb NOT NULL CHECK(jsonb_typeof(manifest)='object'),
 byte_size bigint NOT NULL CHECK(byte_size BETWEEN 1 AND 209715200),
 sha256 text NOT NULL CHECK(sha256~'^[0-9a-f]{64}$'),
 state text NOT NULL DEFAULT 'active' CHECK(state IN ('active','ready','cancelled','expired')),
 source_id text,parent_source_id text,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL,terminal_at timestamptz,cleaned_at timestamptz,
 FOREIGN KEY(workspace_id,creator_id) REFERENCES j01_memberships(workspace_id,user_id),
 FOREIGN KEY(workspace_id,source_id) REFERENCES j01_sources(workspace_id,source_id),
 FOREIGN KEY(workspace_id,parent_source_id) REFERENCES j01_sources(workspace_id,source_id),
 UNIQUE(workspace_id,upload_id),UNIQUE(workspace_id,creator_id,key_hash),
 CHECK((state='ready')=(source_id IS NOT NULL)),CHECK(expires_at>created_at));
CREATE TABLE k07_upload_parts (
 workspace_id text NOT NULL,upload_id text NOT NULL,part_number integer NOT NULL CHECK(part_number BETWEEN 1 AND 512),
 byte_size integer NOT NULL CHECK(byte_size BETWEEN 1 AND 4194304),
 sha256 text NOT NULL CHECK(sha256~'^[0-9a-f]{64}$'),payload bytea NOT NULL,
 PRIMARY KEY(workspace_id,upload_id,part_number),
 FOREIGN KEY(workspace_id,upload_id) REFERENCES k07_uploads(workspace_id,upload_id),
 CHECK(octet_length(payload)=byte_size));
CREATE INDEX k07_uploads_quota ON k07_uploads(workspace_id,creator_id,state) WHERE cleaned_at IS NULL;
CREATE INDEX k07_uploads_cleanup ON k07_uploads(expires_at,upload_id) WHERE cleaned_at IS NULL;
CREATE TABLE k07_upload_migrations (
 version integer PRIMARY KEY CHECK(version=1),checksum text NOT NULL,signature text NOT NULL);
"""
TABLES = ("k07_uploads", "k07_upload_parts", "k07_upload_migrations")


class UploadMigrationRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Upload staging schema is unavailable or does not match the supported migration.")


async def _signature(conn: Connection, schema: str) -> str:
    facts = [(name, await _shape(conn, schema + "." + name)) for name in TABLES]
    return content_hasher(json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def verify(conn: Connection, schema: str) -> bool:
    schema = await _context(conn, schema)
    if not await _exists(conn, schema + ".k07_upload_migrations"):
        return False
    row = await (await conn.execute("SELECT version,checksum,signature FROM k07_upload_migrations")).fetchone()
    return row == (1, content_hasher(DDL.encode()).hexdigest(), await _signature(conn, schema))


async def migrate(conn: Connection, schema: str) -> str:
    schema = await _context(conn, schema)
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("aletheia-k07-staging:" + schema,))
    if await _exists(conn, schema + ".k07_upload_migrations"):
        if not await verify(conn, schema):
            raise UploadMigrationRejected()
        return "unchanged"
    if (not await verify_persistence(conn, schema)
            or any([await _exists(conn, schema + "." + name) for name in TABLES[:-1]])):
        raise UploadMigrationRejected()
    await conn.execute(DDL)
    await conn.execute("INSERT INTO k07_upload_migrations VALUES(1,%s,%s)",
                       (content_hasher(DDL.encode()).hexdigest(), await _signature(conn, schema)))
    return "created"


async def grant_upload_runtime(conn: Connection, schema: str, role: str, *, profile: Literal["api", "worker"],
                               audit: bool = False) -> None:
    """Extend the existing restricted runtime profile; no staging DDL authority."""
    from .runtime_privileges import _identifier, grant_runtime

    namespace, principal = _identifier(schema), _identifier(role)
    if not await verify(conn, schema):
        raise UploadMigrationRejected()
    await grant_runtime(conn, schema, role, profile=profile, audit=audit)
    if profile == "worker":
        for table in ("k07_uploads", "k07_upload_parts"):
            row = await (await conn.execute("SELECT has_table_privilege(%s,%s,'INSERT,UPDATE,DELETE')",
                                           (role, schema + "." + table))).fetchone()
            if row != (False,):
                raise UploadMigrationRejected()
    else:
        await conn.execute(f"GRANT SELECT,INSERT,UPDATE ON {namespace}.k07_uploads TO {principal}")
        await conn.execute(f"GRANT SELECT,INSERT,DELETE ON {namespace}.k07_upload_parts TO {principal}")
    await conn.execute(f"GRANT SELECT ON {namespace}.k07_upload_migrations TO {principal}")
