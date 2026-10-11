"""Run an opt-in local development gap screen, or read-only aggregate replay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.audit_bundle_screen import run, verify


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, help="Replay existing development capture")
    args = parser.parse_args()
    try:
        if args.action == "verify" and args.capture_dir is not None:
            raise ValueError("verify does not accept capture input")
        result = (
            run(
                args.root.resolve(),
                args.study_dir.resolve(),
                args.capture_dir.resolve() if args.capture_dir else None,
            )
            if args.action == "run"
            else verify(args.root.resolve(), args.study_dir.resolve())
        )
    except (ValueError, OSError, RuntimeError, ImportError) as exc:
        print(
            json.dumps(
                {"status": "audit_bundle_screen_failed_closed", "error_type": type(exc).__name__}
            )
        )
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
