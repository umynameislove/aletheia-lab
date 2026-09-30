"""Prepare a matched development pilot offline; execute only a confirmed paid plan."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.evidence_bounded_pilot import (
    checked_policy_plan,
    execute_policy_pilot,
    prepare_policy_pilot,
    verify_policy_pilot,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "execute", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--source-root", type=Path, help="Existing checkout holding hash-pinned old source archives"
    )
    parser.add_argument("--memory-root", type=Path, default=Path("../memory"))
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--confirm-plan-sha256")
    args = parser.parse_args()
    directory = args.directory or args.memory_root / "evidence-bounded-policy-development-v1"
    try:
        if args.action == "prepare":
            report = prepare_policy_pilot(
                root=args.root,
                memory_root=args.memory_root,
                directory=directory,
                source_root=args.source_root,
            )
        elif args.action == "execute":
            if not args.confirm_plan_sha256:
                parser.error("execute requires the freshly approved paid plan digest")
            plan, _ = checked_policy_plan(directory, confirm_sha256=args.confirm_plan_sha256)
            if (directory / "lease.json").exists() or (directory / "results").exists():
                raise ValueError("paid attempt already exists")
            from aletheia_lab.evaluation.warrant_development_live import OpenAIDevelopmentCaller

            caller = OpenAIDevelopmentCaller(maximum_calls=plan["maximum_provider_calls"])
            report = execute_policy_pilot(
                directory=directory,
                confirm_sha256=args.confirm_plan_sha256,
                caller=caller,
                progress=lambda value: print(json.dumps(value), flush=True),
            )
        else:
            report = verify_policy_pilot(directory=directory)
    except (ValueError, OSError, KeyError, StopIteration):
        print(
            json.dumps({"status": "policy_pilot_failed_closed", "private_details_printed": False})
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
