"""Crossed uncertainty and fail-closed decisions for the registered v3 study."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Annotated, Final, Literal, NoReturn

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    DirectionEvidence,
    V3ConfirmatoryProtocol,
    evaluate_cross_dataset_decision,
    holm_adjusted_p_values,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    PROTOCOL_SHA256,
    V3RuntimeError,
)
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN

INFERENCE_SCHEMA_VERSION: Final[Literal["p2-label-noise-shift-inference/1"]] = (
    "p2-label-noise-shift-inference/1"
)
BOOTSTRAP_SCHEMA_VERSION: Final[Literal["p2-v3-two-way-bootstrap/1"]] = (
    "p2-v3-two-way-bootstrap/1"
)
SIGN_FLIP_SCHEMA_VERSION: Final[Literal["p2-v3-paired-sign-flip/1"]] = (
    "p2-v3-paired-sign-flip/1"
)
CONFIDENCE_LEVEL: Final[float] = 0.95
QUANTILE_METHOD: Final[Literal["linear"]] = "linear"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
Direction = Literal["yes_to_no", "no_to_yes"]
DatasetRole = Literal["primary", "external_replication"]
Disposition = Literal["pass", "fail", "abstain"]


def _fail(message: str) -> NoReturn:
    raise V3RuntimeError(message)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _matrix(values: Sequence[Sequence[float]], *, label: str) -> np.ndarray:
    result = np.asarray(tuple(tuple(row) for row in values), dtype=np.float64)
    if (
        result.ndim != 2
        or result.shape[0] < 2
        or result.shape[1] < 2
        or not np.isfinite(result).all()
        or np.any(result < 0.0)
    ):
        _fail(f"{label} must be a finite non-negative seed-by-record matrix")
    return result


class BootstrapResult(_StrictFrozenModel):
    schema_version: Literal["p2-v3-two-way-bootstrap/1"] = BOOTSTRAP_SCHEMA_VERSION
    factors: tuple[Literal["corruption_seed", "evaluation_record"], ...]
    weighting: Literal["independent_multinomial_counts_product"]
    estimand: Literal["relative_net_corruption_effect_vs_prior_matched_clean_control"]
    resamples: int
    seed: int
    confidence_level: float
    quantile_method: Literal["linear"]
    point_estimate: float
    lower_bound: float
    upper_bound: float

    @model_validator(mode="after")
    def _result_is_valid(self) -> BootstrapResult:
        if self.factors != ("corruption_seed", "evaluation_record"):
            raise ValueError("bootstrap factors must retain canonical order")
        if not math.isclose(self.confidence_level, CONFIDENCE_LEVEL):
            raise ValueError("bootstrap confidence level must be 95%")
        if not all(
            math.isfinite(value)
            for value in (self.point_estimate, self.lower_bound, self.upper_bound)
        ) or self.lower_bound > self.upper_bound:
            raise ValueError("bootstrap result is non-finite or inverted")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def two_way_product_weight_bootstrap(
    *,
    corrupted_losses_by_seed: Sequence[Sequence[float]],
    control_losses_by_seed: Sequence[Sequence[float]],
    resamples: int,
    seed: int,
    batch_size: int = 64,
) -> BootstrapResult:
    """Bootstrap independent seed/record multinomial counts and their products."""

    corrupted = _matrix(corrupted_losses_by_seed, label="corrupted losses")
    control = _matrix(control_losses_by_seed, label="control losses")
    if corrupted.shape != control.shape:
        _fail("corrupted and control loss matrices must be paired")
    if resamples < 100 or batch_size <= 0:
        _fail("bootstrap requires at least 100 resamples and a positive batch size")
    control_mean = float(np.mean(control))
    if control_mean <= 0.0:
        _fail("relative bootstrap requires positive control loss")
    point = float((np.mean(corrupted) - control_mean) / control_mean)
    seed_count, record_count = corrupted.shape
    generator = np.random.default_rng(seed)
    estimates = np.empty(resamples, dtype=np.float64)
    seed_probabilities = np.full(seed_count, 1.0 / seed_count, dtype=np.float64)
    record_probabilities = np.full(record_count, 1.0 / record_count, dtype=np.float64)
    difference = corrupted - control
    cursor = 0
    while cursor < resamples:
        size = min(batch_size, resamples - cursor)
        seed_weights = generator.multinomial(
            seed_count, seed_probabilities, size=size
        ).astype(np.float64)
        record_weights = generator.multinomial(
            record_count, record_probabilities, size=size
        ).astype(np.float64)
        denominator = float(seed_count * record_count)
        control_boot = np.einsum(
            "bi,ij,bj->b", seed_weights, control, record_weights, optimize=True
        ) / denominator
        difference_boot = np.einsum(
            "bi,ij,bj->b", seed_weights, difference, record_weights, optimize=True
        ) / denominator
        if np.any(control_boot <= 0.0) or not np.isfinite(control_boot).all():
            _fail("bootstrap produced a non-positive control denominator")
        estimates[cursor : cursor + size] = difference_boot / control_boot
        cursor += size
    lower, upper = np.quantile(
        estimates,
        ((1.0 - CONFIDENCE_LEVEL) / 2.0, 1.0 - (1.0 - CONFIDENCE_LEVEL) / 2.0),
        method=QUANTILE_METHOD,
    )
    return BootstrapResult(
        factors=("corruption_seed", "evaluation_record"),
        weighting="independent_multinomial_counts_product",
        estimand="relative_net_corruption_effect_vs_prior_matched_clean_control",
        resamples=resamples,
        seed=seed,
        confidence_level=CONFIDENCE_LEVEL,
        quantile_method=QUANTILE_METHOD,
        point_estimate=point,
        lower_bound=float(lower),
        upper_bound=float(upper),
    )


class SignFlipResult(_StrictFrozenModel):
    schema_version: Literal["p2-v3-paired-sign-flip/1"] = SIGN_FLIP_SCHEMA_VERSION
    alternative: Literal["mean_seed_level_net_effect_greater_than_zero"]
    comparison: Literal["null_mean_greater_than_or_equal_observed"]
    correction: Literal["plus_one_exceedance"]
    replicate_count: int
    resamples: int
    seed: int
    observed_mean: float
    exceedance_count: int
    p_value: float = Field(ge=0.0, le=1.0)


def paired_sign_flip_test(
    effects: Sequence[float], *, resamples: int, seed: int
) -> SignFlipResult:
    """One-sided paired-seed randomization test with plus-one correction."""

    values = np.asarray(tuple(effects), dtype=np.float64)
    if values.ndim != 1 or values.size < 2 or not np.isfinite(values).all():
        _fail("sign-flip test requires at least two finite paired effects")
    if resamples < 100:
        _fail("sign-flip test requires at least 100 resamples")
    observed = float(np.mean(values))
    generator = np.random.default_rng(seed)
    exceedances = 0
    remaining = resamples
    while remaining:
        size = min(10_000, remaining)
        signs = generator.choice(np.asarray((-1.0, 1.0)), size=(size, values.size))
        null_means = np.mean(signs * values, axis=1)
        exceedances += int(np.count_nonzero(null_means >= observed))
        remaining -= size
    p_value = (exceedances + 1.0) / (resamples + 1.0)
    return SignFlipResult(
        alternative="mean_seed_level_net_effect_greater_than_zero",
        comparison="null_mean_greater_than_or_equal_observed",
        correction="plus_one_exceedance",
        replicate_count=values.size,
        resamples=resamples,
        seed=seed,
        observed_mean=observed,
        exceedance_count=exceedances,
        p_value=p_value,
    )


class SeedNetEffect(_StrictFrozenModel):
    dataset_id: str
    dataset_role: DatasetRole
    direction: Direction
    conditional_rate: float
    corruption_seed: int
    mutation_sha256: Sha256
    corrupted_model_sha256: Sha256
    prior_matched_control_model_sha256: Sha256
    reciprocal_control_model_sha256: Sha256
    corrupted_losses: tuple[float, ...]
    prior_matched_control_losses: tuple[float, ...]
    relative_net_effect: float
    mutation_reconciled: bool
    prior_match_reconciled: bool
    reciprocal_prevalence_reconciled: bool
    serialization_reconciled: bool

    @model_validator(mode="after")
    def _effect_is_derived(self) -> SeedNetEffect:
        corrupted = np.asarray(self.corrupted_losses, dtype=np.float64)
        control = np.asarray(self.prior_matched_control_losses, dtype=np.float64)
        if (
            corrupted.ndim != 1
            or corrupted.size < 2
            or control.shape != corrupted.shape
            or not np.isfinite(corrupted).all()
            or not np.isfinite(control).all()
            or np.any(corrupted < 0.0)
            or np.any(control < 0.0)
        ):
            raise ValueError("seed loss vectors must be finite, paired, and non-negative")
        mean_control = float(np.mean(control))
        expected = float((np.mean(corrupted) - mean_control) / mean_control)
        if not math.isclose(self.relative_net_effect, expected, abs_tol=1e-12):
            raise ValueError("seed net effect must be derived from paired record losses")
        return self

    @property
    def technical_controls_pass(self) -> bool:
        return all(
            (
                self.mutation_reconciled,
                self.prior_match_reconciled,
                self.reciprocal_prevalence_reconciled,
                self.serialization_reconciled,
            )
        )

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class DoseSummary(_StrictFrozenModel):
    direction: Direction
    conditional_rate: float
    replicate_count: int
    mean_relative_net_effect: float
    all_technical_controls_pass: bool


class DatasetDirectionAnalysis(_StrictFrozenModel):
    direction: Direction
    co_primary_rate: float
    replicate_count: int
    mean_relative_net_effect: float
    bootstrap: BootstrapResult
    sign_flip: SignFlipResult
    technical_controls_pass: bool
    prior_only_label_noise_admissions: int
    assumptions_pass: bool
    disposition: Disposition

    @model_validator(mode="after")
    def _disposition_is_derived(self) -> DatasetDirectionAnalysis:
        if not self.assumptions_pass:
            expected = "abstain"
        else:
            passed = (
                self.mean_relative_net_effect >= 0.05
                and self.bootstrap.lower_bound > 0.0
                and self.sign_flip.p_value <= 1.0
                and self.technical_controls_pass
                and self.prior_only_label_noise_admissions == 0
            )
            # Dataset-level p-values are Holm-corrected only after the IUT across datasets.
            expected = "pass" if passed else "fail"
        if self.disposition != expected:
            raise ValueError("dataset direction disposition is inconsistent")
        return self


class DatasetInference(_StrictFrozenModel):
    schema_version: Literal["p2-label-noise-shift-inference/1"] = INFERENCE_SCHEMA_VERSION
    protocol_sha256: Sha256 = PROTOCOL_SHA256
    dataset_id: str
    dataset_role: DatasetRole
    split_membership_sha256: Sha256
    dose_summaries: tuple[DoseSummary, ...]
    directions: tuple[DatasetDirectionAnalysis, DatasetDirectionAnalysis]

    @model_validator(mode="after")
    def _census_is_complete(self) -> DatasetInference:
        if tuple(item.direction for item in self.directions) != ("yes_to_no", "no_to_yes"):
            raise ValueError("dataset inference requires both directions in canonical order")
        expected_doses = tuple(
            (direction, rate)
            for direction in ("yes_to_no", "no_to_yes")
            for rate in (0.1, 0.2, 0.3)
        )
        if tuple((item.direction, item.conditional_rate) for item in self.dose_summaries) != (
            expected_doses
        ):
            raise ValueError("dataset inference requires the complete ordered dose grid")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def analyze_dataset(
    *,
    protocol: V3ConfirmatoryProtocol,
    dataset_id: str,
    dataset_role: DatasetRole,
    split_membership_sha256: str,
    seed_effects: Sequence[SeedNetEffect],
    prior_only_admissions: Mapping[Direction, int],
    assumptions_pass: Mapping[Direction, bool],
    bootstrap_resamples: int | None = None,
    sign_flip_resamples: int | None = None,
) -> DatasetInference:
    """Reconcile the full 6x50 grid before analyzing the 30% co-primary cells."""

    protocol = V3ConfirmatoryProtocol.model_validate(protocol.model_dump())
    if protocol.canonical_sha256() != PROTOCOL_SHA256:
        _fail("dataset inference accepts only the immutable v3.1 protocol")
    effects = tuple(SeedNetEffect.model_validate(item.model_dump()) for item in seed_effects)
    grouped: dict[tuple[Direction, float], list[SeedNetEffect]] = {
        (direction, rate): []
        for direction in ("yes_to_no", "no_to_yes")
        for rate in (0.1, 0.2, 0.3)
    }
    seen: set[tuple[Direction, float, int]] = set()
    for item in effects:
        key = (item.direction, item.conditional_rate, item.corruption_seed)
        if key in seen or (item.direction, item.conditional_rate) not in grouped:
            _fail("duplicate, replayed, or off-grid seed effect detected")
        seen.add(key)
        if item.dataset_id != dataset_id or item.dataset_role != dataset_role:
            _fail("seed effect provenance differs from the dataset analysis")
        grouped[(item.direction, item.conditional_rate)].append(item)
    expected_seeds = tuple(range(6101, 6151))
    summaries: list[DoseSummary] = []
    directions: list[DatasetDirectionAnalysis] = []
    for direction in ("yes_to_no", "no_to_yes"):
        for rate in (0.1, 0.2, 0.3):
            values = tuple(
                sorted(grouped[(direction, rate)], key=lambda item: item.corruption_seed)
            )
            if tuple(item.corruption_seed for item in values) != expected_seeds:
                _fail("each dose cell requires the complete frozen seed census")
            summaries.append(
                DoseSummary(
                    direction=direction,
                    conditional_rate=rate,
                    replicate_count=len(values),
                    mean_relative_net_effect=math.fsum(
                        item.relative_net_effect for item in values
                    )
                    / len(values),
                    all_technical_controls_pass=all(
                        item.technical_controls_pass for item in values
                    ),
                )
            )
        co_primary = tuple(
            sorted(grouped[(direction, 0.3)], key=lambda item: item.corruption_seed)
        )
        corrupted = tuple(item.corrupted_losses for item in co_primary)
        control = tuple(item.prior_matched_control_losses for item in co_primary)
        bootstrap = two_way_product_weight_bootstrap(
            corrupted_losses_by_seed=corrupted,
            control_losses_by_seed=control,
            resamples=bootstrap_resamples or protocol.inference.bootstrap_resamples,
            seed=protocol.inference.bootstrap_seed,
        )
        sign_flip = paired_sign_flip_test(
            tuple(item.relative_net_effect for item in co_primary),
            resamples=sign_flip_resamples or protocol.inference.hypothesis_test_resamples,
            seed=protocol.inference.hypothesis_test_seed,
        )
        controls_pass = all(item.technical_controls_pass for item in effects)
        assumption = assumptions_pass[direction]
        admissions = prior_only_admissions[direction]
        if not assumption:
            disposition: Disposition = "abstain"
        else:
            passed = (
                bootstrap.point_estimate >= protocol.inference.minimum_practical_effect
                and bootstrap.lower_bound > 0.0
                and controls_pass
                and admissions == 0
            )
            disposition = "pass" if passed else "fail"
        directions.append(
            DatasetDirectionAnalysis(
                direction=direction,
                co_primary_rate=0.3,
                replicate_count=50,
                mean_relative_net_effect=bootstrap.point_estimate,
                bootstrap=bootstrap,
                sign_flip=sign_flip,
                technical_controls_pass=controls_pass,
                prior_only_label_noise_admissions=admissions,
                assumptions_pass=assumption,
                disposition=disposition,
            )
        )
    return DatasetInference(
        dataset_id=dataset_id,
        dataset_role=dataset_role,
        split_membership_sha256=split_membership_sha256,
        dose_summaries=tuple(summaries),
        directions=tuple(directions),  # type: ignore[arg-type]
    )


class V3StudyDecision(_StrictFrozenModel):
    schema_version: Literal["p2-label-noise-shift-inference/1"] = INFERENCE_SCHEMA_VERSION
    protocol_sha256: Sha256 = PROTOCOL_SHA256
    primary_inference_sha256: Sha256
    replication_inference_sha256: Sha256
    iut_p_values: dict[Direction, float]
    holm_adjusted_p_values: dict[Direction, float]
    direction_dispositions: dict[Direction, Disposition]
    cross_dataset_claim_allowed: bool
    disposition: Literal["cross_dataset_admission", "fail_closed", "abstain"]

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def decide_study(
    *,
    protocol: V3ConfirmatoryProtocol,
    primary: DatasetInference,
    replication: DatasetInference,
) -> V3StudyDecision:
    """Apply dataset IUT then two-direction Holm without a rescue path."""

    protocol = V3ConfirmatoryProtocol.model_validate(protocol.model_dump())
    primary = DatasetInference.model_validate(primary.model_dump())
    replication = DatasetInference.model_validate(replication.model_dump())
    if (
        protocol.canonical_sha256() != PROTOCOL_SHA256
        or primary.dataset_role != "primary"
        or replication.dataset_role != "external_replication"
    ):
        _fail("study decision requires the registered protocol and dataset roles")
    primary_by_direction = {item.direction: item for item in primary.directions}
    replication_by_direction = {item.direction: item for item in replication.directions}
    iut: dict[Direction, float] = {
        direction: max(
            primary_by_direction[direction].sign_flip.p_value,
            replication_by_direction[direction].sign_flip.p_value,
        )
        for direction in ("yes_to_no", "no_to_yes")
    }
    adjusted_values = holm_adjusted_p_values(
        {str(key): value for key, value in iut.items()}
    )
    adjusted: dict[Direction, float] = {
        "yes_to_no": adjusted_values["yes_to_no"],
        "no_to_yes": adjusted_values["no_to_yes"],
    }
    evidence: list[DirectionEvidence] = []
    for direction in ("yes_to_no", "no_to_yes"):
        left = primary_by_direction[direction]
        right = replication_by_direction[direction]
        evidence.append(
            DirectionEvidence(
                direction=direction,
                net_effects={
                    primary.dataset_id: left.mean_relative_net_effect,
                    replication.dataset_id: right.mean_relative_net_effect,
                },
                bootstrap_lower_bounds={
                    primary.dataset_id: left.bootstrap.lower_bound,
                    replication.dataset_id: right.bootstrap.lower_bound,
                },
                dataset_p_values={
                    primary.dataset_id: left.sign_flip.p_value,
                    replication.dataset_id: right.sign_flip.p_value,
                },
                technical_controls_pass=(
                    left.technical_controls_pass and right.technical_controls_pass
                ),
                prior_only_label_noise_admissions=(
                    left.prior_only_label_noise_admissions
                    + right.prior_only_label_noise_admissions
                ),
                assumptions_pass=(left.assumptions_pass and right.assumptions_pass),
            )
        )
    decision = evaluate_cross_dataset_decision(tuple(evidence))
    dispositions: dict[Direction, Disposition] = {
        item.direction: item.disposition for item in decision.direction_decisions
    }
    observed_adjusted: dict[Direction, float] = {
        item.direction: item.holm_adjusted_p_value for item in decision.direction_decisions
    }
    if any(
        not math.isclose(observed_adjusted[key], adjusted[key], abs_tol=1e-15)
        for key in adjusted
    ):
        _fail("cross-dataset decision and independently computed Holm values disagree")
    return V3StudyDecision(
        primary_inference_sha256=primary.canonical_sha256(),
        replication_inference_sha256=replication.canonical_sha256(),
        iut_p_values=iut,
        holm_adjusted_p_values=adjusted,
        direction_dispositions=dispositions,
        cross_dataset_claim_allowed=decision.claim_allowed,
        disposition=decision.disposition,
    )
