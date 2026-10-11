"""Run owned calibration transfer, same-service measurement or read-only replay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.calibration_audit_study import (
    cost_worker,
    crash_worker,
    prepare,
    read,
    run,
    sealed,
    transfer_worker,
    verify,
)
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("prepare", "run", "verify", "transfer-worker", "cost-worker", "crash-worker"),
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--phase", choices=("development", "evaluation"), default="development")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--seed", type=int, default=2301)
    parser.add_argument("--mode", choices=("raw", "compact", "whole"), default="compact")
    parser.add_argument("--boundary", choices=("before_commit", "after_ack"), default="after_ack")
    args = parser.parse_args()
    try:
        if args.command == "transfer-worker":
            result = transfer_worker(args.seed)
        elif args.command == "cost-worker":
            if args.config is None or args.directory is None:
                parser.error("cost worker requires config and owned directory")
            result = cost_worker(read(args.config), args.directory)
        elif args.command == "crash-worker":
            if args.directory is None:
                parser.error("crash control requires owned directory")
            crash_worker(args.directory, args.mode, args.boundary)
            return
        else:
            if args.directory is None:
                parser.error("study directory required")
            root, directory = args.root.resolve(), args.directory.absolute()
            result = (
                prepare(root, directory)
                if args.command == "prepare"
                else (
                    verify(root, directory, args.phase)
                    if args.command == "verify"
                    else run(root, directory, args.phase)
                )
            )
        if args.output is not None:
            write_new_file(args.output, encode(sealed(result)).encode())
            result = {"status": result["status"], "provider_calls": 0}
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, RuntimeError) as exc:
        print(
            encode({"status": "calibration_audit_failed_closed", "error_type": type(exc).__name__})
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
