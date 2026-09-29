#!/usr/bin/env python3
"""Prepare, execute once, or independently replay a new-source target-binding study."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.target_binding_prospective import execute_study, verify_study
from aletheia_lab.benchmark.p2.target_binding_prospective_sources import (
    ProspectiveBindingError,
    prepare_study,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "execute", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--confirm-plan-sha256")
    args = parser.parse_args()
    try:
        if args.operation == "prepare":
            result = prepare_study(args.root, args.study_dir)
        elif args.operation == "execute":
            if args.confirm_plan_sha256 is None:
                parser.error("execute requires --confirm-plan-sha256")
            result = execute_study(
                args.root, args.study_dir, confirm_plan_sha256=args.confirm_plan_sha256
            )
        else:
            result = verify_study(args.root, args.study_dir)
    except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
        print(
            json.dumps(
                {
                    "status": "prospective_failed_closed",
                    "error_type": type(exc).__name__,
                    "operation": args.operation,
                    "reason": str(exc)
                    if isinstance(exc, ProspectiveBindingError)
                    else "invalid_or_unavailable_local_artifact",
                }
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 1 if result["status"] == "prospective_execution_failed_closed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
