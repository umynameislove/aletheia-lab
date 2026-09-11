#!/usr/bin/env python3
"""Plan, rehearse and qualify the prospective V3 measurement pipeline."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.claim_validation_v3_execution import adapter_for, execute, verify
from aletheia_lab.evaluation.claim_validation_v3_qualification import (
    FALSE_FLAGS,
    audit_failed_qualification,
    authorize,
    build_plan,
    checked_run,
    prepare_requests,
    publish,
    read_document,
    rehearse,
    validate_authority,
    verify_failure_closeout,
    verify_protocol,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "audit-failed-qualification",
            "verify-failure-closeout",
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
    parser.add_argument("--retired-run", type=Path)
    parser.add_argument("--predecessor-closeout", type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--cost-ceiling-usd", type=float)
    parser.add_argument("--confirm-plan-sha256")
    parser.add_argument("--confirm-rehearsal-sha256")
    parser.add_argument("--confirm-authorization-sha256")
    return parser


def _print(payload: dict[str, Any]) -> None:
    omitted = {"implementation_bindings", "outcomes"}
    print(
        json.dumps({k: v for k, v in payload.items() if k not in omitted}, sort_keys=True, indent=2)
    )


def _error_code(exc: Exception) -> str:
    known = {
        "credential is absent": "credential_absent",
        "attempt already registered; use verify": "registered_attempt_use_verify",
        "attempt already registered; verify, never restart": "registered_attempt_use_verify",
        "plan/rehearsal confirmation mismatch": "plan_rehearsal_confirmation_mismatch",
        "authorization confirmation differs": "authorization_confirmation_mismatch",
        "operator budget does not cover qualification": "operator_budget_insufficient",
        "authorize requires clean synchronized main": "clean_synchronized_main_required",
        "live execution requires the authorized clean synchronized main": "authorized_main_required",
        "V3 protocol or source capacity differs from freeze": "frozen_protocol_mismatch",
        "V3 requires the exact immutable V2 extraction closeout": "predecessor_closeout_mismatch",
        "qualification directory contains unknown files or links": "unsafe_run_destination",
        "qualification run overlaps repository or predecessor": "unsafe_run_destination",
        "qualification authorization is absent": "authorization_absent",
    }
    return known.get(str(exc), "frozen_input_or_runtime_contract_failed")


def _live(root: Path, run: Path, plan: dict[str, Any], auth: dict[str, Any]) -> None:
    validate_authority(root, run, plan, auth)
    if any((run / name).exists() for name in ("lease.json", "attempt-store", "receipt.json")):
        raise ValueError("attempt already registered; use verify")
    if not bool(os.environ.get("OPENAI_API_KEY")):
        raise ValueError("credential is absent")
    prepare_requests(root, plan, auth)
    adapter_for(root, plan, auth)  # SDK/environment validation; no request is sent.


def _authorize(
    args: argparse.Namespace, root: Path, run: Path, plan: dict[str, Any], rehearsal: dict[str, Any]
) -> dict[str, Any]:
    if (
        args.confirm_plan_sha256 != plan["plan_sha256"]
        or args.confirm_rehearsal_sha256 != rehearsal["rehearsal_sha256"]
    ):
        raise ValueError("plan/rehearsal confirmation mismatch")
    if args.cost_ceiling_usd is None:
        raise ValueError("operator cost ceiling is required")
    auth = authorize(root, run, plan, args.cost_ceiling_usd)
    run.mkdir(parents=True, exist_ok=True)
    publish(run / "authorization.json", auth)
    return auth


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    run = None
    try:
        if args.command == "audit-failed-qualification":
            if args.retired_run is None:
                raise ValueError("retired V3 run directory is required")
            _print(audit_failed_qualification(root, args.retired_run.resolve()))
            return 0
        if args.command == "verify-failure-closeout":
            _print(verify_failure_closeout(root))
            return 0
        if args.command == "verify-protocol":
            _print(verify_protocol(root))
            return 0
        if args.predecessor_closeout is None or args.run_dir is None:
            raise ValueError("predecessor closeout and private run directory are required")
        run = checked_run(root, args.run_dir, args.predecessor_closeout)
        auth = (
            read_document(run / "authorization.json", "authorization_sha256")
            if (run / "authorization.json").exists()
            else None
        )
        source_commit = auth["source_commit_ref"] if auth and args.command == "verify" else None
        plan = build_plan(root, args.predecessor_closeout, source_commit=source_commit)
        if args.command == "plan":
            _print(plan)
            return 0
        rehearsal = rehearse(plan, root)
        if args.command == "rehearse":
            _print(rehearsal)
            return 0
        if args.command == "authorize":
            _print(_authorize(args, root, run, plan, rehearsal))
            return 0
        if auth is None:
            raise ValueError("qualification authorization is absent")
        if args.command == "verify":
            receipt = verify(root, run, plan, auth)
        else:
            _live(root, run, plan, auth)
            if args.command == "require-live-ready":
                _print(
                    {
                        "status": "v3_1_qualification_live_ready",
                        "request_count": 33,
                        "source_request_count": 21,
                        "relation_request_count": 12,
                        "credential_present": True,
                        "authorization_sha256": auth["authorization_sha256"],
                        "estimated_upper_cost_usd": plan["estimated_upper_cost_usd"],
                        "provider_calls_executed": False,
                        **FALSE_FLAGS,
                    }
                )
                return 0
            receipt = execute(
                root,
                run,
                plan,
                auth,
                confirmation=args.confirm_authorization_sha256,
                adapter=adapter_for(root, plan, auth),
            )
        _print(receipt)
        return 0 if receipt["cohort_planning_unlocked"] else 2
    except (OSError, ValueError, RuntimeError) as exc:
        registered = bool(run is not None and (run / "lease.json").exists())
        # Arbitrary SDK error strings must never enter operator logs.
        _print(
            {
                "status": "v3_qualification_blocked",
                "error_type": type(exc).__name__,
                "blocker_code": _error_code(exc),
                "operation": args.command,
                "execution_registered": registered,
                "provider_calls_executed": None if registered else False,
                **FALSE_FLAGS,
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
