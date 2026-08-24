"""Outcome-blind v3.2 protocol for one disclosed technical recovery."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    V3DatasetBindingManifest,
    V3DatasetBindingReceipt,
    load_v3_dataset_binding_manifest,
    load_v3_dataset_binding_receipt,
    verify_v3_dataset_binding_design,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_design import (
    V3StudyDesign,
    load_v3_study_design,
    verify_v3_predecessor,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    DatasetSplitReceipt,
    DecisionRuntimeContract,
    InferenceRuntimeContract,
    InterventionRuntimeContract,
    PreprocessingContract,
    PriorShiftRuntimeContract,
    ShiftEstimatorRuntimeContract,
    SplitAlgorithmContract,
    V3ConfirmatoryProtocol,
    V3ProtocolError,
    load_v3_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_recovery import (
    V3TechnicalFailureReceipt,
    load_v3_technical_failure_receipt,
)
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN

V3_2_PROTOCOL_SCHEMA_VERSION: Final[Literal["p2-label-noise-shift-protocol/3"]] = (
    "p2-label-noise-shift-protocol/3"
)
DEFAULT_V3_2_PROTOCOL_PATH: Final[Path] = Path(
    "configs/benchmark/p2_label_noise_shift_v3_2_protocol.json"
)
V3_1_PROTOCOL_SHA256: Final[str] = (
    "0e9c594a6453dc111def3208582cec85d13518d542a61d86197620f9707ab7b2"
)
V3_1_FAILURE_RECEIPT_SHA256: Final[str] = (
    "d9b9b916df472418aeed015db7d5617500607c4b69fac89dc4c02a4d9b71111e"
)
V3_1_FAILURE_RECEIPT_FILE_SHA256: Final[str] = (
    "d371c3e830921b4492c58eeccd8e8e9c2d6e4e2333fb44183795420614047700"
)
RECOVERY_IMPLEMENTATION_COMMIT: Final[str] = "c56a184e2bc8f2d3970abaa98910904984719626"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class V32ArtifactBindings(_StrictFrozenModel):
    design_uri: Literal["configs/benchmark/p2_label_noise_shift_v3_design.json"]
    design_sha256: str = Field(pattern=SHA256_PATTERN)
    dataset_manifest_uri: Literal["configs/benchmark/p2_label_noise_shift_v3_dataset_bindings.json"]
    dataset_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    dataset_receipt_uri: Literal["configs/benchmark/provenance/p2_v3_dataset_binding_receipt.json"]
    dataset_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    v2_result_store_sha256: str = Field(pattern=SHA256_PATTERN)
    predecessor_protocol_uri: Literal["configs/benchmark/p2_label_noise_shift_v3_protocol.json"]
    predecessor_protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    technical_failure_receipt_uri: Literal[
        "configs/benchmark/provenance/p2_v3_1_technical_failure_receipt.json"
    ]
    technical_failure_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    technical_failure_receipt_file_sha256: str = Field(pattern=SHA256_PATTERN)


class V32ModelRuntimeContract(_StrictFrozenModel):
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
    calibration: Literal["development_logit_intercept_slope_damped_newton_mean_scaled"]
    calibration_initial_intercept: float
    calibration_initial_slope: float
    calibration_regularization: None
    calibration_probability_clip: float
    calibration_max_iter: Literal[100]
    calibration_tolerance: float
    calibration_objective_scale: Literal["mean_per_development_record"]
    calibration_gradient_scale: Literal["mean_per_development_record"]
    calibration_hessian_scale: Literal["mean_per_development_record"]
    calibration_convergence_norm: Literal["mean_gradient_infinity_norm"]
    calibration_line_search: Literal[
        "nonincreasing_mean_negative_log_likelihood_binary_halving_min_step_2^-20"
    ]
    calibration_failure_action: Literal["structured_abstain"]
    calibration_abstention_exposes_partial_fit: Literal[False]
    calibration_abstention_blocks_dataset_scoring: Literal[True]
    non_calibration_defects_remain_hard_failures: Literal[True]
    hyperparameter_search_forbidden: Literal[True]
    sensitivity_model_cannot_rescue_primary: Literal[True]

    @model_validator(mode="after")
    def _models_are_exact(self) -> V32ModelRuntimeContract:
        if self.calibration_initial_intercept != 0.0 or self.calibration_initial_slope != 1.0:
            raise ValueError("calibration initialization remains frozen at intercept 0 and slope 1")
        if self.primary_parameters != (
            "C=1.0",
            "solver=lbfgs",
            "max_iter=1000",
            "random_state=42",
        ):
            raise ValueError("primary model parameters differ from the predecessor")
        if self.sensitivity_parameters != (
            "learning_rate=0.1",
            "max_iter=100",
            "max_leaf_nodes=31",
            "l2_regularization=0.0",
            "early_stopping=false",
            "random_state=43",
        ):
            raise ValueError("sensitivity model parameters differ from the predecessor")
        fixed = (
            (self.calibration_probability_clip, 1e-15, "probability clip"),
            (self.calibration_tolerance, 1e-8, "calibration tolerance"),
        )
        for observed, expected, label in fixed:
            if not math.isclose(observed, expected, rel_tol=0.0, abs_tol=0.0):
                raise ValueError(f"{label} differs from the predecessor")
        return self


class TechnicalRecoveryContract(_StrictFrozenModel):
    predecessor_tag: Literal["p2-label-noise-shift-factorial-v3.1"]
    predecessor_protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    predecessor_execution_commit: Literal["1d365f22c133ce5d70d3ac13b465bfb6202d6e50"]
    recovery_implementation_commit: Literal["c56a184e2bc8f2d3970abaa98910904984719626"]
    failure_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    root_cause_classification: Literal["implementation_defect"]
    recovery_audit_data_scope: Literal["training_and_development_only"]
    predecessor_sealed_partition_opened: Literal[True]
    predecessor_result_store_published: Literal[False]
    predecessor_scientific_disposition_generated: Literal[False]
    predecessor_rerun_forbidden: Literal[True]
    same_pinned_datasets_and_splits_reused: Literal[True]
    allowed_changes: tuple[
        Literal[
            "calibration_sum_equations_to_mean_equations",
            "calibration_exception_to_structured_abstention",
            "permanent_v3_1_execution_retirement",
            "protocol_schema_tag_and_registration_identity",
        ],
        ...,
    ]
    model_hyperparameters_changed: Literal[False]
    datasets_or_splits_changed: Literal[False]
    intervention_grid_or_seeds_changed: Literal[False]
    estimand_metric_or_inference_changed: Literal[False]
    thresholds_or_decision_rule_changed: Literal[False]
    outcome_information_used_for_tuning: Literal[False]
    maximum_registered_execution_attempts: Literal[1]
    further_recovery_requires_new_disclosed_protocol: Literal[True]

    @model_validator(mode="after")
    def _technical_delta_is_exact(self) -> TechnicalRecoveryContract:
        expected = (
            "calibration_sum_equations_to_mean_equations",
            "calibration_exception_to_structured_abstention",
            "permanent_v3_1_execution_retirement",
            "protocol_schema_tag_and_registration_identity",
        )
        if self.allowed_changes != expected:
            raise ValueError("recovery may contain only the four disclosed technical changes")
        if self.predecessor_protocol_sha256 != V3_1_PROTOCOL_SHA256:
            raise ValueError("recovery is bound to another predecessor protocol")
        if self.failure_receipt_sha256 != V3_1_FAILURE_RECEIPT_SHA256:
            raise ValueError("recovery is bound to another technical failure receipt")
        return self


class V32ProtocolGovernance(_StrictFrozenModel):
    required_git_tag: Literal["p2-label-noise-shift-factorial-v3.2"]
    protocol_only_commit_required: Literal[True]
    immutable_release_required_before_execution: Literal[True]
    recovery_implementation_must_predate_registration: Literal[True]
    structured_internal_outcome_blind_audit_required_before_registration: Literal[True]
    changes_after_registration_require_new_protocol_version: Literal[True]
    primary_and_replication_outcomes_released_together: Literal[True]
    sealed_test_single_open_for_v3_2: Literal[True]
    predecessor_failure_receipt_must_remain_published: Literal[True]
    registration_authorized_by_this_file: Literal[False]
    execution_authorized_by_this_file: Literal[False]


class V32ConfirmatoryProtocol(_StrictFrozenModel):
    schema_version: Literal["p2-label-noise-shift-protocol/3"]
    status: Literal["technical_recovery_protocol_candidate_not_registered"]
    artifacts: V32ArtifactBindings
    split_algorithm: SplitAlgorithmContract
    dataset_splits: tuple[DatasetSplitReceipt, ...]
    preprocessing: PreprocessingContract
    models: V32ModelRuntimeContract
    intervention: InterventionRuntimeContract
    prior_shift: PriorShiftRuntimeContract
    shift_estimators: ShiftEstimatorRuntimeContract
    inference: InferenceRuntimeContract
    decision: DecisionRuntimeContract
    technical_recovery: TechnicalRecoveryContract
    governance: V32ProtocolGovernance
    outcome_fields_forbidden: Literal[True]
    model_fitted: Literal[False]
    predictive_metrics_generated: Literal[False]
    sealed_outcomes_generated: Literal[False]

    @model_validator(mode="after")
    def _dataset_census_is_unchanged(self) -> V32ConfirmatoryProtocol:
        census = tuple((item.dataset_id, item.role, item.seed) for item in self.dataset_splits)
        if census != (
            ("uci_default_of_credit_card_clients", "primary", 2718),
            ("uci_online_shoppers_purchasing_intention", "external_replication", 3141),
        ):
            raise ValueError("v3.2 must preserve the ordered predecessor dataset census")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def load_v3_2_confirmatory_protocol(
    path: str | Path = DEFAULT_V3_2_PROTOCOL_PATH,
) -> V32ConfirmatoryProtocol:
    try:
        return V32ConfirmatoryProtocol.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise V3ProtocolError("v3.2 confirmatory protocol is unavailable or invalid") from exc


def _file_sha256(path: Path) -> str:
    if path.is_symlink():
        raise V3ProtocolError("v3.2 predecessor artifact cannot be a symbolic link")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise V3ProtocolError(f"cannot hash v3.2 predecessor artifact: {path}") from exc
    return digest.hexdigest()


def verify_v3_2_technical_delta(
    protocol: V32ConfirmatoryProtocol,
    predecessor: V3ConfirmatoryProtocol,
) -> None:
    """Prove that scientific design fields are byte-semantically unchanged."""

    checked = V32ConfirmatoryProtocol.model_validate(protocol.model_dump())
    previous = V3ConfirmatoryProtocol.model_validate(predecessor.model_dump())
    identical_sections = (
        "split_algorithm",
        "dataset_splits",
        "preprocessing",
        "intervention",
        "prior_shift",
        "shift_estimators",
        "inference",
        "decision",
    )
    checked_payload = checked.model_dump(mode="json")
    previous_payload = previous.model_dump(mode="json")
    for section in identical_sections:
        if checked_payload[section] != previous_payload[section]:
            raise V3ProtocolError(f"v3.2 changes forbidden scientific section: {section}")
    previous_artifacts = previous.artifacts.model_dump(mode="json")
    current_artifacts = checked.artifacts.model_dump(mode="json")
    for field, value in previous_artifacts.items():
        if current_artifacts.get(field) != value:
            raise V3ProtocolError(f"v3.2 changes predecessor artifact binding: {field}")
    previous_model = previous.models.model_dump(mode="json")
    current_model = checked.models.model_dump(mode="json")
    allowed_model_delta = {
        "calibration",
        "calibration_objective_scale",
        "calibration_gradient_scale",
        "calibration_hessian_scale",
        "calibration_convergence_norm",
        "calibration_line_search",
        "calibration_failure_action",
        "calibration_abstention_exposes_partial_fit",
        "calibration_abstention_blocks_dataset_scoring",
        "non_calibration_defects_remain_hard_failures",
    }
    for field, value in previous_model.items():
        if field not in allowed_model_delta and current_model.get(field) != value:
            raise V3ProtocolError(f"v3.2 changes forbidden model field: {field}")
    if set(current_model) - set(previous_model) != allowed_model_delta - {
        "calibration",
        "calibration_failure_action",
    }:
        raise V3ProtocolError("v3.2 calibration delta contains undeclared fields")


def verify_v3_2_protocol_artifacts(
    protocol: V32ConfirmatoryProtocol,
    *,
    root: str | Path = ".",
) -> tuple[
    V3StudyDesign,
    V3DatasetBindingManifest,
    V3DatasetBindingReceipt,
    V3ConfirmatoryProtocol,
    V3TechnicalFailureReceipt,
]:
    """Verify the full outcome-free predecessor chain and technical-only delta."""

    repository = Path(root)
    checked = V32ConfirmatoryProtocol.model_validate(protocol.model_dump())
    design = load_v3_study_design(repository / checked.artifacts.design_uri)
    manifest = load_v3_dataset_binding_manifest(repository / checked.artifacts.dataset_manifest_uri)
    receipt = load_v3_dataset_binding_receipt(repository / checked.artifacts.dataset_receipt_uri)
    predecessor = load_v3_confirmatory_protocol(
        repository / checked.artifacts.predecessor_protocol_uri
    )
    failure_receipt_path = repository / checked.artifacts.technical_failure_receipt_uri
    failure_receipt = load_v3_technical_failure_receipt(failure_receipt_path)
    verify_v3_predecessor(design, root=repository)
    verify_v3_dataset_binding_design(manifest, root=repository)
    expected_hashes = (
        (design.canonical_sha256(), checked.artifacts.design_sha256, "design"),
        (
            manifest.canonical_sha256(),
            checked.artifacts.dataset_manifest_sha256,
            "dataset manifest",
        ),
        (
            receipt.canonical_sha256(),
            checked.artifacts.dataset_receipt_sha256,
            "dataset receipt",
        ),
        (
            predecessor.canonical_sha256(),
            checked.artifacts.predecessor_protocol_sha256,
            "predecessor protocol",
        ),
        (
            failure_receipt.canonical_sha256(),
            checked.artifacts.technical_failure_receipt_sha256,
            "technical failure receipt",
        ),
        (
            _file_sha256(failure_receipt_path),
            checked.artifacts.technical_failure_receipt_file_sha256,
            "technical failure receipt file",
        ),
    )
    for observed, expected, label in expected_hashes:
        if observed != expected:
            raise V3ProtocolError(f"v3.2 is bound to another {label}")
    if predecessor.canonical_sha256() != V3_1_PROTOCOL_SHA256:
        raise V3ProtocolError("v3.2 predecessor protocol is not the disclosed v3.1 protocol")
    if failure_receipt.canonical_sha256() != V3_1_FAILURE_RECEIPT_SHA256:
        raise V3ProtocolError("v3.2 technical failure is not the disclosed v3.1 failure")
    if _file_sha256(failure_receipt_path) != V3_1_FAILURE_RECEIPT_FILE_SHA256:
        raise V3ProtocolError("v3.2 technical failure file is not the published receipt")
    if design.predecessor.v2_result_store_sha256 != checked.artifacts.v2_result_store_sha256:
        raise V3ProtocolError("v3.2 is bound to another v2 result store")
    if checked.technical_recovery.recovery_implementation_commit != RECOVERY_IMPLEMENTATION_COMMIT:
        raise V3ProtocolError("v3.2 is bound to another recovery implementation")
    if not failure_receipt.rerun_forbidden or failure_receipt.scientific_disposition_generated:
        raise V3ProtocolError("v3.1 receipt does not authorize a technical-only successor")
    receipt_bindings = {
        item.dataset_id: (item.target_binding_sha256, item.record_identity_sha256)
        for item in receipt.datasets
    }
    protocol_bindings = {
        item.dataset_id: (item.target_binding_sha256, item.record_identity_sha256)
        for item in checked.dataset_splits
    }
    if protocol_bindings != receipt_bindings:
        raise V3ProtocolError("v3.2 split identities differ from the audited dataset receipt")
    verify_v3_2_technical_delta(checked, predecessor)
    return design, manifest, receipt, predecessor, failure_receipt


def verify_v3_2_compiled_split_receipts(
    protocol: V32ConfirmatoryProtocol,
    observed: Sequence[DatasetSplitReceipt],
) -> None:
    if canonical_sha256([item.model_dump(mode="json") for item in observed]) != canonical_sha256(
        [item.model_dump(mode="json") for item in protocol.dataset_splits]
    ):
        raise V3ProtocolError("recompiled v3.2 split receipts differ from the frozen protocol")
