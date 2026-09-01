#!/usr/bin/env python3
"""Run or independently validate the offline diagnosis development pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Final

from aletheia_lab.diagnosis.development import (
    DevelopmentArtifactStore,
    DevelopmentPilotError,
    load_development_plan,
    run_development_pilot,
)
from aletheia_lab.diagnosis.variant_registry import load_variant_registry
from aletheia_lab.evaluation.development_audit import (
    DevelopmentPilotAuditError,
    audit_development_pilot,
    require_development_pilot_ready,
)
from aletheia_lab.evaluation.variant_fairness import load_diagnosis_variant_freeze

ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_PLAN: Final = ROOT / "configs/evaluation/diagnosis_development_pilot_plan.json"
DEFAULT_FREEZE: Final = ROOT / "configs/evaluation/diagnosis_variant_fairness_freeze.json"
DEFAULT_STORE: Final = ROOT / "experiments/evaluation/outputs/diagnosis-development"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "validate", "all"))
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument(
        "--run-id",
        help="content-addressed run identifier; required when the store has multiple runs",
    )
    return parser


def _select_run_id(store: DevelopmentArtifactStore, requested: str | None) -> str:
    if requested is not None:
        return requested
    run_ids = store.list_runs()
    if len(run_ids) != 1:
        raise DevelopmentPilotAuditError(
            "--run-id is required unless the store contains exactly one run"
        )
    return run_ids[0]


def main() -> int:
    args = _parser().parse_args()
    plan = load_development_plan(args.plan)
    freeze = load_diagnosis_variant_freeze(args.freeze)
    registry = load_variant_registry(args.freeze)
    store = DevelopmentArtifactStore(args.store)

    try:
        if args.command in {"run", "all"}:
            terminal = run_development_pilot(plan, freeze, registry, store)
            run_id = terminal.run_id
        else:
            terminal = None
            run_id = _select_run_id(store, args.run_id)
        audit = audit_development_pilot(plan, freeze, registry, store, run_id)
        require_development_pilot_ready(audit)
    except (DevelopmentPilotError, DevelopmentPilotAuditError) as exc:
        print(
            json.dumps(
                {
                    "status": "development_pilot_blocked",
                    "error_class": type(exc).__name__,
                    "protected_outcomes_opened": False,
                    "live_provider_calls": 0,
                    "registered_attempts_consumed": 0,
                    "scientific_interpretation_permitted": False,
                },
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    payload: dict[str, object] = {
        "audit": audit.model_dump(mode="json"),
        "status": "development_pilot_validated",
    }
    if terminal is not None:
        payload["terminal"] = terminal.model_dump(mode="json")
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
