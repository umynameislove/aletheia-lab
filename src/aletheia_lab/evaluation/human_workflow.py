"""Fail-closed lifecycle for independent claim-support human annotation.

This module extends the frozen claim-support validation protocol without
changing its sampling or scientific thresholds.  It owns only the human
boundary: synthetic onboarding materials, untrusted rater submissions,
immutable completed packets, and an independence receipt for a completed pair.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Annotated, Final, Literal, NoReturn, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.evaluation.instrument_validation import (
    LABEL_ORDER,
    BlankAnnotationDecision,
    BlindAnnotationPacket,
    BlindClaim,
    BlindEvidenceExcerpt,
    ClaimSupportValidationProtocol,
    SupportLabel,
    load_validation_protocol,
)
from aletheia_lab.project.identity import SHA256_PATTERN, content_sha256, normalize_text

HUMAN_WORKFLOW_SCHEMA_VERSION: Final = "claim-support-human-workflow/v1"
ONBOARDING_FIXTURE_SCHEMA_VERSION: Final = "claim-support-onboarding-fixture/v1"
RATER_SUBMISSION_SCHEMA_VERSION: Final = "claim-support-rater-submission/v1"
COMPLETED_PACKET_SCHEMA_VERSION: Final = "claim-support-completed-packet/v1"
ONBOARDING_KEY_SCHEMA_VERSION: Final = "claim-support-onboarding-answer-key/v1"
ONBOARDING_ASSESSMENT_SCHEMA_VERSION: Final = "claim-support-onboarding-assessment/v1"
PAIR_RECEIPT_SCHEMA_VERSION: Final = "claim-support-human-pair-receipt/v1"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
RaterSlot = Literal["rater_1", "rater_2"]
StudyPhase = Literal["onboarding", "main"]
OnboardingStatus = Literal["ready_for_main_annotation", "retraining_required"]
WorkflowId = Literal[
    "claim-support-independent-human-workflow-v1",
    "claim-support-independent-human-workflow-v2",
]
FixtureId = Literal[
    "claim-support-synthetic-onboarding-v1",
    "claim-support-synthetic-onboarding-v2",
]


class HumanWorkflowError(ValueError):
    """Raised when human-workflow material is incomplete, altered, or unsafe."""


def _fail(message: str) -> NoReturn:
    raise HumanWorkflowError(message)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


def _relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if "\\" in value or path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ValueError("workflow artifact path must be canonical and repository-relative")
    return value


class HumanWorkflowProtocol(_StrictFrozenModel):
    schema_version: Literal["claim-support-human-workflow/v1"] = HUMAN_WORKFLOW_SCHEMA_VERSION
    workflow_id: WorkflowId
    validation_protocol_path: str
    validation_protocol_sha256: Sha256
    rater_guide_path: str
    rater_guide_sha256: Sha256
    onboarding_fixture_path: str
    onboarding_fixture_sha256: Sha256
    onboarding_case_count: Literal[20] = 20
    onboarding_quota_per_label: Literal[5] = 5
    onboarding_minimum_macro_f1: float = Field(ge=0.0, le=1.0)
    onboarding_maximum_critical_false_support_count: int = Field(ge=0, le=20)
    main_claim_count: Literal[200] = 200
    rater_slots: tuple[Literal["rater_1"], Literal["rater_2"]]
    required_attestations: tuple[
        Literal["completed_by_human"],
        Literal["worked_independently"],
        Literal["model_assistance_used_false"],
        Literal["rubric_read_before_rating"],
    ]
    evaluator_mapping_withheld_until_both_packets_locked: Literal[True] = True
    onboarding_excluded_from_scientific_denominators: Literal[True] = True
    main_or_sealed_outcomes_forbidden: Literal[True] = True
    human_annotations_collected: Literal[False] = False
    workflow_sha256: Sha256

    @field_validator(
        "validation_protocol_path",
        "rater_guide_path",
        "onboarding_fixture_path",
    )
    @classmethod
    def _paths_are_relative(cls, value: str) -> str:
        return _relative_path(value)

    @model_validator(mode="after")
    def _workflow_is_coherent(self) -> Self:
        if self.rater_slots != ("rater_1", "rater_2"):
            raise ValueError("workflow requires exactly two ordered independent rater slots")
        if self.onboarding_case_count != self.onboarding_quota_per_label * len(LABEL_ORDER):
            raise ValueError("onboarding quotas must sum to the onboarding census")
        if (
            self.onboarding_minimum_macro_f1 != 0.8
            or self.onboarding_maximum_critical_false_support_count != 0
        ):
            raise ValueError("onboarding qualification gates must equal the frozen values")
        payload = self.model_dump(mode="json", exclude={"workflow_sha256"})
        if self.workflow_sha256 != canonical_sha256(payload):
            raise ValueError("workflow hash does not match canonical content")
        return self


class OnboardingEvidence(_StrictFrozenModel):
    evidence_id: str = Field(pattern=r"^training(?:-v2)?-evidence-[0-9]{2}$")
    artifact_ref: str
    excerpt: str

    @field_validator("artifact_ref")
    @classmethod
    def _artifact_ref_is_relative(cls, value: str) -> str:
        return _relative_path(value)

    @field_validator("excerpt")
    @classmethod
    def _excerpt_is_bounded(cls, value: str) -> str:
        return normalize_text(value, label="onboarding evidence", max_length=1024)


class OnboardingCase(_StrictFrozenModel):
    case_id: str = Field(pattern=r"^onboarding(?:-v2)?-case-[0-9]{2}$")
    claim_text: str
    visible_evidence: tuple[OnboardingEvidence, ...] = Field(min_length=1, max_length=3)
    reference_label: SupportLabel
    teaching_note: str

    @field_validator("claim_text")
    @classmethod
    def _claim_is_bounded(cls, value: str) -> str:
        return normalize_text(value, label="onboarding claim", max_length=1024)

    @field_validator("teaching_note")
    @classmethod
    def _note_is_bounded(cls, value: str) -> str:
        return normalize_text(value, label="onboarding teaching note", max_length=1024)

    @model_validator(mode="after")
    def _evidence_ids_are_unique(self) -> Self:
        identifiers = tuple(item.evidence_id for item in self.visible_evidence)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("onboarding evidence IDs must be unique within a case")
        return self


class OnboardingFixture(_StrictFrozenModel):
    schema_version: Literal["claim-support-onboarding-fixture/v1"] = (
        ONBOARDING_FIXTURE_SCHEMA_VERSION
    )
    fixture_id: FixtureId
    purpose: Literal["rater_training_and_qualification_only"]
    synthetic_only: Literal[True] = True
    eligible_for_scientific_analysis: Literal[False] = False
    cases: tuple[OnboardingCase, ...] = Field(min_length=20, max_length=20)
    fixture_sha256: Sha256

    @model_validator(mode="after")
    def _fixture_is_balanced_and_bound(self) -> Self:
        identifiers = tuple(item.case_id for item in self.cases)
        evidence_ids = tuple(
            evidence.evidence_id for item in self.cases for evidence in item.visible_evidence
        )
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("onboarding case IDs must be unique")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("onboarding evidence IDs must be globally unique")
        census = Counter(item.reference_label for item in self.cases)
        if census != Counter({label: 5 for label in LABEL_ORDER}):
            raise ValueError("onboarding fixture must contain five cases per support label")
        case_prefix = (
            "onboarding-case-"
            if self.fixture_id == "claim-support-synthetic-onboarding-v1"
            else "onboarding-v2-case-"
        )
        evidence_prefix = (
            "training-evidence-"
            if self.fixture_id == "claim-support-synthetic-onboarding-v1"
            else "training-v2-evidence-"
        )
        if any(not identifier.startswith(case_prefix) for identifier in identifiers):
            raise ValueError("onboarding case IDs must match the fixture version")
        if any(not identifier.startswith(evidence_prefix) for identifier in evidence_ids):
            raise ValueError("onboarding evidence IDs must match the fixture version")
        payload = self.model_dump(mode="json", exclude={"fixture_sha256"})
        if self.fixture_sha256 != canonical_sha256(payload):
            raise ValueError("onboarding fixture hash does not match canonical content")
        return self


class CompletedAnnotationDecision(_StrictFrozenModel):
    blind_claim_id: str = Field(pattern=r"^blind-claim-[0-9a-f]{64}$")
    support_label: SupportLabel
    evidence_ids_used: tuple[str, ...]
    rationale: str = Field(min_length=20, max_length=1000)

    @field_validator("evidence_ids_used")
    @classmethod
    def _evidence_is_unique(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("a decision cannot cite the same evidence twice")
        return values

    @field_validator("rationale")
    @classmethod
    def _rationale_is_canonical(cls, value: str) -> str:
        return normalize_text(value, label="annotation rationale", max_length=1000)


class HumanRaterAttestation(_StrictFrozenModel):
    completed_by_human: Literal[True]
    worked_independently: Literal[True]
    model_assistance_used: Literal[False]
    rubric_read_before_rating: Literal[True]


class HumanRaterSubmission(_StrictFrozenModel):
    schema_version: Literal["claim-support-rater-submission/v1"] = RATER_SUBMISSION_SCHEMA_VERSION
    workflow_sha256: Sha256
    source_packet_sha256: Sha256
    phase: StudyPhase
    rater_slot: RaterSlot
    decisions: tuple[CompletedAnnotationDecision, ...]
    attestation: HumanRaterAttestation


class CompletedAnnotationPacket(_StrictFrozenModel):
    schema_version: Literal["claim-support-completed-packet/v1"] = COMPLETED_PACKET_SCHEMA_VERSION
    workflow_sha256: Sha256
    validation_protocol_sha256: Sha256
    source_packet_sha256: Sha256
    source_packet_id: str
    phase: StudyPhase
    rater_slot: RaterSlot
    claim_count: int = Field(ge=1, le=250)
    decisions: tuple[CompletedAnnotationDecision, ...]
    attestation: HumanRaterAttestation
    completed_packet_sha256: Sha256

    @model_validator(mode="after")
    def _packet_is_complete_and_bound(self) -> Self:
        identifiers = tuple(item.blind_claim_id for item in self.decisions)
        if self.claim_count != len(identifiers) or len(identifiers) != len(set(identifiers)):
            raise ValueError("completed decision census must be exact and unique")
        payload = self.model_dump(mode="json", exclude={"completed_packet_sha256"})
        if self.completed_packet_sha256 != canonical_sha256(payload):
            raise ValueError("completed packet hash does not match canonical content")
        return self


class OnboardingAnswerKeyEntry(_StrictFrozenModel):
    blind_claim_id: str = Field(pattern=r"^blind-claim-[0-9a-f]{64}$")
    reference_label: SupportLabel
    teaching_note: str

    @field_validator("teaching_note")
    @classmethod
    def _note_is_bounded(cls, value: str) -> str:
        return normalize_text(value, label="onboarding answer note", max_length=1024)


class OnboardingAnswerKey(_StrictFrozenModel):
    schema_version: Literal["claim-support-onboarding-answer-key/v1"] = (
        ONBOARDING_KEY_SCHEMA_VERSION
    )
    workflow_sha256: Sha256
    fixture_sha256: Sha256
    claim_set_sha256: Sha256
    entries: tuple[OnboardingAnswerKeyEntry, ...]
    answer_key_sha256: Sha256

    @model_validator(mode="after")
    def _key_is_complete_and_bound(self) -> Self:
        identifiers = tuple(item.blind_claim_id for item in self.entries)
        if len(identifiers) != 20 or len(identifiers) != len(set(identifiers)):
            raise ValueError("onboarding answer key must contain exactly 20 unique claims")
        if Counter(item.reference_label for item in self.entries) != Counter(
            {label: 5 for label in LABEL_ORDER}
        ):
            raise ValueError("onboarding answer key must preserve the balanced label census")
        payload = self.model_dump(mode="json", exclude={"answer_key_sha256"})
        if self.answer_key_sha256 != canonical_sha256(payload):
            raise ValueError("onboarding answer-key hash does not match canonical content")
        return self


class OnboardingAssessment(_StrictFrozenModel):
    schema_version: Literal["claim-support-onboarding-assessment/v1"] = (
        ONBOARDING_ASSESSMENT_SCHEMA_VERSION
    )
    workflow_sha256: Sha256
    completed_packet_sha256: Sha256
    rater_slot: RaterSlot
    case_count: Literal[20] = 20
    macro_f1: float = Field(ge=0.0, le=1.0)
    critical_false_support_count: int = Field(ge=0, le=20)
    macro_f1_gate_passed: bool
    critical_error_gate_passed: bool
    status: OnboardingStatus
    assessment_sha256: Sha256

    @model_validator(mode="after")
    def _assessment_is_fail_closed_and_bound(self) -> Self:
        passed = self.macro_f1_gate_passed and self.critical_error_gate_passed
        if (self.status == "ready_for_main_annotation") != passed:
            raise ValueError("onboarding status must equal the conjunction of its gates")
        payload = self.model_dump(mode="json", exclude={"assessment_sha256"})
        if self.assessment_sha256 != canonical_sha256(payload):
            raise ValueError("onboarding assessment hash does not match canonical content")
        return self


class IndependentPairReceipt(_StrictFrozenModel):
    schema_version: Literal["claim-support-human-pair-receipt/v1"] = PAIR_RECEIPT_SCHEMA_VERSION
    workflow_sha256: Sha256
    phase: StudyPhase
    claim_count: int = Field(ge=1, le=250)
    rater_1_completed_sha256: Sha256
    rater_2_completed_sha256: Sha256
    shared_claim_set_sha256: Sha256
    disagreement_count: int = Field(ge=0)
    contradiction_trigger_count: int = Field(ge=0)
    adjudication_required_count: int = Field(ge=0)
    status: Literal[
        "completed_independent_pair_no_adjudication_required",
        "completed_independent_pair_ready_for_adjudication",
    ]
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def _receipt_is_bounded(self) -> Self:
        if (
            max(
                self.disagreement_count,
                self.contradiction_trigger_count,
                self.adjudication_required_count,
            )
            > self.claim_count
        ):
            raise ValueError("pair counts cannot exceed the claim census")
        requires_adjudication = self.adjudication_required_count > 0
        if (
            self.status == "completed_independent_pair_ready_for_adjudication"
        ) != requires_adjudication:
            raise ValueError("pair status must reflect its adjudication census")
        payload = self.model_dump(mode="json", exclude={"receipt_sha256"})
        if self.receipt_sha256 != canonical_sha256(payload):
            raise ValueError("pair receipt hash does not match canonical content")
        return self


def load_human_workflow(root: Path, path: Path) -> HumanWorkflowProtocol:
    """Load the workflow and verify every referenced immutable input."""

    resolved = path if path.is_absolute() else root / path
    try:
        workflow = HumanWorkflowProtocol.model_validate_json(resolved.read_text(encoding="utf-8"))
        validation_path = root / workflow.validation_protocol_path
        guide_path = root / workflow.rater_guide_path
        fixture_path = root / workflow.onboarding_fixture_path
        protocol = load_validation_protocol(validation_path)
        fixture = load_onboarding_fixture(fixture_path)
        guide_sha256 = content_sha256(guide_path.read_bytes())
    except (OSError, ValidationError, ValueError) as exc:
        raise HumanWorkflowError("human workflow or a referenced artifact is invalid") from exc
    if protocol.protocol_sha256 != workflow.validation_protocol_sha256:
        _fail("human workflow is not bound to the validation protocol")
    if guide_sha256 != workflow.rater_guide_sha256:
        _fail("human workflow rater-guide bytes differ from the frozen binding")
    if fixture.fixture_sha256 != workflow.onboarding_fixture_sha256:
        _fail("human workflow onboarding fixture differs from the frozen binding")
    version = workflow.workflow_id.rsplit("-", maxsplit=1)[-1]
    expected_paths = {
        "v1": (
            "docs/claim-support-rater-guide.md",
            "configs/evaluation/claim_support_onboarding_fixture.json",
        ),
        "v2": (
            "docs/claim-support-rater-guide-v2.md",
            "configs/evaluation/claim_support_onboarding_fixture_v2.json",
        ),
    }
    if (workflow.rater_guide_path, workflow.onboarding_fixture_path) != expected_paths[version]:
        _fail("human workflow version uses mismatched guide or fixture paths")
    expected_fixture_id = workflow.workflow_id.replace(
        "independent-human-workflow", "synthetic-onboarding"
    )
    if fixture.fixture_id != expected_fixture_id:
        _fail("human workflow and onboarding fixture versions differ")
    return workflow


def load_onboarding_fixture(path: Path) -> OnboardingFixture:
    try:
        return OnboardingFixture.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise HumanWorkflowError("onboarding fixture is unavailable or invalid") from exc


def _blind_evidence(
    case: OnboardingCase, workflow: HumanWorkflowProtocol
) -> tuple[BlindEvidenceExcerpt, ...]:
    items: list[BlindEvidenceExcerpt] = []
    for source in case.visible_evidence:
        evidence_id = f"evidence-{canonical_sha256({'workflow': workflow.workflow_sha256, 'evidence': source.evidence_id})[:32]}"
        payload = {
            "evidence_id": evidence_id,
            "artifact_ref": source.artifact_ref,
            "excerpt": source.excerpt,
        }
        items.append(BlindEvidenceExcerpt(**payload, excerpt_sha256=canonical_sha256(payload)))
    return tuple(items)


def prepare_onboarding_materials(
    fixture: OnboardingFixture,
    validation_protocol: ClaimSupportValidationProtocol,
    workflow: HumanWorkflowProtocol,
) -> tuple[BlindAnnotationPacket, BlindAnnotationPacket, OnboardingAnswerKey]:
    """Create two blank human packets and one coordinator-only synthetic key."""

    if fixture.fixture_sha256 != workflow.onboarding_fixture_sha256:
        _fail("onboarding fixture is not bound to the human workflow")
    if validation_protocol.protocol_sha256 != workflow.validation_protocol_sha256:
        _fail("validation protocol is not bound to the human workflow")
    ordered = tuple(
        sorted(
            fixture.cases,
            key=lambda item: canonical_sha256(
                {"workflow": workflow.workflow_sha256, "case": item.case_id}
            ),
        )
    )
    claims = tuple(
        BlindClaim(
            blind_claim_id=f"blind-claim-{canonical_sha256({'workflow': workflow.workflow_sha256, 'case': item.case_id})}",
            claim_text=item.claim_text,
            visible_evidence=_blind_evidence(item, workflow),
        )
        for item in ordered
    )
    packets = tuple(
        _build_blank_packet(claims, slot=slot, workflow=workflow) for slot in workflow.rater_slots
    )
    claim_set_sha256 = canonical_sha256([item.model_dump(mode="json") for item in claims])
    key_entries = tuple(
        OnboardingAnswerKeyEntry(
            blind_claim_id=claim.blind_claim_id,
            reference_label=case.reference_label,
            teaching_note=case.teaching_note,
        )
        for case, claim in zip(ordered, claims, strict=True)
    )
    key_payload = {
        "schema_version": ONBOARDING_KEY_SCHEMA_VERSION,
        "workflow_sha256": workflow.workflow_sha256,
        "fixture_sha256": fixture.fixture_sha256,
        "claim_set_sha256": claim_set_sha256,
        "entries": tuple(item.model_dump(mode="json") for item in key_entries),
    }
    key = OnboardingAnswerKey(
        workflow_sha256=workflow.workflow_sha256,
        fixture_sha256=fixture.fixture_sha256,
        claim_set_sha256=claim_set_sha256,
        entries=key_entries,
        answer_key_sha256=canonical_sha256(key_payload),
    )
    return packets[0], packets[1], key


def _build_blank_packet(
    claims: tuple[BlindClaim, ...],
    *,
    slot: RaterSlot,
    workflow: HumanWorkflowProtocol,
) -> BlindAnnotationPacket:
    packet_id = f"annotation-packet-{canonical_sha256({'workflow': workflow.workflow_sha256, 'slot': slot, 'claims': [item.blind_claim_id for item in claims]})}"
    decisions = tuple(
        BlankAnnotationDecision(blind_claim_id=item.blind_claim_id) for item in claims
    )
    payload = {
        "schema_version": "claim-support-blind-annotation-packet/v1",
        "protocol_sha256": workflow.validation_protocol_sha256,
        "rater_slot": slot,
        "packet_id": packet_id,
        "claims": tuple(item.model_dump(mode="json") for item in claims),
        "decisions": tuple(item.model_dump(mode="json") for item in decisions),
    }
    return BlindAnnotationPacket(
        protocol_sha256=workflow.validation_protocol_sha256,
        rater_slot=slot,
        packet_id=packet_id,
        claims=claims,
        decisions=decisions,
        packet_sha256=canonical_sha256(payload),
    )


def submission_template(
    packet: BlindAnnotationPacket,
    workflow: HumanWorkflowProtocol,
    *,
    phase: StudyPhase,
) -> dict[str, object]:
    """Return the only editable rater artifact; nulls make incompleteness explicit."""

    return {
        "schema_version": RATER_SUBMISSION_SCHEMA_VERSION,
        "workflow_sha256": workflow.workflow_sha256,
        "source_packet_sha256": packet.packet_sha256,
        "phase": phase,
        "rater_slot": packet.rater_slot,
        "decisions": [
            {
                "blind_claim_id": claim.blind_claim_id,
                "support_label": None,
                "evidence_ids_used": [],
                "rationale": None,
            }
            for claim in packet.claims
        ],
        "attestation": {
            "completed_by_human": False,
            "worked_independently": False,
            "model_assistance_used": None,
            "rubric_read_before_rating": False,
        },
    }


def load_rater_submission(path: Path) -> HumanRaterSubmission:
    try:
        return HumanRaterSubmission.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise HumanWorkflowError("rater submission is incomplete or invalid") from exc


def lock_completed_packet(
    packet: BlindAnnotationPacket,
    submission: HumanRaterSubmission,
    workflow: HumanWorkflowProtocol,
) -> CompletedAnnotationPacket:
    """Validate untrusted input and return one immutable, content-bound packet."""

    expected_count = (
        workflow.onboarding_case_count
        if submission.phase == "onboarding"
        else workflow.main_claim_count
    )
    if packet.protocol_sha256 != workflow.validation_protocol_sha256:
        _fail("source packet uses a different validation protocol")
    if submission.workflow_sha256 != workflow.workflow_sha256:
        _fail("submission uses a different human workflow")
    if submission.source_packet_sha256 != packet.packet_sha256:
        _fail("submission is not bound to the supplied blind packet")
    if submission.rater_slot != packet.rater_slot:
        _fail("submission rater slot differs from the blind packet")
    if len(packet.claims) != expected_count:
        _fail("blind packet claim census differs from the registered phase")
    expected_ids = tuple(item.blind_claim_id for item in packet.claims)
    actual_ids = tuple(item.blind_claim_id for item in submission.decisions)
    if actual_ids != expected_ids:
        _fail("submission decisions must match the blind packet exactly and in order")
    visible_ids = {
        claim.blind_claim_id: {item.evidence_id for item in claim.visible_evidence}
        for claim in packet.claims
    }
    for decision in submission.decisions:
        cited = set(decision.evidence_ids_used)
        if not cited.issubset(visible_ids[decision.blind_claim_id]):
            _fail("submission cites evidence absent from the rater-visible claim")
        if decision.support_label != "unsupported" and not cited:
            _fail("contradicted or supported decisions must cite visible evidence")
    payload = {
        "schema_version": COMPLETED_PACKET_SCHEMA_VERSION,
        "workflow_sha256": workflow.workflow_sha256,
        "validation_protocol_sha256": workflow.validation_protocol_sha256,
        "source_packet_sha256": packet.packet_sha256,
        "source_packet_id": packet.packet_id,
        "phase": submission.phase,
        "rater_slot": submission.rater_slot,
        "claim_count": len(submission.decisions),
        "decisions": tuple(item.model_dump(mode="json") for item in submission.decisions),
        "attestation": submission.attestation.model_dump(mode="json"),
    }
    return CompletedAnnotationPacket(
        workflow_sha256=workflow.workflow_sha256,
        validation_protocol_sha256=workflow.validation_protocol_sha256,
        source_packet_sha256=packet.packet_sha256,
        source_packet_id=packet.packet_id,
        phase=submission.phase,
        rater_slot=submission.rater_slot,
        claim_count=len(submission.decisions),
        decisions=submission.decisions,
        attestation=submission.attestation,
        completed_packet_sha256=canonical_sha256(payload),
    )


def _macro_f1(predicted: Sequence[SupportLabel], actual: Sequence[SupportLabel]) -> float:
    scores: list[float] = []
    for label in LABEL_ORDER:
        true_positive = sum(
            prediction == label and reference == label
            for prediction, reference in zip(predicted, actual, strict=True)
        )
        false_positive = sum(
            prediction == label and reference != label
            for prediction, reference in zip(predicted, actual, strict=True)
        )
        false_negative = sum(
            prediction != label and reference == label
            for prediction, reference in zip(predicted, actual, strict=True)
        )
        denominator = 2 * true_positive + false_positive + false_negative
        scores.append(0.0 if denominator == 0 else 2 * true_positive / denominator)
    return sum(scores) / len(scores)


def assess_onboarding(
    completed: CompletedAnnotationPacket,
    answer_key: OnboardingAnswerKey,
    workflow: HumanWorkflowProtocol,
) -> OnboardingAssessment:
    """Score synthetic qualification only; never enter a scientific denominator."""

    if completed.phase != "onboarding" or completed.claim_count != workflow.onboarding_case_count:
        _fail("onboarding assessment requires one complete 20-case onboarding packet")
    if completed.workflow_sha256 != workflow.workflow_sha256:
        _fail("completed onboarding packet uses a different workflow")
    if (
        answer_key.workflow_sha256 != workflow.workflow_sha256
        or answer_key.fixture_sha256 != workflow.onboarding_fixture_sha256
    ):
        _fail("onboarding answer key uses a different workflow or fixture")
    references = {item.blind_claim_id: item.reference_label for item in answer_key.entries}
    actual_ids = tuple(item.blind_claim_id for item in completed.decisions)
    if set(actual_ids) != set(references):
        _fail("completed onboarding claims differ from the answer key")
    predicted = [item.support_label for item in completed.decisions]
    actual = [references[item.blind_claim_id] for item in completed.decisions]
    macro_f1 = _macro_f1(predicted, actual)
    critical_count = sum(
        reference == "contradicted" and prediction in {"partially_supported", "fully_supported"}
        for prediction, reference in zip(predicted, actual, strict=True)
    )
    macro_passed = macro_f1 >= workflow.onboarding_minimum_macro_f1
    critical_passed = critical_count <= workflow.onboarding_maximum_critical_false_support_count
    payload = {
        "schema_version": ONBOARDING_ASSESSMENT_SCHEMA_VERSION,
        "workflow_sha256": workflow.workflow_sha256,
        "completed_packet_sha256": completed.completed_packet_sha256,
        "rater_slot": completed.rater_slot,
        "case_count": workflow.onboarding_case_count,
        "macro_f1": macro_f1,
        "critical_false_support_count": critical_count,
        "macro_f1_gate_passed": macro_passed,
        "critical_error_gate_passed": critical_passed,
        "status": "ready_for_main_annotation"
        if macro_passed and critical_passed
        else "retraining_required",
    }
    return OnboardingAssessment(
        workflow_sha256=workflow.workflow_sha256,
        completed_packet_sha256=completed.completed_packet_sha256,
        rater_slot=completed.rater_slot,
        macro_f1=macro_f1,
        critical_false_support_count=critical_count,
        macro_f1_gate_passed=macro_passed,
        critical_error_gate_passed=critical_passed,
        status=(
            "ready_for_main_annotation"
            if macro_passed and critical_passed
            else "retraining_required"
        ),
        assessment_sha256=canonical_sha256(payload),
    )


def validate_independent_pair(
    rater_1: CompletedAnnotationPacket,
    rater_2: CompletedAnnotationPacket,
    workflow: HumanWorkflowProtocol,
) -> IndependentPairReceipt:
    """Verify two locked packets before any evaluator mapping may be opened."""

    if rater_1.rater_slot != "rater_1" or rater_2.rater_slot != "rater_2":
        _fail("completed pair must preserve the two registered rater slots")
    if (
        rater_1.workflow_sha256 != workflow.workflow_sha256
        or rater_2.workflow_sha256 != workflow.workflow_sha256
    ):
        _fail("completed pair uses a different human workflow")
    if (
        rater_1.validation_protocol_sha256 != workflow.validation_protocol_sha256
        or rater_2.validation_protocol_sha256 != workflow.validation_protocol_sha256
    ):
        _fail("completed pair uses a different validation protocol")
    if (
        rater_1.source_packet_sha256 == rater_2.source_packet_sha256
        or rater_1.source_packet_id == rater_2.source_packet_id
        or rater_1.completed_packet_sha256 == rater_2.completed_packet_sha256
    ):
        _fail("completed pair must originate from two distinct rater packets")
    if rater_1.phase != rater_2.phase or rater_1.claim_count != rater_2.claim_count:
        _fail("completed pair phases or claim counts differ")
    first_ids = tuple(item.blind_claim_id for item in rater_1.decisions)
    second_ids = tuple(item.blind_claim_id for item in rater_2.decisions)
    if first_ids != second_ids:
        _fail("completed pair does not cover the same ordered blind claims")
    first = {item.blind_claim_id: item.support_label for item in rater_1.decisions}
    second = {item.blind_claim_id: item.support_label for item in rater_2.decisions}
    disagreements = {
        identifier for identifier in first_ids if first[identifier] != second[identifier]
    }
    contradictions = {
        identifier
        for identifier in first_ids
        if "contradicted" in {first[identifier], second[identifier]}
    }
    required = disagreements | contradictions
    payload = {
        "schema_version": PAIR_RECEIPT_SCHEMA_VERSION,
        "workflow_sha256": workflow.workflow_sha256,
        "phase": rater_1.phase,
        "claim_count": rater_1.claim_count,
        "rater_1_completed_sha256": rater_1.completed_packet_sha256,
        "rater_2_completed_sha256": rater_2.completed_packet_sha256,
        "shared_claim_set_sha256": canonical_sha256(first_ids),
        "disagreement_count": len(disagreements),
        "contradiction_trigger_count": len(contradictions),
        "adjudication_required_count": len(required),
        "status": (
            "completed_independent_pair_ready_for_adjudication"
            if required
            else "completed_independent_pair_no_adjudication_required"
        ),
    }
    return IndependentPairReceipt(
        workflow_sha256=workflow.workflow_sha256,
        phase=rater_1.phase,
        claim_count=rater_1.claim_count,
        rater_1_completed_sha256=rater_1.completed_packet_sha256,
        rater_2_completed_sha256=rater_2.completed_packet_sha256,
        shared_claim_set_sha256=canonical_sha256(first_ids),
        disagreement_count=len(disagreements),
        contradiction_trigger_count=len(contradictions),
        adjudication_required_count=len(required),
        status=(
            "completed_independent_pair_ready_for_adjudication"
            if required
            else "completed_independent_pair_no_adjudication_required"
        ),
        receipt_sha256=canonical_sha256(payload),
    )


def parse_submission(payload: Mapping[str, object]) -> HumanRaterSubmission:
    """Validate a decoded submission while presenting one stable domain error."""

    try:
        return HumanRaterSubmission.model_validate(dict(payload))
    except ValidationError as exc:
        raise HumanWorkflowError("rater submission is incomplete or invalid") from exc
