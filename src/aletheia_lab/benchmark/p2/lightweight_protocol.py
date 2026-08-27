"""Outcome-blind registration contracts for lightweight mechanism studies."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Final, Literal, NoReturn, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN
from aletheia_lab.benchmark.p2.instrument_validity import (
    DEFAULT_INSTRUMENT_PROTOCOL_PATH,
    InstrumentValidityProtocol,
    load_instrument_validity_protocol,
    verify_instrument_validity_protocol,
)

LIGHTWEIGHT_PROTOCOL_SCHEMA_VERSION: Final[
    Literal["p2r-lightweight-confirmatory-protocol/1"]
] = "p2r-lightweight-confirmatory-protocol/1"
DEFAULT_DATA_DRIFT_PROTOCOL_PATH = Path(
    "configs/benchmark/p2r_data_drift_confirmatory_protocol.json"
)
DEFAULT_PREPROCESSING_PROTOCOL_PATH = Path(
    "configs/benchmark/p2r_preprocessing_confirmatory_protocol.json"
)

Sha256 = str
MechanismName = Literal["data_drift", "preprocessing_bug"]
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class LightweightProtocolError(ValueError):
    """Raised when a registered-study candidate is incomplete or inconsistent."""


def _fail(message: str) -> NoReturn:
    raise LightweightProtocolError(message)


def _revalidated(model: _ModelT) -> _ModelT:
    return type(model).model_validate(model.model_dump())


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise LightweightProtocolError(f"bound artifact is unavailable: {path}") from exc


def _json_sha256(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LightweightProtocolError(f"bound JSON artifact is invalid: {path}") from exc
    return canonical_sha256(payload)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class StudyArtifactBindings(_StrictFrozenModel):
    instrument_protocol_uri: str
    instrument_protocol_file_sha256: str = Field(pattern=SHA256_PATTERN)
    instrument_protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    dataset_manifest_uri: str
    dataset_manifest_file_sha256: str = Field(pattern=SHA256_PATTERN)
    dataset_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    dataset_receipt_uri: str
    dataset_receipt_file_sha256: str = Field(pattern=SHA256_PATTERN)
    dataset_receipt_sha256: str = Field(pattern=SHA256_PATTERN)


class StudyDatasetBinding(_StrictFrozenModel):
    dataset_id: Literal[
        "uci_default_of_credit_card_clients",
        "uci_online_shoppers_purchasing_intention",
    ]
    role: Literal["primary", "external_replication"]
    split_membership_sha256: str = Field(pattern=SHA256_PATTERN)
    sealed_membership_sha256: str = Field(pattern=SHA256_PATTERN)
    target_feature: str = Field(min_length=1)
    intervention_rule: str = Field(min_length=1)
    nuisance_comparator: str = Field(min_length=1)


class StudyEndpoint(_StrictFrozenModel):
    primary_metric: Literal["accuracy_drop_from_seed_matched_clean_reference"]
    primary_estimand: Literal["median_seed_level_target_effect"]
    minimum_practical_effect: float
    expected_direction: Literal["decrease"]
    minimum_expected_direction_fraction: float
    manipulation_tolerance: Literal["max_0.01_absolute_or_0.10_relative"]
    dominance_rule: Literal["median_target_ge_1.5x_nuisance_or_margin_ge_0.005"]
    cross_dataset_rule: Literal["both_datasets_must_pass"]
    replicate_unit: Literal["seed_within_dataset_not_independent_study_n"]

    @model_validator(mode="after")
    def _thresholds_are_frozen(self) -> StudyEndpoint:
        expected = (
            (self.minimum_practical_effect, 0.01),
            (self.minimum_expected_direction_fraction, 0.8),
        )
        if any(
            not math.isclose(actual, wanted, rel_tol=0.0, abs_tol=1e-12)
            for actual, wanted in expected
        ):
            raise ValueError("study endpoint thresholds differ from the frozen policy")
        return self


class StudyModelPolicy(_StrictFrozenModel):
    estimator: Literal["sklearn.linear_model.LogisticRegression"]
    parameters: tuple[str, ...]
    preprocessing: Literal[
        "train_fit_standard_scaler_plus_one_hot_handle_unknown_ignore_float64"
    ]
    probability_output_required: Literal[True]
    convergence_warning_action: Literal["technical_failure"]
    nonfinite_metric_action: Literal["technical_failure"]
    hyperparameter_search_forbidden: Literal[True]
    secondary_model_cannot_rescue_primary: Literal[True]

    @model_validator(mode="after")
    def _model_is_exact(self) -> StudyModelPolicy:
        expected = ("C=1.0", "solver=lbfgs", "max_iter=1000", "random_state=42")
        if self.parameters != expected:
            raise ValueError("study model parameters differ from the frozen specification")
        return self


class StudyExecutionPolicy(_StrictFrozenModel):
    seeds: tuple[int, ...]
    maximum_registered_execution_attempts: Literal[1]
    no_interim_analysis: Literal[True]
    outcomes_released_together: Literal[True]
    additional_seed_or_grid_after_outcomes_forbidden: Literal[True]
    candidate_plan_frozen_before_outcomes: Literal[True]
    sealed_outcomes_generated: Literal[False]
    model_fitted: Literal[False]
    execution_authorized: Literal[False]

    @model_validator(mode="after")
    def _seeds_are_exact_and_independent(self) -> StudyExecutionPolicy:
        if self.seeds != (8201, 8202, 8203, 8204, 8205):
            raise ValueError("study seeds differ from the frozen five-seed census")
        return self


class StudyExclusionPolicy(_StrictFrozenModel):
    exclude_stable_controls_from_admission: Literal[True]
    exclude_reserve_candidates_from_admission: Literal[True]
    exclude_duplicate_or_replayed_receipts: Literal[True]
    exclude_cross_family_evidence_reuse: Literal[True]
    exclude_post_outcome_artifacts: Literal[True]
    exclude_failed_manipulation_checks: Literal[True]
    exclude_nuisance_dominated_candidates: Literal[True]
    exclude_incomplete_sibling_bundles: Literal[True]


class StudyDispositionPolicy(_StrictFrozenModel):
    pass_disposition: Literal["admitted"]
    scientific_abstention_disposition: Literal["assumption_limited"]
    valid_negative_disposition: Literal["rejected"]
    contract_failure_disposition: Literal["technical_failure"]
    missing_or_ambiguous_evidence_action: Literal["fail_closed"]
    implementation_or_ci_cannot_admit: Literal[True]
    downstream_denominator_change_forbidden_before_terminal_receipt: Literal[True]


class StudyGovernance(_StrictFrozenModel):
    reuses_partitions_opened_for_another_mechanism: Literal[True]
    independent_new_dataset_replication: Literal[False]
    target_mechanism_outcomes_inspected_before_freeze: Literal[False]
    historical_other_mechanism_results_cannot_select_feature_or_threshold: Literal[True]
    claim_scope: Literal["named_dataset_mechanism_bounded"]
    protocol_amendment_required_for_any_change: Literal[True]


class LightweightConfirmatoryProtocol(_StrictFrozenModel):
    """One mechanism-specific candidate for immutable registration."""

    schema_version: Literal["p2r-lightweight-confirmatory-protocol/1"] = (
        LIGHTWEIGHT_PROTOCOL_SCHEMA_VERSION
    )
    protocol_version: str = Field(min_length=1)
    status: Literal["verified_outcome_blind_not_registered"]
    mechanism: MechanismName
    required_git_tag: str = Field(min_length=1)
    artifacts: StudyArtifactBindings
    datasets: tuple[StudyDatasetBinding, ...]
    model: StudyModelPolicy
    endpoint: StudyEndpoint
    execution: StudyExecutionPolicy
    exclusions: StudyExclusionPolicy
    dispositions: StudyDispositionPolicy
    governance: StudyGovernance

    @model_validator(mode="after")
    def _study_is_cross_dataset_and_mechanism_specific(
        self,
    ) -> LightweightConfirmatoryProtocol:
        expected_datasets = (
            ("uci_default_of_credit_card_clients", "primary"),
            ("uci_online_shoppers_purchasing_intention", "external_replication"),
        )
        actual = tuple((item.dataset_id, item.role) for item in self.datasets)
        if actual != expected_datasets:
            raise ValueError("study must bind both datasets in canonical role order")
        if len({item.split_membership_sha256 for item in self.datasets}) != 2:
            raise ValueError("study datasets must bind distinct split memberships")
        if self.mechanism not in self.protocol_version:
            raise ValueError("protocol version must identify its mechanism")
        expected_identity = {
            "data_drift": (
                "p2r-data_drift-confirmatory/1",
                "p2r-data-drift-confirmatory-v1",
                (
                    (
                        "EDUCATION",
                        "sealed_feature_marginal_plus_0.20_toward_training_mode_by_seeded_row_hash",
                        "same_size_seed_matched_empirical_distribution_resample",
                    ),
                    (
                        "VisitorType",
                        "sealed_feature_marginal_plus_0.20_toward_training_mode_by_seeded_row_hash",
                        "same_size_seed_matched_empirical_distribution_resample",
                    ),
                ),
            ),
            "preprocessing_bug": (
                "p2r-preprocessing_bug-confirmatory/1",
                "p2r-preprocessing-mismatch-confirmatory-v1",
                (
                    (
                        "EDUCATION",
                        "inference_only_training_mode_to_second_mode_mapping_on_seeded_rows",
                        "same_rows_name_bound_column_permutation_control",
                    ),
                    (
                        "VisitorType",
                        "inference_only_training_mode_to_second_mode_mapping_on_seeded_rows",
                        "same_rows_name_bound_column_permutation_control",
                    ),
                ),
            ),
        }
        version, tag, intervention_bindings = expected_identity[self.mechanism]
        if (self.protocol_version, self.required_git_tag) != (version, tag):
            raise ValueError("protocol version or required Git tag is not canonical")
        actual_interventions = tuple(
            (item.target_feature, item.intervention_rule, item.nuisance_comparator)
            for item in self.datasets
        )
        if actual_interventions != intervention_bindings:
            raise ValueError("mechanism intervention bindings differ from the frozen design")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def load_lightweight_confirmatory_protocol(
    path: str | Path,
) -> LightweightConfirmatoryProtocol:
    """Load a mechanism study without opening a sealed outcome."""

    try:
        return LightweightConfirmatoryProtocol.model_validate_json(
            Path(path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise LightweightProtocolError(
            "lightweight confirmatory protocol is unavailable or invalid"
        ) from exc


def verify_lightweight_confirmatory_protocol(
    protocol: LightweightConfirmatoryProtocol,
    *,
    instrument_protocol: InstrumentValidityProtocol | None = None,
) -> LightweightConfirmatoryProtocol:
    """Verify hashes, frozen policy, and the no-outcome registration boundary."""

    checked = _revalidated(protocol)
    bindings = checked.artifacts
    instrument_path = Path(bindings.instrument_protocol_uri)
    dataset_manifest_path = Path(bindings.dataset_manifest_uri)
    dataset_receipt_path = Path(bindings.dataset_receipt_uri)
    if instrument_path != DEFAULT_INSTRUMENT_PROTOCOL_PATH:
        _fail("study must bind the canonical instrument protocol path")
    if _file_sha256(instrument_path) != bindings.instrument_protocol_file_sha256:
        _fail("instrument protocol file hash mismatch")
    if _file_sha256(dataset_manifest_path) != bindings.dataset_manifest_file_sha256:
        _fail("dataset manifest file hash mismatch")
    if _file_sha256(dataset_receipt_path) != bindings.dataset_receipt_file_sha256:
        _fail("dataset receipt file hash mismatch")
    if _json_sha256(dataset_manifest_path) != bindings.dataset_manifest_sha256:
        _fail("dataset manifest canonical hash mismatch")
    if _json_sha256(dataset_receipt_path) != bindings.dataset_receipt_sha256:
        _fail("dataset receipt canonical hash mismatch")
    instrument = verify_instrument_validity_protocol(
        instrument_protocol or load_instrument_validity_protocol(instrument_path)
    )
    if instrument.canonical_sha256() != bindings.instrument_protocol_sha256:
        _fail("study is bound to another instrument protocol")
    if checked.execution.execution_authorized or checked.execution.sealed_outcomes_generated:
        _fail("protocol verification cannot authorize or generate outcomes")
    return checked


def verify_protocol_pair(
    data_drift: LightweightConfirmatoryProtocol,
    preprocessing: LightweightConfirmatoryProtocol,
) -> tuple[LightweightConfirmatoryProtocol, LightweightConfirmatoryProtocol]:
    """Fail closed unless the pair covers each pending mechanism exactly once."""

    drift = verify_lightweight_confirmatory_protocol(data_drift)
    prep = verify_lightweight_confirmatory_protocol(preprocessing)
    if (drift.mechanism, prep.mechanism) != ("data_drift", "preprocessing_bug"):
        _fail("protocol pair must cover data drift and preprocessing exactly once")
    if drift.canonical_sha256() == prep.canonical_sha256():
        _fail("mechanism protocols must have distinct identities")
    return drift, prep
