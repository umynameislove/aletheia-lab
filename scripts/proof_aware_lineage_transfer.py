"""Offline transfer preparation/replay or one expressly authorized paid pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation import proof_aware_lineage_transfer as pilot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "preflight", "execute", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--memory-root", type=Path, required=True)
    parser.add_argument("--pilot-dir", type=Path, required=True)
    parser.add_argument("--confirm-plan-sha256")
    args = parser.parse_args()
    paths = {
        "root": args.root.resolve(),
        "memory_root": args.memory_root,
        "directory": args.pilot_dir,
    }
    try:
        if args.command == "prepare":
            result = pilot.prepare(**paths)
        elif args.command == "preflight":
            result = pilot.preflight(**paths)
        elif args.command == "verify":
            result = pilot.verify(**paths)
        else:
            if not args.confirm_plan_sha256:
                raise ValueError("execution needs the current approved plan digest")
            plan, _ = pilot.checked_plan(**paths, confirm_sha256=args.confirm_plan_sha256)
            if {path.name for path in args.pilot_dir.iterdir()} != pilot.BASE_FILES:
                raise ValueError("attempt exists; do not construct a network client")
            from aletheia_lab.evaluation.warrant_development_live import OpenAIDevelopmentCaller

            caller = OpenAIDevelopmentCaller(maximum_calls=plan["maximum_provider_calls"])
            result = pilot.execute(
                **paths,
                confirm_sha256=args.confirm_plan_sha256,
                caller=caller,
                progress=lambda value: print(json.dumps(value), flush=True),
            )
    except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
        print(
            json.dumps(
                {
                    "status": "proof_aware_lineage_transfer_failed_closed",
                    "error_type": type(exc).__name__,
                }
            )
        )
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
