#!/usr/bin/env python3
"""Plan and authorize the exact V2 diagnosis cohort without executing it."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from aletheia_lab.evaluation.claim_corpus_execution import inspect_repository_state
from aletheia_lab.evaluation.claim_validation_v2_cohort import (
    ClaimValidationV2CohortError,
    build_cohort_authorization,
    build_cohort_plan,
    build_cohort_preflight,
    checked_cohort_run_directory,
    load_cohort_authorization,
    load_verified_qualification,
    publish_cohort_authorization,
    rehearse_cohort,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("plan", "rehearse", "authorize", "require-live-ready"),
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--qualification-run-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--cost-ceiling-usd", type=float)
    parser.add_argument("--confirm-plan-sha256")
    parser.add_argument("--confirm-rehearsal-sha256")
    return parser


def _print(payload: object) -> None:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    try:
        state = inspect_repository_state(root)
        run_dir = checked_cohort_run_directory(root, args.run_dir)
        qualification = load_verified_qualification(
            root, args.qualification_run_dir
        )
        plan = build_cohort_plan(
            root,
            source_commit_ref=state.head_commit,
            qualification_receipt=qualification,
        )
        if args.command == "plan":
            _print(plan)
            return 0
        rehearsal = rehearse_cohort(root, plan, qualification)
        if args.command == "rehearse":
            _print(rehearsal)
            return 0
        if args.command == "authorize":
            if args.confirm_plan_sha256 != plan.plan_sha256:
                raise ClaimValidationV2CohortError(
                    "V2 cohort plan confirmation differs"
                )
            if args.confirm_rehearsal_sha256 != rehearsal.rehearsal_sha256:
                raise ClaimValidationV2CohortError(
                    "V2 cohort rehearsal confirmation differs"
                )
            if args.cost_ceiling_usd is None:
                raise ClaimValidationV2CohortError(
                    "authorize requires an operator cost ceiling"
                )
            created_authorization = build_cohort_authorization(
                plan,
                rehearsal,
                repository_state=state,
                run_dir=run_dir,
                authorized_at=datetime.now(UTC)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
                operator_cost_ceiling_usd=args.cost_ceiling_usd,
            )
            run_dir.mkdir(parents=True, exist_ok=True)
            disposition = publish_cohort_authorization(
                run_dir / "authorization.json", created_authorization
            )
            payload = created_authorization.model_dump(mode="json")
            payload["publication_disposition"] = disposition
            _print(payload)
            return 0
        authorization_path = run_dir / "authorization.json"
        authorization = (
            load_cohort_authorization(authorization_path)
            if authorization_path.is_file()
            else None
        )
        preflight = build_cohort_preflight(
            plan,
            rehearsal,
            repository_state=state,
            credential_present=bool(os.environ.get("OPENAI_API_KEY")),
            authorization=authorization,
            run_dir=run_dir,
        )
        _print(preflight)
        return 0 if not preflight.live_blockers else 2
    except (ClaimValidationV2CohortError, OSError, ValueError) as exc:
        _print(
            {
                "status": "claim_support_validation_v2_cohort_authorization_failed",
                "error": type(exc).__name__,
                "message": str(exc),
                "provider_calls_executed": False,
                "claims_materialized": False,
                "blind_packets_generated": False,
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
