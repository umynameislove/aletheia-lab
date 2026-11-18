"""Operator-only migration CLI; credentials come only from the declared environment."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import cast

from .abuse import RatePolicy
from .admission_migration import MigrationConnection, change_policy, migrate, migration_schema


async def _apply(args: argparse.Namespace, database_url: str) -> str:
    from psycopg import AsyncConnection

    async with asyncio.timeout(30):
        async with await AsyncConnection.connect(database_url, autocommit=False, connect_timeout=5,
                options="-c statement_timeout=10000 -c lock_timeout=3000") as connection:
            trusted = cast(MigrationConnection, connection)
            result: str
            if args.action == "migrate":
                result = await migrate(trusted, args.schema)
            else:
                changed = await change_policy(trusted, args.schema, args.namespace,
                    expected=RatePolicy(*args.expected), replacement=RatePolicy(*args.replacement))
                result = "policy_changed" if changed else "policy_unchanged"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", required=True)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("migrate")
    policy = actions.add_parser("change-policy")
    policy.add_argument("--namespace", required=True)
    policy.add_argument("--expected", nargs=3, type=int, required=True, metavar=("GLOBAL", "SOURCE", "SECONDS"))
    policy.add_argument("--replacement", nargs=3, type=int, required=True, metavar=("GLOBAL", "SOURCE", "SECONDS"))
    args = parser.parse_args()
    try:
        args.schema = migration_schema(args.schema)
        database_url = os.environ["ALETHEIA_HOSTED_MIGRATION_DATABASE_URL"]
        if not database_url:
            raise ValueError("missing migration connection")
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        result = asyncio.run(_apply(args, database_url))
    except Exception:
        print("K03_ADMISSION_MIGRATION=FAILED_OR_UNKNOWN", file=sys.stderr)
        return 1
    # Connection context has committed before reporting an applied result.
    print("K03_ADMISSION_MIGRATION=" + result.upper())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
