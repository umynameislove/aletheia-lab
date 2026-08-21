"""Outcome-free compiler and contract for the v3 confirmatory protocol.

The module freezes deterministic partition membership, preprocessing, model,
control, estimator and decision semantics.  It may inspect labels only to
compile stratified group assignments and eligibility receipts.  It cannot fit
a model, score predictions, open a sealed outcome or authorize execution.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from fractions import Fraction
from numbers import Integral, Real
from pathlib import Path
from typing import Final, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    V3DatasetBinding,
    V3DatasetBindingManifest,
    V3DatasetBindingReceipt,
    load_v3_dataset_binding_manifest,
    load_v3_dataset_binding_receipt,
    load_v3_dataset_snapshot_for_registration,
    verify_v3_dataset_binding_design,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_design import (
    V3StudyDesign,
    load_v3_study_design,
    verify_v3_predecessor,
)
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN

V3_PROTOCOL_SCHEMA_VERSION: Final[Literal["p2-label-noise-shift-protocol/2"]] = (
    "p2-label-noise-shift-protocol/2"
)
DEFAULT_V3_PROTOCOL_PATH: Final[Path] = Path(
    "configs/benchmark/p2_label_noise_shift_v3_protocol.json"
)
PARTITIONS: Final[tuple[str, str, str]] = ("train", "development", "sealed_test")

DatasetRole = Literal["primary", "external_replication"]
Partition = Literal["train", "development", "sealed_test"]
Direction = Literal["yes_to_no", "no_to_yes"]


class V3ProtocolError(ValueError):
    """Raised when protocol compilation or verification fails closed."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ProtocolArtifactBindings(_StrictFrozenModel):
    design_uri: Literal["configs/benchmark/p2_label_noise_shift_v3_design.json"]
    design_sha256: str = Field(pattern=SHA256_PATTERN)
    dataset_manifest_uri: Literal[
        "configs/benchmark/p2_label_noise_shift_v3_dataset_bindings.json"
    ]
    dataset_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    dataset_receipt_uri: Literal[
        "configs/benchmark/provenance/p2_v3_dataset_binding_receipt.json"
    ]
    dataset_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    v2_result_store_sha256: str = Field(pattern=SHA256_PATTERN)


class SplitAlgorithmContract(_StrictFrozenModel):
    algorithm: Literal["sha256_tied_greedy_stratified_feature_group_v1"]
    partition_order: tuple[Literal["train", "development", "sealed_test"], ...]
    fractions: tuple[float, float, float]
    grouping_key: Literal["canonical_analysis_feature_values"]
    group_order: Literal["descending_size_then_class_mass_then_seeded_sha256"]
    assignment_objective: Literal[
        "minimize_normalized_overflow_then_squared_class_and_total_deficit"
    ]
    target_rounding: Literal["largest_remainder_partition_order_tiebreak"]
    duplicate_groups_cannot_cross_partitions: Literal[True]
    source_order_cannot_break_ties: Literal[True]
    sealed_test_single_open: Literal[True]

    @model_validator(mode="after")
    def _algorithm_is_exact(self) -> SplitAlgorithmContract:
        if self.partition_order != PARTITIONS:
            raise ValueError("partition order is frozen")
        if self.fractions != (0.6, 0.2, 0.2):
            raise ValueError("protocol partitions are frozen at 60/20/20")
        return self


class DatasetSplitReceipt(_StrictFrozenModel):
    dataset_id: str
    role: DatasetRole
    seed: int = Field(ge=0)
    record_count: int = Field(gt=0)
    group_count: int = Field(gt=0)
    target_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    record_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    partition_counts: dict[str, int]
    partition_class_counts: dict[str, dict[str, int]]
    membership_sha256: str = Field(pattern=SHA256_PATTERN)
    group_assignment_sha256: str = Field(pattern=SHA256_PATTERN)
    sealed_membership_sha256: str = Field(pattern=SHA256_PATTERN)
    duplicate_group_cross_partition_count: Literal[0]
    labels_used_only_for_stratification: Literal[True]
    model_fitted: Literal[False]
    predictive_metrics_generated: Literal[False]

    @model_validator(mode="after")
    def _counts_reconcile(self) -> DatasetSplitReceipt:
        if tuple(self.partition_counts) != PARTITIONS:
            raise ValueError("split receipt requires the ordered partition census")
        if sum(self.partition_counts.values()) != self.record_count:
            raise ValueError("partition counts do not reconcile with the dataset")
        if tuple(self.partition_class_counts) != PARTITIONS:
            raise ValueError("class counts require the ordered partition census")
        for partition in PARTITIONS:
            if sum(self.partition_class_counts[partition].values()) != self.partition_counts[
                partition
            ]:
                raise ValueError("partition class counts do not reconcile")
        return self


class PreprocessingContract(_StrictFrozenModel):
    fit_partition: Literal["train"]
    target_column_removed_before_fit: Literal[True]
    identifier_columns_removed_before_fit: Literal[True]
    excluded_columns_removed_before_split: Literal[True]
    missing_policy: Literal["fail_closed_no_imputation"]
    numeric_transform: Literal["standard_scaler_float64_train_statistics"]
    categorical_transform: Literal[
        "one_hot_float64_handle_unknown_ignore_train_vocabulary"
    ]
    deterministic_category_normalizations: tuple[
        Literal[
            "uci_default_of_credit_card_clients:EDUCATION:{0,5,6}->other",
            "uci_default_of_credit_card_clients:MARRIAGE:{0}->other",
        ],
        ...,
    ]
    output_column_order: Literal[
        "categorical_manifest_order_then_lexicographic_encoded_token_then_numeric_manifest_order"
    ]
    development_only_calibration: Literal[True]
    sealed_labels_for_selection_forbidden: Literal[True]

    @model_validator(mode="after")
    def _normalizations_are_complete(self) -> PreprocessingContract:
        expected = (
            "uci_default_of_credit_card_clients:EDUCATION:{0,5,6}->other",
            "uci_default_of_credit_card_clients:MARRIAGE:{0}->other",
        )
        if self.deterministic_category_normalizations != expected:
            raise ValueError("the complete source-category normalization policy is required")
        return self


class ModelRuntimeContract(_StrictFrozenModel):
    primary_model: Literal["sklearn.linear_model.LogisticRegression"]
    primary_parameters: tuple[
        Literal["C=1.0", "solver=lbfgs", "max_iter=1000", "random_state=42"], ...
    ]
    sensitivity_model: Literal["sklearn.ensemble.HistGradientBoostingClassifier"]
    sensitivity_parameters: tuple[
        Literal[
            "learning_rate=0.1",
            "max_iter=100",
            "max_leaf_nodes=31",
            "l2_regularization=0.0",
            "early_stopping=false",
            "random_state=43",
        ],
        ...,
    ]
    probability_output_required: Literal[True]
    convergence_warning_action: Literal["technical_failure"]
    nonfinite_probability_action: Literal["technical_failure"]
    calibration: Literal["development_logit_intercept_slope_newton"]
    calibration_initial_intercept: float
    calibration_initial_slope: float
    calibration_regularization: None
    calibration_probability_clip: float
    calibration_max_iter: Literal[100]
    calibration_tolerance: float
    calibration_failure_action: Literal["abstain"]
    hyperparameter_search_forbidden: Literal[True]
    sensitivity_model_cannot_rescue_primary: Literal[True]

    @model_validator(mode="after")
    def _models_are_exact(self) -> ModelRuntimeContract:
        if self.calibration_initial_intercept != 0.0 or self.calibration_initial_slope != 1.0:
            raise ValueError("calibration initialization is frozen at intercept 0 and slope 1")
        if self.primary_parameters != (
            "C=1.0",
            "solver=lbfgs",
            "max_iter=1000",
            "random_state=42",
        ):
            raise ValueError("primary model parameters are frozen")
        if self.sensitivity_parameters != (
            "learning_rate=0.1",
            "max_iter=100",
            "max_leaf_nodes=31",
            "l2_regularization=0.0",
            "early_stopping=false",
            "random_state=43",
        ):
            raise ValueError("sensitivity model parameters are frozen")
        if not math.isclose(self.calibration_tolerance, 1e-8, rel_tol=0.0, abs_tol=0.0):
            raise ValueError("calibration tolerance is frozen at 1e-8")
        if not math.isclose(
            self.calibration_probability_clip, 1e-15, rel_tol=0.0, abs_tol=0.0
        ):
            raise ValueError("calibration probability clipping is frozen at 1e-15")
        return self


class InterventionRuntimeContract(_StrictFrozenModel):
    directions: tuple[Literal["yes_to_no", "no_to_yes"], ...]
    conditional_rates: tuple[float, ...]
    co_primary_rate: float
    corruption_seed_first: Literal[6101]
    corruption_seed_last: Literal[6150]
    source_selection: Literal[
        "sha256_rank_without_replacement_over_canonical_record_identity"
    ]
    mutation_count: Literal["floor_conditional_rate_times_source_class_count"]
    only_training_targets_mutated: Literal[True]
    feature_blind: Literal[True]
    nuisance_match: Literal["clean_label_class_weights_to_corrupted_prevalence"]
    nuisance_weight_normalization: Literal["mean_weight_one"]
    reciprocal_control: Literal[
        "equal_opposite_flips_without_replacement_capped_by_minority_class"
    ]
    reciprocal_control_cannot_claim_equal_mutation_count_when_capped: Literal[True]
    exact_prevalence_reconciliation_required: Literal[True]
    serialization_and_repair_hash_equality_required: Literal[True]

    @model_validator(mode="after")
    def _factor_is_complete(self) -> InterventionRuntimeContract:
        if self.directions != ("yes_to_no", "no_to_yes"):
            raise ValueError("both corruption directions are required")
        if self.conditional_rates != (0.1, 0.2, 0.3):
            raise ValueError("the complete corruption dose grid is required")
        if not math.isclose(self.co_primary_rate, 0.3, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("the co-primary corruption rate is 0.30")
        return self


class PriorShiftRuntimeContract(_StrictFrozenModel):
    odds_multipliers: tuple[float, ...]
    environment_seed_first: Literal[7101]
    environment_seed_last: Literal[7150]
    sample_size: Literal["sealed_test_record_count"]
    class_count_rounding: Literal["largest_remainder_negative_then_positive_tiebreak"]
    sampling: Literal[
        "within_class_sha256_counter_rejection_with_replacement_except_neutral_identity"
    ]
    generator_may_read_target: Literal[True]
    estimator_may_read_target: Literal[False]
    scorer_may_read_target: Literal[True]
    class_conditionals_preserved_by_construction: Literal[True]
    neutral_environment_must_reproduce_sealed_membership: Literal[True]

    @model_validator(mode="after")
    def _environment_grid_is_complete(self) -> PriorShiftRuntimeContract:
        if self.odds_multipliers != (0.25, 1.0, 4.0):
            raise ValueError("the symmetric prior-odds grid is frozen")
        return self


class ShiftEstimatorRuntimeContract(_StrictFrozenModel):
    ordered_estimators: tuple[
        Literal["unadjusted_v2", "oracle_prior_ratio", "bbse", "mlls_em", "rlls"], ...
    ]
    bbse_solver: Literal["unconstrained_least_squares_then_simplex_validity_check"]
    bbse_condition_number_max: float
    mlls_initialization: Literal["development_source_prior"]
    mlls_max_iter: Literal[1000]
    mlls_tolerance: float
    rlls_l2_regularization: float
    invalid_prior_or_weight_action: Literal["abstain"]
    silent_clipping_forbidden: Literal[True]
    oracle_non_deployable: Literal[True]
    diagnostic: Literal["classwise_rbf_mmd_median_bandwidth_permutation"]
    diagnostic_resamples: Literal[2000]
    diagnostic_seed: Literal[314160]
    diagnostic_holm_scope: Literal["classes_crossed_with_datasets"]
    assumption_failure_action: Literal["abstain_from_pure_label_shift_claim"]

    @model_validator(mode="after")
    def _estimators_are_exact(self) -> ShiftEstimatorRuntimeContract:
        expected = ("unadjusted_v2", "oracle_prior_ratio", "bbse", "mlls_em", "rlls")
        if self.ordered_estimators != expected:
            raise ValueError("the complete ordered estimator baseline set is required")
        fixed = (
            (self.bbse_condition_number_max, 1e8, "BBSE condition limit"),
            (self.mlls_tolerance, 1e-8, "MLLS tolerance"),
            (self.rlls_l2_regularization, 0.01, "RLLS regularization"),
        )
        for observed, expected_value, label in fixed:
            if not math.isclose(observed, expected_value, rel_tol=0.0, abs_tol=0.0):
                raise ValueError(f"{label} is frozen at {expected_value}")
        return self


class InferenceRuntimeContract(_StrictFrozenModel):
    primary_metric: Literal["reference_prior_standardized_log_loss"]
    reference_positive_prior: float
    primary_estimand: Literal[
        "relative_net_corruption_effect_vs_prior_matched_clean_control"
    ]
    minimum_practical_effect: float
    primary_environment: Literal["neutral_odds_multiplier_1.0"]
    replicate_unit: Literal["corruption_seed"]
    replicate_count_per_cell: Literal[50]
    bootstrap: Literal["two_way_multinomial_product_weight"]
    bootstrap_resamples: Literal[10000]
    bootstrap_seed: Literal[271829]
    confidence_interval: Literal["equal_tailed_percentile_95"]
    hypothesis_test: Literal["paired_seed_level_monte_carlo_sign_flip_one_sided"]
    hypothesis_test_resamples: Literal[100000]
    hypothesis_test_seed: Literal[161804]
    monte_carlo_p_value: Literal["plus_one_exceedance_correction"]
    cross_dataset_test: Literal["intersection_union_max_p"]
    direction_multiplicity: Literal["holm_two_directions"]
    familywise_alpha: float
    no_interim_analysis: Literal[True]
    report_every_cell_control_estimator_and_abstention: Literal[True]

    @model_validator(mode="after")
    def _inference_is_exact(self) -> InferenceRuntimeContract:
        fixed = (
            (self.reference_positive_prior, 0.5, "reference prior"),
            (self.minimum_practical_effect, 0.05, "practical effect"),
            (self.familywise_alpha, 0.05, "family-wise alpha"),
        )
        for observed, expected, label in fixed:
            if not math.isclose(observed, expected, rel_tol=0.0, abs_tol=1e-12):
                raise ValueError(f"{label} is prospectively frozen at {expected}")
        return self


class DecisionRuntimeContract(_StrictFrozenModel):
    requirements: tuple[
        Literal[
            "net_effect_at_least_0.05_in_both_datasets",
            "bootstrap_lower_bound_above_0_in_both_datasets",
            "iut_p_then_holm_below_0.05",
            "all_technical_and_orthogonalization_controls_pass",
            "zero_prior_only_label_noise_admissions",
        ],
        ...,
    ]
    at_least_one_same_direction_cross_dataset_pass_required: Literal[True]
    any_dataset_failure_blocks_that_direction: Literal[True]
    assumption_failure_is_abstention_not_failure_or_pass: Literal[True]
    secondary_model_metrics_and_estimators_cannot_rescue_primary: Literal[True]
    additional_grid_or_seed_after_outcomes_forbidden: Literal[True]

    @model_validator(mode="after")
    def _decision_is_conjunctive(self) -> DecisionRuntimeContract:
        expected = (
            "net_effect_at_least_0.05_in_both_datasets",
            "bootstrap_lower_bound_above_0_in_both_datasets",
            "iut_p_then_holm_below_0.05",
            "all_technical_and_orthogonalization_controls_pass",
            "zero_prior_only_label_noise_admissions",
        )
        if self.requirements != expected:
            raise ValueError("the complete conjunctive decision rule is required")
        return self


class ProtocolGovernance(_StrictFrozenModel):
    required_git_tag: Literal["p2-label-noise-shift-factorial-v3.1"]
    protocol_only_commit_required: Literal[True]
    immutable_release_required_before_execution: Literal[True]
    structured_internal_outcome_blind_audit_required_before_registration: Literal[True]
    execution_implementation_must_follow_registration: Literal[True]
    changes_after_registration_require_new_protocol_version: Literal[True]
    primary_and_replication_outcomes_released_together: Literal[True]
    sealed_test_single_open: Literal[True]
    registration_authorized_by_this_file: Literal[False]
    execution_authorized_by_this_file: Literal[False]


class V3ConfirmatoryProtocol(_StrictFrozenModel):
    schema_version: Literal["p2-label-noise-shift-protocol/2"]
    status: Literal["frozen_protocol_candidate_not_registered"]
    artifacts: ProtocolArtifactBindings
    split_algorithm: SplitAlgorithmContract
    dataset_splits: tuple[DatasetSplitReceipt, ...]
    preprocessing: PreprocessingContract
    models: ModelRuntimeContract
    intervention: InterventionRuntimeContract
    prior_shift: PriorShiftRuntimeContract
    shift_estimators: ShiftEstimatorRuntimeContract
    inference: InferenceRuntimeContract
    decision: DecisionRuntimeContract
    governance: ProtocolGovernance
    outcome_fields_forbidden: Literal[True]
    model_fitted: Literal[False]
    predictive_metrics_generated: Literal[False]
    sealed_outcomes_generated: Literal[False]

    @model_validator(mode="after")
    def _dataset_census_is_complete(self) -> V3ConfirmatoryProtocol:
        census = tuple((item.dataset_id, item.role, item.seed) for item in self.dataset_splits)
        if census != (
            ("uci_default_of_credit_card_clients", "primary", 2718),
            (
                "uci_online_shoppers_purchasing_intention",
                "external_replication",
                3141,
            ),
        ):
            raise ValueError("the complete ordered protocol dataset census is required")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class DirectionEvidence(_StrictFrozenModel):
    direction: Direction
    net_effects: dict[str, float]
    bootstrap_lower_bounds: dict[str, float]
    dataset_p_values: dict[str, float]
    technical_controls_pass: bool
    prior_only_label_noise_admissions: int = Field(ge=0)
    assumptions_pass: bool

    @model_validator(mode="after")
    def _dataset_evidence_is_complete(self) -> DirectionEvidence:
        expected = {
            "uci_default_of_credit_card_clients",
            "uci_online_shoppers_purchasing_intention",
        }
        for values in (self.net_effects, self.bootstrap_lower_bounds, self.dataset_p_values):
            if set(values) != expected or any(not math.isfinite(value) for value in values.values()):
                raise ValueError("direction evidence requires finite values for both datasets")
        if any(not 0.0 <= value <= 1.0 for value in self.dataset_p_values.values()):
            raise ValueError("dataset p-values must lie in [0, 1]")
        return self


class DirectionDecision(_StrictFrozenModel):
    direction: Direction
    disposition: Literal["pass", "fail", "abstain"]
    iut_p_value: float
    holm_adjusted_p_value: float


class CrossDatasetDecision(_StrictFrozenModel):
    direction_decisions: tuple[DirectionDecision, ...]
    claim_allowed: bool
    disposition: Literal["cross_dataset_admission", "fail_closed", "abstain"]


def _token(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Integral):
        return str(int(value))
    if isinstance(value, Real):
        number = float(value)
        if not math.isfinite(number):
            raise V3ProtocolError("split inputs must be finite")
        if number.is_integer():
            return str(int(number))
        return format(number, ".17g")
    token = str(value).strip()
    if not token:
        raise V3ProtocolError("split inputs must not be blank")
    return token


def _largest_remainder(total: int, fractions: Sequence[Fraction]) -> tuple[int, ...]:
    if total < 0 or not fractions or sum(fractions) != 1:
        raise V3ProtocolError("largest-remainder allocation requires a valid total and fractions")
    exact = tuple(Fraction(total) * fraction for fraction in fractions)
    counts = [value.numerator // value.denominator for value in exact]
    remaining = total - sum(counts)
    order = sorted(range(len(exact)), key=lambda index: (-(exact[index] - counts[index]), index))
    for index in order[:remaining]:
        counts[index] += 1
    return tuple(counts)


def _record_id(dataset: V3DatasetBinding, frame: pd.DataFrame, index: int) -> str:
    if dataset.identifier_columns:
        return "|".join(_token(frame.iloc[index][column]) for column in dataset.identifier_columns)
    return f"{dataset.dataset_id}-row-{index:05d}"


def compile_dataset_split_receipt(
    *,
    dataset: V3DatasetBinding,
    frame: pd.DataFrame,
    target_binding_sha256: str,
    record_identity_sha256: str,
) -> DatasetSplitReceipt:
    """Compile deterministic group-aware membership without fitting an outcome model."""

    if len(frame) != dataset.expected_row_count:
        raise V3ProtocolError("split compiler received an unexpected row count")
    labels = tuple(_token(value).lower() for value in frame[dataset.target.column].tolist())
    allowed = (dataset.target.negative_token, dataset.target.positive_token)
    if set(labels) != set(allowed):
        raise V3ProtocolError("split compiler received an unexpected target encoding")
    records = tuple(_record_id(dataset, frame, index) for index in range(len(frame)))
    if len(set(records)) != len(records):
        raise V3ProtocolError("split compiler requires unique record identities")

    groups: dict[str, list[int]] = defaultdict(list)
    for position, (_, row) in enumerate(
        frame.loc[:, list(dataset.analysis_features)].iterrows()
    ):
        values = tuple(_token(row[column]) for column in dataset.analysis_features)
        groups[canonical_sha256({"analysis_features": values})].append(position)

    fractions = (Fraction(3, 5), Fraction(1, 5), Fraction(1, 5))
    class_targets = {
        label: _largest_remainder(labels.count(label), fractions) for label in allowed
    }
    total_targets = _largest_remainder(len(frame), fractions)
    assigned_total = [0, 0, 0]
    assigned_class = {label: [0, 0, 0] for label in allowed}
    assignments: dict[str, int] = {}

    def group_order(item: tuple[str, list[int]]) -> tuple[int, int, str]:
        group_hash, indices = item
        counts = tuple(sum(labels[index] == label for index in indices) for label in allowed)
        tie = hashlib.sha256(f"{dataset.split_seed}:{group_hash}".encode()).hexdigest()
        return (-len(indices), -max(counts), tie)

    for group_hash, indices in sorted(groups.items(), key=group_order):
        group_counts = {
            label: sum(labels[index] == label for index in indices) for label in allowed
        }
        candidates: list[tuple[Fraction, Fraction, int]] = []
        for partition_index in range(3):
            overflow = Fraction(
                max(0, assigned_total[partition_index] + len(indices) - total_targets[partition_index]),
                max(total_targets[partition_index], 1),
            )
            score = Fraction(0)
            for candidate_partition in range(3):
                total_value = assigned_total[candidate_partition]
                if candidate_partition == partition_index:
                    total_value += len(indices)
                score += Fraction(
                    (total_value - total_targets[candidate_partition]) ** 2,
                    max(total_targets[candidate_partition] ** 2, 1),
                )
                for label in allowed:
                    class_value = assigned_class[label][candidate_partition]
                    if candidate_partition == partition_index:
                        class_value += group_counts[label]
                    target = class_targets[label][candidate_partition]
                    score += Fraction((class_value - target) ** 2, max(target**2, 1))
                    overflow += Fraction(max(0, class_value - target), max(target, 1))
            candidates.append((overflow, score, partition_index))
        selected = min(candidates)[2]
        assignments[group_hash] = selected
        assigned_total[selected] += len(indices)
        for label in allowed:
            assigned_class[label][selected] += group_counts[label]

    membership: list[tuple[str, str]] = []
    group_assignment: list[tuple[str, str]] = []
    sealed_records: list[str] = []
    for group_hash, indices in sorted(groups.items()):
        partition = PARTITIONS[assignments[group_hash]]
        group_assignment.append((group_hash, partition))
        for index in indices:
            membership.append((records[index], partition))
            if partition == "sealed_test":
                sealed_records.append(records[index])
    membership.sort()
    sealed_records.sort()

    partition_counts = {
        partition: assigned_total[index] for index, partition in enumerate(PARTITIONS)
    }
    partition_class_counts: dict[str, dict[str, int]] = {
        partition: {
            label: assigned_class[label][index] for label in allowed
        }
        for index, partition in enumerate(PARTITIONS)
    }
    return DatasetSplitReceipt(
        dataset_id=dataset.dataset_id,
        role=dataset.role,
        seed=dataset.split_seed,
        record_count=len(frame),
        group_count=len(groups),
        target_binding_sha256=target_binding_sha256,
        record_identity_sha256=record_identity_sha256,
        partition_counts=partition_counts,
        partition_class_counts=partition_class_counts,
        membership_sha256=canonical_sha256({"membership": membership}),
        group_assignment_sha256=canonical_sha256({"groups": group_assignment}),
        sealed_membership_sha256=canonical_sha256({"record_ids": sealed_records}),
        duplicate_group_cross_partition_count=0,
        labels_used_only_for_stratification=True,
        model_fitted=False,
        predictive_metrics_generated=False,
    )


def compile_v3_split_receipts(
    manifest: V3DatasetBindingManifest,
    receipt: V3DatasetBindingReceipt,
    *,
    archive_directory: str | Path,
) -> tuple[DatasetSplitReceipt, ...]:
    """Compile both split receipts from the SHA-bound official snapshots."""

    if manifest.canonical_sha256() != receipt.manifest_sha256:
        raise V3ProtocolError("dataset manifest and receipt are not bound together")
    audits = {item.dataset_id: item for item in receipt.datasets}
    output: list[DatasetSplitReceipt] = []
    for dataset in manifest.datasets:
        audit = audits.get(dataset.dataset_id)
        if audit is None:
            raise V3ProtocolError("dataset audit census is incomplete")
        _, frame = load_v3_dataset_snapshot_for_registration(
            dataset=dataset,
            archive_path=Path(archive_directory) / dataset.archive.file_name,
        )
        output.append(
            compile_dataset_split_receipt(
                dataset=dataset,
                frame=frame,
                target_binding_sha256=audit.target_binding_sha256,
                record_identity_sha256=audit.record_identity_sha256,
            )
        )
    return tuple(output)


def reciprocal_pair_count(*, source_class_count: int, opposite_class_count: int, rate: float) -> int:
    """Return the feasible one-to-one prevalence-preserving control pair count."""

    if source_class_count <= 0 or opposite_class_count <= 0 or not 0.0 < rate < 1.0:
        raise V3ProtocolError("reciprocal control requires two nonempty classes and a valid rate")
    requested = math.floor(rate * source_class_count)
    return min(requested, source_class_count, opposite_class_count)


def target_environment_class_counts(
    *, total: int, source_positive_prior: float, odds_multiplier: float
) -> tuple[int, int]:
    """Freeze integer negative/positive counts for a controlled prior environment."""

    if total < 2 or not 0.0 < source_positive_prior < 1.0:
        raise V3ProtocolError("prior environments require a binary pool and at least two records")
    if odds_multiplier <= 0.0 or not math.isfinite(odds_multiplier):
        raise V3ProtocolError("prior odds multiplier must be finite and positive")
    odds = source_positive_prior / (1.0 - source_positive_prior) * odds_multiplier
    target_positive_prior = odds / (1.0 + odds)
    positive_fraction = Fraction.from_float(target_positive_prior)
    negative, positive = _largest_remainder(
        total,
        (1 - positive_fraction, positive_fraction),
    )
    if min(negative, positive) == 0:
        raise V3ProtocolError("controlled prior environment would remove a class")
    return negative, positive


def holm_adjusted_p_values(p_values: Mapping[str, float]) -> dict[str, float]:
    """Return deterministic Holm step-down adjusted p-values."""

    if not p_values or any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in p_values.values()):
        raise V3ProtocolError("Holm correction requires finite p-values in [0, 1]")
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    adjusted: dict[str, float] = {}
    running = 0.0
    count = len(ordered)
    for rank, (name, value) in enumerate(ordered):
        running = max(running, min(1.0, (count - rank) * value))
        adjusted[name] = running
    return adjusted


def evaluate_cross_dataset_decision(
    evidence: Sequence[DirectionEvidence],
) -> CrossDatasetDecision:
    """Evaluate the frozen conjunction on synthetic or future sealed evidence."""

    items = tuple(evidence)
    if tuple(item.direction for item in items) != ("yes_to_no", "no_to_yes"):
        raise V3ProtocolError("decision requires both directions in canonical order")
    iut: dict[str, float] = {
        item.direction: max(item.dataset_p_values.values()) for item in items
    }
    adjusted = holm_adjusted_p_values(iut)
    decisions: list[DirectionDecision] = []
    for item in items:
        if not item.assumptions_pass:
            disposition: Literal["pass", "fail", "abstain"] = "abstain"
        else:
            passed = (
                all(value >= 0.05 for value in item.net_effects.values())
                and all(value > 0.0 for value in item.bootstrap_lower_bounds.values())
                and adjusted[item.direction] < 0.05
                and item.technical_controls_pass
                and item.prior_only_label_noise_admissions == 0
            )
            disposition = "pass" if passed else "fail"
        decisions.append(
            DirectionDecision(
                direction=item.direction,
                disposition=disposition,
                iut_p_value=iut[item.direction],
                holm_adjusted_p_value=adjusted[item.direction],
            )
        )
    claim_allowed = any(item.disposition == "pass" for item in decisions)
    if claim_allowed:
        final: Literal["cross_dataset_admission", "fail_closed", "abstain"] = (
            "cross_dataset_admission"
        )
    elif any(item.disposition == "abstain" for item in decisions):
        final = "abstain"
    else:
        final = "fail_closed"
    return CrossDatasetDecision(
        direction_decisions=tuple(decisions),
        claim_allowed=claim_allowed,
        disposition=final,
    )


def load_v3_confirmatory_protocol(
    path: str | Path = DEFAULT_V3_PROTOCOL_PATH,
) -> V3ConfirmatoryProtocol:
    """Load the strict protocol candidate."""

    try:
        return V3ConfirmatoryProtocol.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise V3ProtocolError("v3 confirmatory protocol is unavailable or invalid") from exc


def verify_v3_protocol_artifacts(
    protocol: V3ConfirmatoryProtocol,
    *,
    root: str | Path = ".",
) -> tuple[V3StudyDesign, V3DatasetBindingManifest, V3DatasetBindingReceipt]:
    """Verify every tracked predecessor and outcome-free binding transitively."""

    repository = Path(root)
    checked = V3ConfirmatoryProtocol.model_validate(protocol.model_dump())
    design = load_v3_study_design(repository / checked.artifacts.design_uri)
    manifest = load_v3_dataset_binding_manifest(repository / checked.artifacts.dataset_manifest_uri)
    receipt = load_v3_dataset_binding_receipt(repository / checked.artifacts.dataset_receipt_uri)
    verify_v3_predecessor(design, root=repository)
    verify_v3_dataset_binding_design(manifest, root=repository)
    if design.canonical_sha256() != checked.artifacts.design_sha256:
        raise V3ProtocolError("protocol is bound to another v3 design")
    if manifest.canonical_sha256() != checked.artifacts.dataset_manifest_sha256:
        raise V3ProtocolError("protocol is bound to another dataset manifest")
    if receipt.canonical_sha256() != checked.artifacts.dataset_receipt_sha256:
        raise V3ProtocolError("protocol is bound to another dataset receipt")
    if design.predecessor.v2_result_store_sha256 != checked.artifacts.v2_result_store_sha256:
        raise V3ProtocolError("protocol is bound to another v2 result store")
    expected = {
        item.dataset_id: (item.target_binding_sha256, item.record_identity_sha256)
        for item in receipt.datasets
    }
    observed = {
        item.dataset_id: (item.target_binding_sha256, item.record_identity_sha256)
        for item in checked.dataset_splits
    }
    if observed != expected:
        raise V3ProtocolError("split receipts are not bound to the D4A dataset receipt")
    return design, manifest, receipt


def verify_compiled_split_receipts(
    protocol: V3ConfirmatoryProtocol,
    observed: Sequence[DatasetSplitReceipt],
) -> None:
    """Fail closed unless locally recompiled membership matches the protocol."""

    if canonical_sha256([item.model_dump(mode="json") for item in observed]) != canonical_sha256(
        [item.model_dump(mode="json") for item in protocol.dataset_splits]
    ):
        raise V3ProtocolError("recompiled split receipts differ from the frozen protocol")
