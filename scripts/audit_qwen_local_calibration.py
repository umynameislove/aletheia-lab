#!/usr/bin/env python3
"""Validate a private Qwen development-calibration receipt without exposing outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.execution_contracts import canonical_execution_json
from aletheia_lab.evaluation.qwen_calibration_correction import (
    load_and_verify_technical_correction,
)
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
        default=Path("configs/evaluation/diagnosis_qwen_local_sensitivity_candidate_v2.json"),
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
    parser.add_argument(
        "--technical-correction",
        type=Path,
        default=Path(
            "configs/evaluation/diagnosis_qwen38_calibration_technical_correction.json"
        ),
    )
    args = parser.parse_args()
    try:
        candidate = load_and_verify_candidate(args.candidate)
        technical_correction = load_and_verify_technical_correction(
            args.technical_correction,
            candidate=candidate,
            repository_root=Path(__file__).resolve().parents[1],
        )
        report = validate_calibration_receipt(
            receipt=_load_object(args.receipt),
            candidate=candidate,
            technical_correction=technical_correction,
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
