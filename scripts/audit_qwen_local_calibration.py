#!/usr/bin/env python3
"""Validate a private Qwen development-calibration receipt without exposing outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.execution_contracts import canonical_execution_json
from aletheia_lab.evaluation.qwen_local_calibration import (
    QwenCalibrationError,
    load_and_verify_candidate,
    validate_calibration_receipt,
)


def _load_object(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise QwenCalibrationError(f"required regular file is unavailable: {path.name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise QwenCalibrationError(f"JSON artifact must be an object: {path.name}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument(
        "--candidate",
        type=Path,
        default=Path("configs/evaluation/diagnosis_qwen_local_sensitivity_candidate.json"),
    )
    parser.add_argument(
        "--development-plan",
        type=Path,
        default=Path("configs/evaluation/diagnosis_development_pilot_plan.json"),
    )
    parser.add_argument(
        "--response-contract",
        type=Path,
        default=Path("configs/evaluation/diagnosis_main_response_contract.json"),
    )
    args = parser.parse_args()
    try:
        report = validate_calibration_receipt(
            receipt=_load_object(args.receipt),
            candidate=load_and_verify_candidate(args.candidate),
            development_plan=_load_object(args.development_plan),
            response_contract=_load_object(args.response_contract),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, QwenCalibrationError) as exc:
        print(canonical_execution_json({"status": "fail", "error_type": type(exc).__name__}))
        return 1
    print(canonical_execution_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
