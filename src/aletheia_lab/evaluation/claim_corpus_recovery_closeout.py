"""Read-only closeout and pool preparation for the completed recovery run.

This module never invokes a provider, retries a diagnosis request, assigns an
automatic support label, materializes the 200-claim sample, or creates human
packets.  It preserves every terminal failure in the registered denominator.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aletheia_lab.evaluation.claim_corpus_construction_contracts import (
    RECOVERY_PREPARATION_SCHEMA_VERSION,
    ClaimNormalizationRecord,
    ClaimPoolConstructionError,
    RecoveryClaimPoolPreparation,
)
from aletheia_lab.evaluation.claim_corpus_contracts import (
    ClaimCorpusContractError,
    ClaimCorpusRequest,
    ClaimCorpusRequestCensus,
    DiagnosisOutputV2,
    EligibleVariant,
    EvidenceCondition,
    Mechanism,
)
from aletheia_lab.evaluation.claim_corpus_execution import (
    RepositoryExecutionState,
    build_execution_plan,
)
from aletheia_lab.evaluation.claim_corpus_live import PreparedClaimCorpusRequest
from aletheia_lab.evaluation.claim_corpus_normalization_recovery import (
    normalize_provider_output_v2,
)
from aletheia_lab.evaluation.claim_corpus_readiness import REQUEST_CENSUS_PATH
from aletheia_lab.evaluation.claim_corpus_recovery_authorization import (
    RecoveryAuthorization,
)
from aletheia_lab.evaluation.claim_corpus_recovery_run import (
    authorized_recovery_requests,
    load_recovery_authorization,
    verify_completed_recovery,
)
from aletheia_lab.evaluation.claim_corpus_terminal_reader import (
    ClaimCorpusTerminalReader,
)
from aletheia_lab.evaluation.claim_evidence_census import ObservedEvidenceCensus
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ClaimRelationAssignmentRequest,
    ModelVisibleEvidenceContext,
    build_relation_assignment_request,
    load_evidence_semantics_policy,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.model_gateway.contracts import TerminalStatus
from aletheia_lab.project.identity import SHA256_PATTERN

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class RecoveryRequestDisposition(_StrictFrozenModel):
    """One registered request and its immutable technical disposition."""

    request_ordinal: int = Field(ge=1, le=360)
    request_sha256: Sha256
    request_identity_sha256: Sha256
    mechanism: Mechanism
    family_id: str
    evidence_condition: EvidenceCondition
    variant: EligibleVariant
    gateway_status: TerminalStatus
    attempt_count: int = Field(ge=1, le=2)
    issue_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,95}$")
    normalized_output_count: int = Field(ge=0, le=1)
    claim_candidate_count: int = Field(ge=0, le=5)
    disposition_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"disposition_sha256"})

    @model_validator(mode="after")
    def _terminal_shape_and_identity_reconcile(self) -> Self:
        parsed = self.gateway_status == "parsed"
        if parsed != (self.issue_code is None):
            raise ValueError("recovery issue presence differs from terminal status")
        if parsed != (self.normalized_output_count == 1):
            raise ValueError("recovery normalization count differs from terminal status")
        if not parsed and self.claim_candidate_count:
            raise ValueError("technical failure cannot contribute claim candidates")
        if self.disposition_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("recovery disposition identity does not match content")
        return self


class RecoveryMissingnessSummary(_StrictFrozenModel):
    """Observed availability for one prospectively declared stratum."""

    stratum_id: str
    scheduled_request_count: int = Field(ge=1, le=360)
    parsed_request_count: int = Field(ge=0, le=360)
    technical_failure_count: int = Field(ge=0, le=360)
    retried_request_count: int = Field(ge=0, le=360)
    claim_candidate_count: int = Field(ge=0, le=1800)

    @model_validator(mode="after")
    def _counts_reconcile(self) -> Self:
        if (
            self.parsed_request_count + self.technical_failure_count != self.scheduled_request_count
            or self.retried_request_count > self.scheduled_request_count
        ):
            raise ValueError("recovery missingness summary does not reconcile")
        return self


class RecoveryExecutionCloseout(_StrictFrozenModel):
    """Immutable closeout for the 360-request diagnosis recovery."""

    schema_version: Literal["claim-corpus-recovery-closeout/v1"]
    status: Literal["recovery_execution_closed_missingness_preserved"]
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    authorization_sha256: Sha256
    protocol_sha256: Sha256
    transport_sha256: Sha256
    execution_plan_sha256: Sha256
    recovery_receipt_sha256: Sha256
    recovery_terminal_store_sha256: Sha256
    evidence_census_sha256: Sha256
    terminal_request_count: Literal[360]
    parsed_terminal_count: int = Field(ge=0, le=360)
    technical_failure_terminal_count: int = Field(ge=0, le=360)
    technical_attempt_count: int = Field(ge=360, le=720)
    normalized_output_count: int = Field(ge=0, le=360)
    claim_candidate_count: int = Field(ge=0, le=1800)
    first_technical_failure_ordinal: int = Field(ge=1, le=360)
    consecutive_parsed_prefix_count: int = Field(ge=0, le=359)
    execution_order_dimensions: tuple[
        Literal["mechanism"],
        Literal["family"],
        Literal["evidence_condition"],
        Literal["variant"],
    ]
    missingness_exchangeability_status: Literal["not_established_execution_order_confounded"]
    mechanism_summaries: tuple[RecoveryMissingnessSummary, ...] = Field(min_length=3, max_length=3)
    condition_summaries: tuple[RecoveryMissingnessSummary, ...] = Field(min_length=3, max_length=3)
    variant_summaries: tuple[RecoveryMissingnessSummary, ...] = Field(min_length=8, max_length=8)
    family_summaries: tuple[RecoveryMissingnessSummary, ...] = Field(min_length=15, max_length=15)
    request_dispositions: tuple[RecoveryRequestDisposition, ...] = Field(
        min_length=360, max_length=360
    )
    failures_preserved_in_denominator: Literal[True]
    recovery_rerun_forbidden: Literal[True]
    reserve_activation_forbidden_after_execution: Literal[True]
    availability_may_not_be_interpreted_as_mechanism_performance: Literal[True]
    ready_for_recovery_pool_preparation: Literal[True]
    relation_assignments_generated: Literal[False]
    automatic_labels_generated: Literal[False]
    claims_materialized: Literal[False]
    blind_packets_generated: Literal[False]
    human_annotations_collected: Literal[False]
    main_or_sealed_outcomes_opened: Literal[False]
    closeout_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"closeout_sha256"})

    @model_validator(mode="after")
    def _census_and_identity_reconcile(self) -> Self:
        records = self.request_dispositions
        statuses = Counter(item.gateway_status for item in records)
        failed_ordinals = tuple(
            item.request_ordinal for item in records if item.gateway_status != "parsed"
        )
        if not failed_ordinals:
            raise ValueError("this closeout requires the observed technical failures")
        if (
            tuple(item.request_ordinal for item in records) != tuple(range(1, 361))
            or len({item.request_sha256 for item in records}) != 360
            or len({item.request_identity_sha256 for item in records}) != 360
            or statuses["parsed"] != self.parsed_terminal_count
            or self.technical_failure_terminal_count
            != self.terminal_request_count - self.parsed_terminal_count
            or self.normalized_output_count != sum(item.normalized_output_count for item in records)
            or self.claim_candidate_count != sum(item.claim_candidate_count for item in records)
            or self.technical_attempt_count != sum(item.attempt_count for item in records)
            or self.first_technical_failure_ordinal != failed_ordinals[0]
            or self.consecutive_parsed_prefix_count != failed_ordinals[0] - 1
        ):
            raise ValueError("recovery closeout request census does not reconcile")
        expected = (
            _summaries(records, "mechanism"),
            _summaries(records, "evidence_condition"),
            _summaries(records, "variant"),
            _summaries(records, "family_id"),
        )
        if expected != (
            self.mechanism_summaries,
            self.condition_summaries,
            self.variant_summaries,
            self.family_summaries,
        ):
            raise ValueError("recovery closeout missingness tables do not reconcile")
        if self.closeout_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("recovery closeout identity does not match content")
        return self


def _summary(
    stratum_id: str, records: Sequence[RecoveryRequestDisposition]
) -> RecoveryMissingnessSummary:
    return RecoveryMissingnessSummary(
        stratum_id=stratum_id,
        scheduled_request_count=len(records),
        parsed_request_count=sum(item.gateway_status == "parsed" for item in records),
        technical_failure_count=sum(item.gateway_status != "parsed" for item in records),
        retried_request_count=sum(item.attempt_count > 1 for item in records),
        claim_candidate_count=sum(item.claim_candidate_count for item in records),
    )


def _summaries(
    records: Sequence[RecoveryRequestDisposition],
    attribute: Literal["mechanism", "evidence_condition", "variant", "family_id"],
) -> tuple[RecoveryMissingnessSummary, ...]:
    order = tuple(dict.fromkeys(str(getattr(item, attribute)) for item in records))
    return tuple(
        _summary(
            value,
            tuple(item for item in records if str(getattr(item, attribute)) == value),
        )
        for value in order
    )


def _frozen_execution_state(
    authorization: RecoveryAuthorization,
) -> RepositoryExecutionState:
    return RepositoryExecutionState(
        branch="main",
        head_commit=authorization.source_commit_ref,
        origin_main_commit=authorization.source_commit_ref,
        clean=True,
    )


def _reader(store: Path, identity: str) -> ClaimCorpusTerminalReader:
    shard = store / "requests" / identity
    return ClaimCorpusTerminalReader(
        root=shard,
        object_root=shard / "objects" / "sha256",
        request_root=shard / "requests",
        terminal_root=shard / "terminal",
        failure_root=shard / "failures",
    )


def _load_recovery_inputs(
    root: Path, run_dir: Path
) -> tuple[
    RecoveryAuthorization,
    dict[str, object],
    tuple[PreparedClaimCorpusRequest, ...],
    ClaimCorpusRequestCensus,
]:
    run = run_dir.resolve()
    authorization = load_recovery_authorization(run, "diagnosis")
    if authorization.phase != "diagnosis":
        raise ClaimPoolConstructionError("recovery run is not the diagnosis phase")
    state = _frozen_execution_state(authorization)
    receipt = verify_completed_recovery(root, state=state, run_dir=run, phase="diagnosis")
    if receipt["status"] != "recovery_diagnosis_execution_complete":
        raise ClaimPoolConstructionError("recovery diagnosis is not terminal")
    prepared = authorized_recovery_requests(
        root, state=state, authorization=authorization, run_dir=run
    )
    census = ClaimCorpusRequestCensus.model_validate_json((root / REQUEST_CENSUS_PATH).read_bytes())
    if tuple(item.request_sha256 for item in prepared) != tuple(
        item.request_sha256 for item in census.primary_requests
    ):
        raise ClaimPoolConstructionError("recovery schedule differs from frozen census")
    return authorization, receipt, prepared, census


def build_recovery_execution_closeout(root: Path, *, run_dir: Path) -> RecoveryExecutionCloseout:
    """Independently close the recovery terminal state without provider access."""

    checked_root = root.resolve()
    authorization, receipt, prepared, census = _load_recovery_inputs(checked_root, run_dir)
    store = run_dir.resolve() / "diagnosis-store"
    records: list[RecoveryRequestDisposition] = []
    for ordinal, (item, frozen) in enumerate(
        zip(prepared, census.primary_requests, strict=True), start=1
    ):
        identity = item.request.initial_attempt.request_identity_sha256
        reader = _reader(store, identity)
        inventory = reader.terminal_inventory(identity)
        issue = reader.terminal_issue(identity)
        parsed = reader.terminal_parsed_payload(identity)
        candidate_count = 0
        normalized_count = 0
        if parsed is not None:
            context = item.request.context
            if not isinstance(context, ModelVisibleEvidenceContext):
                raise ClaimPoolConstructionError("recovery evidence context differs")
            output = normalize_provider_output_v2(
                frozen,
                parsed,
                source_record_sha256=canonical_execution_sha256(parsed),
                visible_evidence_ids=tuple(evidence.evidence_id for evidence in context.items),
            )
            normalized_count = 1
            candidate_count = len(output.atomic_claims)
        payload: dict[str, object] = {
            "request_ordinal": ordinal,
            "request_sha256": frozen.request_sha256,
            "request_identity_sha256": identity,
            "mechanism": frozen.mechanism,
            "family_id": frozen.family_id,
            "evidence_condition": frozen.evidence_condition,
            "variant": frozen.variant,
            "gateway_status": inventory.gateway_status,
            "attempt_count": len(inventory.attempt_outcomes),
            "issue_code": issue.code if issue is not None else None,
            "normalized_output_count": normalized_count,
            "claim_candidate_count": candidate_count,
        }
        records.append(
            RecoveryRequestDisposition.model_validate(
                {
                    **payload,
                    "disposition_sha256": canonical_execution_sha256(payload),
                }
            )
        )
    first_failure = next(
        item.request_ordinal for item in records if item.gateway_status != "parsed"
    )
    evidence_census = ObservedEvidenceCensus.model_validate_json(
        (
            checked_root / "configs/evaluation/claim_support_observed_evidence_census.json"
        ).read_bytes()
    )
    payload = {
        "schema_version": "claim-corpus-recovery-closeout/v1",
        "status": "recovery_execution_closed_missingness_preserved",
        "source_commit_ref": authorization.source_commit_ref,
        "authorization_sha256": authorization.authorization_sha256,
        "protocol_sha256": authorization.protocol_sha256,
        "transport_sha256": authorization.transport_sha256,
        "execution_plan_sha256": authorization.execution_plan_sha256,
        "recovery_receipt_sha256": receipt["receipt_sha256"],
        "recovery_terminal_store_sha256": receipt["terminal_store_sha256"],
        "evidence_census_sha256": evidence_census.census_sha256,
        "terminal_request_count": 360,
        "parsed_terminal_count": receipt["normalized_output_count"],
        "technical_failure_terminal_count": (360 - cast(int, receipt["normalized_output_count"])),
        "technical_attempt_count": receipt["technical_attempt_count"],
        "normalized_output_count": receipt["normalized_output_count"],
        "claim_candidate_count": receipt["claim_candidate_count"],
        "first_technical_failure_ordinal": first_failure,
        "consecutive_parsed_prefix_count": first_failure - 1,
        "execution_order_dimensions": (
            "mechanism",
            "family",
            "evidence_condition",
            "variant",
        ),
        "missingness_exchangeability_status": ("not_established_execution_order_confounded"),
        "mechanism_summaries": tuple(
            item.model_dump(mode="json") for item in _summaries(records, "mechanism")
        ),
        "condition_summaries": tuple(
            item.model_dump(mode="json") for item in _summaries(records, "evidence_condition")
        ),
        "variant_summaries": tuple(
            item.model_dump(mode="json") for item in _summaries(records, "variant")
        ),
        "family_summaries": tuple(
            item.model_dump(mode="json") for item in _summaries(records, "family_id")
        ),
        "request_dispositions": tuple(item.model_dump(mode="json") for item in records),
        "failures_preserved_in_denominator": True,
        "recovery_rerun_forbidden": True,
        "reserve_activation_forbidden_after_execution": True,
        "availability_may_not_be_interpreted_as_mechanism_performance": True,
        "ready_for_recovery_pool_preparation": True,
        "relation_assignments_generated": False,
        "automatic_labels_generated": False,
        "claims_materialized": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return RecoveryExecutionCloseout.model_validate(
        {**payload, "closeout_sha256": canonical_execution_sha256(payload)}
    )


def build_recovery_claim_pool_preparation(
    root: Path,
    *,
    run_dir: Path,
    closeout: RecoveryExecutionCloseout,
) -> RecoveryClaimPoolPreparation:
    """Normalize recovery outputs and bind the exact blind relation census."""

    checked_root = root.resolve()
    fresh_closeout = build_recovery_execution_closeout(checked_root, run_dir=run_dir)
    if fresh_closeout != closeout:
        raise ClaimPoolConstructionError(
            "recovery preparation input differs from independent closeout"
        )
    authorization, receipt, prepared, census = _load_recovery_inputs(checked_root, run_dir)
    store = run_dir.resolve() / "diagnosis-store"
    evidence_census = ObservedEvidenceCensus.model_validate_json(
        (
            checked_root / "configs/evaluation/claim_support_observed_evidence_census.json"
        ).read_bytes()
    )
    evidence_by_key = {
        (item.family_id, item.evidence_condition): item for item in evidence_census.bindings
    }
    records: list[ClaimNormalizationRecord] = []
    relation_requests: list[ClaimRelationAssignmentRequest] = []
    for item, frozen in zip(prepared, census.primary_requests, strict=True):
        identity = item.request.initial_attempt.request_identity_sha256
        reader = _reader(store, identity)
        inventory = reader.terminal_inventory(identity)
        parsed = reader.terminal_parsed_payload(identity)
        if parsed is None:
            records.append(
                _normalization_record(
                    request=frozen,
                    identity=identity,
                    gateway_status=inventory.gateway_status,
                    status="technical_failure",
                    source_record_sha256=None,
                    issue_sha256=inventory.issue_sha256,
                    output=None,
                    relation_requests=(),
                    blocker_code="technical_terminal",
                )
            )
            continue
        context = item.request.context
        if not isinstance(context, ModelVisibleEvidenceContext):
            raise ClaimPoolConstructionError("recovery evidence context differs")
        source_record_sha256 = canonical_execution_sha256(parsed)
        try:
            output = normalize_provider_output_v2(
                frozen,
                parsed,
                source_record_sha256=source_record_sha256,
                visible_evidence_ids=tuple(evidence.evidence_id for evidence in context.items),
            )
        except ClaimCorpusContractError:
            records.append(
                _normalization_record(
                    request=frozen,
                    identity=identity,
                    gateway_status=inventory.gateway_status,
                    status="schema_rejected",
                    source_record_sha256=source_record_sha256,
                    issue_sha256=None,
                    output=None,
                    relation_requests=(),
                    blocker_code="provider_schema_incompatible",
                )
            )
            continue
        output_requests: tuple[ClaimRelationAssignmentRequest, ...] = ()
        if output.output_status == "completed":
            binding = evidence_by_key[(frozen.family_id, frozen.evidence_condition)]
            try:
                output_requests = tuple(
                    build_relation_assignment_request(
                        source_output_sha256=output.output_sha256,
                        claim_local_id=claim.claim_local_id,
                        claim_text=claim.claim_text,
                        claim_type=claim.claim_type,
                        cited_evidence_ids=claim.visible_evidence_ids,
                        evidence_binding=binding,
                    )
                    for claim in output.atomic_claims
                )
            except ClaimCorpusContractError:
                records.append(
                    _normalization_record(
                        request=frozen,
                        identity=identity,
                        gateway_status=inventory.gateway_status,
                        status="claim_binding_rejected",
                        source_record_sha256=source_record_sha256,
                        issue_sha256=None,
                        output=output,
                        relation_requests=(),
                        blocker_code="claim_evidence_binding_invalid",
                    )
                )
                continue
        relation_requests.extend(output_requests)
        records.append(
            _normalization_record(
                request=frozen,
                identity=identity,
                gateway_status=inventory.gateway_status,
                status="normalized",
                source_record_sha256=source_record_sha256,
                issue_sha256=None,
                output=output,
                relation_requests=output_requests,
                blocker_code=None,
            )
        )
    policy = load_evidence_semantics_policy(checked_root)
    outputs = tuple(
        record.normalized_output for record in records if record.normalized_output is not None
    )
    claims = tuple(claim for output in outputs for claim in output.atomic_claims)
    statuses = Counter(record.normalization_status for record in records)
    payload: dict[str, object] = {
        "schema_version": RECOVERY_PREPARATION_SCHEMA_VERSION,
        "source_commit_ref": authorization.source_commit_ref,
        "authorization_sha256": authorization.authorization_sha256,
        "execution_plan_sha256": build_execution_plan(checked_root).plan_sha256,
        "recovery_receipt_sha256": receipt["receipt_sha256"],
        "recovery_closeout_sha256": closeout.closeout_sha256,
        "recovery_terminal_store_sha256": receipt["terminal_store_sha256"],
        "evidence_census_sha256": evidence_census.census_sha256,
        "evidence_semantics_policy_sha256": policy.policy_sha256,
        "terminal_request_count": 360,
        "parsed_terminal_count": closeout.parsed_terminal_count,
        "technical_failure_terminal_count": (closeout.technical_failure_terminal_count),
        "normalized_output_count": len(outputs),
        "normalization_rejection_count": (
            statuses["schema_rejected"] + statuses["claim_binding_rejected"]
        ),
        "completed_output_count": sum(output.output_status == "completed" for output in outputs),
        "abstained_output_count": sum(output.output_status == "abstained" for output in outputs),
        "claim_candidate_count": len(claims),
        "relation_request_count": len(relation_requests),
        "canonical_claim_text_count": len({claim.claim_text for claim in claims}),
        "repeated_claim_instance_count": (
            len(claims) - len({claim.claim_text for claim in claims})
        ),
        "records": tuple(record.model_dump(mode="json") for record in records),
        "relation_requests": tuple(
            request.model_dump(mode="json") for request in relation_requests
        ),
        "failures_preserved_in_denominator": True,
        "recovery_rerun_performed": False,
        "free_text_recovery_performed": False,
        "automatic_labels_generated": False,
        "corpus_entries_materialized": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return RecoveryClaimPoolPreparation.model_validate(
        {
            **payload,
            "records": tuple(records),
            "relation_requests": tuple(relation_requests),
            "preparation_sha256": canonical_execution_sha256(payload),
        }
    )


def _normalization_record(
    *,
    request: ClaimCorpusRequest,
    identity: str,
    gateway_status: TerminalStatus,
    status: Literal[
        "normalized",
        "technical_failure",
        "schema_rejected",
        "claim_binding_rejected",
    ],
    source_record_sha256: str | None,
    issue_sha256: str | None,
    output: DiagnosisOutputV2 | None,
    relation_requests: tuple[ClaimRelationAssignmentRequest, ...],
    blocker_code: Literal[
        "technical_terminal",
        "provider_schema_incompatible",
        "claim_evidence_binding_invalid",
    ]
    | None,
) -> ClaimNormalizationRecord:
    output_payload = output.model_dump(mode="json") if output is not None else None
    payload: dict[str, object] = {
        "request_sha256": request.request_sha256,
        "request_identity_sha256": identity,
        "variant": request.variant,
        "gateway_status": gateway_status,
        "normalization_status": status,
        "source_record_sha256": source_record_sha256,
        "issue_sha256": issue_sha256,
        "normalized_output": output_payload,
        "relation_request_sha256s": tuple(
            item.assignment_request_sha256 for item in relation_requests
        ),
        "blocker_code": blocker_code,
    }
    try:
        return ClaimNormalizationRecord.model_validate(
            {
                **payload,
                "normalized_output": output,
                "record_sha256": canonical_execution_sha256(payload),
            }
        )
    except ValidationError as exc:
        raise ClaimPoolConstructionError("recovery normalization record is invalid") from exc


__all__ = [
    "RecoveryExecutionCloseout",
    "RecoveryMissingnessSummary",
    "RecoveryRequestDisposition",
    "build_recovery_claim_pool_preparation",
    "build_recovery_execution_closeout",
]
