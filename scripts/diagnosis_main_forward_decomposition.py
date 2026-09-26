#!/usr/bin/env python3
"""Reconcile the original and forward-recovery P5 runs without pooling them."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from aletheia_lab.evaluation._diagnosis_main_analysis_contracts import (
    DiagnosisMainAnalysisCensus,
    DiagnosisMainAnalysisInput,
    DiagnosisMainAnalysisPlan,
    DiagnosisMainAnalysisReport,
)
from aletheia_lab.evaluation.diagnosis_main_forward_decomposition import (
    decompose_locked_runs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--census-packet", type=Path, required=True)
    parser.add_argument("--original-input", type=Path, required=True)
    parser.add_argument("--original-report", type=Path, required=True)
    parser.add_argument("--recovery-input", type=Path, required=True)
    parser.add_argument("--recovery-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        inputs = (
            args.plan,
            args.census_packet,
            args.original_input,
            args.original_report,
            args.recovery_input,
            args.recovery_report,
        )
        if any(path.is_symlink() or not path.is_file() for path in inputs):
            raise ValueError("a required immutable input is unavailable")
        repo = Path(__file__).resolve().parents[1]
        output_parent = args.output.parent.resolve(strict=True)
        if output_parent.is_relative_to(repo) or args.output.is_symlink():
            raise ValueError("aggregate output must stay outside the public repository")
        packet = json.loads(args.census_packet.read_text(encoding="utf-8"))
        summary = decompose_locked_runs(
            plan=DiagnosisMainAnalysisPlan.model_validate_json(args.plan.read_bytes()),
            census=DiagnosisMainAnalysisCensus.model_validate_json(
                json.dumps(packet["analysis_census"])
            ),
            original_input=DiagnosisMainAnalysisInput.model_validate_json(
                args.original_input.read_bytes()
            ),
            original_report=DiagnosisMainAnalysisReport.model_validate_json(
                args.original_report.read_bytes()
            ),
            recovery_input=DiagnosisMainAnalysisInput.model_validate_json(
                args.recovery_input.read_bytes()
            ),
            recovery_report=DiagnosisMainAnalysisReport.model_validate_json(
                args.recovery_report.read_bytes()
            ),
        )
        encoded = (json.dumps(summary, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(args.output, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
        print(
            json.dumps(
                {
                    "status": summary["status"],
                    "original_claims": summary["original_registered_attempt"]["raw_claim_count"],
                    "recovery_claims": summary["forward_technical_recovery"]["raw_claim_count"],
                    "primary_effect_reconciled": True,
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "failed_closed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
