"""Frozen, outcome-free contract for the label-noise confirmatory study.

The alpha result is immutable.  This module validates a prospective protocol
that changes the scientific construct rather than tuning the observed alpha:
class-conditional corruption replaces uniform corruption, logarithmic loss
replaces accuracy as the sole eligibility endpoint, and all directions, doses,
replicates and controls are fixed before a confirmatory outcome exists.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256

CONFIRMATORY_PROTOCOL_SCHEMA_VERSION: Final[
    Literal["p2-label-noise-confirmatory-protocol/1"]
] = "p2-label-noise-confirmatory-protocol/1"
DEFAULT_CONFIRMATORY_PROTOCOL_PATH: Final[Path] = Path(
    "configs/benchmark/p2_label_noise_confirmatory_protocol.json"
)
SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")


class ConfirmatoryProtocolError(ValueError):
    """Raised when a protocol artifact violates the frozen research design."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PredecessorBinding(_StrictFrozenModel):
    merge_commit: str
    alpha_store_sha256: str
    recovery_report_sha256: str
    alpha_is_immutable: Literal[True]
    alpha_gate_status: Literal["fail"]
    alpha_missing_mechanism: Literal["label_noise"]

    @field_validator("alpha_store_sha256", "recovery_report_sha256")
    @classmethod
    def _is_sha256(cls, value: str) -> str:
        if SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError("predecessor bindings must be lowercase SHA-256 digests")
        return value

    @field_validator("merge_commit")
    @classmethod
    def _is_git_object_id(cls, value: str) -> str:
        if re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise ValueError("merge_commit must be a full SHA-1 Git object ID")
        return value


class SplitDesign(_StrictFrozenModel):
    strategy: Literal["seeded_stratified", "source_order_temporal"]
    train_fraction: float
    development_fraction: float
    sealed_test_fraction: float
    seed: int | None
    test_is_single_open: Literal[True]

    @model_validator(mode="after")
    def _split_is_complete_and_reproducible(self) -> SplitDesign:
        fractions = (self.train_fraction, self.development_fraction, self.sealed_test_fraction)
        if any(not math.isfinite(value) or value <= 0.0 for value in fractions):
            raise ValueError("split fractions must be finite and positive")
        if not math.isclose(math.fsum(fractions), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("split fractions must sum to one")
        if self.strategy == "seeded_stratified" and self.seed is None:
            raise ValueError("a seeded stratified split requires a seed")
        if self.strategy == "source_order_temporal" and self.seed is not None:
            raise ValueError("a source-order temporal split must not use a random seed")
        return self


class DatasetBinding(_StrictFrozenModel):
    dataset_id: str = Field(min_length=1)
    role: Literal["primary", "external_replication"]
    source_uri: str = Field(min_length=1)
    snapshot_sha256: str
    archive_sha256: str | None = None
    target_column: str = Field(min_length=1)
    positive_label: str = Field(min_length=1)
    excluded_features: tuple[str, ...]
    split: SplitDesign

    @field_validator("snapshot_sha256", "archive_sha256")
    @classmethod
    def _optional_sha256(cls, value: str | None) -> str | None:
        if value is not None and SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError("dataset checksums must be lowercase SHA-256 digests")
        return value

    @model_validator(mode="after")
    def _dataset_role_is_frozen(self) -> DatasetBinding:
        if len(set(self.excluded_features)) != len(self.excluded_features):
            raise ValueError("excluded dataset features must be unique")
        if self.role == "primary":
            if self.dataset_id != "telco_customer_churn":
                raise ValueError("the primary dataset cannot be selected after alpha")
            if self.split.strategy != "seeded_stratified" or self.split.seed != 314159:
                raise ValueError("the primary confirmatory split is frozen at seed 314159")
        else:
            if self.dataset_id != "uci_bank_marketing_additional_full":
                raise ValueError("the external replication dataset is frozen")
            if self.split.strategy != "source_order_temporal":
                raise ValueError("the external replication must preserve source time order")
            if self.excluded_features != ("duration",):
                raise ValueError("the post-call duration feature must be excluded")
        return self


class ModelBinding(_StrictFrozenModel):
    model_type: Literal["logistic_regression"]
    max_iter: Literal[1000]
    c: float = Field(gt=0.0)
    class_weight: None
    solver: Literal["lbfgs"]
    training_seed: Literal[42]
    probability_output_required: Literal[True]

    @field_validator("c")
    @classmethod
    def _regularization_is_frozen(cls, value: float) -> float:
        if not math.isclose(value, 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("logistic-regression C is frozen at 1.0")
        return value


class InterventionCell(_StrictFrozenModel):
    cell_id: str = Field(pattern=r"^ccn-(yes-to-no|no-to-yes)-(10|20|30)$")
    flip_direction: Literal["yes_to_no", "no_to_yes"]
    conditional_flip_rate: float
    rate_denominator: Literal["source_class_count"]
    hypothesis_role: Literal["co_primary", "dose_response_secondary"]
    primary_replicate_seeds: tuple[int, ...]
    replication_replicate_seeds: tuple[int, ...]

    @model_validator(mode="after")
    def _cell_matches_the_complete_grid(self) -> InterventionCell:
        if self.conditional_flip_rate not in {0.1, 0.2, 0.3}:
            raise ValueError("conditional label-noise rates are frozen at 0.10, 0.20 and 0.30")
        direction_token = self.flip_direction.replace("_", "-")
        rate_token = int(self.conditional_flip_rate * 100)
        if self.cell_id != f"ccn-{direction_token}-{rate_token}":
            raise ValueError("cell_id must encode its direction and rate")
        expected_role = "co_primary" if self.conditional_flip_rate == 0.3 else "dose_response_secondary"
        if self.hypothesis_role != expected_role:
            raise ValueError("only the two prespecified 30% cells are co-primary")
        if self.primary_replicate_seeds != tuple(range(4101, 4131)):
            raise ValueError("primary corruption seeds must be the complete frozen set")
        if self.replication_replicate_seeds != tuple(range(5101, 5131)):
            raise ValueError("replication corruption seeds must be the complete frozen set")
        return self


class ControlDesign(_StrictFrozenModel):
    control_ids: tuple[
        Literal[
            "clean_reference",
            "serialization_roundtrip",
            "symmetric_matched_count",
            "label_repair",
        ],
        ...,
    ]
    matched_on_dataset_split_model_and_seed: Literal[True]
    symmetric_control_is_not_a_failure_substitute: Literal[True]
    repair_must_restore_clean_artifact: Literal[True]

    @model_validator(mode="after")
    def _all_controls_are_present(self) -> ControlDesign:
        expected = (
            "clean_reference",
            "serialization_roundtrip",
            "symmetric_matched_count",
            "label_repair",
        )
        if self.control_ids != expected:
            raise ValueError("the complete ordered control set is required")
        return self


class EndpointDesign(_StrictFrozenModel):
    primary_metric: Literal["clean_test_log_loss"]
    primary_effect: Literal["relative_increase_from_paired_clean_reference"]
    minimum_practical_effect: float = Field(gt=0.0)
    legacy_comparator: Literal["accuracy_delta_threshold_0.01"]
    secondary_metrics: tuple[
        Literal[
            "brier_score",
            "roc_auc",
            "balanced_accuracy",
            "accuracy",
            "positive_recall",
            "negative_recall",
        ],
        ...,
    ]
    secondary_metrics_cannot_rescue_primary: Literal[True]

    @model_validator(mode="after")
    def _secondary_metrics_are_complete(self) -> EndpointDesign:
        if not math.isclose(
            self.minimum_practical_effect, 0.05, rel_tol=0.0, abs_tol=1e-12
        ):
            raise ValueError("the minimum practical relative effect is frozen at 0.05")
        expected = (
            "brier_score",
            "roc_auc",
            "balanced_accuracy",
            "accuracy",
            "positive_recall",
            "negative_recall",
        )
        if self.secondary_metrics != expected:
            raise ValueError("secondary metrics must use the frozen ordered set")
        return self


class InferenceDesign(_StrictFrozenModel):
    family_unit: Literal["dataset_model_direction_rate"]
    replicate_unit: Literal["corruption_seed"]
    replicate_count_per_cell: Literal[30]
    target_standardized_effect: float = Field(gt=0.0)
    target_power: float = Field(gt=0.0, lt=1.0)
    familywise_alpha: float = Field(gt=0.0, lt=1.0)
    multiplicity_method: Literal["holm_two_co_primary_directions"]
    interval_method: Literal["two_way_product_weight_bootstrap"]
    bootstrap_factors: tuple[Literal["evaluation_record", "corruption_seed"], ...]
    bootstrap_resamples: Literal[10000]
    bootstrap_seed: Literal[271828]
    hypothesis_test: Literal["paired_seed_level_sign_flip"]
    hypothesis_test_resamples: Literal[100000]
    hypothesis_test_seed: Literal[161803]
    no_early_stopping: Literal[True]
    report_every_cell: Literal[True]

    @model_validator(mode="after")
    def _crossed_uncertainty_is_preserved(self) -> InferenceDesign:
        frozen_floats = (
            (self.target_standardized_effect, 0.53, "target standardized effect"),
            (self.target_power, 0.80, "target power"),
            (self.familywise_alpha, 0.05, "family-wise alpha"),
        )
        for observed, expected, label in frozen_floats:
            if not math.isclose(observed, expected, rel_tol=0.0, abs_tol=1e-12):
                raise ValueError(f"{label} is frozen at {expected}")
        if self.bootstrap_factors != ("evaluation_record", "corruption_seed"):
            raise ValueError("uncertainty must resample records and corruption seeds")
        return self


class DecisionDesign(_StrictFrozenModel):
    direction_pass_requirements: tuple[
        Literal[
            "relative_log_loss_increase_at_least_0.05",
            "bootstrap_lower_bound_above_0",
            "holm_adjusted_one_sided_p_below_0.05",
            "all_technical_and_control_gates_pass",
        ],
        ...,
    ]
    primary_mechanism_gate: Literal["at_least_one_co_primary_direction_passes"]
    external_replication_cannot_rescue_primary: Literal[True]
    cross_dataset_claim_requires_same_direction_to_pass_both: Literal[True]
    failed_primary_action: Literal["retain_fail_closed_and_narrow_claim"]
    additional_grid_after_results_forbidden: Literal[True]

    @model_validator(mode="after")
    def _decision_rule_is_complete(self) -> DecisionDesign:
        expected = (
            "relative_log_loss_increase_at_least_0.05",
            "bootstrap_lower_bound_above_0",
            "holm_adjusted_one_sided_p_below_0.05",
            "all_technical_and_control_gates_pass",
        )
        if self.direction_pass_requirements != expected:
            raise ValueError("all scientific and technical pass requirements are mandatory")
        return self


class GovernanceDesign(_StrictFrozenModel):
    protocol_only_commit_required: Literal[True]
    required_git_tag: Literal["p2-label-noise-confirmatory-v1"]
    immutable_release_or_external_timestamp_required: Literal[True]
    execution_before_registration_forbidden: Literal[True]
    changes_require_new_protocol_version: Literal[True]
    outcome_analysts_cannot_change_admission_rule: Literal[True]
    human_reviewers_cannot_select_families: Literal[True]
    primary_and_replication_outputs_released_together: Literal[True]


class ConfirmatoryProtocol(_StrictFrozenModel):
    schema_version: Literal["p2-label-noise-confirmatory-protocol/1"]
    status: Literal["frozen_not_executed"]
    research_question: Literal[
        "Does class-conditional training-label corruption cause a reproducible degradation in clean-label probabilistic prediction quality?"
    ]
    predecessor: PredecessorBinding
    datasets: tuple[DatasetBinding, ...]
    model: ModelBinding
    intervention_cells: tuple[InterventionCell, ...]
    controls: ControlDesign
    endpoints: EndpointDesign
    inference: InferenceDesign
    decision: DecisionDesign
    governance: GovernanceDesign
    outcome_fields_forbidden: Literal[True]

    @model_validator(mode="after")
    def _protocol_is_complete_and_outcome_free(self) -> ConfirmatoryProtocol:
        if tuple(dataset.role for dataset in self.datasets) != (
            "primary",
            "external_replication",
        ):
            raise ValueError("datasets must contain primary then external replication")
        expected_grid = tuple(
            (direction, rate)
            for direction in ("yes_to_no", "no_to_yes")
            for rate in (0.1, 0.2, 0.3)
        )
        actual_grid = tuple(
            (cell.flip_direction, cell.conditional_flip_rate) for cell in self.intervention_cells
        )
        if actual_grid != expected_grid:
            raise ValueError("the complete canonical direction-by-dose grid is required")
        return self

    def canonical_sha256(self) -> str:
        """Bind every prospective choice without including any outcome."""

        return canonical_sha256(self.model_dump(mode="json"))


def load_confirmatory_protocol(
    path: str | Path = DEFAULT_CONFIRMATORY_PROTOCOL_PATH,
) -> ConfirmatoryProtocol:
    """Load and strictly validate the frozen protocol JSON."""

    source = Path(path)
    try:
        payload = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfirmatoryProtocolError(f"cannot read confirmatory protocol: {source}") from exc
    try:
        return ConfirmatoryProtocol.model_validate_json(payload)
    except ValueError as exc:
        raise ConfirmatoryProtocolError("confirmatory protocol violates its frozen contract") from exc


def verify_confirmatory_predecessor(
    protocol: ConfirmatoryProtocol, *, root: str | Path = "."
) -> None:
    """Bind a protocol to the exact local alpha, recovery report and dataset."""

    protocol = ConfirmatoryProtocol.model_validate(protocol.model_dump())
    repository = Path(root)
    alpha_manifest_path = repository / "experiments/p2/runs/alpha-primary-seed42/store-manifest.json"
    recovery_report_path = repository / "docs/p2-label-noise-recovery.md"
    baseline_path = (
        repository
        / "experiments/baseline/runs/logistic_regression_seed42/provenance.json"
    )
    try:
        alpha_manifest = json.loads(alpha_manifest_path.read_text(encoding="utf-8"))
        recovery_report = recovery_report_path.read_bytes()
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfirmatoryProtocolError("cannot verify confirmatory predecessor artifacts") from exc
    if not isinstance(alpha_manifest, dict) or (
        alpha_manifest.get("store_sha256") != protocol.predecessor.alpha_store_sha256
    ):
        raise ConfirmatoryProtocolError("confirmatory protocol is bound to another alpha store")
    if hashlib.sha256(recovery_report).hexdigest() != protocol.predecessor.recovery_report_sha256:
        raise ConfirmatoryProtocolError("confirmatory protocol is bound to another recovery report")
    primary = next(dataset for dataset in protocol.datasets if dataset.role == "primary")
    if not isinstance(baseline, dict) or baseline.get("dataset_sha256") != primary.snapshot_sha256:
        raise ConfirmatoryProtocolError("confirmatory protocol is bound to another primary dataset")
