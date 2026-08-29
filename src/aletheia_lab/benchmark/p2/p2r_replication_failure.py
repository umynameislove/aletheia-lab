"""Outcome-free audit of the P2R v1.1 replication execution failure.

The registered v1.1 attempt is immutable and must never be replayed.  This
module therefore diagnoses the failure from three independently checkable
sources only:

* the content-addressed terminal failure;
* the execution-commit control flow and error-message preimage; and
* a covariate-only intervention-capacity census over the pinned partitions.

No target vector, prediction, fitted model, or scientific endpoint is accepted
by the feasibility compiler.  Its purpose is to establish treatment positivity:
the registered population must contain enough susceptible rows to deliver the
declared intervention dose before an execution attempt can be authorized.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Final, Literal, NoReturn

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    V3DatasetBinding,
    V3DatasetBindingManifest,
)
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN
from aletheia_lab.benchmark.p2.lightweight_protocol import (
    LightweightConfirmatoryProtocol,
    StudyDatasetBinding,
    verify_protocol_pair,
)
from aletheia_lab.benchmark.p2.p2r_closeout import P2RTechnicalFailure
from aletheia_lab.benchmark.p2.p2r_recovery_execution import (
    load_and_verify_recovery_terminal_store,
)

FEASIBILITY_SCHEMA_VERSION: Final[
    Literal["p2r-intervention-feasibility/1"]
] = "p2r-intervention-feasibility/1"
FEATURE_CAPACITY_SCHEMA_VERSION: Final[
    Literal["p2r-feature-capacity/1"]
] = "p2r-feature-capacity/1"
DATASET_CENSUS_SCHEMA_VERSION: Final[
    Literal["p2r-dataset-feasibility-census/1"]
] = "p2r-dataset-feasibility-census/1"
REPLICATION_FAILURE_AUDIT_SCHEMA_VERSION: Final[
    Literal["p2r-v1-1-replication-failure-audit/1"]
] = "p2r-v1-1-replication-failure-audit/1"

DEFAULT_P2R_V1_1_FEASIBILITY_PATH = Path(
    "configs/benchmark/provenance/p2r_v1_1_intervention_feasibility.json"
)
DEFAULT_P2R_V1_1_FAILURE_AUDIT_PATH = Path(
    "configs/benchmark/provenance/p2r_v1_1_replication_failure_audit.json"
)
DEFAULT_P2R_V1_1_TERMINAL_STORE_PATH = Path(
    "experiments/p2/outputs/p2r-confirmatory-v1-1"
)
DEFAULT_P2R_V1_1_REGISTRATION_PATH = Path(
    "experiments/p2/outputs/p2r-v1-1-registration.json"
)
DEFAULT_P2R_V1_1_MARKER_PATH = Path(
    "experiments/p2/outputs/p2r-v1-1-sealed-open.json"
)

P2R_V1_1_TERMINAL_STORE_SHA256: Final[str] = (
    "aafbecaaab43dddad538cf23a66190ca2b71c1a573ed04c7232db14105e12a53"
)
P2R_V1_1_EXCEPTION_MESSAGE: Final[str] = (
    "registered manipulation cannot achieve its declared row count"
)
P2R_V1_1_EXCEPTION_MESSAGE_SHA256: Final[str] = hashlib.sha256(
    P2R_V1_1_EXCEPTION_MESSAGE.encode()
).hexdigest()

DECLARED_MAGNITUDE: Final[float] = 0.20
CAPACITY_RESERVE: Final[float] = 0.05
TARGET_SELECTION_POLICY: Final[
    Literal[
        "retain_if_jointly_feasible_with_reserve_else_maximize_minimum_capacity_then_manifest_order_v1"
    ]
] = (
    "retain_if_jointly_feasible_with_reserve_else_maximize_minimum_capacity_then_manifest_order_v1"
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
DatasetRole = Literal["primary", "external_replication"]


class P2RReplicationFailureError(ValueError):
    """Raised when failure or feasibility evidence cannot reconcile."""


def _fail(message: str) -> NoReturn:
    raise P2RReplicationFailureError(message)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


def _token(value: object) -> str:
    token = str(value).strip()
    if not token:
        _fail("intervention-feasibility categories must not be blank")
    return token


def _ordered_categories(values: Sequence[object]) -> tuple[tuple[str, int], ...]:
    counts = Counter(_token(value) for value in values)
    if len(counts) < 2:
        _fail("intervention feasibility requires at least two training categories")
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


class P2RFeatureCapacity(_StrictFrozenModel):
    """Covariate-only capacity for both registered 20% interventions."""

    schema_version: Literal["p2r-feature-capacity/1"] = FEATURE_CAPACITY_SCHEMA_VERSION
    feature: str = Field(min_length=1)
    training_record_count: int = Field(gt=0)
    sealed_record_count: int = Field(gt=0)
    training_mode: str = Field(min_length=1)
    training_mode_count: int = Field(gt=0)
    training_second_mode: str = Field(min_length=1)
    training_second_mode_count: int = Field(gt=0)
    sealed_mode_count: int = Field(ge=0)
    sealed_non_mode_count: int = Field(ge=0)
    target_row_count: int = Field(gt=0)
    reserve_row_count: int = Field(ge=0)
    data_drift_capacity_count: int = Field(ge=0)
    preprocessing_capacity_count: int = Field(ge=0)
    minimum_capacity_count: int = Field(ge=0)
    minimum_capacity_fraction: float
    data_drift_feasible: bool
    preprocessing_feasible: bool
    jointly_feasible_with_reserve: bool
    capacity_sha256: Sha256

    @model_validator(mode="after")
    def _capacity_is_derived(self) -> P2RFeatureCapacity:
        if self.training_mode == self.training_second_mode:
            raise ValueError("training mode and second mode must be distinct")
        if self.training_mode_count + self.training_second_mode_count > self.training_record_count:
            raise ValueError("training category counts exceed the training census")
        if self.sealed_mode_count + self.sealed_non_mode_count != self.sealed_record_count:
            raise ValueError("sealed category counts do not reconcile")
        expected_target = math.floor(DECLARED_MAGNITUDE * self.sealed_record_count)
        expected_reserve = math.ceil(CAPACITY_RESERVE * self.sealed_record_count)
        minimum = min(self.sealed_non_mode_count, self.sealed_mode_count)
        expected = (
            expected_target,
            expected_reserve,
            self.sealed_non_mode_count,
            self.sealed_mode_count,
            minimum,
            minimum / self.sealed_record_count,
            self.sealed_non_mode_count >= expected_target,
            self.sealed_mode_count >= expected_target,
            minimum >= expected_target + expected_reserve,
        )
        actual = (
            self.target_row_count,
            self.reserve_row_count,
            self.data_drift_capacity_count,
            self.preprocessing_capacity_count,
            self.minimum_capacity_count,
            self.minimum_capacity_fraction,
            self.data_drift_feasible,
            self.preprocessing_feasible,
            self.jointly_feasible_with_reserve,
        )
        if actual[:-4] != expected[:-4] or any(
            left != right for left, right in zip(actual[-3:], expected[-3:], strict=True)
        ) or not math.isclose(actual[5], expected[5], rel_tol=0.0, abs_tol=1e-15):
            raise ValueError("feature intervention capacity is not derived from its census")
        payload = self.model_dump(mode="json", exclude={"capacity_sha256"})
        if self.capacity_sha256 != canonical_sha256(payload):
            raise ValueError("capacity hash does not bind the feature census")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class P2RDatasetFeasibilityCensus(_StrictFrozenModel):
    schema_version: Literal["p2r-dataset-feasibility-census/1"] = (
        DATASET_CENSUS_SCHEMA_VERSION
    )
    dataset_id: str = Field(min_length=1)
    dataset_role: DatasetRole
    archive_sha256: Sha256
    split_membership_sha256: Sha256
    sealed_membership_sha256: Sha256
    categorical_feature_order: tuple[str, ...]
    frozen_target_feature: str = Field(min_length=1)
    frozen_target_jointly_feasible: bool
    selected_target_feature: str = Field(min_length=1)
    selection_reason: Literal[
        "frozen_target_retained",
        "frozen_target_infeasible_capacity_selected_without_outcomes",
    ]
    capacities: tuple[P2RFeatureCapacity, ...]
    census_sha256: Sha256

    @model_validator(mode="after")
    def _selection_is_deterministic(self) -> P2RDatasetFeasibilityCensus:
        features = tuple(item.feature for item in self.capacities)
        if features != self.categorical_feature_order or len(set(features)) != len(features):
            raise ValueError("feature-capacity census must preserve manifest order exactly once")
        by_feature = {item.feature: item for item in self.capacities}
        if self.frozen_target_feature not in by_feature:
            raise ValueError("frozen target is absent from the capacity census")
        frozen_feasible = by_feature[self.frozen_target_feature].jointly_feasible_with_reserve
        if self.frozen_target_jointly_feasible != frozen_feasible:
            raise ValueError("frozen-target feasibility is not derived")
        if frozen_feasible:
            selected = self.frozen_target_feature
            reason = "frozen_target_retained"
        else:
            eligible = [item for item in self.capacities if item.jointly_feasible_with_reserve]
            if not eligible:
                raise ValueError("no outcome-free feature can deliver both registered interventions")
            manifest_index = {feature: index for index, feature in enumerate(features)}
            selected = min(
                eligible,
                key=lambda item: (-item.minimum_capacity_count, manifest_index[item.feature]),
            ).feature
            reason = "frozen_target_infeasible_capacity_selected_without_outcomes"
        if self.selected_target_feature != selected or self.selection_reason != reason:
            raise ValueError("target selection differs from the frozen outcome-free policy")
        payload = self.model_dump(mode="json", exclude={"census_sha256"})
        if self.census_sha256 != canonical_sha256(payload):
            raise ValueError("dataset census hash does not bind the feature capacities")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class P2RInterventionFeasibilityReceipt(_StrictFrozenModel):
    schema_version: Literal["p2r-intervention-feasibility/1"] = FEASIBILITY_SCHEMA_VERSION
    scientific_protocol_sha256s: tuple[Sha256, Sha256]
    dataset_manifest_sha256: Sha256
    dataset_receipt_sha256: Sha256
    declared_manipulation_magnitude: float
    minimum_capacity_reserve: float
    target_selection_policy: Literal[
        "retain_if_jointly_feasible_with_reserve_else_maximize_minimum_capacity_then_manifest_order_v1"
    ]
    datasets: tuple[P2RDatasetFeasibilityCensus, P2RDatasetFeasibilityCensus]
    sealed_covariates_inspected: Literal[True]
    target_values_used_for_capacity_or_selection: Literal[False]
    registered_target_stratification_reproduced: Literal[True]
    model_fitted: Literal[False]
    predictive_metrics_generated: Literal[False]
    generated_after_retired_v1_1_failure: Literal[True]
    independent_new_dataset_replication: Literal[False]
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def _receipt_is_complete_and_derived(self) -> P2RInterventionFeasibilityReceipt:
        if (
            self.declared_manipulation_magnitude != DECLARED_MAGNITUDE
            or self.minimum_capacity_reserve != CAPACITY_RESERVE
        ):
            raise ValueError("feasibility receipt changes the frozen dose or reserve")
        if tuple((item.dataset_id, item.dataset_role) for item in self.datasets) != (
            ("uci_default_of_credit_card_clients", "primary"),
            ("uci_online_shoppers_purchasing_intention", "external_replication"),
        ):
            raise ValueError("feasibility receipt must preserve the registered dataset census")
        if len(set(self.scientific_protocol_sha256s)) != 2:
            raise ValueError("feasibility receipt requires two distinct scientific protocols")
        payload = self.model_dump(mode="json", exclude={"receipt_sha256"})
        if self.receipt_sha256 != canonical_sha256(payload):
            raise ValueError("receipt hash does not bind intervention feasibility")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def assess_feature_capacity(
    *,
    feature: str,
    training_values: Sequence[object],
    sealed_values: Sequence[object],
) -> P2RFeatureCapacity:
    """Measure treatment support using feature values only."""

    if not training_values or not sealed_values:
        _fail("intervention feasibility requires non-empty train and sealed covariates")
    ordered = _ordered_categories(training_values)
    mode, mode_count = ordered[0]
    second_mode, second_count = ordered[1]
    sealed_tokens = tuple(_token(value) for value in sealed_values)
    sealed_mode_count = sum(value == mode for value in sealed_tokens)
    sealed_non_mode_count = len(sealed_tokens) - sealed_mode_count
    target_count = math.floor(DECLARED_MAGNITUDE * len(sealed_tokens))
    reserve_count = math.ceil(CAPACITY_RESERVE * len(sealed_tokens))
    if target_count <= 0:
        _fail("registered intervention dose rounds to zero rows")
    minimum = min(sealed_mode_count, sealed_non_mode_count)
    payload: dict[str, object] = {
        "schema_version": FEATURE_CAPACITY_SCHEMA_VERSION,
        "feature": feature,
        "training_record_count": len(training_values),
        "sealed_record_count": len(sealed_values),
        "training_mode": mode,
        "training_mode_count": mode_count,
        "training_second_mode": second_mode,
        "training_second_mode_count": second_count,
        "sealed_mode_count": sealed_mode_count,
        "sealed_non_mode_count": sealed_non_mode_count,
        "target_row_count": target_count,
        "reserve_row_count": reserve_count,
        "data_drift_capacity_count": sealed_non_mode_count,
        "preprocessing_capacity_count": sealed_mode_count,
        "minimum_capacity_count": minimum,
        "minimum_capacity_fraction": minimum / len(sealed_tokens),
        "data_drift_feasible": sealed_non_mode_count >= target_count,
        "preprocessing_feasible": sealed_mode_count >= target_count,
        "jointly_feasible_with_reserve": minimum >= target_count + reserve_count,
    }
    return P2RFeatureCapacity.model_validate(
        {**payload, "capacity_sha256": canonical_sha256(payload)}
    )


def build_dataset_feasibility_census(
    *,
    binding: V3DatasetBinding,
    study_dataset: StudyDatasetBinding,
    training_frame: pd.DataFrame,
    sealed_frame: pd.DataFrame,
) -> P2RDatasetFeasibilityCensus:
    """Build the complete feature census without accepting any target vector."""

    if (study_dataset.dataset_id, study_dataset.role) != (
        binding.dataset_id,
        binding.role,
    ):
        _fail("study and manifest dataset bindings do not align")
    required = tuple(binding.categorical_features)
    if (
        tuple(training_frame.columns) != required
        or tuple(sealed_frame.columns) != required
    ):
        _fail(
            "feasibility frames must contain only manifest categorical covariates "
            "in canonical order"
        )
    capacities = tuple(
        assess_feature_capacity(
            feature=feature,
            training_values=tuple(training_frame.loc[:, feature]),
            sealed_values=tuple(sealed_frame.loc[:, feature]),
        )
        for feature in required
    )
    frozen = next(item for item in capacities if item.feature == study_dataset.target_feature)
    if frozen.jointly_feasible_with_reserve:
        selected = frozen.feature
        reason = "frozen_target_retained"
    else:
        eligible = [item for item in capacities if item.jointly_feasible_with_reserve]
        if not eligible:
            _fail("no outcome-free feature can deliver both registered interventions")
        order = {feature: index for index, feature in enumerate(required)}
        selected = min(
            eligible,
            key=lambda item: (-item.minimum_capacity_count, order[item.feature]),
        ).feature
        reason = "frozen_target_infeasible_capacity_selected_without_outcomes"
    payload: dict[str, object] = {
        "schema_version": DATASET_CENSUS_SCHEMA_VERSION,
        "dataset_id": binding.dataset_id,
        "dataset_role": study_dataset.role,
        "archive_sha256": binding.archive.sha256,
        "split_membership_sha256": study_dataset.split_membership_sha256,
        "sealed_membership_sha256": study_dataset.sealed_membership_sha256,
        "categorical_feature_order": required,
        "frozen_target_feature": study_dataset.target_feature,
        "frozen_target_jointly_feasible": frozen.jointly_feasible_with_reserve,
        "selected_target_feature": selected,
        "selection_reason": reason,
        "capacities": tuple(item.model_dump() for item in capacities),
    }
    return P2RDatasetFeasibilityCensus.model_validate(
        {**payload, "census_sha256": canonical_sha256(payload)}
    )


def build_intervention_feasibility_receipt(
    *,
    manifest: V3DatasetBindingManifest,
    protocols: Sequence[LightweightConfirmatoryProtocol],
    frames: Mapping[str, tuple[pd.DataFrame, pd.DataFrame]],
) -> P2RInterventionFeasibilityReceipt:
    """Compile an outcome-free two-dataset receipt for a prospective amendment."""

    if len(protocols) != 2:
        _fail("feasibility compilation requires both scientific protocols")
    drift, preprocessing = verify_protocol_pair(protocols[0], protocols[1])
    if manifest.canonical_sha256() != drift.artifacts.dataset_manifest_sha256:
        _fail("feasibility compilation is bound to another dataset manifest")
    datasets: list[P2RDatasetFeasibilityCensus] = []
    for index, binding in enumerate(manifest.datasets):
        drift_dataset = drift.datasets[index]
        preprocessing_dataset = preprocessing.datasets[index]
        if (
            drift_dataset.dataset_id,
            drift_dataset.role,
            drift_dataset.target_feature,
        ) != (
            preprocessing_dataset.dataset_id,
            preprocessing_dataset.role,
            preprocessing_dataset.target_feature,
        ):
            _fail("paired mechanism protocols must share dataset, role, and target feature")
        try:
            training_frame, sealed_frame = frames[binding.dataset_id]
        except KeyError as exc:
            raise P2RReplicationFailureError(
                "feasibility compilation is missing a registered dataset"
            ) from exc
        datasets.append(
            build_dataset_feasibility_census(
                binding=binding,
                study_dataset=drift_dataset,
                training_frame=training_frame,
                sealed_frame=sealed_frame,
            )
        )
    if set(frames) != {item.dataset_id for item in manifest.datasets}:
        _fail("feasibility compilation contains an unregistered dataset")
    payload: dict[str, object] = {
        "schema_version": FEASIBILITY_SCHEMA_VERSION,
        "scientific_protocol_sha256s": (
            drift.canonical_sha256(),
            preprocessing.canonical_sha256(),
        ),
        "dataset_manifest_sha256": drift.artifacts.dataset_manifest_sha256,
        "dataset_receipt_sha256": drift.artifacts.dataset_receipt_sha256,
        "declared_manipulation_magnitude": DECLARED_MAGNITUDE,
        "minimum_capacity_reserve": CAPACITY_RESERVE,
        "target_selection_policy": TARGET_SELECTION_POLICY,
        "datasets": tuple(item.model_dump() for item in datasets),
        "sealed_covariates_inspected": True,
        "target_values_used_for_capacity_or_selection": False,
        "registered_target_stratification_reproduced": True,
        "model_fitted": False,
        "predictive_metrics_generated": False,
        "generated_after_retired_v1_1_failure": True,
        "independent_new_dataset_replication": False,
    }
    return P2RInterventionFeasibilityReceipt.model_validate(
        {**payload, "receipt_sha256": canonical_sha256(payload)}
    )


def load_intervention_feasibility_receipt(
    path: str | Path = DEFAULT_P2R_V1_1_FEASIBILITY_PATH,
) -> P2RInterventionFeasibilityReceipt:
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        _fail("P2R intervention-feasibility receipt is unavailable or invalid")
    try:
        return P2RInterventionFeasibilityReceipt.model_validate_json(
            target.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise P2RReplicationFailureError(
            "P2R intervention-feasibility receipt is unavailable or invalid"
        ) from exc


class P2RV11ReplicationFailureAudit(_StrictFrozenModel):
    schema_version: Literal["p2r-v1-1-replication-failure-audit/1"] = (
        REPLICATION_FAILURE_AUDIT_SCHEMA_VERSION
    )
    study_tags: tuple[
        Literal["p2r-data-drift-confirmatory-v1.1"],
        Literal["p2r-preprocessing-mismatch-confirmatory-v1.1"],
    ]
    recovery_protocol_sha256s: tuple[Sha256, Sha256]
    recovery_registration_sha256s: tuple[Sha256, Sha256]
    scientific_protocol_sha256s: tuple[Sha256, Sha256]
    execution_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    execution_runtime_file_sha256: Sha256
    execution_entrypoint_file_sha256: Sha256
    failure_stage: Literal["execute_replication"]
    exception_class: Literal["P2RRuntimeError"]
    exception_message_sha256: Sha256
    exception_message_preimage: Literal[
        "registered manipulation cannot achieve its declared row count"
    ]
    terminal_store_sha256: Sha256
    scientific_store_sha256: Sha256
    registration_file_sha256: Sha256
    sealed_marker_file_sha256: Sha256
    terminal_manifest_file_sha256: Sha256
    terminal_failure_file_sha256: Sha256
    terminal_environment_file_sha256: Sha256
    feasibility_receipt_sha256: Sha256
    failed_dataset_id: Literal["uci_online_shoppers_purchasing_intention"]
    failed_mechanism: Literal["data_drift"]
    failed_feature: Literal["VisitorType"]
    declared_target_row_count: Literal[493]
    eligible_row_count: Literal[343]
    capacity_shortfall: Literal[150]
    replacement_feature: Literal["OperatingSystems"]
    root_cause_classification: Literal["registered_intervention_capacity_defect"]
    causal_attribution: Literal[
        "terminal_preimage_control_flow_and_covariate_capacity_census_reconcile"
    ]
    implementation_bug: Literal[False]
    scientific_negative_result: Literal[False]
    protocol_feasibility_defect: Literal[True]
    predictive_outcomes_inspected_for_repair: Literal[False]
    partial_outcome_published: Literal[False]
    scientific_disposition_generated: Literal[False]
    rerun_forbidden: Literal[True]
    v1_1_attempt_retired: Literal[True]
    scientific_semantics_changed_by_repair: Literal[True]
    required_successor_scope: Literal[
        "prospective_v1_2_methodological_amendment_with_structured_dose_and_feasibility_binding"
    ]
    independent_new_dataset_replication: Literal[False]
    audit_sha256: Sha256

    @model_validator(mode="after")
    def _audit_identity_is_frozen(self) -> P2RV11ReplicationFailureAudit:
        if self.terminal_store_sha256 != P2R_V1_1_TERMINAL_STORE_SHA256:
            raise ValueError("v1.1 audit is bound to another terminal store")
        expected_message = hashlib.sha256(self.exception_message_preimage.encode()).hexdigest()
        if self.exception_message_sha256 != expected_message:
            raise ValueError("v1.1 failure-message preimage does not match its digest")
        if self.declared_target_row_count - self.eligible_row_count != self.capacity_shortfall:
            raise ValueError("v1.1 capacity shortfall does not reconcile")
        payload = self.model_dump(mode="json", exclude={"audit_sha256"})
        if self.audit_sha256 != canonical_sha256(payload):
            raise ValueError("audit hash does not bind the replication failure evidence")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def load_p2r_v1_1_replication_failure_audit(
    path: str | Path = DEFAULT_P2R_V1_1_FAILURE_AUDIT_PATH,
) -> P2RV11ReplicationFailureAudit:
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        _fail("P2R v1.1 replication-failure audit is unavailable or invalid")
    try:
        return P2RV11ReplicationFailureAudit.model_validate_json(
            target.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise P2RReplicationFailureError(
            "P2R v1.1 replication-failure audit is unavailable or invalid"
        ) from exc


def _file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        _fail(f"required v1.1 failure artifact is not a regular file: {path}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise P2RReplicationFailureError(
            f"cannot read v1.1 failure artifact: {path}"
        ) from exc
    return digest.hexdigest()


def verify_p2r_v1_1_replication_failure_audit(
    audit: P2RV11ReplicationFailureAudit,
    *,
    root: str | Path,
    feasibility: P2RInterventionFeasibilityReceipt | None = None,
    registration_path: str | Path = DEFAULT_P2R_V1_1_REGISTRATION_PATH,
    marker_path: str | Path = DEFAULT_P2R_V1_1_MARKER_PATH,
    terminal_store_path: str | Path = DEFAULT_P2R_V1_1_TERMINAL_STORE_PATH,
) -> P2RV11ReplicationFailureAudit:
    """Reconcile the tracked audit with immutable local evidence without execution."""

    checked = P2RV11ReplicationFailureAudit.model_validate(audit.model_dump())
    checked_feasibility = P2RInterventionFeasibilityReceipt.model_validate(
        (feasibility or load_intervention_feasibility_receipt()).model_dump()
    )
    if checked_feasibility.canonical_sha256() != checked.feasibility_receipt_sha256:
        _fail("v1.1 failure audit is bound to another feasibility receipt")
    external = checked_feasibility.datasets[1]
    frozen = next(item for item in external.capacities if item.feature == checked.failed_feature)
    if (
        external.selected_target_feature != checked.replacement_feature
        or frozen.target_row_count != checked.declared_target_row_count
        or frozen.data_drift_capacity_count != checked.eligible_row_count
        or frozen.data_drift_feasible
    ):
        _fail("v1.1 covariate-capacity evidence does not explain the terminal failure")

    base = Path(root).resolve()

    def resolve(path: str | Path) -> Path:
        candidate = Path(path)
        return candidate if candidate.is_absolute() else base / candidate

    registration = resolve(registration_path)
    marker = resolve(marker_path)
    store = resolve(terminal_store_path)
    manifest = load_and_verify_recovery_terminal_store(store)
    failure_path = store / "scientific-store" / "technical-failure.json"
    environment_path = store / "scientific-store" / "environment.json"
    try:
        failure = P2RTechnicalFailure.model_validate_json(
            failure_path.read_text(encoding="utf-8")
        )
        environment = json.loads(environment_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise P2RReplicationFailureError("v1.1 terminal failure evidence is invalid") from exc
    observed = (
        manifest.recovery_protocol_sha256s,
        manifest.recovery_registration_sha256s,
        manifest.predecessor_protocol_sha256s,
        failure.execution_commit,
        failure.failure_stage,
        failure.exception_class,
        failure.exception_message_sha256,
        manifest.store_sha256,
        manifest.scientific_store_sha256,
        environment.get("execution_commit"),
    )
    expected = (
        checked.recovery_protocol_sha256s,
        checked.recovery_registration_sha256s,
        checked.scientific_protocol_sha256s,
        checked.execution_commit,
        checked.failure_stage,
        checked.exception_class,
        checked.exception_message_sha256,
        checked.terminal_store_sha256,
        checked.scientific_store_sha256,
        checked.execution_commit,
    )
    if observed != expected:
        _fail("v1.1 replication-failure audit does not reconcile with terminal evidence")
    file_evidence = (
        (_file_sha256(registration), checked.registration_file_sha256),
        (_file_sha256(marker), checked.sealed_marker_file_sha256),
        (_file_sha256(store / "manifest.json"), checked.terminal_manifest_file_sha256),
        (_file_sha256(failure_path), checked.terminal_failure_file_sha256),
        (_file_sha256(environment_path), checked.terminal_environment_file_sha256),
    )
    if any(observed_hash != expected_hash for observed_hash, expected_hash in file_evidence):
        _fail("v1.1 failure artifact bytes differ from the tracked audit")
    return checked
