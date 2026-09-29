#!/usr/bin/env python3
"""Measure offline how P5 claim-label uncertainty could change locked output effects."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation._diagnosis_main_analysis_contracts import (
    DiagnosisMainAnalysisCensus,
    DiagnosisMainAnalysisInput,
    DiagnosisMainAnalysisPlan,
    DiagnosisMainAnalysisReport,
)
from aletheia_lab.evaluation.diagnosis_main_forward_decomposition import decompose_locked_runs
from aletheia_lab.evaluation.diagnosis_main_measurement_impact import (
    analyze_measurement_impact,
)


def _read(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("an immutable input is unavailable")
    return path.read_bytes()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_summary(repo: Path, memory: Path) -> dict[str, Any]:
    audit = memory / "p5-domain-human-audit-v1"
    audit_analysis = audit / "adjudication-v1" / "analysis"
    original = memory / "diagnosis-main-registered-run-v1"
    recovery = memory / "diagnosis-main-recovery-scoring-v1"
    plan = DiagnosisMainAnalysisPlan.model_validate_json(
        _read(repo / "configs/evaluation/diagnosis_main_analysis_plan_v3.json")
    )
    census_packet = json.loads(
        _read(memory / "diagnosis-main-p4-census-v1/private-census-packet.json")
    )
    census = DiagnosisMainAnalysisCensus.model_validate_json(
        json.dumps(census_packet["analysis_census"])
    )
    original_input = DiagnosisMainAnalysisInput.model_validate_json(
        _read(original / "analysis-input.json")
    )
    original_report = DiagnosisMainAnalysisReport.model_validate_json(
        _read(original / "analysis-report.json")
    )
    recovery_input_bytes = _read(recovery / "analysis-input.json")
    recovery_input = DiagnosisMainAnalysisInput.model_validate_json(recovery_input_bytes)
    recovery_report = DiagnosisMainAnalysisReport.model_validate_json(
        _read(memory / "diagnosis-main-recovery-analysis-v1/analysis-report.json")
    )
    decompose_locked_runs(
        plan=plan,
        census=census,
        original_input=original_input,
        original_report=original_report,
        recovery_input=recovery_input,
        recovery_report=recovery_report,
    )
    receipt = json.loads(_read(audit_analysis / "analysis-receipt.json"))
    item_bytes = _read(audit_analysis / "private-item-level.json")
    map_bytes = _read(audit / "output/coordinator-only/sample-map.json")
    report_bytes = _read(audit_analysis / "private-report.json")
    if (
        receipt["status"] != "offline_analysis_complete"
        or receipt["provider_calls"] != 0
        or receipt["registered_p5_outcomes_mutated"]
        or receipt["private_item_level_sha256"] != _sha(item_bytes)
        or receipt["sample_map_byte_sha256"] != _sha(map_bytes)
        or receipt["private_report_sha256"] != _sha(report_bytes)
        or receipt["source_analysis_input_byte_sha256"] != _sha(recovery_input_bytes)
    ):
        raise ValueError("human audit bytes do not match their locked receipt")
    rows = json.loads(item_bytes)["rows"]
    summary = analyze_measurement_impact(
        plan=plan,
        census=census,
        recovery_input=recovery_input,
        recovery_report=recovery_report,
        audit_rows=rows,
        sample_map=json.loads(map_bytes),
        audit_report=json.loads(report_bytes),
    )
    summary["source_byte_sha256"] = {
        "human_item_level": _sha(item_bytes),
        "human_sample_map": _sha(map_bytes),
        "human_audit_report": _sha(report_bytes),
        "recovery_analysis_input": _sha(recovery_input_bytes),
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        repo = Path(__file__).resolve().parents[1]
        memory = args.memory_root.resolve(strict=True)
        output_parent = args.output.parent.resolve(strict=True)
        if (
            memory.is_relative_to(repo)
            or output_parent.is_relative_to(repo)
            or args.output.is_symlink()
            or args.output.exists()
        ):
            raise ValueError("private output must be a new file outside the repository")
        summary = _build_summary(repo, memory)
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
                    "probability_core_claims": summary["sample"][
                        "probability_core_B1_A3_claim_count"
                    ],
                    "primary_bounds": summary["primary_B1_minus_A3"][
                        "assumption_free_label_bounds"
                    ],
                    "private_summary_sha256": _sha(encoded),
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
