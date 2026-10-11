#!/usr/bin/env python3
"""Run the unchanged model-load validation design, with separate scoped authority."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation import model_load_validation_run as runner


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("prepare", "preflight", "execute", "verify", "_prepare", "_slot")
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--confirm-plan-sha256", default="")
    parser.add_argument("--confirm-seal-sha256", default="")
    parser.add_argument("--slot-index", type=int)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            report = runner.prepare(args.root, args.plan, args.study_dir, args.confirm_plan_sha256)
        elif args.command == "preflight":
            report = runner.preflight(args.root, args.plan, args.study_dir)
        elif args.command == "execute":
            report = runner.execute(args.root, args.plan, args.study_dir, args.confirm_seal_sha256)
        elif args.command == "verify":
            report = runner.verify(args.root, args.plan, args.study_dir)
        else:
            from aletheia_lab.evaluation import model_load_validation_worker as worker

            if args.command == "_prepare":
                report = worker.prepare_worker(args.root, args.plan, args.study_dir)
            else:
                if args.slot_index is None:
                    raise ValueError("worker requires a fixed slot index")
                report = worker.slot_worker(args.root, args.plan, args.study_dir, args.slot_index)
        print(
            json.dumps(report, sort_keys=True, indent=None if args.command.startswith("_") else 2)
        )
        return 0
    except (ValueError, RuntimeError, KeyError, TypeError, OSError) as exc:
        print(
            json.dumps(
                {"status": "model_load_validation_failed_closed", "error_type": type(exc).__name__}
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
