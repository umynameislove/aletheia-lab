"""Operator-authorized recovery execution, isolated from the preserved predecessor."""

import json
from pathlib import Path

import tiktoken

from aletheia_lab.evaluation.claim_corpus_execution import (
    RepositoryExecutionState,
    build_execution_plan,
    load_execution_evidence_census,
)
from aletheia_lab.evaluation.claim_corpus_live import (
    NeverCancelled,
    PreparedClaimCorpusRequest,
    SystemMonotonicClock,
    _build_live_requests,
    run_live_execution,
)
from aletheia_lab.evaluation.claim_corpus_live_store import ClaimCorpusAttemptStore
from aletheia_lab.evaluation.claim_corpus_provider_audit import audit_predecessor_provider_failures
from aletheia_lab.evaluation.claim_corpus_recovery_audit import audit_recovery_store
from aletheia_lab.evaluation.claim_corpus_recovery_authorization import (
    RecoveryAuthorization,
    RecoveryPhase,
    acquire_recovery_lease,
    checked_private_path,
    checked_run_directory,
    destination_sha256,
    publish_recovery_json,
)
from aletheia_lab.evaluation.claim_corpus_recovery_budget import (
    AMENDED_MAX_OUTPUT_TOKENS,
    audit_retired_compatibility_run,
    audit_retired_structured_output_run,
    load_recovery_output_budget_amendment,
)
from aletheia_lab.evaluation.claim_corpus_recovery_execution import prepare_recovery_rehearsal
from aletheia_lab.evaluation.claim_corpus_recovery_probe import build_compatibility_requests
from aletheia_lab.evaluation.execution_contracts import (
    EvaluationManifestReference,
    canonical_execution_sha256,
)
from aletheia_lab.evaluation.observed_evidence_receipt import (
    ObservedEvidenceReceipt,
    _chat_input_tokens,
)
from aletheia_lab.model_gateway import ProviderAdapter, execute_gateway_request
from aletheia_lab.model_gateway.recovery_transport import wire_schema_json
from aletheia_lab.project.identity import canonical_project_json

CENSUS_PATH = "configs/evaluation/claim_support_observed_evidence_census.json"
RECEIPT_PATH = "configs/evaluation/claim_support_observed_evidence_receipt.json"


def estimate_schedule_cost(
    prepared: tuple[PreparedClaimCorpusRequest, ...],
    *,
    maximum_output_tokens: int = AMENDED_MAX_OUTPUT_TOKENS,
) -> float:
    """Frozen rates, all authorized retries, max output, and 1024 overhead tokens/call.

    This is a conservative local estimate, NOT a provider billing guarantee.
    Relation assignment and reserves are outside this authorization.
    """
    encoding = tiktoken.get_encoding("o200k_base")
    total = 0.0
    for item in prepared:
        if item.route != "model_gateway":
            continue
        request = item.request
        tokens = _chat_input_tokens(
            encoding, request.prompt_text,
            canonical_project_json(request.context.model_dump(mode="json")),
        ) + len(encoding.encode(wire_schema_json(request.response_schema_json))) + 1024
        total += request.runtime_policy.max_attempts * (
            tokens * 2 + maximum_output_tokens * 8
        ) / 1_000_000
    return round(total, 6)


def make_recovery_authorization(
    root: Path, *, state: RepositoryExecutionState, run_dir: Path,
    predecessor_store: Path, phase: RecoveryPhase, authorized_at: str,
    operator_cost_ceiling_usd: float,
    retired_compatibility_run: Path,
    retired_structured_output_run: Path,
) -> RecoveryAuthorization:
    run = checked_run_directory(root, run_dir, predecessor_store)
    retired_run = checked_private_path(retired_compatibility_run, root)
    if (
        run == retired_run
        or run.is_relative_to(retired_run)
        or retired_run.is_relative_to(run)
    ):
        raise ValueError("new recovery destination overlaps the retired compatibility run")
    predecessor = audit_predecessor_provider_failures(predecessor_store)
    retired = audit_retired_compatibility_run(root, retired_run)
    structured_run = checked_private_path(retired_structured_output_run, root)
    if run == structured_run or run.is_relative_to(structured_run) or structured_run.is_relative_to(run):
        raise ValueError("new recovery destination overlaps the retired structured-output run")
    structured = audit_retired_structured_output_run(root, structured_run)
    amendment = load_recovery_output_budget_amendment(root)
    templates, rehearsal = prepare_recovery_rehearsal(root)
    evidence = load_execution_evidence_census(root, root / CENSUS_PATH)
    receipt = ObservedEvidenceReceipt.model_validate_json((root / RECEIPT_PATH).read_bytes())
    plan = build_execution_plan(root)
    if not state.synchronized_main:
        raise ValueError("recovery authorization requires clean synchronized main")
    compatibility_hash = None
    if phase == "diagnosis":
        compatibility = verify_completed_recovery(
            root, state=state, run_dir=run, phase="compatibility"
        )
        if compatibility["status"] != "recovery_compatibility_pass":
            raise ValueError("synthetic provider compatibility has not passed")
        compatibility_hash = compatibility["receipt_sha256"]
    schedule = build_compatibility_requests(templates) if phase == "compatibility" else templates
    payload: dict[str, object] = {
        "schema_version": "claim-corpus-recovery-authorization/v3",
        "phase": phase, "authorized_at": authorized_at,
        "source_commit_ref": state.head_commit,
        "execution_plan_sha256": plan.plan_sha256,
        "observed_evidence_census_sha256": evidence.census_sha256,
        "observed_evidence_receipt_sha256": receipt.receipt_sha256,
        "model": plan.model, "model_snapshot": plan.model_snapshot,
        "primary_request_count": plan.primary_request_count,
        "model_request_count": plan.model_request_count,
        "deterministic_request_count": plan.deterministic_request_count,
        "maximum_provider_attempts_per_request": plan.maximum_provider_attempts_per_request,
        "maximum_output_tokens_per_model_request": AMENDED_MAX_OUTPUT_TOKENS,
        "protocol_sha256": rehearsal["protocol_sha256"],
        "output_budget_amendment_sha256": amendment.amendment_sha256,
        "failed_compatibility_receipt_sha256": retired["receipt_sha256"],
        "failed_compatibility_store_sha256": retired["terminal_store_sha256"],
        "transport_sha256": rehearsal["transport_sha256"],
        "retired_structured_output_receipt_sha256": structured["receipt_sha256"],
        "retired_structured_output_store_sha256": structured["terminal_store_sha256"],
        "rehearsal_sha256": rehearsal["receipt_sha256"],
        "destination_sha256": destination_sha256(run),
        "predecessor_store_sha256": predecessor["terminal_store_sha256"],
        "compatibility_receipt_sha256": compatibility_hash,
        "estimated_upper_cost_usd": estimate_schedule_cost(schedule),
        "operator_cost_ceiling_usd": operator_cost_ceiling_usd,
        "registered_attempts": 1,
        "relation_assignment_authorized": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return RecoveryAuthorization.model_validate_json(canonical_project_json({
        **payload, "authorization_sha256": canonical_execution_sha256(payload),
    }))


def load_recovery_authorization(run_dir: Path, phase: RecoveryPhase) -> RecoveryAuthorization:
    return RecoveryAuthorization.model_validate_json(
        (run_dir / f"{phase}-authorization.json").read_bytes()
    )


def authorized_recovery_requests(
    root: Path, *, state: RepositoryExecutionState,
    authorization: RecoveryAuthorization, run_dir: Path,
) -> tuple[PreparedClaimCorpusRequest, ...]:
    """Bind the NEW recovery authority to every request, including synthetic probes."""
    checked = RecoveryAuthorization.model_validate(authorization.model_dump(mode="python"))
    _, rehearsal = prepare_recovery_rehearsal(root)
    if (
        checked.rehearsal_sha256 != rehearsal["receipt_sha256"]
        or checked.protocol_sha256 != rehearsal["protocol_sha256"]
        or checked.transport_sha256 != rehearsal["transport_sha256"]
        or checked.retired_structured_output_receipt_sha256 != rehearsal["retired_structured_output_receipt_sha256"]
        or checked.retired_structured_output_store_sha256 != rehearsal["retired_structured_output_store_sha256"]
        or checked.destination_sha256 != destination_sha256(run_dir)
    ):
        raise ValueError("recovery authority does not match current code, protocol or destination")
    authority_hash = checked.authorization_sha256
    manifest = EvaluationManifestReference.build(
        project_id=f"p3-project-{checked.protocol_sha256}",
        snapshot_id=f"p3-snapshot-{checked.rehearsal_sha256}",
        manifest_content_sha256=checked.rehearsal_sha256,
        source_commit_ref=checked.source_commit_ref,
        authorization_state="authorized", authorization_ref=f"ev-{authority_hash}",
        provenance_sha256=checked.protocol_sha256,
        created_at=checked.authorized_at,
        frozen_at=checked.authorized_at,
        visibility="diagnosis",
    )
    evidence = load_execution_evidence_census(root, root / CENSUS_PATH)
    receipt = ObservedEvidenceReceipt.model_validate_json((root / RECEIPT_PATH).read_bytes())
    prepared = _build_live_requests(
        root, repository_state=state, authorization=None,
        evidence_census=evidence, evidence_receipt=receipt,
        recovery=True, recovery_manifest=manifest,
        recovery_max_output_tokens=checked.maximum_output_tokens_per_model_request,
    )
    if checked.phase == "compatibility":
        prepared = build_compatibility_requests(prepared)
    if estimate_schedule_cost(prepared) != checked.estimated_upper_cost_usd:
        raise ValueError("authorized schedule cost differs")
    return prepared


def validate_recovery_execution(
    root: Path, *, state: RepositoryExecutionState, run_dir: Path,
    predecessor_store: Path, phase: RecoveryPhase, retired_compatibility_run: Path,
    retired_structured_output_run: Path,
) -> tuple[RecoveryAuthorization, tuple[PreparedClaimCorpusRequest, ...]]:
    run = checked_run_directory(root, run_dir, predecessor_store)
    authorization = load_recovery_authorization(run, phase)
    expected = make_recovery_authorization(
        root, state=state, run_dir=run, predecessor_store=predecessor_store,
        phase=phase, authorized_at=authorization.authorized_at,
        operator_cost_ceiling_usd=authorization.operator_cost_ceiling_usd,
        retired_compatibility_run=retired_compatibility_run,
        retired_structured_output_run=retired_structured_output_run,
    )
    if authorization != expected:
        raise ValueError("recovery authorization differs from verified inputs")
    for suffix in ("lease.json", "store", "receipt.json"):
        if (run / f"{phase}-{suffix}").exists():
            raise ValueError("attempt already started; use verify, never restart")
    prepared = authorized_recovery_requests(
        root, state=state, authorization=authorization, run_dir=run
    )
    return authorization, prepared


def execute_recovery(
    root: Path, *, state: RepositoryExecutionState, run_dir: Path,
    predecessor_store: Path, phase: RecoveryPhase, confirm_authorization_sha256: str,
    adapter: ProviderAdapter, retired_compatibility_run: Path,
    retired_structured_output_run: Path,
) -> dict[str, object]:
    authorization, prepared = validate_recovery_execution(
        root, state=state, run_dir=run_dir, predecessor_store=predecessor_store,
        phase=phase, retired_compatibility_run=retired_compatibility_run,
        retired_structured_output_run=retired_structured_output_run,
    )
    if confirm_authorization_sha256 != authorization.authorization_sha256:
        raise ValueError("recovery authorization confirmation differs")
    acquire_recovery_lease(run_dir, authorization)
    clock = SystemMonotonicClock()
    store = ClaimCorpusAttemptStore(run_dir / f"{phase}-store", clock=clock)
    if phase == "diagnosis":
        run_live_execution(
            prepared, authorization=authorization,
            evidence_census=load_execution_evidence_census(root, root / CENSUS_PATH),
            store=store, model_adapter=adapter, clock=clock,
        )
    else:
        _execute_compatibility(prepared, store, adapter, clock)
    receipt = _completed_receipt(root, run_dir, authorization, prepared)
    publish_recovery_json(run_dir / f"{phase}-receipt.json", receipt)
    return receipt


def _execute_compatibility(
    prepared: tuple[PreparedClaimCorpusRequest, ...], store: ClaimCorpusAttemptStore,
    adapter: ProviderAdapter, clock: SystemMonotonicClock,
) -> None:
    shards = store.shards(prepared)
    for item in prepared:
        request = item.request
        shard = shards[request.initial_attempt.request_identity_sha256]
        shard.prepare(request)
        shard.start(request)
        result = execute_gateway_request(
            request, adapter=adapter, clock=clock, cancellation=NeverCancelled()
        )
        for attempt in result.attempts:
            shard.record_attempt(request, attempt)
        if result.raw_response is not None:
            shard.record_response(request, result)
        shard.record_parsed_or_failed(request, result)
        shard.mark_closeout_pending(request, result)
        shard.publish_terminal(request, result)


def _completed_receipt(
    root: Path, run_dir: Path, authorization: RecoveryAuthorization,
    prepared: tuple[PreparedClaimCorpusRequest, ...],
) -> dict[str, object]:
    audit = audit_recovery_store(root, run_dir / f"{authorization.phase}-store", prepared)
    passed = audit["completed_output_count"] == len(prepared)
    status = "recovery_diagnosis_execution_complete"
    if authorization.phase == "compatibility":
        status = "recovery_compatibility_pass" if passed else "recovery_compatibility_failed"
    payload: dict[str, object] = {
        "schema_version": "claim-corpus-recovery-execution-receipt/v1",
        "status": status, "phase": authorization.phase,
        "authorization_sha256": authorization.authorization_sha256,
        "protocol_sha256": authorization.protocol_sha256,
        "transport_sha256": authorization.transport_sha256,
        "rehearsal_sha256": authorization.rehearsal_sha256,
        "source_commit_ref": authorization.source_commit_ref,
        "synthetic_only": authorization.phase == "compatibility",
        "provider_calls_executed": True,
        "claims_materialized": False, "automatic_labels_generated": False,
        "blind_packets_generated": False, "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False, "rerun_forbidden": True,
        **audit,
    }
    return {**payload, "receipt_sha256": canonical_execution_sha256(payload)}


def verify_completed_recovery(
    root: Path, *, state: RepositoryExecutionState, run_dir: Path, phase: RecoveryPhase,
) -> dict[str, object]:
    """Regenerate the receipt from immutable shards; a claimed success flag is insufficient."""
    authorization = load_recovery_authorization(run_dir, phase)
    if authorization.phase != phase:
        raise ValueError("recovery phase differs")
    prepared = authorized_recovery_requests(
        root, state=state, authorization=authorization, run_dir=run_dir
    )
    expected_lease = {
        "schema_version": "claim-corpus-recovery-lease/v1",
        "authorization_sha256": authorization.authorization_sha256,
        "destination_sha256": authorization.destination_sha256,
        "phase": phase, "registered_attempts": 1,
    }
    if json.loads((run_dir / f"{phase}-lease.json").read_bytes()) != expected_lease:
        raise ValueError("recovery lease identity differs")
    expected = _completed_receipt(root, run_dir, authorization, prepared)
    actual = json.loads((run_dir / f"{phase}-receipt.json").read_bytes())
    if actual != expected:
        raise ValueError("recovery receipt differs from independently verified terminal state")
    return expected
