#!/usr/bin/env python3
"""Audit, authorize, preflight, execute and verify claim-corpus recovery."""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from aletheia_lab.evaluation.claim_corpus_execution import inspect_repository_state
from aletheia_lab.evaluation.claim_corpus_provider_audit import audit_predecessor_provider_failures
from aletheia_lab.evaluation.claim_corpus_readiness import FAIRNESS_PATH
from aletheia_lab.evaluation.claim_corpus_recovery_authorization import (
    RecoveryPhase,
    publish_recovery_json,
)
from aletheia_lab.evaluation.claim_corpus_recovery_budget import (
    audit_retired_compatibility_run,
)
from aletheia_lab.evaluation.claim_corpus_recovery_execution import prepare_recovery_rehearsal
from aletheia_lab.evaluation.claim_corpus_recovery_run import (
    execute_recovery,
    make_recovery_authorization,
    validate_recovery_execution,
    verify_completed_recovery,
)
from aletheia_lab.evaluation.variant_fairness import load_diagnosis_variant_freeze
from aletheia_lab.model_gateway import OpenAIGatewayPolicy
from aletheia_lab.model_gateway.openai_recovery import OpenAIRecoveryAdapter


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "rehearse",
            "audit-predecessor",
            "audit-retired-compatibility",
            "authorize",
            "require-live-ready",
            "execute",
            "verify",
        ),
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--phase", choices=("compatibility", "diagnosis"))
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--predecessor-store", type=Path)
    parser.add_argument("--retired-compatibility-run", type=Path)
    parser.add_argument("--cost-ceiling-usd", type=float)
    parser.add_argument("--confirm-rehearsal-sha256")
    parser.add_argument("--confirm-authorization-sha256")
    return parser


def _required(args: argparse.Namespace, *names: str) -> None:
    if any(getattr(args, name) is None for name in names):
        raise ValueError("required recovery arguments are missing")


def _adapter(root: Path, prepared):  # type: ignore[no-untyped-def]
    request = next(item.request for item in prepared if item.route == "model_gateway")
    freeze = load_diagnosis_variant_freeze(root / FAIRNESS_PATH)
    policy = OpenAIGatewayPolicy.from_fairness_policy(
        freeze.model_policies["main_llm_v1"]
    ).with_recovery_output_budget()
    return OpenAIRecoveryAdapter.from_environment(
        model_policy=request.initial_attempt.model_policy, policy=policy
    )


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    result: dict[str, object]
    try:
        if args.command == "rehearse":
            _, result = prepare_recovery_rehearsal(root)
        elif args.command == "audit-predecessor":
            _required(args, "predecessor_store")
            result = audit_predecessor_provider_failures(args.predecessor_store)
        elif args.command == "audit-retired-compatibility":
            _required(args, "retired_compatibility_run")
            result = audit_retired_compatibility_run(
                root, args.retired_compatibility_run
            )
        elif args.command == "authorize":
            _required(
                args,
                "phase",
                "run_dir",
                "predecessor_store",
                "cost_ceiling_usd",
                "confirm_rehearsal_sha256",
                "retired_compatibility_run",
            )
            _, rehearsal = prepare_recovery_rehearsal(root)
            if args.confirm_rehearsal_sha256 != rehearsal["receipt_sha256"]:
                raise ValueError("rehearsal confirmation differs")
            phase = cast(RecoveryPhase, args.phase)
            authorization = make_recovery_authorization(
                root,
                state=inspect_repository_state(root),
                run_dir=args.run_dir,
                predecessor_store=args.predecessor_store,
                phase=phase,
                authorized_at=datetime.now(UTC).replace(microsecond=0).isoformat().replace(
                    "+00:00", "Z"
                ),
                operator_cost_ceiling_usd=args.cost_ceiling_usd,
                retired_compatibility_run=args.retired_compatibility_run,
            )
            disposition = publish_recovery_json(
                args.run_dir / f"{args.phase}-authorization.json",
                authorization.model_dump(mode="json"),
            )
            result = {
                **authorization.model_dump(mode="json"),
                "publication_disposition": disposition,
                "credential_present": bool(os.environ.get("OPENAI_API_KEY")),
                "provider_calls_executed": False,
            }
        elif args.command == "verify":
            _required(args, "phase", "run_dir")
            result = verify_completed_recovery(
                root,
                state=inspect_repository_state(root),
                run_dir=args.run_dir,
                phase=cast(RecoveryPhase, args.phase),
            )
        else:
            _required(
                args,
                "phase",
                "run_dir",
                "predecessor_store",
                "retired_compatibility_run",
            )
            authorization, prepared = validate_recovery_execution(
                root,
                state=inspect_repository_state(root),
                run_dir=args.run_dir,
                predecessor_store=args.predecessor_store,
                phase=cast(RecoveryPhase, args.phase),
                retired_compatibility_run=args.retired_compatibility_run,
            )
            adapter = _adapter(root, prepared)
            if args.command == "require-live-ready":
                result = {
                    "schema_version": "claim-corpus-recovery-preflight/v1",
                    "status": "claim_corpus_recovery_live_ready",
                    "phase": args.phase,
                    "authorization_sha256": authorization.authorization_sha256,
                    "protocol_sha256": authorization.protocol_sha256,
                    "output_budget_amendment_sha256": (
                        authorization.output_budget_amendment_sha256
                    ),
                    "failed_compatibility_receipt_sha256": (
                        authorization.failed_compatibility_receipt_sha256
                    ),
                    "failed_compatibility_store_sha256": (
                        authorization.failed_compatibility_store_sha256
                    ),
                    "maximum_output_tokens_per_model_request": (
                        authorization.maximum_output_tokens_per_model_request
                    ),
                    "estimated_upper_cost_usd": authorization.estimated_upper_cost_usd,
                    "operator_cost_ceiling_usd": authorization.operator_cost_ceiling_usd,
                    "request_count": len(prepared),
                    "credential_present": True,
                    "provider_calls_executed": False,
                    "claims_materialized": False,
                    "blind_packets_generated": False,
                    "main_or_sealed_outcomes_opened": False,
                }
            else:
                if args.confirm_authorization_sha256 is None:
                    raise ValueError("execution requires the authorization SHA confirmation")
                result = execute_recovery(
                    root,
                    state=inspect_repository_state(root),
                    run_dir=args.run_dir,
                    predecessor_store=args.predecessor_store,
                    phase=cast(RecoveryPhase, args.phase),
                    confirm_authorization_sha256=args.confirm_authorization_sha256,
                    adapter=adapter,
                    retired_compatibility_run=args.retired_compatibility_run,
                )
    except (ValueError, OSError):
        # Paths, provider messages and credentials must not enter terminal output.
        print(json.dumps({"status": "claim_corpus_recovery_gate_failed"}))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
