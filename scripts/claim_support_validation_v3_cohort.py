#!/usr/bin/env python3
"""Plan, authorize, execute or verify the V3.1 authentic source cohort."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.claim_corpus_execution import inspect_repository_state
from aletheia_lab.evaluation.claim_support_v3_cohort import (
    ClaimSupportV3CohortError,
    build_authorization,
    build_cohort_plan,
    build_preflight,
    checked_cohort_run,
    load_authorization,
    load_verified_qualification,
    rehearse_cohort,
)
from aletheia_lab.evaluation.claim_support_v3_cohort_execution import (
    build_openai_adapter,
    execute_source_cohort,
    prepare_source_requests,
    verify_source_cohort,
)
from aletheia_lab.evaluation.claim_validation_v3_qualification import publish


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
    parser.add_argument("--predecessor-closeout", type=Path, required=True)
    parser.add_argument("--qualification-run-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--cost-ceiling-usd", type=float)
    parser.add_argument("--confirm-plan-sha256")
    parser.add_argument("--confirm-rehearsal-sha256")
    parser.add_argument("--confirm-authorization-sha256")
    return parser


def _print(payload: dict[str, Any]) -> None:
    omitted = {"implementation_bindings", "outcomes", "request_projection_sha256s"}
    print(
        json.dumps(
            {key: value for key, value in payload.items() if key not in omitted},
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
    )


def _blocker_code(exc: Exception) -> str:
    known = {
        "source cohort authorization is invalid": "authorization_absent_or_invalid",
        "authorization confirmation differs": "authorization_confirmation_mismatch",
        "source cohort rehearsal differs from plan": "plan_rehearsal_mismatch",
        "cohort destination contains unknown files or links": "unsafe_run_destination",
        "cohort destination overlaps repository or immutable input": "unsafe_run_destination",
        "live execution requires the authorized clean synchronized main": (
            "authorized_main_required"
        ),
        "authorization requires a fresh destination and clean synchronized main": (
            "authorization_preconditions_failed"
        ),
        "partial source request state forbids continuation": (
            "partial_request_state_requires_audit"
        ),
    }
    return known.get(str(exc), "frozen_input_or_runtime_contract_failed")


def _plan_inputs(
    root: Path,
    predecessor: Path,
    qualification_run: Path,
    *,
    source_commit_ref: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    qualification = load_verified_qualification(root, predecessor, qualification_run)
    plan = build_cohort_plan(root, qualification, source_commit_ref=source_commit_ref)
    return qualification, plan, rehearse_cohort(root, plan, qualification)


def _authorize(
    args: argparse.Namespace,
    root: Path,
    run: Path,
    plan: dict[str, Any],
    rehearsal: dict[str, Any],
) -> dict[str, Any]:
    if (
        args.confirm_plan_sha256 != plan["plan_sha256"]
        or args.confirm_rehearsal_sha256 != rehearsal["rehearsal_sha256"]
    ):
        raise ClaimSupportV3CohortError("plan/rehearsal confirmation differs")
    if args.cost_ceiling_usd is None:
        raise ClaimSupportV3CohortError("operator cost ceiling is required")
    authorization = build_authorization(
        root,
        run,
        plan,
        rehearsal,
        repository_state=inspect_repository_state(root),
        operator_cost_ceiling_usd=args.cost_ceiling_usd,
    )
    run.mkdir(parents=True, exist_ok=True)
    disposition = publish(run / "authorization.json", authorization)
    return {**authorization, "publication_disposition": disposition}


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    run: Path | None = None
    execution_may_have_started = False
    try:
        predecessor = args.predecessor_closeout.resolve()
        qualification_run = args.qualification_run_dir.resolve()
        run = checked_cohort_run(root, args.run_dir, qualification_run, predecessor)
        state = inspect_repository_state(root)
        authorization = (
            load_authorization(run / "authorization.json")
            if (run / "authorization.json").is_file()
            else None
        )
        source_commit = (
            authorization["source_commit_ref"]
            if authorization is not None and args.command == "verify"
            else state.head_commit
        )
        _, plan, rehearsal = _plan_inputs(
            root,
            predecessor,
            qualification_run,
            source_commit_ref=source_commit,
        )
        if args.command == "plan":
            _print(plan)
            return 0
        if args.command == "rehearse":
            _print(rehearsal)
            return 0
        if args.command == "authorize":
            _print(_authorize(args, root, run, plan, rehearsal))
            return 0
        if args.command == "verify":
            receipt = verify_source_cohort(root, run, plan, rehearsal)
            _print(receipt)
            return 0 if receipt["relation_planning_unlocked"] else 2
        preflight = build_preflight(
            root,
            run,
            plan,
            rehearsal,
            authorization,
            repository_state=state,
            credential_present=bool(os.environ.get("OPENAI_API_KEY")),
        )
        if args.command == "require-live-ready" or preflight["live_blockers"]:
            _print(preflight)
            return 0 if not preflight["live_blockers"] else 2
        if not args.confirm_authorization_sha256:
            raise ClaimSupportV3CohortError(
                "execute requires the exact authorization SHA-256 confirmation"
            )
        if authorization is None:
            raise ClaimSupportV3CohortError("source cohort authorization is invalid")
        prepared = prepare_source_requests(root, plan, authorization)
        adapter = build_openai_adapter(root, prepared)
        execution_may_have_started = True
        receipt = execute_source_cohort(
            root,
            run,
            plan,
            rehearsal,
            repository_state=state,
            confirmation=args.confirm_authorization_sha256,
            adapter=adapter,
        )
        _print(receipt)
        return 0 if receipt["relation_planning_unlocked"] else 2
    except (ClaimSupportV3CohortError, OSError, TypeError, ValueError, RuntimeError) as exc:
        registered = bool(run is not None and (run / "lease.json").exists())
        _print(
            {
                "status": "v3_1_source_cohort_blocked",
                "operation": args.command,
                "error_type": type(exc).__name__,
                "blocker_code": _blocker_code(exc),
                "execution_registered": registered,
                "provider_calls_executed": (
                    None if execution_may_have_started or registered else False
                ),
                "claims_materialized": False,
                "blind_packets_generated": False,
                "human_annotations_collected": False,
                "main_or_sealed_outcomes_opened": False,
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
