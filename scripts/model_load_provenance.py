"""Run/replay prevalidation MLflow/in-toto controls; no provider or historical reads."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.model_load_provenance_study import run_development, verify_report
from aletheia_lab.filesystem import write_new_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        if args.report is not None:
            path = args.report.absolute()
            if any(item.is_symlink() for item in (path, *path.parents)):
                raise ValueError("report path must not contain symlinks")
            if path.resolve().is_relative_to(args.root.resolve()):
                raise ValueError("report must remain outside the repository")
        if args.action == "run":
            if args.report is not None and (
                args.report.exists() or not args.report.parent.is_dir()
            ):
                raise ValueError("report must be a new file in an existing private directory")
            report = run_development(args.root.resolve())
            if args.report is not None:
                write_new_file(args.report, (json.dumps(report, indent=2) + "\n").encode())
            result = {
                "status": "provenance_development_complete",
                "disposition": report["disposition"],
                "validation_locked": False,
                "report_sha256": report["report_sha256"],
                **report["summary"],
            }
        else:
            if args.report is None or args.report.stat().st_size > 2_000_000:
                raise ValueError("replay requires a bounded retained report")
            result = verify_report(
                json.loads(args.report.read_text(encoding="utf-8")), args.root.resolve()
            )
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(
            json.dumps(
                {"status": "provenance_development_failed_closed", "error_type": type(exc).__name__}
            )
        )
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
