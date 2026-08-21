"""Contracts and mathematical invariants for the prospective v3 design."""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.confirmatory_v3_design import (
    V3_DESIGN_SCHEMA_VERSION,
    V3DesignError,
    V3StudyDesign,
    clean_label_prior_match_weights,
    effective_positive_prior,
    load_v3_study_design,
    normal_approximation_power,
    reference_prior_standardized_log_loss,
    target_prior_from_odds_multiplier,
    verify_v3_predecessor,
)

_DESIGN_PATH = Path("configs/benchmark/p2_label_noise_shift_v3_design.json")
_EXPECTED_DESIGN_SHA256 = "1c9c6592112038ae5ee11d0ef91921172dc61873e4d20272902178003171bd25"


def _payload() -> dict[str, object]:
    value = json.loads(_DESIGN_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _validate(payload: dict[str, object]) -> V3StudyDesign:
    return V3StudyDesign.model_validate_json(json.dumps(payload))


def test_design_loads_with_stable_hash_and_verified_v2_predecessor() -> None:
    design = load_v3_study_design()

    verify_v3_predecessor(design)
    assert design.schema_version == V3_DESIGN_SCHEMA_VERSION
    assert design.status == "draft_outcome_blind_not_registered"
    assert design.canonical_sha256() == _EXPECTED_DESIGN_SHA256
    assert design.predecessor.registered_decision_unchanged
    assert not design.predecessor.cross_dataset_claim_allowed
    assert not design.predecessor.implementation_defects_detected


def test_new_confirmatory_datasets_are_fixed_and_v2_partitions_are_excluded() -> None:
    design = load_v3_study_design()

    assert tuple(item.dataset_id for item in design.new_datasets) == (
        "uci_default_of_credit_card_clients",
        "uci_online_shoppers_purchasing_intention",
    )
    assert tuple(item.role for item in design.new_datasets) == (
        "primary",
        "external_replication",
    )
    assert design.excluded_confirmation_datasets == (
        "telco_customer_churn",
        "uci_bank_marketing_additional_full",
    )
    assert all(item.license == "CC_BY_4_0" for item in design.new_datasets)
    assert all(
        item.snapshot_sha256_required_before_registration
        and item.no_confirmatory_outcome_previously_opened
        for item in design.new_datasets
    )
    assert design.governance.v2_opened_partitions_forbidden_for_v3_confirmation


def test_design_orthogonalizes_corruption_and_prior_shift() -> None:
    design = load_v3_study_design()

    assert design.corruption.directions == ("yes_to_no", "no_to_yes")
    assert design.corruption.conditional_rates == (0.1, 0.2, 0.3)
    assert design.corruption.corruption_seeds.values() == tuple(range(6101, 6151))
    assert design.orthogonalization.nuisance_control == (
        "clean_labels_effective_prior_matched_sample_weight"
    )
    assert design.orthogonalization.primary_contrast == (
        "corrupted_model_minus_prior_matched_clean_label_model"
    )
    assert design.orthogonalization.prevalence_preserving_control == (
        "reciprocal_matched_pair_flip"
    )
    assert design.prior_environments.odds_multipliers == (0.25, 1.0, 4.0)
    assert design.prior_environments.target_labels_hidden_from_estimators
    assert design.prior_environments.class_conditionals_preserved_by_construction


def test_models_are_fixed_and_secondary_sensitivity_cannot_rescue_primary() -> None:
    design = load_v3_study_design()

    assert design.models.primary_model == "logistic_regression"
    assert design.models.secondary_sensitivity_model == "hist_gradient_boosting"
    assert design.models.preprocessing_fit_on_training_only
    assert design.models.development_only_calibration
    assert design.models.sealed_labels_for_model_selection_forbidden
    assert design.models.hyperparameter_search_forbidden
    assert design.models.secondary_model_cannot_rescue_primary


def test_shift_baselines_and_fail_closed_assumption_policy_are_complete() -> None:
    design = load_v3_study_design()

    assert design.shift_estimators.baselines == (
        "unadjusted_v2_log_loss",
        "oracle_prior_ratio",
        "bbse",
        "mlls_em",
        "rlls",
    )
    assert design.shift_estimators.calibration == ("development_logit_intercept_slope")
    assert not design.shift_estimators.calibration_uses_sealed_labels
    assert design.shift_estimators.ill_conditioned_estimator_action == "abstain"
    assert design.shift_estimators.failed_label_shift_assumption_action == (
        "abstain_from_pure_label_shift_claim"
    )
    assert design.decision.assumption_failure_requires_abstention


def test_primary_endpoint_and_cross_dataset_decision_cannot_be_rescued() -> None:
    design = load_v3_study_design()

    assert design.endpoints.primary_metric == ("reference_prior_standardized_log_loss")
    assert design.endpoints.primary_estimand == (
        "relative_net_corruption_effect_vs_prior_matched_clean_control"
    )
    assert design.endpoints.reference_positive_prior == 0.5
    assert design.endpoints.minimum_practical_effect == 0.05
    assert design.decision.cross_dataset_claim_requires_same_direction_to_pass
    assert design.decision.primary_failure_cannot_be_rescued_by_secondary_metrics
    assert design.decision.estimator_comparison_cannot_change_construct_admission


def test_seed_census_meets_prospective_power_target() -> None:
    design = load_v3_study_design()
    planned = normal_approximation_power(
        replicate_count=design.inference.replicate_count_per_cell,
        standardized_effect=design.inference.target_standardized_effect,
        one_sided_alpha=design.inference.worst_case_direction_alpha,
    )

    assert planned == pytest.approx(0.9424375237070284)
    assert planned >= design.inference.target_power
    assert design.inference.cross_dataset_test == "intersection_union_max_p"
    assert design.inference.direction_multiplicity == "holm_two_directions"


def test_prior_odds_tilts_are_symmetric_on_the_log_odds_scale() -> None:
    source = 0.2
    low = target_prior_from_odds_multiplier(source, 0.25)
    neutral = target_prior_from_odds_multiplier(source, 1.0)
    high = target_prior_from_odds_multiplier(source, 4.0)

    def odds(value: float) -> float:
        return value / (1.0 - value)

    assert neutral == pytest.approx(source)
    assert odds(low) / odds(source) == pytest.approx(0.25)
    assert odds(high) / odds(source) == pytest.approx(4.0)
    assert math.log(odds(low) / odds(source)) == pytest.approx(-math.log(odds(high) / odds(source)))


def test_clean_label_weights_match_prior_without_mutating_labels() -> None:
    targets = (0, 0, 0, 1)
    original = tuple(targets)
    weights = clean_label_prior_match_weights(targets, target_positive_prior=0.6)

    assert targets == original
    assert all(value > 0.0 for value in weights)
    assert effective_positive_prior(targets, weights) == pytest.approx(0.6)


def test_reference_prior_score_is_invariant_to_class_prevalence_duplication() -> None:
    balanced_targets = (0, 0, 1, 1)
    balanced_probabilities = (0.1, 0.2, 0.8, 0.9)
    imbalanced_targets = (0, 0, 0, 0, 0, 0, 1, 1)
    imbalanced_probabilities = (0.1, 0.2, 0.1, 0.2, 0.1, 0.2, 0.8, 0.9)

    balanced = reference_prior_standardized_log_loss(balanced_targets, balanced_probabilities)
    imbalanced = reference_prior_standardized_log_loss(imbalanced_targets, imbalanced_probabilities)

    assert balanced == pytest.approx(imbalanced)


@pytest.mark.parametrize(
    ("section", "field", "replacement"),
    [
        ("governance", "v2_opened_partitions_forbidden_for_v3_confirmation", False),
        ("governance", "structured_internal_outcome_blind_audit_required", False),
        ("governance", "internal_audit_must_precede_registration", False),
        ("governance", "required_future_tag", "p2-label-noise-shift-factorial-v3"),
        ("governance", "target_outcomes_cannot_tune_design", False),
        ("models", "hyperparameter_search_forbidden", False),
        ("models", "secondary_model_cannot_rescue_primary", False),
        ("prior_environments", "target_labels_hidden_from_estimators", False),
        ("shift_estimators", "ill_conditioned_estimator_action", "clip"),
        ("endpoints", "primary_metric", "target_prior_log_loss"),
        ("inference", "replicate_count_per_cell", 30),
        ("inference", "no_early_stopping", False),
        ("decision", "cross_dataset_claim_requires_same_direction_to_pass", False),
        ("decision", "additional_grid_after_outcomes_forbidden", False),
    ],
)
def test_core_safeguards_cannot_be_relaxed(section: str, field: str, replacement: object) -> None:
    payload = _payload()
    nested = payload[section]
    assert isinstance(nested, dict)
    nested[field] = replacement

    with pytest.raises(ValidationError):
        _validate(payload)


def test_baseline_removal_and_partial_decision_rule_are_rejected() -> None:
    payload = _payload()
    estimators = payload["shift_estimators"]
    decision = payload["decision"]
    assert isinstance(estimators, dict)
    assert isinstance(decision, dict)
    baselines = estimators["baselines"]
    requirements = decision["direction_pass_requirements"]
    assert isinstance(baselines, list)
    assert isinstance(requirements, list)
    baselines.pop()
    requirements.pop()

    with pytest.raises(ValidationError):
        _validate(payload)


@pytest.mark.parametrize(
    ("section", "mutation"),
    [
        ("split_nonpositive", ("train_fraction", 0.0)),
        ("split_incomplete", ("train_fraction", 0.5)),
        ("split_wrong_policy", ("fractions", (0.5, 0.3, 0.2))),
        ("dataset_identity", ("role", "external_replication")),
        ("seed_range", ("last", 6149)),
        ("primary_parameters", ("drop_last", True)),
        ("secondary_parameters", ("drop_last", True)),
        ("corruption_directions", ("reverse", True)),
        ("corruption_rates", ("drop_last", True)),
        ("co_primary_rate", ("co_primary_rate", 0.2)),
        ("corruption_seed_namespace", ("shift", 1)),
        ("prior_odds_grid", ("reverse", True)),
        ("environment_seed_namespace", ("shift", 1)),
        ("reference_prior", ("reference_positive_prior", 0.4)),
        ("practical_effect", ("minimum_practical_effect", 0.04)),
        ("legacy_comparators", ("reverse", True)),
        ("secondary_metrics", ("drop_last", True)),
        ("inference_float", ("familywise_alpha", 0.04)),
        ("bootstrap_factors", ("reverse", True)),
    ],
)
def test_structural_design_deviations_fail_closed(
    section: str, mutation: tuple[str, object]
) -> None:
    payload = _payload()
    datasets = payload["new_datasets"]
    assert isinstance(datasets, list)
    first_dataset = datasets[0]
    assert isinstance(first_dataset, dict)
    corruption = payload["corruption"]
    environments = payload["prior_environments"]
    models = payload["models"]
    endpoints = payload["endpoints"]
    inference = payload["inference"]
    assert isinstance(corruption, dict)
    assert isinstance(environments, dict)
    assert isinstance(models, dict)
    assert isinstance(endpoints, dict)
    assert isinstance(inference, dict)
    key, value = mutation
    if section.startswith("split_"):
        split = first_dataset["split"]
        assert isinstance(split, dict)
        if key == "fractions":
            assert isinstance(value, tuple)
            train, development, sealed = value
            split.update(
                train_fraction=train,
                development_fraction=development,
                sealed_test_fraction=sealed,
            )
        else:
            split[key] = value
    elif section == "dataset_identity":
        first_dataset[key] = value
    elif section == "seed_range":
        seeds = corruption["corruption_seeds"]
        assert isinstance(seeds, dict)
        seeds[key] = value
    elif section in {"primary_parameters", "secondary_parameters"}:
        parameter_key = section
        parameters = models[parameter_key]
        assert isinstance(parameters, list)
        parameters.pop()
    elif section == "corruption_directions":
        directions = corruption["directions"]
        assert isinstance(directions, list)
        directions.reverse()
    elif section == "corruption_rates":
        rates = corruption["conditional_rates"]
        assert isinstance(rates, list)
        rates.pop()
    elif section == "co_primary_rate":
        corruption[key] = value
    elif section == "corruption_seed_namespace":
        seeds = corruption["corruption_seeds"]
        assert isinstance(seeds, dict)
        seeds.update(first=6102, last=6151)
    elif section == "prior_odds_grid":
        odds = environments["odds_multipliers"]
        assert isinstance(odds, list)
        odds.reverse()
    elif section == "environment_seed_namespace":
        seeds = environments["environment_seeds"]
        assert isinstance(seeds, dict)
        seeds.update(first=7102, last=7151)
    elif section in {
        "reference_prior",
        "practical_effect",
    }:
        endpoints[key] = value
    elif section in {"legacy_comparators", "secondary_metrics"}:
        values = endpoints[section]
        assert isinstance(values, list)
        if key == "reverse":
            values.reverse()
        else:
            values.pop()
    elif section == "inference_float":
        inference[key] = value
    else:
        factors = inference["bootstrap_factors"]
        assert isinstance(factors, list)
        factors.reverse()

    with pytest.raises(ValidationError):
        _validate(payload)


def test_v2_dataset_reuse_and_post_outcome_fields_are_rejected() -> None:
    payload = _payload()
    datasets = payload["new_datasets"]
    assert isinstance(datasets, list)
    first = datasets[0]
    assert isinstance(first, dict)
    first["dataset_id"] = "uci_bank_marketing_additional_full"
    payload["observed_v3_effect"] = 0.9

    with pytest.raises(ValidationError):
        _validate(payload)


def test_loader_and_math_helpers_fail_closed_on_invalid_inputs(tmp_path: Path) -> None:
    with pytest.raises(V3DesignError, match="cannot read"):
        load_v3_study_design(tmp_path / "missing.json")
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    with pytest.raises(V3DesignError, match="outcome-free contract"):
        load_v3_study_design(invalid)
    with pytest.raises(V3DesignError, match="at least two"):
        normal_approximation_power(
            replicate_count=1, standardized_effect=0.5, one_sided_alpha=0.025
        )
    with pytest.raises(V3DesignError, match="positive effect"):
        normal_approximation_power(
            replicate_count=50, standardized_effect=math.nan, one_sided_alpha=0.025
        )
    with pytest.raises(V3DesignError, match="alpha"):
        normal_approximation_power(replicate_count=50, standardized_effect=0.5, one_sided_alpha=1.0)
    with pytest.raises(V3DesignError, match="source prior"):
        target_prior_from_odds_multiplier(0.0, 1.0)
    with pytest.raises(V3DesignError, match="binary targets"):
        clean_label_prior_match_weights((0, 0), target_positive_prior=0.5)
    with pytest.raises(V3DesignError, match="aligned binary"):
        reference_prior_standardized_log_loss((0, 1), (0.2,))


def test_predecessor_verification_is_hermetic_and_detects_tampering(
    tmp_path: Path,
) -> None:
    design = load_v3_study_design()
    for relative_path in (
        design.predecessor.closeout_receipt_uri,
        design.predecessor.root_cause_summary_uri,
    ):
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(relative_path, destination)

    verify_v3_predecessor(design, root=tmp_path)
    audit_path = tmp_path / design.predecessor.root_cause_summary_uri
    audit_path.write_bytes(audit_path.read_bytes() + b"\n")
    with pytest.raises(V3DesignError, match="root-cause summary checksum"):
        verify_v3_predecessor(design, root=tmp_path)
