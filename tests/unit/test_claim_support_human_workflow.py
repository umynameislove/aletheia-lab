from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.evaluation.human_workflow import (
    CompletedAnnotationDecision,
    CompletedAnnotationPacket,
    HumanRaterAttestation,
    HumanRaterSubmission,
    HumanWorkflowError,
    HumanWorkflowProtocol,
    OnboardingAnswerKey,
    assess_onboarding,
    load_human_workflow,
    load_onboarding_fixture,
    lock_completed_packet,
    parse_submission,
    prepare_onboarding_materials,
    submission_template,
    validate_independent_pair,
)
from aletheia_lab.evaluation.instrument_validation import (
    BlindAnnotationPacket,
    SupportLabel,
    load_validation_protocol,
)

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = Path("configs/evaluation/claim_support_human_workflow.json")


def _materials() -> tuple[
    HumanWorkflowProtocol,
    BlindAnnotationPacket,
    BlindAnnotationPacket,
    OnboardingAnswerKey,
]:
    workflow = load_human_workflow(ROOT, WORKFLOW_PATH)
    fixture = load_onboarding_fixture(ROOT / workflow.onboarding_fixture_path)
    protocol = load_validation_protocol(ROOT / workflow.validation_protocol_path)
    first, second, key = prepare_onboarding_materials(fixture, protocol, workflow)
    return workflow, first, second, key


def _submission(
    packet: BlindAnnotationPacket,
    key: OnboardingAnswerKey,
    workflow: HumanWorkflowProtocol,
    *,
    override: tuple[str, SupportLabel] | None = None,
) -> HumanRaterSubmission:
    references = {item.blind_claim_id: item.reference_label for item in key.entries}
    decisions = []
    for claim in packet.claims:
        label = references[claim.blind_claim_id]
        if override is not None and claim.blind_claim_id == override[0]:
            label = override[1]
        evidence_ids = () if label == "unsupported" else (claim.visible_evidence[0].evidence_id,)
        decisions.append(
            CompletedAnnotationDecision(
                blind_claim_id=claim.blind_claim_id,
                support_label=label,
                evidence_ids_used=evidence_ids,
                rationale="The visible excerpt establishes the material support boundary.",
            )
        )
    return HumanRaterSubmission(
        workflow_sha256=workflow.workflow_sha256,
        source_packet_sha256=packet.packet_sha256,
        phase="onboarding",
        rater_slot=packet.rater_slot,
        decisions=tuple(decisions),
        attestation=HumanRaterAttestation(
            completed_by_human=True,
            worked_independently=True,
            model_assistance_used=False,
            rubric_read_before_rating=True,
        ),
    )


def test_tracked_workflow_is_bound_balanced_and_outcome_free() -> None:
    workflow, first, second, key = _materials()

    assert workflow.human_annotations_collected is False
    assert workflow.main_or_sealed_outcomes_forbidden is True
    assert len(first.claims) == len(second.claims) == len(key.entries) == 20
    assert first.claims == second.claims
    assert first.packet_sha256 != second.packet_sha256
    assert first.rater_slot == "rater_1"
    assert second.rater_slot == "rater_2"


def test_blind_material_and_editable_template_withhold_private_fields() -> None:
    workflow, first, _, _ = _materials()
    template = submission_template(first, workflow, phase="onboarding")
    serialized = json.dumps(
        {"packet": first.model_dump(mode="json"), "template": template},
        sort_keys=True,
    )

    for forbidden in (
        "reference_label",
        "teaching_note",
        "automatic_label",
        "claim_type",
        "case_family_id",
        "hidden_ground_truth",
    ):
        assert forbidden not in serialized
    assert template["decisions"][0]["support_label"] is None
    assert template["attestation"]["completed_by_human"] is False


def test_two_complete_packets_lock_assess_and_reconcile_independently() -> None:
    workflow, first, second, key = _materials()
    completed_1 = lock_completed_packet(first, _submission(first, key, workflow), workflow)
    completed_2 = lock_completed_packet(second, _submission(second, key, workflow), workflow)

    assessment_1 = assess_onboarding(completed_1, key, workflow)
    assessment_2 = assess_onboarding(completed_2, key, workflow)
    pair = validate_independent_pair(completed_1, completed_2, workflow)

    assert assessment_1.status == assessment_2.status == "ready_for_main_annotation"
    assert assessment_1.macro_f1 == assessment_2.macro_f1 == pytest.approx(1.0)
    assert assessment_1.critical_false_support_count == 0
    assert pair.disagreement_count == 0
    assert pair.contradiction_trigger_count == 5
    assert pair.adjudication_required_count == 5
    assert completed_1.completed_packet_sha256 != completed_2.completed_packet_sha256


def test_critical_false_support_blocks_onboarding_even_when_other_items_are_correct() -> None:
    workflow, first, _, key = _materials()
    contradicted_id = next(
        item.blind_claim_id for item in key.entries if item.reference_label == "contradicted"
    )
    completed = lock_completed_packet(
        first,
        _submission(first, key, workflow, override=(contradicted_id, "fully_supported")),
        workflow,
    )
    assessment = assess_onboarding(completed, key, workflow)

    assert assessment.critical_false_support_count == 1
    assert assessment.status == "retraining_required"


def test_submission_must_cover_exact_packet_order_and_visible_evidence() -> None:
    workflow, first, _, key = _materials()
    valid = _submission(first, key, workflow)
    incomplete = HumanRaterSubmission(
        workflow_sha256=valid.workflow_sha256,
        source_packet_sha256=valid.source_packet_sha256,
        phase=valid.phase,
        rater_slot=valid.rater_slot,
        decisions=valid.decisions[:-1],
        attestation=valid.attestation,
    )
    with pytest.raises(HumanWorkflowError, match="exactly and in order"):
        lock_completed_packet(first, incomplete, workflow)

    forged_decision = valid.decisions[0].model_copy(
        update={"evidence_ids_used": ("evidence-not-visible",)}
    )
    forged = HumanRaterSubmission(
        workflow_sha256=valid.workflow_sha256,
        source_packet_sha256=valid.source_packet_sha256,
        phase=valid.phase,
        rater_slot=valid.rater_slot,
        decisions=(forged_decision, *valid.decisions[1:]),
        attestation=valid.attestation,
    )
    with pytest.raises(HumanWorkflowError, match="absent"):
        lock_completed_packet(first, forged, workflow)


def test_packet_binding_and_human_attestations_fail_closed() -> None:
    workflow, first, second, key = _materials()
    wrong_packet = _submission(first, key, workflow).model_copy(
        update={"source_packet_sha256": second.packet_sha256}
    )
    with pytest.raises(HumanWorkflowError, match="not bound"):
        lock_completed_packet(first, wrong_packet, workflow)

    raw = submission_template(first, workflow, phase="onboarding")
    with pytest.raises(HumanWorkflowError, match="incomplete or invalid"):
        parse_submission(raw)


def test_completed_packet_detects_post_lock_tampering() -> None:
    workflow, first, _, key = _materials()
    completed = lock_completed_packet(first, _submission(first, key, workflow), workflow)
    payload = completed.model_dump(mode="json")
    payload["decisions"][0]["support_label"] = "unsupported"

    with pytest.raises(ValidationError, match="completed packet hash"):
        CompletedAnnotationPacket.model_validate_json(json.dumps(payload))


def test_main_phase_rejects_an_onboarding_sized_packet() -> None:
    workflow, first, _, key = _materials()
    submission = _submission(first, key, workflow).model_copy(update={"phase": "main"})

    with pytest.raises(HumanWorkflowError, match="claim census"):
        lock_completed_packet(first, submission, workflow)


def test_answer_key_and_pair_cannot_cross_identity_boundaries() -> None:
    workflow, first, second, key = _materials()
    completed_1 = lock_completed_packet(first, _submission(first, key, workflow), workflow)
    completed_2 = lock_completed_packet(second, _submission(second, key, workflow), workflow)

    wrong_key = key.model_copy(update={"workflow_sha256": "f" * 64})
    with pytest.raises(HumanWorkflowError, match="different workflow or fixture"):
        assess_onboarding(completed_1, wrong_key, workflow)

    reused_source = completed_2.model_copy(
        update={
            "source_packet_sha256": completed_1.source_packet_sha256,
            "source_packet_id": completed_1.source_packet_id,
        }
    )
    with pytest.raises(HumanWorkflowError, match="two distinct rater packets"):
        validate_independent_pair(completed_1, reused_source, workflow)
