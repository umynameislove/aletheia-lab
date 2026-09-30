#!/usr/bin/env python3
"""Write an aggregate-only, private P5 partial-support specification curve."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from aletheia_lab.evaluation._diagnosis_main_analysis_contracts import (
    DiagnosisMainAnalysisCensus,
    DiagnosisMainAnalysisInput,
    DiagnosisMainAnalysisPlan,
    DiagnosisMainAnalysisReport,
)
from aletheia_lab.evaluation.diagnosis_main_partial_sensitivity import (
    analyse_partial_support_curve,
)


def _read(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("a required historical input is unavailable")
    return path.read_bytes()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        repo = Path(__file__).resolve().parents[1]
        memory = args.memory_root.resolve(strict=True)
        output_parent = args.output.parent.resolve(strict=True)
        if memory.is_relative_to(repo) or output_parent.is_relative_to(repo):
            raise ValueError("private sources and output must remain outside the repository")
        plan = DiagnosisMainAnalysisPlan.model_validate_json(
            _read(repo / "configs/evaluation/diagnosis_main_analysis_plan_v3.json")
        )
        packet = json.loads(
            _read(memory / "diagnosis-main-p4-census-v1/private-census-packet.json")
        )
        census = DiagnosisMainAnalysisCensus.model_validate_json(
            json.dumps(packet["analysis_census"])
        )
        original = memory / "diagnosis-main-registered-run-v1"
        recovery = memory / "diagnosis-main-recovery-scoring-v1"
        summary = analyse_partial_support_curve(
            plan=plan,
            census=census,
            original_input=DiagnosisMainAnalysisInput.model_validate_json(
                _read(original / "analysis-input.json")
            ),
            original_report=DiagnosisMainAnalysisReport.model_validate_json(
                _read(original / "analysis-report.json")
            ),
            recovery_input=DiagnosisMainAnalysisInput.model_validate_json(
                _read(recovery / "analysis-input.json")
            ),
            recovery_report=DiagnosisMainAnalysisReport.model_validate_json(
                _read(memory / "diagnosis-main-recovery-analysis-v1/analysis-report.json")
            ),
        )
        encoded = (json.dumps(summary, sort_keys=True, indent=2) + "\n").encode()
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
                    "output_sha256": hashlib.sha256(encoded).hexdigest(),
                    "historical_anchors_reconciled": True,
                    "point_estimate_at_zero": summary["curve"][0]["B1_minus_A3"],
                    "point_estimate_at_one": summary["curve"][-1]["B1_minus_A3"],
                    "all_weights_point_estimate_negative": summary[
                        "all_weights_point_estimate_negative"
                    ],
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
