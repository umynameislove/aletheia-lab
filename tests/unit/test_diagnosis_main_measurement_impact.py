"""Synthetic checks for finite-frame P5 claim-label impact calculations."""

from __future__ import annotations

import math
from collections import Counter

from aletheia_lab.evaluation.diagnosis_main_measurement_impact import (
    ClaimUnit,
    MeasurementImpactError,
    _effect_sensitivity,
)


def _units() -> dict[str, ClaimUnit]:
    return {
        f"c{index}": ClaimUnit(
            claim_id=f"c{index}",
            request_id=f"r{index}",
            family_id="f1",
            arm="B1" if index < 5 else "A3",
            condition="full",
            auto_label="fully_supported",
            output_claim_count=1,
            scored_output=True,
        )
        for index in range(10)
    }


def _analyze(
    units: dict[str, ClaimUnit],
    sampled: dict[str, dict[str, str]],
    known: dict[str, dict[str, str]],
    *,
    partial_weight: float = 0.0,
) -> dict[str, object]:
    return _effect_sensitivity(
        units=units,
        family_sizes=Counter({"f1": 10}),
        sampled=sampled,
        known=known,
        baseline=0.0,
        arms=("B1", "A3"),
        conditions=("full",),
        partial_weight=partial_weight,
        family_count=1,
    )


def test_zero_observed_primary_change_does_not_create_zero_width_interval() -> None:
    units = _units()
    sampled = {f"c{index}": {"human_final_label": "partially_supported"} for index in range(5)}
    result = _analyze(units, sampled, sampled)
    assert result["design_weighted_human_relabel_estimate"] == 0
    assert result["design_standard_error"] == 0
    assert result["design_interval_suppressed_sparse_zero_variance"] is True
    assert result["approx_95_design_interval_not_external_generalization"] is None
    assert result["assumption_free_label_bounds"] == [-5, 0]


def test_stratified_correction_and_known_enrichment_remain_distinct() -> None:
    units = _units()
    sampled = {
        "c0": {"human_final_label": "unsupported"},
        "c1": {"human_final_label": "fully_supported"},
        "c2": {"human_final_label": "fully_supported"},
        "c3": {"human_final_label": "fully_supported"},
        "c4": {"human_final_label": "fully_supported"},
    }
    known = sampled | {"c5": {"human_final_label": "unsupported"}}
    result = _analyze(units, sampled, known)
    assert math.isclose(result["design_weighted_human_relabel_estimate"], 2.0)
    assert result["known_claim_substitution_only_not_population_estimate"] == 0
    assert result["assumption_free_label_bounds"] == [-4, 0]


def test_unscored_emitted_claim_cannot_shift_effect() -> None:
    units = _units()
    units["c0"] = ClaimUnit(
        claim_id="c0",
        request_id="r0",
        family_id="f1",
        arm="B1",
        condition="full",
        auto_label="fully_supported",
        output_claim_count=1,
        scored_output=False,
    )
    sampled = {f"c{index}": {"human_final_label": "unsupported"} for index in range(5)}
    result = _analyze(units, sampled, sampled)
    assert result["known_claim_substitution_only_not_population_estimate"] == 4
    assert result["assumption_free_label_bounds"] == [-1, 4]


def test_incomplete_probability_sample_fails_closed() -> None:
    units = _units()
    sampled = {"c0": {"human_final_label": "unsupported"}}
    try:
        _analyze(units, sampled, sampled)
    except MeasurementImpactError as exc:
        assert "five sampled claims" in str(exc)
    else:
        raise AssertionError("incomplete sample was accepted")
