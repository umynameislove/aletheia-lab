"""Blind, authorized execution of the frozen claim-to-evidence relation census.

The provider receives only claim text, claim type, and the cited visible
evidence.  This boundary seals technical results but deliberately does not
materialize a corpus, assign automatic labels, select a sample, or create
human-rater packets.
"""

from __future__ import annotations

import importlib.metadata
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Final

import tiktoken
from pydantic import BaseModel, ValidationError

from aletheia_lab.evaluation.claim_corpus_construction import build_relation_result_bundle
from aletheia_lab.evaluation.claim_corpus_construction_contracts import (
    ClaimRelationResult,
    ClaimRelationResultBundle,
    RecoveryClaimPoolPreparation,
)
from aletheia_lab.evaluation.claim_corpus_contracts import ClaimCorpusContractError
from aletheia_lab.evaluation.claim_corpus_execution import (
    RepositoryExecutionState,
    inspect_repository_state,
)
from aletheia_lab.evaluation.claim_corpus_live import NeverCancelled, SystemMonotonicClock
from aletheia_lab.evaluation.claim_corpus_live_store import ClaimCorpusAttemptStore
from aletheia_lab.evaluation.claim_corpus_recovery_authorization import checked_private_path
from aletheia_lab.evaluation.claim_corpus_terminal_reader import ClaimCorpusTerminalReader
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ClaimRelationAssignmentResponse,
    load_evidence_semantics_policy,
    parse_relation_assignment,
)
from aletheia_lab.evaluation.claim_relation_execution_contracts import (
    AUTHORIZATION_SCHEMA_VERSION,
    EXPECTED_RELATION_REQUEST_COUNT,
    INPUT_USD_PER_MILLION,
    LEASE_SCHEMA_VERSION,
    MINIMUM_PROVIDER_INTERVAL_MS,
    OUTPUT_USD_PER_MILLION,
    PLAN_SCHEMA_VERSION,
    PREFLIGHT_SCHEMA_VERSION,
    RECEIPT_SCHEMA_VERSION,
    REHEARSAL_SCHEMA_VERSION,
    RESPONSE_FORMAT_TOKEN_ALLOWANCE,
    ClaimRelationExecutionAuthorization,
    ClaimRelationExecutionError,
    ClaimRelationExecutionLease,
    ClaimRelationExecutionPlan,
    ClaimRelationExecutionPreflight,
    ClaimRelationExecutionReceipt,
    ClaimRelationExecutionRehearsal,
)
from aletheia_lab.evaluation.claim_relation_provider import (
    PacedProviderAdapter,
    PreparedRelationRequest,
    build_relation_gateway_requests,
)
from aletheia_lab.evaluation.execution_contracts import (
    canonical_execution_json,
    canonical_execution_sha256,
)
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.model_gateway import (
    Clock,
    ProviderAdapter,
    execute_gateway_request,
)
from aletheia_lab.project.identity import canonical_project_json

TOKENIZER_VERSION: Final = "0.14.0"


def load_recovery_preparation(path: Path) -> RecoveryClaimPoolPreparation:
    try:
        if path.is_symlink() or not path.resolve(strict=True).is_file():
            raise OSError("preparation is not a regular file")
        return RecoveryClaimPoolPreparation.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ClaimRelationExecutionError("recovery preparation is unavailable or invalid") from exc


def _chat_tokens(encoding: tiktoken.Encoding, system_text: str, user_text: str) -> int:
    total = 3
    for role, content in (("system", system_text), ("user", user_text)):
        total += 3 + len(encoding.encode(role)) + len(encoding.encode(content))
    return total


def build_relation_execution_plan(
    root: Path,
    preparation: RecoveryClaimPoolPreparation,
    *,
    source_commit_ref: str,
) -> ClaimRelationExecutionPlan:
    checked = RecoveryClaimPoolPreparation.model_validate(preparation.model_dump(mode="python"))
    policy = load_evidence_semantics_policy(root.resolve())
    identities = tuple(item.assignment_request_sha256 for item in checked.relation_requests)
    if (
        checked.relation_request_count != EXPECTED_RELATION_REQUEST_COUNT
        or len(identities) != EXPECTED_RELATION_REQUEST_COUNT
        or checked.evidence_semantics_policy_sha256 != policy.policy_sha256
    ):
        raise ClaimRelationExecutionError("relation census or frozen policy differs")
    if importlib.metadata.version("tiktoken") != TOKENIZER_VERSION:
        raise ClaimRelationExecutionError("tiktoken version differs from the frozen contract")
    encoding = tiktoken.get_encoding("o200k_base")
    message_tokens = sum(
        _chat_tokens(
            encoding,
            policy.prompt,
            canonical_execution_json(item.provider_payload()),
        )
        for item in checked.relation_requests
    )
    conservative_input = message_tokens * 2 + (
        EXPECTED_RELATION_REQUEST_COUNT * RESPONSE_FORMAT_TOKEN_ALLOWANCE * 2
    )
    output_tokens = EXPECTED_RELATION_REQUEST_COUNT * policy.maximum_output_tokens * 2
    cost = round(
        conservative_input * INPUT_USD_PER_MILLION / 1_000_000
        + output_tokens * OUTPUT_USD_PER_MILLION / 1_000_000,
        6,
    )
    payload: dict[str, object] = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "source_commit_ref": source_commit_ref,
        "preparation_sha256": checked.preparation_sha256,
        "recovery_closeout_sha256": checked.recovery_closeout_sha256,
        "evidence_semantics_policy_sha256": policy.policy_sha256,
        "request_census_sha256": canonical_execution_sha256(identities),
        "request_count": EXPECTED_RELATION_REQUEST_COUNT,
        "model": policy.model,
        "model_snapshot": policy.model_snapshot,
        "maximum_output_tokens_per_request": policy.maximum_output_tokens,
        "maximum_provider_attempts_per_request": policy.maximum_attempts,
        "minimum_provider_interval_ms": MINIMUM_PROVIDER_INTERVAL_MS,
        "tokenizer_name": "tiktoken",
        "tokenizer_version": TOKENIZER_VERSION,
        "tokenizer_encoding": "o200k_base",
        "response_format_token_allowance_per_request": (RESPONSE_FORMAT_TOKEN_ALLOWANCE),
        "input_usd_per_million_tokens": INPUT_USD_PER_MILLION,
        "output_usd_per_million_tokens": OUTPUT_USD_PER_MILLION,
        "exact_message_input_token_count": message_tokens,
        "conservative_input_token_ceiling": conservative_input,
        "output_token_ceiling": output_tokens,
        "estimated_upper_cost_usd": cost,
        "assignment_request_sha256s": identities,
        "provider_calls_executed": False,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ClaimRelationExecutionPlan.model_validate(
        {**payload, "plan_sha256": canonical_execution_sha256(payload)}
    )


def _destination_sha256(run_dir: Path) -> str:
    return canonical_execution_sha256({"private_relation_run_directory": run_dir.as_posix()})


def checked_relation_run_directory(root: Path, run_dir: Path, preparation_path: Path) -> Path:
    run = checked_private_path(run_dir, root)
    preparation_parent = checked_private_path(preparation_path, root).parent
    if (
        run == preparation_parent
        or run.is_relative_to(preparation_parent)
        or preparation_parent.is_relative_to(run)
    ):
        raise ClaimRelationExecutionError("relation run must be isolated from recovery artifacts")
    if run.exists() and not run.is_dir():
        raise ClaimRelationExecutionError("relation run destination is not a directory")
    allowed = {
        "authorization.json",
        "lease.json",
        "attempt-store",
        "receipt.json",
        "relation-results.json",
    }
    if run.exists() and any(
        item.name not in allowed or item.is_symlink() for item in run.iterdir()
    ):
        raise ClaimRelationExecutionError("relation run contains unknown or linked artifacts")
    return run


def build_relation_authorization(
    plan: ClaimRelationExecutionPlan,
    rehearsal: ClaimRelationExecutionRehearsal,
    *,
    repository_state: RepositoryExecutionState,
    run_dir: Path,
    authorized_at: str,
    operator_cost_ceiling_usd: float,
) -> ClaimRelationExecutionAuthorization:
    if (
        not repository_state.synchronized_main
        or repository_state.head_commit != plan.source_commit_ref
        or rehearsal.plan_sha256 != plan.plan_sha256
        or rehearsal.preparation_sha256 != plan.preparation_sha256
    ):
        raise ClaimRelationExecutionError(
            "authorization requires a passed rehearsal on clean synchronized main"
        )
    payload: dict[str, object] = {
        "schema_version": AUTHORIZATION_SCHEMA_VERSION,
        "authorized_at": authorized_at,
        "source_commit_ref": plan.source_commit_ref,
        "plan_sha256": plan.plan_sha256,
        "rehearsal_sha256": rehearsal.rehearsal_sha256,
        "preparation_sha256": plan.preparation_sha256,
        "request_census_sha256": plan.request_census_sha256,
        "request_count": plan.request_count,
        "estimated_upper_cost_usd": plan.estimated_upper_cost_usd,
        "operator_cost_ceiling_usd": operator_cost_ceiling_usd,
        "destination_sha256": _destination_sha256(run_dir),
        "registered_attempts": 1,
        "credential_stored": False,
        "claims_materialized": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    digest = canonical_execution_sha256(payload)
    return ClaimRelationExecutionAuthorization.model_validate(
        {**payload, "authorization_ref": f"ev-{digest}", "authorization_sha256": digest}
    )


def load_relation_authorization(path: Path) -> ClaimRelationExecutionAuthorization:
    try:
        if path.is_symlink() or not path.resolve(strict=True).is_file():
            raise OSError("authorization is not a regular file")
        return ClaimRelationExecutionAuthorization.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ClaimRelationExecutionError(
            "relation authorization is unavailable or invalid"
        ) from exc


def validate_relation_authorization(
    authorization: ClaimRelationExecutionAuthorization,
    plan: ClaimRelationExecutionPlan,
    rehearsal: ClaimRelationExecutionRehearsal,
    *,
    repository_state: RepositoryExecutionState,
    run_dir: Path,
) -> ClaimRelationExecutionAuthorization:
    checked = ClaimRelationExecutionAuthorization.model_validate(
        authorization.model_dump(mode="python")
    )
    if (
        not repository_state.synchronized_main
        or repository_state.head_commit != checked.source_commit_ref
        or checked.plan_sha256 != plan.plan_sha256
        or checked.rehearsal_sha256 != rehearsal.rehearsal_sha256
        or checked.preparation_sha256 != plan.preparation_sha256
        or checked.request_census_sha256 != plan.request_census_sha256
        or checked.request_count != plan.request_count
        or checked.estimated_upper_cost_usd != plan.estimated_upper_cost_usd
        or checked.destination_sha256 != _destination_sha256(run_dir)
    ):
        raise ClaimRelationExecutionError("relation authorization differs from current inputs")
    return checked


def build_relation_preflight(
    plan: ClaimRelationExecutionPlan,
    rehearsal: ClaimRelationExecutionRehearsal,
    *,
    repository_state: RepositoryExecutionState,
    credential_present: bool,
    authorization: ClaimRelationExecutionAuthorization | None,
    run_dir: Path,
) -> ClaimRelationExecutionPreflight:
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
        validate_relation_authorization(
            authorization,
            plan,
            rehearsal,
            repository_state=repository_state,
            run_dir=run_dir,
        )
    payload: dict[str, object] = {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "status": (
            "claim_relation_execution_live_ready"
            if not blockers
            else "claim_relation_execution_live_blocked"
        ),
        "plan_sha256": plan.plan_sha256,
        "preparation_sha256": plan.preparation_sha256,
        "source_commit_ref": plan.source_commit_ref,
        "clean_synchronized_main": repository_state.synchronized_main,
        "credential_present": credential_present,
        "request_count": plan.request_count,
        "exact_message_input_token_count": plan.exact_message_input_token_count,
        "estimated_upper_cost_usd": plan.estimated_upper_cost_usd,
        "operator_cost_ceiling_usd": (
            authorization.operator_cost_ceiling_usd if authorization is not None else None
        ),
        "minimum_provider_interval_ms": plan.minimum_provider_interval_ms,
        "live_blockers": tuple(blockers),
        "provider_calls_executed": False,
        "claims_materialized": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ClaimRelationExecutionPreflight.model_validate(
        {**payload, "preflight_sha256": canonical_execution_sha256(payload)}
    )


def rehearse_relation_execution(
    root: Path,
    preparation: RecoveryClaimPoolPreparation,
    plan: ClaimRelationExecutionPlan,
) -> ClaimRelationExecutionRehearsal:
    """Exercise the frozen provider boundary without calling a provider."""

    policy = load_evidence_semantics_policy(root)
    if (
        preparation.preparation_sha256 != plan.preparation_sha256
        or preparation.evidence_semantics_policy_sha256 != policy.policy_sha256
        or tuple(item.assignment_request_sha256 for item in preparation.relation_requests)
        != plan.assignment_request_sha256s
    ):
        raise ClaimRelationExecutionError("rehearsal inputs differ from the execution plan")
    if any(
        tuple(item.provider_payload()) != ("claim_text", "claim_type", "visible_evidence")
        for item in preparation.relation_requests
    ):
        raise ClaimRelationExecutionError("provider-visible relation fields differ")
    request = max(
        preparation.relation_requests,
        key=lambda item: len(item.visible_evidence),
    )
    decisions = [
        {
            "evidence_id": item.evidence_id,
            "relation_polarity": "neutral",
            "relation_scope": "none",
        }
        for item in request.visible_evidence
    ]
    valid = parse_relation_assignment(request, {"decisions": decisions})
    incoherent_rejected = False
    invalid = [dict(item) for item in decisions]
    invalid[0]["relation_scope"] = "partial"
    try:
        parse_relation_assignment(request, {"decisions": invalid})
    except ClaimCorpusContractError:
        incoherent_rejected = True
    unknown_rejected = False
    unknown = [dict(item) for item in decisions]
    unknown[0]["evidence_id"] = "unknown-evidence"
    try:
        parse_relation_assignment(request, {"decisions": unknown})
    except ClaimCorpusContractError:
        unknown_rejected = True
    if not incoherent_rejected or not unknown_rejected:
        raise ClaimRelationExecutionError("relation semantic rehearsal failed closed")
    payload: dict[str, object] = {
        "schema_version": REHEARSAL_SCHEMA_VERSION,
        "status": "claim_relation_execution_rehearsal_passed",
        "plan_sha256": plan.plan_sha256,
        "preparation_sha256": preparation.preparation_sha256,
        "policy_sha256": policy.policy_sha256,
        "request_count": EXPECTED_RELATION_REQUEST_COUNT,
        "maximum_visible_evidence_items_observed": max(
            len(item.visible_evidence) for item in preparation.relation_requests
        ),
        "provider_input_fields": ("claim_text", "claim_type", "visible_evidence"),
        "valid_response_sha256": valid.response_sha256,
        "incoherent_relation_rejected": True,
        "unknown_evidence_rejected": True,
        "provider_calls_executed": False,
        "claims_materialized": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ClaimRelationExecutionRehearsal.model_validate(
        {**payload, "rehearsal_sha256": canonical_execution_sha256(payload)}
    )


def _reader(store_root: Path, identity: str) -> ClaimCorpusTerminalReader:
    shard = store_root / "requests" / identity
    return ClaimCorpusTerminalReader(
        root=shard,
        object_root=shard / "objects" / "sha256",
        request_root=shard / "requests",
        terminal_root=shard / "terminal",
        failure_root=shard / "failures",
    )


def _relation_result(item: PreparedRelationRequest, store_root: Path) -> ClaimRelationResult:
    identity = item.request.initial_attempt.request_identity_sha256
    reader = _reader(store_root, identity)
    inventory = reader.terminal_inventory(identity)
    response: ClaimRelationAssignmentResponse | None = None
    semantic_issue_sha256: str | None = None
    if inventory.gateway_status == "parsed":
        parsed = reader.terminal_parsed_payload(identity)
        if parsed is None:
            raise ClaimRelationExecutionError("parsed relation terminal has no payload")
        try:
            response = parse_relation_assignment(item.assignment, parsed)
        except ClaimCorpusContractError:
            response = None
            semantic_issue_sha256 = canonical_execution_sha256(
                {
                    "code": "relation_semantic_validation_failed",
                    "assignment_request_sha256": (item.assignment.assignment_request_sha256),
                    "parsed_payload_sha256": canonical_execution_sha256(parsed),
                }
            )
    payload: dict[str, object] = {
        "assignment_request_sha256": item.assignment.assignment_request_sha256,
        "terminal_status": "parsed" if response is not None else "technical_failure",
        "attempt_count": len(inventory.attempt_outcomes),
        "response": response.model_dump(mode="json") if response is not None else None,
        "issue_sha256": (
            None if response is not None else semantic_issue_sha256 or inventory.issue_sha256
        ),
    }
    return ClaimRelationResult.model_validate(
        {**payload, "response": response, "result_sha256": canonical_execution_sha256(payload)}
    )


def _build_receipt(
    preparation: RecoveryClaimPoolPreparation,
    authorization: ClaimRelationExecutionAuthorization,
    bundle: ClaimRelationResultBundle,
    *,
    terminal_store_sha256: str,
    gateway_status_counts: Mapping[str, int],
) -> ClaimRelationExecutionReceipt:
    provider_failures = EXPECTED_RELATION_REQUEST_COUNT - gateway_status_counts.get("parsed", 0)
    semantic_failures = bundle.technical_failure_count - provider_failures
    if semantic_failures < 0:
        raise ClaimRelationExecutionError("relation failure census does not reconcile")
    payload: dict[str, object] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "status": (
            "claim_relation_execution_complete"
            if bundle.technical_failure_count == 0
            else "claim_relation_execution_complete_with_technical_failures"
        ),
        "authorization_sha256": authorization.authorization_sha256,
        "plan_sha256": authorization.plan_sha256,
        "preparation_sha256": preparation.preparation_sha256,
        "policy_sha256": preparation.evidence_semantics_policy_sha256,
        "source_commit_ref": authorization.source_commit_ref,
        "terminal_store_sha256": terminal_store_sha256,
        "relation_result_bundle_sha256": bundle.bundle_sha256,
        "terminal_request_count": EXPECTED_RELATION_REQUEST_COUNT,
        "parsed_count": bundle.parsed_count,
        "technical_failure_count": bundle.technical_failure_count,
        "provider_terminal_failure_count": provider_failures,
        "semantic_validation_failure_count": semantic_failures,
        "provider_attempt_count": bundle.registered_attempt_count,
        "gateway_status_counts": dict(gateway_status_counts),
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ClaimRelationExecutionReceipt.model_validate(
        {**payload, "receipt_sha256": canonical_execution_sha256(payload)}
    )


def execute_relation_census(
    preparation: RecoveryClaimPoolPreparation,
    prepared: tuple[PreparedRelationRequest, ...],
    *,
    authorization: ClaimRelationExecutionAuthorization,
    store_root: Path,
    adapter: ProviderAdapter,
    clock: Clock | None = None,
) -> tuple[ClaimRelationExecutionReceipt, ClaimRelationResultBundle]:
    expected_assignments = tuple(
        item.assignment_request_sha256 for item in preparation.relation_requests
    )
    observed_assignments = tuple(item.assignment.assignment_request_sha256 for item in prepared)
    request_identities = tuple(
        item.request.initial_attempt.request_identity_sha256 for item in prepared
    )
    if (
        authorization.preparation_sha256 != preparation.preparation_sha256
        or len(prepared) != EXPECTED_RELATION_REQUEST_COUNT
        or expected_assignments != observed_assignments
        or len(set(request_identities)) != EXPECTED_RELATION_REQUEST_COUNT
        or any(
            item.authority.assignment_request_sha256 != item.assignment.assignment_request_sha256
            or item.authority.preparation_sha256 != preparation.preparation_sha256
            or item.authority.policy_sha256 != preparation.evidence_semantics_policy_sha256
            for item in prepared
        )
    ):
        raise ClaimRelationExecutionError("relation execution inputs differ from exact census")
    active_clock = clock or SystemMonotonicClock()
    store = ClaimCorpusAttemptStore(store_root, clock=active_clock)
    shards = store.shards(prepared)  # type: ignore[arg-type]
    if any(shards[key].current_state(key) is not None for key in shards):
        raise ClaimRelationExecutionError("existing relation request state forbids replay")
    for item in prepared:
        request = item.request
        identity = request.initial_attempt.request_identity_sha256
        shard = shards[identity]
        shard.prepare(request)
        shard.start(request)
        result = execute_gateway_request(
            request, adapter=adapter, clock=active_clock, cancellation=NeverCancelled()
        )
        for attempt in result.attempts:
            shard.record_attempt(request, attempt)
        if result.raw_response is not None:
            shard.record_response(request, result)
        shard.record_parsed_or_failed(request, result)
        shard.mark_closeout_pending(request, result)
        shard.publish_terminal(request, result)
    results = tuple(_relation_result(item, store_root) for item in prepared)
    bundle = build_relation_result_bundle(preparation, results)
    inventories = store.terminal_inventories(shards)
    counts: dict[str, int] = {
        key: value
        for key, value in sorted(Counter(item.gateway_status for item in inventories).items())
    }
    receipt = _build_receipt(
        preparation,
        authorization,
        bundle,
        terminal_store_sha256=store.store_sha256(shards),
        gateway_status_counts=counts,
    )
    return receipt, bundle


def build_relation_lease(
    authorization: ClaimRelationExecutionAuthorization,
) -> ClaimRelationExecutionLease:
    payload: dict[str, object] = {
        "schema_version": LEASE_SCHEMA_VERSION,
        "authorization_sha256": authorization.authorization_sha256,
        "plan_sha256": authorization.plan_sha256,
        "destination_sha256": authorization.destination_sha256,
        "registered_attempts": 1,
    }
    return ClaimRelationExecutionLease.model_validate(
        {**payload, "lease_sha256": canonical_execution_sha256(payload)}
    )


def acquire_relation_lease(
    run_dir: Path, authorization: ClaimRelationExecutionAuthorization
) -> ClaimRelationExecutionLease:
    if (
        run_dir.is_symlink()
        or not run_dir.is_dir()
        or authorization.destination_sha256 != _destination_sha256(run_dir.resolve())
    ):
        raise ClaimRelationExecutionError("relation lease destination differs or is unsafe")
    if any(
        (run_dir / name).exists()
        for name in ("lease.json", "attempt-store", "receipt.json", "relation-results.json")
    ):
        raise ClaimRelationExecutionError("relation authorization was already consumed")
    lease = build_relation_lease(authorization)
    if (
        publish_immutable_file(
            run_dir / "lease.json",
            (canonical_project_json(lease.model_dump(mode="json")) + "\n").encode(),
        )
        != "created"
    ):
        raise ClaimRelationExecutionError("relation lease was already consumed")
    return lease


def publish_relation_result(path: Path, model: BaseModel) -> str:
    return publish_immutable_file(
        path, (canonical_project_json(model.model_dump(mode="json")) + "\n").encode()
    )


def _require_complete_relation_store(
    store_root: Path,
    prepared: tuple[PreparedRelationRequest, ...],
) -> None:
    identities = {item.request.initial_attempt.request_identity_sha256 for item in prepared}
    request_root = store_root / "requests"
    authority_root = store_root / "authorities"
    try:
        if (
            store_root.is_symlink()
            or not store_root.resolve(strict=True).is_dir()
            or {item.name for item in store_root.iterdir()} != {"requests", "authorities"}
            or any(
                path.is_symlink() or not path.resolve(strict=True).is_dir()
                for path in (request_root, authority_root)
            )
        ):
            raise OSError("relation store root membership differs")
        shards = tuple(request_root.iterdir())
        authorities = tuple(authority_root.iterdir())
        if (
            {item.name for item in shards} != identities
            or {item.name for item in authorities}
            != {f"{identity}.json" for identity in identities}
            or any(item.is_symlink() or not item.is_dir() for item in shards)
            or any(item.is_symlink() or not item.is_file() for item in authorities)
        ):
            raise OSError("relation store census membership differs")
        for shard in shards:
            children = {item.name: item for item in shard.iterdir()}
            if set(children) != {"objects", "requests", "terminal", "failures"}:
                raise OSError("relation request shard membership differs")
            objects = children["objects"]
            if (
                any(item.is_symlink() or not item.is_dir() for item in children.values())
                or {item.name for item in objects.iterdir()} != {"sha256"}
                or (objects / "sha256").is_symlink()
                or not (objects / "sha256").is_dir()
            ):
                raise OSError("relation request shard structure differs")
    except OSError as exc:
        raise ClaimRelationExecutionError("relation attempt store is incomplete or unsafe") from exc


def verify_relation_execution(
    root: Path,
    preparation: RecoveryClaimPoolPreparation,
    *,
    run_dir: Path,
) -> ClaimRelationExecutionReceipt:
    expected_names = {
        "authorization.json",
        "lease.json",
        "attempt-store",
        "receipt.json",
        "relation-results.json",
    }
    try:
        if (
            run_dir.is_symlink()
            or not run_dir.resolve(strict=True).is_dir()
            or {item.name for item in run_dir.iterdir()} != expected_names
            or any(item.is_symlink() for item in run_dir.iterdir())
        ):
            raise OSError("relation run membership differs")
    except OSError as exc:
        raise ClaimRelationExecutionError(
            "relation execution artifacts are incomplete or unsafe"
        ) from exc
    authorization = load_relation_authorization(run_dir / "authorization.json")
    state = RepositoryExecutionState(
        branch="main",
        head_commit=authorization.source_commit_ref,
        origin_main_commit=authorization.source_commit_ref,
        clean=True,
    )
    plan = build_relation_execution_plan(
        root, preparation, source_commit_ref=authorization.source_commit_ref
    )
    rehearsal = rehearse_relation_execution(root, preparation, plan)
    validate_relation_authorization(
        authorization,
        plan,
        rehearsal,
        repository_state=state,
        run_dir=run_dir,
    )
    try:
        tracked_lease = ClaimRelationExecutionLease.model_validate_json(
            (run_dir / "lease.json").read_bytes()
        )
    except (OSError, ValidationError) as exc:
        raise ClaimRelationExecutionError("relation execution lease is invalid") from exc
    if tracked_lease != build_relation_lease(authorization):
        raise ClaimRelationExecutionError("relation execution lease binding differs")
    prepared = build_relation_gateway_requests(root, preparation, plan, authorization)
    store_root = run_dir / "attempt-store"
    _require_complete_relation_store(store_root, prepared)
    store = ClaimCorpusAttemptStore(store_root, clock=SystemMonotonicClock())
    shards = store.shards(prepared)  # type: ignore[arg-type]
    results = tuple(_relation_result(item, run_dir / "attempt-store") for item in prepared)
    bundle = build_relation_result_bundle(preparation, results)
    inventories = store.terminal_inventories(shards)
    counts: dict[str, int] = {
        key: value
        for key, value in sorted(Counter(item.gateway_status for item in inventories).items())
    }
    rebuilt_receipt = _build_receipt(
        preparation,
        authorization,
        bundle,
        terminal_store_sha256=store.store_sha256(shards),
        gateway_status_counts=counts,
    )
    tracked_bundle = ClaimRelationResultBundle.model_validate_json(
        (run_dir / "relation-results.json").read_bytes()
    )
    tracked_receipt = ClaimRelationExecutionReceipt.model_validate_json(
        (run_dir / "receipt.json").read_bytes()
    )
    if bundle != tracked_bundle or tracked_receipt != rebuilt_receipt:
        raise ClaimRelationExecutionError(
            "relation execution artifacts do not independently verify"
        )
    return tracked_receipt


__all__ = [
    "ClaimRelationExecutionAuthorization",
    "ClaimRelationExecutionError",
    "ClaimRelationExecutionPlan",
    "ClaimRelationExecutionPreflight",
    "ClaimRelationExecutionReceipt",
    "PacedProviderAdapter",
    "PreparedRelationRequest",
    "acquire_relation_lease",
    "build_relation_authorization",
    "build_relation_execution_plan",
    "build_relation_gateway_requests",
    "build_relation_lease",
    "build_relation_preflight",
    "checked_relation_run_directory",
    "execute_relation_census",
    "inspect_repository_state",
    "load_recovery_preparation",
    "load_relation_authorization",
    "publish_relation_result",
    "rehearse_relation_execution",
    "verify_relation_execution",
]
