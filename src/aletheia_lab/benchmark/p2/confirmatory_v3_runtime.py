"""Deterministic, outcome-agnostic runtime primitives for the v3 study.

The module implements only operations shared by the immutable v3.1 protocol
and its prospectively registered v3.2 technical recovery:
group-aware partition reconstruction, train-only preprocessing, directional
target corruption, prior-matched controls, controlled prior environments,
registered models, and development-only calibration.  Importing this module
cannot load a dataset or open a sealed partition.
"""

from __future__ import annotations

import hashlib
import math
import warnings
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from numbers import Integral, Real
from typing import TYPE_CHECKING, Annotated, Final, Literal, NoReturn, TypeAlias, TypeVar, cast

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.special import expit, logit  # type: ignore[import-untyped]
from sklearn.ensemble import HistGradientBoostingClassifier  # type: ignore[import-untyped]
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import V3DatasetBinding
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    PARTITIONS,
    DatasetSplitReceipt,
    V3ConfirmatoryProtocol,
    V3ProtocolError,
    compile_dataset_split_receipt,
    reciprocal_pair_count,
    target_environment_class_counts,
)
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN

if TYPE_CHECKING:
    from aletheia_lab.benchmark.p2.confirmatory_v3_2_protocol import (
        V32ConfirmatoryProtocol,
    )
    from aletheia_lab.benchmark.p2.confirmatory_v3_3_protocol import (
        V33ConfirmatoryProtocol,
    )

    RegisteredV3Protocol: TypeAlias = (
        V3ConfirmatoryProtocol | V32ConfirmatoryProtocol | V33ConfirmatoryProtocol
    )
else:
    RegisteredV3Protocol: TypeAlias = V3ConfirmatoryProtocol

RUNTIME_SCHEMA_VERSION: Final[Literal["p2-label-noise-shift-runtime/1"]] = (
    "p2-label-noise-shift-runtime/1"
)
PREPROCESSOR_SCHEMA_VERSION: Final[Literal["p2-v3-preprocessor/1"]] = (
    "p2-v3-preprocessor/1"
)
MUTATION_SCHEMA_VERSION: Final[Literal["p2-v3-directional-corruption/1"]] = (
    "p2-v3-directional-corruption/1"
)
ENVIRONMENT_SCHEMA_VERSION: Final[Literal["p2-v3-prior-environment/1"]] = (
    "p2-v3-prior-environment/1"
)
CALIBRATION_SCHEMA_VERSION: Final[Literal["p2-v3-logit-calibration/2"]] = (
    "p2-v3-logit-calibration/2"
)
CALIBRATION_ABSTENTION_SCHEMA_VERSION: Final[
    Literal["p2-v3-logit-calibration-abstention/1"]
] = "p2-v3-logit-calibration-abstention/1"
MODEL_SCHEMA_VERSION: Final[Literal["p2-v3-fitted-probabilities/1"]] = (
    "p2-v3-fitted-probabilities/1"
)
MODEL_ABSTENTION_SCHEMA_VERSION: Final[
    Literal["p2-v3-model-calibration-abstention/1"]
] = "p2-v3-model-calibration-abstention/1"

PROTOCOL_SHA256: Final[str] = (
    "0e9c594a6453dc111def3208582cec85d13518d542a61d86197620f9707ab7b2"
)
V3_2_PROTOCOL_SHA256: Final[str] = (
    "7cba25f08f4e27007bf17fc837b9f11137123f2f83452378c8ac3db5de3ffe27"
)
V3_3_PROTOCOL_SHA256: Final[str] = (
    "5fa057e17203b00fa78fc86d58ce5b324bb30cebf916ebb94a3d6b389f30b456"
)
FLOAT_TOLERANCE: Final[float] = 1e-12
HASH_BITS: Final[int] = 256
HASH_MODULUS: Final[int] = 1 << HASH_BITS
CALIBRATION_MIN_STEP: Final[float] = 2.0**-20
NUMERIC_EVIDENCE_SIGNIFICANT_DIGITS: Final[int] = 12
CONVERGED_RESIDUAL_DECIMAL_PLACES: Final[int] = 12

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
Direction = Literal["yes_to_no", "no_to_yes"]
Partition = Literal["train", "development", "sealed_test"]
ModelKind = Literal["logistic_regression", "hist_gradient_boosting"]
_ValueT = TypeVar("_ValueT")


class V3RuntimeError(V3ProtocolError):
    """Raised when a runtime artifact cannot satisfy the registered contract."""


def _fail(message: str) -> NoReturn:
    raise V3RuntimeError(message)


def validate_registered_protocol(protocol: RegisteredV3Protocol) -> RegisteredV3Protocol:
    """Validate one of the immutable protocol identities accepted by the runtime."""

    digest = protocol.canonical_sha256()
    if digest == PROTOCOL_SHA256:
        return cast(
            RegisteredV3Protocol,
            V3ConfirmatoryProtocol.model_validate(protocol.model_dump()),
        )
    if digest == V3_2_PROTOCOL_SHA256:
        # Imported lazily because the recovery protocol verifies the v3.1 runtime chain.
        from aletheia_lab.benchmark.p2.confirmatory_v3_2_protocol import (
            V32ConfirmatoryProtocol,
        )

        checked = V32ConfirmatoryProtocol.model_validate(protocol.model_dump())
        if checked.canonical_sha256() != V3_2_PROTOCOL_SHA256:
            _fail("v3.2 runtime protocol identity changed during validation")
        return cast(RegisteredV3Protocol, checked)
    if digest == V3_3_PROTOCOL_SHA256:
        # Imported lazily because the v3.3 protocol verifies the predecessor chain.
        from aletheia_lab.benchmark.p2.confirmatory_v3_3_protocol import (
            V33ConfirmatoryProtocol,
        )

        checked_v33 = V33ConfirmatoryProtocol.model_validate(protocol.model_dump())
        if checked_v33.canonical_sha256() != V3_3_PROTOCOL_SHA256:
            _fail("v3.3 runtime protocol identity changed during validation")
        return cast(RegisteredV3Protocol, checked_v33)
    _fail("runtime protocol is not an immutable registered v3 identity")


def stabilize_numeric_evidence(value: float) -> float:
    """Return a platform-stable float for persisted numerical evidence.

    Registered solvers operate in float64, but LAPACK and libm may disagree in
    the last few binary digits across operating systems. Persisting those
    unobservable backend differences would make evidence hashes depend on the
    runner instead of the registered calculation. Twelve significant
    decimal digits retain substantially more precision than the study's 1e-8
    numerical tolerances while removing that non-scientific variation.
    """

    number = float(value)
    if not math.isfinite(number):
        _fail("numerical evidence must be finite before stabilization")
    stabilized = float(f"{number:.{NUMERIC_EVIDENCE_SIGNIFICANT_DIGITS}g}")
    return 0.0 if stabilized == 0.0 else stabilized


def stabilize_converged_residual(value: float) -> float:
    """Quantize a converged residual at a resolution below protocol tolerance."""

    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        _fail("converged residual evidence must be finite and non-negative")
    stabilized = round(number, CONVERGED_RESIDUAL_DECIMAL_PLACES)
    return 0.0 if stabilized == 0.0 else stabilized


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _scalar_token(value: object) -> str:
    if isinstance(value, (bool, np.bool_)):
        return "true" if bool(value) else "false"
    if isinstance(value, (Integral, np.integer)):
        return str(int(value))
    if isinstance(value, (Real, np.floating)):
        number = float(value)
        if not math.isfinite(number):
            _fail("runtime inputs must be finite")
        if number.is_integer():
            return str(int(number))
        return format(number, ".17g")
    token = str(value).strip()
    if not token:
        _fail("runtime categorical inputs must not be blank")
    return token


def _target_value(dataset: V3DatasetBinding, value: object) -> int:
    token = _scalar_token(value).lower()
    if token == dataset.target.negative_token:
        return 0
    if token == dataset.target.positive_token:
        return 1
    _fail("runtime target token differs from the registered encoding")


def _record_id(dataset: V3DatasetBinding, frame: pd.DataFrame, index: int) -> str:
    if dataset.identifier_columns:
        return "|".join(
            _scalar_token(frame.iloc[index][column]) for column in dataset.identifier_columns
        )
    return f"{dataset.dataset_id}-row-{index:05d}"


def _largest_remainder(total: int, fractions: Sequence[Fraction]) -> tuple[int, ...]:
    exact = tuple(Fraction(total) * fraction for fraction in fractions)
    counts = [value.numerator // value.denominator for value in exact]
    order = sorted(
        range(len(exact)),
        key=lambda index: (-(exact[index] - counts[index]), index),
    )
    for index in order[: total - sum(counts)]:
        counts[index] += 1
    return tuple(counts)


class RuntimeSplit(_StrictFrozenModel):
    """Complete row-level reconstruction of a registered split receipt."""

    schema_version: Literal["p2-label-noise-shift-runtime/1"] = RUNTIME_SCHEMA_VERSION
    protocol_sha256: Sha256
    dataset_id: str
    record_ids: tuple[str, ...]
    labels: tuple[int, ...]
    partitions: tuple[Partition, ...]
    membership_sha256: Sha256
    group_assignment_sha256: Sha256
    sealed_membership_sha256: Sha256

    @model_validator(mode="after")
    def _rows_are_complete(self) -> RuntimeSplit:
        count = len(self.record_ids)
        if count == 0 or len(set(self.record_ids)) != count:
            raise ValueError("runtime split requires unique records")
        if len(self.labels) != count or len(self.partitions) != count:
            raise ValueError("runtime split rows, labels, and partitions must align")
        if set(self.labels) != {0, 1} or set(self.partitions) != set(PARTITIONS):
            raise ValueError("runtime split requires both classes and all partitions")
        return self

    def indices(self, partition: Partition) -> tuple[int, ...]:
        return tuple(index for index, value in enumerate(self.partitions) if value == partition)

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def reconstruct_runtime_split(
    *,
    protocol: RegisteredV3Protocol,
    dataset: V3DatasetBinding,
    frame: pd.DataFrame,
    receipt: DatasetSplitReceipt,
) -> RuntimeSplit:
    """Reconstruct exact membership and reconcile it to the registered receipt."""

    protocol = validate_registered_protocol(protocol)
    dataset = V3DatasetBinding.model_validate(dataset.model_dump())
    registered = {
        (item.dataset_id, item.role): item for item in protocol.dataset_splits
    }
    if registered.get((dataset.dataset_id, dataset.role)) != receipt:
        _fail("runtime dataset and split receipt are outside the registered census")
    if len(frame) != dataset.expected_row_count:
        _fail("runtime frame row count differs from its binding")
    labels_token = tuple(
        _scalar_token(value).lower() for value in frame[dataset.target.column].tolist()
    )
    labels = tuple(_target_value(dataset, value) for value in frame[dataset.target.column])
    record_ids = tuple(_record_id(dataset, frame, index) for index in range(len(frame)))
    if len(set(record_ids)) != len(record_ids):
        _fail("runtime record identities are not unique")

    groups: dict[str, list[int]] = defaultdict(list)
    feature_columns = list(dataset.analysis_features)
    for position, row in enumerate(frame.loc[:, feature_columns].itertuples(index=False, name=None)):
        values = tuple(_scalar_token(value) for value in row)
        groups[canonical_sha256({"analysis_features": values})].append(position)

    allowed = (dataset.target.negative_token, dataset.target.positive_token)
    fractions = (Fraction(3, 5), Fraction(1, 5), Fraction(1, 5))
    class_targets = {
        label: _largest_remainder(labels_token.count(label), fractions) for label in allowed
    }
    total_targets = _largest_remainder(len(frame), fractions)
    assigned_total = [0, 0, 0]
    assigned_class = {label: [0, 0, 0] for label in allowed}
    assignments: dict[str, int] = {}

    def group_order(item: tuple[str, list[int]]) -> tuple[int, int, str]:
        group_hash, indices = item
        counts = tuple(
            sum(labels_token[index] == label for index in indices) for label in allowed
        )
        tie = hashlib.sha256(f"{dataset.split_seed}:{group_hash}".encode()).hexdigest()
        return (-len(indices), -max(counts), tie)

    for group_hash, indices in sorted(groups.items(), key=group_order):
        group_counts = {
            label: sum(labels_token[index] == label for index in indices) for label in allowed
        }
        candidates: list[tuple[Fraction, Fraction, int]] = []
        for partition_index in range(3):
            overflow = Fraction(
                max(
                    0,
                    assigned_total[partition_index]
                    + len(indices)
                    - total_targets[partition_index],
                ),
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

    partitions: list[Partition | None] = [None] * len(frame)
    membership: list[tuple[str, str]] = []
    group_assignment: list[tuple[str, str]] = []
    sealed_records: list[str] = []
    for group_hash, indices in sorted(groups.items()):
        partition = cast(Partition, PARTITIONS[assignments[group_hash]])
        group_assignment.append((group_hash, partition))
        for index in indices:
            partitions[index] = partition
            membership.append((record_ids[index], partition))
            if partition == "sealed_test":
                sealed_records.append(record_ids[index])
    if any(value is None for value in partitions):
        _fail("runtime split failed to assign every row")
    membership.sort()
    sealed_records.sort()
    membership_sha256 = canonical_sha256({"membership": membership})
    group_sha256 = canonical_sha256({"groups": group_assignment})
    sealed_sha256 = canonical_sha256({"record_ids": sealed_records})

    compiled = compile_dataset_split_receipt(
        dataset=dataset,
        frame=frame,
        target_binding_sha256=receipt.target_binding_sha256,
        record_identity_sha256=receipt.record_identity_sha256,
    )
    if compiled != receipt or (
        membership_sha256 != receipt.membership_sha256
        or group_sha256 != receipt.group_assignment_sha256
        or sealed_sha256 != receipt.sealed_membership_sha256
    ):
        _fail("runtime split does not reproduce the registered receipt")
    return RuntimeSplit(
        protocol_sha256=protocol.canonical_sha256(),
        dataset_id=dataset.dataset_id,
        record_ids=record_ids,
        labels=labels,
        partitions=tuple(partitions),  # type: ignore[arg-type]
        membership_sha256=membership_sha256,
        group_assignment_sha256=group_sha256,
        sealed_membership_sha256=sealed_sha256,
    )


def _categorical_token(dataset_id: str, column: str, value: object) -> str:
    token = _scalar_token(value).lower()
    if dataset_id == "uci_default_of_credit_card_clients":
        if column == "EDUCATION" and token in {"0", "5", "6"}:
            return "other"
        if column == "MARRIAGE" and token == "0":
            return "other"
    return token


class PreprocessorState(_StrictFrozenModel):
    schema_version: Literal["p2-v3-preprocessor/1"] = PREPROCESSOR_SCHEMA_VERSION
    dataset_id: str
    categorical_columns: tuple[str, ...]
    numeric_columns: tuple[str, ...]
    category_vocabulary: dict[str, tuple[str, ...]]
    numeric_means: tuple[float, ...]
    numeric_scales: tuple[float, ...]
    output_columns: tuple[str, ...]
    output_columns_sha256: Sha256

    @model_validator(mode="after")
    def _state_is_canonical(self) -> PreprocessorState:
        if tuple(self.category_vocabulary) != self.categorical_columns:
            raise ValueError("category vocabulary must follow manifest order")
        if len(self.numeric_means) != len(self.numeric_columns) or len(
            self.numeric_scales
        ) != len(self.numeric_columns):
            raise ValueError("numeric statistics must match numeric columns")
        if any(not math.isfinite(value) for value in self.numeric_means):
            raise ValueError("numeric means must be finite")
        if any(not math.isfinite(value) or value <= 0.0 for value in self.numeric_scales):
            raise ValueError("numeric scales must be finite and positive")
        if self.output_columns_sha256 != canonical_sha256(
            {"schema_version": PREPROCESSOR_SCHEMA_VERSION, "columns": self.output_columns}
        ):
            raise ValueError("preprocessor output-column hash is inconsistent")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def fit_preprocessor(dataset: V3DatasetBinding, training_frame: pd.DataFrame) -> PreprocessorState:
    """Fit the registered train-only vocabulary and float64 numeric statistics."""

    dataset = V3DatasetBinding.model_validate(dataset.model_dump())
    expected = dataset.analysis_features
    if tuple(str(column) for column in training_frame.columns) != expected:
        _fail("preprocessor frame must contain analysis features in manifest order")
    if training_frame.empty or training_frame.isna().to_numpy().any():
        _fail("preprocessor cannot accept empty or missing training data")
    vocabulary: dict[str, tuple[str, ...]] = {}
    output: list[str] = []
    for column in dataset.categorical_features:
        values = tuple(
            sorted(
                {
                    _categorical_token(dataset.dataset_id, column, value)
                    for value in training_frame[column]
                }
            )
        )
        if not values:
            _fail("categorical training vocabulary cannot be empty")
        vocabulary[column] = values
        output.extend(f"{column}={token}" for token in values)
    means: list[float] = []
    scales: list[float] = []
    for column in dataset.numeric_features:
        try:
            numeric_values = np.asarray(training_frame[column], dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise V3RuntimeError("numeric training feature cannot be converted to float64") from exc
        if numeric_values.ndim != 1 or not np.isfinite(numeric_values).all():
            _fail("numeric training features must be finite vectors")
        mean = float(np.mean(numeric_values, dtype=np.float64))
        scale = float(np.std(numeric_values, ddof=0, dtype=np.float64))
        means.append(mean)
        scales.append(scale if scale > 0.0 else 1.0)
    output.extend(dataset.numeric_features)
    output_columns = tuple(output)
    return PreprocessorState(
        dataset_id=dataset.dataset_id,
        categorical_columns=dataset.categorical_features,
        numeric_columns=dataset.numeric_features,
        category_vocabulary=vocabulary,
        numeric_means=tuple(means),
        numeric_scales=tuple(scales),
        output_columns=output_columns,
        output_columns_sha256=canonical_sha256(
            {"schema_version": PREPROCESSOR_SCHEMA_VERSION, "columns": output_columns}
        ),
    )


def transform_features(
    *, dataset: V3DatasetBinding, state: PreprocessorState, frame: pd.DataFrame
) -> np.ndarray:
    """Apply a fitted state without learning from development or sealed rows."""

    dataset = V3DatasetBinding.model_validate(dataset.model_dump())
    state = PreprocessorState.model_validate(state.model_dump())
    if state.dataset_id != dataset.dataset_id or tuple(frame.columns) != dataset.analysis_features:
        _fail("feature transform does not match the registered dataset schema")
    if frame.empty or frame.isna().to_numpy().any():
        _fail("feature transform cannot accept empty or missing data")
    blocks: list[np.ndarray] = []
    for column in state.categorical_columns:
        values = tuple(
            _categorical_token(dataset.dataset_id, column, value) for value in frame[column]
        )
        vocabulary = state.category_vocabulary[column]
        block = np.zeros((len(frame), len(vocabulary)), dtype=np.float64)
        index = {token: position for position, token in enumerate(vocabulary)}
        for row, token in enumerate(values):
            position = index.get(token)
            if position is not None:
                block[row, position] = 1.0
        blocks.append(block)
    if state.numeric_columns:
        try:
            numeric = frame.loc[:, list(state.numeric_columns)].to_numpy(dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise V3RuntimeError("numeric feature transform failed") from exc
        if not np.isfinite(numeric).all():
            _fail("numeric feature transform produced non-finite values")
        means = np.asarray(state.numeric_means, dtype=np.float64)
        scales = np.asarray(state.numeric_scales, dtype=np.float64)
        blocks.append((numeric - means) / scales)
    if not blocks:
        _fail("registered feature transform produced no columns")
    matrix = np.concatenate(blocks, axis=1)
    if matrix.shape != (len(frame), len(state.output_columns)) or not np.isfinite(matrix).all():
        _fail("feature transform produced an invalid matrix")
    return matrix


def transformed_matrix_sha256(
    matrix: np.ndarray, *, record_ids: Sequence[str], output_columns: Sequence[str]
) -> str:
    values = np.asarray(matrix, dtype=np.float64)
    if values.ndim != 2 or values.shape != (len(record_ids), len(output_columns)):
        _fail("transformed matrix, rows, and output columns must align")
    if not np.isfinite(values).all():
        _fail("transformed matrix must be finite")
    return canonical_sha256(
        {
            "schema_version": "p2-v3-transformed-matrix/1",
            "record_ids": tuple(record_ids),
            "output_columns": tuple(output_columns),
            "dtype": "float64",
            "shape": values.shape,
            "values": tuple(tuple(float(value) for value in row) for row in values),
        }
    )


def _domain_digest(namespace: str, fields: Mapping[str, object]) -> str:
    return canonical_sha256(
        {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "domain": f"aletheia-lab/v3.1/{namespace}",
            "fields": dict(fields),
        }
    )


class DirectionalMutation(_StrictFrozenModel):
    schema_version: Literal["p2-v3-directional-corruption/1"] = MUTATION_SCHEMA_VERSION
    dataset_id: str
    direction: Direction
    conditional_rate: float
    seed: int
    source_label: Literal[0, 1]
    destination_label: Literal[0, 1]
    source_class_count: int
    mutation_count: int
    selected_record_ids: tuple[str, ...]
    clean_positive_count: int
    mutated_positive_count: int
    achieved_conditional_rate: float
    clean_targets_sha256: Sha256
    mutated_targets_sha256: Sha256

    @model_validator(mode="after")
    def _mutation_reconciles(self) -> DirectionalMutation:
        expected = math.floor(self.conditional_rate * self.source_class_count)
        if self.mutation_count != expected or len(self.selected_record_ids) != expected:
            raise ValueError("directional mutation count does not follow floor(rate * source)")
        if len(set(self.selected_record_ids)) != expected:
            raise ValueError("directional mutation selected a duplicate record")
        delta = self.mutation_count if self.destination_label == 1 else -self.mutation_count
        if self.mutated_positive_count != self.clean_positive_count + delta:
            raise ValueError("directional mutation prevalence does not reconcile")
        if not math.isclose(
            self.achieved_conditional_rate,
            self.mutation_count / self.source_class_count,
            abs_tol=FLOAT_TOLERANCE,
        ):
            raise ValueError("achieved mutation rate is inconsistent")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def labelled_targets_sha256(record_ids: Sequence[str], targets: Sequence[int]) -> str:
    if len(record_ids) != len(targets) or len(set(record_ids)) != len(record_ids):
        _fail("labelled target rows must align and be unique")
    if any(value not in (0, 1) for value in targets):
        _fail("labelled targets must be binary")
    rows = sorted(zip(record_ids, targets, strict=True))
    return canonical_sha256(
        {
            "schema_version": "p2-v3-labelled-targets/1",
            "records": tuple({"record_id": rid, "target": value} for rid, value in rows),
        }
    )


def apply_directional_corruption(
    *,
    dataset_id: str,
    record_ids: Sequence[str],
    clean_targets: Sequence[int],
    direction: Direction,
    conditional_rate: float,
    seed: int,
) -> tuple[tuple[int, ...], DirectionalMutation]:
    """Select source-class records by a domain-separated SHA-256 rank."""

    ids = tuple(record_ids)
    targets = tuple(int(value) for value in clean_targets)
    if len(ids) != len(targets) or len(set(ids)) != len(ids) or set(targets) != {0, 1}:
        _fail("directional corruption requires aligned unique binary training rows")
    if not 0.0 < conditional_rate < 1.0:
        _fail("directional corruption rate must lie strictly in (0, 1)")
    source: Literal[0, 1]
    destination: Literal[0, 1]
    source, destination = (1, 0) if direction == "yes_to_no" else (0, 1)
    eligible = [record_id for record_id, value in zip(ids, targets, strict=True) if value == source]
    count = math.floor(conditional_rate * len(eligible))
    if count <= 0:
        _fail("directional corruption must mutate at least one record")
    ranked = sorted(
        eligible,
        key=lambda record_id: (
            _domain_digest(
                "directional-corruption-rank",
                {
                    "dataset_id": dataset_id,
                    "direction": direction,
                    "seed": seed,
                    "record_id": record_id,
                },
            ),
            record_id,
        ),
    )
    selected = tuple(ranked[:count])
    selected_set = set(selected)
    mutated = tuple(
        destination if record_id in selected_set else value
        for record_id, value in zip(ids, targets, strict=True)
    )
    mutation = DirectionalMutation(
        dataset_id=dataset_id,
        direction=direction,
        conditional_rate=conditional_rate,
        seed=seed,
        source_label=source,
        destination_label=destination,
        source_class_count=len(eligible),
        mutation_count=count,
        selected_record_ids=selected,
        clean_positive_count=sum(targets),
        mutated_positive_count=sum(mutated),
        achieved_conditional_rate=count / len(eligible),
        clean_targets_sha256=labelled_targets_sha256(ids, targets),
        mutated_targets_sha256=labelled_targets_sha256(ids, mutated),
    )
    return mutated, mutation


def prior_match_sample_weights(
    clean_targets: Sequence[int], *, target_positive_prior: float
) -> tuple[float, ...]:
    """Weight clean labels to an exact effective target prior and mean weight one."""

    targets = np.asarray(tuple(clean_targets), dtype=np.int64)
    if targets.ndim != 1 or targets.size < 2 or set(targets.tolist()) != {0, 1}:
        _fail("prior matching requires a nonempty binary target vector")
    if not 0.0 < target_positive_prior < 1.0:
        _fail("prior matching target must lie strictly in (0, 1)")
    source_positive = float(np.mean(targets))
    positive_weight = target_positive_prior / source_positive
    negative_weight = (1.0 - target_positive_prior) / (1.0 - source_positive)
    weights = np.where(targets == 1, positive_weight, negative_weight).astype(np.float64)
    weights /= float(np.mean(weights))
    effective = float(np.sum(weights * targets) / np.sum(weights))
    if not math.isclose(effective, target_positive_prior, abs_tol=FLOAT_TOLERANCE):
        _fail("prior-matched weights failed exact effective-prevalence reconciliation")
    return tuple(float(value) for value in weights)


def reciprocal_control_targets(
    *,
    dataset_id: str,
    record_ids: Sequence[str],
    clean_targets: Sequence[int],
    direction: Direction,
    conditional_rate: float,
    seed: int,
) -> tuple[tuple[int, ...], tuple[str, ...], tuple[str, ...]]:
    """Flip equal opposite-class counts with a registered minority-class cap."""

    ids = tuple(record_ids)
    targets = tuple(int(value) for value in clean_targets)
    if len(ids) != len(targets) or set(targets) != {0, 1}:
        _fail("reciprocal control requires aligned binary rows")
    source = 1 if direction == "yes_to_no" else 0
    opposite = 1 - source
    source_ids = [record_id for record_id, value in zip(ids, targets, strict=True) if value == source]
    opposite_ids = [
        record_id for record_id, value in zip(ids, targets, strict=True) if value == opposite
    ]
    pair_count = reciprocal_pair_count(
        source_class_count=len(source_ids),
        opposite_class_count=len(opposite_ids),
        rate=conditional_rate,
    )

    def ranked(values: Sequence[str], label: int) -> tuple[str, ...]:
        return tuple(
            sorted(
                values,
                key=lambda record_id: (
                    _domain_digest(
                        "reciprocal-control-rank",
                        {
                            "dataset_id": dataset_id,
                            "direction": direction,
                            "seed": seed,
                            "source_label": label,
                            "record_id": record_id,
                        },
                    ),
                    record_id,
                ),
            )[:pair_count]
        )

    selected_source = ranked(source_ids, source)
    selected_opposite = ranked(opposite_ids, opposite)
    selected = set((*selected_source, *selected_opposite))
    controlled = tuple(1 - value if record_id in selected else value for record_id, value in zip(ids, targets, strict=True))
    if sum(controlled) != sum(targets):
        _fail("reciprocal control must preserve class prevalence")
    return controlled, selected_source, selected_opposite


def _rejection_index(
    *, dataset_id: str, label: int, environment_seed: int, draw: int, pool_size: int
) -> int:
    if pool_size <= 0:
        _fail("prior environment cannot sample an empty class pool")
    limit = HASH_MODULUS - (HASH_MODULUS % pool_size)
    counter = 0
    while True:
        digest = _domain_digest(
            "prior-environment-counter",
            {
                "dataset_id": dataset_id,
                "label": label,
                "environment_seed": environment_seed,
                "draw": draw,
                "counter": counter,
            },
        )
        integer = int.from_bytes(bytes.fromhex(digest), byteorder="big", signed=False)
        if integer < limit:
            return integer % pool_size
        counter += 1


class PriorEnvironment(_StrictFrozenModel):
    schema_version: Literal["p2-v3-prior-environment/1"] = ENVIRONMENT_SCHEMA_VERSION
    dataset_id: str
    odds_multiplier: float
    environment_seed: int
    source_positive_prior: float
    target_negative_count: int
    target_positive_count: int
    sampled_record_ids: tuple[str, ...]
    sampled_labels: tuple[int, ...]
    neutral_identity: bool

    @model_validator(mode="after")
    def _environment_reconciles(self) -> PriorEnvironment:
        if len(self.sampled_record_ids) != len(self.sampled_labels):
            raise ValueError("prior environment records and labels must align")
        if self.sampled_labels.count(0) != self.target_negative_count or self.sampled_labels.count(
            1
        ) != self.target_positive_count:
            raise ValueError("prior environment class counts do not reconcile")
        if self.neutral_identity != math.isclose(
            self.odds_multiplier, 1.0, abs_tol=FLOAT_TOLERANCE
        ):
            raise ValueError("only odds multiplier one is the neutral identity")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def build_prior_environment(
    *,
    dataset_id: str,
    record_ids: Sequence[str],
    labels: Sequence[int],
    odds_multiplier: float,
    environment_seed: int,
) -> tuple[tuple[int, ...], PriorEnvironment]:
    """Create a deterministic prior environment, returning source-row indices."""

    ids = tuple(record_ids)
    values = tuple(int(value) for value in labels)
    if len(ids) != len(values) or len(set(ids)) != len(ids) or set(values) != {0, 1}:
        _fail("prior environment requires unique aligned binary records")
    source_positive_prior = sum(values) / len(values)
    negative_count, positive_count = target_environment_class_counts(
        total=len(values),
        source_positive_prior=source_positive_prior,
        odds_multiplier=odds_multiplier,
    )
    if math.isclose(odds_multiplier, 1.0, abs_tol=FLOAT_TOLERANCE):
        indices = tuple(range(len(ids)))
    else:
        pools = {
            label: tuple(index for index, value in enumerate(values) if value == label)
            for label in (0, 1)
        }
        sampled: list[int] = []
        for label, count in ((0, negative_count), (1, positive_count)):
            for draw in range(count):
                pool = pools[label]
                sampled.append(
                    pool[
                        _rejection_index(
                            dataset_id=dataset_id,
                            label=label,
                            environment_seed=environment_seed,
                            draw=draw,
                            pool_size=len(pool),
                        )
                    ]
                )
        indices = tuple(sampled)
    sampled_ids = tuple(ids[index] for index in indices)
    sampled_labels = tuple(values[index] for index in indices)
    environment = PriorEnvironment(
        dataset_id=dataset_id,
        odds_multiplier=odds_multiplier,
        environment_seed=environment_seed,
        source_positive_prior=source_positive_prior,
        target_negative_count=negative_count,
        target_positive_count=positive_count,
        sampled_record_ids=sampled_ids,
        sampled_labels=sampled_labels,
        neutral_identity=math.isclose(odds_multiplier, 1.0, abs_tol=FLOAT_TOLERANCE),
    )
    return indices, environment


class CalibrationResult(_StrictFrozenModel):
    schema_version: Literal["p2-v3-logit-calibration/2"] = CALIBRATION_SCHEMA_VERSION
    status: Literal["ok"] = "ok"
    intercept: float
    slope: float
    iterations: int
    converged: Literal[True]
    gradient_infinity_norm: float
    gradient_scale: Literal["mean_per_development_record"] = "mean_per_development_record"
    development_record_count: int

    @model_validator(mode="after")
    def _result_is_finite(self) -> CalibrationResult:
        if not all(
            math.isfinite(value)
            for value in (self.intercept, self.slope, self.gradient_infinity_norm)
        ):
            raise ValueError("calibration result must be finite")
        if self.iterations < 0 or self.development_record_count < 2:
            raise ValueError("calibration iteration and record counts are invalid")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


CalibrationAbstentionReason = Literal[
    "singular_hessian",
    "newton_solve_failed",
    "nonfinite_numerics",
    "line_search_failed",
    "iteration_budget_exhausted",
]


class CalibrationAbstention(_StrictFrozenModel):
    """Fail-closed calibration disposition without a usable partial fit."""

    schema_version: Literal["p2-v3-logit-calibration-abstention/1"] = (
        CALIBRATION_ABSTENTION_SCHEMA_VERSION
    )
    status: Literal["abstain"] = "abstain"
    reason_code: CalibrationAbstentionReason
    iterations: int = Field(ge=0)
    gradient_infinity_norm: float | None
    gradient_scale: Literal["mean_per_development_record"] = "mean_per_development_record"
    objective_mean: float | None
    development_record_count: int = Field(ge=2)
    exposes_partial_calibration: Literal[False] = False

    @model_validator(mode="after")
    def _diagnostics_are_finite(self) -> CalibrationAbstention:
        diagnostics = (self.gradient_infinity_norm, self.objective_mean)
        if any(value is not None and not math.isfinite(value) for value in diagnostics):
            raise ValueError("calibration abstention diagnostics must be finite when present")
        if self.gradient_infinity_norm is not None and self.gradient_infinity_norm < 0.0:
            raise ValueError("calibration abstention gradient norm must be non-negative")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class CalibrationAbstentionSignal(V3RuntimeError):
    """Internal control-flow signal carrying an auditable calibration abstention."""

    def __init__(self, abstention: CalibrationAbstention) -> None:
        self.abstention = CalibrationAbstention.model_validate(abstention.model_dump())
        super().__init__(f"calibration abstained: {self.abstention.reason_code}")


class ModelCalibrationAbstention(_StrictFrozenModel):
    """Dataset/model context for a calibration failure that blocks scoring."""

    schema_version: Literal["p2-v3-model-calibration-abstention/1"] = (
        MODEL_ABSTENTION_SCHEMA_VERSION
    )
    status: Literal["abstain"] = "abstain"
    stage: Literal["development_only_calibration"] = "development_only_calibration"
    protocol_sha256: Sha256
    dataset_id: str
    dataset_role: Literal["primary", "external_replication"]
    model_kind: ModelKind
    training_role: str
    training_targets_sha256: Sha256
    sample_weights_sha256: Sha256 | None
    calibration_abstention: CalibrationAbstention
    predictive_metrics_generated: Literal[False] = False
    partial_model_reusable: Literal[False] = False

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class ModelCalibrationAbstentionSignal(V3RuntimeError):
    """Internal signal used to make dataset execution return an abstention."""

    def __init__(self, abstention: ModelCalibrationAbstention) -> None:
        self.abstention = ModelCalibrationAbstention.model_validate(
            abstention.model_dump()
        )
        super().__init__(
            "model calibration abstained: "
            f"{self.abstention.dataset_id}/{self.abstention.training_role}/"
            f"{self.abstention.calibration_abstention.reason_code}"
        )


def _calibration_objective(design: np.ndarray, targets: np.ndarray, beta: np.ndarray) -> float:
    linear = design @ beta
    return float(np.mean(np.logaddexp(0.0, linear) - targets * linear))


def _calibration_abstention(
    *,
    reason_code: CalibrationAbstentionReason,
    iterations: int,
    gradient_norm: float | None,
    objective: float | None,
    development_record_count: int,
) -> CalibrationAbstention:
    return CalibrationAbstention(
        reason_code=reason_code,
        iterations=iterations,
        gradient_infinity_norm=(
            None
            if gradient_norm is None
            else stabilize_numeric_evidence(gradient_norm)
        ),
        objective_mean=(
            None if objective is None else stabilize_numeric_evidence(objective)
        ),
        development_record_count=development_record_count,
    )


def fit_logit_calibration_attempt(
    raw_probabilities: Sequence[float],
    targets: Sequence[int],
    *,
    probability_clip: float = 1e-15,
    max_iter: int = 100,
    tolerance: float = 1e-8,
) -> CalibrationResult | CalibrationAbstention:
    """Fit calibration or return a structured, non-predictive abstention.

    Objective, gradient, and Hessian are means over development records. The
    Newton direction is therefore identical to the corresponding summed
    equations, while the convergence tolerance no longer changes meaning when
    the development partition size changes.
    """

    probabilities = np.asarray(tuple(raw_probabilities), dtype=np.float64)
    y = np.asarray(tuple(targets), dtype=np.float64)
    if probabilities.ndim != 1 or probabilities.size < 2 or y.shape != probabilities.shape:
        _fail("calibration probabilities and targets must be aligned vectors")
    if set(y.tolist()) != {0.0, 1.0} or not np.isfinite(probabilities).all():
        _fail("calibration requires finite probabilities and both target classes")
    if np.any(probabilities < 0.0) or np.any(probabilities > 1.0):
        _fail("calibration probabilities must lie in [0, 1]")
    if (
        not 0.0 < probability_clip < 0.5
        or max_iter < 1
        or not math.isfinite(tolerance)
        or tolerance <= 0.0
    ):
        _fail("calibration numerical controls are invalid")
    clipped = np.clip(probabilities, probability_clip, 1.0 - probability_clip)
    design = np.column_stack((np.ones_like(clipped), logit(clipped))).astype(np.float64)
    if np.linalg.matrix_rank(design) < 2:
        return _calibration_abstention(
            reason_code="singular_hessian",
            iterations=0,
            gradient_norm=None,
            objective=None,
            development_record_count=probabilities.size,
        )
    beta = np.asarray((0.0, 1.0), dtype=np.float64)
    gradient_norm = math.inf
    objective: float | None = None
    for iteration in range(max_iter + 1):
        fitted = expit(design @ beta)
        gradient = (design.T @ (fitted - y)) / probabilities.size
        gradient_norm = float(np.max(np.abs(gradient)))
        objective = _calibration_objective(design, y, beta)
        if not math.isfinite(gradient_norm) or not math.isfinite(objective):
            return _calibration_abstention(
                reason_code="nonfinite_numerics",
                iterations=iteration,
                gradient_norm=None,
                objective=None,
                development_record_count=probabilities.size,
            )
        if gradient_norm <= tolerance:
            return CalibrationResult(
                intercept=stabilize_numeric_evidence(float(beta[0])),
                slope=stabilize_numeric_evidence(float(beta[1])),
                iterations=iteration,
                converged=True,
                gradient_infinity_norm=stabilize_converged_residual(gradient_norm),
                development_record_count=probabilities.size,
            )
        if iteration == max_iter:
            return _calibration_abstention(
                reason_code="iteration_budget_exhausted",
                iterations=iteration,
                gradient_norm=gradient_norm,
                objective=objective,
                development_record_count=probabilities.size,
            )
        weights = fitted * (1.0 - fitted)
        hessian = (design.T @ (weights[:, None] * design)) / probabilities.size
        if not np.isfinite(hessian).all() or np.linalg.matrix_rank(hessian) < 2:
            return _calibration_abstention(
                reason_code="singular_hessian",
                iterations=iteration,
                gradient_norm=gradient_norm,
                objective=objective,
                development_record_count=probabilities.size,
            )
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            return _calibration_abstention(
                reason_code="newton_solve_failed",
                iterations=iteration,
                gradient_norm=gradient_norm,
                objective=objective,
                development_record_count=probabilities.size,
            )
        multiplier = 1.0
        accepted = False
        while multiplier >= CALIBRATION_MIN_STEP:
            candidate = beta - multiplier * step
            candidate_objective = _calibration_objective(design, y, candidate)
            if math.isfinite(candidate_objective) and candidate_objective <= objective:
                beta = candidate
                accepted = True
                break
            multiplier /= 2.0
        if not accepted:
            return _calibration_abstention(
                reason_code="line_search_failed",
                iterations=iteration,
                gradient_norm=gradient_norm,
                objective=objective,
                development_record_count=probabilities.size,
            )
    raise AssertionError("calibration loop must return before exhaustion")


def fit_logit_calibration(
    raw_probabilities: Sequence[float],
    targets: Sequence[int],
    *,
    probability_clip: float = 1e-15,
    max_iter: int = 100,
    tolerance: float = 1e-8,
) -> CalibrationResult:
    """Fit calibration, signalling the structured abstention to strict callers."""

    attempt = fit_logit_calibration_attempt(
        raw_probabilities,
        targets,
        probability_clip=probability_clip,
        max_iter=max_iter,
        tolerance=tolerance,
    )
    if isinstance(attempt, CalibrationAbstention):
        raise CalibrationAbstentionSignal(attempt)
    return attempt


def apply_logit_calibration(
    probabilities: Sequence[float], calibration: CalibrationResult, *, clip: float = 1e-15
) -> tuple[float, ...]:
    values = np.asarray(tuple(probabilities), dtype=np.float64)
    calibration = CalibrationResult.model_validate(calibration.model_dump())
    if values.ndim != 1 or not np.isfinite(values).all():
        _fail("calibration application requires finite probabilities")
    if np.any(values < 0.0) or np.any(values > 1.0):
        _fail("calibration application probabilities must lie in [0, 1]")
    transformed = expit(
        calibration.intercept
        + calibration.slope * logit(np.clip(values, clip, 1.0 - clip))
    )
    if not np.isfinite(transformed).all() or np.any(transformed <= 0.0) or np.any(
        transformed >= 1.0
    ):
        _fail("calibration produced invalid probabilities")
    return tuple(float(value) for value in transformed)


class FittedProbabilities(_StrictFrozenModel):
    schema_version: Literal["p2-v3-fitted-probabilities/1"] = MODEL_SCHEMA_VERSION
    protocol_sha256: Sha256
    dataset_id: str
    model_kind: ModelKind
    training_role: str
    training_targets_sha256: Sha256
    sample_weights_sha256: Sha256 | None
    preprocessor_sha256: Sha256
    output_columns_sha256: Sha256
    calibration: CalibrationResult
    development_record_ids: tuple[str, ...]
    development_probabilities: tuple[float, ...]
    evaluation_record_ids: tuple[str, ...]
    evaluation_probabilities: tuple[float, ...]
    fitted_model_sha256: Sha256

    @model_validator(mode="after")
    def _vectors_are_valid(self) -> FittedProbabilities:
        pairs = (
            (self.development_record_ids, self.development_probabilities),
            (self.evaluation_record_ids, self.evaluation_probabilities),
        )
        for record_ids, probabilities in pairs:
            if not record_ids or len(record_ids) != len(probabilities):
                raise ValueError("fitted probability vectors must align")
            if any(not math.isfinite(value) or not 0.0 < value < 1.0 for value in probabilities):
                raise ValueError("fitted probabilities must be finite and strictly interior")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def _weights_sha256(weights: Sequence[float] | None) -> str | None:
    if weights is None:
        return None
    values = tuple(float(value) for value in weights)
    if not values or any(not math.isfinite(value) or value <= 0.0 for value in values):
        _fail("model sample weights must be finite and positive")
    return canonical_sha256({"schema_version": "p2-v3-sample-weights/1", "weights": values})


def fit_registered_model(
    *,
    protocol: RegisteredV3Protocol,
    dataset: V3DatasetBinding,
    model_kind: ModelKind,
    training_role: str,
    state: PreprocessorState,
    training_matrix: np.ndarray,
    training_record_ids: Sequence[str],
    training_targets: Sequence[int],
    development_matrix: np.ndarray,
    development_record_ids: Sequence[str],
    development_targets: Sequence[int],
    evaluation_matrix: np.ndarray,
    evaluation_record_ids: Sequence[str],
    sample_weights: Sequence[float] | None = None,
) -> FittedProbabilities:
    """Fit one registered model and calibrate only on development predictions."""

    protocol = validate_registered_protocol(protocol)
    dataset = V3DatasetBinding.model_validate(dataset.model_dump())
    state = PreprocessorState.model_validate(state.model_dump())
    if state.dataset_id != dataset.dataset_id:
        _fail("model runtime does not match its immutable registration")
    if (dataset.dataset_id, dataset.role) not in {
        (item.dataset_id, item.role) for item in protocol.dataset_splits
    }:
        _fail("model runtime dataset is outside the registered census")
    x_train = np.asarray(training_matrix, dtype=np.float64)
    x_development = np.asarray(development_matrix, dtype=np.float64)
    x_evaluation = np.asarray(evaluation_matrix, dtype=np.float64)
    y_train = np.asarray(tuple(training_targets), dtype=np.int64)
    y_development = np.asarray(tuple(development_targets), dtype=np.int64)
    if (
        x_train.shape != (len(training_record_ids), len(state.output_columns))
        or x_development.shape != (len(development_record_ids), len(state.output_columns))
        or x_evaluation.shape != (len(evaluation_record_ids), len(state.output_columns))
        or y_train.shape != (len(training_record_ids),)
        or y_development.shape != (len(development_record_ids),)
        or set(y_train.tolist()) != {0, 1}
        or set(y_development.tolist()) != {0, 1}
    ):
        _fail("model matrices, rows, and binary targets must align")
    if not all(np.isfinite(values).all() for values in (x_train, x_development, x_evaluation)):
        _fail("model matrices must be finite")
    weights_array: np.ndarray | None = None
    if sample_weights is not None:
        weights_array = np.asarray(tuple(sample_weights), dtype=np.float64)
        if weights_array.shape != y_train.shape or not np.isfinite(weights_array).all() or np.any(
            weights_array <= 0.0
        ):
            _fail("sample weights must align and be finite positive values")

    if model_kind == "logistic_regression":
        model: LogisticRegression | HistGradientBoostingClassifier = LogisticRegression(
            C=1.0,
            solver="lbfgs",
            max_iter=1000,
            random_state=42,
        )
    else:
        model = HistGradientBoostingClassifier(
            learning_rate=0.1,
            max_iter=100,
            max_leaf_nodes=31,
            l2_regularization=0.0,
            early_stopping=False,
            random_state=43,
        )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ConvergenceWarning)
        try:
            model.fit(x_train, y_train, sample_weight=weights_array)
            raw_development = np.asarray(model.predict_proba(x_development)[:, 1], dtype=np.float64)
            raw_evaluation = np.asarray(model.predict_proba(x_evaluation)[:, 1], dtype=np.float64)
        except (TypeError, ValueError, FloatingPointError) as exc:
            raise V3RuntimeError("registered model fit or prediction failed") from exc
    if any(issubclass(item.category, ConvergenceWarning) for item in caught):
        _fail("registered model emitted a convergence warning")
    if not np.isfinite(raw_development).all() or not np.isfinite(raw_evaluation).all():
        _fail("registered model produced non-finite probabilities")
    targets_hash = labelled_targets_sha256(training_record_ids, y_train.tolist())
    weights_hash = _weights_sha256(sample_weights)
    try:
        calibration = fit_logit_calibration(
            raw_development.tolist(),
            y_development.tolist(),
            probability_clip=protocol.models.calibration_probability_clip,
            max_iter=protocol.models.calibration_max_iter,
            tolerance=protocol.models.calibration_tolerance,
        )
    except CalibrationAbstentionSignal as exc:
        raise ModelCalibrationAbstentionSignal(
            ModelCalibrationAbstention(
                protocol_sha256=protocol.canonical_sha256(),
                dataset_id=dataset.dataset_id,
                dataset_role=dataset.role,
                model_kind=model_kind,
                training_role=training_role,
                training_targets_sha256=targets_hash,
                sample_weights_sha256=weights_hash,
                calibration_abstention=exc.abstention,
            )
        ) from exc
    development_probabilities = apply_logit_calibration(
        raw_development.tolist(),
        calibration,
        clip=protocol.models.calibration_probability_clip,
    )
    evaluation_probabilities = apply_logit_calibration(
        raw_evaluation.tolist(),
        calibration,
        clip=protocol.models.calibration_probability_clip,
    )
    model_payload: dict[str, object] = {
        "schema_version": MODEL_SCHEMA_VERSION,
        "protocol_sha256": protocol.canonical_sha256(),
        "dataset_id": dataset.dataset_id,
        "model_kind": model_kind,
        "training_role": training_role,
        "training_targets_sha256": targets_hash,
        "sample_weights_sha256": weights_hash,
        "preprocessor_sha256": state.canonical_sha256(),
        "output_columns_sha256": state.output_columns_sha256,
        "classes": tuple(int(value) for value in model.classes_),
        "calibration_sha256": calibration.canonical_sha256(),
        "raw_development_predictions_sha256": canonical_sha256(
            {
                "record_ids": tuple(development_record_ids),
                "probabilities": tuple(float(value) for value in raw_development),
            }
        ),
        "raw_evaluation_predictions_sha256": canonical_sha256(
            {
                "record_ids": tuple(evaluation_record_ids),
                "probabilities": tuple(float(value) for value in raw_evaluation),
            }
        ),
        "calibrated_development_predictions_sha256": canonical_sha256(
            {
                "record_ids": tuple(development_record_ids),
                "probabilities": development_probabilities,
            }
        ),
        "calibrated_evaluation_predictions_sha256": canonical_sha256(
            {
                "record_ids": tuple(evaluation_record_ids),
                "probabilities": evaluation_probabilities,
            }
        ),
    }
    if isinstance(model, LogisticRegression):
        model_payload.update(
            {
                "coefficients": tuple(tuple(float(value) for value in row) for row in model.coef_),
                "intercept": tuple(float(value) for value in model.intercept_),
                "iterations": tuple(int(value) for value in model.n_iter_),
            }
        )
    else:
        model_payload.update(
            {
                "iterations": int(model.n_iter_),
                "train_score": tuple(float(value) for value in model.train_score_),
                "validation_score": tuple(float(value) for value in model.validation_score_),
            }
        )
    fitted_hash = canonical_sha256(model_payload)
    return FittedProbabilities(
        protocol_sha256=protocol.canonical_sha256(),
        dataset_id=dataset.dataset_id,
        model_kind=model_kind,
        training_role=training_role,
        training_targets_sha256=targets_hash,
        sample_weights_sha256=weights_hash,
        preprocessor_sha256=state.canonical_sha256(),
        output_columns_sha256=state.output_columns_sha256,
        calibration=calibration,
        development_record_ids=tuple(development_record_ids),
        development_probabilities=development_probabilities,
        evaluation_record_ids=tuple(evaluation_record_ids),
        evaluation_probabilities=evaluation_probabilities,
        fitted_model_sha256=fitted_hash,
    )


@dataclass(frozen=True)
class PreparedRuntimeDataset:
    """In-memory runtime inputs; deliberately excluded from JSON artifacts."""

    binding: V3DatasetBinding
    split_receipt: DatasetSplitReceipt
    split: RuntimeSplit
    preprocessor: PreprocessorState
    train_record_ids: tuple[str, ...]
    development_record_ids: tuple[str, ...]
    sealed_record_ids: tuple[str, ...]
    train_targets: tuple[int, ...]
    development_targets: tuple[int, ...]
    sealed_targets: tuple[int, ...]
    train_matrix: np.ndarray
    development_matrix: np.ndarray
    sealed_matrix: np.ndarray


def _pick(values: Sequence[_ValueT], indices: Sequence[int]) -> tuple[_ValueT, ...]:
    return tuple(values[index] for index in indices)


def prepare_runtime_dataset(
    *,
    protocol: RegisteredV3Protocol,
    dataset: V3DatasetBinding,
    split_receipt: DatasetSplitReceipt,
    frame: pd.DataFrame,
) -> PreparedRuntimeDataset:
    """Prepare registered partitions without fitting or scoring a model."""

    split = reconstruct_runtime_split(
        protocol=protocol,
        dataset=dataset,
        frame=frame,
        receipt=split_receipt,
    )
    features = frame.loc[:, list(dataset.analysis_features)].copy()
    train_indices = split.indices("train")
    development_indices = split.indices("development")
    sealed_indices = split.indices("sealed_test")

    def subset(indices: Sequence[int]) -> pd.DataFrame:
        return features.iloc[list(indices)].reset_index(drop=True)

    train_frame = subset(train_indices)
    development_frame = subset(development_indices)
    sealed_frame = subset(sealed_indices)
    state = fit_preprocessor(dataset, train_frame)
    train_matrix = transform_features(dataset=dataset, state=state, frame=train_frame)
    development_matrix = transform_features(
        dataset=dataset, state=state, frame=development_frame
    )
    sealed_matrix = transform_features(dataset=dataset, state=state, frame=sealed_frame)

    return PreparedRuntimeDataset(
        binding=dataset,
        split_receipt=split_receipt,
        split=split,
        preprocessor=state,
        train_record_ids=_pick(split.record_ids, train_indices),
        development_record_ids=_pick(split.record_ids, development_indices),
        sealed_record_ids=_pick(split.record_ids, sealed_indices),
        train_targets=_pick(split.labels, train_indices),
        development_targets=_pick(split.labels, development_indices),
        sealed_targets=_pick(split.labels, sealed_indices),
        train_matrix=train_matrix,
        development_matrix=development_matrix,
        sealed_matrix=sealed_matrix,
    )
