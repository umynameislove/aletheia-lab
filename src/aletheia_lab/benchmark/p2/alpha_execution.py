"""Execute the frozen Phase 2 alpha plan against the real baseline system.

The mechanism modules own intervention construction and validation; the
lifecycle module owns classification and admission. This module is the narrow
bridge between them: it derives one frozen runtime from the processed dataset,
runs measured predictions, validates every mechanism artifact, and hands only
validated candidates to the lifecycle.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import pandas as pd
from sklearn.base import clone  # type: ignore[import-untyped]
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]

from aletheia_lab.baseline.artifacts import sha256_file
from aletheia_lab.baseline.loader import LoadedSplits, load_processed, split_dataset
from aletheia_lab.baseline.model import build_pipeline
from aletheia_lab.baseline.run import resolve_settings
from aletheia_lab.baseline.schema import FEATURE_COLUMNS, NUMERIC_FEATURES
from aletheia_lab.benchmark.p2.alpha_lifecycle import (
    EvaluatedAlphaCandidate,
    assemble_alpha_artifacts,
    execution_for_slot,
)
from aletheia_lab.benchmark.p2.alpha_plan import AlphaSystemBinding, build_frozen_alpha_plan
from aletheia_lab.benchmark.p2.artifacts import P2ContractArtifacts
from aletheia_lab.benchmark.p2.binary_evaluation import (
    CleanTestSet,
    MetricComparison,
    PredictionVector,
    compare_binary_metrics,
)
from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.contracts import (
    CandidatePlan,
    CandidateSlot,
    DuplicateAudit,
    TechnicalDispositionEntry,
)
from aletheia_lab.benchmark.p2.data_drift import (
    CategoricalDriftResult,
    CategoricalDriftSpec,
    DriftEvaluationSource,
    apply_categorical_drift,
    apply_empirical_resampling_control,
    build_drift_observed_evaluation_set,
)
from aletheia_lab.benchmark.p2.data_drift_family import (
    DriftBenignControlInputs,
    DriftCandidatePackage,
    DriftFaultDirectedInputs,
    DriftPredictionEvidence,
    build_drift_candidate_package,
    build_drift_prediction_run,
)
from aletheia_lab.benchmark.p2.evidence_projection import (
    CategoryShare,
    DataDriftDiagnosisEvidence,
    DistributionSnapshot,
    LabelDiagnosisEvidence,
    PreprocessingDiagnosisEvidence,
    SchemaComparison,
    SecondaryComparison,
    TargetProjectionComparison,
    TransformSignatureComparison,
    performance_evidence_from,
)
from aletheia_lab.benchmark.p2.identity import (
    DataDriftParameters,
    LabelNoiseParameters,
    PreprocessingBugParameters,
)
from aletheia_lab.benchmark.p2.label_controls import (
    PredictionEquivalenceEvidence,
    PredictionEvaluationSource,
    SemanticTargetSource,
    SerializationControlSpec,
    apply_label_repair,
    apply_serialization_roundtrip,
)
from aletheia_lab.benchmark.p2.label_noise import (
    LabelCorruptionResult,
    LabelCorruptionSpec,
    LabelNoiseSource,
    apply_label_corruption,
    diagnosis_projection,
)
from aletheia_lab.benchmark.p2.mechanism_validation import (
    LabelBenignControlInputs,
    LabelFaultDirectedInputs,
    LabelRepairControlInputs,
    ValidatedMechanismCandidate,
    validate_mechanism_candidate,
)
from aletheia_lab.benchmark.p2.preprocessing_controls import (
    BenignEquivalenceEvidence,
    ColumnPermutationSpec,
    EncoderMappingRepairSpec,
    NamedFeatureRow,
    NamedFeatureTable,
    TransformedMatrix,
    apply_column_permutation,
    apply_encoder_mapping_repair,
)
from aletheia_lab.benchmark.p2.preprocessing_family import (
    BenignControlInputs,
    FaultDirectedInputs,
    RepairControlInputs,
    build_preprocessing_candidate_package,
)
from aletheia_lab.benchmark.p2.preprocessing_intervention import (
    CategoryFrequency,
    EncoderMappingMismatchSpec,
    FrozenCategoryVocabulary,
    InferenceTransformSource,
    apply_encoder_mapping_mismatch,
)

_SNAPSHOT_ID = "telco_customer_churn@2026-07"
_REFERENCE_ID = "clean-test-reference/v1"


@dataclass(frozen=True)
class AlphaRuntime:
    """Authoritative in-memory inputs shared by all 15 primary executions."""

    splits: LoadedSplits
    clean_pipeline: Pipeline
    clean_predictions: tuple[int, ...]
    plan: CandidatePlan
    test_set: CleanTestSet
    drift_source: DriftEvaluationSource
    label_source: LabelNoiseSource
    preprocessing_source: InferenceTransformSource
    dataset_sha256: str
    split_sha256: str
    model_sha256: str
    preprocessing_sha256: str
    test_feature_sha256: str
    test_target_sha256: str


def _frame_sha256(frame: pd.DataFrame) -> str:
    return canonical_sha256(
        {
            "columns": [str(column) for column in frame.columns],
            "rows": frame.astype(object).where(pd.notna(frame), None).values.tolist(),
        }
    )


def _target_sha256(record_ids: tuple[str, ...], labels: tuple[int, ...]) -> str:
    return canonical_sha256({"record_ids": list(record_ids), "labels": list(labels)})


def _predictions(pipeline: Pipeline, frame: pd.DataFrame) -> tuple[int, ...]:
    return tuple(int(value) for value in pipeline.predict(frame))


def _vector(values: tuple[int, ...], role: str) -> PredictionVector:
    return PredictionVector(
        schema_version="p2-prediction-vector/v1",
        role=cast(Any, role),
        predictions=values,
    )


def _secondary() -> SecondaryComparison:
    """A measured structural invariant used only as pinned noisy evidence."""

    return SecondaryComparison(
        reference_value=float(len(FEATURE_COLUMNS)),
        observed_value=float(len(FEATURE_COLUMNS)),
        absolute_delta=0.0,
        stability_bound=0.01,
    )


def prepare_alpha_runtime(config_path: str | Path = "configs/project.yaml") -> AlphaRuntime:
    """Load, attest, split and fit the baseline once for the complete alpha run."""

    settings = resolve_settings(config_path)
    frame = load_processed(settings.processed_path)
    dataset_sha256 = sha256_file(settings.processed_path)
    splits = split_dataset(
        frame,
        dataset_id=settings.dataset_id,
        dataset_sha256=dataset_sha256,
        seed=settings.seed,
        ratios=settings.ratios,
        stratified=settings.stratified,
    )
    split_sha256 = canonical_sha256(splits.manifest.model_dump(mode="json"))
    model_sha256 = canonical_sha256(asdict(settings.model))
    preprocessing_sha256 = canonical_sha256(
        {"protocol": "baseline-preprocessing/v1", "features": list(FEATURE_COLUMNS)}
    )
    record_ids = tuple(str(value) for value in splits.test.ids)
    labels = tuple(int(value) for value in splits.test.target)
    test_feature_sha256 = _frame_sha256(splits.test.features)
    test_target_sha256 = _target_sha256(record_ids, labels)

    clean_pipeline = build_pipeline(settings.model)
    clean_pipeline.fit(splits.train.features, splits.train.target)
    clean_predictions = _predictions(clean_pipeline, splits.test.features)
    test_set = CleanTestSet(
        schema_version="p2-clean-test-set/v1",
        split="test",
        record_ids=record_ids,
        attested_true_labels=labels,
        attested_test_feature_matrix_sha256=test_feature_sha256,
        attested_target_sha256=test_target_sha256,
        attested_split_manifest_sha256=split_sha256,
        attested_model_sha256=model_sha256,
    )
    drift_source = DriftEvaluationSource(
        schema_version="p2-drift-evaluation-source/v1",
        split="test",
        dataset_snapshot_id=_SNAPSHOT_ID,
        dataset_sha256=dataset_sha256,
        model_data_split_manifest_sha256=split_sha256,
        feature="Contract",
        record_ids=record_ids,
        feature_values=tuple(str(value) for value in splits.test.features["Contract"]),
        attested_raw_feature_matrix_sha256=test_feature_sha256,
        attested_raw_target_sha256=test_target_sha256,
        attested_model_sha256=model_sha256,
        attested_preprocessing_specification_sha256=preprocessing_sha256,
    )
    train_ids = tuple(str(value) for value in splits.train.ids)
    train_labels = tuple(int(value) for value in splits.train.target)
    label_source = LabelNoiseSource(
        schema_version="p2-label-source/v1",
        split="train",
        record_ids=train_ids,
        targets=train_labels,
        attested_feature_matrix_sha256=_frame_sha256(splits.train.features),
        attested_preprocessing_specification_sha256=preprocessing_sha256,
        attested_model_specification_sha256=model_sha256,
    )
    counts = splits.train.features["Contract"].value_counts().to_dict()
    vocabulary = FrozenCategoryVocabulary(
        schema_version="p2-category-vocabulary/v1",
        feature="Contract",
        split="train",
        rank_rule="count-desc-then-lexical/v1",
        frequencies=tuple(
            CategoryFrequency(category=str(category), count=int(count))
            for category, count in counts.items()
        ),
    )
    preprocessing_source = InferenceTransformSource(
        schema_version="p2-inference-transform-source/v1",
        split="test",
        feature="Contract",
        record_ids=record_ids,
        raw_categories=tuple(str(value) for value in splits.test.features["Contract"]),
        vocabulary=vocabulary,
        attested_raw_feature_matrix_sha256=test_feature_sha256,
        attested_raw_target_sha256=test_target_sha256,
        attested_model_sha256=model_sha256,
        attested_fitted_training_transform_sha256=preprocessing_sha256,
        attested_other_transform_config_sha256=preprocessing_sha256,
    )
    empirical = drift_source.observed_distribution()
    binding = AlphaSystemBinding(
        dataset_snapshot_id=_SNAPSHOT_ID,
        dataset_sha256=dataset_sha256,
        model_data_split_manifest_sha256=split_sha256,
        model_specification_sha256=model_sha256,
        preprocessing_specification_sha256=preprocessing_sha256,
        reference_construction_id=_REFERENCE_ID,
        data_drift_injector_contract_version="categorical-distribution-shift/v1",
        label_noise_injector_contract_version="label-corruption/v1",
        preprocessing_injector_contract_version="encoder-mapping-mismatch/v1",
        empirical_contract_distribution=empirical,
        data_drift_output_size=len(record_ids),
    )
    return AlphaRuntime(
        splits=splits,
        clean_pipeline=clean_pipeline,
        clean_predictions=clean_predictions,
        plan=build_frozen_alpha_plan(binding),
        test_set=test_set,
        drift_source=drift_source,
        label_source=label_source,
        preprocessing_source=preprocessing_source,
        dataset_sha256=dataset_sha256,
        split_sha256=split_sha256,
        model_sha256=model_sha256,
        preprocessing_sha256=preprocessing_sha256,
        test_feature_sha256=test_feature_sha256,
        test_target_sha256=test_target_sha256,
    )


def _validated(
    *, artifact: Any, slot: CandidateSlot, inputs: Any, disposition: Any
) -> ValidatedMechanismCandidate:
    execution = execution_for_slot(slot)
    return validate_mechanism_candidate(
        artifact,
        slot=slot,
        inputs=inputs,
        execution=execution,
        disposition=disposition,
    )


def _drift_evidence(
    package: DriftCandidatePackage,
    result: CategoricalDriftResult,
) -> DataDriftDiagnosisEvidence:
    measurement = package.measurement
    result_distribution = result.achieved_distribution
    reference_distribution = result.reference_distribution

    def snapshot(distribution: tuple[tuple[str, float], ...], size: int) -> DistributionSnapshot:
        return DistributionSnapshot(
            sample_size=size,
            categories=tuple(
                CategoryShare(category=category, proportion=proportion)
                for category, proportion in sorted(distribution)
            ),
        )

    return DataDriftDiagnosisEvidence(
        performance=performance_evidence_from(measurement.comparison),
        reference_distribution=snapshot(
            reference_distribution, measurement.comparison.reference.prediction_count
        ),
        observed_distribution=snapshot(
            result_distribution, measurement.comparison.observed.prediction_count
        ),
        population_stability_index=measurement.population_stability_index,
        secondary_comparison=(
            _secondary()
            if measurement.comparison.measured_primary_outcome == "regression"
            else None
        ),
    )


def _execute_drift(slot: CandidateSlot, runtime: AlphaRuntime) -> EvaluatedAlphaCandidate:
    parameters = cast(DataDriftParameters, slot.identity.canonical_intervention_parameters)
    spec = CategoricalDriftSpec(injection_id=slot.slot_id, parameters=parameters, seed=slot.identity.seed)
    apply = (
        apply_empirical_resampling_control
        if slot.role == "designed_benign_control"
        else apply_categorical_drift
    )
    result = apply(source=runtime.drift_source, spec=spec, slot=slot)
    indexed = runtime.splits.test.features.copy()
    indexed.index = list(runtime.test_set.record_ids)
    observed_frame = indexed.loc[list(result.selected_record_ids)].reset_index(drop=True)
    observed_set = build_drift_observed_evaluation_set(
        result=result,
        source=runtime.drift_source,
        test_set=runtime.test_set,
        attested_drifted_feature_matrix_sha256=_frame_sha256(observed_frame),
    )
    prediction_evidence = DriftPredictionEvidence(
        reference_run=build_drift_prediction_run(
            role="reference",
            model_specification_sha256=runtime.model_sha256,
            evaluation_source_sha256=runtime.test_set.artifact_sha256(),
            predictions=_vector(runtime.clean_predictions, "reference"),
        ),
        observed_run=build_drift_prediction_run(
            role="observed",
            model_specification_sha256=runtime.model_sha256,
            evaluation_source_sha256=observed_set.artifact_sha256(),
            predictions=_vector(_predictions(runtime.clean_pipeline, observed_frame), "observed"),
        ),
    )
    bundle_type = (
        DriftBenignControlInputs
        if slot.role == "designed_benign_control"
        else DriftFaultDirectedInputs
    )
    inputs = bundle_type(
        source=runtime.drift_source,
        spec=spec,
        result=result,
        test_set=runtime.test_set,
        observed_set=observed_set,
        predictions=prediction_evidence,
    )
    package = build_drift_candidate_package(slot=slot, inputs=inputs)
    candidate = _validated(
        artifact=package, slot=slot, inputs=inputs, disposition=package.disposition
    )
    benign = slot.role == "designed_benign_control"
    return EvaluatedAlphaCandidate(
        candidate=candidate,
        comparison=package.measurement.comparison,
        diagnosis_evidence=None if benign else _drift_evidence(package, result),
        equivalence_checks_passed=True if benign else None,
    )


def _fit_with_targets(runtime: AlphaRuntime, targets: tuple[int, ...]) -> Pipeline:
    pipeline = cast(Pipeline, clone(runtime.clean_pipeline))
    pipeline.fit(runtime.splits.train.features, pd.Series(targets))
    return pipeline


def _label_evidence(
    result: LabelCorruptionResult,
    source: LabelNoiseSource,
    spec: LabelCorruptionSpec,
    comparison: MetricComparison,
) -> LabelDiagnosisEvidence:
    projection = diagnosis_projection(result, source=source, spec=spec)
    return LabelDiagnosisEvidence(
        performance=performance_evidence_from(comparison),
        target_distribution_comparison=projection.target_distribution_comparison,
        target_quality_audit_summary=projection.target_quality_audit,
        secondary_comparison=(
            _secondary() if comparison.measured_primary_outcome == "regression" else None
        ),
    )


def _execute_label_fault(slot: CandidateSlot, runtime: AlphaRuntime) -> EvaluatedAlphaCandidate:
    parameters = cast(LabelNoiseParameters, slot.identity.canonical_intervention_parameters)
    spec = LabelCorruptionSpec(parameters=parameters, seed=slot.identity.seed)
    result = apply_label_corruption(source=runtime.label_source, spec=spec)
    inputs = LabelFaultDirectedInputs(source=runtime.label_source, spec=spec)
    observed = _predictions(
        _fit_with_targets(runtime, result.mutated_targets), runtime.splits.test.features
    )
    comparison = compare_binary_metrics(
        test_set=runtime.test_set,
        reference_predictions=_vector(runtime.clean_predictions, "reference"),
        observed_predictions=_vector(observed, "observed"),
    )
    disposition = execution_for_slot(slot)
    candidate = _validated(
        artifact=result,
        slot=slot,
        inputs=inputs,
        disposition=TechnicalDispositionEntry(
            candidate_id=disposition.candidate_id,
            disposition="technically_valid",
        ),
    )
    return EvaluatedAlphaCandidate(
        candidate=candidate,
        comparison=comparison,
        diagnosis_evidence=_label_evidence(result, runtime.label_source, spec, comparison),
    )


def _execute_label_repair(slot: CandidateSlot, runtime: AlphaRuntime) -> EvaluatedAlphaCandidate:
    parameters = cast(LabelNoiseParameters, slot.identity.canonical_intervention_parameters)
    spec = LabelCorruptionSpec(parameters=parameters, seed=slot.identity.seed)
    corrupted = apply_label_corruption(source=runtime.label_source, spec=spec)
    repaired = apply_label_repair(
        source=runtime.label_source, corrupted=corrupted, spec=spec, slot=slot
    )
    inputs = LabelRepairControlInputs(
        source=runtime.label_source, corrupted=corrupted, spec=spec
    )
    comparison = compare_binary_metrics(
        test_set=runtime.test_set,
        reference_predictions=_vector(
            _predictions(
                _fit_with_targets(runtime, corrupted.mutated_targets),
                runtime.splits.test.features,
            ),
            "reference",
        ),
        observed_predictions=_vector(
            _predictions(
                _fit_with_targets(runtime, repaired.repaired_targets),
                runtime.splits.test.features,
            ),
            "observed",
        ),
    )
    execution = execution_for_slot(slot)
    candidate = _validated(
        artifact=repaired,
        slot=slot,
        inputs=inputs,
        disposition=TechnicalDispositionEntry(
            candidate_id=execution.candidate_id,
            disposition="technically_valid",
        ),
    )
    return EvaluatedAlphaCandidate(
        candidate=candidate,
        comparison=comparison,
        diagnosis_evidence=_label_evidence(corrupted, runtime.label_source, spec, comparison),
    )


def _execute_label_benign(slot: CandidateSlot, runtime: AlphaRuntime) -> EvaluatedAlphaCandidate:
    parameters = cast(LabelNoiseParameters, slot.identity.canonical_intervention_parameters)
    semantic_source = SemanticTargetSource(
        schema_version="p2-semantic-target-source/v1",
        split="train",
        record_ids=runtime.label_source.record_ids,
        targets=tuple("Yes" if value == 1 else "No" for value in runtime.label_source.targets),
        attested_feature_matrix_sha256=runtime.label_source.attested_feature_matrix_sha256,
        attested_preprocessing_specification_sha256=runtime.preprocessing_sha256,
        attested_model_specification_sha256=runtime.model_sha256,
    )
    spec = SerializationControlSpec(parameters=parameters, seed=slot.identity.seed)
    result = apply_serialization_roundtrip(source=semantic_source, spec=spec, slot=slot)
    roundtrip_targets = tuple(1 if value == "Yes" else 0 for value in result.decoded_targets)
    roundtrip_predictions = _predictions(
        _fit_with_targets(runtime, roundtrip_targets), runtime.splits.test.features
    )
    evaluation_source = PredictionEvaluationSource(
        schema_version="p2-prediction-evaluation-source/v1",
        split="test",
        record_ids=runtime.test_set.record_ids,
        true_labels=runtime.test_set.attested_true_labels,
        attested_test_feature_matrix_sha256=runtime.test_feature_sha256,
        attested_split_manifest_sha256=runtime.split_sha256,
    )
    evidence = PredictionEquivalenceEvidence(
        schema_version="p2-prediction-equivalence-evidence/v1",
        roundtrip_artifact_sha256=result.artifact_sha256(),
        evaluation_source_sha256=evaluation_source.artifact_sha256(),
        record_ids=runtime.test_set.record_ids,
        true_labels=runtime.test_set.attested_true_labels,
        reference_predictions=runtime.clean_predictions,
        roundtrip_predictions=roundtrip_predictions,
    )
    inputs = LabelBenignControlInputs(
        source=semantic_source,
        spec=spec,
        evaluation_source=evaluation_source,
        prediction_evidence=evidence,
    )
    execution = execution_for_slot(slot)
    candidate = _validated(
        artifact=result,
        slot=slot,
        inputs=inputs,
        disposition=TechnicalDispositionEntry(
            candidate_id=execution.candidate_id,
            disposition="technically_valid",
        ),
    )
    comparison = compare_binary_metrics(
        test_set=runtime.test_set,
        reference_predictions=_vector(runtime.clean_predictions, "reference"),
        observed_predictions=_vector(roundtrip_predictions, "observed"),
    )
    return EvaluatedAlphaCandidate(
        candidate=candidate,
        comparison=comparison,
        equivalence_checks_passed=True,
    )


def _frame_with_contract(frame: pd.DataFrame, values: tuple[str, ...]) -> pd.DataFrame:
    changed = frame.copy()
    changed["Contract"] = list(values)
    return changed


def _preprocessing_evidence(
    comparison: MetricComparison,
    reference: tuple[tuple[int, ...], ...],
    observed: tuple[tuple[int, ...], ...],
) -> PreprocessingDiagnosisEvidence:
    reference_hash = canonical_sha256(reference)
    observed_hash = canonical_sha256(observed)
    differing = sum(left != right for left, right in zip(reference, observed, strict=True))
    return PreprocessingDiagnosisEvidence(
        performance=performance_evidence_from(comparison),
        transform_signature_comparison=TransformSignatureComparison(
            reference_signature_sha256=reference_hash,
            observed_signature_sha256=observed_hash,
            signatures_equal=reference_hash == observed_hash,
        ),
        target_projection_comparison=TargetProjectionComparison(
            sample_size=len(reference),
            differing_record_count=differing,
            difference_rate=differing / len(reference),
            reference_projection_sha256=reference_hash,
            observed_projection_sha256=observed_hash,
        ),
        schema_comparison=SchemaComparison(
            reference_field_count=len(reference[0]),
            observed_field_count=len(observed[0]),
            field_sets_equal=True,
        ),
        secondary_comparison=(
            _secondary() if comparison.measured_primary_outcome == "regression" else None
        ),
    )


def _rank_categories(source: InferenceTransformSource, parameters: PreprocessingBugParameters) -> tuple[str, str]:
    if parameters.source_rank is None or parameters.mapped_rank is None:
        raise ValueError("mapping candidate requires two category ranks")
    return (
        source.vocabulary.category_for_rank(parameters.source_rank),
        source.vocabulary.category_for_rank(parameters.mapped_rank),
    )


def _execute_preprocessing_mapping(
    slot: CandidateSlot, runtime: AlphaRuntime
) -> EvaluatedAlphaCandidate:
    parameters = cast(PreprocessingBugParameters, slot.identity.canonical_intervention_parameters)
    source_category, mapped_category = _rank_categories(runtime.preprocessing_source, parameters)
    if slot.role == "designed_improvement_control":
        repair_spec = EncoderMappingRepairSpec(
            injection_id=slot.slot_id,
            parameters=parameters,
            source_category=source_category,
            mapped_category=mapped_category,
            seed=slot.identity.seed,
        )
        repair_result = apply_encoder_mapping_repair(
            source=runtime.preprocessing_source, spec=repair_spec, slot=slot
        )
        reference_predictions = _predictions(
            runtime.clean_pipeline,
            _frame_with_contract(
                runtime.splits.test.features, repair_result.mismatched_reference_view
            ),
        )
        observed_predictions = runtime.clean_predictions
        repair_inputs = RepairControlInputs(
            source=runtime.preprocessing_source,
            spec=repair_spec,
            result=repair_result,
            test_set=runtime.test_set,
            mismatched_reference_predictions=_vector(reference_predictions, "reference"),
            repaired_predictions=_vector(observed_predictions, "observed"),
        )
        package = build_preprocessing_candidate_package(slot=slot, inputs=repair_inputs)
        comparison = cast(MetricComparison, package.metric_comparison)
        candidate = _validated(
            artifact=package,
            slot=slot,
            inputs=repair_inputs,
            disposition=package.disposition,
        )
        return EvaluatedAlphaCandidate(
            candidate=candidate,
            comparison=comparison,
            diagnosis_evidence=_preprocessing_evidence(
                comparison,
                repair_result.mismatched_reference_block,
                repair_result.repaired_block,
            ),
        )

    mismatch_spec = EncoderMappingMismatchSpec(
        injection_id=slot.slot_id,
        parameters=parameters,
        source_category=source_category,
        mapped_category=mapped_category,
        seed=slot.identity.seed,
    )
    mismatch_result = apply_encoder_mapping_mismatch(
        source=runtime.preprocessing_source, spec=mismatch_spec, slot=slot
    )
    observed_predictions = _predictions(
        runtime.clean_pipeline,
        _frame_with_contract(
            runtime.splits.test.features, mismatch_result.inference_view_categories
        ),
    )
    mismatch_inputs = FaultDirectedInputs(
        source=runtime.preprocessing_source,
        spec=mismatch_spec,
        result=mismatch_result,
        test_set=runtime.test_set,
        clean_reference_predictions=_vector(runtime.clean_predictions, "reference"),
        mismatched_predictions=_vector(observed_predictions, "observed"),
    )
    package = build_preprocessing_candidate_package(slot=slot, inputs=mismatch_inputs)
    comparison = cast(MetricComparison, package.metric_comparison)
    candidate = _validated(
        artifact=package, slot=slot, inputs=mismatch_inputs, disposition=package.disposition
    )
    return EvaluatedAlphaCandidate(
        candidate=candidate,
        comparison=comparison,
        diagnosis_evidence=_preprocessing_evidence(
            comparison, mismatch_result.reference_block, mismatch_result.mismatched_block
        ),
    )


def _named_table(runtime: AlphaRuntime) -> NamedFeatureTable:
    frame = runtime.splits.test.features
    return NamedFeatureTable(
        schema_version="p2-named-feature-table/v1",
        feature_names=tuple(str(column) for column in frame.columns),
        rows=tuple(
            NamedFeatureRow(
                record_id=record_id,
                values=tuple(str(value) for value in row),
            )
            for record_id, row in zip(
                runtime.test_set.record_ids, frame.itertuples(index=False, name=None), strict=True
            )
        ),
    )


def _table_frame(table: NamedFeatureTable) -> pd.DataFrame:
    frame = pd.DataFrame([row.values for row in table.rows], columns=table.feature_names)
    for column in NUMERIC_FEATURES:
        frame[column] = pd.to_numeric(frame[column])
    return frame


def _transformed(pipeline: Pipeline, frame: pd.DataFrame) -> TransformedMatrix:
    transformer = pipeline.named_steps["preprocess"]
    matrix = transformer.transform(frame)
    if hasattr(matrix, "toarray"):
        matrix = matrix.toarray()
    return TransformedMatrix(
        schema_version="p2-transformed-matrix/v1",
        column_names=tuple(str(value) for value in transformer.get_feature_names_out()),
        rows=tuple(tuple(float(value) for value in row) for row in matrix),
    )


def _execute_preprocessing_benign(
    slot: CandidateSlot, runtime: AlphaRuntime
) -> EvaluatedAlphaCandidate:
    parameters = cast(PreprocessingBugParameters, slot.identity.canonical_intervention_parameters)
    table = _named_table(runtime)
    spec = ColumnPermutationSpec(
        injection_id=slot.slot_id, parameters=parameters, seed=slot.identity.seed
    )
    result = apply_column_permutation(table=table, spec=spec, slot=slot)
    original_frame = _table_frame(result.original_table)
    permuted_frame = _table_frame(result.permuted_table)
    reference_predictions = _predictions(runtime.clean_pipeline, original_frame)
    observed_predictions = _predictions(runtime.clean_pipeline, permuted_frame)
    evidence = BenignEquivalenceEvidence(
        schema_version="p2-benign-equivalence-evidence/v1",
        permutation_artifact_sha256=result.artifact_sha256(),
        source_table_sha256=table.artifact_sha256(),
        record_ids_sha256=table.record_ids_sha256(),
        evaluation_source_sha256=runtime.test_set.artifact_sha256(),
        attested_model_sha256=runtime.model_sha256,
        preprocessing_specification_sha256=runtime.preprocessing_sha256,
        original_transformed=_transformed(runtime.clean_pipeline, original_frame),
        permuted_transformed=_transformed(runtime.clean_pipeline, permuted_frame),
        reference_predictions=_vector(reference_predictions, "reference"),
        observed_predictions=_vector(observed_predictions, "observed"),
    )
    inputs = BenignControlInputs(
        table=table, spec=spec, result=result, test_set=runtime.test_set, evidence=evidence
    )
    package = build_preprocessing_candidate_package(slot=slot, inputs=inputs)
    comparison = cast(MetricComparison, package.metric_comparison)
    candidate = _validated(
        artifact=package, slot=slot, inputs=inputs, disposition=package.disposition
    )
    return EvaluatedAlphaCandidate(
        candidate=candidate,
        comparison=comparison,
        equivalence_checks_passed=True,
    )


def execute_alpha_slot(slot: CandidateSlot, runtime: AlphaRuntime) -> EvaluatedAlphaCandidate:
    """Execute one frozen slot without inferring behavior from its identifier."""

    if slot.fault_type == "data_drift":
        return _execute_drift(slot, runtime)
    if slot.fault_type == "label_noise":
        if slot.role == "designed_improvement_control":
            return _execute_label_repair(slot, runtime)
        if slot.role == "designed_benign_control":
            return _execute_label_benign(slot, runtime)
        return _execute_label_fault(slot, runtime)
    if slot.role == "designed_benign_control":
        return _execute_preprocessing_benign(slot, runtime)
    return _execute_preprocessing_mapping(slot, runtime)


def execute_primary_alpha(runtime: AlphaRuntime) -> P2ContractArtifacts:
    """Run all 15 primary slots and assemble the validated nine-file bundle."""

    results = tuple(
        execute_alpha_slot(slot, runtime)
        for slot in runtime.plan.slots
        if slot.slot_kind == "primary"
    )
    return assemble_alpha_artifacts(
        plan=runtime.plan,
        results=results,
        duplicate_audit=DuplicateAudit(schema_version="p2-duplicate-audit/1", findings=()),
    )
