#!/usr/bin/env python3
"""Freeze, authorize, execute, or verify the V3.2 relation cohort."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.claim_support_v3_2_relation_cohort import (
    PROTECTED_FALSE_FLAGS,
    RELATION_REQUEST_COUNT,
    SOURCE_REQUEST_COUNT,
    authorize,
    build_plan,
    build_protocol,
    checked_run,
    prepare_requests,
    publish,
    read_document,
    rehearse,
    validate_authority,
    verify_protocol,
)
from aletheia_lab.evaluation.claim_support_v3_2_relation_cohort_execution import (
    adapter_for,
    execute,
    verify,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "review",
            "verify-protocol",
            "plan",
            "rehearse",
            "authorize",
            "require-live-ready",
            "execute",
            "verify",
        ),
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--qualification-run-dir", type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--cost-ceiling-usd", type=float)
    parser.add_argument("--confirm-plan-sha256")
    parser.add_argument("--confirm-rehearsal-sha256")
    parser.add_argument("--confirm-authorization-sha256")
    return parser


def _print(payload: dict[str, Any]) -> None:
    omitted = {"implementation_bindings", "outcomes"}
    print(
        json.dumps(
            {key: value for key, value in payload.items() if key not in omitted},
            sort_keys=True,
            indent=2,
        )
    )


def _error_code(exc: Exception) -> str:
    known = {
        "credential is absent": "credential_absent",
        "attempt already registered; use verify": "registered_attempt_use_verify",
        "attempt already registered; verify, never restart": "registered_attempt_use_verify",
        "plan/rehearsal confirmation mismatch": "plan_rehearsal_confirmation_mismatch",
        "authorization confirmation differs": "authorization_confirmation_mismatch",
        "operator budget does not cover relation cohort": "operator_budget_insufficient",
        "authorize requires clean synchronized main": "clean_synchronized_main_required",
        "live execution requires the authorized clean synchronized main": (
            "authorized_main_required"
        ),
        "tracked V3.2 relation cohort protocol differs": "frozen_protocol_mismatch",
        "relation cohort directory contains unknown files or links": (
            "unsafe_run_destination"
        ),
        "relation cohort run overlaps a protected source or repository": (
            "unsafe_run_destination"
        ),
        "relation cohort authorization is absent": "authorization_absent",
        "V3.2 relation qualification did not pass exactly 12 of 12": (
            "qualification_not_passed"
        ),
    }
    return known.get(str(exc), "frozen_input_or_runtime_contract_failed")


def _live(
    root: Path,
    run: Path,
    qualification_run: Path,
    plan: dict[str, Any],
    auth: dict[str, Any],
) -> None:
    validate_authority(root, run, qualification_run, plan, auth)
    if any(
        (run / name).exists() for name in ("lease.json", "attempt-store", "receipt.json")
    ):
        raise ValueError("attempt already registered; use verify")
    if not bool(os.environ.get("OPENAI_API_KEY")):
        raise ValueError("credential is absent")
    prepare_requests(root, plan, auth)
    adapter_for(root, plan, auth)  # Validate SDK and environment without a provider call.


def _authorize(
    args: argparse.Namespace,
    root: Path,
    run: Path,
    qualification_run: Path,
    plan: dict[str, Any],
    rehearsal: dict[str, Any],
) -> dict[str, Any]:
    if (
        args.confirm_plan_sha256 != plan["plan_sha256"]
        or args.confirm_rehearsal_sha256 != rehearsal["rehearsal_sha256"]
    ):
        raise ValueError("plan/rehearsal confirmation mismatch")
    if args.cost_ceiling_usd is None:
        raise ValueError("operator cost ceiling is required")
    auth = authorize(root, run, qualification_run, plan, args.cost_ceiling_usd)
    run.mkdir(parents=True, exist_ok=True)
    publish(run / "authorization.json", auth)
    return auth


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    run: Path | None = None
    try:
        if args.command == "review":
            _print(build_protocol(root))
            return 0
        if args.command == "verify-protocol":
            _print(verify_protocol(root))
            return 0
        if args.qualification_run_dir is None:
            raise ValueError("private qualification run directory is required")
        qualification_run = args.qualification_run_dir.resolve()
        if args.run_dir is None:
            raise ValueError("private relation cohort run directory is required")
        run = checked_run(root, args.run_dir, qualification_run)
        auth = (
            read_document(run / "authorization.json", "authorization_sha256")
            if (run / "authorization.json").exists()
            else None
        )
        source_commit = auth["source_commit_ref"] if auth and args.command == "verify" else None
        plan = build_plan(root, qualification_run, source_commit=source_commit)
        if args.command == "plan":
            _print(plan)
            return 0
        rehearsal = rehearse(plan, root)
        if args.command == "rehearse":
            _print(rehearsal)
            return 0
        if args.command == "authorize":
            _print(_authorize(args, root, run, qualification_run, plan, rehearsal))
            return 0
        if auth is None:
            raise ValueError("relation cohort authorization is absent")
        if args.command == "verify":
            receipt = verify(root, run, qualification_run, plan, auth)
        else:
            _live(root, run, qualification_run, plan, auth)
            if args.command == "require-live-ready":
                _print(
                    {
                        "status": "v3_2_relation_cohort_live_ready",
                        "request_count": RELATION_REQUEST_COUNT,
                        "source_request_count": SOURCE_REQUEST_COUNT,
                        "relation_request_count": RELATION_REQUEST_COUNT,
                        "credential_present": True,
                        "authorization_sha256": auth["authorization_sha256"],
                        "qualification_receipt_sha256": plan[
                            "qualification_receipt_sha256"
                        ],
                        "estimated_upper_cost_usd": plan["estimated_upper_cost_usd"],
                        "provider_calls_executed": False,
                        "relation_execution_authorized": True,
                        "relation_closeout_unlocked": False,
                        **PROTECTED_FALSE_FLAGS,
                    }
                )
                return 0
            receipt = execute(
                root,
                run,
                qualification_run,
                plan,
                auth,
                confirmation=args.confirm_authorization_sha256,
                adapter=adapter_for(root, plan, auth),
            )
        _print(receipt)
        return 0
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        registered = bool(run is not None and (run / "lease.json").exists())
        _print(
            {
                "status": "v3_2_relation_cohort_blocked",
                "error_type": type(exc).__name__,
                "blocker_code": _error_code(exc),
                "operation": args.command,
                "execution_registered": registered,
                "provider_calls_executed": None if registered else False,
                "relation_execution_authorized": False,
                "relation_closeout_unlocked": False,
                **PROTECTED_FALSE_FLAGS,
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
