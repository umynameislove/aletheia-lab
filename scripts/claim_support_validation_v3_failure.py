#!/usr/bin/env python3
"""Verify and close the immutable failed V3.1 source cohort."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.claim_support_v3_cohort import (
    build_cohort_plan,
    checked_cohort_run,
    load_authorization,
    load_verified_qualification,
    rehearse_cohort,
)
from aletheia_lab.evaluation.claim_support_v3_cohort_execution import (
    verify_source_cohort,
)
from aletheia_lab.evaluation.claim_support_v3_cohort_failure import (
    audit_failed_source_cohort,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--predecessor-closeout", type=Path, required=True)
    parser.add_argument("--qualification-run-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    return parser


def _print(payload: dict[str, Any]) -> None:
    omitted = {"failure_cases", "outcomes"}
    print(
        json.dumps(
            {key: value for key, value in payload.items() if key not in omitted},
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
    )


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    try:
        predecessor = args.predecessor_closeout.resolve()
        qualification_run = args.qualification_run_dir.resolve()
        run = checked_cohort_run(root, args.run_dir, qualification_run, predecessor)
        authorization = load_authorization(run / "authorization.json")
        qualification = load_verified_qualification(root, predecessor, qualification_run)
        plan = build_cohort_plan(
            root,
            qualification,
            source_commit_ref=authorization["source_commit_ref"],
        )
        rehearsal = rehearse_cohort(root, plan, qualification)
        receipt = verify_source_cohort(root, run, plan, rehearsal)
        _print(audit_failed_source_cohort(root, run, receipt))
        return 0
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        _print(
            {
                "status": "v3_1_source_cohort_failure_audit_blocked",
                "error_type": type(exc).__name__,
                "rerun_forbidden": True,
                "new_provider_execution_authorized": False,
                "claims_materialized": False,
                "blind_packets_generated": False,
                "human_annotations_collected": False,
                "main_or_sealed_outcomes_opened": False,
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
