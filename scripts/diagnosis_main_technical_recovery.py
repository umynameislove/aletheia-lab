#!/usr/bin/env python3
"""Prepare, execute once, or verify the separate diagnosis main recovery."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.diagnosis.main_technical_recovery import (
    MainTechnicalRecoveryError,
    execute_recovery,
    prepare_recovery,
)
from aletheia_lab.diagnosis.main_technical_recovery_verify import verify_recovery


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "execute", "verify"):
        item = subparsers.add_parser(command)
        item.add_argument("--root", type=Path, default=Path("."))
        item.add_argument("--private-packet", type=Path, required=True)
        item.add_argument("--predecessor-run", type=Path, required=True)
        item.add_argument("--passing-smoke-dir", type=Path, required=True)
        item.add_argument("--recovery-dir", type=Path, required=True)
        if command == "prepare":
            item.add_argument("--cost-ceiling-usd", type=float, required=True)
        elif command == "execute":
            item.add_argument("--confirm-plan-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    common = {
        "root": args.root.resolve(),
        "packet_path": args.private_packet,
        "predecessor": args.predecessor_run,
        "smoke": args.passing_smoke_dir,
        "recovery": args.recovery_dir,
    }
    try:
        if args.command == "prepare":
            result = prepare_recovery(**common, cost_ceiling_usd=args.cost_ceiling_usd)
        elif args.command == "execute":
            result = execute_recovery(**common, confirmed_plan_sha256=args.confirm_plan_sha256)
        else:
            result = verify_recovery(**common)
    except (MainTechnicalRecoveryError, OSError, ValueError, TypeError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error_type": type(exc).__name__,
                    "reason": str(exc) if isinstance(exc, MainTechnicalRecoveryError) else None,
                    "execution_lease_present": (args.recovery_dir / "lease.json").exists(),
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 2 if result.get("status") == "pilot_failed_closed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
