"""Prepare/replay the prediction-blind validation design; no execution action."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from aletheia_lab.evaluation.model_load_validation import preflight, prepare


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "preflight"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    try:
        operation = prepare if args.action == "prepare" else preflight
        result = operation(args.root, args.plan)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(
            json.dumps(
                {"status": "validation_design_failed_closed", "error_type": type(exc).__name__}
            )
        )
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
