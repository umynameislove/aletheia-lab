"""Explicit K-07 additive migration/verification and bounded owned-part cleanup."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from importlib import import_module

from aletheia_lab.hosted.persistence_migration import migration_schema
from aletheia_lab.hosted.persistence_migration import verify as verify_core
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.preview_migration import migrate as migrate_preview
from aletheia_lab.hosted.preview_migration import verify as verify_preview
from aletheia_lab.hosted.upload_staging_migration import migrate, verify


async def _main(args: argparse.Namespace) -> int:
    psycopg = import_module("psycopg")

    database = os.environ.get(args.database_url_env)
    if not database:
        raise ValueError("An explicit database environment variable is required.")
    schema = migration_schema(args.schema)
    result = ""
    async with await psycopg.AsyncConnection.connect(database) as conn:
        if args.action == "migrate-imports":
            await migrate(conn, schema)
            await migrate_preview(conn, schema)
            if not (await verify(conn, schema) and await verify_preview(conn) and await verify_core(conn, schema)):
                raise ValueError("The combined import migrations did not preserve core verification.")
            result = "K07_IMPORT_MIGRATIONS_VERIFIED=true"
        elif args.action == "verify-imports":
            if not (await verify(conn, schema) and await verify_preview(conn) and await verify_core(conn, schema)):
                raise ValueError("The combined import schema did not verify.")
            result = "K07_IMPORT_SCHEMA_VERIFIED=true"
        elif args.action == "migrate":
            result = await migrate(conn, schema)
            result = "K07_UPLOAD_MIGRATION=" + result
        elif not await verify(conn, schema):
            raise ValueError("The installed upload migration did not verify.")
        elif args.action == "cleanup":
            count = await PostgresSources._cleanup_uploads(conn, limit=args.limit, grace_seconds=args.grace_seconds)
            result = "K07_UPLOADS_CLEANED=" + str(count)
        else:
            result = "K07_UPLOAD_SCHEMA_VERIFIED=true"
    print(result)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("migrate", "verify", "cleanup", "migrate-imports", "verify-imports"))
    parser.add_argument("--schema", required=True)
    parser.add_argument("--database-url-env", default="ALETHEIA_HOSTED_DATABASE_URL")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--grace-seconds", type=int, default=300)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100 or not 0 <= args.grace_seconds <= 86400:
        parser.error("limit must be 1..100 and grace-seconds must be 0..86400")
    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
            return runner.run(_main(args))
    except Exception:
        print("K07_UPLOAD_ADMIN_FAILED", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
