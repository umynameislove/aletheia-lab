"""Prepare native development evidence or run one expressly authorized comparison."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from aletheia_lab.evaluation import native_cache_experiment as experiment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "preflight", "execute", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--confirm-plan-sha256")
    args = parser.parse_args()
    directory = args.study_dir
    try:
        if args.command == "prepare":
            result = experiment.prepare(root=args.root, directory=directory)
        elif args.command == "preflight":
            result = experiment.preflight(directory=directory)
        elif args.command == "verify":
            result = experiment.verify(directory=directory)
        else:
            if not args.confirm_plan_sha256:
                raise ValueError("execution requires the current approved plan identity")
            plan, _ = experiment.checked_plan(
                directory=directory, confirm_sha256=args.confirm_plan_sha256
            )
            if (directory / "lease.json").exists() or (directory / "results").exists():
                raise ValueError("attempt exists; do not construct a network client")
            from aletheia_lab.evaluation.warrant_development_live import OpenAIDevelopmentCaller

            caller = OpenAIDevelopmentCaller(maximum_calls=plan["maximum_provider_calls"])
            result = experiment.execute(
                directory=directory,
                confirm_sha256=args.confirm_plan_sha256,
                caller=caller,
                progress=lambda value: print(json.dumps(value), flush=True),
            )
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        IndexError,
        subprocess.SubprocessError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "native_cache_extraction_failed_closed",
                    "error_type": type(exc).__name__,
                }
            )
        )
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
