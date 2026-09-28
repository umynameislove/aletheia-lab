"""Independently replay source targets, donor lineage, and target-only scoring."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import CalibrationResult
from aletheia_lab.benchmark.p2.confirmatory_v3_shift import reference_prior_standardized_log_loss
from aletheia_lab.benchmark.p2.score_mapping_intervention import (
    BinaryProbabilityModel,
    EvaluatorScoreSource,
)
from aletheia_lab.benchmark.p2.score_mapping_verification import (
    IndependentScoreWitness,
    SourceArtifactPaths,
    capture_independent_score_witness,
)
from aletheia_lab.benchmark.p2.target_binding_intervention import (
    TARGET_SELECTOR_SEED,
    TARGET_SHARD_COUNT,
    PairedTargetBindingIntervention,
    TargetBindingError,
    TargetBindingIntervention,
    TargetBindingSource,
)
from aletheia_lab.evidence.schema import sha256_text


@dataclass(frozen=True, slots=True)
class TargetBindingVerification:
    healthy_log_loss: float
    faulty_log_loss: float
    corrected_log_loss: float
    changed_target_count: int
    changed_donor_count: int
    class_counts_preserved: bool
    source_target_sha256: str
    observed_target_sha256: str
    corrected_target_sha256: str
    score_source_sha256: str


def verify_target_binding(
    *,
    witness: IndependentScoreWitness,
    score_source: EvaluatorScoreSource,
    target_source: TargetBindingSource,
    intervention: TargetBindingIntervention,
    corrected_target_rows: tuple[tuple[str, int], ...],
    reference_model: BinaryProbabilityModel,
    evaluation_matrix: NDArray[np.float64],
    reference_calibration: CalibrationResult,
    artifacts: SourceArtifactPaths,
) -> TargetBindingVerification:
    """Check the donor permutation without calling the intervention or correction.

    The source witness must have been captured from upstream before the fault.
    Hash agreement binds bytes, not the semantic truth of the original labels.
    """

    if not isinstance(target_source, TargetBindingSource) or not isinstance(
        intervention, TargetBindingIntervention
    ):
        raise TargetBindingError("typed target source and intervention are required")
    replay = capture_independent_score_witness(
        dataset_id=witness.dataset_id,
        record_ids=witness.record_ids,
        target_rows=witness.target_rows,
        evaluation_matrix=evaluation_matrix,
        model=reference_model,
        calibration=reference_calibration,
        artifacts=artifacts,
    )
    if replay != witness:
        raise TargetBindingError("upstream source differs from pre-intervention witness")
    if (
        target_source.dataset_id != witness.dataset_id
        or target_source.target_rows != witness.target_rows
        or score_source.record_ids != witness.record_ids
        or score_source.dataset_id != witness.dataset_id
        or score_source.model_classes != witness.model_classes
        or score_source.raw_score_rows != witness.raw_score_rows
        or score_source.calibrated_score_rows != witness.calibrated_score_rows
        or score_source.calibration.canonical_sha256() != witness.calibration_sha256
    ):
        raise TargetBindingError("target fault also changed upstream scores or target source")
    count = intervention.selected_shard_count
    if type(count) is not int or count not in (0, 1, 2, 4):
        raise TargetBindingError("unknown development dose")
    ids = witness.record_ids
    keys = {
        row_id: sha256_text(f"{TARGET_SELECTOR_SEED}\x00{witness.dataset_id}\x00{row_id}")
        for row_id in ids
    }
    shards = tuple(int(keys[row_id], 16) % TARGET_SHARD_COUNT for row_id in ids)
    selected = tuple(row_id for row_id, shard in zip(ids, shards, strict=True) if shard < count)
    ordered = sorted(selected, key=lambda row_id: (keys[row_id], row_id))
    donor_map = dict(zip(ordered, ordered[1:] + ordered[:1], strict=True))
    donors = tuple(donor_map.get(row_id, row_id) for row_id in ids)
    labels = dict(witness.target_rows)
    expected = tuple((row_id, labels[donor]) for row_id, donor in zip(ids, donors, strict=True))
    changed = tuple(
        row_id
        for (row_id, before), (_, after) in zip(witness.target_rows, expected, strict=True)
        if before != after
    )
    if (
        intervention.row_shards != shards
        or intervention.donor_record_ids != donors
        or intervention.observed_target_rows != expected
        or intervention.selected_record_ids != selected
        or intervention.changed_record_ids != changed
        or corrected_target_rows != witness.target_rows
    ):
        raise TargetBindingError("donor lineage, target values, or correction cannot be replayed")
    healthy_targets = tuple(label for _, label in witness.target_rows)
    faulty_targets = tuple(label for _, label in expected)
    probabilities = tuple(
        row[witness.model_classes.index(1)] for row in witness.calibrated_score_rows
    )
    healthy = reference_prior_standardized_log_loss(
        true_labels=healthy_targets, probabilities=probabilities
    )
    faulty = reference_prior_standardized_log_loss(
        true_labels=faulty_targets, probabilities=probabilities
    )
    corrected = reference_prior_standardized_log_loss(
        true_labels=tuple(label for _, label in corrected_target_rows), probabilities=probabilities
    )
    return TargetBindingVerification(
        healthy,
        faulty,
        corrected,
        len(changed),
        sum(donor != row_id for row_id, donor in zip(ids, donors, strict=True)),
        sum(healthy_targets) == sum(faulty_targets),
        canonical_sha256({"target_rows": witness.target_rows}),
        canonical_sha256({"target_rows": expected}),
        canonical_sha256({"target_rows": corrected_target_rows}),
        witness.calibrated_scores_sha256,
    )


def verify_paired_target_binding(
    *,
    witness: IndependentScoreWitness,
    score_source: EvaluatorScoreSource,
    target_source: TargetBindingSource,
    intervention: PairedTargetBindingIntervention,
    corrected_target_rows: tuple[tuple[str, int], ...],
    reference_model: BinaryProbabilityModel,
    evaluation_matrix: NDArray[np.float64],
    reference_calibration: CalibrationResult,
    artifacts: SourceArtifactPaths,
) -> TargetBindingVerification:
    """Replay an adversarial donor ledger without using its mutation helper.

    A successful replay proves a distinct intervention locus and provenance,
    not that the development-selected pairs will generalize to a holdout.
    """

    if not isinstance(target_source, TargetBindingSource) or not isinstance(
        intervention, PairedTargetBindingIntervention
    ):
        raise TargetBindingError("typed target source and paired intervention are required")
    replay = capture_independent_score_witness(
        dataset_id=witness.dataset_id,
        record_ids=witness.record_ids,
        target_rows=witness.target_rows,
        evaluation_matrix=evaluation_matrix,
        model=reference_model,
        calibration=reference_calibration,
        artifacts=artifacts,
    )
    if replay != witness:
        raise TargetBindingError("upstream source differs from pre-intervention witness")
    if (
        target_source.dataset_id != witness.dataset_id
        or target_source.target_rows != witness.target_rows
        or score_source.record_ids != witness.record_ids
        or score_source.dataset_id != witness.dataset_id
        or score_source.model_classes != witness.model_classes
        or score_source.raw_score_rows != witness.raw_score_rows
        or score_source.calibrated_score_rows != witness.calibrated_score_rows
        or score_source.calibration.canonical_sha256() != witness.calibration_sha256
    ):
        raise TargetBindingError("target fault also changed upstream scores or target source")

    ids = witness.record_ids
    labels = dict(witness.target_rows)
    donors = {row_id: row_id for row_id in ids}
    used: set[str] = set()
    if not isinstance(intervention.swapped_pairs, tuple):
        raise TargetBindingError("pair ledger must be a tuple")
    for pair in intervention.swapped_pairs:
        if (
            not isinstance(pair, tuple)
            or len(pair) != 2
            or not all(isinstance(row_id, str) for row_id in pair)
            or pair[0] not in labels
            or pair[1] not in labels
            or pair[0] in used
            or pair[1] in used
            or labels[pair[0]] != 0
            or labels[pair[1]] != 1
        ):
            raise TargetBindingError("pair ledger repeats or mislabels a source row")
        zero_id, one_id = pair
        donors[zero_id] = one_id
        donors[one_id] = zero_id
        used.update(pair)
    expected_donors = tuple(donors[row_id] for row_id in ids)
    expected = tuple(
        (row_id, labels[donor]) for row_id, donor in zip(ids, expected_donors, strict=True)
    )
    expected_changed = tuple(row_id for row_id in ids if row_id in used)
    if (
        intervention.donor_record_ids != expected_donors
        or intervention.observed_target_rows != expected
        or intervention.changed_record_ids != expected_changed
        or corrected_target_rows != witness.target_rows
    ):
        raise TargetBindingError("donor lineage, target values, or correction cannot be replayed")
    healthy_targets = tuple(label for _, label in witness.target_rows)
    faulty_targets = tuple(label for _, label in expected)
    probabilities = tuple(
        row[witness.model_classes.index(1)] for row in witness.calibrated_score_rows
    )
    healthy = reference_prior_standardized_log_loss(
        true_labels=healthy_targets, probabilities=probabilities
    )
    faulty = reference_prior_standardized_log_loss(
        true_labels=faulty_targets, probabilities=probabilities
    )
    corrected = reference_prior_standardized_log_loss(
        true_labels=tuple(label for _, label in corrected_target_rows), probabilities=probabilities
    )
    return TargetBindingVerification(
        healthy,
        faulty,
        corrected,
        len(expected_changed),
        len(expected_changed),
        sum(healthy_targets) == sum(faulty_targets),
        canonical_sha256({"target_rows": witness.target_rows}),
        canonical_sha256({"target_rows": expected}),
        canonical_sha256({"target_rows": corrected_target_rows}),
        witness.calibrated_scores_sha256,
    )
