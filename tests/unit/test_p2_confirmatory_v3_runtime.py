"""Conformance tests for deterministic v3 execution primitives."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    load_v3_dataset_binding_manifest,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    load_v3_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    PreprocessorState,
    V3RuntimeError,
    apply_directional_corruption,
    apply_logit_calibration,
    build_prior_environment,
    fit_logit_calibration,
    fit_preprocessor,
    fit_registered_model,
    prior_match_sample_weights,
    reciprocal_control_targets,
    stabilize_numeric_evidence,
    transform_features,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_shift import (
    adjust_probabilities_for_prior,
    classwise_mmd_diagnostic,
    estimate_label_shift,
    holm_adjust_all,
    reference_prior_standardized_log_loss,
)


def _credit_binding():
    return load_v3_dataset_binding_manifest().datasets[0]


def _feature_frame(size: int = 8) -> pd.DataFrame:
    dataset = _credit_binding()
    values: dict[str, list[object]] = {}
    for column in dataset.categorical_features:
        values[column] = [1 + index % 2 for index in range(size)]
    values["EDUCATION"] = [0, 5, 6, 1, 2, 3, 4, 1][:size]
    values["MARRIAGE"] = [0, 1, 2, 3, 1, 2, 1, 2][:size]
    for offset, column in enumerate(dataset.numeric_features):
        values[column] = [float(offset + index) for index in range(size)]
    return pd.DataFrame(values, columns=dataset.analysis_features)


def test_preprocessor_canonicalizes_categories_and_uses_train_only_statistics() -> None:
    dataset = _credit_binding()
    train = _feature_frame()
    state = fit_preprocessor(dataset, train)
    matrix = transform_features(dataset=dataset, state=state, frame=train)

    assert "other" in state.category_vocabulary["EDUCATION"]
    assert "0" not in state.category_vocabulary["EDUCATION"]
    assert "other" in state.category_vocabulary["MARRIAGE"]
    assert tuple(state.category_vocabulary) == dataset.categorical_features
    assert state.output_columns[-len(dataset.numeric_features) :] == dataset.numeric_features
    assert matrix.dtype == np.float64
    assert matrix.shape == (len(train), len(state.output_columns))
    numeric = matrix[:, -len(dataset.numeric_features) :]
    assert np.allclose(np.mean(numeric, axis=0), 0.0, atol=1e-12)
    assert np.allclose(np.std(numeric, axis=0), 1.0, atol=1e-12)


def test_unknown_evaluation_category_is_all_zero_without_vocabulary_mutation() -> None:
    dataset = _credit_binding()
    train = _feature_frame()
    state = fit_preprocessor(dataset, train)
    evaluation = train.iloc[:1].copy()
    evaluation.loc[0, "SEX"] = 999

    matrix = transform_features(dataset=dataset, state=state, frame=evaluation)
    start = 0
    width = len(state.category_vocabulary["SEX"])
    assert np.array_equal(matrix[0, start : start + width], np.zeros(width))
    assert state == fit_preprocessor(dataset, train)


def test_preprocessor_fails_closed_on_schema_missing_and_nonfinite_values() -> None:
    dataset = _credit_binding()
    frame = _feature_frame()
    with pytest.raises(V3RuntimeError, match="manifest order"):
        fit_preprocessor(dataset, frame.drop(columns=dataset.numeric_features[0]))
    frame.loc[0, dataset.numeric_features[0]] = math.inf
    with pytest.raises(V3RuntimeError, match="finite"):
        fit_preprocessor(dataset, frame)


def test_directional_corruption_is_order_independent_and_exact() -> None:
    record_ids = tuple(f"row-{index:03d}" for index in range(100))
    targets = tuple([0] * 60 + [1] * 40)
    mutated, receipt = apply_directional_corruption(
        dataset_id="dataset",
        record_ids=record_ids,
        clean_targets=targets,
        direction="yes_to_no",
        conditional_rate=0.3,
        seed=6101,
    )
    reversed_ids = tuple(reversed(record_ids))
    reversed_targets = tuple(reversed(targets))
    _, reversed_receipt = apply_directional_corruption(
        dataset_id="dataset",
        record_ids=reversed_ids,
        clean_targets=reversed_targets,
        direction="yes_to_no",
        conditional_rate=0.3,
        seed=6101,
    )

    assert receipt.mutation_count == 12
    assert sum(targets) - sum(mutated) == 12
    assert receipt.selected_record_ids == reversed_receipt.selected_record_ids
    assert receipt.achieved_conditional_rate == 0.3


def test_directional_corruption_changes_with_seed_and_rejects_duplicate_ids() -> None:
    record_ids = tuple(f"row-{index:03d}" for index in range(100))
    targets = tuple([0] * 60 + [1] * 40)
    first = apply_directional_corruption(
        dataset_id="dataset",
        record_ids=record_ids,
        clean_targets=targets,
        direction="no_to_yes",
        conditional_rate=0.2,
        seed=6101,
    )[1]
    second = apply_directional_corruption(
        dataset_id="dataset",
        record_ids=record_ids,
        clean_targets=targets,
        direction="no_to_yes",
        conditional_rate=0.2,
        seed=6102,
    )[1]
    assert first.selected_record_ids != second.selected_record_ids
    with pytest.raises(V3RuntimeError, match="unique"):
        apply_directional_corruption(
            dataset_id="dataset",
            record_ids=("same", "same"),
            clean_targets=(0, 1),
            direction="yes_to_no",
            conditional_rate=0.3,
            seed=1,
        )


def test_prior_match_weights_reproduce_target_prevalence_and_mean_one() -> None:
    targets = tuple([0] * 80 + [1] * 20)
    weights = np.asarray(
        prior_match_sample_weights(targets, target_positive_prior=0.38), dtype=float
    )
    labels = np.asarray(targets, dtype=float)
    assert np.mean(weights) == pytest.approx(1.0)
    assert np.sum(weights * labels) / np.sum(weights) == pytest.approx(0.38)


def test_reciprocal_control_preserves_prevalence_and_reports_cap_by_selection_size() -> None:
    record_ids = tuple(f"row-{index:03d}" for index in range(100))
    targets = tuple([0] * 80 + [1] * 20)
    controlled, source, opposite = reciprocal_control_targets(
        dataset_id="dataset",
        record_ids=record_ids,
        clean_targets=targets,
        direction="no_to_yes",
        conditional_rate=0.3,
        seed=6101,
    )
    assert sum(controlled) == sum(targets)
    assert len(source) == len(opposite) == 20
    assert not set(source) & set(opposite)


def test_prior_environment_neutral_is_identity_and_shifted_sampling_is_reproducible() -> None:
    record_ids = tuple(f"row-{index:03d}" for index in range(100))
    targets = tuple([0] * 80 + [1] * 20)
    neutral_indices, neutral = build_prior_environment(
        dataset_id="dataset",
        record_ids=record_ids,
        labels=targets,
        odds_multiplier=1.0,
        environment_seed=7101,
    )
    first_indices, first = build_prior_environment(
        dataset_id="dataset",
        record_ids=record_ids,
        labels=targets,
        odds_multiplier=4.0,
        environment_seed=7101,
    )
    second_indices, second = build_prior_environment(
        dataset_id="dataset",
        record_ids=record_ids,
        labels=targets,
        odds_multiplier=4.0,
        environment_seed=7101,
    )

    assert neutral_indices == tuple(range(100))
    assert neutral.sampled_record_ids == record_ids
    assert first_indices == second_indices
    assert first == second
    assert first.target_positive_count == 50
    assert len(set(first.sampled_record_ids)) < len(first.sampled_record_ids)


def test_calibration_is_deterministic_and_reduces_development_log_loss() -> None:
    raw = np.linspace(0.05, 0.95, 200)
    targets = (raw > 0.55).astype(int)
    # Make both classes nonseparable in logit space to avoid infinite calibration.
    targets[::9] = 1 - targets[::9]
    calibration = fit_logit_calibration(raw, targets)
    calibrated = np.asarray(apply_logit_calibration(raw, calibration))
    raw_loss = -np.mean(targets * np.log(raw) + (1 - targets) * np.log1p(-raw))
    calibrated_loss = -np.mean(
        targets * np.log(calibrated) + (1 - targets) * np.log1p(-calibrated)
    )
    assert calibration == fit_logit_calibration(raw, targets)
    assert calibrated_loss <= raw_loss
    assert calibration.gradient_infinity_norm <= 1e-8


def _state_for_model() -> PreprocessorState:
    from aletheia_lab.benchmark.p2.canonical import canonical_sha256
    from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import PREPROCESSOR_SCHEMA_VERSION

    output_columns = ("x1", "x2")
    return PreprocessorState(
        dataset_id=_credit_binding().dataset_id,
        categorical_columns=(),
        numeric_columns=("x1", "x2"),
        category_vocabulary={},
        numeric_means=(0.0, 0.0),
        numeric_scales=(1.0, 1.0),
        output_columns=output_columns,
        output_columns_sha256=canonical_sha256(
            {
                "schema_version": PREPROCESSOR_SCHEMA_VERSION,
                "columns": output_columns,
            }
        ),
    )


def test_registered_model_is_deterministic_and_binds_prior_weights() -> None:
    state = _state_for_model()
    protocol = load_v3_confirmatory_protocol()
    dataset = _credit_binding()
    rng = np.random.default_rng(11)
    train = rng.normal(size=(200, 2))
    train_targets = (train[:, 0] + 0.4 * train[:, 1] + rng.normal(scale=0.7, size=200) > 0).astype(int)
    development = rng.normal(size=(100, 2))
    development_targets = (
        development[:, 0]
        + 0.4 * development[:, 1]
        + rng.normal(scale=0.7, size=100)
        > 0
    ).astype(int)
    evaluation = rng.normal(size=(80, 2))
    train_ids = tuple(f"train-{index}" for index in range(200))
    development_ids = tuple(f"development-{index}" for index in range(100))
    evaluation_ids = tuple(f"evaluation-{index}" for index in range(80))
    weights = prior_match_sample_weights(train_targets, target_positive_prior=0.6)
    kwargs = {
        "protocol": protocol,
        "dataset": dataset,
        "model_kind": "logistic_regression",
        "training_role": "prior_matched_clean",
        "state": state,
        "training_matrix": train,
        "training_record_ids": train_ids,
        "training_targets": train_targets,
        "development_matrix": development,
        "development_record_ids": development_ids,
        "development_targets": development_targets,
        "evaluation_matrix": evaluation,
        "evaluation_record_ids": evaluation_ids,
        "sample_weights": weights,
    }
    first = fit_registered_model(**kwargs)  # type: ignore[arg-type]
    second = fit_registered_model(**kwargs)  # type: ignore[arg-type]
    assert first == second
    assert first.sample_weights_sha256 is not None
    assert first.evaluation_record_ids == evaluation_ids


def _shift_fixture() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(17)
    development_targets = np.asarray([0] * 700 + [1] * 300)
    development_probabilities = np.where(
        development_targets == 1,
        rng.beta(8, 2, size=1000),
        rng.beta(2, 8, size=1000),
    )
    target_labels = np.asarray([0] * 400 + [1] * 600)
    target_probabilities = np.where(
        target_labels == 1,
        rng.beta(8, 2, size=1000),
        rng.beta(2, 8, size=1000),
    )
    return development_probabilities, development_targets, target_probabilities


@pytest.mark.parametrize("estimator", ["bbse", "mlls_em", "rlls"])
def test_shift_estimators_recover_direction_and_are_reproducible(estimator: str) -> None:
    development, targets, target = _shift_fixture()
    first = estimate_label_shift(
        estimator=estimator,  # type: ignore[arg-type]
        development_probabilities=development,
        development_targets=targets,
        target_probabilities=target,
    )
    second = estimate_label_shift(
        estimator=estimator,  # type: ignore[arg-type]
        development_probabilities=development,
        development_targets=targets,
        target_probabilities=target,
    )
    assert first == second
    assert first.status == "ok"
    assert first.target_positive_prior is not None
    assert first.target_positive_prior > first.source_positive_prior


def test_bbse_abstains_on_singular_soft_confusion_without_clipping() -> None:
    targets = tuple([0] * 50 + [1] * 50)
    estimate = estimate_label_shift(
        estimator="bbse",
        development_probabilities=(0.5,) * 100,
        development_targets=targets,
        target_probabilities=(0.5,) * 100,
    )
    assert estimate.status == "abstain"
    assert estimate.adjusted_probabilities == ()
    assert estimate.reason is not None


def test_oracle_prior_adjustment_and_reference_score_are_well_defined() -> None:
    probabilities = (0.2, 0.3, 0.7, 0.8)
    adjusted = adjust_probabilities_for_prior(
        probabilities,
        source_positive_prior=0.5,
        target_positive_prior=0.8,
    )
    assert all(left < right for left, right in zip(probabilities, adjusted, strict=True))

    labels = (0, 0, 1, 1)
    base = reference_prior_standardized_log_loss(
        true_labels=labels, probabilities=probabilities
    )
    duplicated = reference_prior_standardized_log_loss(
        true_labels=(0, 0, 0, 0, 1, 1),
        probabilities=(0.2, 0.3, 0.2, 0.3, 0.7, 0.8),
    )
    assert base == pytest.approx(duplicated)


def test_classwise_mmd_is_deterministic_and_holm_family_is_complete() -> None:
    rng = np.random.default_rng(23)
    labels = np.asarray([0] * 40 + [1] * 40)
    source = np.column_stack((labels + rng.normal(scale=0.2, size=80), rng.normal(size=80)))
    target = source.copy()
    ids = tuple(f"row-{index}" for index in range(80))
    first = classwise_mmd_diagnostic(
        dataset_id="dataset",
        source_matrix=source,
        source_record_ids=ids,
        source_labels=labels,
        target_matrix=target,
        target_record_ids=tuple(f"target-{index}" for index in range(80)),
        target_labels=labels,
        resamples=100,
        seed=314160,
    )
    second = classwise_mmd_diagnostic(
        dataset_id="dataset",
        source_matrix=source,
        source_record_ids=ids,
        source_labels=labels,
        target_matrix=target,
        target_record_ids=tuple(f"target-{index}" for index in range(80)),
        target_labels=labels,
        resamples=100,
        seed=314160,
    )
    assert first == second
    adjusted = holm_adjust_all(
        {f"dataset/class-{item.class_label}": item.permutation_p_value for item in first.classes}
    )
    assert set(adjusted) == {"dataset/class-0", "dataset/class-1"}
    assert all(0.0 <= value <= 1.0 for value in adjusted.values())


def test_hash_sampling_golden_vectors_freeze_domain_and_counter_streams() -> None:
    record_ids = tuple(f"r{index}" for index in range(12))
    targets = (0,) * 7 + (1,) * 5
    yes = apply_directional_corruption(
        dataset_id="golden",
        record_ids=record_ids,
        clean_targets=targets,
        direction="yes_to_no",
        conditional_rate=0.4,
        seed=6101,
    )[1]
    no = apply_directional_corruption(
        dataset_id="golden",
        record_ids=record_ids,
        clean_targets=targets,
        direction="no_to_yes",
        conditional_rate=0.4,
        seed=6101,
    )[1]
    low_indices, low = build_prior_environment(
        dataset_id="golden",
        record_ids=record_ids,
        labels=targets,
        odds_multiplier=0.25,
        environment_seed=7101,
    )
    high_indices, high = build_prior_environment(
        dataset_id="golden",
        record_ids=record_ids,
        labels=targets,
        odds_multiplier=4.0,
        environment_seed=7101,
    )
    assert yes.selected_record_ids == ("r11", "r8")
    assert no.selected_record_ids == ("r1", "r6")
    assert yes.mutated_targets_sha256 == (
        "dc19061abd054a9d7064e76d4ceae8c6ec9510a1cd15daac80d35863d3295efb"
    )
    assert no.mutated_targets_sha256 == (
        "9f140fefb1454e4ed25d92aa7b81c7ded0e9adcefeefca49987fabdef09a19a7"
    )
    assert low_indices == (1, 5, 2, 4, 5, 0, 1, 0, 3, 1, 11, 7)
    assert high_indices == (1, 5, 2, 11, 7, 11, 8, 8, 9, 8, 10, 7)
    assert low.canonical_sha256() == (
        "40c11f79b09a806d2e8e73c3d88ff9650a2aee9e90bee8ad4f4143cc29ce5d93"
    )
    assert high.canonical_sha256() == (
        "9f78c7344fcc945a3b8707b2b09bdfcd582e34b436b27b26c429e6a944f2fcb5"
    )


def test_shift_estimator_golden_fixture_freezes_equations_and_class_order() -> None:
    development = (0.1, 0.3, 0.7, 0.9)
    targets = (0, 0, 1, 1)
    target = (0.2, 0.4, 0.8, 0.9)
    expected = {
        "unadjusted_v2": (0.5, None),
        "oracle_prior_ratio": (0.625, None),
        "bbse": (0.6250000000000001, None),
        "mlls_em": (0.7323163522783097, 53),
        "rlls": (0.6216216216216216, None),
    }
    expected_hashes = {
        "unadjusted_v2": "1b959e96600bd41101d2fe6026e9f48b3fd78739302cdb6477d368781eeb53e3",
        "oracle_prior_ratio": "d7bfe68bb11857a8d7a616ea2c7c7ef25f8846f47b7a78e7ac970dba94f0dd0a",
        "bbse": "f3fc7ea3d1a0e9bfaaf06f140df2b0090c62212cac9f49e1f2861d4fcf1e3f7b",
        "mlls_em": "b2a6628ec0e42d2ca2a4bfbf60188c25f8947daa7abd14c443efa48cf6ddb95c",
        "rlls": "e413d115f8c6a37b013646dcaab95d1c368dddda40ecdacd7e740fb945ffe598",
    }
    for estimator, (expected_prior, expected_iterations) in expected.items():
        result = estimate_label_shift(
            estimator=estimator,  # type: ignore[arg-type]
            development_probabilities=development,
            development_targets=targets,
            target_probabilities=target,
            oracle_target_positive_prior=(
                0.625 if estimator == "oracle_prior_ratio" else None
            ),
        )
        assert result.status == "ok"
        assert result.target_positive_prior == pytest.approx(expected_prior, abs=1e-11)
        assert result.iterations == expected_iterations
        assert result.canonical_sha256() == expected_hashes[estimator]


def test_calibration_golden_fixture_freezes_newton_solution() -> None:
    calibration = fit_logit_calibration(
        (0.1, 0.2, 0.3, 0.6, 0.7, 0.8),
        (0, 0, 1, 0, 1, 1),
    )
    assert calibration.intercept == pytest.approx(0.32561960840052373, abs=1e-11)
    assert calibration.slope == pytest.approx(1.1865688446812601, abs=1e-11)
    assert calibration.iterations == 4
    assert calibration.canonical_sha256() == (
        "525e00dd76b3b272be9b1b924de1ca68f3555f5a131a946b78830a745cc18389"
    )


def test_numeric_evidence_stabilization_removes_last_bit_backend_noise() -> None:
    values = (
        0.32561960840052373,
        1.1865688446812601,
        9.913111128921549e-15,
        0.6216216216216216,
        1.6666666666666672,
        0.2911392405063291,
    )
    for value in values:
        expected = stabilize_numeric_evidence(value)
        for direction in (-math.inf, math.inf):
            perturbed = value
            for _ in range(8):
                perturbed = math.nextafter(perturbed, direction)
            assert stabilize_numeric_evidence(perturbed) == expected

    assert math.copysign(1.0, stabilize_numeric_evidence(-0.0)) == 1.0
    with pytest.raises(V3RuntimeError, match="finite"):
        stabilize_numeric_evidence(math.inf)


def test_mmd_golden_fixture_freezes_statistic_permutation_and_holm() -> None:
    source = np.asarray(
        (
            (0.0, 0.0),
            (1.0, 0.0),
            (0.0, 1.0),
            (1.0, 1.0),
            (2.0, 0.0),
            (2.0, 1.0),
            (3.0, 0.0),
            (3.0, 1.0),
        )
    )
    target = source + np.asarray((0.1, -0.2))
    labels = (0, 0, 0, 0, 1, 1, 1, 1)
    diagnostic = classwise_mmd_diagnostic(
        dataset_id="golden",
        source_matrix=source,
        source_record_ids=tuple(f"s{index}" for index in range(8)),
        source_labels=labels,
        target_matrix=target,
        target_record_ids=tuple(f"t{index}" for index in range(8)),
        target_labels=labels,
        resamples=100,
        seed=314160,
    )
    assert diagnostic.classes[0].statistic == pytest.approx(-0.04190652316900073)
    assert diagnostic.classes[0].permutation_p_value == 57 / 101
    assert diagnostic.classes[1].statistic == pytest.approx(0.02403010577642939)
    assert diagnostic.classes[1].permutation_p_value == 41 / 101
    adjusted = holm_adjust_all(
        {f"c{item.class_label}": item.permutation_p_value for item in diagnostic.classes}
    )
    assert adjusted == {"c1": 82 / 101, "c0": 82 / 101}
    assert diagnostic.canonical_sha256() == (
        "2983b0d1cdcc06df73861e18bf736309d8aad33f47f48ca320648296092ee22b"
    )
