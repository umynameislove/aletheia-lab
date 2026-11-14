"""Check the actual pool principal/schema; this architecture uses repository scope."""
from __future__ import annotations

from aletheia_lab.hosted_auth.postgres_sessions import Pool

from .settings import StorageSettings

_RELATIONS = (
    "hosted_identity_bindings", "j01_sessions", "j01_session_credentials", "j01_workspaces",
    "j01_memberships", "j01_sources", "j01_upload_snapshots", "j01_jobs", "j01_source_grants",
    "k05_projects", "k05_records", "k05_objects", "j01_project_grants",
)


async def verify_runtime_binding(pool: Pool, storage: StorageSettings, *, strict: bool = False) -> bool:
    """A missing local legacy binding is permitted, never reported as a verified role."""
    schema, role = storage.expected_schema, storage.expected_database_role
    if schema is None or role is None:
        return schema is None and role is None and not strict
    try:
        async with pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT current_user,session_user,current_schema(),r.rolsuper,r.rolbypassrls,"
                "r.rolcreaterole,r.rolcreatedb,pg_has_role(current_user,n.nspowner,'MEMBER'),"
                "EXISTS(SELECT 1 FROM pg_class c WHERE c.relnamespace=n.oid AND c.relkind IN ('r','p','v','m','S') "
                "AND pg_has_role(current_user,c.relowner,'MEMBER')),"
                "has_schema_privilege(current_user,n.oid,'CREATE'),"
                "has_database_privilege(current_user,current_database(),'CREATE') "
                "FROM pg_roles r JOIN pg_namespace n ON n.nspname=%s WHERE r.rolname=current_user",
                (schema,),
            )).fetchone()
            if (row is None or len(row) != 11 or row[:3] != (role, role, schema)
                    or any(type(flag) is not bool or flag for flag in row[3:])):
                return False
            for relation in _RELATIONS:
                actual = await (await conn.execute(
                    "SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE c.oid=to_regclass(%s) AND c.relkind='r'", (relation,),
                )).fetchone()
                if actual != (schema,):
                    return False
        return True
    except Exception:
        return False
