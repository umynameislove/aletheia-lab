"""Outcome-free design contract for a shift-aware label-noise study.

The design responds to the frozen v2 replication without rewriting it.  It
orthogonalizes label corruption from class-prior shift, reserves two new
datasets for later registration, and defines a nuisance-matched primary
contrast.  This module cannot authorize or execute a confirmatory outcome.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from pathlib import Path
from statistics import NormalDist
from typing import Final, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN

V3_DESIGN_SCHEMA_VERSION: Final[Literal["p2-label-noise-shift-design/1"]] = (
    "p2-label-noise-shift-design/1"
)
DEFAULT_V3_DESIGN_PATH: Final[Path] = Path("configs/benchmark/p2_label_noise_shift_v3_design.json")

Direction = Literal["yes_to_no", "no_to_yes"]
DatasetRole = Literal["primary", "external_replication"]


class V3DesignError(ValueError):
    """Raised when the prospective design or its predecessor is invalid."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PredecessorEvidence(_StrictFrozenModel):
    v2_protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    v2_result_store_sha256: str = Field(pattern=SHA256_PATTERN)
    closeout_receipt_uri: Literal[
        "configs/benchmark/provenance/p2_label_noise_confirmatory_v2_closeout_receipt.json"
    ]
    closeout_receipt_file_sha256: str = Field(pattern=SHA256_PATTERN)
    root_cause_summary_uri: Literal[
        "configs/benchmark/provenance/p2_label_noise_confirmatory_v2_bank_root_cause_summary.json"
    ]
    root_cause_summary_file_sha256: str = Field(pattern=SHA256_PATTERN)
    registered_disposition: Literal["primary_dataset_bounded_admission"]
    cross_dataset_claim_allowed: Literal[False]
    audit_disposition: Literal["temporal_prior_shift_supported"]
    implementation_defects_detected: Literal[False]
    registered_decision_unchanged: Literal[True]


class SplitPlan(_StrictFrozenModel):
    strategy: Literal["seeded_stratified"]
    train_fraction: float
    development_fraction: float
    sealed_test_fraction: float
    seed: int = Field(ge=0)
    sealed_test_single_open: Literal[True]

    @model_validator(mode="after")
    def _split_is_complete(self) -> SplitPlan:
        fractions = (
            self.train_fraction,
            self.development_fraction,
            self.sealed_test_fraction,
        )
        if any(not math.isfinite(value) or value <= 0.0 for value in fractions):
            raise ValueError("split fractions must be finite and positive")
        if not math.isclose(math.fsum(fractions), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("split fractions must sum to one")
        if fractions != (0.6, 0.2, 0.2):
            raise ValueError("the design uses one common 60/20/20 split policy")
        return self


class NewDatasetPlan(_StrictFrozenModel):
    dataset_id: Literal[
        "uci_default_of_credit_card_clients",
        "uci_online_shoppers_purchasing_intention",
    ]
    role: DatasetRole
    uci_id: Literal[350, 468]
    doi: Literal["10.24432/C55S3H", "10.24432/C5F88Q"]
    source_uri: Literal[
        "https://archive.ics.uci.edu/dataset/350/default+of+credit+card+clients",
        "https://archive.ics.uci.edu/dataset/468/online+shoppers+purchasing+intention+dataset",
    ]
    license: Literal["CC_BY_4_0"]
    expected_record_count: Literal[30000, 12330]
    binary_target_required: Literal[True]
    minimum_records_per_class: Literal[1000]
    no_confirmatory_outcome_previously_opened: Literal[True]
    snapshot_sha256_required_before_registration: Literal[True]
    post_outcome_features_must_be_excluded: Literal[True]
    split: SplitPlan

    @model_validator(mode="after")
    def _identity_matches_role(self) -> NewDatasetPlan:
        expected = {
            "uci_default_of_credit_card_clients": (
                "primary",
                350,
                "10.24432/C55S3H",
                30000,
                2718,
            ),
            "uci_online_shoppers_purchasing_intention": (
                "external_replication",
                468,
                "10.24432/C5F88Q",
                12330,
                3141,
            ),
        }[self.dataset_id]
        observed = (
            self.role,
            self.uci_id,
            self.doi,
            self.expected_record_count,
            self.split.seed,
        )
        if observed != expected:
            raise ValueError("dataset identity, role, metadata and split seed must agree")
        return self


class SeedRange(_StrictFrozenModel):
    first: int = Field(ge=0)
    last: int = Field(ge=0)
    count: int = Field(ge=1)

    @model_validator(mode="after")
    def _range_is_complete(self) -> SeedRange:
        if self.last - self.first + 1 != self.count:
            raise ValueError("seed range is not contiguous or does not match its count")
        return self

    def values(self) -> tuple[int, ...]:
        return tuple(range(self.first, self.last + 1))


class ModelDesign(_StrictFrozenModel):
    primary_model: Literal["logistic_regression"]
    primary_parameters: tuple[
        Literal["c=1.0", "solver=lbfgs", "max_iter=1000", "random_state=42"], ...
    ]
    secondary_sensitivity_model: Literal["hist_gradient_boosting"]
    secondary_parameters: tuple[
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
    preprocessing_fit_on_training_only: Literal[True]
    development_only_calibration: Literal[True]
    sealed_labels_for_model_selection_forbidden: Literal[True]
    hyperparameter_search_forbidden: Literal[True]
    secondary_model_cannot_rescue_primary: Literal[True]

    @model_validator(mode="after")
    def _models_are_fully_specified(self) -> ModelDesign:
        if self.primary_parameters != (
            "c=1.0",
            "solver=lbfgs",
            "max_iter=1000",
            "random_state=42",
        ):
            raise ValueError("the primary logistic-regression specification is fixed")
        if self.secondary_parameters != (
            "learning_rate=0.1",
            "max_iter=100",
            "max_leaf_nodes=31",
            "l2_regularization=0.0",
            "early_stopping=false",
            "random_state=43",
        ):
            raise ValueError("the secondary sensitivity-model specification is fixed")
        return self


class CorruptionFactor(_StrictFrozenModel):
    directions: tuple[Direction, ...]
    conditional_rates: tuple[float, ...]
    rate_denominator: Literal["source_class_count"]
    co_primary_rate: float
    selection_is_feature_blind: Literal[True]
    only_training_targets_may_change: Literal[True]
    corruption_seeds: SeedRange

    @model_validator(mode="after")
    def _factor_is_complete(self) -> CorruptionFactor:
        if self.directions != ("yes_to_no", "no_to_yes"):
            raise ValueError("both corruption directions are required")
        if self.conditional_rates != (0.1, 0.2, 0.3):
            raise ValueError("the complete prospective dose grid is required")
        if not math.isclose(self.co_primary_rate, 0.3, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("the co-primary corruption rate is 0.30")
        if self.corruption_seeds != SeedRange(first=6101, last=6150, count=50):
            raise ValueError("the complete new corruption-seed namespace is required")
        return self


class OrthogonalizationDesign(_StrictFrozenModel):
    primary_treatment: Literal["class_conditional_label_corruption"]
    nuisance_control: Literal["clean_labels_effective_prior_matched_sample_weight"]
    nuisance_match_target: Literal["corrupted_training_label_prevalence"]
    prevalence_preserving_control: Literal["reciprocal_matched_pair_flip"]
    primary_contrast: Literal["corrupted_model_minus_prior_matched_clean_label_model"]
    clean_reference_required: Literal[True]
    serialization_roundtrip_required: Literal[True]
    label_repair_required: Literal[True]
    exact_prevalence_reconciliation_required: Literal[True]
    controls_cannot_rescue_primary: Literal[True]


class PriorEnvironmentDesign(_StrictFrozenModel):
    construction: Literal["label_conditioned_sealed_evaluation_resampling"]
    odds_multipliers: tuple[float, ...]
    environment_seeds: SeedRange
    target_labels_hidden_from_estimators: Literal[True]
    target_labels_available_only_to_generator_and_scorer: Literal[True]
    class_conditionals_preserved_by_construction: Literal[True]
    natural_temporal_bank_is_exploratory_only: Literal[True]

    @model_validator(mode="after")
    def _environment_grid_is_symmetric(self) -> PriorEnvironmentDesign:
        if self.odds_multipliers != (0.25, 1.0, 4.0):
            raise ValueError("prior environments require symmetric odds tilts")
        if self.environment_seeds != SeedRange(first=7101, last=7150, count=50):
            raise ValueError("the complete target-environment seed namespace is required")
        return self


class ShiftEstimatorDesign(_StrictFrozenModel):
    baselines: tuple[
        Literal[
            "unadjusted_v2_log_loss",
            "oracle_prior_ratio",
            "bbse",
            "mlls_em",
            "rlls",
        ],
        ...,
    ]
    calibration: Literal["development_logit_intercept_slope"]
    calibration_uses_sealed_labels: Literal[False]
    ill_conditioned_estimator_action: Literal["abstain"]
    silent_weight_clipping_forbidden: Literal[True]
    oracle_is_upper_bound_not_deployable_baseline: Literal[True]
    class_conditional_shift_diagnostic: Literal["classwise_mmd_permutation"]
    diagnostic_resamples: Literal[2000]
    diagnostic_seed: Literal[314160]
    diagnostic_multiplicity: Literal["holm_across_classes_and_datasets"]
    failed_label_shift_assumption_action: Literal["abstain_from_pure_label_shift_claim"]

    @model_validator(mode="after")
    def _baseline_set_is_complete(self) -> ShiftEstimatorDesign:
        expected = (
            "unadjusted_v2_log_loss",
            "oracle_prior_ratio",
            "bbse",
            "mlls_em",
            "rlls",
        )
        if self.baselines != expected:
            raise ValueError("all preregistered shift baselines are required")
        return self


class EndpointDesign(_StrictFrozenModel):
    primary_metric: Literal["reference_prior_standardized_log_loss"]
    reference_positive_prior: float
    primary_estimand: Literal["relative_net_corruption_effect_vs_prior_matched_clean_control"]
    minimum_practical_effect: float
    legacy_comparators: tuple[
        Literal["accuracy_regression_v1", "raw_target_prior_log_loss_v2"], ...
    ]
    secondary_metrics: tuple[
        Literal[
            "target_prior_log_loss",
            "brier_score",
            "calibration_intercept",
            "calibration_slope",
            "roc_auc",
            "balanced_accuracy",
            "classwise_log_loss",
        ],
        ...,
    ]
    secondary_metrics_cannot_rescue_primary: Literal[True]

    @model_validator(mode="after")
    def _endpoint_is_reference_standardized(self) -> EndpointDesign:
        if not math.isclose(self.reference_positive_prior, 0.5, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("the reference population uses equal class weight")
        if not math.isclose(self.minimum_practical_effect, 0.05, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("the prospective practical-effect threshold is 0.05")
        if self.legacy_comparators != (
            "accuracy_regression_v1",
            "raw_target_prior_log_loss_v2",
        ):
            raise ValueError("both historical comparators are mandatory")
        expected_secondary = (
            "target_prior_log_loss",
            "brier_score",
            "calibration_intercept",
            "calibration_slope",
            "roc_auc",
            "balanced_accuracy",
            "classwise_log_loss",
        )
        if self.secondary_metrics != expected_secondary:
            raise ValueError("secondary metrics must use the complete ordered set")
        return self


class InferenceDesign(_StrictFrozenModel):
    replicate_unit: Literal["corruption_seed"]
    replicate_count_per_cell: Literal[50]
    target_standardized_effect: float
    target_power: float
    familywise_alpha: float
    worst_case_direction_alpha: float
    cross_dataset_test: Literal["intersection_union_max_p"]
    direction_multiplicity: Literal["holm_two_directions"]
    interval_method: Literal["two_way_product_weight_bootstrap"]
    bootstrap_factors: tuple[Literal["evaluation_record", "corruption_seed"], ...]
    bootstrap_resamples: Literal[10000]
    bootstrap_seed: Literal[271829]
    hypothesis_test: Literal["paired_seed_level_sign_flip"]
    hypothesis_test_resamples: Literal[100000]
    hypothesis_test_seed: Literal[161804]
    no_early_stopping: Literal[True]
    report_every_cell_and_baseline: Literal[True]

    @model_validator(mode="after")
    def _power_and_uncertainty_are_sufficient(self) -> InferenceDesign:
        expected_floats = (
            (self.target_standardized_effect, 0.5, "target standardized effect"),
            (self.target_power, 0.9, "target power"),
            (self.familywise_alpha, 0.05, "family-wise alpha"),
            (self.worst_case_direction_alpha, 0.025, "worst-case direction alpha"),
        )
        for observed, expected, label in expected_floats:
            if not math.isclose(observed, expected, rel_tol=0.0, abs_tol=1e-12):
                raise ValueError(f"{label} is prospectively fixed at {expected}")
        if self.bootstrap_factors != ("evaluation_record", "corruption_seed"):
            raise ValueError("primary uncertainty must resample records and corruption seeds")
        planned = normal_approximation_power(
            replicate_count=self.replicate_count_per_cell,
            standardized_effect=self.target_standardized_effect,
            one_sided_alpha=self.worst_case_direction_alpha,
        )
        if planned < self.target_power:
            raise ValueError("the corruption-seed census does not meet planned power")
        return self


class DecisionDesign(_StrictFrozenModel):
    direction_pass_requirements: tuple[
        Literal[
            "net_effect_at_least_0.05_in_both_new_datasets",
            "bootstrap_lower_bound_above_0_in_both_new_datasets",
            "intersection_union_p_then_holm_below_0.05",
            "all_orthogonalization_and_technical_controls_pass",
            "zero_label_noise_admissions_in_prior_only_negative_controls",
        ],
        ...,
    ]
    cross_dataset_claim_requires_same_direction_to_pass: Literal[True]
    primary_failure_cannot_be_rescued_by_secondary_metrics: Literal[True]
    estimator_comparison_cannot_change_construct_admission: Literal[True]
    assumption_failure_requires_abstention: Literal[True]
    additional_grid_after_outcomes_forbidden: Literal[True]

    @model_validator(mode="after")
    def _decision_is_conjunctive(self) -> DecisionDesign:
        expected = (
            "net_effect_at_least_0.05_in_both_new_datasets",
            "bootstrap_lower_bound_above_0_in_both_new_datasets",
            "intersection_union_p_then_holm_below_0.05",
            "all_orthogonalization_and_technical_controls_pass",
            "zero_label_noise_admissions_in_prior_only_negative_controls",
        )
        if self.direction_pass_requirements != expected:
            raise ValueError("the complete cross-dataset decision rule is required")
        return self


class GovernanceDesign(_StrictFrozenModel):
    design_is_not_execution_authority: Literal[True]
    v2_artifacts_remain_immutable: Literal[True]
    v2_opened_partitions_forbidden_for_v3_confirmation: Literal[True]
    dataset_bytes_and_columns_must_be_pinned_before_registration: Literal[True]
    protocol_only_registration_commit_required: Literal[True]
    required_future_tag: Literal["p2-label-noise-shift-factorial-v3"]
    independent_methods_review_required: Literal[True]
    review_must_precede_registration: Literal[True]
    execution_implementation_must_follow_registration: Literal[True]
    target_outcomes_cannot_tune_design: Literal[True]
    primary_and_replication_released_together: Literal[True]


class V3StudyDesign(_StrictFrozenModel):
    schema_version: Literal["p2-label-noise-shift-design/1"]
    status: Literal["draft_outcome_blind_not_registered"]
    research_question: Literal[
        "Can a shift-aware verification gate distinguish training-label corruption from class-prior shift while preserving cross-dataset sensitivity to corruption?"
    ]
    predecessor: PredecessorEvidence
    new_datasets: tuple[NewDatasetPlan, ...]
    excluded_confirmation_datasets: tuple[
        Literal["telco_customer_churn", "uci_bank_marketing_additional_full"], ...
    ]
    models: ModelDesign
    corruption: CorruptionFactor
    orthogonalization: OrthogonalizationDesign
    prior_environments: PriorEnvironmentDesign
    shift_estimators: ShiftEstimatorDesign
    endpoints: EndpointDesign
    inference: InferenceDesign
    decision: DecisionDesign
    governance: GovernanceDesign
    outcome_fields_forbidden: Literal[True]

    @model_validator(mode="after")
    def _design_is_complete_and_non_reusing(self) -> V3StudyDesign:
        if tuple(item.role for item in self.new_datasets) != (
            "primary",
            "external_replication",
        ):
            raise ValueError("new datasets must be primary then external replication")
        if len({item.dataset_id for item in self.new_datasets}) != 2:
            raise ValueError("two distinct new confirmatory datasets are required")
        if self.excluded_confirmation_datasets != (
            "telco_customer_churn",
            "uci_bank_marketing_additional_full",
        ):
            raise ValueError("both outcome-opened v2 datasets must remain excluded")
        if self.corruption.corruption_seeds.values() == (
            self.prior_environments.environment_seeds.values()
        ):
            raise ValueError("corruption and environment seed namespaces must be independent")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def normal_approximation_power(
    *, replicate_count: int, standardized_effect: float, one_sided_alpha: float
) -> float:
    """Prospective paired-normal power calculation used only for planning."""

    if replicate_count < 2 or standardized_effect <= 0.0 or not math.isfinite(standardized_effect):
        raise V3DesignError("power planning requires positive effect and at least two replicates")
    if not 0.0 < one_sided_alpha < 1.0:
        raise V3DesignError("one-sided alpha must lie strictly between zero and one")
    critical = NormalDist().inv_cdf(1.0 - one_sided_alpha)
    return NormalDist().cdf(math.sqrt(replicate_count) * standardized_effect - critical)


def target_prior_from_odds_multiplier(source_prior: float, multiplier: float) -> float:
    """Tilt class odds while preserving class-conditional distributions."""

    if not 0.0 < source_prior < 1.0 or multiplier <= 0.0 or not math.isfinite(multiplier):
        raise V3DesignError("prior tilting requires a valid source prior and multiplier")
    source_odds = source_prior / (1.0 - source_prior)
    target_odds = source_odds * multiplier
    return target_odds / (1.0 + target_odds)


def clean_label_prior_match_weights(
    clean_targets: Sequence[int], *, target_positive_prior: float
) -> tuple[float, ...]:
    """Create clean-label sample weights with a requested effective class prior."""

    values = tuple(clean_targets)
    if not values or set(values) != {0, 1} or not 0.0 < target_positive_prior < 1.0:
        raise V3DesignError("prior matching requires nonempty binary targets and a valid prior")
    source_prior = math.fsum(values) / len(values)
    positive_weight = target_positive_prior / source_prior
    negative_weight = (1.0 - target_positive_prior) / (1.0 - source_prior)
    return tuple(positive_weight if target else negative_weight for target in values)


def effective_positive_prior(targets: Sequence[int], weights: Sequence[float]) -> float:
    """Recompute the class prior represented by nonnegative sample weights."""

    labels = tuple(targets)
    values = tuple(weights)
    if len(labels) != len(values) or not labels or set(labels) != {0, 1}:
        raise V3DesignError("effective-prior inputs must be aligned and binary")
    if any(not math.isfinite(value) or value < 0.0 for value in values):
        raise V3DesignError("effective-prior weights must be finite and nonnegative")
    total = math.fsum(values)
    if total <= 0.0:
        raise V3DesignError("effective-prior weights must have positive mass")
    return (
        math.fsum(weight for target, weight in zip(labels, values, strict=True) if target) / total
    )


def reference_prior_standardized_log_loss(
    targets: Sequence[int],
    positive_probabilities: Sequence[float],
    *,
    reference_positive_prior: float = 0.5,
) -> float:
    """Score class-conditional losses under one fixed reference population."""

    labels = tuple(targets)
    probabilities = tuple(positive_probabilities)
    if len(labels) != len(probabilities) or not labels or set(labels) != {0, 1}:
        raise V3DesignError("standardized log loss requires aligned binary outcomes")
    if not 0.0 < reference_positive_prior < 1.0:
        raise V3DesignError("reference prior must lie strictly between zero and one")
    positive_losses: list[float] = []
    negative_losses: list[float] = []
    for target, probability in zip(labels, probabilities, strict=True):
        if not 0.0 <= probability <= 1.0 or not math.isfinite(probability):
            raise V3DesignError("probabilities must be finite and lie in [0, 1]")
        clipped = min(max(probability, 1e-15), 1.0 - 1e-15)
        if target:
            positive_losses.append(-math.log(clipped))
        else:
            negative_losses.append(-math.log1p(-clipped))
    positive_mean = math.fsum(positive_losses) / len(positive_losses)
    negative_mean = math.fsum(negative_losses) / len(negative_losses)
    return (
        reference_positive_prior * positive_mean + (1.0 - reference_positive_prior) * negative_mean
    )


def load_v3_study_design(path: str | Path = DEFAULT_V3_DESIGN_PATH) -> V3StudyDesign:
    """Load and strictly validate the prospective v3 design."""

    source = Path(path)
    try:
        payload = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise V3DesignError(f"cannot read v3 design: {source}") from exc
    try:
        return V3StudyDesign.model_validate_json(payload)
    except ValueError as exc:
        raise V3DesignError("v3 design violates its outcome-free contract") from exc


def verify_v3_predecessor(design: V3StudyDesign, *, root: str | Path = ".") -> None:
    """Verify the tracked v2 receipt and root-cause evidence bound by the design."""

    checked = V3StudyDesign.model_validate(design.model_dump())
    repository = Path(root)
    closeout_path = repository / checked.predecessor.closeout_receipt_uri
    audit_path = repository / checked.predecessor.root_cause_summary_uri
    try:
        closeout_bytes = closeout_path.read_bytes()
        audit_bytes = audit_path.read_bytes()
        closeout = json.loads(closeout_bytes)
        audit = json.loads(audit_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise V3DesignError("cannot verify v3 predecessor evidence") from exc
    if hashlib.sha256(closeout_bytes).hexdigest() != (
        checked.predecessor.closeout_receipt_file_sha256
    ):
        raise V3DesignError("v2 closeout receipt checksum mismatch")
    if hashlib.sha256(audit_bytes).hexdigest() != (
        checked.predecessor.root_cause_summary_file_sha256
    ):
        raise V3DesignError("v2 root-cause summary checksum mismatch")
    if not isinstance(closeout, dict) or not isinstance(audit, dict):
        raise V3DesignError("v3 predecessor artifacts must be JSON objects")
    protocol = closeout.get("protocol")
    artifact_identity = closeout.get("artifact_identity")
    decision = closeout.get("decision")
    if not all(isinstance(item, dict) for item in (protocol, artifact_identity, decision)):
        raise V3DesignError("v2 closeout receipt is structurally incomplete")
    protocol = cast(dict[str, object], protocol)
    artifact_identity = cast(dict[str, object], artifact_identity)
    decision = cast(dict[str, object], decision)
    if protocol.get("sha256") != checked.predecessor.v2_protocol_sha256:
        raise V3DesignError("v3 design is bound to another v2 protocol")
    if artifact_identity.get("result_store_sha256") != (checked.predecessor.v2_result_store_sha256):
        raise V3DesignError("v3 design is bound to another v2 result store")
    if (
        decision.get("disposition") != checked.predecessor.registered_disposition
        or decision.get("cross_dataset_claim_allowed") is not False
    ):
        raise V3DesignError("v2 registered decision does not match the v3 predecessor")
    if (
        audit.get("disposition") != checked.predecessor.audit_disposition
        or audit.get("registered_decision_unchanged") is not True
        or audit.get("implementation_defects") != []
    ):
        raise V3DesignError("v2 root-cause audit does not match the v3 predecessor")
