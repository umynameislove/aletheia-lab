"""Human-review infrastructure tests using synthetic reviewer decisions only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.binary_evaluation import ConfusionMatrix
from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import (
    ExecutedCandidate,
    FamilyCensusEntry,
    TechnicalDispositionEntry,
)
from aletheia_lab.benchmark.p2.evidence_conditions import (
    EvidenceConditionBundle,
    build_evidence_condition_bundles,
)
from aletheia_lab.benchmark.p2.evidence_projection import (
    CategoryShare,
    DataDriftDiagnosisEvidence,
    DiagnosisConfusionComparison,
    DiagnosisMetricSnapshot,
    DiagnosisPerformanceComparison,
    DistributionSnapshot,
    LabelDiagnosisEvidence,
    PerformanceEvidence,
    PreprocessingDiagnosisEvidence,
    SchemaComparison,
    SecondaryComparison,
    TargetProjectionComparison,
    TransformSignatureComparison,
)
from aletheia_lab.benchmark.p2.human_validity_review import (
    BlindReviewEntry,
    BlindReviewPacket,
    BlindStageDecisionForm,
    BlindStageWorksheet,
    HumanEvidenceRubric,
    HumanFamilyDecision,
    HumanFamilyDecisionForm,
    HumanReviewDecision,
    HumanReviewDecisionForm,
    HumanReviewRecord,
    HumanReviewWorksheet,
    HumanValidityReviewError,
    ReviewMappingEntry,
    ReviewMappingPacket,
    build_blind_stage_worksheet,
    build_human_review_packets,
    build_human_review_worksheet,
    evaluate_human_review,
    finalize_blind_stage_worksheet,
    finalize_human_review_worksheet,
    human_evidence_rubric_for,
    open_mapped_review_stage,
    select_review_bundles,
    validate_human_review,
    validate_review_packets,
)
from aletheia_lab.benchmark.p2.label_noise import (
    TargetDistributionComparison,
    TargetQualityAudit,
    wilson_interval,
)
from aletheia_lab.benchmark.p2.mechanism_validation import ValidatedMechanismCandidate

_HEX = {letter: letter * 64 for letter in "abcdef"}
_SLOT = {
    "data_drift": "M1-F1",
    "label_noise": "M2-F1",
    "preprocessing_bug": "M3-F1",
}


def _candidate(fault_type: str, marker: str | None = None) -> ValidatedMechanismCandidate:
    index = ("data_drift", "label_noise", "preprocessing_bug").index(fault_type)
    fingerprint_marker = marker or "abc"[index]
    candidate_marker = marker or "def"[index]
    fingerprint = fingerprint_marker * 64
    candidate_id = f"p2-candidate-{candidate_marker * 64}"
    execution = ExecutedCandidate(
        candidate_id=candidate_id,
        slot_id=_SLOT[fault_type],  # type: ignore[arg-type]
        fault_type=fault_type,  # type: ignore[arg-type]
        role="fault_directed",
        slot_kind="primary",
        proposed_family_sha256=fingerprint,
        dataset_sha256=_HEX["e"],
        model_data_split_manifest_sha256=_HEX["f"],
    )
    return ValidatedMechanismCandidate(
        candidate_id=candidate_id,
        slot_id=_SLOT[fault_type],  # type: ignore[arg-type]
        fault_type=fault_type,  # type: ignore[arg-type]
        proposed_family_sha256=fingerprint,
        slot_sha256=_HEX["a"],
        artifact_sha256=_HEX["b"],
        execution=execution,
        disposition=TechnicalDispositionEntry(
            candidate_id=candidate_id,
            disposition="technically_valid",
        ),
    )


def _family(candidate: ValidatedMechanismCandidate) -> FamilyCensusEntry:
    return FamilyCensusEntry(
        case_family_id=f"p2-family-{candidate.proposed_family_sha256}",
        candidate_id=candidate.candidate_id,
        fault_type=candidate.fault_type,
        family_class="eligible_failure",
        proposed_family_sha256=candidate.proposed_family_sha256,
    )


def _snapshot(confusion: ConfusionMatrix) -> DiagnosisMetricSnapshot:
    return DiagnosisMetricSnapshot(
        accuracy=confusion.accuracy(),
        macro_f1=confusion.macro_f1(),
        minority_recall=confusion.minority_recall(),
    )


def _performance() -> PerformanceEvidence:
    reference = ConfusionMatrix(
        true_negative=70, false_positive=10, false_negative=10, true_positive=10
    )
    observed = ConfusionMatrix(
        true_negative=60, false_positive=20, false_negative=15, true_positive=5
    )
    reference_rates = _snapshot(reference)
    observed_rates = _snapshot(observed)
    return PerformanceEvidence(
        performance_comparison=DiagnosisPerformanceComparison(
            reference_sample_size=100,
            observed_sample_size=100,
            reference=reference_rates,
            observed=observed_rates,
            accuracy_delta=observed_rates.accuracy - reference_rates.accuracy,
            macro_f1_delta=observed_rates.macro_f1 - reference_rates.macro_f1,
            minority_recall_delta=(
                observed_rates.minority_recall - reference_rates.minority_recall
            ),
        ),
        confusion_comparison=DiagnosisConfusionComparison(
            reference=reference,
            observed=observed,
        ),
    )


def _secondary() -> SecondaryComparison:
    return SecondaryComparison(
        reference_value=0.4,
        observed_value=0.405,
        absolute_delta=0.005,
        stability_bound=0.01,
    )


def _evidence(fault_type: str) -> object:
    if fault_type == "data_drift":
        return DataDriftDiagnosisEvidence(
            performance=_performance(),
            reference_distribution=DistributionSnapshot(
                sample_size=100,
                categories=(
                    CategoryShare(category="annual", proportion=0.4),
                    CategoryShare(category="monthly", proportion=0.6),
                ),
            ),
            observed_distribution=DistributionSnapshot(
                sample_size=100,
                categories=(
                    CategoryShare(category="annual", proportion=0.2),
                    CategoryShare(category="monthly", proportion=0.8),
                ),
            ),
            population_stability_index=0.19,
            secondary_comparison=_secondary(),
        )
    if fault_type == "label_noise":
        lower, upper = wilson_interval(successes=10, trials=100)
        return LabelDiagnosisEvidence(
            performance=_performance(),
            target_distribution_comparison=TargetDistributionComparison(
                reference_positive_count=50,
                reference_negative_count=50,
                observed_positive_count=48,
                observed_negative_count=52,
            ),
            target_quality_audit_summary=TargetQualityAudit(
                schema_version="p2-target-quality-audit/v1",
                audited_record_count=100,
                disagreeing_record_count=10,
                disagreement_rate=0.1,
                disagreement_rate_lower_bound=lower,
                disagreement_rate_upper_bound=upper,
                interval_method="wilson-score/95",
                protocol_version="target-quality-audit/v1",
            ),
            secondary_comparison=_secondary(),
        )
    return PreprocessingDiagnosisEvidence(
        performance=_performance(),
        transform_signature_comparison=TransformSignatureComparison(
            reference_signature_sha256=_HEX["a"],
            observed_signature_sha256=_HEX["b"],
            signatures_equal=False,
        ),
        target_projection_comparison=TargetProjectionComparison(
            sample_size=100,
            differing_record_count=20,
            difference_rate=0.2,
            reference_projection_sha256=_HEX["c"],
            observed_projection_sha256=_HEX["d"],
        ),
        schema_comparison=SchemaComparison(
            reference_field_count=24,
            observed_field_count=24,
            field_sets_equal=True,
        ),
        secondary_comparison=_secondary(),
    )


def _family_bundles(fault_type: str, marker: str | None = None) -> tuple[EvidenceConditionBundle, ...]:
    candidate = _candidate(fault_type, marker)
    return build_evidence_condition_bundles(
        candidate=candidate,
        family=_family(candidate),
        evidence=_evidence(fault_type),  # type: ignore[arg-type]
    )


def _census() -> tuple[EvidenceConditionBundle, ...]:
    return tuple(
        bundle
        for fault_type in ("data_drift", "label_noise", "preprocessing_bug")
        for bundle in _family_bundles(fault_type)
    )


def _packets() -> tuple[BlindReviewPacket, ReviewMappingPacket]:
    return build_human_review_packets(_census())


def _passing_record(
    blind: BlindReviewPacket,
    mapping: ReviewMappingPacket,
) -> HumanReviewRecord:
    decisions = tuple(
        HumanReviewDecision(
            review_id=entry.review_id,
            diagnosis_projection_sha256=entry.diagnosis_projection_sha256,
            hidden_answer_cue_found="no",
            expected_judgment_cue_found="no",
            unsupported_causal_wording_found="no",
            observed_sufficiency=entry.rubric.expected_sufficiency,
            maximum_supported_claim=entry.rubric.maximum_supported_claim,
            allowed_claims_match="yes",
            forbidden_claims_enforced="yes",
            threshold_or_guardrail_threats=(),
            protocol_deviations=(),
            rationale="The visible evidence and claim boundary match the mapped rubric.",
        )
        for entry in mapping.entries
    )
    family_ids = sorted({entry.family_review_id for entry in mapping.entries})
    family_decisions = tuple(
        HumanFamilyDecision(
            family_review_id=family_id,
            family_binding_preserved="yes",
            core_observations_preserved="yes",
            missing_key_withholds_decisive_evidence="yes",
            noisy_adds_only_neutral_secondary_evidence="yes",
            noisy_secondary_not_marked_as_distractor="yes",
            threats=(),
            protocol_deviations=(),
            rationale="All three siblings preserve the intended paired evidence construction.",
        )
        for family_id in family_ids
    )
    return HumanReviewRecord(
        reviewer_kind="human",
        reviewer_id="synthetic-test-reviewer",
        blind_stage_completed_before_mapping_opened=True,
        judgments_personally_recorded=True,
        blind_packet_sha256=blind.canonical_sha256(),
        mapping_packet_sha256=mapping.canonical_sha256(),
        decisions=decisions,
        family_decisions=family_decisions,
    )


def test_condition_rubrics_freeze_claim_strength_and_guardrails() -> None:
    full = human_evidence_rubric_for("full")
    missing = human_evidence_rubric_for("missing_key")
    noisy = human_evidence_rubric_for("noisy")

    assert full.maximum_supported_claim == "bounded_causal_hypothesis"
    assert missing.maximum_supported_claim == "bounded_tentative_hypothesis"
    assert missing.expected_sufficiency == "bounded_hypothesis_tentative_only"
    assert "explicit_uncertainty" in missing.required_guardrails
    assert "reject_unsupported_secondary_cause" in noisy.required_guardrails
    for rubric in (full, missing, noisy):
        assert "causal_conclusion" in rubric.forbidden_claims
        assert "strong_causal_conclusion" in rubric.forbidden_claims


def test_rubric_rejects_tampered_condition_semantics() -> None:
    payload = human_evidence_rubric_for("missing_key").model_dump()
    payload["expected_sufficiency"] = "bounded_hypothesis_supported"
    with pytest.raises(ValidationError, match="tentative-only"):
        HumanEvidenceRubric.model_validate(payload)


def test_sampling_selects_complete_mechanism_condition_matrix() -> None:
    selected = select_review_bundles(_census())
    assert len(selected) == 9
    assert {bundle.fault_type for bundle in selected} == {
        "data_drift",
        "label_noise",
        "preprocessing_bug",
    }
    assert {bundle.evidence_condition for bundle in selected} == {
        "full",
        "missing_key",
        "noisy",
    }
    assert len({bundle.case_family_id for bundle in selected}) == 3


def test_sampling_is_input_order_independent_and_uses_stable_tie_breaking() -> None:
    census = (*_census(), *_family_bundles("data_drift", "f"))
    forward = select_review_bundles(tuple(census))
    reverse = select_review_bundles(tuple(reversed(census)))
    assert forward == reverse


@pytest.mark.parametrize("omitted", ["data_drift", "label_noise", "preprocessing_bug"])
def test_sampling_requires_every_mechanism(omitted: str) -> None:
    census = tuple(bundle for bundle in _census() if bundle.fault_type != omitted)
    with pytest.raises(HumanValidityReviewError, match="no complete eligible family"):
        select_review_bundles(census)


def test_incomplete_family_is_not_eligible_for_sampling() -> None:
    census = tuple(
        bundle
        for bundle in _census()
        if not (bundle.fault_type == "data_drift" and bundle.evidence_condition == "noisy")
    )
    with pytest.raises(HumanValidityReviewError, match="no complete eligible family"):
        select_review_bundles(census)


def test_blind_packet_contains_no_evaluator_mapping_metadata() -> None:
    blind, _ = _packets()
    serialized = json.dumps(blind.model_dump(mode="json"), sort_keys=True)
    for forbidden in (
        "case_family_id",
        "candidate_id",
        "fault_type",
        "evidence_condition",
        "expected_sufficiency",
        "allowed_claims",
        "forbidden_claims",
        "family_review_id",
    ):
        assert forbidden not in serialized


def test_packets_are_bound_and_cover_three_by_three_matrix() -> None:
    blind, mapping = _packets()
    validate_review_packets(blind, mapping)
    assert mapping.blind_packet_sha256 == blind.canonical_sha256()
    assert len(mapping.entries) == len(blind.entries) == 9
    assert len({entry.family_review_id for entry in mapping.entries}) == 3


def test_worksheet_is_bound_incomplete_and_contains_no_expected_answers() -> None:
    blind, mapping = _packets()
    worksheet = build_human_review_worksheet(blind, mapping)
    assert worksheet.reviewer_id is None
    assert not worksheet.blind_stage_completed_before_mapping_opened
    assert all(form.hidden_answer_cue_found is None for form in worksheet.decisions)
    serialized = worksheet.model_dump_json()
    assert "bounded_hypothesis_supported" not in serialized
    assert "causal_conclusion" not in serialized
    with pytest.raises(HumanValidityReviewError, match="reviewer ID"):
        finalize_human_review_worksheet(worksheet)


def test_two_stage_workflow_freezes_blind_answers_before_mapping() -> None:
    blind, mapping = _packets()
    worksheet = build_blind_stage_worksheet(blind)
    assert worksheet.reviewer_id is None
    serialized = worksheet.model_dump_json()
    for forbidden in ("fault_type", "evidence_condition", "rubric", "case_family_id"):
        assert forbidden not in serialized

    completed = worksheet.model_copy(
        update={
            "reviewer_id": "reviewer-a",
            "judgments_personally_recorded": True,
            "mapping_packet_not_opened": True,
            "decisions": tuple(
                BlindStageDecisionForm(
                    review_id=form.review_id,
                    diagnosis_projection_sha256=form.diagnosis_projection_sha256,
                    hidden_answer_cue_found="no",
                    expected_judgment_cue_found="no",
                    unsupported_causal_wording_found="no",
                    rationale="Visible evidence contains measurements without evaluator labels.",
                )
                for form in worksheet.decisions
            ),
        }
    )
    record = finalize_blind_stage_worksheet(blind, completed)
    mapped = open_mapped_review_stage(blind, mapping, record)
    assert mapped.reviewer_id == "reviewer-a"
    assert mapped.blind_stage_completed_before_mapping_opened
    assert all(form.hidden_answer_cue_found == "no" for form in mapped.decisions)
    assert all(form.observed_sufficiency is None for form in mapped.decisions)


def test_blind_stage_rejects_incomplete_or_post_mapping_attestation() -> None:
    blind, _ = _packets()
    worksheet = build_blind_stage_worksheet(blind).model_copy(
        update={"reviewer_id": "reviewer-a", "judgments_personally_recorded": True}
    )
    with pytest.raises(HumanValidityReviewError, match="mapping packet was not opened"):
        finalize_blind_stage_worksheet(blind, worksheet)

    completed_forms = list(worksheet.decisions)
    completed_forms[0] = completed_forms[0].model_copy(
        update={
            "hidden_answer_cue_found": "no",
            "expected_judgment_cue_found": "no",
            "unsupported_causal_wording_found": "no",
            "rationale": "This entry was reviewed only from its visible projection.",
        }
    )
    incomplete = BlindStageWorksheet.model_validate(
        {
            **worksheet.model_dump(),
            "mapping_packet_not_opened": True,
            "decisions": tuple(completed_forms),
        }
    )
    with pytest.raises(HumanValidityReviewError, match="entry is incomplete"):
        finalize_blind_stage_worksheet(blind, incomplete)


def test_complete_worksheet_finalizes_to_the_same_strict_record() -> None:
    blind, mapping = _packets()
    expected = _passing_record(blind, mapping)
    worksheet = build_human_review_worksheet(blind, mapping).model_copy(
        update={
            "reviewer_id": expected.reviewer_id,
            "blind_stage_completed_before_mapping_opened": True,
            "judgments_personally_recorded": True,
            "decisions": tuple(
                HumanReviewDecisionForm(**decision.model_dump())
                for decision in expected.decisions
            ),
            "family_decisions": tuple(
                HumanFamilyDecisionForm(**decision.model_dump())
                for decision in expected.family_decisions
            ),
        }
    )
    finalized = finalize_human_review_worksheet(worksheet)
    assert finalized == expected
    assert validate_human_review(blind, mapping, finalized).status == "pass"


def test_worksheet_rejects_omitted_and_duplicated_forms() -> None:
    blind, mapping = _packets()
    worksheet = build_human_review_worksheet(blind, mapping)
    with pytest.raises(ValidationError, match="nine unique"):
        HumanReviewWorksheet.model_validate(
            {**worksheet.model_dump(), "decisions": worksheet.decisions[:-1]}
        )
    with pytest.raises(ValidationError, match="nine unique"):
        HumanReviewWorksheet.model_validate(
            {
                **worksheet.model_dump(),
                "decisions": (*worksheet.decisions[:-1], worksheet.decisions[0]),
            }
        )


def test_packet_builder_is_deterministic() -> None:
    first = build_human_review_packets(_census())
    second = build_human_review_packets(tuple(reversed(_census())))
    assert first == second
    assert first[0].canonical_sha256() == second[0].canonical_sha256()
    assert first[1].canonical_sha256() == second[1].canonical_sha256()


def test_packet_validator_rejects_blind_projection_tamper() -> None:
    blind, mapping = _packets()
    entry = blind.entries[0]
    projection = dict(entry.diagnosis_projection)
    projection["unexpected"] = "visible tamper"
    forged_entry = BlindReviewEntry(
        review_id=entry.review_id,
        diagnosis_projection=projection,
        diagnosis_projection_sha256=canonical_sha256(projection),
    )
    forged = BlindReviewPacket(
        instructions=blind.instructions,
        entries=tuple(sorted((forged_entry, *blind.entries[1:]), key=lambda item: item.review_id)),
    )
    with pytest.raises(HumanValidityReviewError, match="not bound"):
        validate_review_packets(forged, mapping)


def test_mapping_rejects_rubric_and_binding_tamper() -> None:
    _, mapping = _packets()
    entry = mapping.entries[0]
    with pytest.raises(ValidationError, match="frozen condition rubric"):
        ReviewMappingEntry.model_validate(
            {**entry.model_dump(), "rubric": human_evidence_rubric_for("missing_key")}
        )
    with pytest.raises(ValidationError, match="binding mismatch"):
        ReviewMappingEntry.model_validate({**entry.model_dump(), "binding_sha256": _HEX["a"]})


def test_synthetic_complete_record_derives_passing_report() -> None:
    blind, mapping = _packets()
    report = validate_human_review(blind, mapping, _passing_record(blind, mapping))
    assert report.status == "pass"
    assert report.reviewed_entries == 9
    assert report.reviewed_families == 3
    assert report.findings == ()


@pytest.mark.parametrize(
    ("update", "kind"),
    [
        ({"hidden_answer_cue_found": "yes"}, "leakage"),
        ({"expected_judgment_cue_found": "uncertain"}, "uncertain_judgment"),
        ({"unsupported_causal_wording_found": "yes"}, "claim_boundary"),
        ({"observed_sufficiency": "cannot_assess"}, "uncertain_judgment"),
        ({"maximum_supported_claim": "causal_conclusion"}, "validation"),
        ({"allowed_claims_match": "no"}, "claim_boundary"),
        ({"forbidden_claims_enforced": "no"}, "claim_boundary"),
    ],
)
def test_entry_blockers_fail_closed(update: dict[str, object], kind: str) -> None:
    blind, mapping = _packets()
    record = _passing_record(blind, mapping)
    payload = record.decisions[0].model_dump()
    payload.update(update)
    if kind == "validation":
        with pytest.raises(ValidationError):
            HumanReviewDecision.model_validate(payload)
        return
    decision = HumanReviewDecision.model_validate(payload)
    record = record.model_copy(update={"decisions": (decision, *record.decisions[1:])})
    report = evaluate_human_review(blind, mapping, record)
    assert report.status == "blocked"
    assert kind in {finding.kind for finding in report.findings}
    with pytest.raises(HumanValidityReviewError, match="blocking finding"):
        validate_human_review(blind, mapping, record)


def test_family_pairing_failure_blocks_review() -> None:
    blind, mapping = _packets()
    record = _passing_record(blind, mapping)
    first = record.family_decisions[0].model_copy(
        update={"noisy_adds_only_neutral_secondary_evidence": "no"}
    )
    record = record.model_copy(update={"family_decisions": (first, *record.family_decisions[1:])})
    report = evaluate_human_review(blind, mapping, record)
    assert report.status == "blocked"
    assert "family_pairing" in {finding.kind for finding in report.findings}


def test_threats_and_deviations_are_preserved_without_being_silently_rewritten() -> None:
    blind, mapping = _packets()
    record = _passing_record(blind, mapping)
    first = record.decisions[0].model_copy(
        update={
            "threshold_or_guardrail_threats": ("accuracy_threshold_construct",),
            "protocol_deviations": ("Threshold requires a separate construct rationale.",),
        }
    )
    record = record.model_copy(update={"decisions": (first, *record.decisions[1:])})
    report = evaluate_human_review(blind, mapping, record)
    assert report.status == "pass"
    assert record.decisions[0].threshold_or_guardrail_threats == (
        "accuracy_threshold_construct",
    )


def test_review_record_rejects_packet_replay_and_projection_rebinding() -> None:
    blind, mapping = _packets()
    record = _passing_record(blind, mapping)
    with pytest.raises(HumanValidityReviewError, match="blind packet"):
        evaluate_human_review(
            blind,
            mapping,
            record.model_copy(update={"blind_packet_sha256": _HEX["a"]}),
        )
    decision = record.decisions[0].model_copy(update={"diagnosis_projection_sha256": _HEX["a"]})
    forged = record.model_copy(update={"decisions": (decision, *record.decisions[1:])})
    with pytest.raises(HumanValidityReviewError, match="different diagnosis projection"):
        evaluate_human_review(blind, mapping, forged)


_REPRODUCTION_SCRIPT = """
import json
import sys
from aletheia_lab.benchmark.p2.evidence_conditions import EvidenceConditionBundle
from aletheia_lab.benchmark.p2.human_validity_review import build_human_review_packets
bundles = tuple(EvidenceConditionBundle.model_validate(item) for item in json.loads(sys.argv[1]))
blind, mapping = build_human_review_packets(bundles)
sys.stdout.write(json.dumps({
    "blind": blind.model_dump(mode="json"),
    "mapping": mapping.model_dump(mode="json"),
}, sort_keys=True))
"""


def _subprocess_packets(seed: str, bundles: tuple[EvidenceConditionBundle, ...]) -> str:
    repo_root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = seed
    environment["PYTHONPATH"] = str(repo_root / "src")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    payload = json.dumps([bundle.model_dump(mode="json") for bundle in bundles])
    return subprocess.run(
        [sys.executable, "-c", _REPRODUCTION_SCRIPT, payload],
        check=True,
        capture_output=True,
        text=True,
        cwd=repo_root,
        env=environment,
    ).stdout


def test_packets_are_byte_identical_across_hash_seeds() -> None:
    bundles = _census()
    assert _subprocess_packets("1", bundles) == _subprocess_packets("999", bundles)
