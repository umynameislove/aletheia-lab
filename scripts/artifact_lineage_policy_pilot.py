"""Prepare offline, explicitly execute once, and verify a lineage policy pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.artifact_lineage_pilot import (
    checked_plan,
    execute_artifact_lineage_pilot,
    prepare_artifact_lineage_pilot,
    verify_artifact_lineage_pilot,
    verify_artifact_lineage_preflight,
)


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
            result = prepare_artifact_lineage_pilot(**paths)
        elif args.command == "preflight":
            result = verify_artifact_lineage_preflight(**paths)
        elif args.command == "verify":
            result = verify_artifact_lineage_pilot(**paths)
        else:
            if not args.confirm_plan_sha256:
                raise ValueError("execution needs the current explicitly approved plan digest")
            plan, _ = checked_plan(**paths, confirm_sha256=args.confirm_plan_sha256)
            # Only this branch constructs a real network client, after local checks.
            from aletheia_lab.evaluation.warrant_development_live import OpenAIDevelopmentCaller

            caller = OpenAIDevelopmentCaller(maximum_calls=plan["maximum_provider_calls"])
            result = execute_artifact_lineage_pilot(
                **paths,
                confirm_sha256=args.confirm_plan_sha256,
                caller=caller,
                progress=lambda value: print(json.dumps(value), flush=True),
            )
    except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
        print(
            json.dumps(
                {"status": "artifact_lineage_pilot_failed_closed", "error_type": type(exc).__name__}
            )
        )
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
