#!/usr/bin/env python3
"""Plan, authorize, execute, and verify the seven-request V2 qualification."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from aletheia_lab.evaluation.claim_validation_v2_qualification import (
    ClaimValidationV2QualificationError,
    build_qualification_authorization,
    build_qualification_gateway_requests,
    build_qualification_plan,
    build_qualification_preflight,
    checked_qualification_run_directory,
    inspect_repository_state,
    load_qualification_authorization,
    publish_qualification_result,
    rehearse_qualification,
)
from aletheia_lab.evaluation.claim_validation_v2_qualification_execution import (
    build_openai_qualification_adapter,
    execute_qualification,
    verify_completed_qualification,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "plan",
            "rehearse",
            "authorize",
            "require-live-ready",
            "execute",
            "verify",
        ),
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--cost-ceiling-usd", type=float)
    parser.add_argument("--confirm-plan-sha256")
    parser.add_argument("--confirm-rehearsal-sha256")
    parser.add_argument("--confirm-authorization-sha256")
    return parser


def _print(payload: object) -> None:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    run_dir: Path | None = None
    try:
        state = inspect_repository_state(root)
        run_dir = checked_qualification_run_directory(root, args.run_dir)
        plan = build_qualification_plan(root, source_commit_ref=state.head_commit)
        if args.command == "plan":
            _print(plan)
            return 0
        rehearsal = rehearse_qualification(root, plan)
        if args.command == "rehearse":
            _print(rehearsal)
            return 0
        if args.command == "authorize":
            if args.confirm_plan_sha256 != plan.plan_sha256:
                raise ClaimValidationV2QualificationError("qualification plan confirmation differs")
            if args.confirm_rehearsal_sha256 != rehearsal.rehearsal_sha256:
                raise ClaimValidationV2QualificationError(
                    "qualification rehearsal confirmation differs"
                )
            if args.cost_ceiling_usd is None:
                raise ClaimValidationV2QualificationError(
                    "authorize requires an operator cost ceiling"
                )
            authorization = build_qualification_authorization(
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
            disposition = publish_qualification_result(
                run_dir / "authorization.json", authorization
            )
            payload = authorization.model_dump(mode="json")
            payload["publication_disposition"] = disposition
            _print(payload)
            return 0
        if args.command == "verify":
            receipt = verify_completed_qualification(root, repository_state=state, run_dir=run_dir)
            _print(receipt)
            return 0 if receipt.full_cohort_authorization_unlocked else 2
        authorization_path = run_dir / "authorization.json"
        loaded_authorization = (
            load_qualification_authorization(authorization_path)
            if authorization_path.is_file()
            else None
        )
        preflight = build_qualification_preflight(
            plan,
            rehearsal,
            repository_state=state,
            credential_present=bool(os.environ.get("OPENAI_API_KEY")),
            authorization=loaded_authorization,
            run_dir=run_dir,
        )
        if args.command == "require-live-ready":
            if not preflight.live_blockers and loaded_authorization is not None:
                prepared = build_qualification_gateway_requests(root, plan, loaded_authorization)
                build_openai_qualification_adapter(root, prepared)
            _print(preflight)
            return 0 if not preflight.live_blockers else 2
        if loaded_authorization is None:
            raise ClaimValidationV2QualificationError("qualification authorization is unavailable")
        if args.confirm_authorization_sha256 != loaded_authorization.authorization_sha256:
            raise ClaimValidationV2QualificationError(
                "qualification authorization confirmation differs"
            )
        if preflight.live_blockers:
            raise ClaimValidationV2QualificationError(
                "qualification execution preflight remains blocked"
            )
        prepared = build_qualification_gateway_requests(root, plan, loaded_authorization)
        adapter = build_openai_qualification_adapter(root, prepared)
        receipt = execute_qualification(
            root,
            repository_state=state,
            run_dir=run_dir,
            confirm_authorization_sha256=(loaded_authorization.authorization_sha256),
            adapter=adapter,
        )
        _print(receipt)
        return 0 if receipt.full_cohort_authorization_unlocked else 2
    except (ClaimValidationV2QualificationError, OSError, ValueError) as exc:
        execution_registered = bool(run_dir is not None and (run_dir / "lease.json").is_file())
        _print(
            {
                "status": "claim_support_validation_v2_qualification_failed",
                "error": type(exc).__name__,
                "message": str(exc),
                "execution_registered": execution_registered,
                "provider_calls_executed": False if not execution_registered else None,
                "claims_materialized": False,
                "blind_packets_generated": False,
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
