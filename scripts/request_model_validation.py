"""Opt-in local request/model replication, audit and durable retention comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("worker", "run", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=("original", "owned_ml"), default="original")
    parser.add_argument("--before-python", type=Path)
    parser.add_argument("--fixed-python", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "worker":
            from aletheia_lab.evaluation.request_model_source import run_source

            source = run_source(args.study_dir.resolve(), args.mode)
            write_new_file(args.study_dir / "source.json", encode(source).encode())
            result = {
                "status": "request_model_native_worker_terminal",
                "mode": args.mode,
                "request_count": len(source["rows"]),
                "http_success_count": sum(row["http_status"] == 200 for row in source["rows"]),
                "batch_count": len(source["batches"]),
                "failure_count": len(source["failures"]),
                "provider_calls": 0,
            }
        else:
            from aletheia_lab.evaluation.request_model_study import run, verify

            if args.action == "verify":
                result = verify(args.root.resolve(), args.study_dir.resolve())
            else:
                if args.before_python is None or args.fixed_python is None:
                    raise ValueError("two explicitly isolated Python environments required")
                result = run(
                    args.root.resolve(),
                    args.study_dir.resolve(),
                    args.before_python.absolute(),
                    args.fixed_python.absolute(),
                )
    except (ValueError, OSError, RuntimeError, ImportError) as exc:
        print(
            json.dumps({"status": "request_model_failed_closed", "error_type": type(exc).__name__})
        )
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
