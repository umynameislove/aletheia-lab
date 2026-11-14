"""Observe ambiguous mutation keys after a capacity run; never replay a new action."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from aletheia_lab.hosted.capacity_reconciliation import reconcile_mutations
from aletheia_lab.hosted.persistence_migration import migration_schema


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--database-env", default="ALETHEIA_OPERATOR_DATABASE_URL")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.result.stat().st_size > 32 * 1024**2:
            raise ValueError("Bounded safe capacity report required.")
        report = json.loads(args.result.read_bytes())
        schema = migration_schema(args.schema)

        async def execute():
            import psycopg
            from psycopg.conninfo import make_conninfo

            async with (
                asyncio.timeout(20),
                await psycopg.AsyncConnection.connect(
                    make_conninfo(
                        os.environ[args.database_env],
                        connect_timeout=5,
                        options="-csearch_path=" + schema,
                    )
                ) as conn,
            ):
                await conn.execute("SET TRANSACTION READ ONLY")
                return await reconcile_mutations(conn, schema, report)

        with asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
        ) as runner:
            result = runner.run(execute())
        with args.output.open("x") as stream:
            json.dump(result, stream, allow_nan=False, indent=2)
            stream.write("\n")
        return 0
    except Exception:
        print('{"status":"failed","code":"capacity_reconciliation_unavailable"}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
