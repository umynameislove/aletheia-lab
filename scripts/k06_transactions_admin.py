"""Explicit operator command for the additive K06 action-audit schema only."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys

from aletheia_lab.hosted.action_audit_migration import migrate, verify


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("migrate", "verify"))
    parser.add_argument("--schema", required=True)
    parser.add_argument("--database-url-env", default="ALETHEIA_HOSTED_DATABASE_URL")
    options = parser.parse_args(argv)
    if (not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", options.schema)
            or options.schema in ("public", "information_schema") or options.schema.startswith("pg_")):
        parser.error("An explicit application schema is required.")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", options.database_url_env):
        parser.error("A database secret environment-variable name is required.")
    return options


async def execute(options: argparse.Namespace) -> dict[str, object]:
    import psycopg

    database = os.environ.get(options.database_url_env)
    if not database:
        raise ValueError("Database secret reference is unavailable.")
    async with asyncio.timeout(45):
        async with await psycopg.AsyncConnection.connect(database, connect_timeout=10) as conn:
            await conn.execute("SET LOCAL lock_timeout='5s'")
            await conn.execute("SET LOCAL statement_timeout='30s'")
            result: dict[str, object] = {"schema": options.schema}
            if options.operation == "migrate":
                result["audit_migration"] = await migrate(conn, options.schema)
            if not await verify(conn, options.schema):
                raise ValueError("Action audit verification failed.")
            result["audit_schema"] = "verified"
    return result


def main(argv: list[str] | None = None) -> int:
    options = arguments(argv)
    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
            result = runner.run(execute(options))
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except Exception:
        print("Transaction administration failed; database details were not logged.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
