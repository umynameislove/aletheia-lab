"""Contract tests for versioned, canonical diagnosis evidence bundles."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.binary_evaluation import ConfusionMatrix
from aletheia_lab.benchmark.p2.contracts import (
    ExecutedCandidate,
    FamilyCensusEntry,
    TechnicalDispositionEntry,
)
from aletheia_lab.benchmark.p2.evidence_conditions import (
    EVIDENCE_CONDITION_BUILDER_VERSION,
    EVIDENCE_CONDITION_BUNDLE_SCHEMA_VERSION,
    EvidenceConditionBundle,
    EvidenceConditionError,
    build_evidence_condition_bundles,
    evidence_execution_id_for,
    is_evidence_bundle_id,
    validate_evidence_condition_bundles,
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


def _candidate(fault_type: str) -> ValidatedMechanismCandidate:
    index = ("data_drift", "label_noise", "preprocessing_bug").index(fault_type)
    fingerprint = "abc"[index] * 64
    candidate_id = f"p2-candidate-{'def'[index] * 64}"
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


def _drift_evidence() -> DataDriftDiagnosisEvidence:
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


def _label_evidence() -> LabelDiagnosisEvidence:
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


def _preprocessing_evidence() -> PreprocessingDiagnosisEvidence:
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


_EVIDENCE_FACTORY = {
    "data_drift": _drift_evidence,
    "label_noise": _label_evidence,
    "preprocessing_bug": _preprocessing_evidence,
}


@pytest.mark.parametrize("fault_type", list(_EVIDENCE_FACTORY))
def test_builder_creates_versioned_family_paired_condition_set(fault_type: str) -> None:
    candidate = _candidate(fault_type)
    bundles = build_evidence_condition_bundles(
        candidate=candidate,
        family=_family(candidate),
        evidence=_EVIDENCE_FACTORY[fault_type](),
    )

    assert tuple(bundle.evidence_condition for bundle in bundles) == (
        "full",
        "missing_key",
        "noisy",
    )
    assert {bundle.schema_version for bundle in bundles} == {
        EVIDENCE_CONDITION_BUNDLE_SCHEMA_VERSION
    }
    assert {bundle.builder_version for bundle in bundles} == {
        EVIDENCE_CONDITION_BUILDER_VERSION
    }
    assert {bundle.case_family_id for bundle in bundles} == {
        _family(candidate).case_family_id
    }
    assert {bundle.candidate_id for bundle in bundles} == {candidate.candidate_id}
    assert {bundle.source_execution_id for bundle in bundles} == {
        evidence_execution_id_for(candidate)
    }
    assert {bundle.source_binding_sha256 for bundle in bundles} == {
        candidate.binding_sha256()
    }
    assert len({bundle.evidence_bundle_id for bundle in bundles}) == 3
    assert all(is_evidence_bundle_id(bundle.evidence_bundle_id) for bundle in bundles)


@pytest.mark.parametrize("fault_type", list(_EVIDENCE_FACTORY))
def test_equivalent_authoritative_input_produces_identical_bundles(fault_type: str) -> None:
    candidate = _candidate(fault_type)
    family = _family(candidate)
    evidence = _EVIDENCE_FACTORY[fault_type]()

    first = build_evidence_condition_bundles(
        candidate=candidate, family=family, evidence=evidence
    )
    second = build_evidence_condition_bundles(
        candidate=ValidatedMechanismCandidate.model_validate(
            json.loads(candidate.model_dump_json())
        ),
        family=FamilyCensusEntry.model_validate(json.loads(family.model_dump_json())),
        evidence=type(evidence).model_validate_json(evidence.model_dump_json()),
    )

    assert first == second
    assert tuple(bundle.bundle_sha256 for bundle in first) == tuple(
        bundle.bundle_sha256 for bundle in second
    )


def test_bundle_roundtrip_preserves_canonical_identity() -> None:
    candidate = _candidate("label_noise")
    bundles = build_evidence_condition_bundles(
        candidate=candidate,
        family=_family(candidate),
        evidence=_label_evidence(),
    )

    restored = tuple(
        EvidenceConditionBundle.model_validate_json(bundle.model_dump_json())
        for bundle in bundles
    )
    assert restored == bundles


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", "p2-evidence-condition-bundle/v999", "schema_version"),
        ("builder_version", "p2-condition-builder/v999", "builder_version"),
        ("evidence_bundle_id", f"p2-evidence-bundle-{_HEX['a']}", "derived"),
        ("bundle_sha256", _HEX["a"], "canonical bundle"),
        ("diagnosis_projection_sha256", _HEX["a"], "projection"),
        ("source_binding_sha256", _HEX["a"], "canonical bundle"),
        ("source_execution_id", f"p2-execution-{_HEX['a']}", "canonical bundle"),
    ],
)
def test_bundle_contract_rejects_version_and_digest_forgery(
    field: str, value: str, message: str
) -> None:
    candidate = _candidate("data_drift")
    bundle = build_evidence_condition_bundles(
        candidate=candidate,
        family=_family(candidate),
        evidence=_drift_evidence(),
    )[0]
    payload = bundle.model_dump(mode="json")
    payload[field] = value

    with pytest.raises(ValidationError, match=message):
        EvidenceConditionBundle.model_validate(payload)


def test_bundle_contract_rejects_unknown_fields_and_mutation() -> None:
    candidate = _candidate("preprocessing_bug")
    bundle = build_evidence_condition_bundles(
        candidate=candidate,
        family=_family(candidate),
        evidence=_preprocessing_evidence(),
    )[0]
    payload = bundle.model_dump(mode="json")
    payload["created_at"] = "2026-08-12T00:00:00Z"
    with pytest.raises(ValidationError, match="Extra inputs"):
        EvidenceConditionBundle.model_validate(payload)
    with pytest.raises(ValidationError, match="frozen"):
        bundle.bundle_sha256 = _HEX["a"]  # type: ignore[misc]


@pytest.mark.parametrize(
    "leak",
    [
        {"ground_truth": "hidden cause"},
        {"expected_diagnosis": "label corruption"},
        {"evaluator_metadata": {"score": 1}},
        {"admission_outcome": "accepted"},
        {"family_class": "eligible_failure"},
        {"fault_type": "data_drift"},
        {"artifact_path": "/private/expected-answer.json"},
        {"source": "file:///private/evaluator.json"},
        {"source": "C:\\private\\evaluator.json"},
        {"value": "missing_key"},
    ],
)
def test_bundle_boundary_blocks_evaluator_and_hidden_truth_leakage(
    leak: dict[str, object],
) -> None:
    candidate = _candidate("data_drift")
    bundle = build_evidence_condition_bundles(
        candidate=candidate,
        family=_family(candidate),
        evidence=_drift_evidence(),
    )[0]
    payload = bundle.model_dump(mode="json")
    projection = dict(bundle.diagnosis_projection)
    projection["leaked"] = leak
    payload["diagnosis_projection"] = projection

    with pytest.raises(ValidationError, match="projection|evaluator"):
        EvidenceConditionBundle.model_validate(payload)


def test_validator_rejects_missing_reordered_and_foreign_bundle_sets() -> None:
    candidate = _candidate("label_noise")
    family = _family(candidate)
    evidence = _label_evidence()
    bundles = build_evidence_condition_bundles(
        candidate=candidate,
        family=family,
        evidence=evidence,
    )

    for forged in (bundles[:-1], tuple(reversed(bundles))):
        with pytest.raises(EvidenceConditionError, match="canonical condition build"):
            validate_evidence_condition_bundles(
                forged,
                candidate=candidate,
                family=family,
                evidence=evidence,
            )

    other = _candidate("preprocessing_bug")
    other_bundles = build_evidence_condition_bundles(
        candidate=other,
        family=_family(other),
        evidence=_preprocessing_evidence(),
    )
    with pytest.raises(EvidenceConditionError, match="canonical condition build"):
        validate_evidence_condition_bundles(
            other_bundles,
            candidate=candidate,
            family=family,
            evidence=evidence,
        )


def test_bundle_id_namespace_is_strict() -> None:
    assert not is_evidence_bundle_id(f"p2-context-{_HEX['a']}")
    assert not is_evidence_bundle_id(f"p2-evidence-bundle-{_HEX['a'].upper()}")
    assert not is_evidence_bundle_id("p2-evidence-bundle-short")


_REPRODUCTION_SCRIPT = r"""
import json
import sys

from aletheia_lab.benchmark.p2.binary_evaluation import ConfusionMatrix
from aletheia_lab.benchmark.p2.contracts import (
    ExecutedCandidate,
    FamilyCensusEntry,
    TechnicalDispositionEntry,
)
from aletheia_lab.benchmark.p2.evidence_conditions import build_evidence_condition_bundles
from aletheia_lab.benchmark.p2.evidence_projection import (
    CategoryShare,
    DataDriftDiagnosisEvidence,
    DiagnosisConfusionComparison,
    DiagnosisMetricSnapshot,
    DiagnosisPerformanceComparison,
    DistributionSnapshot,
    PerformanceEvidence,
    SecondaryComparison,
)
from aletheia_lab.benchmark.p2.mechanism_validation import ValidatedMechanismCandidate

fingerprint = "a" * 64
candidate_id = "p2-candidate-" + "d" * 64
execution = ExecutedCandidate(
    candidate_id=candidate_id,
    slot_id="M1-F1",
    fault_type="data_drift",
    role="fault_directed",
    slot_kind="primary",
    proposed_family_sha256=fingerprint,
    dataset_sha256="e" * 64,
    model_data_split_manifest_sha256="f" * 64,
)
candidate = ValidatedMechanismCandidate(
    candidate_id=candidate_id,
    slot_id="M1-F1",
    fault_type="data_drift",
    proposed_family_sha256=fingerprint,
    slot_sha256="a" * 64,
    artifact_sha256="b" * 64,
    execution=execution,
    disposition=TechnicalDispositionEntry(
        candidate_id=candidate_id,
        disposition="technically_valid",
    ),
)
family = FamilyCensusEntry(
    case_family_id="p2-family-" + fingerprint,
    candidate_id=candidate_id,
    fault_type="data_drift",
    family_class="eligible_failure",
    proposed_family_sha256=fingerprint,
)
reference = ConfusionMatrix(
    true_negative=70,
    false_positive=10,
    false_negative=10,
    true_positive=10,
)
observed = ConfusionMatrix(
    true_negative=60,
    false_positive=20,
    false_negative=15,
    true_positive=5,
)
def rates(matrix):
    return DiagnosisMetricSnapshot(
        accuracy=matrix.accuracy(),
        macro_f1=matrix.macro_f1(),
        minority_recall=matrix.minority_recall(),
    )
reference_rates = rates(reference)
observed_rates = rates(observed)
performance = PerformanceEvidence(
    performance_comparison=DiagnosisPerformanceComparison(
        reference_sample_size=100,
        observed_sample_size=100,
        reference=reference_rates,
        observed=observed_rates,
        accuracy_delta=observed_rates.accuracy - reference_rates.accuracy,
        macro_f1_delta=observed_rates.macro_f1 - reference_rates.macro_f1,
        minority_recall_delta=(observed_rates.minority_recall - reference_rates.minority_recall),
    ),
    confusion_comparison=DiagnosisConfusionComparison(reference=reference, observed=observed),
)
evidence = DataDriftDiagnosisEvidence(
    performance=performance,
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
    secondary_comparison=SecondaryComparison(
        reference_value=0.4,
        observed_value=0.405,
        absolute_delta=0.005,
        stability_bound=0.01,
    ),
)
bundles = build_evidence_condition_bundles(
    candidate=candidate,
    family=family,
    evidence=evidence,
)
sys.stdout.write(json.dumps([bundle.model_dump(mode="json") for bundle in bundles], sort_keys=True))
"""


def _build_in_subprocess(seed: str, repo_root: Path) -> str:
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = seed
    environment["PYTHONPATH"] = str(repo_root / "src")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-c", _REPRODUCTION_SCRIPT],
        check=True,
        capture_output=True,
        text=True,
        cwd=repo_root,
        env=environment,
    ).stdout


def test_bundle_bytes_are_identical_across_hash_seeds() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    assert _build_in_subprocess("1", repo_root) == _build_in_subprocess("999", repo_root)
