#!/usr/bin/env python3
"""Prepare the frozen M5 design, execute once with U3 authorization, or replay it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.benchmark.p2.score_mapping_new_source_plan import preflight_study, prepare_study
from aletheia_lab.benchmark.p2.score_mapping_new_source_study import execute_study, verify_study
from aletheia_lab.benchmark.p2.target_binding_prospective_sources import ProspectiveBindingError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("preflight", "prepare", "execute", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--confirm-plan-sha256")
    parser.add_argument("--authorize-final-execution", action="store_true")
    args = parser.parse_args()
    try:
        if args.operation == "preflight":
            result = preflight_study(args.root, args.study_dir)
        elif args.operation == "prepare":
            result = prepare_study(args.root, args.study_dir)
        elif args.operation == "execute":
            if not args.confirm_plan_sha256:
                parser.error("execute requires --confirm-plan-sha256")
            result = execute_study(
                args.root,
                args.study_dir,
                confirm_plan_sha256=args.confirm_plan_sha256,
                authorize_final_execution=args.authorize_final_execution,
            )
        else:
            result = verify_study(args.root, args.study_dir)
    except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
        print(
            json.dumps(
                {
                    "status": "new_source_failed_closed",
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
    return 1 if result["status"] == "new_source_failed_closed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
