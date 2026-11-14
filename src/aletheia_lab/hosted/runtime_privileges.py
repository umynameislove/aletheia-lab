"""Explicit operator grants for existing API/worker roles; never startup DDL.

Database principals run trusted repository code. These grants constrain database
ownership/DDL and sensitive columns; they do not replace per-actor authorization.
"""
from __future__ import annotations

import re
from typing import Literal, cast

from aletheia_lab.hosted_auth.postgres_sessions import Connection

from .action_audit_migration import verify as verify_action_audit

_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")
_COMMON_READ = (
    "hosted_identity_bindings", "j01_sessions", "j01_session_credentials", "j01_workspaces", "j01_memberships",
    "j01_sources", "j01_upload_snapshots", "j01_jobs", "j01_source_grants", "j01_project_grants",
    "k05_projects", "k05_records", "k05_objects", "k05_record_parents", "k05_current_pointers",
    "k05_object_references", "k05_object_leases",
)
_API_READ = ("j01_invitations", "j01_login_browsers", "j01_login_claims", "j01_fresh_login_browsers",
             "j01_fresh_login_intents", "k03_auth_admission", "k05_confirmations", "k05_preview_leases", "j01_annotations", "k02_exports")
_LEDGERS = ("k03_auth_admission_migrations", "k04_workspace_migrations", "k04_project_policy_migrations",
            "k04_review_migrations", "k05_persistence_migrations", "k10_read_indexes")
_JOB_INSERT = ("job_id", "workspace_id", "creator_id", "credential_digest", "operation", "key_hash",
               "request_hash", "request", "request_id", "state", "retry_of_job_id", "canonical_project_id", "parent_result_id",
               "deadline_at", "attempt_count", "created_at")
_PUBLICATION_INSERT = {
    "k09_job_attempts": ("workspace_id", "job_id", "epoch", "started_at", "heartbeat_at", "lease_until", "finished_at", "outcome", "failure_kind"),
    "k09_queue_turns": ("workspace_id", "operation", "last_claim_at"),
    "k10_catalog_revisions": ("workspace_id", "revision"),
    "k10_result_views": ("workspace_id", "result_id", "projection_version", "project_id", "snapshot_id", "canonical_sha256", "projection_sha256", "claim_count", "evidence_count", "node_count", "edge_count", "header", "header_sha256"),
    "k10_result_items": ("workspace_id", "result_id", "projection_version", "kind", "item_id", "payload", "sha256", "source_id", "target_id", "search_text"),
    "k10_evidence_links": ("workspace_id", "result_id", "projection_version", "claim_id", "evidence_id", "relation"),
    "j01_upload_snapshots": ("snapshot_id", "workspace_id", "source_id", "payload", "canonical_record_id"),
    "k05_projects": ("workspace_id", "project_id", "creator_id", "revision", "provenance_kind"),
    "k05_current_pointers": ("workspace_id", "project_id", "pointer_name", "record_id", "revision"),
    "k05_objects": ("workspace_id", "sha256", "byte_size", "media_type", "schema_version"),
    "k05_records": ("workspace_id", "record_id", "project_id", "record_type", "schema_version", "canonical_sha256",
                    "object_sha256", "snapshot_id", "parent_result_id", "source_id"),
    "k05_record_parents": ("workspace_id", "project_id", "record_id", "relation", "parent_id"),
    "k05_object_references": ("workspace_id", "record_id", "sha256"),
    "k05_object_leases": ("workspace_id", "sha256", "lease_id", "expires_at"),
}
_API_INSERT = {
    **_PUBLICATION_INSERT,
    "k02_exports": ("workspace_id", "export_id", "project_id", "creator_id", "result_id", "format",
                    "object_sha256", "request_key_hash", "expires_at"),
    "j01_workspaces": ("workspace_id", "name"),
    "j01_memberships": ("workspace_id", "user_id", "role", "active", "revision"),
    "j01_sources": ("source_id", "workspace_id", "creator_id", "request_key_hash", "sha256", "payload", "manifest"),
    "j01_jobs": _JOB_INSERT,
    "j01_source_grants": ("workspace_id", "source_id", "user_id", "actions", "revision"),
    "j01_project_grants": ("workspace_id", "project_id", "user_id", "actions", "revision"),
    "j01_invitations": ("invitation_id", "workspace_id", "user_id", "role", "creator_id", "creator_revision", "token_digest", "expires_at"),
    "j01_sessions": ("context_id", "user_id", "csrf_token", "state", "issued_at", "last_seen_at", "rotated_at",
                     "idle_expires_at", "absolute_expires_at", "generation", "session_epoch"),
    "j01_session_credentials": ("digest", "context_id", "state", "generation"),
    "j01_login_browsers": ("browser_digest", "epoch", "latest_intent"),
    "j01_login_claims": ("intent_id", "browser_digest", "state_digest", "browser_epoch", "expires_at", "phase", "sealed_proof"),
    "j01_fresh_login_browsers": ("browser_digest", "epoch", "latest_intent", "current_context"),
    "j01_fresh_login_intents": ("intent_id", "browser_digest", "state_digest", "issuer", "client_id", "redirect_uri",
                               "sealed_proof", "created_at", "expires_at", "browser_epoch", "return_to", "reauthenticate",
                               "phase", "previous_context_id"),
    "k03_auth_admission": ("namespace", "operation", "global_limit", "subject_limit", "window_seconds",
                           "window_number", "last_seen", "attempts", "subjects"),
    "k05_projects": ("workspace_id", "project_id", "creator_id", "revision", "provenance_kind"),
    "k05_current_pointers": ("workspace_id", "project_id", "pointer_name", "record_id", "revision"),
    "k05_confirmations": ("workspace_id", "preview_id", "project_id", "project_state_record_id"),
    "k05_preview_leases": ("workspace_id", "preview_id", "leased_at", "canonical_sha256"),
    "j01_annotations": ("workspace_id", "project_id", "result_id", "annotation_id", "revision", "author_id", "body", "status"),
}
# Row-lock queries require UPDATE on at least one column. Identity/role/grant
# authority remains unwritable by the worker; immutable/FK guards protect keys.
_LOCK_UPDATE = {
    "k09_job_attempts": ("heartbeat_at", "lease_until", "finished_at", "outcome", "failure_kind"),
    "k09_queue_turns": ("last_claim_at",),
    "k10_catalog_revisions": ("revision",),
    "hosted_identity_bindings": ("user_id",), "j01_sessions": ("generation",),
    "j01_session_credentials": ("generation",), "j01_workspaces": ("workspace_id",),
    "j01_memberships": ("revision",), "j01_sources": ("source_id",),
    "j01_source_grants": ("revision",), "j01_project_grants": ("revision",),
    "k05_projects": ("revision",), "k05_records": ("record_id",),
    "k05_objects": ("sha256",),
    "j01_upload_snapshots": ("snapshot_id", "canonical_record_id"),
    "k05_current_pointers": ("record_id", "revision"),
}
_JOB_UPDATE = ("state", "lease_digest", "lease_until", "result", "canonical_result_id", "failure_kind", "available_at")
_API_UPDATE = {
    **_LOCK_UPDATE,
    "hosted_identity_bindings": ("account_epoch", "logout_all_at", "onboarding", "last_workspace_id"),
    "j01_sessions": ("state", "generation", "rotated_at", "last_seen_at", "idle_expires_at"),
    "j01_session_credentials": ("state",),
    "j01_workspaces": ("name", "settings_revision", "maximum_upload_bytes", "maximum_workspace_bytes"),
    "k05_projects": ("revision", "display_name", "deletion_revision", "deletion_tombstone_id", "deleted_by"),
    "j01_memberships": ("role", "active", "revision"),
    "j01_source_grants": ("actions", "revision"), "j01_project_grants": ("actions", "revision"),
    "j01_invitations": ("revoked", "accepted_at"), "j01_jobs": _JOB_UPDATE,
    "j01_login_browsers": ("epoch", "latest_intent"), "j01_login_claims": ("phase", "claim_digest"),
    "j01_fresh_login_browsers": ("epoch", "latest_intent", "current_context"),
    "j01_fresh_login_intents": ("phase", "claim_digest"),
    "k03_auth_admission": ("window_number", "last_seen", "attempts", "subjects"),
    "k05_current_pointers": ("record_id", "revision"),
}


class RuntimePrivilegesRejected(PermissionError):
    def __init__(self) -> None:
        super().__init__("Runtime privilege preconditions were not satisfied.")


def _identifier(value: str) -> str:
    if (not isinstance(value, str) or _NAME.fullmatch(value) is None or value.startswith("pg_")
            or value in ("public", "information_schema")):
        raise RuntimePrivilegesRejected()
    return '"' + value + '"'


async def grant_runtime(conn: Connection, schema: str, role: str, *, profile: Literal["api", "worker"],
                        audit: bool = False) -> None:
    """Grant only installed known relations to a pre-existing unprivileged LOGIN.

    Caller must commit this explicit operator transaction. Provisioning identities,
    migrating schema and creating/altering roles remain separate operator actions.
    """
    namespace, principal = _identifier(schema), _identifier(role)
    if conn.autocommit or profile not in ("api", "worker") or type(audit) is not bool:
        raise RuntimePrivilegesRejected()
    owner = await (await conn.execute("SELECT current_user,pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=%s",
                                     (schema,))).fetchone()
    flags = await (await conn.execute("SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolinherit,rolcanlogin,"
        "EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid),"
        "has_database_privilege(r.oid,current_database(),'CREATE') FROM pg_roles r WHERE rolname=%s", (role,))).fetchone()
    if (owner is None or owner[0] != owner[1] or owner[0] == role
            or flags != (False, False, False, False, False, True, False, False)):
        raise RuntimePrivilegesRejected()
    if audit and not await verify_action_audit(conn, schema):
        raise RuntimePrivilegesRejected()
    selected = _COMMON_READ + _LEDGERS + (_API_READ if profile == "api" else ())
    selected += ('k09_job_attempts', 'k09_queue_turns', 'k10_catalog_revisions',
                 'k10_result_views', 'k10_result_items', 'k10_evidence_links',
                 'k13_retention_policy', 'k13_deletions', 'k13_project_objects')
    if audit:
        selected += ("k06_action_audit", "k06_action_audit_migrations")
    inserts = _API_INSERT if profile == "api" else _PUBLICATION_INSERT
    if audit:
        inserts = {**inserts, "k06_action_audit": ("audit_id", "request_id", "operation_id", "actor_id",
            "workspace_id", "action", "effect_id", "revision", "job_id", "parent_id", "subject_id")}
    updates = _API_UPDATE if profile == "api" else {**_LOCK_UPDATE, "j01_jobs": _JOB_UPDATE}
    known = sorted(set(selected) | set(inserts) | set(updates))
    relations = await _installed_relations(conn, schema, known, cast(str, owner[0]))
    ddl = await (await conn.execute("SELECT has_schema_privilege(%s,n.oid,'CREATE'),"
        "pg_has_role(%s,n.nspowner,'MEMBER') FROM pg_namespace n WHERE n.nspname=%s", (role, role, schema))).fetchone()
    if ddl != (False, False):
        raise RuntimePrivilegesRejected()
    await _reject_existing_write_grants(conn, schema, role, profile)
    await conn.execute("GRANT USAGE ON SCHEMA " + namespace + " TO " + principal)
    for table in selected:
        if table in relations:
            await conn.execute('GRANT SELECT ON ' + namespace + '."' + table + '" TO ' + principal)
    await _grant_write_columns(conn, schema, namespace, principal, relations, inserts, updates)
    await conn.execute('GRANT DELETE ON ' + namespace + '."k05_object_leases" TO ' + principal)
async def _reject_existing_write_grants(conn: Connection, schema: str, role: str,
                                       profile: Literal["api", "worker"]) -> None:
    forbidden_columns = [("hosted_identity_bindings", column) for column in ("enabled", "email_verified", "issuer", "subject")]
    forbidden_inserts = ["hosted_identity_bindings"]
    if profile == "worker":
        forbidden_columns += [("j01_memberships", "role"), ("j01_memberships", "active"),
                              ("j01_source_grants", "actions"), ("j01_project_grants", "actions")]
        forbidden_inserts += ["j01_memberships", "j01_source_grants", "j01_project_grants", "j01_session_credentials"]
    for table, column in forbidden_columns:
        allowed = await (await conn.execute("SELECT has_column_privilege(%s,%s,%s,'UPDATE')",
                                            (role, schema + "." + table, column))).fetchone()
        if allowed != (False,):
            raise RuntimePrivilegesRejected()
    for table in forbidden_inserts:
        allowed = await (await conn.execute("SELECT has_any_column_privilege(%s,%s,'INSERT')",
                                            (role, schema + "." + table))).fetchone()
        if allowed != (False,):
            raise RuntimePrivilegesRejected()

async def _grant_write_columns(conn: Connection, schema: str, namespace: str, principal: str,
                               relations: set[str], inserts: dict[str, tuple[str, ...]],
                               updates: dict[str, tuple[str, ...]]) -> None:
    for permission, mapping in (("INSERT", inserts), ("UPDATE", updates)):
        for table, columns in mapping.items():
            if table not in relations:
                continue
            # Version-specific optional columns are granted only when installed.
            cursor = await conn.execute("SELECT attname FROM pg_attribute WHERE attrelid=to_regclass(%s) "
                "AND attnum>0 AND NOT attisdropped", (schema + "." + table,))
            existing: set[str] = set()
            while (row := await cursor.fetchone()) is not None:
                existing.add(str(row[0]))
            names = ','.join('"' + column + '"' for column in columns if column in existing)
            if names:
                await conn.execute("GRANT " + permission + " (" + names + ') ON ' + namespace + '."' + table + '" TO ' + principal)

async def _installed_relations(conn: Connection, schema: str, known: list[str],
                               owner: str) -> set[str]:
    cursor = await conn.execute("SELECT c.relname,c.relkind,pg_get_userbyid(c.relowner) FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname=ANY(%s)", (schema, known))
    relations: set[str] = set()
    while (row := await cursor.fetchone()) is not None:
        if len(row) != 3 or not isinstance(row[0], str) or row[1] != "r" or row[2] != owner:
            raise RuntimePrivilegesRejected()
        relations.add(row[0])
    if not set(_COMMON_READ) <= relations:
        raise RuntimePrivilegesRejected()
    return relations

