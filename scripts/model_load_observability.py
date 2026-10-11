"""Run/replay bounded local model-load development; no paid or protected execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.model_load_observability import run_development, verify_report
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
                raise ValueError("generated report must remain outside the code repository")
            if args.action == "run" and (path.exists() or not path.parent.is_dir()):
                raise ValueError("report must be a new file in an existing private directory")
        if args.action == "run":
            report = run_development(args.root.resolve())
            if args.report is not None:
                write_new_file(
                    args.report, (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
                )
            result = {
                "status": "controlled_load_development_complete",
                "disposition": report["disposition"],
                "report_sha256": report["report_sha256"],
                **report["summary"],
                "provider_calls": 0,
            }
        else:
            if args.report is None:
                raise ValueError("verify requires a retained report")
            if args.report.stat().st_size > 2_000_000:
                raise ValueError("report exceeds bounded development input size")
            result = verify_report(
                json.loads(args.report.read_text(encoding="utf-8")), root=args.root.resolve()
            )
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(
            json.dumps(
                {"status": "load_development_failed_closed", "error_type": type(exc).__name__}
            )
        )
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
