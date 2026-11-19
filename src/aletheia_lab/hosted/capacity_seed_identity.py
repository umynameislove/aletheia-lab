"""Synthetic catalog identity bootstrap and overlapping workspace memberships."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr

from aletheia_lab.hosted_auth.boundary import new_opaque_secret
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.sessions import credential_digest

from .capacity_seed_models import ROLES, SeedPlan, SeedRejected
from .upload_analysis import WINDOW_OBSERVATION_MODEL


async def _session(
    conn: AsyncConnection, user: str, tokens: CsrfTokens, plan: SeedPlan
) -> tuple[SecretStr, str, str, str, str]:
    context = "k17ctx_" + uuid4().hex
    raw = new_opaque_secret()
    proof = tokens.issue(context)
    digest = credential_digest(raw)
    inserted = await conn.execute(
        "INSERT INTO j01_sessions(context_id,user_id,csrf_token,state,issued_at,rotated_at,last_seen_at,idle_expires_at,absolute_expires_at,session_epoch,generation) "
        "VALUES(%s,%s,%s,'active',statement_timestamp(),statement_timestamp(),statement_timestamp(),statement_timestamp()+%s*interval '1 second',statement_timestamp()+%s*interval '1 second',0,0) RETURNING absolute_expires_at",
        (context, user, proof.get_secret_value(), plan.idle_seconds, plan.absolute_seconds),
    )
    expiry = await inserted.fetchone()
    if expiry is None or not isinstance(expiry[0], datetime) or expiry[0].utcoffset() is None:
        raise SeedRejected()
    await conn.execute(
        "INSERT INTO j01_session_credentials VALUES(%s,%s,'active',0)",
        (digest.get_secret_value(), context),
    )
    return (
        digest,
        context,
        raw.get_secret_value(),
        proof.get_secret_value(),
        expiry[0].astimezone(UTC).isoformat(),
    )


async def _bootstrap(
    pool: AsyncConnectionPool, plan: SeedPlan, tokens: CsrfTokens, workspace_index: int
) -> tuple[SecretStr, str]:
    workspace = plan.workspace(workspace_index)
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO j01_workspaces(workspace_id,name) VALUES(%s,%s)",
            (workspace, "Synthetic K17 workspace"),
        )
        first = workspace_index * plan.projects_per_workspace * len(ROLES)
        for offset in range(plan.projects_per_workspace * len(ROLES)):
            user = plan.user(first + offset)
            await conn.execute(
                "INSERT INTO hosted_identity_bindings(issuer,subject,user_id,display_name,email,email_verified,enabled,account_epoch,onboarding) "
                "VALUES('https://k17-synthetic.example.invalid',%s,%s,'Synthetic capacity actor',%s,true,true,0,'ready')",
                (user, user, user + "@example.invalid"),
            )
            await conn.execute(
                "INSERT INTO j01_memberships(workspace_id,user_id,role,active,revision) VALUES(%s,%s,%s,true,1)",
                (workspace, user, ROLES[offset % len(ROLES)]),
            )
        digest, context, _, _, _ = await _session(conn, plan.user(first), tokens, plan)
    return digest, context


async def _refresh_seed_operator(
    conn: AsyncConnection, plan: SeedPlan, tokens: CsrfTokens, workspace_index: int, owner: SecretStr, context: str
) -> tuple[SecretStr, str]:
    if plan.analysis_model != WINDOW_OBSERVATION_MODEL:
        return owner, context
    active = await (await conn.execute(
        "UPDATE j01_sessions SET last_seen_at=statement_timestamp(), "
        "idle_expires_at=LEAST(absolute_expires_at,statement_timestamp()+%s*interval '1 second') "
        "WHERE context_id=%s AND state='active' AND idle_expires_at>statement_timestamp() "
        "AND absolute_expires_at>statement_timestamp()+interval '5 seconds' RETURNING context_id",
        (plan.idle_seconds, context),
    )).fetchone()
    if active is not None:
        return owner, context
    await conn.execute("UPDATE j01_sessions SET state='revoked' WHERE context_id=%s", (context,))
    first = workspace_index * plan.projects_per_workspace * len(ROLES)
    new_digest, new_context, _, _, _ = await _session(conn, plan.user(first), tokens, plan)
    return new_digest, new_context


async def _source_operator(
    pool: AsyncConnectionPool, plan: SeedPlan, tokens: CsrfTokens, workspace_index: int, owner: SecretStr, context: str
) -> tuple[SecretStr, str]:
    # Source admission owns its transaction and needs a committed session.
    if plan.analysis_model != WINDOW_OBSERVATION_MODEL:
        return owner, context
    async with pool.connection() as conn:
        return await _refresh_seed_operator(conn, plan, tokens, workspace_index, owner, context)


async def _populate_shared_workspaces(pool: AsyncConnectionPool, plan: SeedPlan) -> dict[str, int]:
    if plan.analysis_model != WINDOW_OBSERVATION_MODEL or plan.principals < 500:
        return {}
    counts = {}
    locals_per_workspace = plan.projects_per_workspace * len(ROLES)
    async with pool.connection() as conn:
        for index, label, total in ((0, "shared", 50), (1, "hot", 500)):
            workspace = plan.workspace(index)
            first_local = index * locals_per_workspace
            foreign = [plan.user(i) for i in range(total) if not first_local <= i < first_local + locals_per_workspace]
            await conn.execute(
                "INSERT INTO j01_memberships(workspace_id,user_id,role,active,revision) "
                "SELECT %s,user_id,'viewer',true,1 FROM unnest(%s::text[]) AS cohort(user_id)",
                (workspace, foreign),
            )
            measured = await (await conn.execute(
                "SELECT count(*) FROM j01_memberships WHERE workspace_id=%s AND active", (workspace,)
            )).fetchone()
            if measured != (total,):
                raise SeedRejected()
            counts[label] = total
    return counts
