"""Secondary measurement robustness on an unchanged finite diagnosis frame.

No labels are inferred, no failed output is imputed, and no estimator replaces
the registered result. Label-set bounds are conditional feasibility regions,
not confidence intervals or evidence that human references are infallible.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from aletheia_lab.evaluation._diagnosis_main_analysis_contracts import CORE_CONDITIONS
from aletheia_lab.evaluation.diagnosis_main_measurement_impact import (
    ARMS,
    LABELS,
    ClaimUnit,
    ImpactFrame,
)


class ComparisonRobustnessError(ValueError):
    """The frame or reference cannot support the declared comparison."""


def _weight(value: float) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ComparisonRobustnessError("partial harm must be finite and in [0, 1]")
    return value


def _harm(label: str, weight: float) -> float:
    if label not in LABELS:
        raise ComparisonRobustnessError("reference contains an unknown support label")
    return (
        weight
        if label == "partially_supported"
        else float(label in {"unsupported", "contradicted"})
    )


def _check_atomic_outputs(frame: ImpactFrame) -> None:
    for cell, request in frame.requests_by_cell.items():
        identities = frame.output_claims.get(request, set())
        if identities - frame.units.keys():
            raise ComparisonRobustnessError("an output has missing atomic claims")
        units = [frame.units[identity] for identity in identities]
        flags = {unit.scored_output for unit in units}
        if len(flags) > 1:
            raise ComparisonRobustnessError("scoreability differs within one output")
        if flags != {True}:
            continue
        for weight, loss in frame.output_losses[cell].items():
            mean = math.fsum(_harm(unit.auto_label, weight) for unit in units) / len(units)
            if not math.isclose(mean, loss, rel_tol=0, abs_tol=1e-12):
                raise ComparisonRobustnessError("atomic labels do not reproduce frozen loss")


def _core(frame: ImpactFrame) -> dict[str, ClaimUnit]:
    families = frame.family_ids
    expected = {
        (family, condition, arm)
        for family in families
        for condition in CORE_CONDITIONS
        for arm in ARMS
    }
    if (
        not families
        or len(set(families)) != len(families)
        or set(frame.output_losses) != expected
        or set(frame.requests_by_cell) != expected
    ):
        raise ComparisonRobustnessError("the complete paired family census is required")
    core = {
        identity: unit
        for identity, unit in frame.units.items()
        if unit.arm in ARMS and unit.condition in CORE_CONDITIONS and unit.scored_output
    }
    for identity, unit in core.items():
        if (
            identity != unit.claim_id
            or unit.family_id not in families
            or unit.output_claim_count <= 0
            or frame.requests_by_cell[(unit.family_id, unit.condition, unit.arm)] != unit.request_id
            or identity not in frame.output_claims.get(unit.request_id, set())
            or len(frame.output_claims[unit.request_id]) != unit.output_claim_count
        ):
            raise ComparisonRobustnessError("a claim differs from its frozen output")
        _harm(unit.auto_label, 0.0)
    for losses in frame.output_losses.values():
        if set(losses) != {0.0, 0.5} or any(
            not math.isfinite(loss) or not 0 <= loss <= 1 for loss in losses.values()
        ):
            raise ComparisonRobustnessError("frozen endpoint losses are invalid")
    _check_atomic_outputs(frame)
    return core


def _coefficient(unit: ClaimUnit, family_count: int) -> float:
    sign = 1 if unit.arm == "B1" else -1
    return sign / (family_count * len(CORE_CONDITIONS) * unit.output_claim_count)


def _automatic(frame: ImpactFrame, weight: float) -> float:
    return math.fsum(
        (1 if arm == "B1" else -1) * (losses[0.0] + 2 * weight * (losses[0.5] - losses[0.0]))
        for (_, _, arm), losses in frame.output_losses.items()
    ) / (len(frame.family_ids) * len(CORE_CONDITIONS))


def _reference_sets(
    frame: ImpactFrame, references: Sequence[Mapping[str, str]]
) -> dict[str, frozenset[str]]:
    merged: dict[str, set[str]] = {}
    for reference in references:
        for identity, label in reference.items():
            if identity not in frame.units:
                raise ComparisonRobustnessError("reference claim is outside the frozen frame")
            _harm(label, 0.0)
            merged.setdefault(identity, set()).add(label)
    return {identity: frozenset(labels) for identity, labels in merged.items()}


def _label_region(
    core: Mapping[str, ClaimUnit],
    references: Mapping[str, frozenset[str]],
    coefficients: Mapping[str, float],
    constant: float,
    weight: float,
) -> tuple[float, float]:
    lower, upper = [], []
    for identity in core:
        possibilities = references.get(identity, LABELS)
        values = [coefficients[identity] * _harm(label, weight) for label in possibilities]
        lower.append(min(values))
        upper.append(max(values))
    return constant + math.fsum(lower), constant + math.fsum(upper)


def conditional_label_bounds(
    frame: ImpactFrame,
    references: Sequence[Mapping[str, str]],
    partial_weight: float,
) -> dict[str, Any]:
    """Sharp under a per-claim product domain, conditional on returned references.

    An unseen claim may have any rubric label. At an overlap, every returned
    label remains allowed; selecting a winner would be new adjudication. The
    union is never used as a probability sample or a combined point estimator.
    """
    weight = _weight(partial_weight)
    core = _core(frame)
    known = _reference_sets(frame, references)
    coefficients = {
        identity: _coefficient(unit, len(frame.family_ids)) for identity, unit in core.items()
    }
    constant = _automatic(frame, weight) - math.fsum(
        coefficients[identity] * _harm(unit.auto_label, weight) for identity, unit in core.items()
    )
    lower, upper = _label_region(core, known, coefficients, constant, weight)
    return {
        "conditional_product_label_region": [lower, upper],
        "scored_core_claims": len(core),
        "reference_observed_core_claims": len(core.keys() & known.keys()),
        "unseen_core_claims": len(core.keys() - known.keys()),
        "reference_disputed_core_claims": sum(len(known[c]) > 1 for c in core if c in known),
        "reference_disputed_harm_core_claims": sum(
            len({_harm(label, weight) for label in known[c]}) > 1 for c in core if c in known
        ),
        "sign_identified": "positive" if lower > 0 else "negative" if upper < 0 else "unidentified",
        "minimum_effect_0_05_ruled_out": upper < 0.05,
        "minimum_effect_0_05_forced": lower >= 0.05,
        "scope": "conditional_label_domain_not_truth_bound_or_confidence_interval",
        "sampling_designs_pooled": False,
    }


def affine_weight_profile(primary: float, partial_half: float) -> dict[str, Any]:
    """An exact algebraic continuation, never a selected replacement metric."""
    if not math.isfinite(primary) or not math.isfinite(partial_half):
        raise ComparisonRobustnessError("affine endpoints must be finite")
    slope = 2 * (partial_half - primary)
    crossing = None if slope == 0 else -primary / slope
    grid = {str(weight): primary + weight * slope for weight in (0.0, 0.25, 0.5, 0.75, 1.0)}
    return {
        "intercept": primary,
        "slope": slope,
        "exploratory_grid": grid,
        "zero_crossing_in_unit_interval": crossing
        if crossing is not None and 0 <= crossing <= 1
        else None,
        "maximum_point_effect_over_unit_interval": max(primary, primary + slope),
        "point_curve_reaches_0_05": max(primary, primary + slope) >= 0.05,
        "registered_endpoints_unchanged": True,
        "scope": "algebra_conditional_on_fixed_labels_statuses_and_segmentation_not_ci",
    }


def pilot_reference_scenarios(
    frame: ImpactFrame,
    selected_families: Sequence[str],
    first: Mapping[str, str],
    second: Mapping[str, str],
    partial_weight: float,
) -> dict[str, Any]:
    """Feasible *pilot difference-estimator* range for existing two-rater labels."""
    weight = _weight(partial_weight)
    full_core = _core(frame)
    selected = set(selected_families)
    if (
        not selected
        or len(selected) != len(selected_families)
        or not selected <= set(frame.family_ids)
    ):
        raise ComparisonRobustnessError("selected family IDs are invalid")
    core = {identity: unit for identity, unit in full_core.items() if unit.family_id in selected}
    if set(first) != set(core) or set(second) != set(core):
        raise ComparisonRobustnessError(
            "both original raters must cover every selected scored claim"
        )
    labels = _reference_sets(frame, (first, second))
    coefficients = {identity: _coefficient(unit, len(selected)) for identity, unit in core.items()}
    constant = _automatic(frame, weight) - math.fsum(
        coefficients[identity] * _harm(unit.auto_label, weight) for identity, unit in core.items()
    )
    lower, upper = _label_region(core, labels, coefficients, constant, weight)
    return {
        "pilot_difference_estimator_scenario_range": [lower, upper],
        "selected_families": len(selected),
        "disputed_claims": sum(first[c] != second[c] for c in core),
        "harm_disputed_claims": sum(
            _harm(first[c], weight) != _harm(second[c], weight) for c in core
        ),
        "scope": "returned_rater_label_scenarios_not_population_bound_or_sampling_ci",
        "mixed_label_resolutions_allowed": True,
    }


def automatic_label_stress(
    frame: ImpactFrame,
    partial_weight: float,
    target: float,
    *,
    strict: bool,
) -> dict[str, Any]:
    """Unit-cost adversarial changes, not estimated human error or audit priority."""
    weight = _weight(partial_weight)
    if not math.isfinite(target):
        raise ComparisonRobustnessError("stress target must be finite")
    core = _core(frame)
    baseline = _automatic(frame, weight)
    gains = sorted(
        (
            max(
                _coefficient(unit, len(frame.family_ids))
                * (_harm(label, weight) - _harm(unit.auto_label, weight))
                for label in LABELS
            )
            for unit in core.values()
        ),
        reverse=True,
    )
    total, count = baseline, 0
    reached = total > target if strict else total >= target
    for gain in gains:
        if reached or gain <= 0:
            break
        count += 1
        total = baseline + math.fsum(gains[:count])
        reached = total > target if strict else total >= target
    return {
        "automatic_effect": baseline,
        "target": target,
        "strict_target": strict,
        "minimum_changed_automatic_claim_labels": count if reached else None,
        "hypothetical_attained_effect": total if reached else None,
        "scored_core_claims": len(core),
        "scope": "adversarial_unit_cost_label_perturbation_not_error_rate_or_new_outcome",
    }


def metric_semantics(frame: ImpactFrame) -> dict[str, Any]:
    """Supplementary any-harm indicator; preserve all frozen non-claim losses."""
    core = _core(frame)
    by_request: dict[str, list[ClaimUnit]] = {}
    for unit in core.values():
        by_request.setdefault(unit.request_id, []).append(unit)
    arm_counts: dict[str, Counter[str]] = {arm: Counter() for arm in ARMS}
    any_terms = []
    for (family, condition, arm), losses in frame.output_losses.items():
        request = frame.requests_by_cell[(family, condition, arm)]
        units = by_request.get(request, [])
        loss = losses[0.0]
        if units:
            if len(units) != units[0].output_claim_count:
                raise ComparisonRobustnessError("a scored output has incomplete atomic claims")
            harm = int(any(_harm(unit.auto_label, 0.0) > 0 for unit in units))
            arm_counts[arm]["scored_outputs"] += 1
            arm_counts[arm]["scored_claims"] += len(units)
            arm_counts[arm]["scored_outputs_with_any_automatic_harm"] += harm
            loss = float(harm)
        any_terms.append((1 if arm == "B1" else -1) * loss)
    denominator = len(frame.family_ids) * len(CORE_CONDITIONS)
    return {
        "automatic_mean_claim_harm_contrast": _automatic(frame, 0.0),
        "supplementary_any_harm_output_contrast": math.fsum(any_terms) / denominator,
        "arm_scored_counts": {arm: dict(counts) for arm, counts in arm_counts.items()},
        "fixed_non_claim_loss_preserved": True,
        "scope": "secondary_construct_sensitivity_not_replacement_primary_or_diagnosis_accuracy",
    }
