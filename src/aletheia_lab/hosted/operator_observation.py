"""Explicit read-only operator observations; no public endpoint, SQL text, or payloads."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any

from .persistence_migration import migration_schema

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_OPERATIONS = ("preview", "confirm", "analyze", "follow_up", "refresh")
_STATES = ("queued", "running", "cancel_requested")


async def _boundary(conn: Any, schema: str) -> None:
    schema = migration_schema(schema)
    row = await (
        await conn.execute("SELECT current_schema(),current_setting('transaction_read_only')")
    ).fetchone()
    if row != (schema, "on"):
        raise ValueError("An explicit matching read-only operator transaction is required.")
    await conn.execute("SET LOCAL statement_timeout='1s'")
    await conn.execute("SET LOCAL lock_timeout='250ms'")


def _number(value: object) -> float:
    result = float(value)  # type: ignore[arg-type]
    if not math.isfinite(result) or result < 0:
        raise ValueError("Finite nonnegative observation required.")
    return result


async def operator_snapshot(conn: Any, schema: str, *, maximum_jobs: int = 100) -> dict[str, Any]:
    """At most1000+1 rows per fixed job stratum; never counts all payload tables."""
    if type(maximum_jobs) is not int or not 1 <= maximum_jobs <= 1000:
        raise ValueError("Bounded operator sample required.")
    await _boundary(conn, schema)
    samples: list[dict[str, Any]] = []
    for operation in _OPERATIONS:
        for state in _STATES:
            rows = await (
                await conn.execute(
                    "SELECT EXTRACT(EPOCH FROM (clock_timestamp()-created_at)) FROM j01_jobs "
                    "WHERE operation=%s AND state=%s ORDER BY created_at,job_id LIMIT %s",
                    (operation, state, maximum_jobs + 1),
                )
            ).fetchall()
            samples.append(
                {
                    "operation": operation,
                    "state": state,
                    "sampled_rows": min(len(rows), maximum_jobs),
                    "truncated": len(rows) > maximum_jobs,
                    "oldest_seconds": _number(rows[0][0]) if rows else 0.0,
                }
            )
    row = await (
        await conn.execute(
            "SELECT count(*),count(*) FILTER(WHERE wait_event_type='Lock'),"
            "count(*) FILTER(WHERE state='idle in transaction') FROM pg_stat_activity "
            "WHERE datname=current_database() AND pid<>pg_backend_pid()"
        )
    ).fetchone()
    invalid = await (
        await conn.execute(
            "SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s "
            "AND (NOT i.indisvalid OR NOT i.indisready)",
            (schema,),
        )
    ).fetchone()
    leases = await (
        await conn.execute(
            "SELECT count(*) FROM (SELECT 1 FROM k05_object_leases WHERE expires_at<=clock_timestamp() LIMIT %s) x",
            (maximum_jobs + 1,),
        )
    ).fetchone()
    stats = await (
        await conn.execute(
            "SELECT deadlocks,temp_bytes,blks_read,blks_hit FROM pg_stat_database WHERE datname=current_database()"
        )
    ).fetchone()
    retention: dict[str, Any] = {
        "available": False,
        "due_lower_bound": None,
        "due_exact": None,
        "oldest_overdue_seconds": None,
    }
    present = await (
        await conn.execute("SELECT to_regclass(%s)", (schema + ".k13_deletions",))
    ).fetchone()
    if present is not None and present[0] is not None:
        due = await (
            await conn.execute(
                "SELECT EXTRACT(EPOCH FROM (clock_timestamp()-eligible_at)) FROM k13_deletions "
                "WHERE state<>'purged' AND eligible_at<=clock_timestamp() ORDER BY eligible_at LIMIT %s",
                (maximum_jobs + 1,),
            )
        ).fetchall()
        retention = {
            "available": True,
            "due_lower_bound": min(len(due), maximum_jobs),
            "due_exact": len(due) <= maximum_jobs,
            "oldest_overdue_seconds": _number(due[0][0]) if due else 0.0,
        }
    return {
        "schema_version": "hosted-operator-snapshot/v1",
        "scope": "Explicit operator read-only database observations, not application-pool stats or per-run attribution.",
        "job_sample_limit_per_stratum": maximum_jobs,
        "jobs": samples,
        "queue_oldest_seconds": max(
            item["oldest_seconds"] for item in samples if item["state"] == "queued"
        ),
        "pending_count_lower_bound": sum(
            item["sampled_rows"] for item in samples if item["state"] == "queued"
        ),
        "pending_count_exact": not any(
            item["truncated"] for item in samples if item["state"] == "queued"
        ),
        "database": dict(
            zip(("other_backends", "lock_waiting", "idle_in_transaction"), row, strict=True)
        ),
        "invalid_indexes": invalid[0],
        "expired_object_leases_lower_bound": min(leases[0], maximum_jobs),
        "expired_object_leases_exact": leases[0] <= maximum_jobs,
        "database_cumulative": dict(
            zip(("deadlocks", "temp_bytes", "blocks_read", "blocks_hit"), stats, strict=True)
        ),
        "retention": retention,
        "query_policy": "Read-only;1s statement timeout;250ms lock timeout;failure means unavailable, not zero.",
    }


async def trace_journey(
    conn: Any,
    schema: str,
    *,
    request_id: str | None = None,
    job_id: str | None = None,
    maximum: int = 100,
) -> dict[str, Any]:
    if (
        (request_id is None) == (job_id is None)
        or type(maximum) is not int
        or not 1 <= maximum <= 100
        or _ID.fullmatch(request_id or job_id or "") is None
    ):
        raise ValueError("One bounded request/job identifier required.")
    await _boundary(conn, schema)
    job = None
    if job_id is not None:
        row = await (
            await conn.execute(
                "SELECT request_id,operation,state,canonical_project_id,snapshot_id,parent_result_id,attempt_count "
                "FROM j01_jobs WHERE job_id=%s",
                (job_id,),
            )
        ).fetchone()
        if row is None:
            return {"schema_version": "hosted-journey/v1", "found": False, "actions": []}
        request_id = row[0]
        job = dict(
            zip(
                (
                    "request_id",
                    "operation",
                    "state",
                    "project_id",
                    "snapshot_id",
                    "parent_result_id",
                    "attempt_count",
                ),
                row,
                strict=True,
            )
        )
        job["job_id"] = job_id
    rows = await (
        await conn.execute(
            "SELECT audit_id,request_id,operation_id,action,effect_id,revision,job_id,recorded_at,parent_id,subject_id "
            "FROM k06_action_audit WHERE request_id=%s ORDER BY audit_id LIMIT %s",
            (request_id, maximum + 1),
        )
    ).fetchall()
    names = (
        "audit_id",
        "request_id",
        "operation_id",
        "action",
        "effect_id",
        "revision",
        "job_id",
        "recorded_at",
        "parent_id",
        "subject_id",
    )
    actions = [dict(zip(names, row, strict=True)) for row in rows[:maximum]]
    return {
        "schema_version": "hosted-journey/v1",
        "found": job is not None or bool(rows),
        "request_id": request_id,
        "job": job,
        "actions": actions,
        "truncated": len(rows) > maximum,
        "scope": "Opaque committed action/object IDs only; audit rows prove their own effect transaction, worker settled logs alone do not.",
    }


async def _execute(args: argparse.Namespace) -> dict[str, Any]:
    import psycopg
    from psycopg.conninfo import make_conninfo

    value = os.environ.get(args.database_env)
    if not value:
        raise ValueError("Configured operator database environment is unavailable.")
    schema = migration_schema(args.schema)
    conninfo = make_conninfo(
        value,
        options="-csearch_path=" + schema + " -cstatement_timeout=1000 -clock_timeout=250",
        connect_timeout=5,
    )
    async with await psycopg.AsyncConnection.connect(conninfo) as conn:
        await conn.execute("SET TRANSACTION READ ONLY")
        if args.command == "snapshot":
            return await operator_snapshot(conn, schema, maximum_jobs=args.limit)
        return await trace_journey(
            conn, schema, request_id=args.request_id, job_id=args.job_id, maximum=args.limit
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("snapshot", "journey"))
    parser.add_argument("--schema", required=True)
    parser.add_argument("--database-env", default="ALETHEIA_OPERATOR_DATABASE_URL")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--request-id")
    parser.add_argument("--job-id")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:

        async def bounded() -> dict[str, Any]:
            async with asyncio.timeout(20):
                return await _execute(args)

        with asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
        ) as runner:
            result = runner.run(bounded())
        text = json.dumps(result, default=str, ensure_ascii=True, allow_nan=False, indent=2) + "\n"
        if args.output is None:
            print(text, end="")
        else:
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(text)
        return 0
    except Exception:
        print('{"error":"operator_observation_unavailable"}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
