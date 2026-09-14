#!/usr/bin/env python3
"""Audit completed V3.2 relations and prepare private, held human packets offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.claim_support_v3_2_packets import (
    build_artifacts,
    checked_destination,
    publish_artifacts,
    verify_artifacts,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("audit", "prepare", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--cohort-run-dir", type=Path, required=True)
    parser.add_argument("--qualification-run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    cohort_run = args.cohort_run_dir.resolve()
    qualification_run = args.qualification_run_dir.resolve()
    try:
        files = build_artifacts(root, cohort_run, qualification_run)
        report = json.loads(files["sealed-evaluator/closeout.json"])
        manifest = json.loads(files["manifest.json"])
        disposition = "not_written"
        if args.command != "audit":
            if args.output_dir is None:
                raise ValueError("output directory is required")
            destination = checked_destination(
                args.output_dir, (root, cohort_run, qualification_run)
            )
            if args.command == "prepare":
                disposition = publish_artifacts(destination, files)
            else:
                verify_artifacts(destination, files)
                disposition = "verified"
        print(
            json.dumps(
                {
                    "status": report["status"] if args.command == "audit" else manifest["status"],
                    "closeout_sha256": report["closeout_sha256"],
                    "manifest_sha256": manifest["manifest_sha256"],
                    "terminal_request_count": report["terminal_request_count"],
                    "automatic_label_count": report["automatic_label_count"],
                    "technical_failure_count": report["technical_failure_count"],
                    "structural_failure_count": report["structural_failure_count"],
                    "selection_blocker": report["selection_blocker"],
                    "selected_main_count": len(report["selected_entry_sha256"]),
                    "selected_onboarding_count": len(report["onboarding_entry_sha256"]),
                    "sample_materialized": args.command != "audit"
                    and manifest["sample_materialized"],
                    "blind_packets_generated": args.command != "audit"
                    and manifest["blind_packets_generated"],
                    "publication_disposition": disposition,
                    "main_packet_release_authorized": False,
                    "provider_calls_executed": False,
                    "human_annotations_collected": False,
                    "externally_delivered": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if report["selection_blocker"] is None else 2
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "v3_2_closeout_failed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
