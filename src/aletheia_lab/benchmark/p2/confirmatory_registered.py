"""Registered data and execution contracts for the confirmatory study.

This module is deliberately separate from the command that opens the sealed
partitions.  It validates registered inputs, executes one complete dataset
matrix, and returns an in-memory result.  Publication is handled atomically by
the closeout store so neither the primary nor replication result can be
inspected in isolation.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final, Literal, TypeVar

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_execution import (
    ConfirmatoryExecutionError,
    ConfirmatoryMutation,
    ConfirmatorySplit,
    ConfirmatoryTrainingSource,
    ControlGate,
    ProbabilityMetrics,
    ProbabilityVector,
    TargetControlResult,
    apply_class_conditional_noise,
    build_confirmatory_split,
    labelled_targets_sha256,
    mutation_spec_for,
    repair_labels,
    score_probabilities,
    serialization_roundtrip,
    symmetric_matched_count,
    validate_complete_target_controls,
    validate_control_predictions,
)
from aletheia_lab.benchmark.p2.confirmatory_inference import (
    ConfirmatoryReplicate,
    DatasetAnalysis,
    InferenceRunPlan,
    analyze_dataset,
    build_replicate,
)
from aletheia_lab.benchmark.p2.confirmatory_protocol import (
    ConfirmatoryProtocol,
    DatasetBinding,
)
from aletheia_lab.benchmark.p2.confirmatory_runtime import (
    feature_frame_sha256,
    fit_frozen_probability_vector,
    frozen_model_specification_sha256,
    frozen_preprocessing_sha256,
)
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN

REGISTERED_DATASET_SCHEMA_VERSION: Final[Literal["p2-confirmatory-registered-dataset/1"]] = (
    "p2-confirmatory-registered-dataset/1"
)
REPLICATE_ARTIFACT_SCHEMA_VERSION: Final[Literal["p2-confirmatory-replicate-artifact/1"]] = (
    "p2-confirmatory-replicate-artifact/1"
)
DATASET_OUTCOME_SCHEMA_VERSION: Final[Literal["p2-confirmatory-dataset-outcome/1"]] = (
    "p2-confirmatory-dataset-outcome/1"
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
DatasetRole = Literal["primary", "external_replication"]
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _revalidated(model: _ModelT) -> _ModelT:
    return type(model).model_validate(model.model_dump())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ConfirmatoryExecutionError(f"cannot read registered dataset: {path}") from exc
    return digest.hexdigest()


class RegisteredDatasetReceipt(_StrictFrozenModel):
    schema_version: Literal["p2-confirmatory-registered-dataset/1"] = (
        REGISTERED_DATASET_SCHEMA_VERSION
    )
    protocol_sha256: Sha256
    dataset_id: str = Field(min_length=1)
    dataset_role: DatasetRole
    snapshot_sha256: Sha256
    archive_sha256: Sha256 | None
    source_path_name: str = Field(min_length=1)
    row_count: int = Field(ge=5)
    feature_columns: tuple[str, ...]
    excluded_features: tuple[str, ...]
    target_column: str = Field(min_length=1)
    positive_label: str = Field(min_length=1)
    negative_label: str = Field(min_length=1)
    record_membership_sha256: Sha256
    feature_matrix_sha256: Sha256
    target_artifact_sha256: Sha256

    @model_validator(mode="after")
    def _schema_is_usable(self) -> RegisteredDatasetReceipt:
        if not self.feature_columns or len(set(self.feature_columns)) != len(self.feature_columns):
            raise ValueError("registered feature columns must be non-empty and unique")
        if set(self.excluded_features) & set(self.feature_columns):
            raise ValueError("excluded features cannot reach the registered model frame")
        if self.target_column in self.feature_columns:
            raise ValueError("the target cannot appear in the registered feature frame")
        if self.positive_label == self.negative_label:
            raise ValueError("registered binary labels must be distinct")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


@dataclass(frozen=True)
class RegisteredDataset:
    """Validated frame kept outside Pydantic while its identity is attested."""

    binding: DatasetBinding
    receipt: RegisteredDatasetReceipt
    record_ids: tuple[str, ...]
    targets: tuple[int, ...]
    features: pd.DataFrame


def _record_membership_sha256(record_ids: Sequence[str]) -> str:
    return canonical_sha256(
        {
            "schema_version": REGISTERED_DATASET_SCHEMA_VERSION,
            "record_ids": sorted(record_ids),
        }
    )


def load_registered_dataset(
    *,
    protocol: ConfirmatoryProtocol,
    dataset: DatasetBinding,
    snapshot_path: str | Path,
    archive_path: str | Path | None = None,
) -> RegisteredDataset:
    """Load only a byte-pinned dataset with canonical IDs and binary targets."""

    protocol = _revalidated(protocol)
    dataset = _revalidated(dataset)
    if dataset not in protocol.datasets:
        raise ConfirmatoryExecutionError("dataset is outside the frozen confirmatory protocol")
    source = Path(snapshot_path)
    if _sha256_file(source) != dataset.snapshot_sha256:
        raise ConfirmatoryExecutionError("registered dataset snapshot checksum mismatch")
    observed_archive: str | None = None
    if dataset.archive_sha256 is not None:
        if archive_path is None:
            raise ConfirmatoryExecutionError("external replication requires its pinned archive")
        observed_archive = _sha256_file(Path(archive_path))
        if observed_archive != dataset.archive_sha256:
            raise ConfirmatoryExecutionError("registered dataset archive checksum mismatch")
    elif archive_path is not None:
        raise ConfirmatoryExecutionError("the primary dataset must not receive an archive")
    try:
        frame = pd.read_csv(
            source,
            sep=";" if dataset.role == "external_replication" else ",",
            keep_default_na=False,
        )
    except (OSError, ValueError) as exc:
        raise ConfirmatoryExecutionError("cannot parse registered dataset snapshot") from exc
    if frame.empty or dataset.target_column not in frame.columns:
        raise ConfirmatoryExecutionError("registered target column is missing")
    if len(set(str(column) for column in frame.columns)) != len(frame.columns):
        raise ConfirmatoryExecutionError("registered dataset columns must be unique")
    labels = tuple(str(value).strip() for value in frame[dataset.target_column])
    observed_labels = set(labels)
    if dataset.positive_label not in observed_labels or len(observed_labels) != 2:
        raise ConfirmatoryExecutionError("registered target must contain exactly two labels")
    negative_label = next(value for value in observed_labels if value != dataset.positive_label)
    targets = tuple(1 if value == dataset.positive_label else 0 for value in labels)
    if dataset.role == "primary":
        if "customerID" not in frame.columns:
            raise ConfirmatoryExecutionError("primary record identifier column is missing")
        record_ids = tuple(str(value).strip() for value in frame["customerID"])
        identifier_columns = {"customerID"}
    else:
        record_ids = tuple(f"bank-row-{index:05d}" for index in range(len(frame)))
        identifier_columns = set()
    if any(not record_id for record_id in record_ids) or len(record_ids) != len(set(record_ids)):
        raise ConfirmatoryExecutionError(
            "registered record identifiers must be non-empty and unique"
        )
    excluded = set(dataset.excluded_features)
    if not excluded.issubset(set(str(column) for column in frame.columns)):
        raise ConfirmatoryExecutionError("a frozen excluded feature is absent from the snapshot")
    drop_columns = {dataset.target_column, *identifier_columns, *excluded}
    features = frame.drop(columns=sorted(drop_columns)).reset_index(drop=True)
    if features.empty or features.shape[1] == 0:
        raise ConfirmatoryExecutionError("registered feature frame is empty")
    feature_columns = tuple(str(column) for column in features.columns)
    receipt = RegisteredDatasetReceipt(
        protocol_sha256=protocol.canonical_sha256(),
        dataset_id=dataset.dataset_id,
        dataset_role=dataset.role,
        snapshot_sha256=dataset.snapshot_sha256,
        archive_sha256=observed_archive,
        source_path_name=source.name,
        row_count=len(frame),
        feature_columns=feature_columns,
        excluded_features=dataset.excluded_features,
        target_column=dataset.target_column,
        positive_label=dataset.positive_label,
        negative_label=negative_label,
        record_membership_sha256=_record_membership_sha256(record_ids),
        feature_matrix_sha256=feature_frame_sha256(features, record_ids),
        target_artifact_sha256=labelled_targets_sha256(record_ids, targets),
    )
    return RegisteredDataset(
        binding=dataset,
        receipt=receipt,
        record_ids=record_ids,
        targets=targets,
        features=features,
    )


class ReplicateArtifact(_StrictFrozenModel):
    schema_version: Literal["p2-confirmatory-replicate-artifact/1"] = (
        REPLICATE_ARTIFACT_SCHEMA_VERSION
    )
    replicate: ConfirmatoryReplicate
    mutation: ConfirmatoryMutation
    observed_vector: ProbabilityVector
    symmetric_vector: ProbabilityVector
    target_controls: tuple[TargetControlResult, ...]

    @model_validator(mode="after")
    def _evidence_reconciles(self) -> ReplicateArtifact:
        if self.replicate.mutation_sha256 != self.mutation.canonical_sha256():
            raise ValueError("replicate is not bound to its mutation artifact")
        if self.observed_vector.role != "class_conditional":
            raise ValueError("observed vector has the wrong execution role")
        if self.symmetric_vector.role != "symmetric_matched_count":
            raise ValueError("symmetric vector has the wrong control role")
        if self.observed_vector.training_targets_sha256 != (self.mutation.mutated_targets_sha256):
            raise ValueError("observed vector is not trained on the mutation artifact")
        expected_controls = (
            "serialization_roundtrip",
            "symmetric_matched_count",
            "label_repair",
        )
        if tuple(item.control_id for item in self.target_controls) != expected_controls:
            raise ValueError("replicate target controls are incomplete or unordered")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class DatasetOutcome(_StrictFrozenModel):
    schema_version: Literal["p2-confirmatory-dataset-outcome/1"] = DATASET_OUTCOME_SCHEMA_VERSION
    receipt: RegisteredDatasetReceipt
    split: ConfirmatorySplit
    training_source: ConfirmatoryTrainingSource
    clean_vector: ProbabilityVector
    clean_metrics: ProbabilityMetrics
    serialization_vector: ProbabilityVector
    repair_vector: ProbabilityVector
    replicates: tuple[ReplicateArtifact, ...]
    analysis: DatasetAnalysis

    @model_validator(mode="after")
    def _complete_batch_reconciles(self) -> DatasetOutcome:
        if self.receipt.dataset_id != self.analysis.dataset_id:
            raise ValueError("dataset receipt and analysis disagree")
        if self.split.manifest.canonical_sha256() != self.analysis.split_manifest_sha256:
            raise ValueError("dataset analysis is not bound to its split")
        if self.clean_metrics.vector_sha256 != self.clean_vector.canonical_sha256():
            raise ValueError("clean metrics are not bound to the clean vector")
        if len(self.replicates) != 180 or self.analysis.replicate_count != 180:
            raise ValueError("registered dataset outcome requires all 180 replicates")
        replicate_hashes = tuple(item.replicate.canonical_sha256() for item in self.replicates)
        if len(set(replicate_hashes)) != len(replicate_hashes):
            raise ValueError("registered dataset outcome contains replayed evidence")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def _slice_frame(dataset: RegisteredDataset, record_ids: Sequence[str]) -> pd.DataFrame:
    position = {record_id: index for index, record_id in enumerate(dataset.record_ids)}
    try:
        rows = [position[record_id] for record_id in record_ids]
    except KeyError as exc:
        raise ConfirmatoryExecutionError("split references an unknown dataset record") from exc
    return dataset.features.iloc[rows].reset_index(drop=True)


def _targets_for(dataset: RegisteredDataset, record_ids: Sequence[str]) -> tuple[int, ...]:
    target_by_id = dict(zip(dataset.record_ids, dataset.targets, strict=True))
    try:
        return tuple(target_by_id[record_id] for record_id in record_ids)
    except KeyError as exc:
        raise ConfirmatoryExecutionError("split target references an unknown record") from exc


def execute_registered_dataset(
    *, protocol: ConfirmatoryProtocol, registered: RegisteredDataset
) -> DatasetOutcome:
    """Open one sealed partition and execute all 180 prespecified replicates."""

    protocol = _revalidated(protocol)
    dataset = _revalidated(registered.binding)
    receipt = _revalidated(registered.receipt)
    if (
        receipt.protocol_sha256 != protocol.canonical_sha256()
        or receipt.dataset_id != dataset.dataset_id
        or receipt.dataset_role != dataset.role
        or receipt.snapshot_sha256 != dataset.snapshot_sha256
        or receipt.archive_sha256 != dataset.archive_sha256
    ):
        raise ConfirmatoryExecutionError("registered dataset receipt uses another protocol input")
    if (
        len(registered.record_ids) != receipt.row_count
        or len(registered.targets) != receipt.row_count
        or len(registered.features) != receipt.row_count
        or tuple(str(column) for column in registered.features.columns) != receipt.feature_columns
        or _record_membership_sha256(registered.record_ids) != receipt.record_membership_sha256
        or feature_frame_sha256(registered.features, registered.record_ids)
        != receipt.feature_matrix_sha256
        or labelled_targets_sha256(registered.record_ids, registered.targets)
        != receipt.target_artifact_sha256
    ):
        raise ConfirmatoryExecutionError("registered dataset content disagrees with its receipt")
    split = build_confirmatory_split(
        protocol=protocol,
        dataset=dataset,
        dataset_sha256=registered.receipt.snapshot_sha256,
        record_ids=registered.record_ids,
        labels=registered.targets,
    )
    train_ids = split.membership.train
    test_ids = split.membership.sealed_test
    training_features = _slice_frame(registered, train_ids)
    evaluation_features = _slice_frame(registered, test_ids)
    training_targets = _targets_for(registered, train_ids)
    evaluation_targets = _targets_for(registered, test_ids)
    source = ConfirmatoryTrainingSource(
        dataset_id=dataset.dataset_id,
        dataset_role=dataset.role,
        record_ids=train_ids,
        clean_targets=training_targets,
        dataset_sha256=registered.receipt.snapshot_sha256,
        split_manifest_sha256=split.manifest.canonical_sha256(),
        feature_matrix_sha256=feature_frame_sha256(training_features, train_ids),
        preprocessing_sha256=frozen_preprocessing_sha256(),
        model_specification_sha256=frozen_model_specification_sha256(protocol),
        protocol_sha256=protocol.canonical_sha256(),
    )
    clean_vector = fit_frozen_probability_vector(
        protocol=protocol,
        dataset=dataset,
        source=source,
        training_features=training_features,
        training_targets=training_targets,
        evaluation_features=evaluation_features,
        evaluation_record_ids=test_ids,
        role="clean_reference",
    )
    clean_metrics = score_probabilities(true_labels=evaluation_targets, vector=clean_vector)
    roundtrip_reference = serialization_roundtrip(source)
    serialization_vector = fit_frozen_probability_vector(
        protocol=protocol,
        dataset=dataset,
        source=source,
        training_features=training_features,
        training_targets=roundtrip_reference.targets,
        evaluation_features=evaluation_features,
        evaluation_record_ids=test_ids,
        role="serialization_roundtrip",
    )
    repair_vector = fit_frozen_probability_vector(
        protocol=protocol,
        dataset=dataset,
        source=source,
        training_features=training_features,
        training_targets=training_targets,
        evaluation_features=evaluation_features,
        evaluation_record_ids=test_ids,
        role="label_repair",
    )
    artifacts: list[ReplicateArtifact] = []
    for cell in protocol.intervention_cells:
        seeds = (
            cell.primary_replicate_seeds
            if dataset.role == "primary"
            else cell.replication_replicate_seeds
        )
        for seed in seeds:
            spec = mutation_spec_for(protocol=protocol, dataset=dataset, cell=cell, seed=seed)
            mutation = apply_class_conditional_noise(source=source, spec=spec)
            roundtrip = roundtrip_reference
            symmetric = symmetric_matched_count(source=source, mutation=mutation)
            repair = repair_labels(source=source, mutation=mutation)
            controls_by_name: dict[str, TargetControlResult] = {
                roundtrip.control_id: roundtrip,
                symmetric.control_id: symmetric,
                repair.control_id: repair,
            }
            validate_complete_target_controls(controls_by_name)
            observed_vector = fit_frozen_probability_vector(
                protocol=protocol,
                dataset=dataset,
                source=source,
                training_features=training_features,
                training_targets=mutation.mutated_targets,
                evaluation_features=evaluation_features,
                evaluation_record_ids=test_ids,
                role="class_conditional",
            )
            symmetric_vector = fit_frozen_probability_vector(
                protocol=protocol,
                dataset=dataset,
                source=source,
                training_features=training_features,
                training_targets=symmetric.targets,
                evaluation_features=evaluation_features,
                evaluation_record_ids=test_ids,
                role="symmetric_matched_count",
            )
            gates: tuple[ControlGate, ...] = validate_control_predictions(
                clean=clean_vector,
                serialization=serialization_vector,
                symmetric=symmetric_vector,
                repair=repair_vector,
                target_controls=controls_by_name,
            )
            observed_metrics = score_probabilities(
                true_labels=evaluation_targets, vector=observed_vector
            )
            replicate = build_replicate(
                dataset_id=dataset.dataset_id,
                dataset_role=dataset.role,
                cell_id=cell.cell_id,
                direction=cell.flip_direction,
                conditional_rate=cell.conditional_flip_rate,
                seed=seed,
                protocol_sha256=protocol.canonical_sha256(),
                split_manifest_sha256=split.manifest.canonical_sha256(),
                mutation_sha256=mutation.canonical_sha256(),
                clean_metrics=clean_metrics,
                observed_metrics=observed_metrics,
                controls=gates,
            )
            artifacts.append(
                ReplicateArtifact(
                    replicate=replicate,
                    mutation=mutation,
                    observed_vector=observed_vector,
                    symmetric_vector=symmetric_vector,
                    target_controls=(roundtrip, symmetric, repair),
                )
            )
    if len(artifacts) != 180:
        raise ConfirmatoryExecutionError("registered execution did not produce 180 replicates")
    analysis = analyze_dataset(
        protocol=protocol,
        dataset=dataset,
        replicates=tuple(item.replicate for item in artifacts),
        run_plan=InferenceRunPlan.registered(protocol),
    )
    if not analysis.batch_technical_gates_pass:
        raise ConfirmatoryExecutionError("registered dataset has a failed technical control")
    return DatasetOutcome(
        receipt=registered.receipt,
        split=split,
        training_source=source,
        clean_vector=clean_vector,
        clean_metrics=clean_metrics,
        serialization_vector=serialization_vector,
        repair_vector=repair_vector,
        replicates=tuple(artifacts),
        analysis=analysis,
    )


def validate_dose_monotonicity(outcome: DatasetOutcome) -> dict[str, bool]:
    """Report, but never admit on, monotonic mean dose-response behavior."""

    outcome = _revalidated(outcome)
    result: dict[str, bool] = {}
    for direction in ("yes_to_no", "no_to_yes"):
        values = [
            item.mean_relative_log_loss_increase
            for item in outcome.analysis.dose_summaries
            if item.direction == direction
        ]
        if len(values) != 3 or any(not math.isfinite(value) for value in values):
            raise ConfirmatoryExecutionError("dose summaries are incomplete")
        result[direction] = values[0] <= values[1] <= values[2]
    return result
