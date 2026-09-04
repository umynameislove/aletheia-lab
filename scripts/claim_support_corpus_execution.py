#!/usr/bin/env python3
"""Authorize, inspect, rehearse, or execute the development claim corpus."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from aletheia_lab.evaluation.claim_corpus_execution import (
    ClaimCorpusExecutionAuthorization,
    ClaimCorpusExecutionError,
    ClaimCorpusExecutionPreflight,
    ClaimCorpusExecutionRehearsal,
    build_execution_authorization,
    build_execution_plan,
    build_execution_preflight,
    inspect_repository_state,
    load_execution_authorization,
    load_execution_evidence_census,
    rehearse_execution,
)
from aletheia_lab.evaluation.claim_corpus_live import (
    ClaimCorpusAttemptStore,
    SystemMonotonicClock,
    build_execution_lease,
    build_live_requests,
    run_live_execution,
)
from aletheia_lab.evaluation.claim_corpus_readiness import FAIRNESS_PATH
from aletheia_lab.evaluation.claim_evidence_census import ObservedEvidenceCensus
from aletheia_lab.evaluation.observed_evidence_receipt import ObservedEvidenceReceipt
from aletheia_lab.evaluation.variant_fairness import load_diagnosis_variant_freeze
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.model_gateway import (
    OpenAIChatCompletionsGatewayAdapter,
    OpenAIGatewayPolicy,
)
from aletheia_lab.project.identity import canonical_project_json


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("preflight", "rehearse", "authorize", "require-live-ready", "execute"),
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--evidence-census",
        type=Path,
        help="local observed-evidence census used only by live preflight",
    )
    parser.add_argument(
        "--evidence-receipt",
        type=Path,
        help="token and cost receipt bound to the observed-evidence census",
    )
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--store", type=Path)
    parser.add_argument("--lease", type=Path)
    parser.add_argument("--confirm-plan-sha256")
    parser.add_argument("--confirm-authorization-sha256")
    return parser


def _outside_repository(root: Path, destination: Path) -> Path:
    resolved = destination.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return resolved
    raise ClaimCorpusExecutionError("execution artifacts must remain outside the repository")


def _publish_result(root: Path, output: Path, result: BaseModel) -> None:
    destination = _outside_repository(root, output)
    payload = result.model_dump(mode="json")
    disposition = publish_immutable_file(
        destination,
        (canonical_project_json(payload) + "\n").encode(),
    )
    payload["output_path"] = str(destination)
    payload["publication_disposition"] = disposition
    print(json.dumps(payload, indent=2, sort_keys=True))


def _authorize(
    args: argparse.Namespace,
    root: Path,
    evidence_census: ObservedEvidenceCensus | None,
    evidence_receipt: ObservedEvidenceReceipt | None,
) -> int:
    if (
        evidence_census is None
        or evidence_receipt is None
        or args.output is None
        or args.confirm_plan_sha256 is None
    ):
        raise ClaimCorpusExecutionError(
            "authorize requires evidence census, receipt, output, and plan confirmation"
        )
    plan = build_execution_plan(root)
    if args.confirm_plan_sha256 != plan.plan_sha256:
        raise ClaimCorpusExecutionError("execution-plan confirmation differs")
    result = build_execution_authorization(
        root,
        repository_state=inspect_repository_state(root),
        evidence_census=evidence_census,
        evidence_receipt=evidence_receipt,
        authorized_at=datetime.now(UTC).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        ),
    )
    _publish_result(root, args.output, result)
    return 0


def _execute(
    args: argparse.Namespace,
    root: Path,
    evidence_census: ObservedEvidenceCensus | None,
    evidence_receipt: ObservedEvidenceReceipt | None,
    authorization: ClaimCorpusExecutionAuthorization | None,
) -> int:
    if (
        evidence_census is None
        or evidence_receipt is None
        or authorization is None
        or args.store is None
        or args.lease is None
        or args.output is None
        or args.confirm_authorization_sha256 is None
    ):
        raise ClaimCorpusExecutionError(
            "execute requires evidence, authorization, store, lease, output, and confirmation"
        )
    if args.confirm_authorization_sha256 != authorization.authorization_sha256:
        raise ClaimCorpusExecutionError("execution-authorization confirmation differs")
    _outside_repository(root, args.authorization)
    repository_state = inspect_repository_state(root)
    preflight = build_execution_preflight(
        root,
        repository_state=repository_state,
        credential_present=bool(os.environ.get("OPENAI_API_KEY")),
        evidence_census=evidence_census,
        evidence_receipt=evidence_receipt,
        authorization=authorization,
    )
    if preflight.live_blockers:
        raise ClaimCorpusExecutionError("live execution preflight remains blocked")
    store_path = _outside_repository(root, args.store)
    lease_path = _outside_repository(root, args.lease)
    prepared = build_live_requests(
        root,
        repository_state=repository_state,
        authorization=authorization,
        evidence_census=evidence_census,
        evidence_receipt=evidence_receipt,
    )
    model_request = next(item.request for item in prepared if item.route == "model_gateway")
    freeze = load_diagnosis_variant_freeze(root / FAIRNESS_PATH)
    provider_policy = OpenAIGatewayPolicy.from_fairness_policy(
        freeze.model_policies["main_llm_v1"]
    )
    adapter = OpenAIChatCompletionsGatewayAdapter.from_environment(
        model_policy=model_request.initial_attempt.model_policy,
        policy=provider_policy,
    )
    lease = build_execution_lease(authorization, store_path)
    publish_immutable_file(
        lease_path,
        (canonical_project_json(lease.model_dump(mode="json")) + "\n").encode(),
    )
    clock = SystemMonotonicClock()
    result = run_live_execution(
        prepared,
        authorization=authorization,
        evidence_census=evidence_census,
        store=ClaimCorpusAttemptStore(store_path, clock=clock),
        model_adapter=adapter,
        clock=clock,
    )
    _publish_result(root, args.output, result)
    return 0


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    result: ClaimCorpusExecutionPreflight | ClaimCorpusExecutionRehearsal
    try:
        if args.command == "rehearse":
            result = rehearse_execution(root)
        else:
            evidence_census = (
                load_execution_evidence_census(root, args.evidence_census)
                if args.evidence_census is not None
                else None
            )
            evidence_receipt = (
                ObservedEvidenceReceipt.model_validate_json(args.evidence_receipt.read_bytes())
                if args.evidence_receipt is not None
                else None
            )
            if args.command == "authorize":
                return _authorize(args, root, evidence_census, evidence_receipt)
            authorization = (
                load_execution_authorization(args.authorization)
                if args.authorization is not None
                else None
            )
            if args.command == "execute":
                return _execute(
                    args, root, evidence_census, evidence_receipt, authorization
                )
            result = build_execution_preflight(
                root,
                repository_state=inspect_repository_state(root),
                credential_present=bool(os.environ.get("OPENAI_API_KEY")),
                evidence_census=evidence_census,
                evidence_receipt=evidence_receipt,
                authorization=authorization,
            )
    except (ClaimCorpusExecutionError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "claim_corpus_execution_preflight_failed", "error": str(exc)},
                indent=2,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    if (
        args.command == "require-live-ready"
        and isinstance(result, ClaimCorpusExecutionPreflight)
        and result.live_blockers
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
