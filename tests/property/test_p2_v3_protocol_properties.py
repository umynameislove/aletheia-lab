"""Property invariants for v3 protocol decision and control algebra."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    DirectionEvidence,
    evaluate_cross_dataset_decision,
    holm_adjusted_p_values,
    reciprocal_pair_count,
    target_environment_class_counts,
)

_DATASETS = (
    "uci_default_of_credit_card_clients",
    "uci_online_shoppers_purchasing_intention",
)


@given(
    source=st.integers(min_value=1, max_value=100000),
    opposite=st.integers(min_value=1, max_value=100000),
    rate=st.floats(min_value=1e-6, max_value=0.999, allow_nan=False, allow_infinity=False),
)
def test_reciprocal_pairs_never_exceed_either_class(
    source: int, opposite: int, rate: float
) -> None:
    pairs = reciprocal_pair_count(
        source_class_count=source,
        opposite_class_count=opposite,
        rate=rate,
    )
    assert 0 <= pairs <= min(source, opposite)
    assert pairs <= int(rate * source)


@given(
    total=st.integers(min_value=1000, max_value=100000),
    prior=st.floats(min_value=0.05, max_value=0.95, allow_nan=False, allow_infinity=False),
    multiplier=st.floats(min_value=0.25, max_value=4.0, allow_nan=False, allow_infinity=False),
)
def test_prior_environment_integer_allocation_always_reconciles(
    total: int, prior: float, multiplier: float
) -> None:
    negative, positive = target_environment_class_counts(
        total=total,
        source_positive_prior=prior,
        odds_multiplier=multiplier,
    )
    assert negative + positive == total
    assert negative > 0
    assert positive > 0


@given(
    first=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    second=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)
def test_holm_adjustment_is_bounded_and_never_smaller_than_raw(
    first: float, second: float
) -> None:
    raw = {"yes_to_no": first, "no_to_yes": second}
    adjusted = holm_adjusted_p_values(raw)
    assert set(adjusted) == set(raw)
    assert all(raw[key] <= adjusted[key] <= 1.0 for key in raw)


@given(
    weak_effect=st.floats(
        min_value=-10.0,
        max_value=0.049999,
        allow_nan=False,
        allow_infinity=False,
    )
)
def test_one_weak_dataset_can_never_be_rescued_by_the_other(weak_effect: float) -> None:
    def evidence(direction: str, effects: tuple[float, float]) -> DirectionEvidence:
        return DirectionEvidence.model_validate(
            {
                "direction": direction,
                "net_effects": dict(zip(_DATASETS, effects, strict=True)),
                "bootstrap_lower_bounds": dict(zip(_DATASETS, (0.01, 0.01), strict=True)),
                "dataset_p_values": dict(zip(_DATASETS, (0.001, 0.001), strict=True)),
                "technical_controls_pass": True,
                "prior_only_label_noise_admissions": 0,
                "assumptions_pass": True,
            }
        )

    decision = evaluate_cross_dataset_decision(
        (
            evidence("yes_to_no", (weak_effect, 10.0)),
            evidence("no_to_yes", (weak_effect, 10.0)),
        )
    )
    assert not decision.claim_allowed
    assert decision.disposition == "fail_closed"
