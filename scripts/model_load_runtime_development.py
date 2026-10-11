#!/usr/bin/env python3
"""Run new local native-cost/concurrent-retention development, not validation."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from aletheia_lab.evaluation.model_load_evidence_analysis import publish_report
from aletheia_lab.evaluation.model_load_provenance import document_digest
from aletheia_lab.evaluation.model_load_retention_concurrency import run_concurrency
from aletheia_lab.evaluation.model_load_runtime_cost import (
    code_bindings,
    run_native_cost,
    verify_report,
    worker,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "verify", "worker"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--backend")
    parser.add_argument("--mode")
    args = parser.parse_args()
    try:
        if args.command == "worker":
            if args.directory is None or args.backend is None or args.mode is None:
                raise ValueError(
                    "worker requires a fresh artifact directory and fixed configuration"
                )
            print(json.dumps(worker(args.backend, args.mode, args.directory)))
            return 0
        if args.report is None or args.report.resolve().is_relative_to(args.root.resolve()):
            raise ValueError("aggregate private report must be outside repository")
        if args.report.resolve().is_relative_to(
            (args.root.parent / "memory/model-load-validation-v1").resolve()
        ):
            raise ValueError("historical validation store is not a development destination")
        if args.command == "run":
            if args.report.exists() or args.report.is_symlink():
                raise ValueError("cannot replace a prior development report")
            bindings = code_bindings(args.root)
            native = run_native_cost(args.root)
            concurrency = run_concurrency()
            report = {
                "schema_version": "model-load-runtime-development/v1",
                "status": "development_complete",
                "code_bindings": bindings,
                "native_cost": native,
                "concurrency": concurrency,
                "provider_calls": 0,
                "protected_validation_executed": False,
                "scope": "exposed local development; native cost serialized; authored producer concurrency; not validation/production generalization",
            }
            if bindings != code_bindings(args.root):
                report["status"] = "source_changed_partial_evidence"
            if native["status_counts"] != {"completed": 72}:
                report["status"] = "development_incomplete_failures_preserved"
            if concurrency["status"] != "concurrency_development_complete":
                report["status"] = "development_incomplete_failures_preserved"
            report["report_sha256"] = document_digest(report)
            publish_report(args.report, report)
        else:
            report = verify_report(args.root, args.report)
        print(
            json.dumps(
                {
                    key: report[key]
                    for key in (
                        "status",
                        "report_sha256",
                        "provider_calls",
                        "protected_validation_executed",
                    )
                },
                indent=2,
            )
        )
        return 0 if report["status"] == "development_complete" else 1
    except (ValueError, RuntimeError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        print(
            json.dumps(
                {"status": "runtime_development_failed_closed", "error_type": type(exc).__name__}
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
