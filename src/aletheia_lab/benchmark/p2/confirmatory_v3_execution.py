"""Registered v3 dataset execution after immutable outcome-blind registration."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Annotated, Final, Literal, NoReturn

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import V3DatasetBinding
from aletheia_lab.benchmark.p2.confirmatory_v3_inference import (
    SeedNetEffect,
    paired_sign_flip_test,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    DatasetSplitReceipt,
    V3ConfirmatoryProtocol,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    PROTOCOL_SHA256,
    Direction,
    FittedProbabilities,
    PreparedRuntimeDataset,
    V3RuntimeError,
    apply_directional_corruption,
    build_prior_environment,
    fit_registered_model,
    labelled_targets_sha256,
    prepare_runtime_dataset,
    prior_match_sample_weights,
    reciprocal_control_targets,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_shift import (
    EstimatorName,
    MmdDiagnostic,
    classwise_mmd_diagnostic,
    estimate_label_shift,
    holm_adjust_all,
    reference_prior_standardized_log_loss,
    reference_prior_standardized_losses,
)
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN

EXECUTION_SCHEMA_VERSION: Final[Literal["p2-label-noise-shift-execution/1"]] = (
    "p2-label-noise-shift-execution/1"
)
REGISTERED_CORRUPTION_SEEDS: Final[tuple[int, ...]] = tuple(range(6101, 6151))
REGISTERED_ENVIRONMENT_SEEDS: Final[tuple[int, ...]] = tuple(range(7101, 7151))
REGISTERED_RATES: Final[tuple[float, ...]] = (0.1, 0.2, 0.3)
REGISTERED_DIRECTIONS: Final[tuple[Direction, ...]] = ("yes_to_no", "no_to_yes")
REGISTERED_ESTIMATORS: Final[tuple[EstimatorName, ...]] = (
    "unadjusted_v2",
    "oracle_prior_ratio",
    "bbse",
    "mlls_em",
    "rlls",
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
DatasetRole = Literal["primary", "external_replication"]


def _fail(message: str) -> NoReturn:
    raise V3RuntimeError(message)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ExecutionPlan(_StrictFrozenModel):
    mode: Literal["synthetic_conformance", "registered_execution"]
    corruption_seeds: tuple[int, ...]
    environment_seeds: tuple[int, ...]
    bootstrap_resamples: int
    sign_flip_resamples: int
    mmd_resamples: int
    include_sensitivity_model: bool

    @classmethod
    def registered(cls, protocol: V3ConfirmatoryProtocol) -> ExecutionPlan:
        protocol = V3ConfirmatoryProtocol.model_validate(protocol.model_dump())
        return cls(
            mode="registered_execution",
            corruption_seeds=REGISTERED_CORRUPTION_SEEDS,
            environment_seeds=REGISTERED_ENVIRONMENT_SEEDS,
            bootstrap_resamples=protocol.inference.bootstrap_resamples,
            sign_flip_resamples=protocol.inference.hypothesis_test_resamples,
            mmd_resamples=protocol.shift_estimators.diagnostic_resamples,
            include_sensitivity_model=True,
        )

    @classmethod
    def synthetic(
        cls,
        *,
        corruption_seeds: Sequence[int] = tuple(range(6101, 6151)),
        environment_seeds: Sequence[int] = (7101, 7102),
        bootstrap_resamples: int = 200,
        sign_flip_resamples: int = 500,
        mmd_resamples: int = 100,
    ) -> ExecutionPlan:
        return cls(
            mode="synthetic_conformance",
            corruption_seeds=tuple(corruption_seeds),
            environment_seeds=tuple(environment_seeds),
            bootstrap_resamples=bootstrap_resamples,
            sign_flip_resamples=sign_flip_resamples,
            mmd_resamples=mmd_resamples,
            include_sensitivity_model=False,
        )

    def validate_against(self, protocol: V3ConfirmatoryProtocol) -> None:
        if self.mode == "registered_execution" and self != ExecutionPlan.registered(protocol):
            _fail("registered execution plan differs from the immutable protocol")


class PriorShiftEstimatorEvidence(_StrictFrozenModel):
    dataset_id: str
    odds_multiplier: float
    environment_seed: int
    environment_sha256: Sha256
    estimator: EstimatorName
    estimate_sha256: Sha256
    status: Literal["ok", "abstain"]
    estimated_positive_prior: float | None
    oracle_positive_prior: float
    reference_prior_log_loss: float | None
    reason: str | None


class PriorOnlyControlSummary(_StrictFrozenModel):
    odds_multiplier: float
    replicate_count: int
    mean_relative_score_change: float
    raw_one_sided_p_value: float
    bonferroni_adjusted_p_value: float
    label_noise_admission: bool

    @model_validator(mode="after")
    def _admission_is_derived(self) -> PriorOnlyControlSummary:
        expected = (
            self.mean_relative_score_change >= 0.05
            and self.bonferroni_adjusted_p_value < 0.05
        )
        if self.label_noise_admission != expected:
            raise ValueError("prior-only admission must follow the frozen negative-control rule")
        return self


class SensitivityDoseSummary(_StrictFrozenModel):
    direction: Direction
    conditional_rate: float
    replicate_count: int
    mean_relative_net_effect: float
    sensitivity_only: Literal[True] = True
    can_rescue_primary: Literal[False] = False


class V3DatasetOutcome(_StrictFrozenModel):
    schema_version: Literal["p2-label-noise-shift-execution/1"] = EXECUTION_SCHEMA_VERSION
    protocol_sha256: Sha256 = PROTOCOL_SHA256
    dataset_id: str
    dataset_role: DatasetRole
    execution_mode: Literal["synthetic_conformance", "registered_execution"]
    corruption_replicate_count: int = Field(gt=0)
    environment_replicate_count: int = Field(gt=0)
    split_membership_sha256: Sha256
    runtime_split_sha256: Sha256
    preprocessor_sha256: Sha256
    output_columns_sha256: Sha256
    clean_primary_model_sha256: Sha256
    clean_roundtrip_model_sha256: Sha256
    clean_roundtrip_equal: Literal[True]
    seed_effects: tuple[SeedNetEffect, ...]
    prior_shift_evidence: tuple[PriorShiftEstimatorEvidence, ...]
    prior_only_controls: tuple[PriorOnlyControlSummary, ...]
    mmd_diagnostics: tuple[MmdDiagnostic, ...]
    mmd_holm_adjusted_p_values: dict[str, float]
    assumptions_pass: bool
    sensitivity_summaries: tuple[SensitivityDoseSummary, ...]

    @model_validator(mode="after")
    def _outcome_is_complete(self) -> V3DatasetOutcome:
        if len(self.seed_effects) != 6 * self.corruption_replicate_count:
            raise ValueError("primary seed-effect census is incomplete")
        if len(self.prior_shift_evidence) != 15 * self.environment_replicate_count:
            raise ValueError("shift-estimator census is incomplete")
        if tuple(item.odds_multiplier for item in self.prior_only_controls) != (0.25, 4.0):
            raise ValueError("prior-only control census is incomplete")
        if len(self.mmd_diagnostics) != 3:
            raise ValueError("MMD diagnostics require all three prior environments")
        if len(self.sensitivity_summaries) != 6:
            raise ValueError("sensitivity model requires all six dose summaries")
        if any(item.dataset_id != self.dataset_id for item in self.seed_effects):
            raise ValueError("seed-effect provenance differs from the outcome dataset")
        expected_sensitivity = tuple(
            (direction, rate)
            for direction in REGISTERED_DIRECTIONS
            for rate in REGISTERED_RATES
        )
        if tuple(
            (item.direction, item.conditional_rate)
            for item in self.sensitivity_summaries
        ) != expected_sensitivity:
            raise ValueError("sensitivity dose census is not canonical")
        if any(item.dataset_id != self.dataset_id for item in self.mmd_diagnostics):
            raise ValueError("MMD provenance differs from the outcome dataset")
        raw_mmd = {
            f"odds={multiplier:g}/class={item.class_label}": item.permutation_p_value
            for multiplier, diagnostic in zip(
                (0.25, 1.0, 4.0), self.mmd_diagnostics, strict=True
            )
            for item in diagnostic.classes
        }
        expected_mmd = holm_adjust_all(raw_mmd)
        if set(expected_mmd) != set(self.mmd_holm_adjusted_p_values) or any(
            not math.isclose(
                expected_mmd[key], self.mmd_holm_adjusted_p_values[key], abs_tol=1e-15
            )
            for key in expected_mmd
        ):
            raise ValueError("local MMD Holm report is not derived from its diagnostics")
        if self.assumptions_pass != all(value >= 0.05 for value in expected_mmd.values()):
            raise ValueError("local assumption disposition is not derived")
        if self.execution_mode == "registered_execution":
            if self.corruption_replicate_count != 50:
                raise ValueError("registered outcome requires 50 corruption replicates")
            if self.environment_replicate_count != 50:
                raise ValueError("registered outcome requires 50 environment replicates")
            if any(item.replicate_count != 50 for item in self.sensitivity_summaries):
                raise ValueError("registered sensitivity census requires 50 replicates")
            expected_effects = {
                (direction, rate, seed)
                for direction in REGISTERED_DIRECTIONS
                for rate in REGISTERED_RATES
                for seed in REGISTERED_CORRUPTION_SEEDS
            }
            observed_effects = {
                (item.direction, item.conditional_rate, item.corruption_seed)
                for item in self.seed_effects
            }
            if observed_effects != expected_effects:
                raise ValueError("registered seed-effect census is incomplete or replayed")
            expected_shift = {
                (multiplier, seed, estimator)
                for multiplier in (0.25, 1.0, 4.0)
                for seed in REGISTERED_ENVIRONMENT_SEEDS
                for estimator in REGISTERED_ESTIMATORS
            }
            observed_shift = {
                (item.odds_multiplier, item.environment_seed, item.estimator)
                for item in self.prior_shift_evidence
            }
            if observed_shift != expected_shift or any(
                item.dataset_id != self.dataset_id for item in self.prior_shift_evidence
            ):
                raise ValueError("registered shift-estimator census is incomplete or replayed")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def _partition_receipt(
    protocol: V3ConfirmatoryProtocol, dataset: V3DatasetBinding
) -> DatasetSplitReceipt:
    try:
        return next(item for item in protocol.dataset_splits if item.dataset_id == dataset.dataset_id)
    except StopIteration as exc:
        raise V3RuntimeError("dataset is outside the registered split census") from exc


def _fit(
    *,
    protocol: V3ConfirmatoryProtocol,
    prepared: PreparedRuntimeDataset,
    model_kind: Literal["logistic_regression", "hist_gradient_boosting"],
    training_role: str,
    targets: Sequence[int],
    sample_weights: Sequence[float] | None = None,
) -> FittedProbabilities:
    return fit_registered_model(
        protocol=protocol,
        dataset=prepared.binding,
        model_kind=model_kind,
        training_role=training_role,
        state=prepared.preprocessor,
        training_matrix=prepared.train_matrix,
        training_record_ids=prepared.train_record_ids,
        training_targets=targets,
        development_matrix=prepared.development_matrix,
        development_record_ids=prepared.development_record_ids,
        development_targets=prepared.development_targets,
        evaluation_matrix=prepared.sealed_matrix,
        evaluation_record_ids=prepared.sealed_record_ids,
        sample_weights=sample_weights,
    )


def _serialization_roundtrip_targets(
    record_ids: Sequence[str], targets: Sequence[int]
) -> tuple[int, ...]:
    payload: tuple[tuple[str, int], ...] = tuple(
        (record_id, int(target))
        for record_id, target in zip(record_ids, targets, strict=True)
    )
    reconstructed = tuple(target for _, target in payload)
    if labelled_targets_sha256(record_ids, reconstructed) != labelled_targets_sha256(
        record_ids, targets
    ):
        _fail("training target serialization roundtrip changed its hash")
    return reconstructed


def _losses(
    labels: Sequence[int], probabilities: Sequence[float]
) -> tuple[float, ...]:
    return reference_prior_standardized_losses(
        true_labels=labels,
        probabilities=probabilities,
    )


def _environment_evidence(
    *,
    protocol: V3ConfirmatoryProtocol,
    prepared: PreparedRuntimeDataset,
    clean: FittedProbabilities,
    plan: ExecutionPlan,
) -> tuple[
    tuple[PriorShiftEstimatorEvidence, ...],
    tuple[PriorOnlyControlSummary, ...],
    tuple[MmdDiagnostic, ...],
    dict[str, float],
]:
    neutral_score = reference_prior_standardized_log_loss(
        true_labels=prepared.sealed_targets,
        probabilities=clean.evaluation_probabilities,
    )
    evidence: list[PriorShiftEstimatorEvidence] = []
    effects_by_multiplier: dict[float, list[float]] = {0.25: [], 4.0: []}
    first_environment: dict[float, tuple[tuple[int, ...], object]] = {}
    for multiplier in (0.25, 1.0, 4.0):
        for environment_seed in plan.environment_seeds:
            indices, environment = build_prior_environment(
                dataset_id=prepared.binding.dataset_id,
                record_ids=prepared.sealed_record_ids,
                labels=prepared.sealed_targets,
                odds_multiplier=multiplier,
                environment_seed=environment_seed,
            )
            first_environment.setdefault(multiplier, (indices, environment))
            target_probabilities = tuple(clean.evaluation_probabilities[index] for index in indices)
            target_labels = tuple(prepared.sealed_targets[index] for index in indices)
            oracle_prior = sum(target_labels) / len(target_labels)
            if multiplier != 1.0:
                score = reference_prior_standardized_log_loss(
                    true_labels=target_labels,
                    probabilities=target_probabilities,
                )
                effects_by_multiplier[multiplier].append(
                    (score - neutral_score) / neutral_score
                )
            for estimator in REGISTERED_ESTIMATORS:
                estimate = estimate_label_shift(
                    estimator=estimator,
                    development_probabilities=clean.development_probabilities,
                    development_targets=prepared.development_targets,
                    target_probabilities=target_probabilities,
                    oracle_target_positive_prior=(
                        oracle_prior if estimator == "oracle_prior_ratio" else None
                    ),
                    bbse_condition_number_max=protocol.shift_estimators.bbse_condition_number_max,
                    mlls_max_iter=protocol.shift_estimators.mlls_max_iter,
                    mlls_tolerance=protocol.shift_estimators.mlls_tolerance,
                    rlls_l2_regularization=protocol.shift_estimators.rlls_l2_regularization,
                )
                estimator_score: float | None = (
                    reference_prior_standardized_log_loss(
                        true_labels=target_labels,
                        probabilities=estimate.adjusted_probabilities,
                    )
                    if estimate.status == "ok"
                    else None
                )
                evidence.append(
                    PriorShiftEstimatorEvidence(
                        dataset_id=prepared.binding.dataset_id,
                        odds_multiplier=multiplier,
                        environment_seed=environment_seed,
                        environment_sha256=environment.canonical_sha256(),
                        estimator=estimator,
                        estimate_sha256=estimate.canonical_sha256(),
                        status=estimate.status,
                        estimated_positive_prior=estimate.target_positive_prior,
                        oracle_positive_prior=oracle_prior,
                        reference_prior_log_loss=estimator_score,
                        reason=estimate.reason,
                    )
                )

    controls: list[PriorOnlyControlSummary] = []
    for multiplier in (0.25, 4.0):
        effects = effects_by_multiplier[multiplier]
        test = paired_sign_flip_test(
            effects,
            resamples=plan.sign_flip_resamples,
            seed=protocol.inference.hypothesis_test_seed,
        )
        mean_effect = math.fsum(effects) / len(effects)
        adjusted = min(1.0, test.p_value * 2.0)
        controls.append(
            PriorOnlyControlSummary(
                odds_multiplier=multiplier,
                replicate_count=len(effects),
                mean_relative_score_change=mean_effect,
                raw_one_sided_p_value=test.p_value,
                bonferroni_adjusted_p_value=adjusted,
                label_noise_admission=(mean_effect >= 0.05 and adjusted < 0.05),
            )
        )

    diagnostics: list[MmdDiagnostic] = []
    p_values: dict[str, float] = {}
    for multiplier in (0.25, 1.0, 4.0):
        indices, _ = first_environment[multiplier]
        target_matrix = prepared.sealed_matrix[np.asarray(indices, dtype=int)]
        target_ids = tuple(prepared.sealed_record_ids[index] for index in indices)
        target_labels = tuple(prepared.sealed_targets[index] for index in indices)
        diagnostic = classwise_mmd_diagnostic(
            dataset_id=prepared.binding.dataset_id,
            source_matrix=prepared.development_matrix,
            source_record_ids=prepared.development_record_ids,
            source_labels=prepared.development_targets,
            target_matrix=target_matrix,
            target_record_ids=target_ids,
            target_labels=target_labels,
            resamples=plan.mmd_resamples,
            seed=protocol.shift_estimators.diagnostic_seed,
        )
        diagnostics.append(diagnostic)
        for item in diagnostic.classes:
            p_values[f"odds={multiplier:g}/class={item.class_label}"] = (
                item.permutation_p_value
            )
    return tuple(evidence), tuple(controls), tuple(diagnostics), p_values


def _sensitivity_summaries(
    *,
    protocol: V3ConfirmatoryProtocol,
    prepared: PreparedRuntimeDataset,
    plan: ExecutionPlan,
) -> tuple[SensitivityDoseSummary, ...]:
    if not plan.include_sensitivity_model:
        return tuple(
            SensitivityDoseSummary(
                direction=direction,
                conditional_rate=rate,
                replicate_count=0,
                mean_relative_net_effect=0.0,
            )
            for direction in REGISTERED_DIRECTIONS
            for rate in REGISTERED_RATES
        )
    summaries: list[SensitivityDoseSummary] = []
    for direction in REGISTERED_DIRECTIONS:
        for rate in REGISTERED_RATES:
            first_targets, first_mutation = apply_directional_corruption(
                dataset_id=prepared.binding.dataset_id,
                record_ids=prepared.train_record_ids,
                clean_targets=prepared.train_targets,
                direction=direction,
                conditional_rate=rate,
                seed=plan.corruption_seeds[0],
            )
            target_prior = sum(first_targets) / len(first_targets)
            weights = prior_match_sample_weights(
                prepared.train_targets,
                target_positive_prior=target_prior,
            )
            control = _fit(
                protocol=protocol,
                prepared=prepared,
                model_kind="hist_gradient_boosting",
                training_role=f"sensitivity-prior-match-{direction}-{rate:g}",
                targets=prepared.train_targets,
                sample_weights=weights,
            )
            control_losses = _losses(
                prepared.sealed_targets, control.evaluation_probabilities
            )
            effects: list[float] = []
            for seed in plan.corruption_seeds:
                if seed == plan.corruption_seeds[0]:
                    mutated, mutation = first_targets, first_mutation
                else:
                    mutated, mutation = apply_directional_corruption(
                        dataset_id=prepared.binding.dataset_id,
                        record_ids=prepared.train_record_ids,
                        clean_targets=prepared.train_targets,
                        direction=direction,
                        conditional_rate=rate,
                        seed=seed,
                    )
                fitted = _fit(
                    protocol=protocol,
                    prepared=prepared,
                    model_kind="hist_gradient_boosting",
                    training_role=f"sensitivity-corrupted-{direction}-{rate:g}-{seed}",
                    targets=mutated,
                )
                losses = _losses(prepared.sealed_targets, fitted.evaluation_probabilities)
                mean_control = float(np.mean(control_losses))
                effects.append((float(np.mean(losses)) - mean_control) / mean_control)
                if mutation.mutated_targets_sha256 != fitted.training_targets_sha256:
                    _fail("sensitivity model target provenance does not reconcile")
            summaries.append(
                SensitivityDoseSummary(
                    direction=direction,
                    conditional_rate=rate,
                    replicate_count=len(effects),
                    mean_relative_net_effect=math.fsum(effects) / len(effects),
                )
            )
    return tuple(summaries)


def execute_v3_dataset(
    *,
    protocol: V3ConfirmatoryProtocol,
    dataset: V3DatasetBinding,
    frame: pd.DataFrame,
    plan: ExecutionPlan,
) -> V3DatasetOutcome:
    """Execute the complete registered matrix for one dataset in memory."""

    protocol = V3ConfirmatoryProtocol.model_validate(protocol.model_dump())
    dataset = V3DatasetBinding.model_validate(dataset.model_dump())
    plan = ExecutionPlan.model_validate(plan.model_dump())
    if protocol.canonical_sha256() != PROTOCOL_SHA256:
        _fail("execution accepts only the immutable v3.1 protocol")
    plan.validate_against(protocol)
    if (
        plan.mode == "registered_execution"
        and tuple(plan.corruption_seeds) != REGISTERED_CORRUPTION_SEEDS
    ):
        _fail("v3 dataset inference requires all 50 registered corruption seeds")
    split_receipt = _partition_receipt(protocol, dataset)
    prepared = prepare_runtime_dataset(
        protocol=protocol,
        dataset=dataset,
        split_receipt=split_receipt,
        frame=frame,
    )
    clean = _fit(
        protocol=protocol,
        prepared=prepared,
        model_kind="logistic_regression",
        training_role="clean-reference",
        targets=prepared.train_targets,
    )
    roundtrip_targets = _serialization_roundtrip_targets(
        prepared.train_record_ids, prepared.train_targets
    )
    clean_roundtrip = _fit(
        protocol=protocol,
        prepared=prepared,
        model_kind="logistic_regression",
        training_role="clean-reference",
        targets=roundtrip_targets,
    )
    if clean_roundtrip.fitted_model_sha256 != clean.fitted_model_sha256:
        _fail("clean serialization roundtrip changed the fitted model")

    seed_effects: list[SeedNetEffect] = []
    for direction in REGISTERED_DIRECTIONS:
        for rate in REGISTERED_RATES:
            first_mutated, first_receipt = apply_directional_corruption(
                dataset_id=dataset.dataset_id,
                record_ids=prepared.train_record_ids,
                clean_targets=prepared.train_targets,
                direction=direction,
                conditional_rate=rate,
                seed=plan.corruption_seeds[0],
            )
            target_prior = sum(first_mutated) / len(first_mutated)
            weights = prior_match_sample_weights(
                prepared.train_targets,
                target_positive_prior=target_prior,
            )
            prior_matched = _fit(
                protocol=protocol,
                prepared=prepared,
                model_kind="logistic_regression",
                training_role=f"prior-matched-clean-{direction}-{rate:g}",
                targets=prepared.train_targets,
                sample_weights=weights,
            )
            control_losses = _losses(
                prepared.sealed_targets, prior_matched.evaluation_probabilities
            )
            effective_prior = float(
                np.sum(np.asarray(weights) * np.asarray(prepared.train_targets))
                / np.sum(weights)
            )
            for seed in plan.corruption_seeds:
                if seed == plan.corruption_seeds[0]:
                    mutated, mutation = first_mutated, first_receipt
                else:
                    mutated, mutation = apply_directional_corruption(
                        dataset_id=dataset.dataset_id,
                        record_ids=prepared.train_record_ids,
                        clean_targets=prepared.train_targets,
                        direction=direction,
                        conditional_rate=rate,
                        seed=seed,
                    )
                corrupted = _fit(
                    protocol=protocol,
                    prepared=prepared,
                    model_kind="logistic_regression",
                    training_role=f"corrupted-{direction}-{rate:g}-{seed}",
                    targets=mutated,
                )
                reciprocal_targets, selected_source, selected_opposite = (
                    reciprocal_control_targets(
                        dataset_id=dataset.dataset_id,
                        record_ids=prepared.train_record_ids,
                        clean_targets=prepared.train_targets,
                        direction=direction,
                        conditional_rate=rate,
                        seed=seed,
                    )
                )
                reciprocal = _fit(
                    protocol=protocol,
                    prepared=prepared,
                    model_kind="logistic_regression",
                    training_role=f"reciprocal-{direction}-{rate:g}-{seed}",
                    targets=reciprocal_targets,
                )
                corrupted_losses = _losses(
                    prepared.sealed_targets, corrupted.evaluation_probabilities
                )
                mean_control = float(np.mean(control_losses))
                relative = (float(np.mean(corrupted_losses)) - mean_control) / mean_control
                seed_effects.append(
                    SeedNetEffect(
                        dataset_id=dataset.dataset_id,
                        dataset_role=dataset.role,
                        direction=direction,
                        conditional_rate=rate,
                        corruption_seed=seed,
                        mutation_sha256=mutation.canonical_sha256(),
                        corrupted_model_sha256=corrupted.fitted_model_sha256,
                        prior_matched_control_model_sha256=(
                            prior_matched.fitted_model_sha256
                        ),
                        reciprocal_control_model_sha256=reciprocal.fitted_model_sha256,
                        corrupted_losses=corrupted_losses,
                        prior_matched_control_losses=control_losses,
                        relative_net_effect=relative,
                        mutation_reconciled=(
                            mutation.mutated_targets_sha256
                            == corrupted.training_targets_sha256
                        ),
                        prior_match_reconciled=math.isclose(
                            effective_prior, target_prior, abs_tol=1e-12
                        ),
                        reciprocal_prevalence_reconciled=(
                            sum(reciprocal_targets) == sum(prepared.train_targets)
                            and len(selected_source) == len(selected_opposite)
                            and reciprocal.training_targets_sha256
                            == labelled_targets_sha256(
                                prepared.train_record_ids, reciprocal_targets
                            )
                        ),
                        serialization_reconciled=(
                            clean_roundtrip.fitted_model_sha256
                            == clean.fitted_model_sha256
                        ),
                    )
                )

    shift_evidence, prior_controls, diagnostics, mmd_p_values = _environment_evidence(
        protocol=protocol,
        prepared=prepared,
        clean=clean,
        plan=plan,
    )
    # Dataset-local values are reported; the final closeout recomputes the registered
    # classes-crossed-with-datasets Holm family before making an assumption decision.
    local_mmd_adjusted = holm_adjust_all(mmd_p_values)
    assumptions_pass = all(value >= 0.05 for value in local_mmd_adjusted.values())
    sensitivity = _sensitivity_summaries(
        protocol=protocol,
        prepared=prepared,
        plan=plan,
    )
    return V3DatasetOutcome(
        dataset_id=dataset.dataset_id,
        dataset_role=dataset.role,
        execution_mode=plan.mode,
        corruption_replicate_count=len(plan.corruption_seeds),
        environment_replicate_count=len(plan.environment_seeds),
        split_membership_sha256=split_receipt.membership_sha256,
        runtime_split_sha256=prepared.split.canonical_sha256(),
        preprocessor_sha256=prepared.preprocessor.canonical_sha256(),
        output_columns_sha256=prepared.preprocessor.output_columns_sha256,
        clean_primary_model_sha256=clean.fitted_model_sha256,
        clean_roundtrip_model_sha256=clean_roundtrip.fitted_model_sha256,
        clean_roundtrip_equal=True,
        seed_effects=tuple(seed_effects),
        prior_shift_evidence=shift_evidence,
        prior_only_controls=prior_controls,
        mmd_diagnostics=diagnostics,
        mmd_holm_adjusted_p_values=local_mmd_adjusted,
        assumptions_pass=assumptions_pass,
        sensitivity_summaries=sensitivity,
    )
