"""Strict contracts for outcome-blind diagnosis-main materialization."""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.evaluation.claim_evidence_semantics import (
    RELATION_ASSIGNMENT_VERSION,
    ClaimRelationAssignmentRequest,
    ClaimRelationAssignmentResponse,
)
from aletheia_lab.evaluation.claim_support_instrument import INSTRUMENT_VERSION
from aletheia_lab.evaluation.diagnosis_main_analysis import (
    ClaimType,
    OutputStatus,
    TechnicalStatus,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.project.identity import SHA256_PATTERN

SCORING_CONTRACT_SCHEMA_VERSION: Final = "diagnosis-main-scoring-contract/v1"
SCORING_PREPARATION_SCHEMA_VERSION: Final = "diagnosis-main-scoring-preparation/v1"
RELATION_RESULTS_SCHEMA_VERSION: Final = "diagnosis-main-relation-results/v1"
MATERIALIZATION_REHEARSAL_SCHEMA_VERSION: Final = "diagnosis-main-materialization-rehearsal/v1"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
IssueCode = Annotated[str, Field(pattern=r"^[a-z0-9_]{1,64}$")]
ExecutionMode = Literal["offline_rehearsal", "authorized_execution"]
RelationTerminalStatus = Literal["parsed", "technical_failure"]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class DiagnosisMainScoringContract(_StrictFrozenModel):
    """Prospective relation-scoring boundary for emitted main-study claims."""

    schema_version: Literal["diagnosis-main-scoring-contract/v1"] = SCORING_CONTRACT_SCHEMA_VERSION
    status: Literal["outcome_blind_scoring_implementation_contract"]
    protected_main_outcomes_opened: Literal[False]
    execution_authorized: Literal[False]
    analysis_plan_sha256: Sha256
    runtime_contract_sha256: Sha256
    response_contract_sha256: Sha256
    automatic_instrument_manifest_file_sha256: Sha256
    automatic_instrument_manifest_sha256: Sha256
    evidence_semantics_policy_file_sha256: Sha256
    evidence_semantics_policy_sha256: Sha256
    instrument_version: Literal["claim-support-visible-relation/1"]
    relation_assignment_version: Literal["claim-visible-relation-assignment/1"]
    judge_provider: Literal["openai"]
    judge_model: Literal["gpt-4.1"]
    judge_model_snapshot: Literal["gpt-4.1-2025-04-14"]
    judge_temperature: float = Field(ge=0.0, le=0.0)
    judge_seed: Literal[17]
    maximum_output_tokens_per_relation: Literal[600]
    maximum_provider_attempts_per_relation: Literal[2]
    relation_prompt_sha256: Sha256
    relation_response_schema_sha256: Sha256
    permitted_provider_input_fields: tuple[
        Literal["claim_text"], Literal["claim_type"], Literal["visible_evidence"]
    ]
    withheld_provider_input_fields: tuple[
        Literal[
            "mechanism",
            "evidence_condition",
            "variant",
            "hidden_ground_truth",
            "human_judgment",
            "main_outcome",
        ],
        ...,
    ]
    evidence_selection_policy: Literal["entire_final_visible_context_for_every_emitted_claim"]
    citation_scoring_is_separate: Literal[True]
    all_emitted_claims_scored: Literal[True]
    source_output_binding_policy: Literal["logical_request_and_canonical_parsed_output"]
    relation_failure_policy: Literal["entire_output_unresolved_loss_one"]
    deterministic_b0_policy: Literal["successful_completed_zero_claim_output"]
    maximum_atomic_claims_per_provider_output: Literal[5]
    provider_backed_logical_request_count: Literal[896]
    maximum_relation_request_count: Literal[4480]
    exact_relation_count_known_only_after_terminalization: Literal[True]
    automatic_labels_are_measurements_not_ground_truth: Literal[True]
    judge_model_independence_claim_permitted: Literal[False]
    contract_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"contract_sha256"})

    @model_validator(mode="after")
    def _contract_reconciles(self) -> Self:
        expected_fields = ("claim_text", "claim_type", "visible_evidence")
        expected_withheld = (
            "mechanism",
            "evidence_condition",
            "variant",
            "hidden_ground_truth",
            "human_judgment",
            "main_outcome",
        )
        if (
            self.instrument_version != INSTRUMENT_VERSION
            or self.relation_assignment_version != RELATION_ASSIGNMENT_VERSION
            or self.permitted_provider_input_fields != expected_fields
            or self.withheld_provider_input_fields != expected_withheld
            or self.maximum_relation_request_count
            != self.provider_backed_logical_request_count
            * self.maximum_atomic_claims_per_provider_output
            or self.contract_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("main scoring contract identity or boundary changed")
        return self


class DiagnosisMainPreparedClaim(_StrictFrozenModel):
    claim_id: str = Field(pattern=r"^ccrel-[0-9a-f]{64}$")
    claim_type: ClaimType
    citation_required: bool
    citation_present: bool
    citation_ids_valid: bool
    relation_request: ClaimRelationAssignmentRequest

    @model_validator(mode="after")
    def _claim_reconciles(self) -> Self:
        if (
            self.claim_id != self.relation_request.assignment_request_id
            or self.claim_type != self.relation_request.claim_type
        ):
            raise ValueError("prepared claim differs from its blind relation request")
        if not self.citation_present and self.citation_ids_valid:
            raise ValueError("absent citations cannot be declared valid")
        return self


class DiagnosisMainPreparedRecord(_StrictFrozenModel):
    request_id: str
    request_sha256: Sha256
    terminal_sha256: Sha256
    technical_status: TechnicalStatus
    output_status: OutputStatus | None
    claims: tuple[DiagnosisMainPreparedClaim, ...]

    @model_validator(mode="after")
    def _record_reconciles(self) -> Self:
        if not self.request_id or self.request_id != self.request_id.strip():
            raise ValueError("prepared request ID must be non-blank and trimmed")
        if self.technical_status == "success":
            if self.output_status is None:
                raise ValueError("successful preparation record requires output status")
        elif self.output_status is not None or self.claims:
            raise ValueError("failed preparation record cannot retain parsed output")
        claim_ids = tuple(item.claim_id for item in self.claims)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("prepared output contains duplicate claims")
        return self


class DiagnosisMainScoringPreparation(_StrictFrozenModel):
    schema_version: Literal["diagnosis-main-scoring-preparation/v1"] = (
        SCORING_PREPARATION_SCHEMA_VERSION
    )
    execution_mode: ExecutionMode
    analysis_plan_sha256: Sha256
    census_sha256: Sha256
    runtime_contract_sha256: Sha256
    response_contract_sha256: Sha256
    scoring_contract_sha256: Sha256
    batch_binding_sha256: Sha256
    batch_result_sha256: Sha256
    terminal_ledger_sha256: Sha256
    record_count: Literal[1024]
    emitted_claim_count: int = Field(ge=0, le=4480)
    relation_request_count: int = Field(ge=0, le=4480)
    technical_status_counts: dict[str, int]
    relation_provider_calls_executed: Literal[False]
    analysis_executed: Literal[False]
    records: tuple[DiagnosisMainPreparedRecord, ...] = Field(min_length=1024, max_length=1024)
    preparation_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"preparation_sha256"})

    @model_validator(mode="after")
    def _preparation_reconciles(self) -> Self:
        request_ids = tuple(item.request_id for item in self.records)
        relation_ids = tuple(
            claim.relation_request.assignment_request_sha256
            for record in self.records
            for claim in record.claims
        )
        observed_counts = dict(
            sorted(Counter(item.technical_status for item in self.records).items())
        )
        if (
            request_ids != tuple(sorted(request_ids))
            or len(request_ids) != len(set(request_ids))
            or len(relation_ids) != len(set(relation_ids))
            or self.record_count != len(self.records)
            or self.emitted_claim_count != len(relation_ids)
            or self.relation_request_count != len(relation_ids)
            or self.technical_status_counts != observed_counts
            or self.preparation_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("main scoring preparation census or identity changed")
        return self


class DiagnosisMainRelationResult(_StrictFrozenModel):
    assignment_request_sha256: Sha256
    terminal_status: RelationTerminalStatus
    response: ClaimRelationAssignmentResponse | None
    issue_code: IssueCode | None

    @model_validator(mode="after")
    def _result_reconciles(self) -> Self:
        if self.terminal_status == "parsed":
            if (
                self.response is None
                or self.issue_code is not None
                or self.response.assignment_request_sha256 != self.assignment_request_sha256
            ):
                raise ValueError("parsed relation result is incomplete or mismatched")
        elif self.response is not None or not self.issue_code:
            raise ValueError("failed relation result requires only a bounded issue code")
        return self


class DiagnosisMainRelationResults(_StrictFrozenModel):
    schema_version: Literal["diagnosis-main-relation-results/v1"] = RELATION_RESULTS_SCHEMA_VERSION
    execution_mode: ExecutionMode
    preparation_sha256: Sha256
    provider_calls_executed: bool
    registered_relation_attempts_consumed: int = Field(ge=0, le=1)
    results: tuple[DiagnosisMainRelationResult, ...] = Field(max_length=4480)
    results_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"results_sha256"})

    @model_validator(mode="after")
    def _results_reconcile(self) -> Self:
        identities = tuple(item.assignment_request_sha256 for item in self.results)
        offline_state_valid = (
            not self.provider_calls_executed and self.registered_relation_attempts_consumed == 0
        )
        authorized_state_valid = (
            self.provider_calls_executed and self.registered_relation_attempts_consumed == 1
        )
        mode_state_valid = (
            offline_state_valid
            if self.execution_mode == "offline_rehearsal"
            else authorized_state_valid
        )
        if (
            identities != tuple(sorted(identities))
            or len(identities) != len(set(identities))
            or not mode_state_valid
            or self.results_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("main relation result census or identity changed")
        return self


class DiagnosisMainMaterializationRehearsal(_StrictFrozenModel):
    schema_version: Literal["diagnosis-main-materialization-rehearsal/v1"] = (
        MATERIALIZATION_REHEARSAL_SCHEMA_VERSION
    )
    status: Literal["offline_materialization_rehearsal_pass"]
    scientific_result_eligible: Literal[False]
    provider_calls_executed: Literal[False]
    registered_attempts_consumed: Literal[0]
    logical_request_count: Literal[1024]
    emitted_claim_count: int = Field(ge=0, le=4480)
    relation_request_count: int = Field(ge=0, le=4480)
    materialized_claim_count: int = Field(ge=0, le=4480)
    technical_status_counts: dict[str, int]
    support_label_counts: dict[str, int]
    preparation_sha256: Sha256
    relation_results_sha256: Sha256
    analysis_input_sha256: Sha256
    analysis_contract_accepted: Literal[True]
    analysis_report_sha256: Sha256
    receipt_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"receipt_sha256"})

    @model_validator(mode="after")
    def _receipt_reconciles(self) -> Self:
        valid_technical = {"success", "provider_failure", "parse_failure", "unresolved"}
        valid_support = {
            "contradicted",
            "unsupported",
            "partially_supported",
            "fully_supported",
        }
        if (
            self.emitted_claim_count != self.relation_request_count
            or self.materialized_claim_count != self.relation_request_count
            or set(self.technical_status_counts) - valid_technical
            or set(self.support_label_counts) - valid_support
            or any(value < 0 for value in self.technical_status_counts.values())
            or any(value < 0 for value in self.support_label_counts.values())
            or sum(self.technical_status_counts.values()) != self.logical_request_count
            or sum(self.support_label_counts.values()) != self.materialized_claim_count
            or self.receipt_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("materialization rehearsal counts or identity changed")
        return self


__all__ = [
    "MATERIALIZATION_REHEARSAL_SCHEMA_VERSION",
    "RELATION_RESULTS_SCHEMA_VERSION",
    "SCORING_CONTRACT_SCHEMA_VERSION",
    "SCORING_PREPARATION_SCHEMA_VERSION",
    "DiagnosisMainMaterializationRehearsal",
    "DiagnosisMainPreparedClaim",
    "DiagnosisMainPreparedRecord",
    "DiagnosisMainRelationResult",
    "DiagnosisMainRelationResults",
    "DiagnosisMainScoringContract",
    "DiagnosisMainScoringPreparation",
]
