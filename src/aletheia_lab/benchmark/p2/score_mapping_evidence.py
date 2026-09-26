"""Development-only evidence views for evaluator mapping and its target rival.

The projection contains observations, never the internal cause, selector,
dose, row IDs, or source hashes. Its source is a Session-4 witness; the
authenticity of the upstream model and label ledger remains a caller duty.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal, TypeAlias, cast

import numpy as np
from numpy.typing import NDArray

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import CalibrationResult
from aletheia_lab.benchmark.p2.confirmatory_v3_shift import (
    reference_prior_standardized_log_loss,
)
from aletheia_lab.benchmark.p2.score_mapping_intervention import (
    BinaryProbabilityModel,
    EvaluatorScoreSource,
    MappingInterventionResult,
)
from aletheia_lab.benchmark.p2.score_mapping_verification import (
    IndependentScoreWitness,
    SourceArtifactPaths,
    verify_evaluator_mapping,
)

EvidenceCondition: TypeAlias = Literal["full", "missing_key", "noisy", "misleading"]
_SCHEMA_VERSION = "diagnosis-development-projection/v1"
_DISPLAY_DECIMALS = 12


class ScoreMappingEvidenceError(ValueError):
    """The development evidence is inconsistent or leaks its answer."""


@dataclass(frozen=True, slots=True)
class DevelopmentObservation:
    """Internally checked measurements; never serialize this object directly."""

    record_count: int
    reference_log_loss: float
    observed_log_loss: float
    model_classes: tuple[int, int]
    evaluator_classes: tuple[int, int]
    example_score_pair: tuple[float, float]
    example_observed_positive: float
    example_corrected_positive: float
    target_binding_matches_source: bool
    corrected_log_loss: float
    feature_width: int
    reference_feature_count: int
    feature_mean_distance: float
    source_identity_sha256: str
    reference_features_sha256: str


@dataclass(frozen=True, slots=True)
class PairwiseProjectionAudit:
    """A scoped matched-pair result, not G1/G2 admission for a population."""

    construct_loci_differ: bool
    full_witness_distinguishes: bool
    missing_payloads_identical: bool
    symptom_only_balanced_accuracy_ceiling: float


Projection: TypeAlias = dict[str, object]
ProjectionBundle: TypeAlias = dict[EvidenceCondition, Projection]


def _source_matches(witness: IndependentScoreWitness, source: EvaluatorScoreSource) -> None:
    if not isinstance(witness, IndependentScoreWitness) or not isinstance(
        source, EvaluatorScoreSource
    ):
        raise ScoreMappingEvidenceError("an independent witness and typed source are required")
    ids = witness.record_ids
    if (
        source.dataset_id != witness.dataset_id
        or source.record_ids != ids
        or source.model_classes != witness.model_classes
        or source.raw_score_rows != witness.raw_score_rows
        or source.calibrated_score_rows != witness.calibrated_score_rows
        or source.calibration.canonical_sha256() != witness.calibration_sha256
        or witness.raw_scores_sha256
        != canonical_sha256({"record_ids": ids, "scores": witness.raw_score_rows})
        or witness.calibrated_scores_sha256
        != canonical_sha256({"record_ids": ids, "scores": witness.calibrated_score_rows})
        or witness.target_binding_sha256 != canonical_sha256({"target_rows": witness.target_rows})
    ):
        raise ScoreMappingEvidenceError("source differs from the independent witness")


def _feature_summary(
    witness: IndependentScoreWitness,
    *,
    reference_features: NDArray[np.float64],
    evaluation_features: NDArray[np.float64],
) -> tuple[int, int, float, str]:
    try:
        reference = np.asarray(reference_features, dtype=np.float64)
        evaluation = np.asarray(evaluation_features, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ScoreMappingEvidenceError("feature comparison needs numeric matrices") from exc
    if (
        reference.ndim != 2
        or evaluation.ndim != 2
        or reference.shape[0] == 0
        or evaluation.shape[0] != len(witness.record_ids)
        or reference.shape[1] == 0
        or reference.shape[1] != evaluation.shape[1]
        or not np.isfinite(reference).all()
        or not np.isfinite(evaluation).all()
    ):
        raise ScoreMappingEvidenceError("feature comparison has incompatible rows or columns")
    rows = tuple(
        tuple(0.0 if value == 0.0 else float(value) for value in row) for row in evaluation
    )
    if (
        canonical_sha256({"record_ids": witness.record_ids, "features": rows})
        != witness.feature_matrix_sha256
    ):
        raise ScoreMappingEvidenceError("evaluation features differ from the source witness")
    distance = float(np.linalg.norm(reference.mean(axis=0) - evaluation.mean(axis=0)))
    if not math.isfinite(distance):
        raise ScoreMappingEvidenceError("feature comparison is non-finite")
    reference_rows = tuple(
        tuple(0.0 if value == 0.0 else float(value) for value in row) for row in reference
    )
    return (
        int(evaluation.shape[1]),
        int(reference.shape[0]),
        distance,
        canonical_sha256({"reference_features": reference_rows}),
    )


def _observation(
    witness: IndependentScoreWitness,
    *,
    reference_log_loss: float,
    observed_log_loss: float,
    evaluator_classes: tuple[int, int],
    example_index: int,
    example_observed_positive: float,
    target_binding_matches_source: bool,
    corrected_log_loss: float,
    reference_features: NDArray[np.float64],
    evaluation_features: NDArray[np.float64],
) -> DevelopmentObservation:
    width, reference_count, distance, reference_digest = _feature_summary(
        witness,
        reference_features=reference_features,
        evaluation_features=evaluation_features,
    )
    pair = witness.calibrated_score_rows[example_index]
    return DevelopmentObservation(
        record_count=len(witness.record_ids),
        reference_log_loss=reference_log_loss,
        observed_log_loss=observed_log_loss,
        model_classes=witness.model_classes,
        evaluator_classes=evaluator_classes,
        example_score_pair=pair,
        example_observed_positive=example_observed_positive,
        example_corrected_positive=pair[witness.model_classes.index(1)],
        target_binding_matches_source=target_binding_matches_source,
        corrected_log_loss=corrected_log_loss,
        feature_width=width,
        reference_feature_count=reference_count,
        feature_mean_distance=distance,
        source_identity_sha256=canonical_sha256(
            {
                "dataset_id": witness.dataset_id,
                "record_ids": witness.record_ids,
                "target_binding_sha256": witness.target_binding_sha256,
                "artifact_hashes": (
                    witness.artifact_hashes.dataset,
                    witness.artifact_hashes.split,
                    witness.artifact_hashes.preprocessor,
                    witness.artifact_hashes.fitted_model,
                ),
                "feature_matrix_sha256": witness.feature_matrix_sha256,
                "calibration_sha256": witness.calibration_sha256,
                "raw_scores_sha256": witness.raw_scores_sha256,
                "calibrated_scores_sha256": witness.calibrated_scores_sha256,
            }
        ),
        reference_features_sha256=reference_digest,
    )


def mapping_observation(
    *,
    witness: IndependentScoreWitness,
    source: EvaluatorScoreSource,
    intervention: MappingInterventionResult,
    scoring_target_rows: Sequence[tuple[str, int]],
    reference_model: BinaryProbabilityModel,
    reference_calibration: CalibrationResult,
    artifacts: SourceArtifactPaths,
    reference_features: NDArray[np.float64],
    evaluation_features: NDArray[np.float64],
) -> DevelopmentObservation:
    """Project a nonzero mapping trace only after an independent verification."""

    _source_matches(witness, source)
    if not isinstance(intervention, MappingInterventionResult):
        raise ScoreMappingEvidenceError("a typed intervention is required")
    verification = verify_evaluator_mapping(
        witness=witness,
        source=source,
        intervention=intervention,
        scoring_target_rows=scoring_target_rows,
        reference_model=reference_model,
        evaluation_matrix=evaluation_features,
        reference_calibration=reference_calibration,
        artifacts=artifacts,
    )
    if not verification.changed_score_record_ids:
        raise ScoreMappingEvidenceError("no changed score can furnish a mapping trace")
    ids = witness.record_ids
    targets = tuple(target for _, target in witness.target_rows)
    healthy = tuple(row[witness.model_classes.index(1)] for row in witness.calibrated_score_rows)
    observed = intervention.positive_probabilities
    if (
        intervention.record_ids != ids
        or intervention.changed_score_record_ids != verification.changed_score_record_ids
        or intervention.affected_record_ids != verification.affected_record_ids
        or intervention.faulty_column_classes != tuple(reversed(witness.model_classes))
        or verification.artifact_hashes != witness.artifact_hashes
        or verification.raw_scores_sha256 != witness.raw_scores_sha256
        or verification.calibrated_scores_sha256 != witness.calibrated_scores_sha256
        or verification.target_binding_sha256 != witness.target_binding_sha256
        or verification.corrected_log_loss != verification.healthy_log_loss
        or reference_prior_standardized_log_loss(true_labels=targets, probabilities=healthy)
        != verification.healthy_log_loss
        or reference_prior_standardized_log_loss(true_labels=targets, probabilities=observed)
        != verification.faulty_log_loss
    ):
        raise ScoreMappingEvidenceError("mapping evidence disagrees with independent verification")
    index = ids.index(verification.changed_score_record_ids[0])
    pair = witness.calibrated_score_rows[index]
    if observed[index] != pair[intervention.faulty_column_classes.index(1)]:
        raise ScoreMappingEvidenceError("mapping trace does not decode the original score pair")
    return _observation(
        witness,
        reference_log_loss=verification.healthy_log_loss,
        observed_log_loss=verification.faulty_log_loss,
        evaluator_classes=intervention.faulty_column_classes,
        example_index=index,
        example_observed_positive=observed[index],
        target_binding_matches_source=True,
        corrected_log_loss=verification.corrected_log_loss,
        reference_features=reference_features,
        evaluation_features=evaluation_features,
    )


def target_binding_rival_observation(
    *,
    witness: IndependentScoreWitness,
    source: EvaluatorScoreSource,
    scoring_target_rows: Sequence[tuple[str, int]],
    reference_features: NDArray[np.float64],
    evaluation_features: NDArray[np.float64],
) -> DevelopmentObservation:
    """Construct an adversarial development rival, never an admitted mechanism."""

    _source_matches(witness, source)
    try:
        rows = tuple(tuple(row) for row in scoring_target_rows)
    except TypeError as exc:
        raise ScoreMappingEvidenceError("rival needs ordered row-target bindings") from exc
    if (
        len(rows) != len(witness.record_ids)
        or any(len(row) != 2 for row in rows)
        or tuple(row[0] for row in rows) != witness.record_ids
        or any(type(row[1]) is not int or row[1] not in (0, 1) for row in rows)
        or {row[1] for row in rows} != {0, 1}
        or rows == witness.target_rows
    ):
        raise ScoreMappingEvidenceError("rival targets must change values under the same row IDs")
    positive = tuple(row[witness.model_classes.index(1)] for row in witness.calibrated_score_rows)
    reference_targets = tuple(target for _, target in witness.target_rows)
    observed_targets = tuple(cast(int, target) for _, target in rows)
    reference_loss = reference_prior_standardized_log_loss(
        true_labels=reference_targets, probabilities=positive
    )
    observed_loss = reference_prior_standardized_log_loss(
        true_labels=observed_targets, probabilities=positive
    )
    index = next(
        i
        for i, (source_row, rival_row) in enumerate(zip(witness.target_rows, rows, strict=True))
        if source_row != rival_row
    )
    return _observation(
        witness,
        reference_log_loss=reference_loss,
        observed_log_loss=observed_loss,
        evaluator_classes=witness.model_classes,
        example_index=index,
        example_observed_positive=positive[index],
        target_binding_matches_source=False,
        corrected_log_loss=observed_loss,
        reference_features=reference_features,
        evaluation_features=evaluation_features,
    )


def _item(item_id: str, payload: dict[str, object]) -> dict[str, object]:
    return {"id": item_id, "payload": payload}


def _projection(items: tuple[dict[str, object], ...]) -> Projection:
    return {"schema_version": _SCHEMA_VERSION, "items": deepcopy(list(items))}


def _display_metric(value: float) -> float:
    rounded = round(value, _DISPLAY_DECIMALS)
    return 0.0 if rounded == 0.0 else rounded


def build_development_evidence_views(observation: DevelopmentObservation) -> ProjectionBundle:
    """Materialize four sibling views with condition names kept outside payloads."""

    if not isinstance(observation, DevelopmentObservation):
        raise ScoreMappingEvidenceError("a checked development observation is required")
    pair = observation.example_score_pair
    if (
        observation.record_count < 2
        or observation.reference_feature_count < 1
        or observation.feature_width < 1
        or set(observation.model_classes) != {0, 1}
        or set(observation.evaluator_classes) != {0, 1}
        or len(pair) != 2
        or not all(
            math.isfinite(value)
            for value in (
                *pair,
                observation.example_observed_positive,
                observation.example_corrected_positive,
                observation.reference_log_loss,
                observation.observed_log_loss,
                observation.corrected_log_loss,
                observation.feature_mean_distance,
            )
        )
        or any(value < 0.0 or value > 1.0 for value in pair)
        or not math.isclose(sum(pair), 1.0, rel_tol=0.0, abs_tol=1e-12)
        or observation.example_observed_positive != pair[observation.evaluator_classes.index(1)]
        or observation.example_corrected_positive != pair[observation.model_classes.index(1)]
        or any(
            value < 0.0
            for value in (
                observation.reference_log_loss,
                observation.observed_log_loss,
                observation.corrected_log_loss,
            )
        )
    ):
        raise ScoreMappingEvidenceError("development observation is malformed")
    if observation.feature_mean_distance <= 0.0:
        raise ScoreMappingEvidenceError("misleading view needs a real nonzero competing clue")
    reference = _display_metric(observation.reference_log_loss)
    observed = _display_metric(observation.observed_log_loss)
    performance = _item(
        "performance-comparison",
        {
            "record_count": observation.record_count,
            "reference_log_loss": reference,
            "observed_log_loss": observed,
            "log_loss_change": _display_metric(observed - reference),
        },
    )
    controls = _item(
        "score-source-controls",
        {"score_rows_shared": True, "score_row_count": observation.record_count},
    )
    common = (performance, controls)
    decisive = (
        _item(
            "column-interpretation",
            {
                "model_column_classes": observation.model_classes,
                "evaluator_column_classes": observation.evaluator_classes,
                "example_source_score_pair": observation.example_score_pair,
                "example_observed_positive": observation.example_observed_positive,
                "example_source_positive": observation.example_corrected_positive,
            },
        ),
        _item(
            "target-binding-check",
            {"scoring_targets_match_source_rows": observation.target_binding_matches_source},
        ),
        _item(
            "independent-recomputation",
            {"source_column_decode_log_loss": _display_metric(observation.corrected_log_loss)},
        ),
    )
    full = common + decisive
    noisy = _item(
        "feature-shape-observation",
        {
            "reference_feature_width": observation.feature_width,
            "evaluation_feature_width": observation.feature_width,
        },
    )
    competing = _item(
        "cohort-feature-comparison",
        {
            "provenance": "unmodified_reference_and_evaluation_feature_rows",
            "reference_row_count": observation.reference_feature_count,
            "evaluation_row_count": observation.record_count,
            "mean_vector_distance": observation.feature_mean_distance,
        },
    )
    return {
        "full": _projection(full),
        "missing_key": _projection(common),
        "noisy": _projection(full + (noisy,)),
        "misleading": _projection(full + (competing,)),
    }


def audit_matched_target_rival(
    mapping: DevelopmentObservation,
    rival: DevelopmentObservation,
) -> PairwiseProjectionAudit:
    """Test G1/G2 against one matched adversarial target-binding world.

    Identical missing views cap a balanced deterministic shortcut rule at
    50% on *this pair only*. They do not establish population-level leakage
    resistance or distinguishability against other mechanisms.
    """

    mapping_views = build_development_evidence_views(mapping)
    rival_views = build_development_evidence_views(rival)
    if (
        abs(mapping.reference_log_loss - rival.reference_log_loss) > 1e-12
        or abs(mapping.observed_log_loss - rival.observed_log_loss) > 1e-12
    ):
        raise ScoreMappingEvidenceError("the underlying performance measurements do not match")
    if (
        mapping.model_classes == mapping.evaluator_classes
        or rival.model_classes != rival.evaluator_classes
        or not mapping.target_binding_matches_source
        or rival.target_binding_matches_source
        or mapping.corrected_log_loss != mapping.reference_log_loss
        or rival.corrected_log_loss != rival.observed_log_loss
        or mapping.source_identity_sha256 != rival.source_identity_sha256
        or mapping.reference_features_sha256 != rival.reference_features_sha256
    ):
        raise ScoreMappingEvidenceError("mapping and target rival do not occupy distinct loci")
    if mapping_views["missing_key"] != rival_views["missing_key"]:
        raise ScoreMappingEvidenceError("aggregate symptom or metadata distinguishes the rival")
    if mapping_views["full"] == rival_views["full"]:
        raise ScoreMappingEvidenceError("full view does not separate mapping and target loci")
    if (
        mapping.feature_width != rival.feature_width
        or mapping.reference_feature_count != rival.reference_feature_count
        or mapping.feature_mean_distance != rival.feature_mean_distance
    ):
        raise ScoreMappingEvidenceError("added evidence becomes a rival shortcut")
    return PairwiseProjectionAudit(True, True, True, 0.5)
