#!/usr/bin/env python3
"""Bounded local MLServer application development and hash-only private replay.

Only run and verify are public commands. Internal workers receive fresh trusted
artifacts prepared by run; arbitrary caller-supplied pickle is not an input.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.model_load_application_analysis import run, verify
from aletheia_lab.evaluation.model_load_evidence_analysis import publish_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        if (
            args.report is None
            or args.report.resolve().is_relative_to(args.root.resolve())
            or args.report.is_symlink()
        ):
            raise ValueError("new private report must be outside repository")
        if args.command == "run":
            if args.report.exists():
                raise ValueError("cannot replace a prior report")
            report = run(args.root.resolve())
            publish_report(args.report, report)
        else:
            report = verify(args.root.resolve(), args.report)
        print(
            json.dumps(
                {
                    "status": report["summary"]["disposition"],
                    "report_sha256": report["report_sha256"],
                    "plan_sha256": report["plan_sha256"],
                    "summary": report["summary"],
                    "comparison": report["comparison"],
                    "provider_calls": 0,
                },
                indent=2,
            )
        )
        return (
            0
            if report["implementation_unchanged"]
            and report["summary"]["disposition"]
            == "bounded_application_capture_transfer_no_new_checker_advantage"
            else 1
        )
    except (ValueError, RuntimeError, KeyError, OSError, TypeError) as exc:
        print(
            json.dumps(
                {
                    "status": "application_development_failed_closed",
                    "error_type": type(exc).__name__,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
