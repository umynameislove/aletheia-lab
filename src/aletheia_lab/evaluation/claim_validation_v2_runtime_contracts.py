"""Immutable contracts for the prospective claim-validation V2 runtime."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.evaluation.claim_corpus_contracts import (
    EligibleVariant,
    EvidenceCondition,
    Mechanism,
)
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ClaimRelationAssignmentRequest,
    ModelVisibleEvidenceContext,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.project.identity import SHA256_PATTERN


def evaluate_v2_technical_admission(
    manifest: ClaimSupportValidationV2RuntimeManifest,
    *,
    terminal_request_sha256: Sequence[str],
    parsed_request_sha256: Sequence[str],
) -> dict[str, object]:
    """Assess the frozen diagnosis thresholds from independently audited request IDs.

    This pure reducer does not authenticate a store or authorize execution.
    All scheduled requests remain denominators, including terminal failures.
    """
    checked = ClaimSupportValidationV2RuntimeManifest.model_validate(
        manifest.model_dump(mode="python")
    )
    scheduled = {item.v2_request_sha256 for item in checked.diagnosis_schedule}
    terminal, parsed = set(terminal_request_sha256), set(parsed_request_sha256)
    if (
        len(terminal) != len(terminal_request_sha256)
        or len(parsed) != len(parsed_request_sha256)
        or not parsed <= terminal <= scheduled
    ):
        raise ClaimValidationV2RuntimeError("admission census has duplicate or unknown IDs")
    blockers = []
    if terminal != scheduled:
        blockers.append("incomplete_terminal_census")
    if len(parsed) * 100 < len(scheduled) * 95:
        blockers.append("global_parsed_rate_below_95_percent")
    strata = []
    for dimension in ("mechanism", "evidence_condition", "variant"):
        totals: Counter[str] = Counter()
        successes: Counter[str] = Counter()
        for entry in checked.diagnosis_schedule:
            if dimension == "variant" and entry.variant == "B0":
                continue
            key = str(getattr(entry, dimension))
            totals[key] += 1
            successes[key] += entry.v2_request_sha256 in parsed
        for key, denominator in sorted(totals.items()):
            passed = successes[key] * 100 >= denominator * 90
            strata.append({"dimension": dimension, "value": key,
                           "parsed_count": successes[key], "scheduled_count": denominator,
                           "passed": passed})
            if not passed:
                blockers.append(f"{dimension}:{key}:below_90_percent")
    return {
        "runtime_manifest_sha256": checked.manifest_sha256,
        "scheduled_request_count": len(scheduled), "terminal_request_count": len(terminal),
        "parsed_request_count": len(parsed), "strata": tuple(strata),
        "blocker_codes": tuple(blockers), "technical_admission_passed": not blockers,
        "missingness_exchangeability_established": False,
        "sample_selection_authorized": False,
    }

REQUEST_CENSUS_PATH: Final = "configs/evaluation/claim_support_request_census.json"
EVIDENCE_CENSUS_PATH: Final = (
    "configs/evaluation/claim_support_observed_evidence_census.json"
)
RUNTIME_MANIFEST_PATH: Final = (
    "configs/evaluation/claim_support_validation_v2_runtime_manifest.json"
)
RUNTIME_READINESS_PATH: Final = (
    "configs/evaluation/claim_support_validation_v2_runtime_readiness.json"
)
RUNTIME_MANIFEST_SCHEMA_VERSION: Final = "claim-support-validation-v2-runtime/v1"
RUNTIME_READINESS_SCHEMA_VERSION: Final = (
    "claim-support-validation-v2-runtime-readiness/v1"
)
SCHEDULE_ALGORITHM: Final = "balanced_interleave_sha256/v1"
FRAME_ASSIGNMENT_ALGORITHM: Final = (
    "pre-relation-balanced-hash-over-eligible-authentic-frames/v1"
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
ExecutionRoute = Literal["model_gateway", "deterministic_local"]
RelationFrame = Literal[
    "natural_context",
    "support_withdrawal",
    "partial_support_projection",
    "direct_counterevidence",
]
ChallengeFrame = Literal[
    "support_withdrawal",
    "partial_support_projection",
    "direct_counterevidence",
]
IneligibilityReason = Literal[
    "decisive_support_not_cited",
    "withdrawal_removed_all_cited_evidence",
    "claim_has_one_material_part",
    "claim_has_one_citation",
    "registered_projection_not_cited",
    "no_authentic_counterevidence",
    "material_measurement_binding_unestablished",
    "withdrawal_not_a_proper_projection",
    "projection_not_a_proper_subset",
]


class ClaimValidationV2RuntimeError(ValueError):
    """Raised when V2 scheduling or authentic frame construction diverges."""


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class V2DiagnosisScheduleEntry(StrictFrozenModel):
    protocol_sha256: Sha256
    sequence: int = Field(ge=1, le=360)
    schedule_round: int = Field(ge=1, le=15)
    source_request_sha256: Sha256
    family_id: str
    family_sha256: Sha256
    mechanism: Mechanism
    family_order: int = Field(ge=1, le=5)
    evidence_condition: EvidenceCondition
    variant: EligibleVariant
    execution_route: ExecutionRoute
    visible_context_sha256: Sha256
    v2_request_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"v2_request_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        expected_route = (
            "deterministic_local" if self.variant == "B0" else "model_gateway"
        )
        if (
            self.execution_route != expected_route
            or not self.family_id.endswith(f"-{self.family_order}")
            or self.v2_request_sha256
            != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("V2 diagnosis schedule entry does not reconcile")
        return self


class V2SourceFrameEntry(StrictFrozenModel):
    family_id: str
    family_sha256: Sha256
    mechanism: Mechanism
    evidence_condition: EvidenceCondition
    source_projection_sha256: Sha256
    natural_context_sha256: Sha256
    natural_evidence_ids: tuple[str, ...]
    support_withdrawal_context_sha256: Sha256 | None
    partial_support_context_sha256: Sha256
    partial_support_evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=1)
    direct_counterevidence_context_sha256: Sha256
    direct_counterevidence_family_id: str
    eligible_frames: tuple[RelationFrame, ...]
    entry_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"entry_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        expected = ["natural_context"]
        if self.support_withdrawal_context_sha256 is not None:
            expected.append("support_withdrawal")
        expected.extend(("partial_support_projection", "direct_counterevidence"))
        if (
            self.eligible_frames != tuple(expected)
            or self.direct_counterevidence_family_id == self.family_id
            or len(self.natural_evidence_ids) != len(set(self.natural_evidence_ids))
            or self.partial_support_evidence_ids[0] not in self.natural_evidence_ids
            or self.partial_support_context_sha256 == self.natural_context_sha256
            or self.direct_counterevidence_context_sha256
            == self.natural_context_sha256
            or (
                self.support_withdrawal_context_sha256 is not None
                and self.support_withdrawal_context_sha256
                in {
                    self.natural_context_sha256,
                    self.partial_support_context_sha256,
                }
            )
            or self.entry_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("V2 source-frame entry does not reconcile")
        return self


class V2FrameCapacity(StrictFrozenModel):
    frame: RelationFrame
    family_count: int = Field(ge=10, le=15)
    context_count: int = Field(ge=25, le=45)
    scheduled_diagnosis_cell_count: int = Field(ge=25, le=360)


class V2QualificationProbe(StrictFrozenModel):
    variant: Literal["A1", "A2", "A3", "B1", "B2", "CodeGraph", "FULL"]
    source_schedule_entry_sha256: Sha256
    prompt_text: str = Field(min_length=1)
    response_schema_json: str = Field(min_length=1)
    context: ModelVisibleEvidenceContext
    model_snapshot: Literal["gpt-4.1-2025-04-14"]
    maximum_output_tokens: Literal[2048]
    qualification_request_sha256: Sha256
    synthetic_only: Literal[True] = True
    admitted_to_corpus: Literal[False] = False


class V2RuntimeSourceBinding(StrictFrozenModel):
    relative_path: str = Field(pattern=r"^(src|scripts)/[a-zA-Z0-9_./-]+\.py$")
    content_sha256: Sha256


class ClaimSupportValidationV2RuntimeManifest(StrictFrozenModel):
    schema_version: Literal["claim-support-validation-v2-runtime/v1"] = (
        RUNTIME_MANIFEST_SCHEMA_VERSION
    )
    status: Literal["v2_runtime_frozen_qualification_authorization_pending"]
    protocol_sha256: Sha256
    request_census_sha256: Sha256
    evidence_census_sha256: Sha256
    schedule_algorithm: Literal["balanced_interleave_sha256/v1"]
    frame_assignment_algorithm: Literal[
        "pre-relation-balanced-hash-over-eligible-authentic-frames/v1"
    ]
    diagnosis_schedule: tuple[V2DiagnosisScheduleEntry, ...] = Field(
        min_length=360, max_length=360
    )
    source_frames: tuple[V2SourceFrameEntry, ...] = Field(
        min_length=45, max_length=45
    )
    frame_capacity: tuple[V2FrameCapacity, ...] = Field(min_length=4, max_length=4)
    qualification_probes: tuple[V2QualificationProbe, ...] = Field(
        min_length=7, max_length=7
    )
    implementation_sources: tuple[V2RuntimeSourceBinding, ...] = Field(
        min_length=10, max_length=10
    )
    minimum_provider_start_interval_ms: Literal[1000]
    retry_initial_backoff_ms: Literal[5000]
    retry_backoff_multiplier: Literal[2]
    retry_backoff_ceiling_ms: Literal[60000]
    retry_after_ceiling_ms: Literal[60000]
    provider_calls_executed: Literal[False] = False
    claims_materialized: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    manifest_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"manifest_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        schedule = self.diagnosis_schedule
        frame_pairs = {
            (item.family_id, item.evidence_condition): item for item in self.source_frames
        }
        qualification_hashes = tuple(
            canonical_execution_sha256(
                {
                    "protocol_sha256": self.protocol_sha256,
                    **item.model_dump(mode="json", exclude={"qualification_request_sha256"}),
                }
            )
            for item in self.qualification_probes
        )
        if (
            tuple(item.sequence for item in schedule) != tuple(range(1, 361))
            or len({item.source_request_sha256 for item in schedule}) != 360
            or len({item.v2_request_sha256 for item in schedule}) != 360
            or any(item.source_request_sha256 == item.v2_request_sha256 for item in schedule)
            or any(item.protocol_sha256 != self.protocol_sha256 for item in schedule)
            or len(frame_pairs) != 45
            or any(
                (item.family_id, item.evidence_condition) not in frame_pairs
                or item.visible_context_sha256
                != frame_pairs[(item.family_id, item.evidence_condition)].natural_context_sha256
                for item in schedule
            )
            or tuple(item.frame for item in self.frame_capacity)
            != (
                "natural_context",
                "support_withdrawal",
                "partial_support_projection",
                "direct_counterevidence",
            )
            or tuple(item.variant for item in self.qualification_probes)
            != ("A1", "A2", "A3", "B1", "B2", "CodeGraph", "FULL")
            or qualification_hashes
            != tuple(item.qualification_request_sha256 for item in self.qualification_probes)
            or tuple(item.relative_path for item in self.implementation_sources)
            != tuple(sorted(item.relative_path for item in self.implementation_sources))
            or len({item.relative_path for item in self.implementation_sources}) != 10
            or self.manifest_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("V2 runtime manifest census or identity differs")
        return self


class ClaimSupportValidationV2RuntimeReadiness(StrictFrozenModel):
    schema_version: Literal["claim-support-validation-v2-runtime-readiness/v1"] = (
        RUNTIME_READINESS_SCHEMA_VERSION
    )
    status: Literal["claim_support_validation_v2_runtime_ready_qualification_pending"]
    protocol_sha256: Sha256
    runtime_manifest_sha256: Sha256
    diagnosis_request_count: Literal[360]
    provider_backed_diagnosis_request_count: Literal[315]
    deterministic_diagnosis_request_count: Literal[45]
    relation_request_ceiling: Literal[1440]
    qualification_request_count: Literal[7]
    source_frame_context_count: Literal[45]
    all_source_frames_meet_structural_family_minimum: Literal[True]
    all_source_frames_meet_structural_scheduled_cell_minimum: Literal[True]
    balanced_round_count: Literal[15]
    safe_failure_taxonomy_bound: Literal[True]
    retry_and_pacing_policy_bound: Literal[True]
    source_claim_expressiveness_review_required: Literal[True]
    live_qualification_authorized: Literal[False] = False
    provider_calls_executed: Literal[False] = False
    claims_materialized: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    readiness_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"readiness_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        if self.readiness_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("V2 runtime readiness identity differs")
        return self


class V2FrameIneligibility(StrictFrozenModel):
    schedule_entry_sha256: Sha256
    source_output_sha256: Sha256
    claim_local_id: str
    frame: ChallengeFrame
    reason: IneligibilityReason


class V2RelationFrameAssignment(StrictFrozenModel):
    schedule_entry_sha256: Sha256
    family_id: str
    source_output_sha256: Sha256
    claim_local_id: str
    frame: RelationFrame
    request: ClaimRelationAssignmentRequest
    assignment_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"assignment_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        if (
            self.request.source_output_sha256 != self.source_output_sha256
            or self.request.claim_local_id != self.claim_local_id
            or self.assignment_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("V2 frame assignment identity differs")
        return self


class V2RelationFrameBatch(StrictFrozenModel):
    protocol_sha256: Sha256
    runtime_manifest_sha256: Sha256
    source_claim_count: int = Field(ge=0)
    assignments: tuple[V2RelationFrameAssignment, ...]
    ineligible_frames: tuple[V2FrameIneligibility, ...]
    relation_outcomes_observed: Literal[False] = False
    batch_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"batch_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        natural = sum(item.frame == "natural_context" for item in self.assignments)
        challenges = len(self.assignments) - natural
        by_claim: dict[tuple[str, str], list[V2RelationFrameAssignment]] = {}
        for assignment in self.assignments:
            by_claim.setdefault(
                (assignment.source_output_sha256, assignment.claim_local_id), []
            ).append(assignment)
        if (
            natural != self.source_claim_count
            or challenges > self.source_claim_count
            or len({item.assignment_sha256 for item in self.assignments})
            != len(self.assignments)
            or len(
                {item.request.assignment_request_sha256 for item in self.assignments}
            )
            != len(self.assignments)
            or any(
                len(items) not in {1, 2}
                or sum(item.frame == "natural_context" for item in items) != 1
                for items in by_claim.values()
            )
            or self.batch_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("V2 relation-frame batch does not reconcile")
        return self


__all__ = [
    "EVIDENCE_CENSUS_PATH",
    "FRAME_ASSIGNMENT_ALGORITHM",
    "REQUEST_CENSUS_PATH",
    "RUNTIME_MANIFEST_PATH",
    "RUNTIME_MANIFEST_SCHEMA_VERSION",
    "RUNTIME_READINESS_PATH",
    "RUNTIME_READINESS_SCHEMA_VERSION",
    "SCHEDULE_ALGORITHM",
    "ChallengeFrame",
    "ClaimSupportValidationV2RuntimeManifest",
    "ClaimSupportValidationV2RuntimeReadiness",
    "ClaimValidationV2RuntimeError",
    "ExecutionRoute",
    "IneligibilityReason",
    "RelationFrame",
    "StrictFrozenModel",
    "V2DiagnosisScheduleEntry",
    "V2FrameCapacity",
    "V2FrameIneligibility",
    "V2QualificationProbe",
    "V2RuntimeSourceBinding",
    "V2RelationFrameAssignment",
    "V2RelationFrameBatch",
    "V2SourceFrameEntry",
    "evaluate_v2_technical_admission",
]
