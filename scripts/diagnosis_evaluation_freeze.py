#!/usr/bin/env python3
"""Verify outcome-blind diagnosis feasibility and variant-fairness freezes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Final

from aletheia_lab.evaluation.protocol_feasibility import (
    audit_diagnosis_feasibility,
    load_diagnosis_feasibility_plan,
)
from aletheia_lab.evaluation.variant_fairness import (
    audit_diagnosis_variant_fairness,
    load_diagnosis_variant_freeze,
)

ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_FEASIBILITY: Final = ROOT / "configs/evaluation/diagnosis_protocol_feasibility_plan.json"
DEFAULT_VARIANTS: Final = ROOT / "configs/evaluation/diagnosis_variant_fairness_freeze.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scope",
        choices=("all", "feasibility", "fairness"),
        nargs="?",
        default="all",
    )
    parser.add_argument("--feasibility-plan", type=Path, default=DEFAULT_FEASIBILITY)
    parser.add_argument("--variant-freeze", type=Path, default=DEFAULT_VARIANTS)
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="return a blocking exit code while any pre-outcome readiness blocker remains",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    payload: dict[str, object] = {
        "protected_outcomes_opened": False,
        "execution_authorized": False,
    }
    blocker_count = 0
    if args.scope in {"all", "feasibility"}:
        feasibility = audit_diagnosis_feasibility(
            load_diagnosis_feasibility_plan(args.feasibility_plan),
            repository_root=ROOT,
        )
        payload["feasibility"] = feasibility.model_dump(mode="json")
        blocker_count += len(feasibility.blocker_codes)
    if args.scope in {"all", "fairness"}:
        fairness = audit_diagnosis_variant_fairness(
            load_diagnosis_variant_freeze(args.variant_freeze)
        )
        payload["fairness"] = fairness.model_dump(mode="json")
        blocker_count += len(fairness.blocker_codes)
    payload["status"] = (
        "diagnosis_freezes_verified_with_execution_blockers"
        if blocker_count
        else "diagnosis_freezes_verified_ready_for_registration"
    )
    payload["blocker_count"] = blocker_count
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
    return 2 if args.require_ready and blocker_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
