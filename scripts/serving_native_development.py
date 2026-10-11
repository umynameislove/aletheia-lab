"""Run source-pinned development boundaries or reverify retained public signatures."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from aletheia_lab.evaluation.serving_native_development import run_development, verify_development


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "run":
            with asyncio.Runner() as runner:
                result = runner.run(run_development(args.sources, args.output))
            summary = {
                key: result[key]
                for key in ("status", "planned_conditions", "completed_conditions", "report_sha256")
            }
        else:
            summary = verify_development(args.sources, args.output)
    except (ValueError, OSError, KeyError, TypeError, ImportError) as error:
        print(
            json.dumps({"status": "development_failed_closed", "error_type": type(error).__name__})
        )
        return 1
    print(json.dumps(summary, indent=2))
    return 0 if summary["completed_conditions"] == summary["planned_conditions"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
