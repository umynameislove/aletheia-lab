from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
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
V2_WORKFLOW_PATH = Path("configs/evaluation/claim_support_human_workflow_v2.json")


def _materials(
    workflow_path: Path = WORKFLOW_PATH,
) -> tuple[
    HumanWorkflowProtocol,
    BlindAnnotationPacket,
    BlindAnnotationPacket,
    OnboardingAnswerKey,
]:
    workflow = load_human_workflow(ROOT, workflow_path)
    fixture = load_onboarding_fixture(ROOT / workflow.onboarding_fixture_path)
    protocol = load_validation_protocol(ROOT / workflow.validation_protocol_path)
    first, second, key = prepare_onboarding_materials(fixture, protocol, workflow)
    return workflow, first, second, key


def test_v1_historical_inputs_remain_byte_frozen() -> None:
    expected = {
        "configs/evaluation/claim_support_human_workflow.json": (
            "65181d8c0f9a73e270e57c0d5cfd45ee1c6385a6b350c9b422ecdd0412a2e824"
        ),
        "configs/evaluation/claim_support_onboarding_fixture.json": (
            "e0dfb039f2eed7d8c434ef1d7bf90d30c7dad19a3b7c2c74707d8784a153b5fc"
        ),
        "docs/claim-support-rater-guide.md": (
            "38d8d7f0e419078d6ff4542f778c4267897112bf3162da540c6277e569604064"
        ),
    }

    for relative_path, expected_sha256 in expected.items():
        assert hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest() == expected_sha256

    _, first, second, answer_key = _materials()
    assert first.packet_sha256 == (
        "8f4ad6d588bf1d949de41f05839bd264f6aa9f682870c8ff9a5330852caad231"
    )
    assert second.packet_sha256 == (
        "f2c8317f12ef6d31f3890be1f6ac5a5f566a8314d7e213d012a3ffbc7d6d0d2d"
    )
    assert answer_key.answer_key_sha256 == (
        "95e6dea8aaff18fef4c59705650542c4ad6c3a4883cfb5ef78fac40daa3a70f0"
    )


def test_v2_fixture_is_fresh_balanced_and_bound_to_clarified_semantics() -> None:
    workflow, first, second, key = _materials(V2_WORKFLOW_PATH)
    fixture = load_onboarding_fixture(ROOT / workflow.onboarding_fixture_path)
    v1 = load_onboarding_fixture(ROOT / "configs/evaluation/claim_support_onboarding_fixture.json")

    assert workflow.workflow_id == "claim-support-independent-human-workflow-v2"
    assert fixture.fixture_id == "claim-support-synthetic-onboarding-v2"
    assert Counter(item.reference_label for item in fixture.cases) == Counter(
        {
            label: 5
            for label in ("contradicted", "unsupported", "partially_supported", "fully_supported")
        }
    )
    assert {item.claim_text for item in fixture.cases}.isdisjoint(
        item.claim_text for item in v1.cases
    )
    assert {
        evidence.excerpt for item in fixture.cases for evidence in item.visible_evidence
    }.isdisjoint(evidence.excerpt for item in v1.cases for evidence in item.visible_evidence)
    assert all(item.case_id.startswith("onboarding-v2-case-") for item in fixture.cases)
    assert all(
        evidence.evidence_id.startswith("training-v2-evidence-")
        for item in fixture.cases
        for evidence in item.visible_evidence
    )
    assert first.claims == second.claims
    assert first.packet_sha256 != second.packet_sha256
    assert key.workflow_sha256 == workflow.workflow_sha256


def test_v2_material_conflict_and_missing_evidence_controls_are_unambiguous() -> None:
    workflow = load_human_workflow(ROOT, V2_WORKFLOW_PATH)
    fixture = load_onboarding_fixture(ROOT / workflow.onboarding_fixture_path)
    labels = {item.case_id: item.reference_label for item in fixture.cases}

    assert {labels[f"onboarding-v2-case-{index:02d}"] for index in range(1, 6)} == {"contradicted"}
    assert {labels[f"onboarding-v2-case-{index:02d}"] for index in range(6, 11)} == {"unsupported"}
    assert {labels[f"onboarding-v2-case-{index:02d}"] for index in range(11, 16)} == {
        "partially_supported"
    }
    assert {labels[f"onboarding-v2-case-{index:02d}"] for index in range(16, 21)} == {
        "fully_supported"
    }
    partial_notes = [item.teaching_note for item in fixture.cases[10:15]]
    assert all("unestablish" in note or "unmeasured" in note for note in partial_notes)


def test_workflow_cannot_claim_v2_while_binding_the_v1_fixture(tmp_path: Path) -> None:
    payload = json.loads((ROOT / WORKFLOW_PATH).read_text(encoding="utf-8"))
    payload["workflow_id"] = "claim-support-independent-human-workflow-v2"
    payload["workflow_sha256"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "workflow_sha256"}
    )
    path = tmp_path / "mixed-workflow.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(HumanWorkflowError, match="mismatched guide or fixture paths"):
        load_human_workflow(ROOT, path)


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


def test_submission_requires_citation_for_every_non_unsupported_decision() -> None:
    workflow, first, _, key = _materials(V2_WORKFLOW_PATH)
    valid = _submission(first, key, workflow)
    index = next(
        index
        for index, decision in enumerate(valid.decisions)
        if decision.support_label != "unsupported"
    )
    missing_citation = valid.decisions[index].model_copy(update={"evidence_ids_used": ()})
    decisions = list(valid.decisions)
    decisions[index] = missing_citation
    invalid = valid.model_copy(update={"decisions": tuple(decisions)})

    with pytest.raises(HumanWorkflowError, match="must cite visible evidence"):
        lock_completed_packet(first, invalid, workflow)


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
