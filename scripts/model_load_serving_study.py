"""Opt-in real serving development run, or strictly read-only result replay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.model_load_serving_study import preflight, run, verify


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "run", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--study-dir", type=Path)
    args = parser.parse_args()
    if args.action != "preflight" and args.study_dir is None:
        parser.error("run/verify require --study-dir")
    try:
        if args.action == "preflight":
            result = preflight(args.root.resolve())
        elif args.action == "run":
            result = run(args.root.resolve(), args.study_dir.resolve())
        else:
            result = verify(args.root.resolve(), args.study_dir.resolve())
    except (ValueError, OSError, RuntimeError, ImportError) as exc:
        print(
            json.dumps({"status": "serving_study_failed_closed", "error_type": type(exc).__name__})
        )
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
