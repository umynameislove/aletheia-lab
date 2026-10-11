"""Opt-in local joint-inference capture, durable audit replay and read-only checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.joint_inference_study import run, verify, worker


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "worker", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--dependencies", type=Path, action="append", default=[])
    args = parser.parse_args()
    try:
        if args.action == "worker":
            worker(args.study_dir.resolve())
            result = {"status": "joint_native_worker_complete"}
        elif args.action == "verify":
            result = verify(args.root.resolve(), args.study_dir.resolve())
        else:
            if not args.dependencies:
                raise ValueError("explicit isolated dependency directories required")
            result = run(args.root.resolve(), args.study_dir.resolve(), args.dependencies)
    except (ValueError, OSError, RuntimeError, ImportError) as exc:
        print(
            json.dumps(
                {"status": "joint_inference_failed_closed", "error_type": type(exc).__name__}
            )
        )
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
