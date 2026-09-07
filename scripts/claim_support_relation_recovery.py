#!/usr/bin/env python3
"""Close, authorize, recover, and verify one transient relation terminal."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from aletheia_lab.evaluation.claim_corpus_execution import inspect_repository_state
from aletheia_lab.evaluation.claim_corpus_readiness import FAIRNESS_PATH
from aletheia_lab.evaluation.claim_relation_provider import PacedProviderAdapter
from aletheia_lab.evaluation.claim_relation_recovery import (
    ClaimRelationRecoveryError,
    acquire_recovery_lease,
    build_predecessor_closeout,
    build_recovery_authorization,
    build_recovery_plan,
    build_recovery_preflight,
    build_targeted_gateway_request,
    checked_recovery_run_directory,
    load_predecessor_closeout,
    load_recovery_authorization,
    load_recovery_preparation,
    publish_relation_result,
    rehearse_targeted_recovery,
    validate_predecessor_closeout,
)
from aletheia_lab.evaluation.claim_relation_recovery_execution import (
    execute_targeted_recovery,
    finalize_targeted_recovery,
    verify_targeted_recovery,
)
from aletheia_lab.evaluation.execution_contracts import ModelPolicyReference
from aletheia_lab.evaluation.variant_fairness import load_diagnosis_variant_freeze
from aletheia_lab.model_gateway import (
    OpenAIChatCompletionsGatewayAdapter,
    OpenAIGatewayPolicy,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "closeout",
            "plan",
            "rehearse",
            "authorize",
            "require-live-ready",
            "execute",
            "finalize",
            "verify",
        ),
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--preparation", type=Path, required=True)
    parser.add_argument("--predecessor-run", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--cost-ceiling-usd", type=float)
    parser.add_argument("--confirm-plan-sha256")
    parser.add_argument("--confirm-rehearsal-sha256")
    parser.add_argument("--confirm-authorization-sha256")
    return parser


def _print(payload: object) -> None:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    print(json.dumps(payload, indent=2, sort_keys=True))


def _adapter(
    root: Path,
    model_policy: ModelPolicyReference,
) -> OpenAIChatCompletionsGatewayAdapter:
    freeze = load_diagnosis_variant_freeze(root / FAIRNESS_PATH)
    provider_policy = OpenAIGatewayPolicy.from_fairness_policy(freeze.model_policies["main_llm_v1"])
    return OpenAIChatCompletionsGatewayAdapter.from_environment(
        model_policy=model_policy,
        policy=provider_policy,
    )


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    try:
        preparation_path = args.preparation.resolve()
        predecessor_run = args.predecessor_run.resolve()
        preparation = load_recovery_preparation(preparation_path)
        run_dir = checked_recovery_run_directory(
            root,
            args.run_dir,
            preparation_path=preparation_path,
            predecessor_run=predecessor_run,
        )
        if args.command == "closeout":
            closeout = build_predecessor_closeout(
                root,
                preparation,
                predecessor_run=predecessor_run,
            )
            run_dir.mkdir(parents=True, exist_ok=True)
            disposition = publish_relation_result(run_dir / "predecessor-closeout.json", closeout)
            payload = closeout.model_dump(mode="json")
            payload["publication_disposition"] = disposition
            _print(payload)
            return 0
        if args.command == "verify":
            _print(
                verify_targeted_recovery(
                    root,
                    preparation,
                    predecessor_run=predecessor_run,
                    run_dir=run_dir,
                )
            )
            return 0
        state = inspect_repository_state(root)
        if args.command == "finalize":
            _print(
                finalize_targeted_recovery(
                    root,
                    preparation,
                    predecessor_run=predecessor_run,
                    run_dir=run_dir,
                    repository_state=state,
                )
            )
            return 0
        closeout = load_predecessor_closeout(run_dir / "predecessor-closeout.json")
        validate_predecessor_closeout(
            root,
            preparation,
            predecessor_run=predecessor_run,
            closeout=closeout,
        )
        plan = build_recovery_plan(
            root,
            preparation,
            predecessor_run=predecessor_run,
            closeout=closeout,
            source_commit_ref=state.head_commit,
        )
        if args.command == "plan":
            _print(plan)
            return 0
        rehearsal = rehearse_targeted_recovery(
            root,
            preparation,
            predecessor_run=predecessor_run,
            closeout=closeout,
            plan=plan,
        )
        if args.command == "rehearse":
            _print(rehearsal)
            return 0
        if args.command == "authorize":
            if args.confirm_plan_sha256 != plan.plan_sha256:
                raise ClaimRelationRecoveryError("recovery-plan confirmation differs")
            if args.confirm_rehearsal_sha256 != rehearsal.rehearsal_sha256:
                raise ClaimRelationRecoveryError("recovery-rehearsal confirmation differs")
            if args.cost_ceiling_usd is None:
                raise ClaimRelationRecoveryError("authorize requires a cost ceiling")
            created_authorization = build_recovery_authorization(
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
            disposition = publish_relation_result(
                run_dir / "authorization.json", created_authorization
            )
            payload = created_authorization.model_dump(mode="json")
            payload["publication_disposition"] = disposition
            _print(payload)
            return 0
        authorization_path = run_dir / "authorization.json"
        authorization = (
            load_recovery_authorization(authorization_path)
            if authorization_path.is_file()
            else None
        )
        preflight = build_recovery_preflight(
            plan,
            rehearsal,
            repository_state=state,
            credential_present=bool(os.environ.get("OPENAI_API_KEY")),
            authorization=authorization,
            run_dir=run_dir,
        )
        if args.command == "require-live-ready":
            if not preflight.live_blockers and authorization is not None:
                target = build_targeted_gateway_request(
                    root,
                    preparation,
                    predecessor_run=predecessor_run,
                    authorization=authorization,
                )
                _adapter(root, target.request.initial_attempt.model_policy)
            _print(preflight)
            return 0 if not preflight.live_blockers else 2
        if authorization is None:
            raise ClaimRelationRecoveryError("targeted recovery authorization is unavailable")
        if args.confirm_authorization_sha256 != authorization.authorization_sha256:
            raise ClaimRelationRecoveryError("recovery-authorization confirmation differs")
        if preflight.live_blockers:
            raise ClaimRelationRecoveryError("targeted recovery preflight remains blocked")
        target = build_targeted_gateway_request(
            root,
            preparation,
            predecessor_run=predecessor_run,
            authorization=authorization,
        )
        base_adapter = _adapter(root, target.request.initial_attempt.model_policy)
        adapter = PacedProviderAdapter(
            base_adapter,
            minimum_interval_seconds=plan.minimum_provider_interval_ms / 1000,
        )
        acquire_recovery_lease(run_dir, authorization)
        receipt, _, _ = execute_targeted_recovery(
            root,
            preparation,
            predecessor_run=predecessor_run,
            closeout=closeout,
            authorization=authorization,
            run_dir=run_dir,
            adapter=adapter,
        )
        _print(receipt)
        return 0
    except (ClaimRelationRecoveryError, OSError, ValueError) as exc:
        _print({"status": "claim_relation_targeted_recovery_failed", "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
