"""Registered P2R runtime for dominant-cause instrument measurements.

The runtime deliberately separates three units that must not be conflated:

* a dataset-level measurement is one seed evaluated on one pinned dataset;
* a paired observation is the conservative cross-dataset reduction for one seed;
* a mechanism study is the five paired observations required by the protocol.

This prevents the two datasets from being counted as ten independent seeds while
still enforcing the registered rule that both datasets must pass.  The module
contains no network, filesystem, release, or attempt-marker operations; those
belong to the closeout entry point.
"""

from __future__ import annotations

import hashlib
import math
import warnings
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Final, Literal, NoReturn

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import V3DatasetBinding
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    PreparedRuntimeDataset,
    transform_features,
)
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN
from aletheia_lab.benchmark.p2.instrument_validity import (
    InstrumentCandidatePlan,
    ManipulationObservation,
    PlannedInstrumentCandidate,
    build_manipulation_observation,
)
from aletheia_lab.benchmark.p2.lightweight_protocol import (
    LightweightConfirmatoryProtocol,
    MechanismName,
    StudyDatasetBinding,
    verify_lightweight_confirmatory_protocol,
)

DATASET_MEASUREMENT_SCHEMA_VERSION: Final[Literal["p2r-dataset-seed-measurement/1"]] = (
    "p2r-dataset-seed-measurement/1"
)
MODEL_RECEIPT_SCHEMA_VERSION: Final[Literal["p2r-fitted-model/1"]] = "p2r-fitted-model/1"
DECLARED_MANIPULATION_MAGNITUDE: Final[float] = 0.20

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
DatasetRole = Literal["primary", "external_replication"]


class P2RRuntimeError(ValueError):
    """Raised when execution evidence cannot satisfy the frozen contract."""


def _fail(message: str) -> NoReturn:
    raise P2RRuntimeError(message)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


def _rank(namespace: str, seed: int, record_id: str) -> str:
    return hashlib.sha256(f"{namespace}:{seed}:{record_id}".encode()).hexdigest()


def _accuracy(targets: Sequence[int], probabilities: Sequence[float]) -> float:
    if not targets or len(targets) != len(probabilities):
        _fail("accuracy requires aligned non-empty target and probability vectors")
    values = tuple(float(value) for value in probabilities)
    if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in values):
        _fail("accuracy probabilities must be finite and lie in [0, 1]")
    return math.fsum(
        int((probability >= 0.5) == bool(target))
        for target, probability in zip(targets, values, strict=True)
    ) / len(targets)


def _category_token(value: object) -> str:
    token = str(value).strip()
    if not token:
        _fail("categorical intervention values must not be blank")
    return token


def _mode_and_second(values: Sequence[object]) -> tuple[str, str]:
    counts = Counter(_category_token(value) for value in values)
    if len(counts) < 2:
        _fail("registered categorical intervention requires at least two categories")
    ordered = sorted(counts, key=lambda item: (-counts[item], item))
    return ordered[0], ordered[1]


def _selected_indices(
    *, namespace: str, seed: int, record_ids: Sequence[str], eligible: Sequence[int], count: int
) -> tuple[int, ...]:
    if count <= 0 or count > len(eligible):
        _fail("registered manipulation cannot achieve its declared row count")
    return tuple(
        sorted(
            sorted(
                eligible,
                key=lambda index: (_rank(namespace, seed, record_ids[index]), index),
            )[:count]
        )
    )


class P2RFittedModelReceipt(_StrictFrozenModel):
    schema_version: Literal["p2r-fitted-model/1"] = MODEL_RECEIPT_SCHEMA_VERSION
    protocol_sha256: Sha256
    dataset_id: str
    training_membership_sha256: Sha256
    preprocessor_sha256: Sha256
    output_columns_sha256: Sha256
    model_parameters: tuple[str, ...]
    classes: tuple[int, ...]
    coefficient_sha256: Sha256
    clean_probability_sha256: Sha256
    model_sha256: Sha256

    @model_validator(mode="after")
    def _receipt_is_derived(self) -> P2RFittedModelReceipt:
        payload = self.model_dump(mode="json", exclude={"model_sha256"})
        if self.model_sha256 != canonical_sha256(payload):
            raise ValueError("model_sha256 does not bind the fitted-model receipt")
        if self.model_parameters != (
            "C=1.0",
            "solver=lbfgs",
            "max_iter=1000",
            "random_state=42",
        ):
            raise ValueError("fitted model differs from the registered parameters")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


@dataclass(frozen=True)
class _FittedModel:
    estimator: LogisticRegression
    receipt: P2RFittedModelReceipt
    clean_probabilities: tuple[float, ...]


def fit_p2r_model(
    *,
    protocol: LightweightConfirmatoryProtocol,
    prepared: PreparedRuntimeDataset,
) -> _FittedModel:
    """Fit the one registered logistic model without calibration or tuning."""

    checked = verify_lightweight_confirmatory_protocol(protocol)
    if prepared.binding.dataset_id not in {item.dataset_id for item in checked.datasets}:
        _fail("prepared dataset is outside the mechanism protocol")
    model = LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000, random_state=42)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ConvergenceWarning)
        try:
            model.fit(prepared.train_matrix, np.asarray(prepared.train_targets, dtype=np.int64))
            probabilities = np.asarray(
                model.predict_proba(prepared.sealed_matrix)[:, 1], dtype=np.float64
            )
        except (TypeError, ValueError, FloatingPointError) as exc:
            raise P2RRuntimeError("registered P2R model fit or prediction failed") from exc
    if any(issubclass(item.category, ConvergenceWarning) for item in caught):
        _fail("registered P2R model emitted a convergence warning")
    if (
        probabilities.shape != (len(prepared.sealed_record_ids),)
        or not np.isfinite(probabilities).all()
    ):
        _fail("registered P2R model produced invalid probabilities")
    probability_tuple = tuple(float(value) for value in probabilities)
    payload: dict[str, object] = {
        "schema_version": MODEL_RECEIPT_SCHEMA_VERSION,
        "protocol_sha256": checked.canonical_sha256(),
        "dataset_id": prepared.binding.dataset_id,
        "training_membership_sha256": canonical_sha256(
            {"record_ids": prepared.train_record_ids, "targets": prepared.train_targets}
        ),
        "preprocessor_sha256": prepared.preprocessor.canonical_sha256(),
        "output_columns_sha256": prepared.preprocessor.output_columns_sha256,
        "model_parameters": checked.model.parameters,
        "classes": tuple(int(value) for value in model.classes_),
        "coefficient_sha256": canonical_sha256(
            {
                "coef": tuple(tuple(float(value) for value in row) for row in model.coef_),
                "intercept": tuple(float(value) for value in model.intercept_),
                "iterations": tuple(int(value) for value in model.n_iter_),
            }
        ),
        "clean_probability_sha256": canonical_sha256(
            {
                "record_ids": prepared.sealed_record_ids,
                "probabilities": probability_tuple,
            }
        ),
    }
    receipt = P2RFittedModelReceipt.model_validate(
        {**payload, "model_sha256": canonical_sha256(payload)}
    )
    return _FittedModel(model, receipt, probability_tuple)


class DatasetSeedMeasurement(_StrictFrozenModel):
    """One dataset-level seed result; datasets are not independent seed N."""

    schema_version: Literal["p2r-dataset-seed-measurement/1"] = DATASET_MEASUREMENT_SCHEMA_VERSION
    protocol_sha256: Sha256
    mechanism: MechanismName
    dataset_id: str
    dataset_role: DatasetRole
    split_membership_sha256: Sha256
    sealed_membership_sha256: Sha256
    target_feature: str
    seed: int
    declared_manipulation_magnitude: float
    achieved_manipulation_magnitude: float
    clean_accuracy: float
    manipulated_accuracy: float
    nuisance_accuracy: float
    target_metric_delta: float
    nuisance_effect_magnitude: float
    model_sha256: Sha256
    source_binding_sha256: Sha256
    intervention_sha256: Sha256
    nuisance_comparator_sha256: Sha256
    measurement_sha256: Sha256

    @model_validator(mode="after")
    def _measurement_is_derived(self) -> DatasetSeedMeasurement:
        finite = (
            self.declared_manipulation_magnitude,
            self.achieved_manipulation_magnitude,
            self.clean_accuracy,
            self.manipulated_accuracy,
            self.nuisance_accuracy,
            self.target_metric_delta,
            self.nuisance_effect_magnitude,
        )
        if any(not math.isfinite(value) for value in finite):
            raise ValueError("dataset measurement values must be finite")
        if not math.isclose(
            self.target_metric_delta,
            self.manipulated_accuracy - self.clean_accuracy,
            abs_tol=1e-12,
        ):
            raise ValueError("target metric delta is not derived from accuracies")
        if not math.isclose(
            self.nuisance_effect_magnitude,
            abs(self.nuisance_accuracy - self.clean_accuracy),
            abs_tol=1e-12,
        ):
            raise ValueError("nuisance effect is not derived from accuracies")
        payload = self.model_dump(mode="json", exclude={"measurement_sha256"})
        if self.measurement_sha256 != canonical_sha256(payload):
            raise ValueError("measurement_sha256 does not bind the measurement")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def _measurement(
    *,
    protocol: LightweightConfirmatoryProtocol,
    study_dataset: StudyDatasetBinding,
    prepared: PreparedRuntimeDataset,
    training_frame: pd.DataFrame,
    sealed_frame: pd.DataFrame,
    seed: int,
    fitted: _FittedModel,
) -> DatasetSeedMeasurement:
    binding: V3DatasetBinding = prepared.binding
    if (
        tuple(training_frame.columns) != binding.analysis_features
        or tuple(sealed_frame.columns) != binding.analysis_features
    ):
        _fail("intervention frames must preserve manifest feature order")
    if len(training_frame) != len(prepared.train_record_ids):
        _fail("training intervention frame and registered membership do not align")
    if len(sealed_frame) != len(prepared.sealed_record_ids):
        _fail("sealed intervention frame and registered membership do not align")
    feature = study_dataset.target_feature
    if feature not in sealed_frame or feature not in binding.categorical_features:
        _fail("registered target feature is not a bound categorical feature")
    train_feature_index = binding.analysis_features.index(feature)
    # Reconstruct train-feature tokens from the source frame is intentionally
    # required by the caller through the train-frame metadata below.
    training_raw = tuple(training_frame[feature])
    sealed_raw = tuple(sealed_frame[feature])
    training_values = tuple(_category_token(value) for value in training_raw)
    sealed_values = tuple(_category_token(value) for value in sealed_raw)
    training_mode, second_mode = _mode_and_second(training_values)
    raw_by_token = {_category_token(value): value for value in training_raw}
    training_mode_value = raw_by_token[training_mode]
    second_mode_value = raw_by_token[second_mode]
    target_count = math.floor(DECLARED_MANIPULATION_MAGNITUDE * len(sealed_frame))
    manipulated = sealed_frame.copy(deep=True)
    nuisance = sealed_frame.copy(deep=True)
    if protocol.mechanism == "data_drift":
        eligible = tuple(
            index for index, value in enumerate(sealed_values) if value != training_mode
        )
        selected = _selected_indices(
            namespace=f"{protocol.mechanism}:{binding.dataset_id}:target",
            seed=seed,
            record_ids=prepared.sealed_record_ids,
            eligible=eligible,
            count=target_count,
        )
        manipulated.iloc[list(selected), train_feature_index] = training_mode_value
        empirical = tuple(sorted(sealed_raw, key=_category_token))
        nuisance_values = tuple(
            empirical[
                int(
                    _rank(
                        f"{protocol.mechanism}:{binding.dataset_id}:nuisance",
                        seed,
                        prepared.sealed_record_ids[index],
                    ),
                    16,
                )
                % len(empirical)
            ]
            for index in selected
        )
        nuisance.iloc[list(selected), train_feature_index] = nuisance_values
    else:
        eligible = tuple(
            index for index, value in enumerate(sealed_values) if value == training_mode
        )
        selected = _selected_indices(
            namespace=f"{protocol.mechanism}:{binding.dataset_id}:target",
            seed=seed,
            record_ids=prepared.sealed_record_ids,
            eligible=eligible,
            count=target_count,
        )
        manipulated.iloc[list(selected), train_feature_index] = second_mode_value
        # Name-bound column reordering must be prediction invariant.  It is
        # transformed back into manifest order, so any positional dependence is
        # exposed without injecting a second scientific fault.
        nuisance = nuisance.loc[:, list(reversed(binding.analysis_features))]
        nuisance = nuisance.loc[:, list(binding.analysis_features)]

    manipulated_matrix = transform_features(
        dataset=binding, state=prepared.preprocessor, frame=manipulated
    )
    nuisance_matrix = transform_features(
        dataset=binding, state=prepared.preprocessor, frame=nuisance
    )
    try:
        manipulated_probabilities = tuple(
            float(value) for value in fitted.estimator.predict_proba(manipulated_matrix)[:, 1]
        )
        nuisance_probabilities = tuple(
            float(value) for value in fitted.estimator.predict_proba(nuisance_matrix)[:, 1]
        )
    except (TypeError, ValueError, FloatingPointError) as exc:
        raise P2RRuntimeError("registered P2R intervention prediction failed") from exc
    clean_accuracy = _accuracy(prepared.sealed_targets, fitted.clean_probabilities)
    manipulated_accuracy = _accuracy(prepared.sealed_targets, manipulated_probabilities)
    nuisance_accuracy = _accuracy(prepared.sealed_targets, nuisance_probabilities)
    achieved = len(selected) / len(sealed_frame)
    source_binding = canonical_sha256(
        {
            "protocol_sha256": protocol.canonical_sha256(),
            "dataset_id": binding.dataset_id,
            "split_membership_sha256": study_dataset.split_membership_sha256,
            "sealed_membership_sha256": study_dataset.sealed_membership_sha256,
        }
    )
    intervention_hash = canonical_sha256(
        {
            "mechanism": protocol.mechanism,
            "dataset_id": binding.dataset_id,
            "seed": seed,
            "feature": feature,
            "selected_record_ids": tuple(prepared.sealed_record_ids[index] for index in selected),
            "declared_magnitude": DECLARED_MANIPULATION_MAGNITUDE,
            "achieved_magnitude": achieved,
            "matrix_sha256": canonical_sha256(manipulated_matrix.tolist()),
        }
    )
    nuisance_hash = canonical_sha256(
        {
            "mechanism": protocol.mechanism,
            "dataset_id": binding.dataset_id,
            "seed": seed,
            "comparator": study_dataset.nuisance_comparator,
            "matrix_sha256": canonical_sha256(nuisance_matrix.tolist()),
        }
    )
    payload: dict[str, object] = {
        "schema_version": DATASET_MEASUREMENT_SCHEMA_VERSION,
        "protocol_sha256": protocol.canonical_sha256(),
        "mechanism": protocol.mechanism,
        "dataset_id": binding.dataset_id,
        "dataset_role": study_dataset.role,
        "split_membership_sha256": study_dataset.split_membership_sha256,
        "sealed_membership_sha256": study_dataset.sealed_membership_sha256,
        "target_feature": feature,
        "seed": seed,
        "declared_manipulation_magnitude": DECLARED_MANIPULATION_MAGNITUDE,
        "achieved_manipulation_magnitude": achieved,
        "clean_accuracy": clean_accuracy,
        "manipulated_accuracy": manipulated_accuracy,
        "nuisance_accuracy": nuisance_accuracy,
        "target_metric_delta": manipulated_accuracy - clean_accuracy,
        "nuisance_effect_magnitude": abs(nuisance_accuracy - clean_accuracy),
        "model_sha256": fitted.receipt.model_sha256,
        "source_binding_sha256": source_binding,
        "intervention_sha256": intervention_hash,
        "nuisance_comparator_sha256": nuisance_hash,
    }
    return DatasetSeedMeasurement.model_validate(
        {**payload, "measurement_sha256": canonical_sha256(payload)}
    )


def execute_p2r_dataset(
    *,
    protocol: LightweightConfirmatoryProtocol,
    prepared: PreparedRuntimeDataset,
    training_frame: pd.DataFrame,
    sealed_frame: pd.DataFrame,
) -> tuple[P2RFittedModelReceipt, tuple[DatasetSeedMeasurement, ...]]:
    """Execute all five seeds for one dataset in memory."""

    checked = verify_lightweight_confirmatory_protocol(protocol)
    try:
        study_dataset = next(
            item for item in checked.datasets if item.dataset_id == prepared.binding.dataset_id
        )
    except StopIteration as exc:
        raise P2RRuntimeError("prepared dataset is outside the protocol census") from exc
    if prepared.split.membership_sha256 != study_dataset.split_membership_sha256:
        _fail("runtime split differs from the registered membership")
    if prepared.split.sealed_membership_sha256 != study_dataset.sealed_membership_sha256:
        _fail("runtime sealed membership differs from the registered protocol")
    fitted = fit_p2r_model(protocol=checked, prepared=prepared)
    measurements = tuple(
        _measurement(
            protocol=checked,
            study_dataset=study_dataset,
            prepared=prepared,
            training_frame=training_frame,
            sealed_frame=sealed_frame,
            seed=seed,
            fitted=fitted,
        )
        for seed in checked.execution.seeds
    )
    return fitted.receipt, measurements


def _candidate_identity(mechanism: MechanismName, seed: int, kind: str) -> str:
    digest = canonical_sha256(
        {"schema_version": "p2r-paired-candidate-identity/1", "mechanism": mechanism, "seed": seed}
    )
    prefix = "p2-candidate-" if kind == "candidate" else "p2-family-"
    return prefix + digest


def build_joint_candidate_plan(
    *,
    instrument_protocol_sha256: str,
    protocols: Sequence[LightweightConfirmatoryProtocol],
) -> InstrumentCandidatePlan:
    """Freeze ten paired candidates without reading any measured outcome."""

    checked = tuple(verify_lightweight_confirmatory_protocol(item) for item in protocols)
    if tuple(item.mechanism for item in checked) != ("data_drift", "preprocessing_bug"):
        _fail("joint candidate plan requires drift then preprocessing protocols")
    entries: list[PlannedInstrumentCandidate] = []
    for protocol in checked:
        for seed in protocol.execution.seeds:
            source = canonical_sha256(
                {
                    "protocol_sha256": protocol.canonical_sha256(),
                    "dataset_bindings": tuple(
                        (
                            item.dataset_id,
                            item.split_membership_sha256,
                            item.sealed_membership_sha256,
                        )
                        for item in protocol.datasets
                    ),
                    "seed": seed,
                }
            )
            nuisance = canonical_sha256(
                {
                    "protocol_sha256": protocol.canonical_sha256(),
                    "comparators": tuple(item.nuisance_comparator for item in protocol.datasets),
                    "seed": seed,
                }
            )
            manifest = canonical_sha256(
                {
                    "schema_version": "p2r-paired-measurement-manifest/1",
                    "protocol_sha256": protocol.canonical_sha256(),
                    "mechanism": protocol.mechanism,
                    "seed": seed,
                    "reduction": "worst_dataset_for_each_gate",
                }
            )
            entries.append(
                PlannedInstrumentCandidate(
                    candidate_id=_candidate_identity(protocol.mechanism, seed, "candidate"),
                    case_family_id=_candidate_identity(protocol.mechanism, seed, "family"),
                    fault_type=protocol.mechanism,
                    candidate_role="fault_directed",
                    seed=seed,
                    declared_manipulation_magnitude=DECLARED_MANIPULATION_MAGNITUDE,
                    source_binding_sha256=source,
                    nuisance_comparator_sha256=nuisance,
                    measurement_manifest_sha256=manifest,
                )
            )
    return InstrumentCandidatePlan(
        protocol_sha256=instrument_protocol_sha256,
        entries=tuple(sorted(entries, key=lambda item: item.candidate_id)),
        frozen_before_outcomes=True,
        model_fitted=False,
        predictive_metrics_generated=False,
    )


def paired_observations(
    *,
    plan: InstrumentCandidatePlan,
    measurements: Sequence[DatasetSeedMeasurement],
) -> tuple[ManipulationObservation, ...]:
    """Reduce both datasets conservatively into one observation per mechanism/seed."""

    by_key: dict[tuple[MechanismName, int], list[DatasetSeedMeasurement]] = {}
    for item in measurements:
        by_key.setdefault((item.mechanism, item.seed), []).append(item)
    output: list[ManipulationObservation] = []
    for planned in plan.entries:
        pair = tuple(by_key.get((planned.fault_type, planned.seed), ()))
        if len(pair) != 2 or {item.dataset_role for item in pair} != {
            "primary",
            "external_replication",
        }:
            _fail("paired observation requires both registered datasets exactly once")
        if len({item.protocol_sha256 for item in pair}) != 1:
            _fail("paired datasets are bound to different mechanism protocols")
        # The least harmful target delta, largest nuisance, and achieved value
        # furthest from the declaration make passing no easier than either
        # dataset-level result.
        achieved = max(
            (item.achieved_manipulation_magnitude for item in pair),
            key=lambda value: abs(value - planned.declared_manipulation_magnitude),
        )
        output.append(
            build_manipulation_observation(
                candidate_id=planned.candidate_id,
                case_family_id=planned.case_family_id,
                fault_type=planned.fault_type,
                seed=planned.seed,
                declared_manipulation_magnitude=planned.declared_manipulation_magnitude,
                achieved_manipulation_magnitude=achieved,
                target_metric_delta=max(item.target_metric_delta for item in pair),
                nuisance_effect_magnitude=max(item.nuisance_effect_magnitude for item in pair),
                source_binding_sha256=planned.source_binding_sha256,
                nuisance_comparator_sha256=planned.nuisance_comparator_sha256,
                measurement_manifest_sha256=planned.measurement_manifest_sha256,
            )
        )
    return tuple(sorted(output, key=lambda item: item.candidate_id))


def measurement_census(
    measurements: Sequence[DatasetSeedMeasurement],
    protocols: Mapping[MechanismName, LightweightConfirmatoryProtocol],
) -> tuple[DatasetSeedMeasurement, ...]:
    """Validate the exact 2 mechanisms x 2 datasets x 5 seeds census."""

    checked = {
        mechanism: verify_lightweight_confirmatory_protocol(protocol)
        for mechanism, protocol in protocols.items()
    }
    if set(checked) != {"data_drift", "preprocessing_bug"}:
        _fail("measurement census requires both mechanism protocols")
    try:
        revalidated = tuple(
            DatasetSeedMeasurement.model_validate(item.model_dump()) for item in measurements
        )
    except ValidationError as exc:
        raise P2RRuntimeError("dataset measurement content or hash is invalid") from exc
    ordered = tuple(
        sorted(revalidated, key=lambda item: (item.mechanism, item.dataset_id, item.seed))
    )
    expected = {
        (protocol.mechanism, dataset.dataset_id, seed)
        for protocol in checked.values()
        for dataset in protocol.datasets
        for seed in protocol.execution.seeds
    }
    observed = {(item.mechanism, item.dataset_id, item.seed) for item in ordered}
    if observed != expected or len(ordered) != len(expected):
        _fail("dataset measurement census is incomplete, duplicated, or out of protocol")
    hashes = tuple(item.measurement_sha256 for item in ordered)
    if len(set(hashes)) != len(hashes):
        _fail("dataset measurement census contains replayed evidence")
    for item in ordered:
        protocol = checked[item.mechanism]
        if item.protocol_sha256 != protocol.canonical_sha256():
            _fail("dataset measurement is bound to another protocol")
        dataset = next(
            candidate for candidate in protocol.datasets if candidate.dataset_id == item.dataset_id
        )
        if (
            item.dataset_role != dataset.role
            or item.split_membership_sha256 != dataset.split_membership_sha256
            or item.sealed_membership_sha256 != dataset.sealed_membership_sha256
            or item.target_feature != dataset.target_feature
        ):
            _fail("dataset measurement differs from its frozen dataset binding")
        expected_source = canonical_sha256(
            {
                "protocol_sha256": protocol.canonical_sha256(),
                "dataset_id": dataset.dataset_id,
                "split_membership_sha256": dataset.split_membership_sha256,
                "sealed_membership_sha256": dataset.sealed_membership_sha256,
            }
        )
        if item.source_binding_sha256 != expected_source:
            _fail("dataset measurement source binding does not reconcile")
    for label, hashes in (
        ("intervention", tuple(item.intervention_sha256 for item in ordered)),
        ("nuisance comparator", tuple(item.nuisance_comparator_sha256 for item in ordered)),
    ):
        if len(set(hashes)) != len(hashes):
            _fail(f"dataset measurement census replays a {label} receipt")
    return ordered
