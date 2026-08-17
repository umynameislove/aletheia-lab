"""Outcome-agnostic execution kernel for the label-noise confirmation study.

This module implements the pieces that must be validated before the sealed
datasets are opened: deterministic split membership, class-conditional target
mutation, matched controls and probability-based scoring.  It deliberately has
no data downloader and no command that can execute the registered study.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Final, Literal, NoReturn, TypeVar

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sklearn.metrics import roc_auc_score  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_protocol import (
    ConfirmatoryProtocol,
    DatasetBinding,
    InterventionCell,
)
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN
from aletheia_lab.benchmark.p2.validation import ContractViolation

EXECUTION_SCHEMA_VERSION: Final[Literal["p2-label-noise-confirmatory-execution/1"]] = (
    "p2-label-noise-confirmatory-execution/1"
)
SPLIT_SCHEMA_VERSION: Final[Literal["p2-confirmatory-split/1"]] = "p2-confirmatory-split/1"
MUTATION_SCHEMA_VERSION: Final[Literal["p2-confirmatory-class-conditional-mutation/1"]] = (
    "p2-confirmatory-class-conditional-mutation/1"
)
METRIC_SCHEMA_VERSION: Final[Literal["p2-confirmatory-probability-metrics/1"]] = (
    "p2-confirmatory-probability-metrics/1"
)
SPLIT_ALGORITHM: Final[Literal["seeded-classwise-sha256-or-source-order/v1"]] = (
    "seeded-classwise-sha256-or-source-order/v1"
)
SELECTION_ALGORITHM: Final[Literal["seeded-source-class-sha256/v1"]] = (
    "seeded-source-class-sha256/v1"
)
COUNT_RULE: Final[Literal["decimal-round-half-up/v1"]] = "decimal-round-half-up/v1"
PROBABILITY_CLIP: Final[float] = 1e-15
FLOAT_TOLERANCE: Final[float] = 1e-12
BINARY_LABELS: Final[frozenset[int]] = frozenset({0, 1})
REQUIRED_CONTROL_IDS: Final[tuple[str, ...]] = (
    "clean_reference",
    "serialization_roundtrip",
    "symmetric_matched_count",
    "label_repair",
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
DatasetRole = Literal["primary", "external_replication"]
SplitName = Literal["train", "development", "sealed_test"]
FlipDirection = Literal["yes_to_no", "no_to_yes"]
PredictionRole = Literal[
    "clean_reference",
    "class_conditional",
    "serialization_roundtrip",
    "symmetric_matched_count",
    "label_repair",
]

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class ConfirmatoryExecutionError(ContractViolation):
    """Raised when independently valid confirmatory artifacts disagree."""


def _fail(message: str) -> NoReturn:
    raise ConfirmatoryExecutionError(message)


def _revalidated(model: _ModelT) -> _ModelT:
    return type(model).model_validate(model.model_dump())


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _validate_record_ids(record_ids: Sequence[str]) -> None:
    if not record_ids:
        raise ValueError("at least one record is required")
    if len(set(record_ids)) != len(record_ids):
        raise ValueError("record identifiers must be unique")
    for record_id in record_ids:
        if not record_id or record_id != record_id.strip():
            raise ValueError("record identifiers must be non-blank and trimmed")


def _validate_binary(labels: Sequence[int], *, label: str) -> None:
    if not labels:
        raise ValueError(f"{label} must not be empty")
    if any(value not in BINARY_LABELS for value in labels):
        raise ValueError(f"{label} must contain binary values only")


def _labelled_sha256(record_ids: Sequence[str], labels: Sequence[int]) -> str:
    records = [
        {"record_id": record_id, "label": value}
        for record_id, value in zip(record_ids, labels, strict=True)
    ]
    records.sort(key=lambda item: str(item["record_id"]))
    return canonical_sha256({"schema_version": EXECUTION_SCHEMA_VERSION, "records": records})


def labelled_targets_sha256(record_ids: Sequence[str], labels: Sequence[int]) -> str:
    """Public order-independent identity for a labelled training artifact."""

    _validate_record_ids(record_ids)
    _validate_binary(labels, label="targets")
    if len(record_ids) != len(labels):
        _fail("record identifiers and targets must align")
    return _labelled_sha256(record_ids, labels)


def _membership_sha256(record_ids: Sequence[str]) -> str:
    return canonical_sha256(
        {"schema_version": SPLIT_SCHEMA_VERSION, "record_ids": sorted(record_ids)}
    )


def _rank(*, namespace: str, seed: int, record_id: str) -> str:
    return canonical_sha256(
        {
            "schema_version": EXECUTION_SCHEMA_VERSION,
            "namespace": namespace,
            "seed": seed,
            "record_id": record_id,
        }
    )


def _rounded_count(rate: float, denominator: int) -> int:
    if denominator <= 0:
        _fail("a mutation requires a non-empty eligible source class")
    if not math.isfinite(rate) or not 0.0 < rate < 0.5:
        _fail("the conditional rate must be finite and lie strictly between zero and 0.5")
    return int(
        (Decimal(str(rate)) * Decimal(denominator)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


class SplitMembership(_StrictFrozenModel):
    """Evaluator-only record membership for the three frozen partitions."""

    train: tuple[str, ...]
    development: tuple[str, ...]
    sealed_test: tuple[str, ...]

    @model_validator(mode="after")
    def _partitions_are_disjoint(self) -> SplitMembership:
        for values in (self.train, self.development, self.sealed_test):
            _validate_record_ids(values)
        train, development, test = map(set, (self.train, self.development, self.sealed_test))
        if train & development or train & test or development & test:
            raise ValueError("confirmatory split partitions must be disjoint")
        return self

    def all_record_ids(self) -> frozenset[str]:
        return frozenset((*self.train, *self.development, *self.sealed_test))


class ConfirmatorySplitManifest(_StrictFrozenModel):
    schema_version: Literal["p2-confirmatory-split/1"] = SPLIT_SCHEMA_VERSION
    algorithm: Literal["seeded-classwise-sha256-or-source-order/v1"] = SPLIT_ALGORITHM
    protocol_sha256: Sha256
    dataset_id: str = Field(min_length=1)
    dataset_role: DatasetRole
    dataset_sha256: Sha256
    strategy: Literal["seeded_stratified", "source_order_temporal"]
    seed: int | None
    train_fraction: float
    development_fraction: float
    sealed_test_fraction: float
    source_record_count: int = Field(ge=3)
    source_membership_sha256: Sha256
    train_count: int = Field(ge=1)
    development_count: int = Field(ge=1)
    sealed_test_count: int = Field(ge=1)
    train_membership_sha256: Sha256
    development_membership_sha256: Sha256
    sealed_test_membership_sha256: Sha256

    @model_validator(mode="after")
    def _counts_and_strategy_are_complete(self) -> ConfirmatorySplitManifest:
        if self.train_count + self.development_count + self.sealed_test_count != (
            self.source_record_count
        ):
            raise ValueError("split counts must reconcile to the source record count")
        if self.strategy == "seeded_stratified" and self.seed is None:
            raise ValueError("the stratified split requires its frozen seed")
        if self.strategy == "source_order_temporal" and self.seed is not None:
            raise ValueError("the temporal split must not use a random seed")
        if not math.isclose(
            self.train_fraction + self.development_fraction + self.sealed_test_fraction,
            1.0,
            abs_tol=FLOAT_TOLERANCE,
        ):
            raise ValueError("split fractions must sum to one")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class ConfirmatorySplit(_StrictFrozenModel):
    manifest: ConfirmatorySplitManifest
    membership: SplitMembership

    @model_validator(mode="after")
    def _manifest_matches_membership(self) -> ConfirmatorySplit:
        values = self.membership
        if len(values.all_record_ids()) != self.manifest.source_record_count:
            raise ValueError("split membership does not cover the declared source")
        checks = (
            (len(values.train), self.manifest.train_count, "train count"),
            (len(values.development), self.manifest.development_count, "development count"),
            (len(values.sealed_test), self.manifest.sealed_test_count, "sealed-test count"),
            (
                _membership_sha256(values.train),
                self.manifest.train_membership_sha256,
                "train membership",
            ),
            (
                _membership_sha256(values.development),
                self.manifest.development_membership_sha256,
                "development membership",
            ),
            (
                _membership_sha256(values.sealed_test),
                self.manifest.sealed_test_membership_sha256,
                "sealed-test membership",
            ),
        )
        for observed, expected, label in checks:
            if observed != expected:
                raise ValueError(f"{label} does not match the split manifest")
        return self


def _classwise_partition(
    record_ids: Sequence[str], labels: Sequence[int], *, seed: int
) -> SplitMembership:
    partitions: dict[str, list[str]] = {"train": [], "development": [], "sealed_test": []}
    for source_label in (0, 1):
        eligible = [
            record_id
            for record_id, value in zip(record_ids, labels, strict=True)
            if value == source_label
        ]
        eligible.sort(
            key=lambda record_id: (
                _rank(namespace=f"split-class-{source_label}", seed=seed, record_id=record_id),
                record_id,
            )
        )
        if len(eligible) < 5:
            _fail("each class needs at least five records for a 60/20/20 stratified split")
        train_end = int(Decimal("0.6") * len(eligible))
        development_end = train_end + int(Decimal("0.2") * len(eligible))
        partitions["train"].extend(eligible[:train_end])
        partitions["development"].extend(eligible[train_end:development_end])
        partitions["sealed_test"].extend(eligible[development_end:])
    return SplitMembership(
        train=tuple(sorted(partitions["train"])),
        development=tuple(sorted(partitions["development"])),
        sealed_test=tuple(sorted(partitions["sealed_test"])),
    )


def _temporal_partition(record_ids: Sequence[str], labels: Sequence[int]) -> SplitMembership:
    size = len(record_ids)
    train_end = int(Decimal("0.6") * size)
    development_end = train_end + int(Decimal("0.2") * size)
    ranges = (
        (record_ids[:train_end], labels[:train_end], "train"),
        (record_ids[train_end:development_end], labels[train_end:development_end], "development"),
        (record_ids[development_end:], labels[development_end:], "sealed_test"),
    )
    for _, partition_labels, name in ranges:
        if set(partition_labels) != BINARY_LABELS:
            _fail(f"the temporal {name} partition must contain both classes")
    return SplitMembership(
        train=tuple(record_ids[:train_end]),
        development=tuple(record_ids[train_end:development_end]),
        sealed_test=tuple(record_ids[development_end:]),
    )


def build_confirmatory_split(
    *,
    protocol: ConfirmatoryProtocol,
    dataset: DatasetBinding,
    dataset_sha256: str,
    record_ids: Sequence[str],
    labels: Sequence[int],
) -> ConfirmatorySplit:
    """Build and hash the exact split without exposing any test outcome."""

    protocol = _revalidated(protocol)
    dataset = _revalidated(dataset)
    _validate_record_ids(record_ids)
    _validate_binary(labels, label="dataset labels")
    if len(record_ids) != len(labels):
        _fail("dataset record identifiers and labels must align")
    if dataset not in protocol.datasets:
        _fail("the dataset binding is not part of this protocol")
    if dataset.snapshot_sha256 != dataset_sha256:
        _fail("the dataset snapshot does not match the frozen protocol")
    if dataset.split.strategy == "seeded_stratified":
        assert dataset.split.seed is not None
        membership = _classwise_partition(record_ids, labels, seed=dataset.split.seed)
    else:
        membership = _temporal_partition(record_ids, labels)
    manifest = ConfirmatorySplitManifest(
        protocol_sha256=protocol.canonical_sha256(),
        dataset_id=dataset.dataset_id,
        dataset_role=dataset.role,
        dataset_sha256=dataset_sha256,
        strategy=dataset.split.strategy,
        seed=dataset.split.seed,
        train_fraction=dataset.split.train_fraction,
        development_fraction=dataset.split.development_fraction,
        sealed_test_fraction=dataset.split.sealed_test_fraction,
        source_record_count=len(record_ids),
        source_membership_sha256=_membership_sha256(record_ids),
        train_count=len(membership.train),
        development_count=len(membership.development),
        sealed_test_count=len(membership.sealed_test),
        train_membership_sha256=_membership_sha256(membership.train),
        development_membership_sha256=_membership_sha256(membership.development),
        sealed_test_membership_sha256=_membership_sha256(membership.sealed_test),
    )
    result = ConfirmatorySplit(manifest=manifest, membership=membership)
    if result.membership.all_record_ids() != frozenset(record_ids):
        _fail("the confirmatory split lost or introduced records")
    return result


class ConfirmatoryTrainingSource(_StrictFrozenModel):
    """Training targets and attestations; feature values are absent by design."""

    dataset_id: str = Field(min_length=1)
    dataset_role: DatasetRole
    record_ids: tuple[str, ...]
    clean_targets: tuple[int, ...]
    dataset_sha256: Sha256
    split_manifest_sha256: Sha256
    feature_matrix_sha256: Sha256
    preprocessing_sha256: Sha256
    model_specification_sha256: Sha256
    protocol_sha256: Sha256

    @model_validator(mode="after")
    def _source_is_usable(self) -> ConfirmatoryTrainingSource:
        _validate_record_ids(self.record_ids)
        _validate_binary(self.clean_targets, label="clean training targets")
        if len(self.record_ids) != len(self.clean_targets):
            raise ValueError("training identifiers and targets must align")
        if set(self.clean_targets) != BINARY_LABELS:
            raise ValueError("the training source must contain both classes")
        return self

    def targets_sha256(self) -> str:
        return _labelled_sha256(self.record_ids, self.clean_targets)

    def artifact_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class ConfirmatoryMutationSpec(_StrictFrozenModel):
    cell_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_role: DatasetRole
    direction: FlipDirection
    conditional_rate: float = Field(gt=0.0, lt=0.5)
    rate_denominator: Literal["source_class_count"]
    seed: int = Field(ge=0)
    protocol_sha256: Sha256


def mutation_spec_for(
    *, protocol: ConfirmatoryProtocol, dataset: DatasetBinding, cell: InterventionCell, seed: int
) -> ConfirmatoryMutationSpec:
    protocol = _revalidated(protocol)
    dataset = _revalidated(dataset)
    cell = _revalidated(cell)
    if dataset not in protocol.datasets or cell not in protocol.intervention_cells:
        _fail("the requested mutation is outside the frozen protocol grid")
    expected_seeds = (
        cell.primary_replicate_seeds
        if dataset.role == "primary"
        else cell.replication_replicate_seeds
    )
    if seed not in expected_seeds:
        _fail("the requested corruption seed is outside the frozen namespace")
    return ConfirmatoryMutationSpec(
        cell_id=cell.cell_id,
        dataset_id=dataset.dataset_id,
        dataset_role=dataset.role,
        direction=cell.flip_direction,
        conditional_rate=cell.conditional_flip_rate,
        rate_denominator=cell.rate_denominator,
        seed=seed,
        protocol_sha256=protocol.canonical_sha256(),
    )


class MutationEntry(_StrictFrozenModel):
    record_id: str = Field(min_length=1)
    original_label: int
    mutated_label: int

    @model_validator(mode="after")
    def _is_binary_flip(self) -> MutationEntry:
        if {self.original_label, self.mutated_label} != BINARY_LABELS:
            raise ValueError("a confirmatory mutation entry must be a binary flip")
        return self


class ConfirmatoryMutation(_StrictFrozenModel):
    schema_version: Literal["p2-confirmatory-class-conditional-mutation/1"] = (
        MUTATION_SCHEMA_VERSION
    )
    spec: ConfirmatoryMutationSpec
    record_ids: tuple[str, ...]
    mutated_targets: tuple[int, ...]
    entries: tuple[MutationEntry, ...]
    source_class_count: int = Field(ge=1)
    mutation_count: int = Field(ge=1)
    achieved_conditional_rate: float = Field(gt=0.0, le=1.0)
    source_sha256: Sha256
    source_targets_sha256: Sha256
    mutated_targets_sha256: Sha256
    mutation_map_sha256: Sha256

    @model_validator(mode="after")
    def _mutation_is_reconciled(self) -> ConfirmatoryMutation:
        _validate_record_ids(self.record_ids)
        _validate_binary(self.mutated_targets, label="mutated training targets")
        if len(self.record_ids) != len(self.mutated_targets):
            raise ValueError("mutated identifiers and targets must align")
        if len(self.entries) != self.mutation_count:
            raise ValueError("mutation entries must reconcile to the mutation count")
        if len({entry.record_id for entry in self.entries}) != len(self.entries):
            raise ValueError("a training record may be mutated at most once")
        expected = self.mutation_count / self.source_class_count
        if not math.isclose(self.achieved_conditional_rate, expected, abs_tol=FLOAT_TOLERANCE):
            raise ValueError("achieved conditional rate must use the source-class denominator")
        if self.source_targets_sha256 == self.mutated_targets_sha256:
            raise ValueError("a confirmatory mutation must change the target artifact")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def apply_class_conditional_noise(
    *, source: ConfirmatoryTrainingSource, spec: ConfirmatoryMutationSpec
) -> ConfirmatoryMutation:
    """Flip a fixed fraction of one source class without receiving features."""

    source = _revalidated(source)
    spec = _revalidated(spec)
    if (
        source.dataset_id != spec.dataset_id
        or source.dataset_role != spec.dataset_role
        or source.protocol_sha256 != spec.protocol_sha256
    ):
        _fail("the mutation spec is not bound to this training source")
    source_label = 1 if spec.direction == "yes_to_no" else 0
    eligible = [
        record_id
        for record_id, value in zip(source.record_ids, source.clean_targets, strict=True)
        if value == source_label
    ]
    count = _rounded_count(spec.conditional_rate, len(eligible))
    if count == 0:
        _fail("the frozen conditional rate has no effect at this sample size")
    eligible.sort(
        key=lambda record_id: (
            _rank(
                namespace=f"class-conditional-{spec.direction}",
                seed=spec.seed,
                record_id=record_id,
            ),
            record_id,
        )
    )
    selected = frozenset(eligible[:count])
    original = dict(zip(source.record_ids, source.clean_targets, strict=True))
    mutated = tuple(
        1 - value if record_id in selected else value
        for record_id, value in zip(source.record_ids, source.clean_targets, strict=True)
    )
    if set(mutated) != BINARY_LABELS:
        _fail("the class-conditional mutation erased a training class")
    entries = tuple(
        MutationEntry(
            record_id=record_id,
            original_label=original[record_id],
            mutated_label=1 - original[record_id],
        )
        for record_id in sorted(selected)
    )
    mutation_map_sha256 = canonical_sha256(
        {
            "schema_version": MUTATION_SCHEMA_VERSION,
            "entries": [entry.model_dump(mode="json") for entry in entries],
        }
    )
    return ConfirmatoryMutation(
        spec=spec,
        record_ids=source.record_ids,
        mutated_targets=mutated,
        entries=entries,
        source_class_count=len(eligible),
        mutation_count=count,
        achieved_conditional_rate=count / len(eligible),
        source_sha256=source.artifact_sha256(),
        source_targets_sha256=source.targets_sha256(),
        mutated_targets_sha256=_labelled_sha256(source.record_ids, mutated),
        mutation_map_sha256=mutation_map_sha256,
    )


class TargetControlResult(_StrictFrozenModel):
    control_id: Literal["serialization_roundtrip", "symmetric_matched_count", "label_repair"]
    record_ids: tuple[str, ...]
    targets: tuple[int, ...]
    changed_record_count: int = Field(ge=0)
    source_targets_sha256: Sha256
    control_targets_sha256: Sha256
    pass_target_gate: bool


def serialization_roundtrip(source: ConfirmatoryTrainingSource) -> TargetControlResult:
    """Round-trip targets through canonical JSON-compatible pairs."""

    source = _revalidated(source)
    payload: list[dict[str, str | int]] = [
        {"record_id": record_id, "target": target}
        for record_id, target in zip(source.record_ids, source.clean_targets, strict=True)
    ]
    restored_ids = tuple(str(item["record_id"]) for item in payload)
    restored_targets = tuple(int(item["target"]) for item in payload)
    source_hash = source.targets_sha256()
    restored_hash = _labelled_sha256(restored_ids, restored_targets)
    return TargetControlResult(
        control_id="serialization_roundtrip",
        record_ids=restored_ids,
        targets=restored_targets,
        changed_record_count=0,
        source_targets_sha256=source_hash,
        control_targets_sha256=restored_hash,
        pass_target_gate=(restored_ids == source.record_ids and restored_hash == source_hash),
    )


def repair_labels(
    *, source: ConfirmatoryTrainingSource, mutation: ConfirmatoryMutation
) -> TargetControlResult:
    source = _revalidated(source)
    mutation = _revalidated(mutation)
    if mutation.source_sha256 != source.artifact_sha256():
        _fail("the mutation cannot be repaired against another source")
    repaired = list(mutation.mutated_targets)
    position = {record_id: index for index, record_id in enumerate(source.record_ids)}
    for entry in mutation.entries:
        repaired[position[entry.record_id]] = entry.original_label
    repaired_targets = tuple(repaired)
    source_hash = source.targets_sha256()
    repaired_hash = _labelled_sha256(source.record_ids, repaired_targets)
    return TargetControlResult(
        control_id="label_repair",
        record_ids=source.record_ids,
        targets=repaired_targets,
        changed_record_count=mutation.mutation_count,
        source_targets_sha256=source_hash,
        control_targets_sha256=repaired_hash,
        pass_target_gate=(
            repaired_targets == source.clean_targets and repaired_hash == source_hash
        ),
    )


def symmetric_matched_count(
    *, source: ConfirmatoryTrainingSource, mutation: ConfirmatoryMutation
) -> TargetControlResult:
    """Flip the same total count across the full training population."""

    source = _revalidated(source)
    mutation = _revalidated(mutation)
    if mutation.source_sha256 != source.artifact_sha256():
        _fail("the symmetric control is not bound to this source")
    ranked = sorted(
        source.record_ids,
        key=lambda record_id: (
            _rank(
                namespace="symmetric-matched-count", seed=mutation.spec.seed, record_id=record_id
            ),
            record_id,
        ),
    )
    selected = frozenset(ranked[: mutation.mutation_count])
    targets = tuple(
        1 - value if record_id in selected else value
        for record_id, value in zip(source.record_ids, source.clean_targets, strict=True)
    )
    if set(targets) != BINARY_LABELS:
        _fail("the symmetric matched-count control erased a training class")
    source_hash = source.targets_sha256()
    control_hash = _labelled_sha256(source.record_ids, targets)
    return TargetControlResult(
        control_id="symmetric_matched_count",
        record_ids=source.record_ids,
        targets=targets,
        changed_record_count=mutation.mutation_count,
        source_targets_sha256=source_hash,
        control_targets_sha256=control_hash,
        pass_target_gate=(
            mutation.mutation_count > 0
            and mutation.mutation_count
            == sum(
                first != second for first, second in zip(source.clean_targets, targets, strict=True)
            )
            and control_hash != source_hash
        ),
    )


class ProbabilityVector(_StrictFrozenModel):
    role: PredictionRole
    record_ids: tuple[str, ...]
    positive_probabilities: tuple[float, ...]
    model_artifact_sha256: Sha256
    training_targets_sha256: Sha256
    evaluation_feature_matrix_sha256: Sha256
    split_manifest_sha256: Sha256
    protocol_sha256: Sha256

    @model_validator(mode="after")
    def _vector_is_aligned(self) -> ProbabilityVector:
        _validate_record_ids(self.record_ids)
        if len(self.record_ids) != len(self.positive_probabilities):
            raise ValueError("probabilities must align one-to-one with record identifiers")
        for value in self.positive_probabilities:
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError("probabilities must be finite and lie in [0, 1]")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class ProbabilityMetrics(_StrictFrozenModel):
    schema_version: Literal["p2-confirmatory-probability-metrics/1"] = METRIC_SCHEMA_VERSION
    vector_sha256: Sha256
    record_count: int = Field(ge=2)
    log_loss: float = Field(ge=0.0)
    brier_score: float = Field(ge=0.0, le=1.0)
    roc_auc: float = Field(ge=0.0, le=1.0)
    balanced_accuracy: float = Field(ge=0.0, le=1.0)
    accuracy: float = Field(ge=0.0, le=1.0)
    positive_recall: float = Field(ge=0.0, le=1.0)
    negative_recall: float = Field(ge=0.0, le=1.0)
    per_record_log_loss: tuple[float, ...]
    per_record_brier: tuple[float, ...]

    @field_validator("log_loss", "brier_score", "roc_auc", "balanced_accuracy", "accuracy")
    @classmethod
    def _aggregate_is_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("probability metrics must be finite")
        return value

    @model_validator(mode="after")
    def _per_record_values_reconcile(self) -> ProbabilityMetrics:
        if len(self.per_record_log_loss) != self.record_count:
            raise ValueError("per-record log losses must reconcile to the record count")
        if len(self.per_record_brier) != self.record_count:
            raise ValueError("per-record Brier losses must reconcile to the record count")
        if not math.isclose(
            self.log_loss,
            math.fsum(self.per_record_log_loss) / self.record_count,
            abs_tol=FLOAT_TOLERANCE,
        ):
            raise ValueError("aggregate log loss must be derived from per-record losses")
        if not math.isclose(
            self.brier_score,
            math.fsum(self.per_record_brier) / self.record_count,
            abs_tol=FLOAT_TOLERANCE,
        ):
            raise ValueError("aggregate Brier score must be derived from per-record losses")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def score_probabilities(
    *, true_labels: Sequence[int], vector: ProbabilityVector
) -> ProbabilityMetrics:
    """Compute every endpoint from clean labels and a probability vector."""

    vector = _revalidated(vector)
    _validate_binary(true_labels, label="clean evaluation labels")
    if len(true_labels) != len(vector.record_ids):
        _fail("clean labels and probabilities must align")
    if set(true_labels) != BINARY_LABELS:
        _fail("probability scoring requires both clean evaluation classes")
    probabilities = np.asarray(vector.positive_probabilities, dtype=float)
    truth = np.asarray(true_labels, dtype=int)
    clipped = np.clip(probabilities, PROBABILITY_CLIP, 1.0 - PROBABILITY_CLIP)
    losses = -(truth * np.log(clipped) + (1 - truth) * np.log(1.0 - clipped))
    brier = np.square(probabilities - truth)
    predicted = (probabilities >= 0.5).astype(int)
    positives = truth == 1
    negatives = truth == 0
    positive_recall = float(np.mean(predicted[positives] == 1))
    negative_recall = float(np.mean(predicted[negatives] == 0))
    return ProbabilityMetrics(
        vector_sha256=vector.canonical_sha256(),
        record_count=len(true_labels),
        log_loss=float(np.mean(losses)),
        brier_score=float(np.mean(brier)),
        roc_auc=float(roc_auc_score(truth, probabilities)),
        balanced_accuracy=(positive_recall + negative_recall) / 2.0,
        accuracy=float(np.mean(predicted == truth)),
        positive_recall=positive_recall,
        negative_recall=negative_recall,
        per_record_log_loss=tuple(float(value) for value in losses),
        per_record_brier=tuple(float(value) for value in brier),
    )


class ControlGate(_StrictFrozenModel):
    control_id: Literal[
        "clean_reference", "serialization_roundtrip", "symmetric_matched_count", "label_repair"
    ]
    passed: bool
    detail_sha256: Sha256


def validate_control_predictions(
    *,
    clean: ProbabilityVector,
    serialization: ProbabilityVector,
    symmetric: ProbabilityVector,
    repair: ProbabilityVector,
    target_controls: Mapping[str, TargetControlResult],
) -> tuple[ControlGate, ...]:
    """Require no-op/repair equivalence while retaining the sensitivity control."""

    vectors = tuple(map(_revalidated, (clean, serialization, symmetric, repair)))
    clean, serialization, symmetric, repair = vectors
    if tuple(vector.role for vector in vectors) != REQUIRED_CONTROL_IDS:
        _fail("control prediction roles must be complete and canonically ordered")
    common_ids = clean.record_ids
    common_protocol = clean.protocol_sha256
    common_split = clean.split_manifest_sha256
    common_evaluation_features = clean.evaluation_feature_matrix_sha256
    if any(vector.record_ids != common_ids for vector in vectors):
        _fail("all controls must score the identical evaluation records")
    if any(vector.protocol_sha256 != common_protocol for vector in vectors):
        _fail("all controls must use the same frozen protocol")
    if any(vector.split_manifest_sha256 != common_split for vector in vectors):
        _fail("all controls must use the same frozen split")
    if any(
        vector.evaluation_feature_matrix_sha256 != common_evaluation_features for vector in vectors
    ):
        _fail("all controls must use the same evaluation feature matrix")
    expected_target_controls = {
        "serialization_roundtrip",
        "symmetric_matched_count",
        "label_repair",
    }
    if set(target_controls) != expected_target_controls:
        _fail("target-control evidence is incomplete")
    validated_targets = {name: _revalidated(result) for name, result in target_controls.items()}
    source_target_hashes = {result.source_targets_sha256 for result in validated_targets.values()}
    if len(source_target_hashes) != 1 or clean.training_targets_sha256 not in source_target_hashes:
        _fail("control predictions are not bound to one clean training-target artifact")
    serialization_equal = (
        validated_targets["serialization_roundtrip"].pass_target_gate
        and serialization.training_targets_sha256
        == validated_targets["serialization_roundtrip"].control_targets_sha256
        and serialization.positive_probabilities == clean.positive_probabilities
        and serialization.model_artifact_sha256 == clean.model_artifact_sha256
    )
    repair_equal = (
        validated_targets["label_repair"].pass_target_gate
        and repair.training_targets_sha256
        == validated_targets["label_repair"].control_targets_sha256
        and repair.positive_probabilities == clean.positive_probabilities
        and repair.model_artifact_sha256 == clean.model_artifact_sha256
    )
    symmetric_valid = (
        validated_targets["symmetric_matched_count"].pass_target_gate
        and symmetric.training_targets_sha256
        == validated_targets["symmetric_matched_count"].control_targets_sha256
    )
    return (
        ControlGate(
            control_id="clean_reference",
            passed=True,
            detail_sha256=clean.canonical_sha256(),
        ),
        ControlGate(
            control_id="serialization_roundtrip",
            passed=serialization_equal,
            detail_sha256=serialization.canonical_sha256(),
        ),
        ControlGate(
            control_id="symmetric_matched_count",
            passed=symmetric_valid,
            detail_sha256=symmetric.canonical_sha256(),
        ),
        ControlGate(
            control_id="label_repair",
            passed=repair_equal,
            detail_sha256=repair.canonical_sha256(),
        ),
    )


def validate_complete_target_controls(
    controls: Mapping[str, TargetControlResult],
) -> None:
    """Fail closed before model execution when any target control is invalid."""

    expected = {"serialization_roundtrip", "symmetric_matched_count", "label_repair"}
    if set(controls) != expected:
        _fail("target controls are incomplete")
    for name, result in controls.items():
        result = _revalidated(result)
        if result.control_id != name or not result.pass_target_gate:
            _fail(f"target control {name!r} failed")
