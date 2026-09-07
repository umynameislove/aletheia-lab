#!/usr/bin/env python3
"""Plan, authorize, execute, and verify blind claim-relation assignment."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from aletheia_lab.evaluation.claim_corpus_readiness import FAIRNESS_PATH
from aletheia_lab.evaluation.claim_relation_execution import (
    ClaimRelationExecutionError,
    PacedProviderAdapter,
    PreparedRelationRequest,
    acquire_relation_lease,
    build_relation_authorization,
    build_relation_execution_plan,
    build_relation_gateway_requests,
    build_relation_preflight,
    checked_relation_run_directory,
    execute_relation_census,
    inspect_repository_state,
    load_recovery_preparation,
    load_relation_authorization,
    publish_relation_result,
    rehearse_relation_execution,
    verify_relation_execution,
)
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
            "plan",
            "rehearse",
            "authorize",
            "require-live-ready",
            "execute",
            "verify",
        ),
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--preparation", type=Path, required=True)
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


def _openai_adapter(
    root: Path,
    prepared: tuple[PreparedRelationRequest, ...],
) -> OpenAIChatCompletionsGatewayAdapter:
    freeze = load_diagnosis_variant_freeze(root / FAIRNESS_PATH)
    provider_policy = OpenAIGatewayPolicy.from_fairness_policy(freeze.model_policies["main_llm_v1"])
    return OpenAIChatCompletionsGatewayAdapter.from_environment(
        model_policy=prepared[0].request.initial_attempt.model_policy,
        policy=provider_policy,
    )


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    try:
        preparation_path = args.preparation.resolve()
        preparation = load_recovery_preparation(preparation_path)
        run_dir = checked_relation_run_directory(root, args.run_dir, preparation_path)
        state = inspect_repository_state(root)
        plan = build_relation_execution_plan(root, preparation, source_commit_ref=state.head_commit)
        if args.command == "plan":
            payload = plan.model_dump(mode="json", exclude={"assignment_request_sha256s"})
            payload["assignment_request_identity_count"] = len(plan.assignment_request_sha256s)
            _print(payload)
            return 0
        rehearsal = rehearse_relation_execution(root, preparation, plan)
        if args.command == "rehearse":
            _print(rehearsal)
            return 0
        if args.command == "authorize":
            if args.confirm_plan_sha256 != plan.plan_sha256:
                raise ClaimRelationExecutionError("execution-plan confirmation differs")
            if args.confirm_rehearsal_sha256 != rehearsal.rehearsal_sha256:
                raise ClaimRelationExecutionError("execution-rehearsal confirmation differs")
            if args.cost_ceiling_usd is None:
                raise ClaimRelationExecutionError("authorize requires a cost ceiling")
            authorization = build_relation_authorization(
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
            disposition = publish_relation_result(run_dir / "authorization.json", authorization)
            payload = authorization.model_dump(mode="json")
            payload["publication_disposition"] = disposition
            _print(payload)
            return 0
        authorization_path = run_dir / "authorization.json"
        loaded_authorization = (
            load_relation_authorization(authorization_path)
            if authorization_path.is_file()
            else None
        )
        if args.command == "verify":
            _print(verify_relation_execution(root, preparation, run_dir=run_dir))
            return 0
        preflight = build_relation_preflight(
            plan,
            rehearsal,
            repository_state=state,
            credential_present=bool(os.environ.get("OPENAI_API_KEY")),
            authorization=loaded_authorization,
            run_dir=run_dir,
        )
        if args.command == "require-live-ready":
            if not preflight.live_blockers and loaded_authorization is not None:
                prepared = build_relation_gateway_requests(
                    root, preparation, plan, loaded_authorization
                )
                _openai_adapter(root, prepared)
            _print(preflight)
            return 0 if not preflight.live_blockers else 2
        if loaded_authorization is None:
            raise ClaimRelationExecutionError("execution authorization is unavailable")
        if args.confirm_authorization_sha256 != loaded_authorization.authorization_sha256:
            raise ClaimRelationExecutionError("execution-authorization confirmation differs")
        if preflight.live_blockers:
            raise ClaimRelationExecutionError("relation execution preflight remains blocked")
        prepared = build_relation_gateway_requests(root, preparation, plan, loaded_authorization)
        base_adapter = _openai_adapter(root, prepared)
        adapter = PacedProviderAdapter(
            base_adapter,
            minimum_interval_seconds=plan.minimum_provider_interval_ms / 1000,
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        acquire_relation_lease(run_dir, loaded_authorization)
        receipt, bundle = execute_relation_census(
            preparation,
            prepared,
            authorization=loaded_authorization,
            store_root=run_dir / "attempt-store",
            adapter=adapter,
        )
        publish_relation_result(run_dir / "relation-results.json", bundle)
        publish_relation_result(run_dir / "receipt.json", receipt)
        _print(receipt)
        return 0
    except (ClaimRelationExecutionError, OSError, ValueError) as exc:
        _print({"status": "claim_relation_execution_failed", "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
