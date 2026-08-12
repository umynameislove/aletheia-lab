"""Regression tests for condition-safe Phase 2 diagnosis projections."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.binary_evaluation import (
    BinaryMetricSnapshot,
    ConfusionMatrix,
    MetricComparison,
)
from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import (
    ContextEntry,
    ExecutedCandidate,
    FamilyCensusEntry,
    TechnicalDispositionEntry,
    context_id_for,
)
from aletheia_lab.benchmark.p2.evidence_projection import (
    CategoryShare,
    DataDriftDiagnosisEvidence,
    DiagnosisConfusionComparison,
    DiagnosisMetricSnapshot,
    DiagnosisPerformanceComparison,
    DistributionSnapshot,
    EvidenceProjectionError,
    LabelDiagnosisEvidence,
    PerformanceEvidence,
    PreprocessingDiagnosisEvidence,
    SchemaComparison,
    SecondaryComparison,
    TargetProjectionComparison,
    TransformSignatureComparison,
    build_diagnosis_contexts,
    performance_evidence_from,
    validate_diagnosis_contexts,
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


def _candidate(
    fault_type: str,
    *,
    fingerprint: str | None = None,
    technically_valid: bool = True,
) -> ValidatedMechanismCandidate:
    mechanism_index = ("data_drift", "label_noise", "preprocessing_bug").index(fault_type)
    digest = fingerprint or ("abc"[mechanism_index] * 64)
    candidate_id = f"p2-candidate-{'def'[mechanism_index] * 64}"
    execution = ExecutedCandidate(
        candidate_id=candidate_id,
        slot_id=_SLOT[fault_type],  # type: ignore[arg-type]
        fault_type=fault_type,  # type: ignore[arg-type]
        role="fault_directed",
        slot_kind="primary",
        proposed_family_sha256=digest,
        dataset_sha256=_HEX["e"],
        model_data_split_manifest_sha256=_HEX["f"],
    )
    disposition = TechnicalDispositionEntry(
        candidate_id=candidate_id,
        disposition="technically_valid" if technically_valid else "technical_rejected",
        rejection_reason=None if technically_valid else "source_contract_mismatch",
    )
    return ValidatedMechanismCandidate(
        candidate_id=candidate_id,
        slot_id=_SLOT[fault_type],  # type: ignore[arg-type]
        fault_type=fault_type,  # type: ignore[arg-type]
        proposed_family_sha256=digest,
        slot_sha256=_HEX["a"],
        artifact_sha256=_HEX["b"],
        execution=execution,
        disposition=disposition,
    )


def _family(
    candidate: ValidatedMechanismCandidate,
    family_class: str = "eligible_failure",
) -> FamilyCensusEntry:
    return FamilyCensusEntry(
        case_family_id=f"p2-family-{candidate.proposed_family_sha256}",
        candidate_id=candidate.candidate_id,
        fault_type=candidate.fault_type,
        family_class=family_class,  # type: ignore[arg-type]
        proposed_family_sha256=candidate.proposed_family_sha256,
    )


def _metric_snapshot(confusion: ConfusionMatrix) -> DiagnosisMetricSnapshot:
    return DiagnosisMetricSnapshot(
        accuracy=confusion.accuracy(),
        macro_f1=confusion.macro_f1(),
        minority_recall=confusion.minority_recall(),
    )


def _performance() -> PerformanceEvidence:
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
    reference_rates = _metric_snapshot(reference)
    observed_rates = _metric_snapshot(observed)
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
        reference_value=0.40,
        observed_value=0.405,
        absolute_delta=0.005,
        stability_bound=0.01,
    )


def _distribution(sample_size: int, first: float) -> DistributionSnapshot:
    return DistributionSnapshot(
        sample_size=sample_size,
        categories=(
            CategoryShare(category="annual", proportion=1.0 - first),
            CategoryShare(category="monthly", proportion=first),
        ),
    )


def _drift_evidence() -> DataDriftDiagnosisEvidence:
    return DataDriftDiagnosisEvidence(
        performance=_performance(),
        reference_distribution=_distribution(100, 0.60),
        observed_distribution=_distribution(100, 0.80),
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


def _item_ids(context: ContextEntry) -> tuple[str, ...]:
    items = context.diagnosis_projection["items"]
    assert isinstance(items, list)
    return tuple(str(item["id"]) for item in items if isinstance(item, dict))


@pytest.mark.parametrize(
    ("fault_type", "full_ids", "missing_ids"),
    [
        (
            "data_drift",
            (
                "distribution-reference",
                "distribution-observed",
                "population-stability-summary",
                "performance-comparison",
                "confusion-comparison",
            ),
            ("distribution-observed", "confusion-comparison"),
        ),
        (
            "label_noise",
            (
                "target-distribution-comparison",
                "target-quality-audit-summary",
                "performance-comparison",
                "confusion-comparison",
            ),
            ("performance-comparison", "confusion-comparison"),
        ),
        (
            "preprocessing_bug",
            (
                "transform-signature-comparison",
                "target-projection-comparison",
                "performance-comparison",
                "schema-comparison",
            ),
            ("performance-comparison", "schema-comparison"),
        ),
    ],
)
def test_eligible_projection_has_canonical_family_specific_conditions(
    fault_type: str,
    full_ids: tuple[str, ...],
    missing_ids: tuple[str, ...],
) -> None:
    candidate = _candidate(fault_type)
    family = _family(candidate)
    evidence = _EVIDENCE_FACTORY[fault_type]()

    contexts = build_diagnosis_contexts(
        candidate=candidate,
        family=family,
        evidence=evidence,
    )

    assert tuple(context.evidence_condition for context in contexts) == (
        "full",
        "missing_key",
        "noisy",
    )
    assert _item_ids(contexts[0]) == full_ids
    assert _item_ids(contexts[1]) == missing_ids
    assert _item_ids(contexts[2]) == full_ids + ("secondary-comparison",)
    assert len(set(_item_ids(contexts[2]))) == len(_item_ids(contexts[2]))
    assert (
        contexts[2].diagnosis_projection["items"][:-1] == contexts[0].diagnosis_projection["items"]
    )


@pytest.mark.parametrize("fault_type", list(_EVIDENCE_FACTORY))
def test_siblings_share_one_opaque_source_binding_and_never_expose_condition(
    fault_type: str,
) -> None:
    candidate = _candidate(fault_type)
    contexts = build_diagnosis_contexts(
        candidate=candidate,
        family=_family(candidate),
        evidence=_EVIDENCE_FACTORY[fault_type](),
    )

    bindings = {str(context.diagnosis_projection["source_binding_sha256"]) for context in contexts}
    assert bindings == {candidate.binding_sha256()}
    for context in contexts:
        serialized = json.dumps(context.diagnosis_projection, sort_keys=True)
        assert context.evidence_condition not in serialized
        folded = serialized.casefold()
        for hidden_marker in (
            "candidate_id",
            "distractor",
            "eligibility",
            "evidence_condition",
            "expected_behavior",
            "family_class",
            "mutation_map",
            "provenance",
        ):
            assert hidden_marker not in folded


def test_authoritative_metric_comparison_is_whitelisted_without_provenance() -> None:
    performance = _performance()
    visible = performance.performance_comparison

    def snapshot(
        rates: DiagnosisMetricSnapshot,
        confusion: ConfusionMatrix,
    ) -> BinaryMetricSnapshot:
        return BinaryMetricSnapshot(
            schema_version="p2-binary-metric-snapshot/v1",
            metric_protocol_version="binary-alpha-metrics/v1",
            zero_division_policy="zero/v1",
            minority_label=1,
            prediction_count=confusion.total,
            accuracy=rates.accuracy,
            macro_f1=rates.macro_f1,
            minority_recall=rates.minority_recall,
            confusion=confusion,
        )

    comparison = MetricComparison(
        schema_version="p2-binary-metric-comparison/v1",
        metric_protocol_version="binary-alpha-metrics/v1",
        primary_metric="accuracy",
        primary_threshold=0.01,
        reference=snapshot(
            visible.reference,
            performance.confusion_comparison.reference,
        ),
        observed=snapshot(
            visible.observed,
            performance.confusion_comparison.observed,
        ),
        accuracy_delta=visible.accuracy_delta,
        macro_f1_delta=visible.macro_f1_delta,
        minority_recall_delta=visible.minority_recall_delta,
        measured_primary_outcome="regression",
        evaluation_source_sha256=_HEX["a"],
        reference_predictions_sha256=_HEX["b"],
        observed_predictions_sha256=_HEX["c"],
    )

    projected = performance_evidence_from(comparison)

    assert projected == performance
    serialized = json.dumps(projected.model_dump(mode="json"), sort_keys=True)
    assert "sha256" not in serialized
    assert "measured_primary_outcome" not in serialized


@pytest.mark.parametrize("family_class", ["stable_control", "improvement_control"])
def test_non_benign_controls_receive_full_only(family_class: str) -> None:
    candidate = _candidate("label_noise")
    contexts = build_diagnosis_contexts(
        candidate=candidate,
        family=_family(candidate, family_class),
        evidence=_label_evidence(),
    )
    assert tuple(context.evidence_condition for context in contexts) == ("full",)


def test_benign_control_receives_no_context_or_evidence() -> None:
    candidate = _candidate("preprocessing_bug")
    assert (
        build_diagnosis_contexts(
            candidate=candidate,
            family=_family(candidate, "benign_control"),
            evidence=None,
        )
        == ()
    )
    with pytest.raises(EvidenceProjectionError, match="must not receive"):
        build_diagnosis_contexts(
            candidate=candidate,
            family=_family(candidate, "benign_control"),
            evidence=_preprocessing_evidence(),
        )


def test_technically_rejected_candidate_cannot_produce_contexts() -> None:
    candidate = _candidate("data_drift", technically_valid=False)
    with pytest.raises(EvidenceProjectionError, match="technically rejected"):
        build_diagnosis_contexts(
            candidate=candidate,
            family=_family(candidate),
            evidence=_drift_evidence(),
        )


def test_cross_mechanism_evidence_is_rejected() -> None:
    candidate = _candidate("data_drift")
    with pytest.raises(EvidenceProjectionError, match="different mechanism"):
        build_diagnosis_contexts(
            candidate=candidate,
            family=_family(candidate),
            evidence=_label_evidence(),
        )


def test_eligible_failure_requires_bounded_secondary_evidence() -> None:
    candidate = _candidate("data_drift")
    evidence = _drift_evidence().model_copy(update={"secondary_comparison": None})
    with pytest.raises(EvidenceProjectionError, match="stable secondary"):
        build_diagnosis_contexts(
            candidate=candidate,
            family=_family(candidate),
            evidence=evidence,
        )


@pytest.mark.parametrize(
    ("fault_type", "evidence"),
    [
        (
            "data_drift",
            _drift_evidence().model_copy(
                update={
                    "observed_distribution": _drift_evidence().reference_distribution,
                    "population_stability_index": 0.0,
                }
            ),
        ),
        (
            "label_noise",
            _label_evidence().model_copy(
                update={
                    "target_quality_audit_summary": TargetQualityAudit(
                        schema_version="p2-target-quality-audit/v1",
                        audited_record_count=100,
                        disagreeing_record_count=0,
                        disagreement_rate=0.0,
                        disagreement_rate_lower_bound=wilson_interval(successes=0, trials=100)[0],
                        disagreement_rate_upper_bound=wilson_interval(successes=0, trials=100)[1],
                        interval_method="wilson-score/95",
                        protocol_version="target-quality-audit/v1",
                    )
                }
            ),
        ),
        (
            "preprocessing_bug",
            _preprocessing_evidence().model_copy(
                update={
                    "transform_signature_comparison": TransformSignatureComparison(
                        reference_signature_sha256=_HEX["a"],
                        observed_signature_sha256=_HEX["a"],
                        signatures_equal=True,
                    ),
                    "target_projection_comparison": TargetProjectionComparison(
                        sample_size=100,
                        differing_record_count=0,
                        difference_rate=0.0,
                        reference_projection_sha256=_HEX["c"],
                        observed_projection_sha256=_HEX["c"],
                    ),
                }
            ),
        ),
    ],
)
def test_eligible_full_projection_requires_decisive_family_evidence(
    fault_type: str,
    evidence: object,
) -> None:
    candidate = _candidate(fault_type)
    with pytest.raises(EvidenceProjectionError, match="eligible"):
        build_diagnosis_contexts(
            candidate=candidate,
            family=_family(candidate),
            evidence=evidence,  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError, match="stability bound"):
        SecondaryComparison(
            reference_value=0.4,
            observed_value=0.5,
            absolute_delta=0.1,
            stability_bound=0.01,
        )


def test_projection_is_deterministic() -> None:
    candidate = _candidate("label_noise")
    family = _family(candidate)
    evidence = _label_evidence()
    first = build_diagnosis_contexts(candidate=candidate, family=family, evidence=evidence)
    second = build_diagnosis_contexts(candidate=candidate, family=family, evidence=evidence)
    assert first == second
    assert tuple(item.diagnosis_projection_sha256 for item in first) == tuple(
        item.diagnosis_projection_sha256 for item in second
    )


def test_validator_rejects_cross_family_replay() -> None:
    candidate = _candidate("data_drift")
    family = _family(candidate)
    evidence = _drift_evidence()
    contexts = build_diagnosis_contexts(candidate=candidate, family=family, evidence=evidence)

    other_candidate = _candidate("data_drift", fingerprint="f" * 64)
    with pytest.raises(EvidenceProjectionError, match="canonical family projection"):
        validate_diagnosis_contexts(
            contexts,
            candidate=other_candidate,
            family=_family(other_candidate),
            evidence=evidence,
        )


def test_validator_rejects_validly_rehashed_item_tamper() -> None:
    candidate = _candidate("preprocessing_bug")
    family = _family(candidate)
    evidence = _preprocessing_evidence()
    contexts = build_diagnosis_contexts(candidate=candidate, family=family, evidence=evidence)
    full = contexts[0]
    projection = dict(full.diagnosis_projection)
    projection["items"] = list(projection["items"])[1:]
    forged = ContextEntry(
        diagnosis_context_id=full.diagnosis_context_id,
        case_family_id=full.case_family_id,
        evidence_condition=full.evidence_condition,
        diagnosis_projection=projection,
        diagnosis_projection_sha256=canonical_sha256(projection),
    )
    with pytest.raises(EvidenceProjectionError, match="canonical family projection"):
        validate_diagnosis_contexts(
            (forged, *contexts[1:]),
            candidate=candidate,
            family=family,
            evidence=evidence,
        )


@pytest.mark.parametrize(
    "leak",
    [
        {"candidate_id": f"p2-candidate-{_HEX['a']}"},
        {"provenance": {"seed": 201}},
        {"mutation_map": ["record-001"]},
        {"flip_rate": 0.05},
        {"original_targets": [0, 1]},
        {"affected_record_ids": ["record-001"]},
        {"mapping": {"from": 1, "to": 2}},
        {"mechanism": "label_noise"},
        {"value": "label noise"},
        {"value": "preprocessing-bug"},
        {"value": "full-comparison"},
        {"value": "missing key evidence"},
        {"value": "training_target_label_corruption"},
        {"value": "inference_encoder_mapping_mismatch"},
    ],
)
def test_context_boundary_rejects_hidden_evaluator_material(leak: dict[str, object]) -> None:
    family_id = f"p2-family-{_HEX['a']}"
    with pytest.raises(ValidationError, match="projection"):
        ContextEntry(
            diagnosis_context_id=context_id_for(
                case_family_id=family_id,
                evidence_condition="full",
            ),
            case_family_id=family_id,
            evidence_condition="full",
            diagnosis_projection=leak,
            diagnosis_projection_sha256=canonical_sha256(leak),
        )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"category": " monthly", "proportion": 1.0}, "trimmed Unicode NFC"),
        ({"category": "monthly ", "proportion": 1.0}, "trimmed Unicode NFC"),
    ],
)
def test_category_share_requires_canonical_text(
    payload: dict[str, object], expected: str
) -> None:
    with pytest.raises(ValidationError, match=expected):
        CategoryShare(**payload)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("categories", "expected"),
    [
        ((), "at least one category"),
        (
            (
                CategoryShare(category="a", proportion=0.5),
                CategoryShare(category="a", proportion=0.5),
            ),
            "unique",
        ),
        (
            (
                CategoryShare(category="b", proportion=0.5),
                CategoryShare(category="a", proportion=0.5),
            ),
            "sorted order",
        ),
        ((CategoryShare(category="a", proportion=0.9),), "sum to one"),
    ],
)
def test_distribution_snapshot_rejects_noncanonical_categories(
    categories: tuple[CategoryShare, ...], expected: str
) -> None:
    with pytest.raises(ValidationError, match=expected):
        DistributionSnapshot(sample_size=10, categories=categories)


def test_mechanism_evidence_requires_aligned_aggregate_sources() -> None:
    with pytest.raises(ValidationError, match="same categories"):
        DataDriftDiagnosisEvidence(
            performance=_performance(),
            reference_distribution=_distribution(100, 0.6),
            observed_distribution=DistributionSnapshot(
                sample_size=100,
                categories=(CategoryShare(category="unknown", proportion=1.0),),
            ),
            population_stability_index=0.2,
        )

    label = _label_evidence()
    lower, upper = wilson_interval(successes=10, trials=99)
    with pytest.raises(ValidationError, match="same sample"):
        LabelDiagnosisEvidence(
            performance=label.performance,
            target_distribution_comparison=label.target_distribution_comparison,
            target_quality_audit_summary=TargetQualityAudit(
                schema_version="p2-target-quality-audit/v1",
                audited_record_count=99,
                disagreeing_record_count=10,
                disagreement_rate=10 / 99,
                disagreement_rate_lower_bound=lower,
                disagreement_rate_upper_bound=upper,
                interval_method="wilson-score/95",
                protocol_version="target-quality-audit/v1",
            ),
        )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {
                "reference_signature_sha256": _HEX["a"],
                "observed_signature_sha256": _HEX["a"],
                "signatures_equal": False,
            },
            "derived",
        ),
        (
            {
                "reference_signature_sha256": _HEX["a"],
                "observed_signature_sha256": _HEX["b"],
                "signatures_equal": True,
            },
            "derived",
        ),
    ],
)
def test_transform_signature_equality_is_derived(
    payload: dict[str, object], expected: str
) -> None:
    with pytest.raises(ValidationError, match=expected):
        TransformSignatureComparison(**payload)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("count", "rate", "reference", "observed", "expected"),
    [
        (11, 1.1, _HEX["a"], _HEX["b"], "less than or equal to 1"),
        (2, 0.1, _HEX["a"], _HEX["b"], "derived from the two counts"),
        (0, 0.0, _HEX["a"], _HEX["b"], "signatures and differing count"),
        (1, 0.1, _HEX["a"], _HEX["a"], "signatures and differing count"),
    ],
)
def test_target_projection_rejects_inconsistent_aggregate_claims(
    count: int,
    rate: float,
    reference: str,
    observed: str,
    expected: str,
) -> None:
    with pytest.raises(ValidationError, match=expected):
        TargetProjectionComparison(
            sample_size=10,
            differing_record_count=count,
            difference_rate=rate,
            reference_projection_sha256=reference,
            observed_projection_sha256=observed,
        )


def test_schema_equality_requires_equal_field_counts() -> None:
    with pytest.raises(ValidationError, match="equal field counts"):
        SchemaComparison(
            reference_field_count=10,
            observed_field_count=11,
            field_sets_equal=True,
        )


@pytest.mark.parametrize("fault_type", list(_EVIDENCE_FACTORY))
def test_non_benign_family_requires_observable_evidence(fault_type: str) -> None:
    candidate = _candidate(fault_type)
    with pytest.raises(EvidenceProjectionError, match="requires observable evidence"):
        build_diagnosis_contexts(
            candidate=candidate,
            family=_family(candidate),
            evidence=None,
        )


def test_family_binding_rejects_candidate_mechanism_and_fingerprint_replay() -> None:
    candidate = _candidate("data_drift")
    base = _family(candidate)
    mutations = (
        base.model_copy(update={"candidate_id": f"p2-candidate-{'a' * 64}"}),
        base.model_copy(update={"fault_type": "label_noise"}),
        base.model_copy(update={"proposed_family_sha256": _HEX["f"]}),
    )
    for family, expected, error_type in zip(
        mutations,
        ("different candidate", "mechanism differs", "namespaced family fingerprint"),
        (EvidenceProjectionError, EvidenceProjectionError, ValidationError),
        strict=True,
    ):
        with pytest.raises(error_type, match=expected):
            build_diagnosis_contexts(
                candidate=candidate,
                family=family,
                evidence=_drift_evidence(),
            )
