"""Owned forward Pipeline transfer, delayed-audit service and read-only replay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.calibration_audit_study import read, sealed
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.pipeline_audit_study import prepare, run, transfer_worker, verify
from aletheia_lab.evaluation.pipeline_audit_workload import worker
from aletheia_lab.filesystem import write_new_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("prepare", "run", "verify", "transfer-worker", "cost-worker")
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--phase", choices=("development", "evaluation"), default="development")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--seed", type=int, default=4101)
    args = parser.parse_args()
    try:
        if args.command == "transfer-worker":
            result = transfer_worker(args.seed)
        elif args.command == "cost-worker":
            if args.config is None:
                parser.error("owned configuration required")
            result = worker(read(args.config), args.directory)
        else:
            root = args.root.resolve()
            args.directory = args.directory.absolute()
            result = (
                prepare(root, args.directory)
                if args.command == "prepare"
                else verify(root, args.directory, args.phase)
                if args.command == "verify"
                else run(root, args.directory, args.phase)
            )
        if args.output is not None:
            write_new_file(args.output, encode(sealed(result)).encode())
            result = {"status": result["status"], "provider_calls": 0}
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, RuntimeError) as exc:
        print(
            encode({"status": "forward_pipeline_failed_closed", "error_type": type(exc).__name__})
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
