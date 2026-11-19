"""Persisted job budgets and public metadata, including explicit legacy unknowns."""
from __future__ import annotations

import json
from collections.abc import Mapping

from aletheia_lab.hosted_auth.postgres_sessions import Connection
from aletheia_lab.hosted_contract.job_page import Job

DEADLINE = "COALESCE((to_jsonb(j01_jobs)->>'deadline_at')::timestamptz,'infinity'::timestamptz)"
ATTEMPTS = "COALESCE((to_jsonb(j01_jobs)->>'attempt_count')::integer,0)"
MAX_ATTEMPTS = "COALESCE((to_jsonb(j01_jobs)->>'max_attempts')::integer,2147483647)"
CLAIM_BUDGET = f"({DEADLINE}>clock_timestamp() AND {ATTEMPTS}<{MAX_ATTEMPTS})"
PUBLICATION_BUDGET = f"({DEADLINE}>clock_timestamp() AND {ATTEMPTS}<={MAX_ATTEMPTS})"


async def expire_job(conn: Connection, workspace_id: str, job_id: str, *, execution: bool = False) -> bool:
    """Terminate this known job; an active final attempt retains its bounded lease."""
    from psycopg import sql

    if execution:
        row = await (await conn.execute(
            "UPDATE j01_jobs SET state=CASE WHEN state='cancel_requested' THEN 'cancelled' "
            "WHEN deadline_at<=clock_timestamp() THEN 'expired' ELSE 'failed' END, "
            "failure_kind=CASE WHEN state='cancel_requested' THEN 'cancelled' "
            "WHEN deadline_at<=clock_timestamp() THEN 'deadline' ELSE 'attempts_exhausted' END, "
            "result=NULL,lease_digest=NULL,lease_until=NULL WHERE workspace_id=%s AND job_id=%s AND "
            "state IN ('queued','running','cancel_requested') AND (deadline_at<=clock_timestamp() OR "
            "(state='cancel_requested' AND lease_until<=clock_timestamp()) OR "
            "(attempt_count>=max_attempts AND (state='queued' OR lease_until<=clock_timestamp()))) RETURNING job_id",
            (workspace_id, job_id),
        )).fetchone()
        return row is not None
    row = await (await conn.execute(
        sql.SQL(
            "UPDATE j01_jobs SET state='failed',result=NULL,lease_digest=NULL,lease_until=NULL WHERE workspace_id=%s "
            "AND job_id=%s AND state IN ('queued','running') AND ({}<=clock_timestamp() OR ({}>={} AND (state='queued'"
            ' OR lease_until<clock_timestamp()))) RETURNING job_id'
        ).format(sql.SQL(DEADLINE), sql.SQL(ATTEMPTS), sql.SQL(MAX_ATTEMPTS)).as_string(),
        (workspace_id, job_id),
    )).fetchone()
    return row is not None


def public_job(workspace_id: str, job_id: str, metadata: Mapping[str, object],
               *, retryable: bool | None) -> Job:
    count, maximum = metadata.get("attempt_count"), metadata.get("max_attempts")
    if (count is not None and (type(count) is not int or count < 0)
            or maximum is not None and (type(maximum) is not int or maximum < 1)):
        raise ValueError("Invalid persisted job budget.")
    value = {"schema_version": "product-api/v1-draft", "workspace_id": workspace_id, "job_id": job_id,
        "status": metadata.get("state"), "progress": None, "operation": metadata.get("operation"),
        "created_at": metadata.get("created_at"), "deadline_at": metadata.get("deadline_at"),
        "terminal_at": metadata.get("terminal_at"), "attempt": None if count == 0 else count,
        "max_attempts": maximum, "retryable": retryable,
        "output_url": f"/v1/workspaces/{workspace_id}/jobs/{job_id}/output" if metadata.get("state") == "succeeded" else None}
    return Job.model_validate_json(json.dumps(value, allow_nan=False))
