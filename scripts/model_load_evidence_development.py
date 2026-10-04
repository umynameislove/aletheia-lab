#!/usr/bin/env python3
"""Close out immutable validation evidence and run bounded collector development."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.model_load_evidence_analysis import closeout, publish_report
from aletheia_lab.evaluation.model_load_provenance import document_digest
from aletheia_lab.evaluation.model_load_retention_development import run_pilot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("closeout", "collector"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--study-dir", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    try:
        if args.output.resolve().is_relative_to(args.root.resolve()):
            raise ValueError("private report must be outside repository")
        if args.command == "closeout":
            if args.plan is None or args.study_dir is None:
                raise ValueError("closeout requires frozen plan and study directory")
            report = closeout(args.root, args.plan, args.study_dir, args.output)
            summary = {
                key: report[key]
                for key in (
                    "report_sha256",
                    "validation_results_sha256",
                    "remaining_gaps",
                    "native_loads_replayed",
                    "provider_calls",
                    "validation_mutated",
                )
            }
        else:
            report = run_pilot(repeats=args.repeats)
            report["report_sha256"] = document_digest(report)
            publish_report(args.output, report)
            summary = {
                key: report[key]
                for key in (
                    "status",
                    "episode_count",
                    "configuration_count",
                    "executed_episode_runs",
                    "report_sha256",
                    "native_loader_entries",
                    "provider_calls",
                )
            }
        print(json.dumps(summary, sort_keys=True, indent=2))
        return 0
    except (ValueError, RuntimeError, KeyError, TypeError, OSError) as exc:
        print(
            json.dumps(
                {"status": "evidence_development_failed_closed", "error_type": type(exc).__name__}
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
