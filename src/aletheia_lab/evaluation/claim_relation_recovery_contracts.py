"""Immutable contracts for one targeted claim-relation terminal recovery."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.evaluation.claim_corpus_construction_contracts import ClaimRelationResult
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.project.identity import SHA256_PATTERN

CLOSEOUT_SCHEMA_VERSION: Final = "claim-relation-predecessor-closeout/v1"
PLAN_SCHEMA_VERSION: Final = "claim-relation-targeted-recovery-plan/v1"
REHEARSAL_SCHEMA_VERSION: Final = "claim-relation-targeted-recovery-rehearsal/v1"
AUTHORIZATION_SCHEMA_VERSION: Final = "claim-relation-targeted-recovery-authorization/v1"
LEASE_SCHEMA_VERSION: Final = "claim-relation-targeted-recovery-lease/v1"
PREFLIGHT_SCHEMA_VERSION: Final = "claim-relation-targeted-recovery-preflight/v1"
RECONCILED_BUNDLE_SCHEMA_VERSION: Final = "claim-relation-reconciled-result-bundle/v1"
RECEIPT_SCHEMA_VERSION: Final = "claim-relation-targeted-recovery-receipt/v1"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
RecoveryBlocker = Literal[
    "authorization_pending",
    "credential_missing",
    "execution_already_started",
    "repository_not_clean_synchronized_main",
]


class ClaimRelationRecoveryError(ValueError):
    """Raised when targeted recovery cannot preserve its frozen boundary."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always", allow_inf_nan=False
    )


class ClaimRelationPredecessorCloseout(_StrictFrozenModel):
    schema_version: Literal["claim-relation-predecessor-closeout/v1"] = CLOSEOUT_SCHEMA_VERSION
    status: Literal["claim_relation_predecessor_closed_one_transient_failure"]
    predecessor_authorization_sha256: Sha256
    predecessor_plan_sha256: Sha256
    predecessor_receipt_sha256: Sha256
    predecessor_terminal_store_sha256: Sha256
    predecessor_result_bundle_sha256: Sha256
    predecessor_source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    preparation_sha256: Sha256
    policy_sha256: Sha256
    terminal_request_count: Literal[962]
    parsed_count: Literal[961]
    technical_failure_count: Literal[1]
    provider_terminal_failure_count: Literal[1]
    semantic_validation_failure_count: Literal[0]
    predecessor_provider_attempt_count: int = Field(ge=962, le=1924)
    failed_assignment_request_sha256: Sha256
    failed_gateway_request_identity_sha256: Sha256
    failed_issue_sha256: Sha256
    failed_gateway_status: Literal["retry_exhausted"]
    failed_attempt_count: Literal[2]
    failed_attempt_outcomes: tuple[Literal["transient_error"], Literal["transient_error"]]
    failures_preserved_in_denominator: Literal[True]
    successful_results_locked_by_hash: Literal[True]
    provider_calls_executed: Literal[True]
    claims_materialized: Literal[False] = False
    automatic_labels_generated: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    closeout_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"closeout_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        if self.closeout_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("relation predecessor closeout identity differs")
        return self


class ClaimRelationRecoveryPlan(_StrictFrozenModel):
    schema_version: Literal["claim-relation-targeted-recovery-plan/v1"] = PLAN_SCHEMA_VERSION
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    predecessor_closeout_sha256: Sha256
    predecessor_plan_sha256: Sha256
    predecessor_result_bundle_sha256: Sha256
    preparation_sha256: Sha256
    policy_sha256: Sha256
    target_assignment_request_sha256: Sha256
    predecessor_gateway_request_identity_sha256: Sha256
    target_provider_payload_sha256: Sha256
    request_count: Literal[1]
    unchanged_predecessor_result_count: Literal[961]
    model: Literal["gpt-4.1"]
    model_snapshot: Literal["gpt-4.1-2025-04-14"]
    maximum_output_tokens_per_request: Literal[600]
    maximum_provider_attempts_per_request: Literal[2]
    minimum_provider_interval_ms: Literal[1000]
    tokenizer_name: Literal["tiktoken"]
    tokenizer_version: Literal["0.14.0"]
    tokenizer_encoding: Literal["o200k_base"]
    response_format_token_allowance_per_request: Literal[1024]
    input_usd_per_million_tokens: float = Field(gt=0)
    output_usd_per_million_tokens: float = Field(gt=0)
    exact_message_input_token_count: int = Field(gt=0)
    conservative_input_token_ceiling: int = Field(gt=0)
    output_token_ceiling: int = Field(gt=0)
    estimated_upper_cost_usd: float = Field(gt=0)
    registered_recovery_attempts: Literal[1]
    predecessor_results_visible_to_provider: Literal[False]
    provider_calls_executed: Literal[False] = False
    claims_materialized: Literal[False] = False
    automatic_labels_generated: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    plan_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"plan_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        expected_input = self.exact_message_input_token_count * 2 + 2048
        expected_output = self.maximum_output_tokens_per_request * 2
        expected_cost = round(
            expected_input * self.input_usd_per_million_tokens / 1_000_000
            + expected_output * self.output_usd_per_million_tokens / 1_000_000,
            6,
        )
        if (
            self.conservative_input_token_ceiling != expected_input
            or self.output_token_ceiling != expected_output
            or self.estimated_upper_cost_usd != expected_cost
            or self.plan_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("targeted relation recovery plan accounting or identity differs")
        return self


class ClaimRelationRecoveryRehearsal(_StrictFrozenModel):
    schema_version: Literal["claim-relation-targeted-recovery-rehearsal/v1"] = (
        REHEARSAL_SCHEMA_VERSION
    )
    status: Literal["claim_relation_targeted_recovery_rehearsal_passed"]
    plan_sha256: Sha256
    predecessor_closeout_sha256: Sha256
    target_assignment_request_sha256: Sha256
    request_count: Literal[1]
    provider_input_fields: tuple[Literal["claim_text", "claim_type", "visible_evidence"], ...]
    target_provider_payload_sha256: Sha256
    valid_response_sha256: Sha256
    incoherent_relation_rejected: Literal[True]
    unknown_evidence_rejected: Literal[True]
    predecessor_successes_unchanged: Literal[True]
    provider_calls_executed: Literal[False] = False
    claims_materialized: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    rehearsal_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"rehearsal_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        if self.provider_input_fields != (
            "claim_text",
            "claim_type",
            "visible_evidence",
        ) or self.rehearsal_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("targeted relation recovery rehearsal identity differs")
        return self


class ClaimRelationRecoveryAuthorization(_StrictFrozenModel):
    schema_version: Literal["claim-relation-targeted-recovery-authorization/v1"] = (
        AUTHORIZATION_SCHEMA_VERSION
    )
    authorization_ref: str = Field(pattern=r"^ev-[0-9a-f]{64}$")
    authorized_at: str
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    plan_sha256: Sha256
    rehearsal_sha256: Sha256
    predecessor_closeout_sha256: Sha256
    preparation_sha256: Sha256
    target_assignment_request_sha256: Sha256
    predecessor_gateway_request_identity_sha256: Sha256
    request_count: Literal[1]
    estimated_upper_cost_usd: float = Field(gt=0)
    operator_cost_ceiling_usd: float = Field(gt=0)
    destination_sha256: Sha256
    registered_attempts: Literal[1] = 1
    maximum_provider_attempts_per_request: Literal[2]
    predecessor_results_visible_to_provider: Literal[False]
    credential_stored: Literal[False] = False
    claims_materialized: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    authorization_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"authorization_ref", "authorization_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        try:
            parsed = datetime.fromisoformat(self.authorized_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("targeted relation recovery authorization time is invalid") from exc
        digest = canonical_execution_sha256(self.identity_payload())
        if (
            not self.authorized_at.endswith("Z")
            or parsed.utcoffset() is None
            or self.operator_cost_ceiling_usd < self.estimated_upper_cost_usd
            or self.authorization_sha256 != digest
            or self.authorization_ref != f"ev-{digest}"
        ):
            raise ValueError("targeted relation recovery authorization differs")
        return self


class ClaimRelationRecoveryLease(_StrictFrozenModel):
    schema_version: Literal["claim-relation-targeted-recovery-lease/v1"] = LEASE_SCHEMA_VERSION
    authorization_sha256: Sha256
    plan_sha256: Sha256
    destination_sha256: Sha256
    target_assignment_request_sha256: Sha256
    registered_attempts: Literal[1] = 1
    lease_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"lease_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        if self.lease_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("targeted relation recovery lease identity differs")
        return self


class ClaimRelationRecoveryPreflight(_StrictFrozenModel):
    schema_version: Literal["claim-relation-targeted-recovery-preflight/v1"] = (
        PREFLIGHT_SCHEMA_VERSION
    )
    status: Literal[
        "claim_relation_targeted_recovery_live_blocked",
        "claim_relation_targeted_recovery_live_ready",
    ]
    plan_sha256: Sha256
    predecessor_closeout_sha256: Sha256
    target_assignment_request_sha256: Sha256
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    clean_synchronized_main: bool
    credential_present: bool
    request_count: Literal[1]
    estimated_upper_cost_usd: float = Field(gt=0)
    operator_cost_ceiling_usd: float | None = Field(default=None, gt=0)
    live_blockers: tuple[RecoveryBlocker, ...]
    provider_calls_executed: Literal[False] = False
    claims_materialized: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    preflight_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"preflight_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        if (not self.live_blockers) != (
            self.status == "claim_relation_targeted_recovery_live_ready"
        ) or self.preflight_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("targeted relation recovery preflight identity differs")
        return self


class ReconciledClaimRelationResultBundle(_StrictFrozenModel):
    schema_version: Literal["claim-relation-reconciled-result-bundle/v1"] = (
        RECONCILED_BUNDLE_SCHEMA_VERSION
    )
    preparation_sha256: Sha256
    policy_sha256: Sha256
    predecessor_result_bundle_sha256: Sha256
    predecessor_closeout_sha256: Sha256
    recovery_result_sha256: Sha256
    recovered_assignment_request_sha256: Sha256
    results: tuple[ClaimRelationResult, ...] = Field(min_length=962, max_length=962)
    parsed_count: Literal[962]
    technical_failure_count: Literal[0]
    unchanged_predecessor_result_count: Literal[961]
    recovered_result_count: Literal[1]
    predecessor_provider_attempt_count: int = Field(ge=962, le=1924)
    recovery_provider_attempt_count: int = Field(ge=1, le=2)
    total_provider_attempt_count: int = Field(ge=963, le=1926)
    failures_preserved_in_history: Literal[True]
    provider_calls_executed: Literal[True]
    labels_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    bundle_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"bundle_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        identities = tuple(item.assignment_request_sha256 for item in self.results)
        recovered = tuple(
            item
            for item in self.results
            if item.assignment_request_sha256 == self.recovered_assignment_request_sha256
        )
        if (
            len(set(identities)) != 962
            or sum(item.terminal_status == "parsed" for item in self.results) != 962
            or len(recovered) != 1
            or recovered[0].result_sha256 != self.recovery_result_sha256
            or recovered[0].attempt_count != self.recovery_provider_attempt_count
            or self.total_provider_attempt_count
            != self.predecessor_provider_attempt_count + self.recovery_provider_attempt_count
            or self.bundle_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("reconciled relation result bundle differs")
        return self


class ClaimRelationRecoveryReceipt(_StrictFrozenModel):
    schema_version: Literal["claim-relation-targeted-recovery-receipt/v1"] = RECEIPT_SCHEMA_VERSION
    status: Literal[
        "claim_relation_targeted_recovery_complete",
        "claim_relation_targeted_recovery_complete_with_failure",
    ]
    authorization_sha256: Sha256
    plan_sha256: Sha256
    predecessor_closeout_sha256: Sha256
    preparation_sha256: Sha256
    policy_sha256: Sha256
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    recovery_terminal_store_sha256: Sha256
    target_assignment_request_sha256: Sha256
    recovery_result_sha256: Sha256
    recovery_terminal_status: Literal["parsed", "technical_failure"]
    recovery_provider_attempt_count: int = Field(ge=1, le=2)
    recovery_gateway_status: str
    predecessor_unresolved_terminal_count: Literal[1]
    unresolved_terminal_count: Literal[0, 1]
    predecessor_provider_attempt_count: int = Field(ge=962, le=1924)
    total_provider_attempt_count: int = Field(ge=963, le=1926)
    reconciled_result_bundle_sha256: Sha256 | None
    unchanged_predecessor_result_count: Literal[961]
    rerun_forbidden: Literal[True]
    failures_preserved_in_history: Literal[True]
    provider_calls_executed: Literal[True]
    claims_materialized: Literal[False] = False
    automatic_labels_generated: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    receipt_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"receipt_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        succeeded = self.recovery_terminal_status == "parsed"
        if (
            succeeded != (self.status == "claim_relation_targeted_recovery_complete")
            or succeeded != (self.unresolved_terminal_count == 0)
            or succeeded != (self.reconciled_result_bundle_sha256 is not None)
            or (succeeded and self.recovery_gateway_status != "parsed")
            or self.total_provider_attempt_count
            != self.predecessor_provider_attempt_count + self.recovery_provider_attempt_count
            or self.receipt_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("targeted relation recovery receipt differs")
        return self


__all__ = [
    "AUTHORIZATION_SCHEMA_VERSION",
    "CLOSEOUT_SCHEMA_VERSION",
    "LEASE_SCHEMA_VERSION",
    "PLAN_SCHEMA_VERSION",
    "PREFLIGHT_SCHEMA_VERSION",
    "RECEIPT_SCHEMA_VERSION",
    "RECONCILED_BUNDLE_SCHEMA_VERSION",
    "REHEARSAL_SCHEMA_VERSION",
    "ClaimRelationPredecessorCloseout",
    "ClaimRelationRecoveryAuthorization",
    "ClaimRelationRecoveryError",
    "ClaimRelationRecoveryLease",
    "ClaimRelationRecoveryPlan",
    "ClaimRelationRecoveryPreflight",
    "ClaimRelationRecoveryReceipt",
    "ClaimRelationRecoveryRehearsal",
    "ReconciledClaimRelationResultBundle",
]
