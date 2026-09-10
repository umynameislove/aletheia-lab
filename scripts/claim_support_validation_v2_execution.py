#!/usr/bin/env python3
"""Execute or independently verify the authorized V2 diagnosis cohort."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from aletheia_lab.evaluation.claim_corpus_execution import inspect_repository_state
from aletheia_lab.evaluation.claim_validation_v2_cohort import (
    ClaimValidationV2CohortError,
    build_cohort_plan,
    build_cohort_preflight,
    checked_cohort_run_directory,
    load_cohort_authorization,
    load_verified_qualification,
    rehearse_cohort,
)
from aletheia_lab.evaluation.claim_validation_v2_cohort_execution import (
    build_openai_cohort_adapter,
    build_v2_cohort_gateway_requests,
    execute_v2_cohort,
    verify_completed_v2_cohort,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("execute", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--qualification-run-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--confirm-authorization-sha256")
    return parser


def _print(payload: object) -> None:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    execution_may_have_started = False
    try:
        run_dir = checked_cohort_run_directory(root, args.run_dir)
        if args.command == "verify":
            receipt = verify_completed_v2_cohort(
                root,
                qualification_run_dir=args.qualification_run_dir,
                run_dir=run_dir,
            )
            _print(receipt)
            return 0
        if not args.confirm_authorization_sha256:
            raise ClaimValidationV2CohortError(
                "execute requires the exact authorization SHA-256 confirmation"
            )
        state = inspect_repository_state(root)
        qualification = load_verified_qualification(
            root, args.qualification_run_dir
        )
        plan = build_cohort_plan(
            root,
            source_commit_ref=state.head_commit,
            qualification_receipt=qualification,
        )
        rehearsal = rehearse_cohort(root, plan, qualification)
        authorization = load_cohort_authorization(run_dir / "authorization.json")
        preflight = build_cohort_preflight(
            plan,
            rehearsal,
            repository_state=state,
            credential_present=bool(os.environ.get("OPENAI_API_KEY")),
            authorization=authorization,
            run_dir=run_dir,
        )
        if preflight.live_blockers:
            _print(preflight)
            return 2
        prepared = build_v2_cohort_gateway_requests(root, plan, authorization)
        adapter = build_openai_cohort_adapter(root, prepared)
        execution_may_have_started = True
        receipt = execute_v2_cohort(
            root,
            repository_state=state,
            qualification_run_dir=args.qualification_run_dir,
            run_dir=run_dir,
            confirm_authorization_sha256=args.confirm_authorization_sha256,
            adapter=adapter,
        )
        _print(receipt)
        return 0
    except (ClaimValidationV2CohortError, OSError, ValueError) as exc:
        _print(
            {
                "status": "claim_support_validation_v2_cohort_execution_failed",
                "error": type(exc).__name__,
                "message": str(exc),
                "provider_calls_executed": (
                    None if execution_may_have_started else False
                ),
                "provider_calls_may_have_executed": execution_may_have_started,
                "claims_materialized": False,
                "blind_packets_generated": False,
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
