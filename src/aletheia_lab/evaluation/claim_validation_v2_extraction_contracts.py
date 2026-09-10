"""Immutable contracts for provider-free V2 claim extraction closeout."""

from __future__ import annotations

from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.evaluation.claim_corpus_contracts import (
    EligibleVariant,
    EvidenceCondition,
    Mechanism,
    OutputStatus,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.model_gateway.contracts import ProviderFailureCategory
from aletheia_lab.project.identity import SHA256_PATTERN

EXTRACTION_SCHEMA_VERSION: Final = "claim-support-validation-v2-extraction-closeout/v1"
SAMPLE_TARGET: Final = 200
RELATION_REQUEST_CEILING: Final = 1440
MAXIMUM_SOURCE_CLAIMS_PER_OUTPUT: Final = 2

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
ExtractionBlocker = Literal[
    "insufficient_distinct_canonical_claim_texts",
    "non_unique_source_output_identity_for_frozen_relation_batch",
]


class ClaimValidationV2ExtractionError(ValueError):
    """Raised when V2 extraction differs from its frozen source cohort."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class V2DashboardInputUsageObservation(_StrictFrozenModel):
    """Supplemental account/day observation, not cohort billing evidence."""

    scope: Literal["openai_dashboard_utc_day_all_input_tokens"]
    observed_utc_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    input_token_count: int = Field(gt=0)
    evidence_content_sha256: Sha256
    cohort_exclusive_attribution_established: Literal[False] = False
    provider_output_token_count_available: Literal[False] = False
    exact_realized_cost_available: Literal[False] = False
    observation_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"observation_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        if self.observation_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("dashboard usage observation identity differs")
        return self


class V2ExtractionRequestRecord(_StrictFrozenModel):
    """One normalized terminal and its prospectively selected source claims."""

    sequence: int = Field(ge=1, le=360)
    schedule_round: int = Field(ge=1, le=15)
    v2_request_sha256: Sha256
    gateway_request_identity_sha256: Sha256
    family_id: str
    mechanism: Mechanism
    evidence_condition: EvidenceCondition
    variant: EligibleVariant
    attempt_count: int = Field(ge=1, le=2)
    provider_failure_categories: tuple[ProviderFailureCategory, ...] = Field(max_length=2)
    source_record_sha256: Sha256
    normalized_output_sha256: Sha256
    output_status: OutputStatus
    atomic_claim_count: int = Field(ge=0, le=5)
    selected_claim_local_ids: tuple[str, ...] = Field(max_length=MAXIMUM_SOURCE_CLAIMS_PER_OUTPUT)
    selected_claim_text_sha256s: tuple[Sha256, ...] = Field(
        max_length=MAXIMUM_SOURCE_CLAIMS_PER_OUTPUT
    )
    record_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"record_sha256"})

    @model_validator(mode="after")
    def _selection_and_identity_reconcile(self) -> Self:
        expected_selected = min(self.atomic_claim_count, MAXIMUM_SOURCE_CLAIMS_PER_OUTPUT)
        local = self.variant == "B0"
        if (
            len(self.selected_claim_local_ids) != expected_selected
            or len(self.selected_claim_text_sha256s) != expected_selected
            or len(set(self.selected_claim_local_ids)) != expected_selected
            or (self.output_status == "completed") != (self.atomic_claim_count > 0)
            or len(self.provider_failure_categories) > self.attempt_count
            or (local and self.attempt_count != 1)
            or (local and bool(self.provider_failure_categories))
            or self.record_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("V2 extraction request record does not reconcile")
        return self


class V2ClaimExtractionCloseout(_StrictFrozenModel):
    """Outcome-blind closeout before any relation assignment is authorized."""

    schema_version: Literal["claim-support-validation-v2-extraction-closeout/v1"] = (
        EXTRACTION_SCHEMA_VERSION
    )
    status: Literal[
        "claim_support_validation_v2_extraction_relation_prerequisite_passed",
        "claim_support_validation_v2_extraction_relation_blocked",
    ]
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    protocol_sha256: Sha256
    runtime_manifest_sha256: Sha256
    extraction_implementation_sha256: Sha256
    cohort_authorization_sha256: Sha256
    cohort_receipt_sha256: Sha256
    cohort_terminal_store_sha256: Sha256
    terminal_request_count: Literal[360]
    parsed_terminal_count: Literal[360]
    normalized_output_count: Literal[360]
    completed_output_count: int = Field(ge=0, le=360)
    source_provider_attempt_count: int = Field(ge=315, le=630)
    provider_failure_attempt_count: int = Field(ge=0, le=315)
    raw_atomic_claim_count: int = Field(ge=0, le=1800)
    selected_source_claim_count: int = Field(ge=0, le=720)
    distinct_canonical_claim_text_count: int = Field(ge=0, le=720)
    repeated_claim_instance_count: int = Field(ge=0, le=720)
    distinct_normalized_output_count: int = Field(ge=0, le=360)
    repeated_normalized_output_instance_count: int = Field(ge=0, le=360)
    maximum_source_claims_per_output: Literal[2]
    global_deduplication_before_relation_forbidden: Literal[True] = True
    request_records: tuple[V2ExtractionRequestRecord, ...] = Field(min_length=360, max_length=360)
    relation_request_ceiling: Literal[1440]
    sample_target: Literal[200]
    repeated_canonical_claim_text_forbidden_in_sample: Literal[True]
    distinct_text_prerequisite_satisfied: bool
    source_output_instance_identity_unique: bool
    exact_frozen_selection_feasibility: Literal[
        "impossible_distinct_text_shortfall",
        "not_yet_assessed",
    ]
    relation_frame_batch_built: Literal[False] = False
    relation_request_count_known: Literal[False] = False
    relation_outcomes_observed: Literal[False] = False
    relation_execution_technically_unlocked: Literal[True]
    relation_execution_ready: bool
    relation_execution_authorized: Literal[False] = False
    extraction_blockers: tuple[ExtractionBlocker, ...]
    next_authorized_action: Literal["review_separately_versioned_prospective_design"]
    dashboard_input_usage_observation: V2DashboardInputUsageObservation | None
    additional_provider_calls_executed: Literal[False] = False
    claims_materialized: Literal[False] = False
    automatic_labels_generated: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    closeout_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"closeout_sha256"})

    @model_validator(mode="after")
    def _census_gate_and_identity_reconcile(self) -> Self:
        selected_text_hashes = tuple(
            digest
            for record in self.request_records
            for digest in record.selected_claim_text_sha256s
        )
        distinct_outputs = len({record.normalized_output_sha256 for record in self.request_records})
        unique_texts = len(set(selected_text_hashes))
        blockers: list[ExtractionBlocker] = []
        if unique_texts < self.sample_target:
            blockers.append("insufficient_distinct_canonical_claim_texts")
        if distinct_outputs < self.normalized_output_count:
            blockers.append("non_unique_source_output_identity_for_frozen_relation_batch")
        expected_blockers = tuple(blockers)
        ready = not expected_blockers
        expected_status = (
            "claim_support_validation_v2_extraction_relation_prerequisite_passed"
            if ready
            else "claim_support_validation_v2_extraction_relation_blocked"
        )
        expected_feasibility = (
            "impossible_distinct_text_shortfall"
            if unique_texts < self.sample_target
            else "not_yet_assessed"
        )
        if (
            tuple(item.sequence for item in self.request_records) != tuple(range(1, 361))
            or len({item.v2_request_sha256 for item in self.request_records}) != 360
            or len({item.gateway_request_identity_sha256 for item in self.request_records}) != 360
            or self.completed_output_count
            != sum(item.output_status == "completed" for item in self.request_records)
            or self.raw_atomic_claim_count
            != sum(item.atomic_claim_count for item in self.request_records)
            or self.source_provider_attempt_count
            != sum(item.attempt_count for item in self.request_records if item.variant != "B0")
            or self.provider_failure_attempt_count
            != sum(len(item.provider_failure_categories) for item in self.request_records)
            or self.selected_source_claim_count != len(selected_text_hashes)
            or self.distinct_canonical_claim_text_count != unique_texts
            or self.repeated_claim_instance_count != self.selected_source_claim_count - unique_texts
            or self.distinct_normalized_output_count != distinct_outputs
            or self.repeated_normalized_output_instance_count
            != self.normalized_output_count - distinct_outputs
            or self.distinct_text_prerequisite_satisfied != (unique_texts >= self.sample_target)
            or self.source_output_instance_identity_unique
            != (distinct_outputs == self.normalized_output_count)
            or self.exact_frozen_selection_feasibility != expected_feasibility
            or self.extraction_blockers != expected_blockers
            or self.relation_execution_ready != ready
            or self.status != expected_status
            or self.closeout_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("V2 claim extraction closeout does not reconcile")
        return self


__all__ = [
    "ClaimValidationV2ExtractionError",
    "EXTRACTION_SCHEMA_VERSION",
    "MAXIMUM_SOURCE_CLAIMS_PER_OUTPUT",
    "RELATION_REQUEST_CEILING",
    "SAMPLE_TARGET",
    "V2ClaimExtractionCloseout",
    "V2DashboardInputUsageObservation",
    "V2ExtractionRequestRecord",
]
