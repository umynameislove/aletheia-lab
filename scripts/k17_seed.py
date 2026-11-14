"""Generate private native fixtures in an already migrated fresh k17_seed_UUID schema."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from pydantic import SecretStr

from aletheia_lab.hosted.capacity_seed import SeedLimits, SeedRejected, seed_native
from aletheia_lab.hosted.capacity_seed_models import SeedPlan


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--database-env", default="ALETHEIA_HOSTED_DATABASE_URL")
    parser.add_argument("--csrf-secret-env", default="ALETHEIA_HOSTED_CSRF_SECRET")
    parser.add_argument("--max-bytes", required=True, type=int)
    parser.add_argument("--max-source-rows", required=True, type=int)
    parser.add_argument("--max-records", required=True, type=int)
    parser.add_argument("--max-seconds", type=int, default=3600)
    parser.add_argument(
        "--require-primary",
        action="store_true",
        help="Fail before writes while current native analyzer dimensions do not meet primary guide",
    )
    return parser.parse_args(argv)


async def execute(args: argparse.Namespace) -> dict[str, object]:
    from psycopg.conninfo import make_conninfo
    from psycopg_pool import AsyncConnectionPool

    if args.plan.is_symlink() or args.plan.stat().st_size > 65536:
        raise SeedRejected()
    plan = SeedPlan.model_validate_json(args.plan.read_bytes())
    limits = SeedLimits(args.max_bytes, args.max_source_rows, args.max_records, args.max_seconds)
    limits.validate(plan)
    # This gate precedes database connection and output creation.
    if args.require_primary and plan.primary_gaps():
        return {
            "status": "unsupported_primary_dimensions",
            "primary_dataset_qualified": False,
            "primary_gaps": plan.primary_gaps(),
        }
    database = os.environ.get(args.database_env)
    csrf = os.environ.get(args.csrf_secret_env)
    if not database or not csrf:
        raise SeedRejected()
    import re

    if re.fullmatch(r"k17_seed_[0-9a-f]{32}", args.schema) is None:
        raise SeedRejected()
    scoped = make_conninfo(
        database,
        options="-csearch_path="
        + args.schema
        + " -clock_timeout=1000 -cstatement_timeout=30000 -cidle_in_transaction_session_timeout=30000",
        application_name="aletheia-k17-private-seed",
        connect_timeout=5,
    )
    async with AsyncConnectionPool(scoped, min_size=1, max_size=2, open=False) as pool:
        await pool.wait(timeout=10)
        result = await seed_native(
            pool,
            schema=args.schema,
            output=args.output,
            plan=plan,
            csrf_secret=SecretStr(csrf),
            limits=limits,
            require_primary=args.require_primary,
        )
    return {
        "status": result["status"],
        "dataset_sha256": result["dataset_sha256"],
        "manifest": str(args.output / "manifest.json"),
        "logical_counts": result["logical_counts"],
        "primary_dataset_qualified": False,
        "primary_gaps": result["primary_gaps"],
    }


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    try:
        with asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
        ) as runner:
            result = runner.run(execute(args))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2 if result["status"] == "unsupported_primary_dimensions" else 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 130
    except Exception:
        print(
            json.dumps({"error": "native_capacity_seed_failed", "partial_target_may_exist": True}),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
