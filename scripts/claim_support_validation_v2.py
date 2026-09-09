#!/usr/bin/env python3
"""Verify the prospective V2 freeze or reproduce its closed V1 audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Final

from aletheia_lab.evaluation.claim_validation_v2 import (
    V1_AUDIT_PATH,
    ClaimValidationV2Error,
    build_closed_v1_failure_audit,
    load_v1_failure_audit,
    verify_tracked_v2_protocol,
)

ROOT: Final = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "audit-v1"))
    parser.add_argument("--recovery-closeout", type=Path)
    parser.add_argument("--feasibility-closeout", type=Path)
    parser.add_argument("--diagnosis-store", type=Path)
    return parser


def _require_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    paths = (
        args.recovery_closeout,
        args.feasibility_closeout,
        args.diagnosis_store,
    )
    if any(path is None for path in paths):
        raise ClaimValidationV2Error(
            "audit-v1 requires recovery-closeout, feasibility-closeout and diagnosis-store"
        )
    recovery, feasibility, store = paths
    assert recovery is not None and feasibility is not None and store is not None
    return recovery, feasibility, store


def _run(args: argparse.Namespace) -> dict[str, object]:
    if args.command == "verify":
        return verify_tracked_v2_protocol(ROOT)
    recovery, feasibility, store = _require_paths(args)
    observed = build_closed_v1_failure_audit(
        recovery_closeout_path=recovery,
        feasibility_closeout_path=feasibility,
        diagnosis_store=store,
    )
    tracked = load_v1_failure_audit(ROOT / V1_AUDIT_PATH)
    if observed != tracked:
        raise ClaimValidationV2Error("private V1 artifacts differ from the tracked audit")
    return observed.model_dump(mode="json")


def main() -> int:
    args = _parser().parse_args()
    try:
        payload = _run(args)
    except (ClaimValidationV2Error, OSError) as exc:
        payload = {
            "status": "claim_support_validation_v2_verification_failed",
            "error": type(exc).__name__,
            "message": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
        return 1
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
