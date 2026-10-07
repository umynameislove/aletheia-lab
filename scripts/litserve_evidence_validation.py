"""Opt-in local evidence/repair transfer on a pinned, previously unused runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "verify", "closeout", "serve"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--native-python", type=Path)
    parser.add_argument("--native-site", type=Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--arm", choices=("native", "cooperative"), default="native")
    args = parser.parse_args()
    try:
        if args.action == "closeout":
            from aletheia_lab.evaluation.litserve_evidence_closeout import closeout

            result = closeout(args.root.resolve(), args.study_dir.resolve())
            print(json.dumps(result, indent=2))
            return 0
        if args.action == "serve":
            from aletheia_lab.evaluation.litserve_evidence_source import serve

            serve(args.study_dir.resolve(), args.port, args.arm)
            return 0
        from aletheia_lab.evaluation.litserve_evidence_study import design, run, verify

        if args.action == "verify":
            result = verify(args.root.resolve(), args.study_dir.resolve())
        else:
            if args.native_python is None or args.native_site is None:
                raise ValueError("explicit isolated native environment required")
            if args.action == "prepare":
                result = design(
                    args.root.resolve(), args.native_python.absolute(), args.native_site.resolve()
                )
            else:
                result = run(
                    args.root.resolve(),
                    args.study_dir.resolve(),
                    args.native_python.absolute(),
                    args.native_site.resolve(),
                )
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(
            json.dumps(
                {"status": "litserve_evidence_failed_closed", "error_type": type(exc).__name__}
            )
        )
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
