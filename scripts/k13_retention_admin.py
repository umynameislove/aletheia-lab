"""Explicit owner-only bounded retention; never removes workspace source uploads."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from aletheia_lab.hosted.persistence_migration import (
    PersistenceMigrationRejected,
    migration_schema,
    verify,
)


def _schema(value: str) -> str:
    try:
        return migration_schema(value)
    except PersistenceMigrationRejected:
        raise argparse.ArgumentTypeError("An explicit non-system schema is required.") from None


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("due", "expire", "purge", "configure"))
    parser.add_argument("--schema", required=True, type=_schema)
    parser.add_argument("--object-root", required=True, type=Path)
    parser.add_argument("--database-env", default="ALETHEIA_HOSTED_DATABASE_URL")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--after", help="Previous due next_cursor JSON, only for due")
    parser.add_argument("--workspace")
    parser.add_argument("--project")
    parser.add_argument("--project-days", type=int)
    parser.add_argument("--terminal-job-days", type=int)
    parser.add_argument("--object-grace-seconds", type=int)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    if args.after is not None and args.action != "due":
        parser.error("--after applies only to due")
    if (args.workspace is not None or args.project is not None) != (args.action == "purge"):
        parser.error("Only purge requires both --workspace and --project")
    if args.action == "purge" and not (args.workspace and args.project):
        parser.error("purge requires both --workspace and --project")
    policy = (args.project_days, args.terminal_job_days, args.object_grace_seconds)
    if any(value is not None for value in policy) != (args.action == "configure"):
        parser.error("Only configure requires all three policy values")
    if args.action == "configure" and any(value is None for value in policy):
        parser.error("configure requires all three policy values")
    return args


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    from psycopg.conninfo import make_conninfo
    from psycopg_pool import AsyncConnectionPool

    from aletheia_lab.hosted.object_store import FilesystemObjectStore
    from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
    from aletheia_lab.hosted.postgres_objects import PostgresObjects
    from aletheia_lab.hosted.retention import PostgresRetention
    from aletheia_lab.hosted.review_migration import verify as verify_reviews

    database = os.environ.get(args.database_env)
    if not database or not (args.object_root / ".k05-object-store").is_file():
        raise ValueError("An owner database and existing managed object root are required.")
    scoped = make_conninfo(
        database,
        options="-csearch_path="
        + args.schema
        + " -clock_timeout=1000 -cstatement_timeout=30000 -cidle_in_transaction_session_timeout=30000",
        connect_timeout=5,
        application_name="aletheia-k13-retention-admin",
    )
    async with AsyncConnectionPool(scoped, min_size=1, max_size=1, open=False) as pool:
        await pool.wait(timeout=10)
        async with pool.connection() as conn:
            if not await verify(conn, args.schema) or not await verify_reviews(conn, args.schema):
                raise ValueError("Current persistence and review migrations are required.")
        objects = PostgresObjects(pool, FilesystemObjectStore(args.object_root))
        retention = PostgresRetention(pool, PostgresCatalog(pool, objects))
        if args.action == "due":
            from pydantic import AwareDatetime, TypeAdapter

            from aletheia_lab.hosted_contract.runtime_management import ProjectId
            from aletheia_lab.hosted_contract.session_capabilities import Identifier

            after = (
                TypeAdapter(tuple[AwareDatetime, Identifier, ProjectId]).validate_json(args.after)
                if args.after
                else None
            )
            projects = await retention.due(limit=args.limit, after=after)
            return {
                "action": "due",
                "projects": [
                    {"eligible_at": row[0], "workspace_id": row[1], "project_id": row[2]}
                    for row in projects
                ],
                "limit": args.limit,
                "next_cursor": projects[-1] if len(projects) == args.limit else None,
            }
        if args.action == "expire":
            return {"action": "expire", **await retention.expire(limit=args.limit)}
        if args.action == "configure":
            await retention.configure(
                project_days=args.project_days,
                terminal_job_days=args.terminal_job_days,
                object_grace_seconds=args.object_grace_seconds,
            )
            return {
                "action": "configure",
                "future_project_days": args.project_days,
                "terminal_job_days": args.terminal_job_days,
                "object_grace_seconds": args.object_grace_seconds,
                "existing_deletion_eligibility_unchanged": True,
            }
        return {
            "action": "purge",
            **asdict(
                await retention.purge(
                    workspace_id=args.workspace, project_id=args.project, limit=args.limit
                )
            ),
        }


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)

    async def bounded() -> dict[str, Any]:
        async with asyncio.timeout(300):
            return await execute(args)

    try:
        with asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
        ) as runner:
            result = runner.run(bounded())
        print(json.dumps(result, default=str, ensure_ascii=False, sort_keys=True))
        return 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 130
    except Exception:
        print(
            json.dumps({"error": "retention_admin_failed", "action": args.action}), file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
