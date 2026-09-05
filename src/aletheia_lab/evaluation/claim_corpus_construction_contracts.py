"""Immutable contracts for fail-closed claim-pool construction."""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.evaluation.claim_corpus_contracts import (
    AtomicClaimV2,
    ClaimCorpusStoreReceipt,
    DiagnosisOutputV2,
    EligibleVariant,
)
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ClaimRelationAssignmentRequest,
    ClaimRelationAssignmentResponse,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.model_gateway.contracts import TerminalStatus
from aletheia_lab.project.identity import SHA256_PATTERN

PREPARATION_SCHEMA_VERSION: Final = "claim-pool-preparation/v1"
RELATION_RESULT_BUNDLE_SCHEMA_VERSION: Final = "claim-relation-result-bundle/v1"
POOL_CLOSEOUT_SCHEMA_VERSION: Final = "claim-pool-publication-closeout/v1"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
NormalizationStatus = Literal[
    "normalized",
    "technical_failure",
    "schema_rejected",
    "claim_binding_rejected",
]
RelationTerminalStatus = Literal["parsed", "technical_failure"]


class ClaimPoolConstructionError(ValueError):
    """Raised when construction inputs are incomplete, unsafe, or inconsistent."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class ProviderDiagnosisOutput(_StrictFrozenModel):
    """Exact structured envelope emitted by the registered diagnosis run."""

    schema_version: Literal["diagnosis-provider-output/1"]
    output_status: Literal["completed", "abstained"]
    atomic_claims: tuple[AtomicClaimV2, ...] = Field(max_length=5)
    abstention_reason: str

    @model_validator(mode="after")
    def _terminal_shape_reconciles(self) -> Self:
        if self.output_status == "completed":
            if not self.atomic_claims or self.abstention_reason:
                raise ValueError("completed provider output has an invalid terminal shape")
        elif self.atomic_claims or not self.abstention_reason.strip():
            raise ValueError("abstained provider output has an invalid terminal shape")
        return self


class ClaimNormalizationRecord(_StrictFrozenModel):
    """Denominator-preserving normalization disposition for one request."""

    request_sha256: Sha256
    request_identity_sha256: Sha256
    variant: EligibleVariant
    gateway_status: TerminalStatus
    normalization_status: NormalizationStatus
    source_record_sha256: Sha256 | None
    issue_sha256: Sha256 | None
    normalized_output: DiagnosisOutputV2 | None
    relation_request_sha256s: tuple[Sha256, ...]
    blocker_code: Literal[
        "technical_terminal",
        "provider_schema_incompatible",
        "claim_evidence_binding_invalid",
    ] | None
    record_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"record_sha256"})

    @model_validator(mode="after")
    def _shape_and_identity_reconcile(self) -> Self:
        if self.normalization_status == "technical_failure":
            valid = (
                self.gateway_status != "parsed"
                and self.source_record_sha256 is None
                and self.issue_sha256 is not None
                and self.normalized_output is None
                and not self.relation_request_sha256s
                and self.blocker_code == "technical_terminal"
            )
        elif self.normalization_status == "schema_rejected":
            valid = (
                self.gateway_status == "parsed"
                and self.source_record_sha256 is not None
                and self.issue_sha256 is None
                and self.normalized_output is None
                and not self.relation_request_sha256s
                and self.blocker_code == "provider_schema_incompatible"
            )
        elif self.normalization_status == "claim_binding_rejected":
            valid = (
                self.gateway_status == "parsed"
                and self.source_record_sha256 is not None
                and self.issue_sha256 is None
                and self.normalized_output is not None
                and not self.relation_request_sha256s
                and self.blocker_code == "claim_evidence_binding_invalid"
            )
        else:
            claim_count = (
                len(self.normalized_output.atomic_claims)
                if self.normalized_output is not None
                else 0
            )
            valid = (
                self.gateway_status == "parsed"
                and self.source_record_sha256 is not None
                and self.issue_sha256 is None
                and self.normalized_output is not None
                and len(self.relation_request_sha256s) == claim_count
                and len(set(self.relation_request_sha256s)) == claim_count
                and self.blocker_code is None
            )
        if not valid:
            raise ValueError("normalization record fields do not match disposition")
        if self.record_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("normalization record identity does not match content")
        return self


class ClaimPoolPreparation(_StrictFrozenModel):
    """Immutable normalization output and exact blind relation-request census."""

    schema_version: Literal["claim-pool-preparation/v1"] = PREPARATION_SCHEMA_VERSION
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    authorization_sha256: Sha256
    execution_plan_sha256: Sha256
    live_receipt_sha256: Sha256
    reconciliation_receipt_sha256: Sha256
    reserve_receipt_sha256: Sha256
    evidence_census_sha256: Sha256
    evidence_semantics_policy_sha256: Sha256
    terminal_request_count: Literal[360]
    parsed_terminal_count: int = Field(ge=0, le=360)
    technical_failure_terminal_count: int = Field(ge=0, le=360)
    normalized_output_count: int = Field(ge=0, le=360)
    normalization_rejection_count: int = Field(ge=0, le=360)
    completed_output_count: int = Field(ge=0, le=360)
    abstained_output_count: int = Field(ge=0, le=360)
    claim_candidate_count: int = Field(ge=0, le=1800)
    relation_request_count: int = Field(ge=0, le=1800)
    records: tuple[ClaimNormalizationRecord, ...] = Field(
        min_length=360, max_length=360
    )
    relation_requests: tuple[ClaimRelationAssignmentRequest, ...]
    failures_preserved_in_denominator: Literal[True] = True
    free_text_recovery_performed: Literal[False] = False
    automatic_labels_generated: Literal[False] = False
    corpus_entries_materialized: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    preparation_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"preparation_sha256"})

    @model_validator(mode="after")
    def _census_and_identity_reconcile(self) -> Self:
        statuses = Counter(item.normalization_status for item in self.records)
        outputs = tuple(
            item.normalized_output
            for item in self.records
            if item.normalized_output is not None
        )
        request_hashes = tuple(item.request_sha256 for item in self.records)
        identities = tuple(item.request_identity_sha256 for item in self.records)
        relation_hashes = tuple(
            item.assignment_request_sha256 for item in self.relation_requests
        )
        row_relation_hashes = tuple(
            digest for item in self.records for digest in item.relation_request_sha256s
        )
        if (
            len(set(request_hashes)) != 360
            or len(set(identities)) != 360
            or self.parsed_terminal_count + self.technical_failure_terminal_count != 360
            or statuses["technical_failure"] != self.technical_failure_terminal_count
            or self.parsed_terminal_count
            != statuses["normalized"]
            + statuses["schema_rejected"]
            + statuses["claim_binding_rejected"]
            or self.normalized_output_count != len(outputs)
            or self.normalization_rejection_count
            != statuses["schema_rejected"] + statuses["claim_binding_rejected"]
            or self.completed_output_count
            != sum(item.output_status == "completed" for item in outputs)
            or self.abstained_output_count
            != sum(item.output_status == "abstained" for item in outputs)
            or self.claim_candidate_count
            != sum(len(item.atomic_claims) for item in outputs)
            or self.relation_request_count != len(self.relation_requests)
            or self.relation_request_count != len(relation_hashes)
            or len(set(relation_hashes)) != len(relation_hashes)
            or relation_hashes != row_relation_hashes
        ):
            raise ValueError("claim-pool preparation census does not reconcile")
        known_outputs = {item.output_sha256 for item in outputs}
        if any(
            item.source_output_sha256 not in known_outputs
            for item in self.relation_requests
        ):
            raise ValueError("relation request references an unknown normalized output")
        if self.preparation_sha256 != canonical_execution_sha256(
            self.identity_payload()
        ):
            raise ValueError("claim-pool preparation identity does not match content")
        return self


class ClaimRelationResult(_StrictFrozenModel):
    assignment_request_sha256: Sha256
    terminal_status: RelationTerminalStatus
    attempt_count: int = Field(ge=1, le=2)
    response: ClaimRelationAssignmentResponse | None
    issue_sha256: Sha256 | None
    result_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"result_sha256"})

    @model_validator(mode="after")
    def _shape_and_identity_reconcile(self) -> Self:
        if self.terminal_status == "parsed":
            valid = (
                self.response is not None
                and self.issue_sha256 is None
                and self.response.assignment_request_sha256
                == self.assignment_request_sha256
            )
        else:
            valid = self.response is None and self.issue_sha256 is not None
        if not valid:
            raise ValueError("relation result fields do not match terminal status")
        if self.result_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("relation result identity does not match content")
        return self


class ClaimRelationResultBundle(_StrictFrozenModel):
    schema_version: Literal["claim-relation-result-bundle/v1"] = (
        RELATION_RESULT_BUNDLE_SCHEMA_VERSION
    )
    preparation_sha256: Sha256
    policy_sha256: Sha256
    results: tuple[ClaimRelationResult, ...]
    parsed_count: int = Field(ge=0, le=1800)
    technical_failure_count: int = Field(ge=0, le=1800)
    registered_attempt_count: int = Field(ge=0, le=3600)
    provider_calls_executed: bool
    labels_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    bundle_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"bundle_sha256"})

    @model_validator(mode="after")
    def _counts_and_identity_reconcile(self) -> Self:
        identities = tuple(item.assignment_request_sha256 for item in self.results)
        if (
            len(identities) != len(set(identities))
            or self.parsed_count
            != sum(item.terminal_status == "parsed" for item in self.results)
            or self.technical_failure_count
            != sum(item.terminal_status == "technical_failure" for item in self.results)
            or self.registered_attempt_count
            != sum(item.attempt_count for item in self.results)
            or self.provider_calls_executed != bool(self.results)
        ):
            raise ValueError("relation-result bundle counts do not reconcile")
        if self.bundle_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("relation-result bundle identity does not match content")
        return self


class ClaimPoolPublicationCloseout(_StrictFrozenModel):
    schema_version: Literal["claim-pool-publication-closeout/v1"] = (
        POOL_CLOSEOUT_SCHEMA_VERSION
    )
    preparation_sha256: Sha256
    relation_result_bundle_sha256: Sha256
    policy_sha256: Sha256
    corpus_store_receipt: ClaimCorpusStoreReceipt
    candidate_claim_count: int = Field(ge=0, le=1800)
    automatically_labeled_claim_count: int = Field(ge=0, le=1800)
    relation_technical_failure_count: Literal[0] = 0
    corpus_entry_count: int = Field(ge=0, le=1800)
    failures_preserved_in_denominator: Literal[True] = True
    labels_immutable_before_human_access: Literal[True] = True
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    closeout_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"closeout_sha256"})

    @model_validator(mode="after")
    def _counts_and_identity_reconcile(self) -> Self:
        if (
            self.candidate_claim_count != self.automatically_labeled_claim_count
            or self.candidate_claim_count != self.corpus_entry_count
            or self.corpus_entry_count != self.corpus_store_receipt.entry_count
        ):
            raise ValueError("claim-pool publication counts do not reconcile")
        if self.closeout_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("claim-pool publication identity does not match content")
        return self


__all__ = [
    "POOL_CLOSEOUT_SCHEMA_VERSION",
    "PREPARATION_SCHEMA_VERSION",
    "RELATION_RESULT_BUNDLE_SCHEMA_VERSION",
    "ClaimNormalizationRecord",
    "ClaimPoolConstructionError",
    "ClaimPoolPreparation",
    "ClaimPoolPublicationCloseout",
    "ClaimRelationResult",
    "ClaimRelationResultBundle",
    "NormalizationStatus",
    "ProviderDiagnosisOutput",
]
