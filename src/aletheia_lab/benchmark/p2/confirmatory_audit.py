"""Outcome-aware audit of the frozen external label-noise replication.

The audit is deliberately downstream of confirmatory closeout.  It may explain
an observed result, but it cannot change the registered decision, intervention
grid, thresholds, or stored artifacts.
"""

from __future__ import annotations

import math
import statistics
import warnings
from collections import defaultdict
from collections.abc import Sequence
from typing import Final, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.optimize import brentq  # type: ignore[import-untyped]
from scipy.special import expit, logit  # type: ignore[import-untyped]
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_execution import (
    PROBABILITY_CLIP,
    ConfirmatoryExecutionError,
    FlipDirection,
    ProbabilityMetrics,
    ProbabilityVector,
    apply_class_conditional_noise,
    build_confirmatory_split,
)
from aletheia_lab.benchmark.p2.confirmatory_inference import (
    InferenceRunPlan,
    analyze_dataset,
)
from aletheia_lab.benchmark.p2.confirmatory_protocol import ConfirmatoryProtocol
from aletheia_lab.benchmark.p2.confirmatory_registered import (
    DatasetOutcome,
    RegisteredDataset,
    ReplicateArtifact,
)
from aletheia_lab.benchmark.p2.confirmatory_runtime import fit_frozen_probability_vector
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN

AUDIT_SCHEMA_VERSION: Final[Literal["p2-confirmatory-root-cause-audit/1"]] = (
    "p2-confirmatory-root-cause-audit/1"
)

Sha256 = str
Disposition = Literal[
    "implementation_defect_detected",
    "temporal_prior_shift_supported",
    "inconclusive",
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PartitionPrevalence(_StrictFrozenModel):
    partition: Literal["train", "development", "sealed_test"]
    record_count: int = Field(ge=2)
    positive_count: int = Field(ge=1)
    negative_count: int = Field(ge=1)
    positive_rate: float = Field(gt=0.0, lt=1.0)

    @model_validator(mode="after")
    def _counts_reconcile(self) -> PartitionPrevalence:
        if self.positive_count + self.negative_count != self.record_count:
            raise ValueError("partition class counts do not reconcile")
        if not math.isclose(
            self.positive_rate,
            self.positive_count / self.record_count,
            abs_tol=1e-15,
        ):
            raise ValueError("partition prevalence is not derived from its counts")
        return self


class EncodingAudit(_StrictFrozenModel):
    target_column: str
    positive_label: str
    negative_label: str
    numeric_mapping: dict[str, int]
    yes_to_no_mapping: tuple[int, int]
    no_to_yes_mapping: tuple[int, int]
    labels_match_registered_bank_contract: bool
    all_mutation_entries_match_direction: bool
    all_mutations_reproduced_from_frozen_specs: bool


class FeatureAudit(_StrictFrozenModel):
    declared_excluded_features: tuple[str, ...]
    observed_feature_columns: tuple[str, ...]
    duration_declared_excluded: bool
    duration_absent_from_model_frame: bool
    stored_receipt_matches_loaded_frame: bool


class SeedMutationAudit(_StrictFrozenModel):
    seed: int = Field(ge=0)
    mutation_count: int = Field(ge=1)
    achieved_conditional_rate: float = Field(gt=0.0, lt=1.0)
    mutation_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    reproduced_exactly: bool


class MutationCellAudit(_StrictFrozenModel):
    cell_id: str
    direction: FlipDirection
    declared_conditional_rate: float
    seed_count: int = Field(ge=1)
    source_label: int
    destination_label: int
    source_class_count: int = Field(ge=1)
    mutation_count: int = Field(ge=1)
    achieved_conditional_rate: float = Field(gt=0.0, lt=1.0)
    clean_training_positive_rate: float = Field(gt=0.0, lt=1.0)
    mutated_training_positive_rate: float = Field(gt=0.0, lt=1.0)
    absolute_prior_gap_before: float = Field(ge=0.0, lt=1.0)
    absolute_prior_gap_after: float = Field(ge=0.0, lt=1.0)
    seed_census_matches_protocol: bool
    every_seed_reproduced_exactly: bool
    seeds: tuple[SeedMutationAudit, ...]

    @model_validator(mode="after")
    def _seed_details_reconcile(self) -> MutationCellAudit:
        expected_mapping = (1, 0) if self.direction == "yes_to_no" else (0, 1)
        if (self.source_label, self.destination_label) != expected_mapping:
            raise ValueError("mutation direction labels are reversed")
        if not math.isclose(
            self.achieved_conditional_rate,
            self.mutation_count / self.source_class_count,
            abs_tol=1e-15,
        ):
            raise ValueError("mutation count and achieved rate do not reconcile")
        if len(self.seeds) != self.seed_count:
            raise ValueError("mutation seed details do not match the declared census")
        if len({item.seed for item in self.seeds}) != self.seed_count:
            raise ValueError("mutation seed details contain a duplicate seed")
        if any(item.mutation_count != self.mutation_count for item in self.seeds):
            raise ValueError("mutation count varies within a registered cell")
        if any(
            not math.isclose(
                item.achieved_conditional_rate,
                self.achieved_conditional_rate,
                abs_tol=1e-15,
            )
            for item in self.seeds
        ):
            raise ValueError("achieved rate varies within a registered cell")
        if self.every_seed_reproduced_exactly != all(
            item.reproduced_exactly for item in self.seeds
        ):
            raise ValueError("mutation reproduction summary does not reconcile")
        return self


class PredictionCellAudit(_StrictFrozenModel):
    cell_id: str
    direction: FlipDirection
    declared_conditional_rate: float
    seed_count: int
    mean_relative_log_loss_change: float
    population_std_relative_log_loss_change: float
    median_relative_log_loss_change: float
    minimum_relative_log_loss_change: float
    maximum_relative_log_loss_change: float
    negative_effect_seed_count: int
    positive_effect_seed_count: int
    mean_accuracy_delta: float
    mean_predicted_positive_rate: float
    mean_calibration_intercept: float
    mean_positive_class_log_loss: float
    mean_negative_class_log_loss: float
    all_technical_gates_pass: bool


class CleanPredictionAudit(_StrictFrozenModel):
    test_positive_rate: float
    mean_predicted_positive_rate: float
    calibration_intercept: float
    log_loss: float
    positive_class_log_loss: float
    negative_class_log_loss: float


class PrimaryDirectionInferenceAudit(_StrictFrozenModel):
    direction: FlipDirection
    co_primary_cell_id: str
    point_estimate: float
    bootstrap_lower_bound: float
    bootstrap_upper_bound: float
    raw_one_sided_sign_flip_pvalue: float = Field(ge=0.0, le=1.0)
    holm_adjusted_pvalue: float = Field(ge=0.0, le=1.0)
    direction_pass: bool


class InferenceReproductionAudit(_StrictFrozenModel):
    stored_analysis_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    reproduced_analysis_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    exact_match: bool
    bootstrap_resamples: int
    sign_flip_resamples: int
    multiplicity_method: str
    directions: tuple[PrimaryDirectionInferenceAudit, ...]

    @model_validator(mode="after")
    def _reproduction_reconciles(self) -> InferenceReproductionAudit:
        hashes_match = self.stored_analysis_sha256 == self.reproduced_analysis_sha256
        if self.exact_match != hashes_match:
            raise ValueError("inference equality and analysis hashes disagree")
        if tuple(item.direction for item in self.directions) != (
            "yes_to_no",
            "no_to_yes",
        ):
            raise ValueError("inference audit requires both directions in canonical order")
        return self


class ConvergenceAudit(_StrictFrozenModel):
    performed: bool
    requested_refits: int = Field(ge=0)
    exact_prediction_vector_matches: int = Field(ge=0)
    convergence_warning_count: int = Field(ge=0)
    mismatched_cells_and_seeds: tuple[str, ...]

    @model_validator(mode="after")
    def _counts_reconcile(self) -> ConvergenceAudit:
        if not self.performed and any(
            (
                self.requested_refits,
                self.exact_prediction_vector_matches,
                self.convergence_warning_count,
                len(self.mismatched_cells_and_seeds),
            )
        ):
            raise ValueError("a skipped convergence audit cannot contain refit results")
        if self.exact_prediction_vector_matches > self.requested_refits:
            raise ValueError("exact refit matches exceed requested refits")
        return self


class BankRootCauseAudit(_StrictFrozenModel):
    schema_version: Literal["p2-confirmatory-root-cause-audit/1"] = AUDIT_SCHEMA_VERSION
    protocol_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    result_store_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    replication_outcome_sha256: Sha256 = Field(pattern=SHA256_PATTERN)
    dataset_id: str
    split_strategy: str
    split_reproduced_exactly: bool
    partitions: tuple[PartitionPrevalence, ...]
    encoding: EncodingAudit
    features: FeatureAudit
    mutations: tuple[MutationCellAudit, ...]
    clean_prediction: CleanPredictionAudit
    prediction_cells: tuple[PredictionCellAudit, ...]
    inference: InferenceReproductionAudit
    convergence: ConvergenceAudit
    implementation_defects: tuple[str, ...]
    scientific_findings: tuple[str, ...]
    disposition: Disposition
    registered_decision_unchanged: bool = True

    @model_validator(mode="after")
    def _disposition_is_fail_closed(self) -> BankRootCauseAudit:
        if self.implementation_defects and self.disposition != "implementation_defect_detected":
            raise ValueError("an implementation defect must dominate the audit disposition")
        if not self.implementation_defects and self.disposition == "implementation_defect_detected":
            raise ValueError("a defect disposition requires concrete defect evidence")
        if not self.registered_decision_unchanged:
            raise ValueError("an outcome-aware audit cannot alter the registered decision")
        if self.disposition == "temporal_prior_shift_supported":
            support_gates = (
                self.split_reproduced_exactly,
                self.encoding.labels_match_registered_bank_contract,
                self.encoding.all_mutation_entries_match_direction,
                self.encoding.all_mutations_reproduced_from_frozen_specs,
                self.features.duration_declared_excluded,
                self.features.duration_absent_from_model_frame,
                self.features.stored_receipt_matches_loaded_frame,
                all(
                    item.seed_census_matches_protocol and item.every_seed_reproduced_exactly
                    for item in self.mutations
                ),
                self.inference.exact_match,
                self.convergence.performed,
                self.convergence.exact_prediction_vector_matches
                == self.convergence.requested_refits,
                self.convergence.convergence_warning_count == 0,
                not self.convergence.mismatched_cells_and_seeds,
            )
            if not all(support_gates):
                raise ValueError("a prior-shift disposition requires every validity gate")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def _mean(values: Sequence[float]) -> float:
    if not values:
        raise ConfirmatoryExecutionError("audit statistics require at least one value")
    return math.fsum(values) / len(values)


def _partition(
    name: Literal["train", "development", "sealed_test"],
    record_ids: Sequence[str],
    target_by_id: dict[str, int],
) -> PartitionPrevalence:
    try:
        targets = tuple(target_by_id[record_id] for record_id in record_ids)
    except KeyError as exc:
        raise ConfirmatoryExecutionError("split contains an unknown target identifier") from exc
    positives = sum(targets)
    return PartitionPrevalence(
        partition=name,
        record_count=len(targets),
        positive_count=positives,
        negative_count=len(targets) - positives,
        positive_rate=positives / len(targets),
    )


def _frame_for_ids(dataset: RegisteredDataset, record_ids: Sequence[str]) -> pd.DataFrame:
    position = {record_id: index for index, record_id in enumerate(dataset.record_ids)}
    try:
        rows = [position[record_id] for record_id in record_ids]
    except KeyError as exc:
        raise ConfirmatoryExecutionError("audit frame references an unknown record") from exc
    return dataset.features.iloc[rows].reset_index(drop=True)


def _calibration_intercept(targets: Sequence[int], probabilities: Sequence[float]) -> float:
    truth = np.asarray(targets, dtype=float)
    values = np.clip(np.asarray(probabilities, dtype=float), PROBABILITY_CLIP, 1 - PROBABILITY_CLIP)
    if truth.ndim != 1 or values.shape != truth.shape or set(truth) != {0.0, 1.0}:
        raise ConfirmatoryExecutionError("calibration audit needs aligned binary outcomes")
    logits = logit(values)
    target_rate = float(np.mean(truth))

    def residual(intercept: float) -> float:
        return float(np.mean(expit(logits + intercept)) - target_rate)

    return float(brentq(residual, -80.0, 80.0))


def _classwise_losses(metrics: ProbabilityMetrics, targets: Sequence[int]) -> tuple[float, float]:
    if len(metrics.per_record_log_loss) != len(targets):
        raise ConfirmatoryExecutionError("classwise audit losses are misaligned")
    positive = [
        loss
        for loss, target in zip(metrics.per_record_log_loss, targets, strict=True)
        if target == 1
    ]
    negative = [
        loss
        for loss, target in zip(metrics.per_record_log_loss, targets, strict=True)
        if target == 0
    ]
    return _mean(positive), _mean(negative)


def _prediction_cell(
    *, artifacts: Sequence[ReplicateArtifact], targets: Sequence[int]
) -> PredictionCellAudit:
    typed = tuple(artifacts)
    if not typed:
        raise ConfirmatoryExecutionError("prediction audit requires replicate artifacts")
    first = typed[0]
    effects = tuple(item.replicate.relative_log_loss_increase for item in typed)
    predicted_means = tuple(_mean(item.observed_vector.positive_probabilities) for item in typed)
    intercepts = tuple(
        _calibration_intercept(targets, item.observed_vector.positive_probabilities)
        for item in typed
    )
    class_losses = tuple(
        _classwise_losses(item.replicate.observed_metrics, targets) for item in typed
    )
    return PredictionCellAudit(
        cell_id=first.replicate.cell_id,
        direction=first.replicate.direction,
        declared_conditional_rate=first.replicate.conditional_rate,
        seed_count=len(typed),
        mean_relative_log_loss_change=_mean(effects),
        population_std_relative_log_loss_change=statistics.pstdev(effects),
        median_relative_log_loss_change=statistics.median(effects),
        minimum_relative_log_loss_change=min(effects),
        maximum_relative_log_loss_change=max(effects),
        negative_effect_seed_count=sum(value < 0.0 for value in effects),
        positive_effect_seed_count=sum(value > 0.0 for value in effects),
        mean_accuracy_delta=_mean(tuple(item.replicate.accuracy_delta for item in typed)),
        mean_predicted_positive_rate=_mean(predicted_means),
        mean_calibration_intercept=_mean(intercepts),
        mean_positive_class_log_loss=_mean(tuple(value[0] for value in class_losses)),
        mean_negative_class_log_loss=_mean(tuple(value[1] for value in class_losses)),
        all_technical_gates_pass=all(item.replicate.technical_gates_pass for item in typed),
    )


def _refit_vectors(
    *,
    protocol: ConfirmatoryProtocol,
    registered: RegisteredDataset,
    outcome: DatasetOutcome,
) -> ConvergenceAudit:
    dataset = registered.binding
    train_features = _frame_for_ids(registered, outcome.training_source.record_ids)
    test_features = _frame_for_ids(registered, outcome.clean_vector.record_ids)
    requested = 1 + len(outcome.replicates)
    exact = 0
    warning_count = 0
    mismatches: list[str] = []

    jobs: tuple[tuple[str, Sequence[int], ProbabilityVector], ...] = (
        (
            "clean_reference",
            outcome.training_source.clean_targets,
            outcome.clean_vector,
        ),
        *(
            (
                f"{artifact.replicate.cell_id}:{artifact.replicate.seed}",
                artifact.mutation.mutated_targets,
                artifact.observed_vector,
            )
            for artifact in outcome.replicates
        ),
    )
    for identifier, training_targets, expected in jobs:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ConvergenceWarning)
            observed = fit_frozen_probability_vector(
                protocol=protocol,
                dataset=dataset,
                source=outcome.training_source,
                training_features=train_features,
                training_targets=training_targets,
                evaluation_features=test_features,
                evaluation_record_ids=outcome.clean_vector.record_ids,
                role="clean_reference" if identifier == "clean_reference" else "class_conditional",
            )
        warning_count += sum(issubclass(item.category, ConvergenceWarning) for item in caught)
        if observed == expected:
            exact += 1
        else:
            mismatches.append(identifier)
    return ConvergenceAudit(
        performed=True,
        requested_refits=requested,
        exact_prediction_vector_matches=exact,
        convergence_warning_count=warning_count,
        mismatched_cells_and_seeds=tuple(mismatches),
    )


def audit_bank_replication(
    *,
    protocol: ConfirmatoryProtocol,
    registered: RegisteredDataset,
    outcome: DatasetOutcome,
    result_store_sha256: str,
    verify_convergence: bool = True,
) -> BankRootCauseAudit:
    """Audit the frozen Bank outcome without changing its registered decision."""

    dataset = registered.binding
    if dataset.role != "external_replication" or outcome.receipt.dataset_role != dataset.role:
        raise ConfirmatoryExecutionError("root-cause audit requires the external replication")
    if outcome.receipt != registered.receipt:
        raise ConfirmatoryExecutionError(
            "loaded Bank bytes do not match the frozen outcome receipt"
        )
    reproduced_split = build_confirmatory_split(
        protocol=protocol,
        dataset=dataset,
        dataset_sha256=registered.receipt.snapshot_sha256,
        record_ids=registered.record_ids,
        labels=registered.targets,
    )
    split_matches = reproduced_split == outcome.split
    target_by_id = dict(zip(registered.record_ids, registered.targets, strict=True))
    partitions = (
        _partition("train", outcome.split.membership.train, target_by_id),
        _partition("development", outcome.split.membership.development, target_by_id),
        _partition("sealed_test", outcome.split.membership.sealed_test, target_by_id),
    )
    train_rate = partitions[0].positive_rate
    test_rate = partitions[2].positive_rate
    clean_positive_count = sum(outcome.training_source.clean_targets)

    grouped: dict[str, list[ReplicateArtifact]] = defaultdict(list)
    mutations: list[MutationCellAudit] = []
    entry_mapping_valid = True
    all_mutations_reproduced = True
    for artifact in outcome.replicates:
        grouped[artifact.replicate.cell_id].append(artifact)
        expected_labels = (1, 0) if artifact.replicate.direction == "yes_to_no" else (0, 1)
        entry_mapping_valid = entry_mapping_valid and all(
            (entry.original_label, entry.mutated_label) == expected_labels
            for entry in artifact.mutation.entries
        )
        reproduced = apply_class_conditional_noise(
            source=outcome.training_source,
            spec=artifact.mutation.spec,
        )
        all_mutations_reproduced = all_mutations_reproduced and reproduced == artifact.mutation

    for cell in protocol.intervention_cells:
        values = grouped[cell.cell_id]
        if not values:
            raise ConfirmatoryExecutionError("root-cause audit found an empty intervention cell")
        typed = tuple(
            sorted(
                (item for item in outcome.replicates if item.replicate.cell_id == cell.cell_id),
                key=lambda item: item.replicate.seed,
            )
        )
        first = typed[0]
        source_label, destination_label = (1, 0) if cell.flip_direction == "yes_to_no" else (0, 1)
        expected_seeds = cell.replication_replicate_seeds
        observed_seeds = tuple(sorted(item.replicate.seed for item in typed))
        reproduced_flags = tuple(
            apply_class_conditional_noise(
                source=outcome.training_source,
                spec=item.mutation.spec,
            )
            == item.mutation
            for item in typed
        )
        seed_audits = tuple(
            SeedMutationAudit(
                seed=item.replicate.seed,
                mutation_count=item.mutation.mutation_count,
                achieved_conditional_rate=item.mutation.achieved_conditional_rate,
                mutation_sha256=item.mutation.canonical_sha256(),
                reproduced_exactly=reproduced,
            )
            for item, reproduced in zip(typed, reproduced_flags, strict=True)
        )
        mutated_positive_count = clean_positive_count + (
            first.mutation.mutation_count
            if cell.flip_direction == "no_to_yes"
            else -first.mutation.mutation_count
        )
        mutated_rate = mutated_positive_count / len(outcome.training_source.clean_targets)
        mutations.append(
            MutationCellAudit(
                cell_id=cell.cell_id,
                direction=cell.flip_direction,
                declared_conditional_rate=cell.conditional_flip_rate,
                seed_count=len(typed),
                source_label=source_label,
                destination_label=destination_label,
                source_class_count=first.mutation.source_class_count,
                mutation_count=first.mutation.mutation_count,
                achieved_conditional_rate=first.mutation.achieved_conditional_rate,
                clean_training_positive_rate=train_rate,
                mutated_training_positive_rate=mutated_rate,
                absolute_prior_gap_before=abs(train_rate - test_rate),
                absolute_prior_gap_after=abs(mutated_rate - test_rate),
                seed_census_matches_protocol=observed_seeds == expected_seeds,
                every_seed_reproduced_exactly=all(reproduced_flags),
                seeds=seed_audits,
            )
        )

    test_targets = tuple(target_by_id[item] for item in outcome.clean_vector.record_ids)
    positive_clean_loss, negative_clean_loss = _classwise_losses(
        outcome.clean_metrics, test_targets
    )
    clean_prediction = CleanPredictionAudit(
        test_positive_rate=test_rate,
        mean_predicted_positive_rate=_mean(outcome.clean_vector.positive_probabilities),
        calibration_intercept=_calibration_intercept(
            test_targets, outcome.clean_vector.positive_probabilities
        ),
        log_loss=outcome.clean_metrics.log_loss,
        positive_class_log_loss=positive_clean_loss,
        negative_class_log_loss=negative_clean_loss,
    )
    prediction_cells = tuple(
        _prediction_cell(artifacts=grouped[cell.cell_id], targets=test_targets)
        for cell in protocol.intervention_cells
    )

    reproduced_analysis = analyze_dataset(
        protocol=protocol,
        dataset=dataset,
        replicates=tuple(item.replicate for item in outcome.replicates),
        run_plan=InferenceRunPlan.registered(protocol),
    )
    inference = InferenceReproductionAudit(
        stored_analysis_sha256=outcome.analysis.canonical_sha256(),
        reproduced_analysis_sha256=reproduced_analysis.canonical_sha256(),
        exact_match=reproduced_analysis == outcome.analysis,
        bootstrap_resamples=protocol.inference.bootstrap_resamples,
        sign_flip_resamples=protocol.inference.hypothesis_test_resamples,
        multiplicity_method=protocol.inference.multiplicity_method,
        directions=tuple(
            PrimaryDirectionInferenceAudit(
                direction=item.direction,
                co_primary_cell_id=item.co_primary_cell_id,
                point_estimate=item.interval.point_estimate,
                bootstrap_lower_bound=item.interval.lower_bound,
                bootstrap_upper_bound=item.interval.upper_bound,
                raw_one_sided_sign_flip_pvalue=item.raw_one_sided_pvalue,
                holm_adjusted_pvalue=item.holm_adjusted_pvalue,
                direction_pass=item.direction_pass,
            )
            for item in reproduced_analysis.directions
        ),
    )
    convergence = (
        _refit_vectors(protocol=protocol, registered=registered, outcome=outcome)
        if verify_convergence
        else ConvergenceAudit(
            performed=False,
            requested_refits=0,
            exact_prediction_vector_matches=0,
            convergence_warning_count=0,
            mismatched_cells_and_seeds=(),
        )
    )

    feature_columns = tuple(str(column) for column in registered.features.columns)
    features = FeatureAudit(
        declared_excluded_features=dataset.excluded_features,
        observed_feature_columns=feature_columns,
        duration_declared_excluded="duration" in dataset.excluded_features,
        duration_absent_from_model_frame="duration" not in feature_columns,
        stored_receipt_matches_loaded_frame=(
            outcome.receipt == registered.receipt
            and registered.receipt.feature_columns == feature_columns
            and registered.receipt.excluded_features == dataset.excluded_features
        ),
    )
    labels_match_contract = (
        registered.receipt.target_column == dataset.target_column == "y"
        and registered.receipt.positive_label == dataset.positive_label == "yes"
        and registered.receipt.negative_label == "no"
        and set(registered.targets) == {0, 1}
    )
    encoding = EncodingAudit(
        target_column=registered.receipt.target_column,
        positive_label=registered.receipt.positive_label,
        negative_label=registered.receipt.negative_label,
        numeric_mapping={
            registered.receipt.negative_label: 0,
            registered.receipt.positive_label: 1,
        },
        yes_to_no_mapping=(1, 0),
        no_to_yes_mapping=(0, 1),
        labels_match_registered_bank_contract=labels_match_contract,
        all_mutation_entries_match_direction=entry_mapping_valid,
        all_mutations_reproduced_from_frozen_specs=all_mutations_reproduced,
    )

    defects: list[str] = []
    checks = {
        "split_reproduction_mismatch": split_matches,
        "target_direction_mapping_mismatch": (entry_mapping_valid and all_mutations_reproduced),
        "duration_reached_model_frame": (
            features.duration_declared_excluded and features.duration_absent_from_model_frame
        ),
        "inference_reproduction_mismatch": inference.exact_match,
        "technical_control_failure": outcome.analysis.batch_technical_gates_pass,
        "seed_or_mutation_census_mismatch": all(
            item.seed_census_matches_protocol and item.every_seed_reproduced_exactly
            for item in mutations
        ),
    }
    checks["registered_bank_label_contract_mismatch"] = labels_match_contract
    checks["registered_feature_receipt_mismatch"] = features.stored_receipt_matches_loaded_frame
    if convergence.performed:
        checks["prediction_or_convergence_reproduction_failure"] = (
            convergence.exact_prediction_vector_matches == convergence.requested_refits
            and convergence.convergence_warning_count == 0
            and not convergence.mismatched_cells_and_seeds
        )
    defects.extend(name for name, passed in checks.items() if not passed)

    no_to_yes = tuple(item for item in prediction_cells if item.direction == "no_to_yes")
    high_dose_mutation = next(
        item
        for item in mutations
        if item.direction == "no_to_yes" and math.isclose(item.declared_conditional_rate, 0.3)
    )
    all_no_to_yes_seeds_improve = all(
        item.negative_effect_seed_count == item.seed_count for item in no_to_yes
    )
    prior_gap_is_large = test_rate - train_rate >= 0.20
    corruption_moves_prior_toward_test = (
        high_dose_mutation.absolute_prior_gap_after < high_dose_mutation.absolute_prior_gap_before
    )
    clean_is_underconfident_for_test_prior = (
        clean_prediction.mean_predicted_positive_rate < train_rate < test_rate
        and clean_prediction.calibration_intercept > 0.0
    )
    findings: tuple[str, ...] = (
        "temporal test prevalence exceeds training prevalence",
        "clean predictions severely understate the sealed-test positive prevalence",
        "no_to_yes corruption moves the training label prior toward the sealed-test prior",
        "every no_to_yes seed reduces log loss relative to the frozen clean model",
        "registered inference and technical controls reproduce exactly",
    )
    shift_supported = all(
        (
            not defects,
            convergence.performed,
            prior_gap_is_large,
            corruption_moves_prior_toward_test,
            clean_is_underconfident_for_test_prior,
            all_no_to_yes_seeds_improve,
        )
    )
    disposition: Disposition
    if defects:
        disposition = "implementation_defect_detected"
    elif shift_supported:
        disposition = "temporal_prior_shift_supported"
    else:
        disposition = "inconclusive"
    if disposition != "temporal_prior_shift_supported":
        findings = tuple(
            item
            for item, supported in zip(
                findings,
                (
                    prior_gap_is_large,
                    clean_is_underconfident_for_test_prior,
                    corruption_moves_prior_toward_test,
                    all_no_to_yes_seeds_improve,
                    inference.exact_match,
                ),
                strict=True,
            )
            if supported
        )

    return BankRootCauseAudit(
        protocol_sha256=protocol.canonical_sha256(),
        result_store_sha256=result_store_sha256,
        replication_outcome_sha256=outcome.canonical_sha256(),
        dataset_id=dataset.dataset_id,
        split_strategy=dataset.split.strategy,
        split_reproduced_exactly=split_matches,
        partitions=partitions,
        encoding=encoding,
        features=features,
        mutations=tuple(mutations),
        clean_prediction=clean_prediction,
        prediction_cells=prediction_cells,
        inference=inference,
        convergence=convergence,
        implementation_defects=tuple(defects),
        scientific_findings=findings,
        disposition=disposition,
    )
