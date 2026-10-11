"""Prepare, inspect or explicitly execute the bounded native SQLite transfer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation import sqlite_evidence_transfer as study


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "preflight", "execute", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--confirm-plan-sha256")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = study.prepare(root=args.root, directory=args.study_dir)
        elif args.command in {"preflight", "verify"}:
            result = getattr(study, args.command)(directory=args.study_dir)
        else:
            if not args.confirm_plan_sha256:
                raise ValueError("current plan confirmation is required")
            plan, _ = study.checked_plan(
                directory=args.study_dir, confirm_sha256=args.confirm_plan_sha256
            )
            if (args.study_dir / "lease.json").exists() or (args.study_dir / "results").exists():
                raise ValueError("attempt exists; do not construct a network client")
            from aletheia_lab.evaluation.warrant_development_live import OpenAIDevelopmentCaller

            result = study.execute(
                directory=args.study_dir,
                confirm_sha256=args.confirm_plan_sha256,
                caller=OpenAIDevelopmentCaller(maximum_calls=plan["maximum_provider_calls"]),
                progress=lambda value: print(json.dumps(value), flush=True),
            )
    except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
        print(
            json.dumps(
                {
                    "status": "sqlite_evidence_transfer_failed_closed",
                    "error_type": type(exc).__name__,
                }
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
