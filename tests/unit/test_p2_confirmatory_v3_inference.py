"""Adversarial tests for v3 crossed inference and cross-dataset admission."""

from __future__ import annotations

import math

import numpy as np
import pytest

from aletheia_lab.benchmark.p2.confirmatory_v3_inference import (
    SeedNetEffect,
    V3RuntimeError,
    analyze_dataset,
    decide_study,
    paired_sign_flip_test,
    two_way_product_weight_bootstrap,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    load_v3_confirmatory_protocol,
)

_PRIMARY = "uci_default_of_credit_card_clients"
_REPLICATION = "uci_online_shoppers_purchasing_intention"


def test_two_way_bootstrap_is_deterministic_and_recovers_known_effect() -> None:
    control = np.ones((10, 20), dtype=float)
    corrupted = np.full((10, 20), 1.1, dtype=float)
    first = two_way_product_weight_bootstrap(
        corrupted_losses_by_seed=corrupted,
        control_losses_by_seed=control,
        resamples=200,
        seed=271829,
    )
    second = two_way_product_weight_bootstrap(
        corrupted_losses_by_seed=corrupted,
        control_losses_by_seed=control,
        resamples=200,
        seed=271829,
    )
    assert first == second
    assert first.point_estimate == pytest.approx(0.1)
    assert first.lower_bound == pytest.approx(0.1)
    assert first.upper_bound == pytest.approx(0.1)


def test_two_way_bootstrap_resamples_both_factors_and_rejects_misalignment() -> None:
    rng = np.random.default_rng(5)
    control = rng.uniform(0.2, 1.2, size=(8, 15))
    corrupted = control + rng.normal(0.08, 0.03, size=(8, 15))
    result = two_way_product_weight_bootstrap(
        corrupted_losses_by_seed=corrupted,
        control_losses_by_seed=control,
        resamples=300,
        seed=11,
    )
    assert result.factors == ("corruption_seed", "evaluation_record")
    assert result.lower_bound < result.point_estimate < result.upper_bound
    with pytest.raises(V3RuntimeError, match="paired"):
        two_way_product_weight_bootstrap(
            corrupted_losses_by_seed=corrupted[:, :-1],
            control_losses_by_seed=control,
            resamples=100,
            seed=1,
        )


def test_sign_flip_uses_plus_one_and_is_deterministic() -> None:
    effects = tuple(0.1 + index / 1000 for index in range(50))
    first = paired_sign_flip_test(effects, resamples=1000, seed=161804)
    second = paired_sign_flip_test(effects, resamples=1000, seed=161804)
    assert first == second
    assert first.p_value == (first.exceedance_count + 1) / 1001
    assert first.p_value > 0.0
    assert first.p_value < 0.05


def _seed_effects(
    dataset_id: str,
    role: str,
    *,
    co_primary_effect: float,
    controls: bool = True,
) -> tuple[SeedNetEffect, ...]:
    output: list[SeedNetEffect] = []
    for direction_index, direction in enumerate(("yes_to_no", "no_to_yes")):
        for rate in (0.1, 0.2, 0.3):
            base_effect = co_primary_effect * rate / 0.3
            for seed in range(6101, 6151):
                effect = base_effect + (seed - 6125.5) * 1e-5
                control_losses = tuple(0.8 + index * 0.001 for index in range(20))
                corrupted_losses = tuple(value * (1.0 + effect) for value in control_losses)
                output.append(
                    SeedNetEffect(
                        dataset_id=dataset_id,
                        dataset_role=role,
                        direction=direction,
                        conditional_rate=rate,
                        corruption_seed=seed,
                        mutation_sha256=(f"{direction_index + 1:x}" * 64),
                        corrupted_model_sha256="a" * 64,
                        prior_matched_control_model_sha256="b" * 64,
                        reciprocal_control_model_sha256="c" * 64,
                        corrupted_losses=corrupted_losses,
                        prior_matched_control_losses=control_losses,
                        relative_net_effect=(
                            (np.mean(corrupted_losses) - np.mean(control_losses))
                            / np.mean(control_losses)
                        ),
                        mutation_reconciled=controls,
                        prior_match_reconciled=True,
                        reciprocal_prevalence_reconciled=True,
                        serialization_reconciled=True,
                    )
                )
    return tuple(output)


def _analysis(
    dataset_id: str,
    role: str,
    *,
    effect: float,
    controls: bool = True,
    assumptions: bool = True,
    admissions: int = 0,
):
    protocol = load_v3_confirmatory_protocol()
    return analyze_dataset(
        protocol=protocol,
        dataset_id=dataset_id,
        dataset_role=role,
        split_membership_sha256="d" * 64,
        seed_effects=_seed_effects(
            dataset_id,
            role,
            co_primary_effect=effect,
            controls=controls,
        ),
        prior_only_admissions={"yes_to_no": admissions, "no_to_yes": admissions},
        assumptions_pass={"yes_to_no": assumptions, "no_to_yes": assumptions},
        bootstrap_resamples=200,
        sign_flip_resamples=1000,
    )


def test_dataset_analysis_reconciles_all_six_cells_and_fifty_seeds() -> None:
    analysis = _analysis(_PRIMARY, "primary", effect=0.1)
    assert len(analysis.dose_summaries) == 6
    assert all(item.replicate_count == 50 for item in analysis.dose_summaries)
    assert tuple(item.direction for item in analysis.directions) == (
        "yes_to_no",
        "no_to_yes",
    )
    assert all(item.disposition == "pass" for item in analysis.directions)
    assert all(item.bootstrap.lower_bound > 0.05 for item in analysis.directions)


def test_dataset_analysis_rejects_missing_replayed_and_failed_controls() -> None:
    protocol = load_v3_confirmatory_protocol()
    effects = list(_seed_effects(_PRIMARY, "primary", co_primary_effect=0.1))
    with pytest.raises(V3RuntimeError, match="seed census"):
        analyze_dataset(
            protocol=protocol,
            dataset_id=_PRIMARY,
            dataset_role="primary",
            split_membership_sha256="d" * 64,
            seed_effects=effects[:-1],
            prior_only_admissions={"yes_to_no": 0, "no_to_yes": 0},
            assumptions_pass={"yes_to_no": True, "no_to_yes": True},
            bootstrap_resamples=100,
            sign_flip_resamples=100,
        )
    failed = _analysis(_PRIMARY, "primary", effect=0.1, controls=False)
    assert all(item.disposition == "fail" for item in failed.directions)


def test_cross_dataset_decision_requires_same_direction_in_both_datasets() -> None:
    protocol = load_v3_confirmatory_protocol()
    primary = _analysis(_PRIMARY, "primary", effect=0.1)
    replication = _analysis(_REPLICATION, "external_replication", effect=0.09)
    decision = decide_study(
        protocol=protocol,
        primary=primary,
        replication=replication,
    )
    assert decision.cross_dataset_claim_allowed
    assert decision.disposition == "cross_dataset_admission"
    assert set(decision.iut_p_values) == {"yes_to_no", "no_to_yes"}
    assert all(value < 0.05 for value in decision.holm_adjusted_p_values.values())


def test_replication_failure_cannot_be_rescued_by_strong_primary() -> None:
    protocol = load_v3_confirmatory_protocol()
    primary = _analysis(_PRIMARY, "primary", effect=0.2)
    replication = _analysis(_REPLICATION, "external_replication", effect=0.01)
    decision = decide_study(
        protocol=protocol,
        primary=primary,
        replication=replication,
    )
    assert not decision.cross_dataset_claim_allowed
    assert decision.disposition == "fail_closed"
    assert set(decision.direction_dispositions.values()) == {"fail"}


def test_assumption_failure_abstains_and_prior_only_admission_fails() -> None:
    protocol = load_v3_confirmatory_protocol()
    primary = _analysis(_PRIMARY, "primary", effect=0.1, assumptions=False)
    replication = _analysis(_REPLICATION, "external_replication", effect=0.1)
    abstained = decide_study(
        protocol=protocol,
        primary=primary,
        replication=replication,
    )
    assert not abstained.cross_dataset_claim_allowed
    assert abstained.disposition == "abstain"

    primary_admission = _analysis(_PRIMARY, "primary", effect=0.1, admissions=1)
    failed = decide_study(
        protocol=protocol,
        primary=primary_admission,
        replication=replication,
    )
    assert not failed.cross_dataset_claim_allowed
    assert failed.disposition == "fail_closed"


def test_seed_effect_derivation_rejects_fabricated_relative_effect() -> None:
    with pytest.raises(ValueError, match="derived"):
        SeedNetEffect(
            dataset_id=_PRIMARY,
            dataset_role="primary",
            direction="yes_to_no",
            conditional_rate=0.3,
            corruption_seed=6101,
            mutation_sha256="1" * 64,
            corrupted_model_sha256="a" * 64,
            prior_matched_control_model_sha256="b" * 64,
            reciprocal_control_model_sha256="c" * 64,
            corrupted_losses=(1.1, 1.1),
            prior_matched_control_losses=(1.0, 1.0),
            relative_net_effect=0.2,
            mutation_reconciled=True,
            prior_match_reconciled=True,
            reciprocal_prevalence_reconciled=True,
            serialization_reconciled=True,
        )


def test_point_effect_matches_loss_matrix_exactly() -> None:
    analysis = _analysis(_PRIMARY, "primary", effect=0.08)
    for direction in analysis.directions:
        assert math.isclose(
            direction.mean_relative_net_effect,
            direction.bootstrap.point_estimate,
            abs_tol=1e-15,
        )
