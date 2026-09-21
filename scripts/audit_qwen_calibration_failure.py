#!/usr/bin/env python3
"""Audit the sanitized Qwen calibration-failure closeout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.qwen_calibration_failure import (
    QwenCalibrationFailureError,
    audit_private_server_log,
    load_and_validate_failure_closeout,
    validate_failure_closeout,
)

DEFAULT_CLOSEOUT = Path(
    "configs/evaluation/diagnosis_qwen38_calibration_failure_closeout.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--closeout", type=Path, default=DEFAULT_CLOSEOUT)
    parser.add_argument("--server-log", type=Path)
    args = parser.parse_args()
    try:
        payload = load_and_validate_failure_closeout(args.closeout)
        report = validate_failure_closeout(payload)
        report["private_server_log_verification"] = (
            audit_private_server_log(payload, args.server_log)
            if args.server_log is not None
            else {"status": "not_requested"}
        )
    except (OSError, KeyError, TypeError, ValueError, QwenCalibrationFailureError) as exc:
        print(json.dumps({"status": "fail", "error_type": type(exc).__name__}))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
