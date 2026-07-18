"""Evaluator-layer and paired-sensitivity regression tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from aletheia_lab.benchmark.case_writer import load_case_dir_schema_only
from aletheia_lab.benchmark.generator import generate_p1
from aletheia_lab.diagnosis.adapters import DeterministicMockAdapter
from aletheia_lab.diagnosis.pilot import run_p1_matched_pilot
from aletheia_lab.diagnosis.schema import DiagnosisOutput
from aletheia_lab.evaluation.pilot import (
    evaluate_behavior,
    evaluate_citations,
    evaluate_correctness,
    evaluate_matched_pilot,
    evaluate_missing_key_sensitivity,
    evaluate_support,
    write_evaluation_report,
)
from aletheia_lab.evidence.p1 import generate_p1_evidence_store
from aletheia_lab.evidence.schema import EvidenceBundle
from aletheia_lab.evidence.store import load_bundle_store


@pytest.fixture
def evaluated_pilot(
    p1_generator_config: Path, tmp_path: Path
) -> tuple[Path, Path, Path, Path]:
    cases = tmp_path / "cases"
    store = tmp_path / "evidence-store"
    pilot = tmp_path / "pilot"
    generate_p1(p1_generator_config, cases)
    generate_p1_evidence_store(cases, store)
    run_p1_matched_pilot(store, pilot, adapter=DeterministicMockAdapter())
    return cases, store, pilot, tmp_path


def _bundle_and_case(
    cases: Path, store: Path, condition: str, *, eligible: bool | None = None
):
    for bundle in load_bundle_store(store).bundles:
        if bundle.evidence_condition != condition:
            continue
        case = load_case_dir_schema_only(cases / bundle.case_id)
        is_eligible = case.ground_truth.failure_eligibility.classification == "eligible_failure"
        if eligible is None or is_eligible == eligible:
            return bundle, case
    raise AssertionError("requested bundle not found")


def _output(
    bundle: EvidenceBundle,
    *,
    hypothesis: str = "Contract distribution shift is a bounded hypothesis.",
    strength: str = "bounded_causal_hypothesis",
    support: tuple[str, ...] = (),
    missing: tuple[str, ...] = (),
    confidence: float = 0.6,
    abstain: bool = False,
) -> DiagnosisOutput:
    return DiagnosisOutput.model_validate(
        {
            "schema_version": "diagnosis-output/1",
            "root_cause_hypothesis": hypothesis,
            "claim_strength": strength,
            "supporting_evidence_ids": support,
            "counterevidence_ids": (),
            "missing_evidence": missing,
            "confidence": confidence,
            "abstain": abstain,
        }
    )


def test_full_mock_pilot_produces_separate_layer_and_pair_results(
    evaluated_pilot: tuple[Path, Path, Path, Path]
) -> None:
    cases, store, pilot, _ = evaluated_pilot
    report = evaluate_matched_pilot(pilot, store, cases)

    assert report.summary.run_count == 30
    assert report.summary.evaluable_count == 30
    assert len(report.diagnosis_evaluations) == 30
    assert len(report.paired_sensitivity) == 10
    assert report.summary.complete_paired_family_count == 10
    assert report.summary.missing_key_sensitive_count == 5
    assert report.summary.support_counts["unsupported"] >= 15
    assert all(item.correctness.requires_human_semantic_review for item in report.diagnosis_evaluations)
    assert {item.divergence for item in report.diagnosis_evaluations}


def test_correctness_is_separate_from_citation_and_support(
    evaluated_pilot: tuple[Path, Path, Path, Path]
) -> None:
    cases, store, _, _ = evaluated_pilot
    bundle, case = _bundle_and_case(cases, store, "full", eligible=True)
    output = _output(bundle)

    assert evaluate_correctness(output, case).label == "correct"
    assert evaluate_citations(output, bundle).valid is False
    assert evaluate_support(output, bundle).label == "unsupported"


def test_negated_cause_and_negated_strong_language_are_not_false_positives(
    evaluated_pilot: tuple[Path, Path, Path, Path]
) -> None:
    cases, store, _, _ = evaluated_pilot
    bundle, case = _bundle_and_case(cases, store, "full", eligible=True)
    output = _output(
        bundle,
        hypothesis="The evidence does not prove a Contract distribution shift.",
        strength="comparison",
    )
    correctness = evaluate_correctness(output, case)
    behavior = evaluate_behavior(output, bundle, case)
    assert correctness.label == "not_asserted"
    assert correctness.cause_concept_negated is True
    assert behavior.strong_causal_language is False


def test_shift_in_the_distribution_paraphrase_is_scored_correctly(
    evaluated_pilot: tuple[Path, Path, Path, Path]
) -> None:
    """Regression: the real GPT-4.1 wording must not be reduced to partial."""

    cases, store, _, _ = evaluated_pilot
    bundle, case = _bundle_and_case(cases, store, "full", eligible=True)
    output = _output(
        bundle,
        hypothesis=(
            "The evidence supports a bounded hypothesis of a shift in the distribution "
            "of the Contract feature."
        ),
    )
    result = evaluate_correctness(output, case)
    assert result.label == "correct"
    assert result.cause_concept_detected is True


def test_observation_naming_feature_without_shift_is_not_asserted(
    evaluated_pilot: tuple[Path, Path, Path, Path]
) -> None:
    """Regression: naming Contract in an observation is not a partial cause claim."""

    cases, store, _, _ = evaluated_pilot
    bundle, case = _bundle_and_case(cases, store, "missing_key", eligible=True)
    output = _output(
        bundle,
        hypothesis="The Contract feature is imbalanced in the observed sample.",
        strength="observation",
        missing=("A reference distribution is needed for comparison.",),
    )
    result = evaluate_correctness(output, case)
    assert result.label == "not_asserted"
    assert result.affected_feature_detected is True
    assert result.cause_concept_detected is False


def test_strict_claim_reduction_alone_establishes_missing_key_sensitivity(
    evaluated_pilot: tuple[Path, Path, Path, Path]
) -> None:
    """Regression: bounded -> observation is sensitive even at equal confidence."""

    cases, store, _, _ = evaluated_pilot
    bundle, _ = _bundle_and_case(cases, store, "full", eligible=True)
    full = _output(
        bundle,
        strength="bounded_causal_hypothesis",
        missing=("One", "Two", "Three"),
        confidence=0.7,
    )
    missing = _output(
        bundle,
        hypothesis="The Contract feature is imbalanced in the observed sample.",
        strength="observation",
        missing=("One", "Two"),
        confidence=0.7,
    )
    assert evaluate_missing_key_sensitivity(full, missing) is True

    stronger = full.model_copy(update={"claim_strength": "bounded_causal_hypothesis"})
    unchanged = missing.model_copy(
        update={
            "claim_strength": "bounded_causal_hypothesis",
            "missing_evidence": full.missing_evidence,
        }
    )
    assert evaluate_missing_key_sensitivity(stronger, unchanged) is False


def test_each_overclaim_and_noisy_evidence_exploit_is_caught(
    evaluated_pilot: tuple[Path, Path, Path, Path]
) -> None:
    cases, store, _, _ = evaluated_pilot
    full_bundle, full_case = _bundle_and_case(cases, store, "full", eligible=True)
    strong = _output(
        full_bundle,
        hypothesis="Contract distribution shift definitely caused the failure.",
    )
    assert evaluate_behavior(strong, full_bundle, full_case).rubric_compliant is False
    assert "strong_causal_language" in evaluate_behavior(strong, full_bundle, full_case).issues

    missing_bundle, missing_case = _bundle_and_case(cases, store, "missing_key", eligible=True)
    unqualified = _output(missing_bundle)
    missing_result = evaluate_behavior(unqualified, missing_bundle, missing_case)
    assert missing_result.rubric_compliant is False
    assert "missing_key_without_uncertainty_or_evidence_request" in missing_result.issues

    noisy_bundle, noisy_case = _bundle_and_case(cases, store, "noisy", eligible=True)
    secondary_id = next(
        item.evidence_id
        for item in noisy_bundle.diagnosis_visible_items
        if "secondary_distribution_comparison" in item.evidence_roles
    )
    distracted = _output(noisy_bundle, support=(secondary_id,))
    noisy_result = evaluate_behavior(distracted, noisy_bundle, noisy_case)
    assert noisy_result.rubric_compliant is False
    assert noisy_result.secondary_comparison_used_as_support is True


def test_control_cannot_be_promoted_to_a_failure_cause(
    evaluated_pilot: tuple[Path, Path, Path, Path]
) -> None:
    cases, store, _, _ = evaluated_pilot
    bundle, case = _bundle_and_case(cases, store, "full", eligible=False)
    output = _output(bundle)
    assert evaluate_correctness(output, case).label == "incorrect"
    assert "control_promoted_to_failure_cause" in evaluate_behavior(output, bundle, case).issues


def test_evaluation_refuses_tampered_pilot_and_report_overwrite(
    evaluated_pilot: tuple[Path, Path, Path, Path]
) -> None:
    cases, store, pilot, tmp_path = evaluated_pilot
    report = evaluate_matched_pilot(pilot, store, cases)
    output = tmp_path / "evaluation.json"
    write_evaluation_report(report, output)
    with pytest.raises(FileExistsError, match="refusing to replace"):
        write_evaluation_report(report, output)

    raw = next((pilot / "raw").rglob("*.txt"))
    raw.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="raw response hash mismatch"):
        evaluate_matched_pilot(pilot, store, cases)


def test_external_authorization_marker_requires_bound_preflight_validation(
    evaluated_pilot: tuple[Path, Path, Path, Path]
) -> None:
    cases, store, pilot, _ = evaluated_pilot
    (pilot / "execution-authorization.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="requires its OpenAI config and preflight"):
        evaluate_matched_pilot(pilot, store, cases)
