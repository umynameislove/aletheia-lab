"""Opt-in native response-origin lifecycle and standard cache-repair comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "verify", "worker"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--native-python", type=Path)
    parser.add_argument("--arm", default="native")
    parser.add_argument("--workflow", default="version_routes")
    args = parser.parse_args()
    try:
        if args.action == "worker":
            from aletheia_lab.evaluation.response_origin_source import worker

            source = worker(args.arm, args.workflow, args.study_dir.resolve())
            write_new_file(args.study_dir / "source.json", encode(source).encode())
            result = {"status": "response_origin_worker_terminal", "terminal": source["terminal"]}
        else:
            from aletheia_lab.evaluation.response_origin_study import run, verify

            if args.action == "verify":
                result = verify(args.root.resolve(), args.study_dir.resolve())
            else:
                if args.native_python is None or not args.native_python.is_file():
                    raise ValueError("explicit native runtime required")
                # Resolving a venv interpreter symlink would discard its environment.
                result = run(
                    args.root.resolve(), args.study_dir.resolve(), args.native_python.absolute()
                )
    except (ValueError, OSError, RuntimeError, ImportError) as exc:
        print(
            json.dumps(
                {"status": "response_origin_failed_closed", "error_type": type(exc).__name__}
            )
        )
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
