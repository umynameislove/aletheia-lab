"""One-request recovery for a verified transient claim-relation terminal failure.

The predecessor execution remains immutable. Recovery is restricted to its sole
failed assignment and produces a separately attributable, reconciled result
bundle only when that request parses and passes the frozen semantic validator.
"""

from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import tiktoken
from pydantic import BaseModel, ValidationError

from aletheia_lab.evaluation.claim_corpus_construction_contracts import (
    ClaimRelationResult,
    ClaimRelationResultBundle,
    RecoveryClaimPoolPreparation,
)
from aletheia_lab.evaluation.claim_corpus_execution import RepositoryExecutionState
from aletheia_lab.evaluation.claim_corpus_recovery_authorization import checked_private_path
from aletheia_lab.evaluation.claim_corpus_terminal_reader import ClaimCorpusTerminalReader
from aletheia_lab.evaluation.claim_evidence_semantics import parse_relation_assignment
from aletheia_lab.evaluation.claim_relation_execution import (
    build_relation_execution_plan,
    load_recovery_preparation,
    load_relation_authorization,
    publish_relation_result,
    rehearse_relation_execution,
    verify_relation_execution,
)
from aletheia_lab.evaluation.claim_relation_execution_contracts import (
    INPUT_USD_PER_MILLION,
    MINIMUM_PROVIDER_INTERVAL_MS,
    OUTPUT_USD_PER_MILLION,
    RESPONSE_FORMAT_TOKEN_ALLOWANCE,
    ClaimRelationExecutionAuthorization,
    ClaimRelationExecutionPlan,
)
from aletheia_lab.evaluation.claim_relation_provider import (
    PreparedRelationRequest,
    build_relation_gateway_requests,
)
from aletheia_lab.evaluation.claim_relation_recovery_contracts import (
    AUTHORIZATION_SCHEMA_VERSION,
    CLOSEOUT_SCHEMA_VERSION,
    LEASE_SCHEMA_VERSION,
    PLAN_SCHEMA_VERSION,
    PREFLIGHT_SCHEMA_VERSION,
    REHEARSAL_SCHEMA_VERSION,
    ClaimRelationPredecessorCloseout,
    ClaimRelationRecoveryAuthorization,
    ClaimRelationRecoveryError,
    ClaimRelationRecoveryLease,
    ClaimRelationRecoveryPlan,
    ClaimRelationRecoveryPreflight,
    ClaimRelationRecoveryReceipt,
    ClaimRelationRecoveryRehearsal,
    ReconciledClaimRelationResultBundle,
)
from aletheia_lab.evaluation.execution_contracts import (
    canonical_execution_json,
    canonical_execution_sha256,
)

TOKENIZER_VERSION: Final = "0.14.0"
EXPECTED_PREDECESSOR_PARSED: Final = 961
EXPECTED_PREDECESSOR_FAILURES: Final = 1


@dataclass(frozen=True)
class _VerifiedPredecessor:
    authorization: ClaimRelationExecutionAuthorization
    plan: ClaimRelationExecutionPlan
    receipt_sha256: str
    terminal_store_sha256: str
    bundle: ClaimRelationResultBundle
    prepared: tuple[PreparedRelationRequest, ...]
    failed_result: ClaimRelationResult
    failed_prepared: PreparedRelationRequest
    failed_request_identity: str
    failed_issue_sha256: str
    failed_attempt_outcomes: tuple[str, ...]


def _load_model(path: Path, model: type[BaseModel], label: str) -> BaseModel:
    try:
        if path.is_symlink() or not path.resolve(strict=True).is_file():
            raise OSError(f"{label} is not a regular file")
        return model.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ClaimRelationRecoveryError(f"{label} is unavailable or invalid") from exc


def _terminal_reader(store_root: Path, request_identity: str) -> ClaimCorpusTerminalReader:
    shard = store_root / "requests" / request_identity
    return ClaimCorpusTerminalReader(
        root=shard,
        object_root=shard / "objects" / "sha256",
        request_root=shard / "requests",
        terminal_root=shard / "terminal",
        failure_root=shard / "failures",
    )


def _verified_predecessor(
    root: Path,
    preparation: RecoveryClaimPoolPreparation,
    predecessor_run: Path,
) -> _VerifiedPredecessor:
    receipt = verify_relation_execution(root, preparation, run_dir=predecessor_run)
    authorization = load_relation_authorization(predecessor_run / "authorization.json")
    plan = build_relation_execution_plan(
        root, preparation, source_commit_ref=authorization.source_commit_ref
    )
    rehearsal = rehearse_relation_execution(root, preparation, plan)
    if (
        authorization.plan_sha256 != plan.plan_sha256
        or authorization.rehearsal_sha256 != rehearsal.rehearsal_sha256
        or receipt.parsed_count != EXPECTED_PREDECESSOR_PARSED
        or receipt.technical_failure_count != EXPECTED_PREDECESSOR_FAILURES
        or receipt.provider_terminal_failure_count != EXPECTED_PREDECESSOR_FAILURES
        or receipt.semantic_validation_failure_count != 0
    ):
        raise ClaimRelationRecoveryError(
            "predecessor is not the registered one-transient-failure execution"
        )
    bundle = ClaimRelationResultBundle.model_validate_json(
        (predecessor_run / "relation-results.json").read_bytes()
    )
    failures = tuple(item for item in bundle.results if item.terminal_status != "parsed")
    if len(failures) != EXPECTED_PREDECESSOR_FAILURES:
        raise ClaimRelationRecoveryError("predecessor result failure census differs")
    failed = failures[0]
    prepared = build_relation_gateway_requests(root, preparation, plan, authorization)
    failed_prepared = next(
        (
            item
            for item in prepared
            if item.assignment.assignment_request_sha256 == failed.assignment_request_sha256
        ),
        None,
    )
    if failed_prepared is None:
        raise ClaimRelationRecoveryError("failed predecessor assignment is not in the census")
    request_identity = failed_prepared.request.initial_attempt.request_identity_sha256
    reader = _terminal_reader(predecessor_run / "attempt-store", request_identity)
    inventory = reader.terminal_inventory(request_identity)
    issue = reader.terminal_issue(request_identity)
    if (
        inventory.gateway_status != "retry_exhausted"
        or inventory.issue_sha256 != failed.issue_sha256
        or issue is None
        or issue.code != "retry_exhausted"
        or len(inventory.attempt_outcomes) != 2
        or any(outcome != "transient_error" for outcome in inventory.attempt_outcomes)
    ):
        raise ClaimRelationRecoveryError("failed predecessor terminal is not transient-only")
    if failed.issue_sha256 is None:
        raise ClaimRelationRecoveryError("failed predecessor result has no issue identity")
    return _VerifiedPredecessor(
        authorization=authorization,
        plan=plan,
        receipt_sha256=receipt.receipt_sha256,
        terminal_store_sha256=receipt.terminal_store_sha256,
        bundle=bundle,
        prepared=prepared,
        failed_result=failed,
        failed_prepared=failed_prepared,
        failed_request_identity=request_identity,
        failed_issue_sha256=failed.issue_sha256,
        failed_attempt_outcomes=tuple(inventory.attempt_outcomes),
    )


def _build_predecessor_closeout(
    predecessor: _VerifiedPredecessor,
    preparation: RecoveryClaimPoolPreparation,
) -> ClaimRelationPredecessorCloseout:
    payload: dict[str, object] = {
        "schema_version": CLOSEOUT_SCHEMA_VERSION,
        "status": "claim_relation_predecessor_closed_one_transient_failure",
        "predecessor_authorization_sha256": predecessor.authorization.authorization_sha256,
        "predecessor_plan_sha256": predecessor.plan.plan_sha256,
        "predecessor_receipt_sha256": predecessor.receipt_sha256,
        "predecessor_terminal_store_sha256": predecessor.terminal_store_sha256,
        "predecessor_result_bundle_sha256": predecessor.bundle.bundle_sha256,
        "predecessor_source_commit_ref": predecessor.authorization.source_commit_ref,
        "preparation_sha256": preparation.preparation_sha256,
        "policy_sha256": preparation.evidence_semantics_policy_sha256,
        "terminal_request_count": 962,
        "parsed_count": 961,
        "technical_failure_count": 1,
        "provider_terminal_failure_count": 1,
        "semantic_validation_failure_count": 0,
        "predecessor_provider_attempt_count": predecessor.bundle.registered_attempt_count,
        "failed_assignment_request_sha256": (predecessor.failed_result.assignment_request_sha256),
        "failed_gateway_request_identity_sha256": predecessor.failed_request_identity,
        "failed_issue_sha256": predecessor.failed_issue_sha256,
        "failed_gateway_status": "retry_exhausted",
        "failed_attempt_count": 2,
        "failed_attempt_outcomes": predecessor.failed_attempt_outcomes,
        "failures_preserved_in_denominator": True,
        "successful_results_locked_by_hash": True,
        "provider_calls_executed": True,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ClaimRelationPredecessorCloseout.model_validate(
        {**payload, "closeout_sha256": canonical_execution_sha256(payload)}
    )


def build_predecessor_closeout(
    root: Path,
    preparation: RecoveryClaimPoolPreparation,
    *,
    predecessor_run: Path,
) -> ClaimRelationPredecessorCloseout:
    predecessor = _verified_predecessor(root, preparation, predecessor_run)
    return _build_predecessor_closeout(predecessor, preparation)


def load_predecessor_closeout(path: Path) -> ClaimRelationPredecessorCloseout:
    return ClaimRelationPredecessorCloseout.model_validate(
        _load_model(path, ClaimRelationPredecessorCloseout, "predecessor closeout")
    )


def validate_predecessor_closeout(
    root: Path,
    preparation: RecoveryClaimPoolPreparation,
    *,
    predecessor_run: Path,
    closeout: ClaimRelationPredecessorCloseout,
) -> ClaimRelationPredecessorCloseout:
    expected = build_predecessor_closeout(root, preparation, predecessor_run=predecessor_run)
    if closeout != expected:
        raise ClaimRelationRecoveryError("predecessor closeout differs from immutable execution")
    return closeout


def _message_tokens(system_text: str, user_text: str) -> int:
    if importlib.metadata.version("tiktoken") != TOKENIZER_VERSION:
        raise ClaimRelationRecoveryError("tiktoken version differs from the frozen contract")
    encoding = tiktoken.get_encoding("o200k_base")
    total = 3
    for role, content in (("system", system_text), ("user", user_text)):
        total += 3 + len(encoding.encode(role)) + len(encoding.encode(content))
    return total


def build_recovery_plan(
    root: Path,
    preparation: RecoveryClaimPoolPreparation,
    *,
    predecessor_run: Path,
    closeout: ClaimRelationPredecessorCloseout,
    source_commit_ref: str,
) -> ClaimRelationRecoveryPlan:
    predecessor = _verified_predecessor(root, preparation, predecessor_run)
    checked = _build_predecessor_closeout(predecessor, preparation)
    if closeout != checked:
        raise ClaimRelationRecoveryError("predecessor closeout differs from immutable execution")
    assignment = predecessor.failed_prepared.assignment
    tokens = _message_tokens(
        predecessor.failed_prepared.request.prompt_text,
        canonical_execution_json(assignment.provider_payload()),
    )
    conservative_input = tokens * 2 + RESPONSE_FORMAT_TOKEN_ALLOWANCE * 2
    output_tokens = predecessor.plan.maximum_output_tokens_per_request * 2
    cost = round(
        conservative_input * INPUT_USD_PER_MILLION / 1_000_000
        + output_tokens * OUTPUT_USD_PER_MILLION / 1_000_000,
        6,
    )
    payload: dict[str, object] = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "source_commit_ref": source_commit_ref,
        "predecessor_closeout_sha256": checked.closeout_sha256,
        "predecessor_plan_sha256": checked.predecessor_plan_sha256,
        "predecessor_result_bundle_sha256": checked.predecessor_result_bundle_sha256,
        "preparation_sha256": preparation.preparation_sha256,
        "policy_sha256": preparation.evidence_semantics_policy_sha256,
        "target_assignment_request_sha256": checked.failed_assignment_request_sha256,
        "predecessor_gateway_request_identity_sha256": (
            checked.failed_gateway_request_identity_sha256
        ),
        "target_provider_payload_sha256": (
            predecessor.failed_prepared.authority.provider_payload_sha256
        ),
        "request_count": 1,
        "unchanged_predecessor_result_count": 961,
        "model": predecessor.plan.model,
        "model_snapshot": predecessor.plan.model_snapshot,
        "maximum_output_tokens_per_request": (predecessor.plan.maximum_output_tokens_per_request),
        "maximum_provider_attempts_per_request": (
            predecessor.plan.maximum_provider_attempts_per_request
        ),
        "minimum_provider_interval_ms": MINIMUM_PROVIDER_INTERVAL_MS,
        "tokenizer_name": "tiktoken",
        "tokenizer_version": TOKENIZER_VERSION,
        "tokenizer_encoding": "o200k_base",
        "response_format_token_allowance_per_request": RESPONSE_FORMAT_TOKEN_ALLOWANCE,
        "input_usd_per_million_tokens": INPUT_USD_PER_MILLION,
        "output_usd_per_million_tokens": OUTPUT_USD_PER_MILLION,
        "exact_message_input_token_count": tokens,
        "conservative_input_token_ceiling": conservative_input,
        "output_token_ceiling": output_tokens,
        "estimated_upper_cost_usd": cost,
        "registered_recovery_attempts": 1,
        "predecessor_results_visible_to_provider": False,
        "provider_calls_executed": False,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ClaimRelationRecoveryPlan.model_validate(
        {**payload, "plan_sha256": canonical_execution_sha256(payload)}
    )


def rehearse_targeted_recovery(
    root: Path,
    preparation: RecoveryClaimPoolPreparation,
    *,
    predecessor_run: Path,
    closeout: ClaimRelationPredecessorCloseout,
    plan: ClaimRelationRecoveryPlan,
) -> ClaimRelationRecoveryRehearsal:
    predecessor = _verified_predecessor(root, preparation, predecessor_run)
    if (
        plan.predecessor_closeout_sha256 != closeout.closeout_sha256
        or plan.target_assignment_request_sha256
        != predecessor.failed_result.assignment_request_sha256
        or plan.target_provider_payload_sha256
        != predecessor.failed_prepared.authority.provider_payload_sha256
    ):
        raise ClaimRelationRecoveryError("targeted rehearsal inputs differ from the plan")
    assignment = predecessor.failed_prepared.assignment
    if tuple(assignment.provider_payload()) != (
        "claim_text",
        "claim_type",
        "visible_evidence",
    ):
        raise ClaimRelationRecoveryError("targeted recovery provider fields differ")
    decisions = [
        {
            "evidence_id": item.evidence_id,
            "relation_polarity": "neutral",
            "relation_scope": "none",
        }
        for item in assignment.visible_evidence
    ]
    valid = parse_relation_assignment(assignment, {"decisions": decisions})
    invalid = [dict(item) for item in decisions]
    invalid[0]["relation_scope"] = "partial"
    incoherent_rejected = False
    try:
        parse_relation_assignment(assignment, {"decisions": invalid})
    except ValueError:
        incoherent_rejected = True
    unknown = [dict(item) for item in decisions]
    unknown[0]["evidence_id"] = "unknown-evidence"
    unknown_rejected = False
    try:
        parse_relation_assignment(assignment, {"decisions": unknown})
    except ValueError:
        unknown_rejected = True
    if not incoherent_rejected or not unknown_rejected:
        raise ClaimRelationRecoveryError("targeted relation semantic rehearsal failed")
    unchanged = tuple(
        item.result_sha256
        for item in predecessor.bundle.results
        if item.assignment_request_sha256 != plan.target_assignment_request_sha256
    )
    if len(unchanged) != 961 or len(set(unchanged)) != 961:
        raise ClaimRelationRecoveryError("predecessor successful result census differs")
    payload: dict[str, object] = {
        "schema_version": REHEARSAL_SCHEMA_VERSION,
        "status": "claim_relation_targeted_recovery_rehearsal_passed",
        "plan_sha256": plan.plan_sha256,
        "predecessor_closeout_sha256": closeout.closeout_sha256,
        "target_assignment_request_sha256": plan.target_assignment_request_sha256,
        "request_count": 1,
        "provider_input_fields": ("claim_text", "claim_type", "visible_evidence"),
        "target_provider_payload_sha256": plan.target_provider_payload_sha256,
        "valid_response_sha256": valid.response_sha256,
        "incoherent_relation_rejected": True,
        "unknown_evidence_rejected": True,
        "predecessor_successes_unchanged": True,
        "provider_calls_executed": False,
        "claims_materialized": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ClaimRelationRecoveryRehearsal.model_validate(
        {**payload, "rehearsal_sha256": canonical_execution_sha256(payload)}
    )


def checked_recovery_run_directory(
    root: Path,
    run_dir: Path,
    *,
    preparation_path: Path,
    predecessor_run: Path,
) -> Path:
    run = checked_private_path(run_dir, root)
    preparation_parent = checked_private_path(preparation_path, root).parent
    predecessor = checked_private_path(predecessor_run, root)
    if any(
        run == protected or run.is_relative_to(protected) or protected.is_relative_to(run)
        for protected in (preparation_parent, predecessor)
    ):
        raise ClaimRelationRecoveryError("targeted recovery destination overlaps preserved data")
    if run.exists() and not run.is_dir():
        raise ClaimRelationRecoveryError("targeted recovery destination is not a directory")
    allowed = {
        "predecessor-closeout.json",
        "authorization.json",
        "lease.json",
        "attempt-store",
        "recovery-result.json",
        "reconciled-results.json",
        "receipt.json",
    }
    if run.exists() and any(
        item.name not in allowed or item.is_symlink() for item in run.iterdir()
    ):
        raise ClaimRelationRecoveryError("targeted recovery directory has unknown artifacts")
    return run


def _destination_sha256(run_dir: Path) -> str:
    return canonical_execution_sha256(
        {"private_claim_relation_recovery_directory": run_dir.resolve().as_posix()}
    )


def build_recovery_authorization(
    plan: ClaimRelationRecoveryPlan,
    rehearsal: ClaimRelationRecoveryRehearsal,
    *,
    repository_state: RepositoryExecutionState,
    run_dir: Path,
    authorized_at: str,
    operator_cost_ceiling_usd: float,
) -> ClaimRelationRecoveryAuthorization:
    if (
        not repository_state.synchronized_main
        or repository_state.head_commit != plan.source_commit_ref
        or rehearsal.plan_sha256 != plan.plan_sha256
        or rehearsal.predecessor_closeout_sha256 != plan.predecessor_closeout_sha256
    ):
        raise ClaimRelationRecoveryError(
            "targeted recovery authorization requires clean synchronized main"
        )
    payload: dict[str, object] = {
        "schema_version": AUTHORIZATION_SCHEMA_VERSION,
        "authorized_at": authorized_at,
        "source_commit_ref": plan.source_commit_ref,
        "plan_sha256": plan.plan_sha256,
        "rehearsal_sha256": rehearsal.rehearsal_sha256,
        "predecessor_closeout_sha256": plan.predecessor_closeout_sha256,
        "preparation_sha256": plan.preparation_sha256,
        "target_assignment_request_sha256": plan.target_assignment_request_sha256,
        "predecessor_gateway_request_identity_sha256": (
            plan.predecessor_gateway_request_identity_sha256
        ),
        "request_count": 1,
        "estimated_upper_cost_usd": plan.estimated_upper_cost_usd,
        "operator_cost_ceiling_usd": operator_cost_ceiling_usd,
        "destination_sha256": _destination_sha256(run_dir),
        "registered_attempts": 1,
        "maximum_provider_attempts_per_request": 2,
        "predecessor_results_visible_to_provider": False,
        "credential_stored": False,
        "claims_materialized": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    digest = canonical_execution_sha256(payload)
    return ClaimRelationRecoveryAuthorization.model_validate(
        {**payload, "authorization_ref": f"ev-{digest}", "authorization_sha256": digest}
    )


def load_recovery_authorization(path: Path) -> ClaimRelationRecoveryAuthorization:
    return ClaimRelationRecoveryAuthorization.model_validate(
        _load_model(path, ClaimRelationRecoveryAuthorization, "targeted recovery authorization")
    )


def validate_recovery_authorization(
    authorization: ClaimRelationRecoveryAuthorization,
    plan: ClaimRelationRecoveryPlan,
    rehearsal: ClaimRelationRecoveryRehearsal,
    *,
    repository_state: RepositoryExecutionState,
    run_dir: Path,
) -> ClaimRelationRecoveryAuthorization:
    checked = ClaimRelationRecoveryAuthorization.model_validate(
        authorization.model_dump(mode="python")
    )
    if (
        not repository_state.synchronized_main
        or repository_state.head_commit != checked.source_commit_ref
        or checked.plan_sha256 != plan.plan_sha256
        or checked.rehearsal_sha256 != rehearsal.rehearsal_sha256
        or checked.predecessor_closeout_sha256 != plan.predecessor_closeout_sha256
        or checked.preparation_sha256 != plan.preparation_sha256
        or checked.target_assignment_request_sha256 != plan.target_assignment_request_sha256
        or checked.estimated_upper_cost_usd != plan.estimated_upper_cost_usd
        or checked.destination_sha256 != _destination_sha256(run_dir)
    ):
        raise ClaimRelationRecoveryError("targeted recovery authorization differs from inputs")
    return checked


def _execution_started(run_dir: Path) -> bool:
    return any(
        (run_dir / name).exists()
        for name in (
            "lease.json",
            "attempt-store",
            "recovery-result.json",
            "reconciled-results.json",
            "receipt.json",
        )
    )


def build_recovery_preflight(
    plan: ClaimRelationRecoveryPlan,
    rehearsal: ClaimRelationRecoveryRehearsal,
    *,
    repository_state: RepositoryExecutionState,
    credential_present: bool,
    authorization: ClaimRelationRecoveryAuthorization | None,
    run_dir: Path,
) -> ClaimRelationRecoveryPreflight:
    blockers: list[str] = []
    if (
        not repository_state.synchronized_main
        or repository_state.head_commit != plan.source_commit_ref
    ):
        blockers.append("repository_not_clean_synchronized_main")
    if not credential_present:
        blockers.append("credential_missing")
    if authorization is None:
        blockers.append("authorization_pending")
    else:
        validate_recovery_authorization(
            authorization,
            plan,
            rehearsal,
            repository_state=repository_state,
            run_dir=run_dir,
        )
    if _execution_started(run_dir):
        blockers.append("execution_already_started")
    payload: dict[str, object] = {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "status": (
            "claim_relation_targeted_recovery_live_ready"
            if not blockers
            else "claim_relation_targeted_recovery_live_blocked"
        ),
        "plan_sha256": plan.plan_sha256,
        "predecessor_closeout_sha256": plan.predecessor_closeout_sha256,
        "target_assignment_request_sha256": plan.target_assignment_request_sha256,
        "source_commit_ref": plan.source_commit_ref,
        "clean_synchronized_main": repository_state.synchronized_main,
        "credential_present": credential_present,
        "request_count": 1,
        "estimated_upper_cost_usd": plan.estimated_upper_cost_usd,
        "operator_cost_ceiling_usd": (
            authorization.operator_cost_ceiling_usd if authorization is not None else None
        ),
        "live_blockers": tuple(blockers),
        "provider_calls_executed": False,
        "claims_materialized": False,
        "blind_packets_generated": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ClaimRelationRecoveryPreflight.model_validate(
        {**payload, "preflight_sha256": canonical_execution_sha256(payload)}
    )


def _build_targeted_gateway_request(
    root: Path,
    preparation: RecoveryClaimPoolPreparation,
    *,
    predecessor: _VerifiedPredecessor,
    authorization: ClaimRelationRecoveryAuthorization,
) -> PreparedRelationRequest:
    prepared = build_relation_gateway_requests(root, preparation, predecessor.plan, authorization)
    target = next(
        (
            item
            for item in prepared
            if item.assignment.assignment_request_sha256
            == authorization.target_assignment_request_sha256
        ),
        None,
    )
    if target is None:
        raise ClaimRelationRecoveryError("authorized recovery target is absent")
    if (
        target.authority.provider_payload_sha256
        != predecessor.failed_prepared.authority.provider_payload_sha256
        or target.request.context.model_dump(mode="json")
        != predecessor.failed_prepared.request.context.model_dump(mode="json")
        or target.request.prompt_text != predecessor.failed_prepared.request.prompt_text
        or target.request.response_schema_json
        != predecessor.failed_prepared.request.response_schema_json
        or target.request.runtime_policy.max_attempts != 2
    ):
        raise ClaimRelationRecoveryError("targeted request differs from predecessor semantics")
    return target


def build_targeted_gateway_request(
    root: Path,
    preparation: RecoveryClaimPoolPreparation,
    *,
    predecessor_run: Path,
    authorization: ClaimRelationRecoveryAuthorization,
) -> PreparedRelationRequest:
    predecessor = _verified_predecessor(root, preparation, predecessor_run)
    return _build_targeted_gateway_request(
        root,
        preparation,
        predecessor=predecessor,
        authorization=authorization,
    )


def build_recovery_lease(
    authorization: ClaimRelationRecoveryAuthorization,
) -> ClaimRelationRecoveryLease:
    payload: dict[str, object] = {
        "schema_version": LEASE_SCHEMA_VERSION,
        "authorization_sha256": authorization.authorization_sha256,
        "plan_sha256": authorization.plan_sha256,
        "destination_sha256": authorization.destination_sha256,
        "target_assignment_request_sha256": authorization.target_assignment_request_sha256,
        "registered_attempts": 1,
    }
    return ClaimRelationRecoveryLease.model_validate(
        {**payload, "lease_sha256": canonical_execution_sha256(payload)}
    )


def acquire_recovery_lease(
    run_dir: Path,
    authorization: ClaimRelationRecoveryAuthorization,
) -> ClaimRelationRecoveryLease:
    if (
        run_dir.is_symlink()
        or not run_dir.is_dir()
        or authorization.destination_sha256 != _destination_sha256(run_dir)
        or _execution_started(run_dir)
    ):
        raise ClaimRelationRecoveryError("targeted recovery lease is unsafe or consumed")
    lease = build_recovery_lease(authorization)
    if publish_relation_result(run_dir / "lease.json", lease) != "created":
        raise ClaimRelationRecoveryError("targeted recovery lease was already consumed")
    return lease


__all__ = [
    "ClaimRelationRecoveryAuthorization",
    "ClaimRelationRecoveryError",
    "ClaimRelationRecoveryPlan",
    "ClaimRelationRecoveryPreflight",
    "ClaimRelationRecoveryReceipt",
    "ClaimRelationRecoveryRehearsal",
    "ReconciledClaimRelationResultBundle",
    "acquire_recovery_lease",
    "build_predecessor_closeout",
    "build_recovery_authorization",
    "build_recovery_lease",
    "build_recovery_plan",
    "build_recovery_preflight",
    "build_targeted_gateway_request",
    "checked_recovery_run_directory",
    "load_predecessor_closeout",
    "load_recovery_authorization",
    "load_recovery_preparation",
    "publish_relation_result",
    "rehearse_targeted_recovery",
    "validate_predecessor_closeout",
    "validate_recovery_authorization",
]
