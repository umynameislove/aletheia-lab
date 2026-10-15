"""Exact, synthetic invariants for secondary comparison robustness."""

from __future__ import annotations

import itertools
import math
from collections import Counter

import pytest

from aletheia_lab.evaluation.diagnosis_comparison_robustness import (
    ComparisonRobustnessError,
    affine_weight_profile,
    automatic_label_stress,
    conditional_label_bounds,
    metric_semantics,
    pilot_reference_scenarios,
)
from aletheia_lab.evaluation.diagnosis_main_measurement_impact import ClaimUnit, ImpactFrame


def _frame(full_b1: int = 1, full_a3: int = 1) -> ImpactFrame:
    frame = ImpactFrame(family_ids=("family",))
    for condition, arm in itertools.product(("full", "missing_key", "noisy"), ("B1", "A3")):
        request = f"{condition}:{arm}"
        size = (full_b1 if arm == "B1" else full_a3) if condition == "full" else 1
        claims = {f"{request}:{index}" for index in range(size)}
        frame.output_claims[request] = claims
        frame.requests_by_cell[("family", condition, arm)] = request
        frame.output_losses[("family", condition, arm)] = {0.0: 0.0, 0.5: 0.0}
        for identity in sorted(claims):
            frame.units[identity] = ClaimUnit(
                identity, request, "family", arm, condition, "fully_supported", size, True
            )
    return frame


def _full_reference(frame: ImpactFrame) -> dict[str, str]:
    return {identity: "fully_supported" for identity in frame.units}


def test_identical_99_percent_agreement_and_confusion_can_have_opposite_contrasts() -> None:
    frame = _frame(full_a3=95)
    assert len(frame.units) == 100
    positive = _full_reference(frame)
    negative = _full_reference(frame)
    positive["full:B1:0"] = "unsupported"
    negative["full:A3:0"] = "unsupported"
    assert (
        Counter(positive.values())
        == Counter(negative.values())
        == {"fully_supported": 99, "unsupported": 1}
    )
    a = conditional_label_bounds(frame, (positive,), 0.0)
    b = conditional_label_bounds(frame, (negative,), 0.0)
    assert a["conditional_product_label_region"] == pytest.approx([1 / 3, 1 / 3])
    assert b["conditional_product_label_region"] == pytest.approx([-1 / 285, -1 / 285])
    assert a["sign_identified"] == "positive"
    assert b["sign_identified"] == "negative"


def test_disagreements_may_leave_primary_contrast_exactly_unchanged() -> None:
    frame = _frame()
    reference = dict.fromkeys(frame.units, "partially_supported")
    primary = conditional_label_bounds(frame, (reference,), 0.0)
    assert primary["conditional_product_label_region"] == [0.0, 0.0]
    assert primary["unseen_core_claims"] == 0


def test_empty_reference_gives_a_feasible_region_not_the_automatic_point() -> None:
    result = conditional_label_bounds(_frame(), (), 0.0)
    assert result["conditional_product_label_region"] == [-1.0, 1.0]
    assert result["reference_observed_core_claims"] == 0
    assert result["sign_identified"] == "unidentified"
    assert result["minimum_effect_0_05_forced"] is False


def test_reference_union_retains_overlap_and_harm_disagreement() -> None:
    frame = _frame()
    first = _full_reference(frame)
    second = first | {"full:B1:0": "partially_supported"}
    primary = conditional_label_bounds(frame, (first, second), 0.0)
    half = conditional_label_bounds(frame, (first, second), 0.5)
    assert primary["reference_observed_core_claims"] == 6
    assert primary["reference_disputed_core_claims"] == 1
    assert primary["reference_disputed_harm_core_claims"] == 0
    assert primary["conditional_product_label_region"] == [0.0, 0.0]
    assert half["reference_disputed_harm_core_claims"] == 1
    assert half["conditional_product_label_region"] == pytest.approx([0, 1 / 6])
    assert half["sampling_designs_pooled"] is False


def test_primary_and_half_regions_are_attainable_by_exhaustive_small_completions() -> None:
    frame = _frame()
    fixed = _full_reference(frame)
    del fixed["full:B1:0"], fixed["full:A3:0"]
    for weight in (0.0, 0.5, 1.0):
        extrema = conditional_label_bounds(frame, (fixed,), weight)[
            "conditional_product_label_region"
        ]
        effects = []
        for b1, a3 in itertools.product(
            ("fully_supported", "partially_supported", "unsupported", "contradicted"), repeat=2
        ):
            labels = fixed | {"full:B1:0": b1, "full:A3:0": a3}
            effects.append(
                conditional_label_bounds(frame, (labels,), weight)[
                    "conditional_product_label_region"
                ][0]
            )
        assert extrema == [min(effects), max(effects)]


def test_affine_curve_preserves_registered_endpoints_and_reports_small_sign_crossing() -> None:
    curve = affine_weight_profile(1 / 1152, -163 / 2880)
    assert curve["exploratory_grid"]["0.0"] == 1 / 1152
    assert curve["exploratory_grid"]["0.5"] == pytest.approx(-163 / 2880)
    assert curve["zero_crossing_in_unit_interval"] == pytest.approx(0.007552870090634441)
    assert curve["point_curve_reaches_0_05"] is False
    assert affine_weight_profile(-0.1, -0.2)["zero_crossing_in_unit_interval"] is None
    assert affine_weight_profile(0.1, 0.1)["zero_crossing_in_unit_interval"] is None


def test_pilot_scenario_range_is_not_a_population_bound_or_adjudication() -> None:
    frame = _frame()
    first = _full_reference(frame)
    second = first | {"full:B1:0": "unsupported"}
    result = pilot_reference_scenarios(frame, ("family",), first, second, 0.0)
    assert result["pilot_difference_estimator_scenario_range"] == pytest.approx([0, 1 / 3])
    assert result["disputed_claims"] == result["harm_disputed_claims"] == 1
    assert "not_population_bound" in result["scope"]


def test_adversarial_stress_respects_output_weights_and_strict_zero_target() -> None:
    frame = _frame()
    strict = automatic_label_stress(frame, 0.0, 0.0, strict=True)
    assert strict["minimum_changed_automatic_claim_labels"] == 1
    assert strict["hypothetical_attained_effect"] == pytest.approx(1 / 3)
    assert (
        automatic_label_stress(frame, 0.0, 0.0, strict=False)[
            "minimum_changed_automatic_claim_labels"
        ]
        == 0
    )
    assert (
        automatic_label_stress(frame, 0.0, 2.0, strict=False)[
            "minimum_changed_automatic_claim_labels"
        ]
        is None
    )


def test_mean_harm_dilution_changes_the_construct_not_the_label_truth() -> None:
    frame = _frame(full_b1=20)
    unit = frame.units["full:B1:0"]
    frame.units[unit.claim_id] = ClaimUnit(
        unit.claim_id,
        unit.request_id,
        unit.family_id,
        unit.arm,
        unit.condition,
        "unsupported",
        unit.output_claim_count,
        True,
    )
    frame.output_losses[("family", "full", "B1")] = {0.0: 0.05, 0.5: 0.05}
    result = metric_semantics(frame)
    assert result["automatic_mean_claim_harm_contrast"] == pytest.approx(1 / 60)
    assert result["supplementary_any_harm_output_contrast"] == pytest.approx(1 / 3)
    assert result["arm_scored_counts"]["B1"]["scored_outputs_with_any_automatic_harm"] == 1


def test_fixed_failed_output_is_not_imputed_or_removed() -> None:
    frame = _frame()
    identity = "full:A3:0"
    del frame.units[identity]
    frame.output_claims["full:A3"] = set()
    frame.output_losses[("family", "full", "A3")] = {0.0: 1.0, 0.5: 1.0}
    result = conditional_label_bounds(frame, (_full_reference(frame),), 0.0)
    assert result["conditional_product_label_region"] == pytest.approx([-1 / 3, -1 / 3])
    assert metric_semantics(frame)["supplementary_any_harm_output_contrast"] == pytest.approx(
        -1 / 3
    )


@pytest.mark.parametrize("weight", (-1.0, 1.1, math.inf, math.nan, True))
def test_invalid_weight_fails_closed(weight: float) -> None:
    with pytest.raises(ComparisonRobustnessError):
        conditional_label_bounds(_frame(), (), weight)


@pytest.mark.parametrize("reference", ({"foreign": "fully_supported"}, {"full:B1:0": "uncertain"}))
def test_unknown_claim_or_label_fails_closed(reference: dict[str, str]) -> None:
    with pytest.raises(ComparisonRobustnessError):
        conditional_label_bounds(_frame(), (reference,), 0.0)


def test_incomplete_family_census_and_pilot_reference_fail_closed() -> None:
    frame = _frame()
    with pytest.raises(ComparisonRobustnessError):
        pilot_reference_scenarios(frame, ("family",), {}, {}, 0.0)
    del frame.requests_by_cell[("family", "full", "B1")]
    with pytest.raises(ComparisonRobustnessError):
        conditional_label_bounds(frame, (), 0.0)


def test_repeated_reference_is_not_double_counted_and_source_mappings_are_unchanged() -> None:
    frame = _frame()
    original_units = dict(frame.units)
    reference = _full_reference(frame)
    once = conditional_label_bounds(frame, (reference,), 0.5)
    twice = conditional_label_bounds(frame, (reference, reference), 0.5)
    assert once == twice
    assert frame.units == original_units
    assert reference == _full_reference(frame)


def test_missing_atomic_unit_cannot_be_frozen_as_an_unobserved_constant() -> None:
    frame = _frame(full_b1=2)
    del frame.units["full:B1:0"]
    with pytest.raises(ComparisonRobustnessError, match="missing atomic"):
        conditional_label_bounds(frame, (), 0.0)


def test_wrong_cached_loss_cannot_be_treated_as_a_valid_automatic_endpoint() -> None:
    frame = _frame()
    frame.output_losses[("family", "full", "B1")][0.0] = 1.0
    with pytest.raises(ComparisonRobustnessError, match="reproduce frozen"):
        automatic_label_stress(frame, 0.0, 0.0, strict=True)
