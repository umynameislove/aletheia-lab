"""Explicit K-05 migration/observation/scrub tool; credentials only from environment."""
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
    migrate,
    migration_schema,
    verify,
)


def _schema(value: str) -> str:
    try:
        return migration_schema(value)
    except PersistenceMigrationRejected:
        raise argparse.ArgumentTypeError("explicit non-system schema required") from None


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("migrate", "verify", "observe", "analyze", "vacuum", "scrub", "scrub-catalog", "reconcile", "rebuild-views", "read-indexes"))
    parser.add_argument("--schema", required=True, type=_schema)
    parser.add_argument("--database-env", default="ALETHEIA_HOSTED_DATABASE_URL")
    parser.add_argument("--object-root", type=Path)
    parser.add_argument("--workspace")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--after", default="")
    parser.add_argument("--metadata-after", default="")
    parser.add_argument("--grace-seconds", type=int, default=3600)
    parser.add_argument("--replace", action="store_true", help="Atomically repair the current result projection version")
    args = parser.parse_args(argv)
    if args.action in ("scrub", "scrub-catalog", "reconcile", "rebuild-views") and args.object_root is None:
        parser.error("object maintenance requires --object-root")
    if args.action in ("scrub", "scrub-catalog", "rebuild-views") and not args.workspace:
        parser.error("scrub requires an explicit --workspace")
    if args.replace and args.action != 'rebuild-views':
        parser.error('--replace applies only to rebuild-views')
    return args


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg_pool import AsyncConnectionPool

    from aletheia_lab.hosted.persistence_maintenance import maintain, observe

    url = os.environ.get(args.database_env)
    if not url:
        raise ValueError("Configured database environment variable is missing")
    # Do not let a connection-string search_path silently choose the target.
    scoped = make_conninfo(url, options="-csearch_path=" + args.schema + " -clock_timeout=1000 -cstatement_timeout=30000 -cidle_in_transaction_session_timeout=30000",
                          connect_timeout=5,
                          application_name="aletheia-k05-storage-admin")
    if args.action in ("analyze", "vacuum"):
        async with await psycopg.AsyncConnection.connect(scoped, autocommit=True) as conn:
            await maintain(conn, args.schema, vacuum=args.action == "vacuum")
        return {"action": args.action, "schema": args.schema, "completed": True}
    async with AsyncConnectionPool(scoped, min_size=1, max_size=1, open=False) as pool:
        await pool.wait(timeout=10)
        if args.action in ("scrub", "scrub-catalog", "reconcile", "rebuild-views"):
            from aletheia_lab.hosted.object_store import FilesystemObjectStore
            from aletheia_lab.hosted.postgres_objects import PostgresObjects

            # Existing marked roots only: never initialize a user source folder.
            if not (args.object_root / ".k05-object-store").is_file():
                raise ValueError("An existing managed K-05 object root is required")
            objects = PostgresObjects(pool, FilesystemObjectStore(args.object_root))
            report: Any
            if args.action == 'rebuild-views':
                from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
                from aletheia_lab.hosted.result_views import rebuild_result_views

                report = await rebuild_result_views(PostgresCatalog(pool, objects), args.schema, args.workspace,
                    limit=args.limit, after=args.after, replace=args.replace)
            elif args.action == "scrub-catalog":
                from aletheia_lab.hosted.postgres_catalog import scrub_catalog

                report = await scrub_catalog(pool, objects, args.workspace, limit=args.limit, after=args.after)
            elif args.action == "scrub":
                report = await objects.scrub(args.workspace, limit=args.limit, after=args.after)
            else:
                report = await objects.reconcile(grace_seconds=args.grace_seconds, limit=args.limit, after=args.after, metadata_after=args.metadata_after)
            return {"action": args.action, **asdict(report)}
        async with pool.connection() as conn:
            await conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(args.schema)))
            await conn.execute("SET LOCAL lock_timeout='1s'")
            await conn.execute("SET LOCAL statement_timeout='30s'")
            if args.action == "migrate":
                from aletheia_lab.hosted.project_policy_migration import (
                    migrate as migrate_project_policy,
                )

                disposition = await migrate(conn, args.schema)
                policy = await migrate_project_policy(conn, args.schema)
                return {"action": args.action, "disposition": disposition, "project_policy": policy}
            if args.action == "verify":
                if not await verify(conn, args.schema):
                    raise ValueError("Persistence schema verification failed")
                from aletheia_lab.hosted.project_policy_migration import (
                    verify as verify_project_policy,
                )

                if not await verify_project_policy(conn, args.schema):
                    raise ValueError("Project policy schema verification failed")
                return {"action": args.action, "schema": args.schema, "verified": True, "project_policy_verified": True}
            if args.action == 'read-indexes':
                from aletheia_lab.hosted.read_indexes import migrate as migrate_read_indexes

                return {'action': args.action, 'disposition': await migrate_read_indexes(conn, args.schema)}
            await conn.execute("SET TRANSACTION READ ONLY")
            return await observe(conn, args.schema)


async def _bounded(args: argparse.Namespace) -> dict[str, Any]:
    async with asyncio.timeout(300):
        return await execute(args)


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    try:
        loop = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        with asyncio.Runner(loop_factory=loop) as runner:
            result = runner.run(_bounded(args))
        print(json.dumps(result, default=str, ensure_ascii=False, sort_keys=True))
        return 1 if result.get("corrupt_sha256") or result.get("corrupt_record_ids") else 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 130
    except Exception:
        print(json.dumps({"error": "storage_admin_failed", "action": args.action}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
