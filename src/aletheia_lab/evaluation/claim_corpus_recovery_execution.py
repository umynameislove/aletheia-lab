"""Recovery request rehearsal with explicit isolation from predecessor identities.

This is deliberately non-authorizing: a transport compatibility check is still
required before an operator can approve another paid execution.
"""

import importlib.metadata
from pathlib import Path

import tiktoken

from aletheia_lab.evaluation.claim_corpus_execution import (
    RepositoryExecutionState,
    build_execution_authorization,
    load_execution_evidence_census,
)
from aletheia_lab.evaluation.claim_corpus_live import (
    PreparedClaimCorpusRequest,
    _build_live_requests,
    build_live_requests,
)
from aletheia_lab.evaluation.claim_corpus_normalization_recovery import load_recovery_protocol
from aletheia_lab.evaluation.claim_corpus_recovery_budget import (
    AMENDED_MAX_OUTPUT_TOKENS,
    RecoveryOutputBudgetAmendment,
    load_recovery_output_budget_amendment,
)
from aletheia_lab.evaluation.execution_contracts import (
    EvaluationManifestReference,
    canonical_execution_sha256,
)
from aletheia_lab.evaluation.observed_evidence_receipt import (
    INPUT_USD_PER_MILLION,
    OUTPUT_USD_PER_MILLION,
    TOKENIZER_VERSION,
    ObservedEvidenceReceipt,
    _chat_input_tokens,
)
from aletheia_lab.project.identity import canonical_project_json, content_sha256


def prepare_recovery_rehearsal(
    root: Path,
) -> tuple[tuple[PreparedClaimCorpusRequest, ...], dict[str, object]]:
    """Build and compare both request matrices without a credential or network."""
    protocol = load_recovery_protocol(root)
    amendment = load_recovery_output_budget_amendment(root)
    if amendment.predecessor_protocol_sha256 != protocol.protocol_sha256:
        raise ValueError("output-budget amendment predecessor differs")
    # A fixed synthetic authority is only for offline identity comparison. It
    # must never be published as an operator authorization or passed to live CLI.
    state = RepositoryExecutionState(
        branch="main",
        head_commit="0" * 40,
        origin_main_commit="0" * 40,
        clean=True,
    )
    evidence = load_execution_evidence_census(
        root,
        root / "configs/evaluation/claim_support_observed_evidence_census.json",
    )
    receipt = ObservedEvidenceReceipt.model_validate_json(
        (root / "configs/evaluation/claim_support_observed_evidence_receipt.json").read_bytes()
    )
    authority = build_execution_authorization(
        root,
        repository_state=state,
        evidence_census=evidence,
        evidence_receipt=receipt,
        authorized_at="2000-01-01T00:00:00Z",
    )
    old = build_live_requests(
        root,
        repository_state=state,
        authorization=authority,
        evidence_census=evidence,
        evidence_receipt=receipt,
    )
    rehearsal_authority = canonical_execution_sha256(
        {"purpose": "offline-recovery-rehearsal", "protocol": protocol.protocol_sha256}
    )
    recovery_manifest = EvaluationManifestReference.build(
        project_id=f"p3-project-{protocol.protocol_sha256}",
        snapshot_id=f"p3-snapshot-{rehearsal_authority}",
        manifest_content_sha256=protocol.protocol_sha256,
        source_commit_ref=state.head_commit,
        authorization_state="authorized",
        authorization_ref=f"ev-{rehearsal_authority}",
        provenance_sha256=evidence.census_sha256,
        created_at="2000-01-01T00:00:00Z",
        frozen_at="2000-01-01T00:00:00Z",
        visibility="diagnosis",
    )
    prior_budget_recovery = _build_live_requests(
        root,
        repository_state=state,
        authorization=None,
        evidence_census=evidence,
        evidence_receipt=receipt,
        recovery=True,
        recovery_manifest=recovery_manifest,
    )
    new = _build_live_requests(
        root,
        repository_state=state,
        authorization=None,
        evidence_census=evidence,
        evidence_receipt=receipt,
        recovery=True,
        recovery_manifest=recovery_manifest,
        recovery_max_output_tokens=AMENDED_MAX_OUTPUT_TOKENS,
    )
    new_ids, model_budget_hashes, model_variants = _validate_amended_requests(
        old, prior_budget_recovery, new, amendment
    )
    if importlib.metadata.version("tiktoken") != TOKENIZER_VERSION:
        raise ValueError("recovery token accounting requires the frozen tokenizer")
    encoding = tiktoken.get_encoding("o200k_base")
    messages = 0
    schemas = 0
    for item in new:
        if item.route != "model_gateway":
            continue
        request = item.request
        messages += _chat_input_tokens(
            encoding,
            request.prompt_text,
            canonical_project_json(request.context.model_dump(mode="json")),
        )
        schemas += len(encoding.encode(request.response_schema_json))
    source_paths = (
        "src/aletheia_lab/evaluation/claim_corpus_recovery_execution.py",
        "src/aletheia_lab/evaluation/claim_corpus_recovery_authorization.py",
        "src/aletheia_lab/evaluation/claim_corpus_recovery_probe.py",
        "src/aletheia_lab/evaluation/claim_corpus_recovery_run.py",
        "src/aletheia_lab/evaluation/claim_corpus_recovery_audit.py",
        "src/aletheia_lab/evaluation/claim_corpus_recovery_budget.py",
        "scripts/claim_support_recovery.py",
        "src/aletheia_lab/evaluation/claim_corpus_live.py",
        "src/aletheia_lab/evaluation/claim_corpus_normalization_recovery.py",
        "src/aletheia_lab/model_gateway/openai.py",
        "src/aletheia_lab/model_gateway/openai_recovery.py",
        "src/aletheia_lab/model_gateway/runtime.py",
        "src/aletheia_lab/model_gateway/contracts.py",
        "src/aletheia_lab/model_gateway/schema.py",
    )
    payload: dict[str, object] = {
        "schema_version": "claim-corpus-recovery-rehearsal/v2",
        "status": "recovery_rehearsed_live_execution_blocked",
        "protocol_sha256": amendment.recovery_protocol_sha256,
        "predecessor_protocol_sha256": protocol.protocol_sha256,
        "output_budget_amendment_sha256": amendment.amendment_sha256,
        "failed_compatibility_receipt_sha256": (
            amendment.failed_compatibility_receipt_sha256
        ),
        "failed_compatibility_store_sha256": amendment.failed_compatibility_store_sha256,
        "maximum_output_tokens_per_model_request": AMENDED_MAX_OUTPUT_TOKENS,
        "output_budget_uniform_across_model_variants": True,
        "model_policy_count": len(model_budget_hashes),
        "scheduled_model_backed_variants": model_variants,
        "request_count": len(new),
        "model_request_count": 315,
        "deterministic_request_count": 45,
        "request_set_sha256": canonical_execution_sha256(sorted(new_ids)),
        "implementation_sha256": canonical_execution_sha256(
            {path: content_sha256((root / path).read_bytes()) for path in source_paths}
        ),
        "local_message_token_count": messages,
        "local_schema_token_count": schemas,
        "provider_billed_input_tokens_known": False,
        "diagnosis_cost_estimate_usd_at_frozen_rates": round(
            (
                (messages + schemas) * INPUT_USD_PER_MILLION
                + 315 * AMENDED_MAX_OUTPUT_TOKENS * OUTPUT_USD_PER_MILLION
            )
            / 1_000_000,
            6,
        ),
        "diagnosis_maximum_attempt_cost_estimate_usd": round(
            2
            * (
                (messages + schemas + 315 * 1024) * INPUT_USD_PER_MILLION
                + 315 * AMENDED_MAX_OUTPUT_TOKENS * OUTPUT_USD_PER_MILLION
            )
            / 1_000_000,
            6,
        ),
        "provider_overhead_allowance_tokens_per_call": 1024,
        "maximum_provider_attempts_per_request": 2,
        "relation_assignment_cost_included": False,
        "estimate_is_authorized_cost_ceiling": False,
        "live_blockers": [
            "amended_provider_compatibility_unverified",
            "recovery_authorization_pending",
        ],
        "provider_calls_executed": False,
        "claims_materialized": False,
        "blind_packets_generated": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return new, {**payload, "receipt_sha256": canonical_execution_sha256(payload)}


def _validate_amended_requests(
    predecessor: tuple[PreparedClaimCorpusRequest, ...],
    prior_budget: tuple[PreparedClaimCorpusRequest, ...],
    amended: tuple[PreparedClaimCorpusRequest, ...],
    amendment: RecoveryOutputBudgetAmendment,
) -> tuple[set[str], set[str], tuple[str, ...]]:
    predecessor_ids = {
        item.request.initial_attempt.request_identity_sha256 for item in predecessor
    }
    prior_model_ids = {
        item.request.initial_attempt.request_identity_sha256
        for item in prior_budget
        if item.route == "model_gateway"
    }
    amended_ids = {
        item.request.initial_attempt.request_identity_sha256 for item in amended
    }
    amended_model_ids = {
        item.request.initial_attempt.request_identity_sha256
        for item in amended
        if item.route == "model_gateway"
    }
    if predecessor_ids & amended_ids or prior_model_ids & amended_model_ids:
        raise ValueError("recovery and predecessor request identities overlap")
    if len(amended_ids) != 360:
        raise ValueError("amended recovery request census differs")
    _validate_predecessor_bindings(predecessor, amended)
    _validate_budget_only_change(prior_budget, amended)
    model_hashes = {
        item.request.initial_attempt.model_policy.policy_content_sha256
        for item in amended
        if item.route == "model_gateway"
    }
    if len(model_hashes) != 1:
        raise ValueError("recovery output budget differs between model-backed variants")
    variants = tuple(
        sorted(
            {
                item.authority.variant_binding.variant_id
                for item in amended
                if item.route == "model_gateway"
            }
        )
    )
    if variants != amendment.scheduled_model_backed_variants:
        raise ValueError("amended model-backed variant census differs")
    return amended_ids, model_hashes, variants


def _validate_predecessor_bindings(
    predecessor: tuple[PreparedClaimCorpusRequest, ...],
    amended: tuple[PreparedClaimCorpusRequest, ...],
) -> None:
    for before, after in zip(predecessor, amended, strict=True):
        if (before.request_sha256, before.route, before.authority, before.request.context) != (
            after.request_sha256,
            after.route,
            after.authority,
            after.request.context,
        ):
            raise ValueError("recovery changes a frozen evidence or variant binding")


def _validate_budget_only_change(
    prior_budget: tuple[PreparedClaimCorpusRequest, ...],
    amended: tuple[PreparedClaimCorpusRequest, ...],
) -> None:
    for before, after in zip(prior_budget, amended, strict=True):
        before_request = before.request
        after_request = after.request
        if (
            before.request_sha256,
            before.route,
            before.authority,
            before_request.context,
            before_request.prompt_text,
            before_request.response_schema_json,
            before_request.runtime_policy.timeout_ns,
            before_request.runtime_policy.max_attempts,
            before_request.runtime_policy.max_response_bytes,
        ) != (
            after.request_sha256,
            after.route,
            after.authority,
            after_request.context,
            after_request.prompt_text,
            after_request.response_schema_json,
            after_request.runtime_policy.timeout_ns,
            after_request.runtime_policy.max_attempts,
            after_request.runtime_policy.max_response_bytes,
        ):
            raise ValueError("output-budget amendment changes another recovery input")
