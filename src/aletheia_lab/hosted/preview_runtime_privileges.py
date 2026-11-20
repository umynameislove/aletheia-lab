"""Explicit K-07 preview grants layered on the existing restricted runtime roles."""
from __future__ import annotations

from typing import Literal

from aletheia_lab.hosted_auth.postgres_sessions import Connection

from .persistence_migration import _context
from .preview_migration import verify
from .runtime_privileges import RuntimePrivilegesRejected, _identifier
from .upload_staging_migration import grant_upload_runtime


async def grant_preview_runtime(conn: Connection, schema: str, role: str, *,
                                profile: Literal["api", "worker"], audit: bool = False) -> None:
    """Caller commits; no role creation, source deletion or preview cleanup grant."""
    namespace, principal = _identifier(schema), _identifier(role)
    await _context(conn, schema)
    if not await verify(conn):
        raise RuntimePrivilegesRejected()
    await grant_upload_runtime(conn, schema, role, profile=profile, audit=audit)
    for table in ("j01_sources", "j01_upload_snapshots", "k07_import_previews", "k07_import_failures"):
        deleted = await (await conn.execute("SELECT has_table_privilege(%s,%s,'DELETE,TRUNCATE')",
                                           (role, schema + "." + table))).fetchone()
        if deleted != (False,):
            raise RuntimePrivilegesRejected()
    forbidden = [("k07_import_previews", "payload")]
    if profile == "api":
        forbidden.append(("k07_import_failures", "payload"))
        for table in ("k07_import_previews", "k07_import_failures"):
            inserted = await (await conn.execute("SELECT has_any_column_privilege(%s,%s,'INSERT')",
                                                (role, schema + "." + table))).fetchone()
            if inserted != (False,):
                raise RuntimePrivilegesRejected()
    for table, column in forbidden:
        updated = await (await conn.execute("SELECT has_column_privilege(%s,%s,%s,'UPDATE')",
                                           (role, schema + "." + table, column))).fetchone()
        if updated != (False,):
            raise RuntimePrivilegesRejected()
    await conn.execute(f"GRANT SELECT ON {namespace}.k07_import_previews,{namespace}.k07_import_failures,"
                       f"{namespace}.k07_preview_migrations TO {principal}")
    await conn.execute(f"GRANT UPDATE(preview_id) ON {namespace}.k07_import_previews TO {principal}")
    if profile == "worker":
        await conn.execute(f"GRANT INSERT ON {namespace}.k07_import_previews,{namespace}.k07_import_failures TO {principal}")
        await conn.execute(f"GRANT UPDATE(payload) ON {namespace}.k07_import_failures TO {principal}")
        # The immutable snapshot read in the preview publication guard uses FOR SHARE.
        await conn.execute(f"GRANT UPDATE(snapshot_id) ON {namespace}.j01_upload_snapshots TO {principal}")
