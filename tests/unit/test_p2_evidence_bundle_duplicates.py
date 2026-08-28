"""Duplicate and replay validation for canonical evidence bundles."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.binary_evaluation import ConfusionMatrix
from aletheia_lab.benchmark.p2.contracts import (
    ExecutedCandidate,
    FamilyCensusEntry,
    TechnicalDispositionEntry,
)
from aletheia_lab.benchmark.p2.evidence_conditions import (
    EvidenceBundleDuplicateAudit,
    EvidenceBundleDuplicateFinding,
    EvidenceConditionBuild,
    EvidenceConditionBundle,
    EvidenceConditionError,
    audit_evidence_bundle_duplicates,
    build_evidence_bundle_collection,
    evidence_content_sha256,
    evidence_content_similarity,
    validate_evidence_bundle_collection,
    validate_evidence_condition_bundles,
)
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

_HEX = {letter: letter * 64 for letter in "abcdef"}


def _candidate(marker: str, *, dataset_marker: str = "e") -> ValidatedMechanismCandidate:
    fingerprint = marker * 64
    candidate_id = f"p2-candidate-{marker * 64}"
    execution = ExecutedCandidate(
        candidate_id=candidate_id,
        slot_id="M1-F1",
        fault_type="data_drift",
        role="fault_directed",
        slot_kind="primary",
        proposed_family_sha256=fingerprint,
        dataset_sha256=dataset_marker * 64,
        model_data_split_manifest_sha256=_HEX["f"],
    )
    return ValidatedMechanismCandidate(
        candidate_id=candidate_id,
        slot_id="M1-F1",
        fault_type="data_drift",
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
        fault_type="data_drift",
        family_class="eligible_failure",
        proposed_family_sha256=candidate.proposed_family_sha256,
    )


def _snapshot(matrix: ConfusionMatrix) -> DiagnosisMetricSnapshot:
    return DiagnosisMetricSnapshot(
        accuracy=matrix.accuracy(),
        macro_f1=matrix.macro_f1(),
        minority_recall=matrix.minority_recall(),
    )


def _evidence(
    *,
    observed_monthly: float = 0.8,
    population_stability_index: float = 0.19,
    secondary_observed: float = 0.405,
) -> DataDriftDiagnosisEvidence:
    reference = ConfusionMatrix(
        true_negative=70, false_positive=10, false_negative=10, true_positive=10
    )
    observed = ConfusionMatrix(
        true_negative=60, false_positive=20, false_negative=15, true_positive=5
    )
    reference_rates = _snapshot(reference)
    observed_rates = _snapshot(observed)
    performance = PerformanceEvidence(
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
    return DataDriftDiagnosisEvidence(
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
                CategoryShare(category="annual", proportion=1.0 - observed_monthly),
                CategoryShare(category="monthly", proportion=observed_monthly),
            ),
        ),
        population_stability_index=population_stability_index,
        secondary_comparison=SecondaryComparison(
            reference_value=0.4,
            observed_value=secondary_observed,
            absolute_delta=abs(secondary_observed - 0.4),
            stability_bound=0.05,
        ),
    )


def _build(marker: str, *, evidence: DataDriftDiagnosisEvidence | None = None) -> EvidenceConditionBuild:
    candidate = _candidate(marker)
    return EvidenceConditionBuild(
        candidate=candidate,
        family=_family(candidate),
        evidence=_evidence() if evidence is None else evidence,
    )


def _rehash_bundle(
    bundle: EvidenceConditionBundle,
    **updates: object,
) -> EvidenceConditionBundle:
    """Rebuild through the public builder; tests never forge internal hashes ad hoc."""

    payload = bundle.model_dump(mode="json")
    payload.update(updates)
    return EvidenceConditionBundle.model_validate(payload)


def test_collection_is_canonical_independent_of_build_order() -> None:
    left = _build("a", evidence=_evidence(observed_monthly=0.8))
    right = _build("b", evidence=_evidence(observed_monthly=0.75))
    forward = build_evidence_bundle_collection((left, right))
    reverse = build_evidence_bundle_collection((right, left))

    assert forward == reverse
    assert tuple(bundle.case_family_id for bundle in forward) == tuple(
        sorted(bundle.case_family_id for bundle in forward)
    )


def test_collection_rejects_repeated_family_and_candidate() -> None:
    build = _build("a")
    with pytest.raises(EvidenceConditionError, match="repeat a family"):
        build_evidence_bundle_collection((build, build))

    other_family = _family(_candidate("b"))
    repeated_candidate = EvidenceConditionBuild(
        candidate=build.candidate,
        family=other_family.model_copy(
            update={
                "candidate_id": build.candidate.candidate_id,
                "fault_type": build.candidate.fault_type,
                "proposed_family_sha256": build.candidate.proposed_family_sha256,
                "case_family_id": f"p2-family-{build.candidate.proposed_family_sha256}",
            }
        ),
        evidence=build.evidence,
    )
    with pytest.raises(EvidenceConditionError, match="repeat a family|repeat a candidate"):
        build_evidence_bundle_collection((build, repeated_candidate))


def test_a_repeated_bundle_is_classified_as_exact_replay() -> None:
    bundle = build_evidence_bundle_collection((_build("a"),))[0]
    audit = audit_evidence_bundle_duplicates((bundle, bundle))

    assert len(audit.findings) == 1
    assert audit.findings[0].kind == "exact_replay"
    assert audit.has_blockers()


def test_cross_family_equal_content_is_an_effective_duplicate() -> None:
    bundles = build_evidence_bundle_collection((_build("a"), _build("b")))
    audit = audit_evidence_bundle_duplicates(bundles)

    assert {finding.kind for finding in audit.findings} == {"effective_content"}
    assert {finding.evidence_condition for finding in audit.findings} == {
        "full",
        "missing_key",
        "noisy",
    }
    assert audit.has_blockers()
    with pytest.raises(EvidenceConditionError, match="duplicate content"):
        validate_evidence_bundle_collection(
            bundles,
            builds=(_build("a"), _build("b")),
        )


def test_near_content_is_disclosed_but_not_blocking() -> None:
    left = _build("a", evidence=_evidence(observed_monthly=0.8))
    right = _build("b", evidence=_evidence(observed_monthly=0.79))
    bundles = build_evidence_bundle_collection((left, right))
    audit = audit_evidence_bundle_duplicates(bundles)

    assert audit.findings
    assert {finding.kind for finding in audit.findings} == {"near_content"}
    assert all(0.9 <= finding.similarity < 1.0 for finding in audit.findings)
    assert not audit.has_blockers()
    assert validate_evidence_bundle_collection(bundles, builds=(left, right)) == audit


def test_materially_different_content_is_not_flagged() -> None:
    left = build_evidence_bundle_collection((_build("a"),))[0]
    right = build_evidence_bundle_collection(
        (
            _build(
                "b",
                evidence=_evidence(
                    observed_monthly=0.7,
                    population_stability_index=0.4,
                    secondary_observed=0.44,
                ),
            ),
        )
    )[0]

    assert evidence_content_similarity(left, right) < 0.9
    assert audit_evidence_bundle_duplicates((left, right)).findings == ()


def test_content_hash_excludes_envelope_but_includes_condition() -> None:
    left = build_evidence_bundle_collection((_build("a"),))
    right = build_evidence_bundle_collection((_build("b"),))

    assert evidence_content_sha256(left[0]) == evidence_content_sha256(right[0])
    assert evidence_content_sha256(left[0]) != evidence_content_sha256(left[1])


def test_validator_rejects_bundle_replay_across_authoritative_family() -> None:
    left = _build("a", evidence=_evidence(observed_monthly=0.8))
    right = _build("b", evidence=_evidence(observed_monthly=0.75))
    left_bundles = build_evidence_bundle_collection((left,))

    with pytest.raises(EvidenceConditionError, match="authoritative condition builds"):
        validate_evidence_bundle_collection(left_bundles, builds=(right,))
    with pytest.raises(EvidenceConditionError, match="canonical condition build"):
        validate_evidence_condition_bundles(
            left_bundles,
            candidate=right.candidate,
            family=right.family,
            evidence=right.evidence,
        )


def test_validator_rejects_omission_duplication_and_condition_substitution() -> None:
    build = _build("a")
    bundles = build_evidence_bundle_collection((build,))
    forged_sets = (
        bundles[:-1],
        (*bundles, bundles[0]),
        (bundles[0], bundles[0], bundles[2]),
    )
    for forged in forged_sets:
        with pytest.raises(EvidenceConditionError, match="authoritative condition builds"):
            validate_evidence_bundle_collection(forged, builds=(build,))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_id", f"p2-candidate-{'f' * 64}"),
        ("case_family_id", f"p2-family-{'f' * 64}"),
        ("source_binding_sha256", _HEX["f"]),
        ("source_execution_id", f"p2-execution-{'f' * 64}"),
    ],
)
def test_validly_shaped_envelope_tamper_is_rejected(field: str, value: str) -> None:
    build = _build("a")
    bundle = build_evidence_bundle_collection((build,))[0]
    payload = bundle.model_dump(mode="json")
    payload[field] = value

    with pytest.raises(ValidationError, match="canonical bundle|bind family"):
        EvidenceConditionBundle.model_validate(payload)


def test_duplicate_finding_contract_rejects_false_classifications() -> None:
    bundles = build_evidence_bundle_collection((_build("a"), _build("b")))
    left, right = bundles[0], bundles[3]
    content_hash = evidence_content_sha256(left)
    base = {
        "evidence_condition": left.evidence_condition,
        "evidence_bundle_id": left.evidence_bundle_id,
        "duplicate_of_bundle_id": right.evidence_bundle_id,
        "case_family_id": left.case_family_id,
        "duplicate_of_family_id": right.case_family_id,
        "content_sha256": content_hash,
        "duplicate_of_content_sha256": content_hash,
    }
    with pytest.raises(ValidationError, match="similarity 1.0"):
        EvidenceBundleDuplicateFinding(
            kind="effective_content",
            similarity=0.99,
            **base,  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError, match="near-content similarity"):
        EvidenceBundleDuplicateFinding(
            kind="near_content",
            similarity=0.89,
            **{**base, "duplicate_of_content_sha256": _HEX["f"]},  # type: ignore[arg-type]
        )


def test_duplicate_audit_requires_unique_canonical_findings() -> None:
    bundles = build_evidence_bundle_collection((_build("a"), _build("b")))
    finding = audit_evidence_bundle_duplicates(bundles).findings[0]
    with pytest.raises(ValidationError, match="unique"):
        EvidenceBundleDuplicateAudit(findings=(finding, finding))

    findings = audit_evidence_bundle_duplicates(bundles).findings
    with pytest.raises(ValidationError, match="canonical order"):
        EvidenceBundleDuplicateAudit(findings=tuple(reversed(findings)))


@given(st.integers(min_value=65, max_value=90), st.integers(min_value=65, max_value=90))
@settings(deadline=None)
def test_content_similarity_is_symmetric_bounded_and_reflexive(
    left_percent: int,
    right_percent: int,
) -> None:
    left = build_evidence_bundle_collection(
        (_build("a", evidence=_evidence(observed_monthly=left_percent / 100)),)
    )[0]
    right = build_evidence_bundle_collection(
        (_build("b", evidence=_evidence(observed_monthly=right_percent / 100)),)
    )[0]

    similarity = evidence_content_similarity(left, right)
    assert 0.0 <= similarity <= 1.0
    assert similarity == evidence_content_similarity(right, left)
    assert evidence_content_similarity(left, left) == 1.0


@given(st.integers(min_value=65, max_value=90))
def test_equal_logical_content_has_equal_content_hash_across_family_bindings(
    monthly_percent: int,
) -> None:
    evidence = _evidence(observed_monthly=monthly_percent / 100)
    left = build_evidence_bundle_collection((_build("a", evidence=evidence),))[0]
    right = build_evidence_bundle_collection((_build("b", evidence=evidence),))[0]

    assert left.evidence_bundle_id != right.evidence_bundle_id
    assert evidence_content_sha256(left) == evidence_content_sha256(right)
    assert evidence_content_similarity(left, right) == 1.0
