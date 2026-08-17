"""Paired inference and fail-closed decisions for confirmatory label noise.

Synthetic-readiness analyses exercise this code but can never authorize
admission.  Only the registered mode accepts the frozen 10,000/100,000
resampling budgets and can produce a dataset or study decision.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Final, Literal, NoReturn, TypeVar

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_execution import (
    FLOAT_TOLERANCE,
    REQUIRED_CONTROL_IDS,
    ConfirmatoryExecutionError,
    ControlGate,
    FlipDirection,
    ProbabilityMetrics,
)
from aletheia_lab.benchmark.p2.confirmatory_protocol import (
    ConfirmatoryProtocol,
    DatasetBinding,
)
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN

INFERENCE_SCHEMA_VERSION: Final[Literal["p2-label-noise-confirmatory-inference/1"]] = (
    "p2-label-noise-confirmatory-inference/1"
)
BOOTSTRAP_QUANTILE_METHOD: Final[Literal["linear"]] = "linear"
CONFIDENCE_LEVEL: Final[float] = 0.95

ExecutionMode = Literal["synthetic_readiness", "registered_confirmatory"]
DatasetRole = Literal["primary", "external_replication"]
StudyDisposition = Literal[
    "retain_fail_closed_and_narrow_claim",
    "primary_dataset_bounded_admission",
    "bounded_cross_dataset_replication",
]
Sha256 = str
_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _fail(message: str) -> NoReturn:
    raise ConfirmatoryExecutionError(message)


def _revalidated(model: _ModelT) -> _ModelT:
    return type(model).model_validate(model.model_dump())


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class InferenceRunPlan(_StrictFrozenModel):
    mode: ExecutionMode
    bootstrap_resamples: int = Field(ge=100)
    bootstrap_seed: int = Field(ge=0)
    hypothesis_test_resamples: int = Field(ge=100)
    hypothesis_test_seed: int = Field(ge=0)

    @classmethod
    def synthetic(
        cls, *, bootstrap_resamples: int = 200, test_resamples: int = 500
    ) -> InferenceRunPlan:
        return cls(
            mode="synthetic_readiness",
            bootstrap_resamples=bootstrap_resamples,
            bootstrap_seed=271828,
            hypothesis_test_resamples=test_resamples,
            hypothesis_test_seed=161803,
        )

    @classmethod
    def registered(cls, protocol: ConfirmatoryProtocol) -> InferenceRunPlan:
        protocol = _revalidated(protocol)
        return cls(
            mode="registered_confirmatory",
            bootstrap_resamples=protocol.inference.bootstrap_resamples,
            bootstrap_seed=protocol.inference.bootstrap_seed,
            hypothesis_test_resamples=protocol.inference.hypothesis_test_resamples,
            hypothesis_test_seed=protocol.inference.hypothesis_test_seed,
        )

    def validate_against(self, protocol: ConfirmatoryProtocol) -> None:
        protocol = _revalidated(protocol)
        if self.mode == "registered_confirmatory" and (
            self.bootstrap_resamples != protocol.inference.bootstrap_resamples
            or self.bootstrap_seed != protocol.inference.bootstrap_seed
            or self.hypothesis_test_resamples != protocol.inference.hypothesis_test_resamples
            or self.hypothesis_test_seed != protocol.inference.hypothesis_test_seed
        ):
            _fail("registered inference must use the complete frozen resampling plan")


class ConfirmatoryReplicate(_StrictFrozenModel):
    """All evidence for one corruption seed, before any aggregate decision."""

    dataset_id: str = Field(min_length=1)
    dataset_role: DatasetRole
    cell_id: str = Field(min_length=1)
    direction: FlipDirection
    conditional_rate: float = Field(gt=0.0, lt=0.5)
    seed: int = Field(ge=0)
    protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    split_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    mutation_sha256: str = Field(pattern=SHA256_PATTERN)
    clean_metrics: ProbabilityMetrics
    observed_metrics: ProbabilityMetrics
    controls: tuple[ControlGate, ...]
    relative_log_loss_increase: float
    accuracy_delta: float

    @field_validator("relative_log_loss_increase", "accuracy_delta")
    @classmethod
    def _effect_is_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("replicate effects must be finite")
        return value

    @model_validator(mode="after")
    def _effects_and_controls_are_derived(self) -> ConfirmatoryReplicate:
        if self.clean_metrics.record_count != self.observed_metrics.record_count:
            raise ValueError("paired metrics must score the same record count")
        if self.clean_metrics.log_loss <= 0.0:
            raise ValueError("paired clean log loss must be positive")
        expected_relative = (
            self.observed_metrics.log_loss - self.clean_metrics.log_loss
        ) / self.clean_metrics.log_loss
        if not math.isclose(
            self.relative_log_loss_increase, expected_relative, abs_tol=FLOAT_TOLERANCE
        ):
            raise ValueError("relative log-loss increase must be derived from paired metrics")
        expected_accuracy = self.observed_metrics.accuracy - self.clean_metrics.accuracy
        if not math.isclose(self.accuracy_delta, expected_accuracy, abs_tol=FLOAT_TOLERANCE):
            raise ValueError("accuracy delta must be observed minus paired clean accuracy")
        if tuple(control.control_id for control in self.controls) != REQUIRED_CONTROL_IDS:
            raise ValueError("every replicate requires the complete ordered control set")
        return self

    @property
    def technical_gates_pass(self) -> bool:
        return all(control.passed for control in self.controls)

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def build_replicate(
    *,
    dataset_id: str,
    dataset_role: DatasetRole,
    cell_id: str,
    direction: FlipDirection,
    conditional_rate: float,
    seed: int,
    protocol_sha256: str,
    split_manifest_sha256: str,
    mutation_sha256: str,
    clean_metrics: ProbabilityMetrics,
    observed_metrics: ProbabilityMetrics,
    controls: Sequence[ControlGate],
) -> ConfirmatoryReplicate:
    relative = (observed_metrics.log_loss - clean_metrics.log_loss) / clean_metrics.log_loss
    return ConfirmatoryReplicate(
        dataset_id=dataset_id,
        dataset_role=dataset_role,
        cell_id=cell_id,
        direction=direction,
        conditional_rate=conditional_rate,
        seed=seed,
        protocol_sha256=protocol_sha256,
        split_manifest_sha256=split_manifest_sha256,
        mutation_sha256=mutation_sha256,
        clean_metrics=clean_metrics,
        observed_metrics=observed_metrics,
        controls=tuple(controls),
        relative_log_loss_increase=relative,
        accuracy_delta=observed_metrics.accuracy - clean_metrics.accuracy,
    )


class BootstrapInterval(_StrictFrozenModel):
    method: Literal["two_way_product_weight_bootstrap"]
    factors: tuple[Literal["evaluation_record", "corruption_seed"], ...]
    confidence_level: float
    quantile_method: Literal["linear"]
    resamples: int = Field(ge=100)
    seed: int = Field(ge=0)
    point_estimate: float
    lower_bound: float
    upper_bound: float

    @model_validator(mode="after")
    def _interval_is_ordered(self) -> BootstrapInterval:
        if self.factors != ("evaluation_record", "corruption_seed"):
            raise ValueError("bootstrap must include both crossed factors")
        if not math.isclose(self.confidence_level, CONFIDENCE_LEVEL, abs_tol=FLOAT_TOLERANCE):
            raise ValueError("the confirmatory interval is frozen at 95%")
        if not all(
            math.isfinite(value)
            for value in (self.point_estimate, self.lower_bound, self.upper_bound)
        ):
            raise ValueError("bootstrap estimates must be finite")
        if self.lower_bound > self.upper_bound:
            raise ValueError("bootstrap interval bounds are inverted")
        return self


def two_way_product_weight_bootstrap(
    *,
    clean_losses: Sequence[float],
    observed_losses_by_seed: Sequence[Sequence[float]],
    resamples: int,
    seed: int,
) -> BootstrapInterval:
    """Resample evaluation records and corruption seeds as crossed factors."""

    clean = np.asarray(clean_losses, dtype=float)
    observed = np.asarray(observed_losses_by_seed, dtype=float)
    if clean.ndim != 1 or clean.size < 2:
        _fail("the crossed bootstrap needs at least two evaluation records")
    if observed.ndim != 2 or observed.shape[0] < 2 or observed.shape[1] != clean.size:
        _fail("observed losses must form a seed-by-record matrix aligned to clean losses")
    if resamples < 100:
        _fail("the bootstrap requires at least 100 resamples")
    if not np.isfinite(clean).all() or not np.isfinite(observed).all() or np.any(clean < 0.0):
        _fail("bootstrap losses must be finite and non-negative")
    clean_mean = float(np.mean(clean))
    if clean_mean <= 0.0:
        _fail("relative bootstrap effects require positive clean log loss")
    point = float((np.mean(observed) - clean_mean) / clean_mean)
    generator = np.random.default_rng(seed)
    estimates = np.empty(resamples, dtype=float)
    seed_count, record_count = observed.shape
    for index in range(resamples):
        sampled_seeds = generator.integers(0, seed_count, size=seed_count)
        sampled_records = generator.integers(0, record_count, size=record_count)
        sampled_clean = float(np.mean(clean[sampled_records]))
        sampled_observed = float(np.mean(observed[np.ix_(sampled_seeds, sampled_records)]))
        estimates[index] = (sampled_observed - sampled_clean) / sampled_clean
    lower, upper = np.quantile(
        estimates,
        ((1.0 - CONFIDENCE_LEVEL) / 2.0, 1.0 - (1.0 - CONFIDENCE_LEVEL) / 2.0),
        method=BOOTSTRAP_QUANTILE_METHOD,
    )
    return BootstrapInterval(
        method="two_way_product_weight_bootstrap",
        factors=("evaluation_record", "corruption_seed"),
        confidence_level=CONFIDENCE_LEVEL,
        quantile_method=BOOTSTRAP_QUANTILE_METHOD,
        resamples=resamples,
        seed=seed,
        point_estimate=point,
        lower_bound=float(lower),
        upper_bound=float(upper),
    )


def paired_sign_flip_pvalue(effects: Sequence[float], *, resamples: int, seed: int) -> float:
    """One-sided Monte Carlo paired sign-flip test with a plus-one correction."""

    values = np.asarray(effects, dtype=float)
    if values.ndim != 1 or values.size < 2 or not np.isfinite(values).all():
        _fail("the sign-flip test requires at least two finite paired effects")
    if resamples < 100:
        _fail("the sign-flip test requires at least 100 resamples")
    observed = float(np.mean(values))
    generator = np.random.default_rng(seed)
    exceedances = 0
    remaining = resamples
    while remaining:
        batch_size = min(10_000, remaining)
        signs = generator.choice(np.asarray((-1.0, 1.0)), size=(batch_size, values.size))
        null_means = np.mean(signs * values, axis=1)
        exceedances += int(np.count_nonzero(null_means >= observed))
        remaining -= batch_size
    return (exceedances + 1.0) / (resamples + 1.0)


def holm_adjust(pvalues: Mapping[str, float]) -> dict[str, float]:
    """Return Holm-adjusted p-values while preserving hypothesis identifiers."""

    if len(pvalues) != 2:
        _fail("the frozen multiplicity family contains exactly two directions")
    if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in pvalues.values()):
        _fail("p-values must be finite and lie in [0, 1]")
    ordered = sorted(pvalues.items(), key=lambda item: (item[1], item[0]))
    adjusted: dict[str, float] = {}
    running = 0.0
    family_size = len(ordered)
    for index, (name, value) in enumerate(ordered):
        running = max(running, min(1.0, (family_size - index) * value))
        adjusted[name] = running
    return adjusted


class DoseSummary(_StrictFrozenModel):
    cell_id: str
    direction: FlipDirection
    conditional_rate: float
    replicate_count: int
    mean_relative_log_loss_increase: float
    mean_accuracy_delta: float
    all_technical_gates_pass: bool


class DirectionAnalysis(_StrictFrozenModel):
    direction: FlipDirection
    co_primary_cell_id: str
    replicate_count: int
    mean_relative_log_loss_increase: float
    interval: BootstrapInterval
    raw_one_sided_pvalue: float = Field(ge=0.0, le=1.0)
    holm_adjusted_pvalue: float = Field(ge=0.0, le=1.0)
    all_technical_gates_pass: bool
    practical_effect_pass: bool
    interval_pass: bool
    multiplicity_pass: bool
    direction_pass: bool

    @model_validator(mode="after")
    def _decision_is_derived(self) -> DirectionAnalysis:
        expected_practical = self.mean_relative_log_loss_increase >= 0.05
        expected_interval = self.interval.lower_bound > 0.0
        expected_multiplicity = self.holm_adjusted_pvalue < 0.05
        if (
            self.practical_effect_pass != expected_practical
            or self.interval_pass != expected_interval
            or self.multiplicity_pass != expected_multiplicity
        ):
            raise ValueError("direction gate flags must be derived from frozen thresholds")
        expected_pass = (
            expected_practical
            and expected_interval
            and expected_multiplicity
            and self.all_technical_gates_pass
        )
        if self.direction_pass != expected_pass:
            raise ValueError("direction pass must be the conjunction of all frozen gates")
        return self


class DatasetAnalysis(_StrictFrozenModel):
    schema_version: Literal["p2-label-noise-confirmatory-inference/1"] = INFERENCE_SCHEMA_VERSION
    mode: ExecutionMode
    protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    dataset_id: str
    dataset_role: DatasetRole
    split_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    replicate_count: int
    dose_summaries: tuple[DoseSummary, ...]
    directions: tuple[DirectionAnalysis, ...]
    batch_technical_gates_pass: bool
    admission_authorized: bool
    dataset_pass: bool | None

    @model_validator(mode="after")
    def _authorization_is_fail_closed(self) -> DatasetAnalysis:
        if tuple(item.direction for item in self.directions) != ("yes_to_no", "no_to_yes"):
            raise ValueError("both co-primary directions are required in canonical order")
        if self.mode == "synthetic_readiness":
            if self.admission_authorized or self.dataset_pass is not None:
                raise ValueError("synthetic analysis cannot authorize a scientific decision")
        else:
            expected = any(item.direction_pass for item in self.directions)
            if not self.admission_authorized or self.dataset_pass != expected:
                raise ValueError("registered dataset decision must follow the frozen OR gate")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def _validate_census(
    *,
    protocol: ConfirmatoryProtocol,
    dataset: DatasetBinding,
    replicates: Sequence[ConfirmatoryReplicate],
) -> dict[str, tuple[ConfirmatoryReplicate, ...]]:
    expected_protocol = protocol.canonical_sha256()
    expected: dict[str, tuple[int, ...]] = {}
    for cell in protocol.intervention_cells:
        expected[cell.cell_id] = (
            cell.primary_replicate_seeds
            if dataset.role == "primary"
            else cell.replication_replicate_seeds
        )
    grouped: dict[str, list[ConfirmatoryReplicate]] = {cell_id: [] for cell_id in expected}
    seen: set[tuple[str, int]] = set()
    for raw in replicates:
        replicate = _revalidated(raw)
        key = (replicate.cell_id, replicate.seed)
        if key in seen:
            _fail("duplicate or replayed replicate detected")
        seen.add(key)
        if replicate.cell_id not in grouped:
            _fail("replicate lies outside the frozen cell grid")
        if (
            replicate.dataset_id != dataset.dataset_id
            or replicate.dataset_role != dataset.role
            or replicate.protocol_sha256 != expected_protocol
        ):
            _fail("replicate provenance does not match this dataset and protocol")
        grouped[replicate.cell_id].append(replicate)
    result: dict[str, tuple[ConfirmatoryReplicate, ...]] = {}
    for cell in protocol.intervention_cells:
        values = tuple(sorted(grouped[cell.cell_id], key=lambda item: item.seed))
        if tuple(item.seed for item in values) != expected[cell.cell_id]:
            _fail(f"cell {cell.cell_id!r} does not contain its complete frozen seed census")
        if any(
            item.direction != cell.flip_direction
            or not math.isclose(
                item.conditional_rate, cell.conditional_flip_rate, abs_tol=FLOAT_TOLERANCE
            )
            for item in values
        ):
            _fail(f"cell {cell.cell_id!r} disagrees with the frozen intervention grid")
        result[cell.cell_id] = values
    split_hashes = {item.split_manifest_sha256 for item in replicates}
    clean_hashes = {item.clean_metrics.canonical_sha256() for item in replicates}
    if len(split_hashes) != 1 or len(clean_hashes) != 1:
        _fail("all replicates must share one split and one paired clean reference")
    return result


def analyze_dataset(
    *,
    protocol: ConfirmatoryProtocol,
    dataset: DatasetBinding,
    replicates: Sequence[ConfirmatoryReplicate],
    run_plan: InferenceRunPlan,
) -> DatasetAnalysis:
    """Reconcile all six cells before computing any confirmatory decision."""

    protocol = _revalidated(protocol)
    dataset = _revalidated(dataset)
    run_plan = _revalidated(run_plan)
    run_plan.validate_against(protocol)
    if dataset not in protocol.datasets:
        _fail("dataset analysis requested for an unregistered dataset")
    grouped = _validate_census(protocol=protocol, dataset=dataset, replicates=replicates)
    batch_technical_gates_pass = all(item.technical_gates_pass for item in replicates)
    summaries: list[DoseSummary] = []
    raw_pvalues: dict[str, float] = {}
    intervals: dict[str, BootstrapInterval] = {}
    co_primary: dict[str, tuple[ConfirmatoryReplicate, ...]] = {}
    for cell in protocol.intervention_cells:
        values = grouped[cell.cell_id]
        summaries.append(
            DoseSummary(
                cell_id=cell.cell_id,
                direction=cell.flip_direction,
                conditional_rate=cell.conditional_flip_rate,
                replicate_count=len(values),
                mean_relative_log_loss_increase=math.fsum(
                    item.relative_log_loss_increase for item in values
                )
                / len(values),
                mean_accuracy_delta=math.fsum(item.accuracy_delta for item in values) / len(values),
                all_technical_gates_pass=all(item.technical_gates_pass for item in values),
            )
        )
        if cell.hypothesis_role == "co_primary":
            co_primary[cell.flip_direction] = values
            clean_losses = values[0].clean_metrics.per_record_log_loss
            intervals[cell.flip_direction] = two_way_product_weight_bootstrap(
                clean_losses=clean_losses,
                observed_losses_by_seed=tuple(
                    item.observed_metrics.per_record_log_loss for item in values
                ),
                resamples=run_plan.bootstrap_resamples,
                seed=run_plan.bootstrap_seed,
            )
            raw_pvalues[cell.flip_direction] = paired_sign_flip_pvalue(
                tuple(item.relative_log_loss_increase for item in values),
                resamples=run_plan.hypothesis_test_resamples,
                seed=run_plan.hypothesis_test_seed,
            )
    adjusted = holm_adjust(raw_pvalues)
    directions: list[DirectionAnalysis] = []
    for direction in ("yes_to_no", "no_to_yes"):
        values = co_primary[direction]
        mean_effect = math.fsum(item.relative_log_loss_increase for item in values) / len(values)
        practical = mean_effect >= protocol.endpoints.minimum_practical_effect
        interval_pass = intervals[direction].lower_bound > 0.0
        multiplicity_pass = adjusted[direction] < protocol.inference.familywise_alpha
        technical = batch_technical_gates_pass and all(item.technical_gates_pass for item in values)
        directions.append(
            DirectionAnalysis(
                direction=direction,
                co_primary_cell_id=values[0].cell_id,
                replicate_count=len(values),
                mean_relative_log_loss_increase=mean_effect,
                interval=intervals[direction],
                raw_one_sided_pvalue=raw_pvalues[direction],
                holm_adjusted_pvalue=adjusted[direction],
                all_technical_gates_pass=technical,
                practical_effect_pass=practical,
                interval_pass=interval_pass,
                multiplicity_pass=multiplicity_pass,
                direction_pass=(practical and interval_pass and multiplicity_pass and technical),
            )
        )
    authorized = run_plan.mode == "registered_confirmatory"
    return DatasetAnalysis(
        mode=run_plan.mode,
        protocol_sha256=protocol.canonical_sha256(),
        dataset_id=dataset.dataset_id,
        dataset_role=dataset.role,
        split_manifest_sha256=replicates[0].split_manifest_sha256,
        replicate_count=len(replicates),
        dose_summaries=tuple(summaries),
        directions=tuple(directions),
        batch_technical_gates_pass=batch_technical_gates_pass,
        admission_authorized=authorized,
        dataset_pass=(any(item.direction_pass for item in directions) if authorized else None),
    )


class StudyDecision(_StrictFrozenModel):
    protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    primary_analysis_sha256: str = Field(pattern=SHA256_PATTERN)
    replication_analysis_sha256: str = Field(pattern=SHA256_PATTERN)
    primary_pass: bool
    replication_pass: bool
    cross_dataset_direction: FlipDirection | None
    mechanism_admitted: bool
    cross_dataset_claim_allowed: bool
    disposition: StudyDisposition


def decide_study(
    *,
    protocol: ConfirmatoryProtocol,
    primary: DatasetAnalysis,
    replication: DatasetAnalysis,
) -> StudyDecision:
    """Apply primary precedence; external replication can never rescue failure."""

    protocol = _revalidated(protocol)
    primary = _revalidated(primary)
    replication = _revalidated(replication)
    expected_hash = protocol.canonical_sha256()
    if primary.mode != "registered_confirmatory" or replication.mode != "registered_confirmatory":
        _fail("synthetic analyses cannot produce a study decision")
    if (
        primary.dataset_role != "primary"
        or replication.dataset_role != "external_replication"
        or primary.protocol_sha256 != expected_hash
        or replication.protocol_sha256 != expected_hash
    ):
        _fail("study analyses do not match the frozen primary/replication roles")
    assert primary.dataset_pass is not None and replication.dataset_pass is not None
    primary_directions = {item.direction for item in primary.directions if item.direction_pass}
    replication_directions = {
        item.direction for item in replication.directions if item.direction_pass
    }
    shared = primary_directions & replication_directions
    cross_direction: FlipDirection | None = None
    for direction in ("yes_to_no", "no_to_yes"):
        if direction in shared:
            cross_direction = direction
            break
    mechanism_admitted = primary.dataset_pass
    cross_allowed = mechanism_admitted and cross_direction is not None
    disposition: StudyDisposition
    if not mechanism_admitted:
        disposition = "retain_fail_closed_and_narrow_claim"
    elif cross_allowed:
        disposition = "bounded_cross_dataset_replication"
    else:
        disposition = "primary_dataset_bounded_admission"
    return StudyDecision(
        protocol_sha256=expected_hash,
        primary_analysis_sha256=primary.canonical_sha256(),
        replication_analysis_sha256=replication.canonical_sha256(),
        primary_pass=primary.dataset_pass,
        replication_pass=replication.dataset_pass,
        cross_dataset_direction=cross_direction,
        mechanism_admitted=mechanism_admitted,
        cross_dataset_claim_allowed=cross_allowed,
        disposition=disposition,
    )
