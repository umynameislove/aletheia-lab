#!/usr/bin/env python3
"""Inspect, prepare, execute, or verify recovery-v2 claim–evidence scoring."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.diagnosis.main_recovery_scoring import (
    MainRecoveryScoringError,
    execute_scoring,
    inspect_scoring,
    prepare_scoring,
    verify_scoring,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("inspect", "prepare", "execute", "verify"):
        item = subparsers.add_parser(command)
        item.add_argument("--root", type=Path, default=Path("."))
        item.add_argument("--memory-root", type=Path, required=True)
        if command == "execute":
            item.add_argument("--confirm-plan-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    memory = args.memory_root
    scoring = memory / "diagnosis-main-recovery-scoring-v1"
    common = {
        "root": args.root,
        "packet_path": memory / "diagnosis-main-p4-census-v1/private-census-packet.json",
        "predecessor": memory / "diagnosis-main-registered-run-v1",
        "smoke": memory / "diagnosis-main-schema-smoke-v2",
        "recovery": memory / "diagnosis-main-technical-recovery-v2",
        "scoring": scoring,
    }
    try:
        if args.command == "inspect":
            result = inspect_scoring(**common)
        elif args.command == "prepare":
            result = prepare_scoring(**common)
        elif args.command == "execute":
            result = execute_scoring(**common, confirmed_plan_sha256=args.confirm_plan_sha256)
        else:
            result = verify_scoring(**common)
    except (MainRecoveryScoringError, OSError, ValueError, TypeError, KeyError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error_type": type(exc).__name__,
                    "reason": str(exc) if isinstance(exc, MainRecoveryScoringError) else None,
                    "execution_lease_present": (scoring / "lease.json").exists(),
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
