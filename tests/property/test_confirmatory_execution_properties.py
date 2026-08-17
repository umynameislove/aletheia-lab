"""Property invariants for confirmatory mutation, scoring and inference."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aletheia_lab.benchmark.p2.confirmatory_execution import (
    ConfirmatoryTrainingSource,
    ProbabilityVector,
    apply_class_conditional_noise,
    mutation_spec_for,
    score_probabilities,
)
from aletheia_lab.benchmark.p2.confirmatory_inference import (
    holm_adjust,
    two_way_product_weight_bootstrap,
)
from aletheia_lab.benchmark.p2.confirmatory_protocol import load_confirmatory_protocol

_SHA = "a" * 64


def _source(*, reversed_order: bool = False) -> ConfirmatoryTrainingSource:
    record_ids = tuple(f"property-{index:03d}" for index in range(100))
    targets = tuple(index % 2 for index in range(100))
    if reversed_order:
        record_ids = tuple(reversed(record_ids))
        targets = tuple(reversed(targets))
    return ConfirmatoryTrainingSource(
        dataset_id="telco_customer_churn",
        dataset_role="primary",
        record_ids=record_ids,
        clean_targets=targets,
        dataset_sha256=_SHA,
        split_manifest_sha256=_SHA,
        feature_matrix_sha256=_SHA,
        preprocessing_sha256=_SHA,
        model_specification_sha256=_SHA,
        protocol_sha256=load_confirmatory_protocol().canonical_sha256(),
    )


@given(
    direction=st.sampled_from(("yes_to_no", "no_to_yes")),
    rate=st.sampled_from((0.1, 0.2, 0.3)),
    seed_index=st.integers(min_value=0, max_value=29),
)
def test_class_conditional_selection_is_order_independent_and_source_class_only(
    direction: str, rate: float, seed_index: int
) -> None:
    protocol = load_confirmatory_protocol()
    dataset = protocol.datasets[0]
    cell = next(
        item
        for item in protocol.intervention_cells
        if item.flip_direction == direction and item.conditional_flip_rate == rate
    )
    spec = mutation_spec_for(
        protocol=protocol,
        dataset=dataset,
        cell=cell,
        seed=cell.primary_replicate_seeds[seed_index],
    )
    forward = apply_class_conditional_noise(source=_source(), spec=spec)
    reverse = apply_class_conditional_noise(source=_source(reversed_order=True), spec=spec)
    source_label = 1 if direction == "yes_to_no" else 0

    assert forward.entries == reverse.entries
    assert forward.mutation_map_sha256 == reverse.mutation_map_sha256
    assert all(entry.original_label == source_label for entry in forward.entries)
    assert forward.mutation_count == round(rate * forward.source_class_count)


@given(confidence=st.floats(min_value=0.55, max_value=0.99, allow_nan=False))
def test_moving_probabilities_toward_ignorance_worsens_both_proper_scores(
    confidence: float,
) -> None:
    truth = (0, 1, 0, 1, 0, 1)
    clean_probabilities = tuple(confidence if value else 1.0 - confidence for value in truth)
    degraded_confidence = (confidence + 0.5) / 2.0
    degraded_probabilities = tuple(
        degraded_confidence if value else 1.0 - degraded_confidence for value in truth
    )

    def vector(probabilities: tuple[float, ...]) -> ProbabilityVector:
        return ProbabilityVector(
            role="clean_reference",
            record_ids=tuple(f"score-{index}" for index in range(len(truth))),
            positive_probabilities=probabilities,
            model_artifact_sha256=_SHA,
            training_targets_sha256=_SHA,
            evaluation_feature_matrix_sha256=_SHA,
            split_manifest_sha256=_SHA,
            protocol_sha256=_SHA,
        )

    clean = score_probabilities(true_labels=truth, vector=vector(clean_probabilities))
    degraded = score_probabilities(
        true_labels=truth, vector=vector(degraded_probabilities)
    )
    assert degraded.log_loss > clean.log_loss
    assert degraded.brier_score > clean.brier_score


@given(
    first=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    second=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
def test_holm_adjustment_never_reduces_a_raw_pvalue(first: float, second: float) -> None:
    raw = {"yes_to_no": first, "no_to_yes": second}
    adjusted = holm_adjust(raw)
    assert set(adjusted) == set(raw)
    assert all(adjusted[key] >= raw[key] for key in raw)


@given(scale=st.floats(min_value=0.1, max_value=10.0, allow_nan=False))
def test_relative_two_way_bootstrap_is_scale_invariant(scale: float) -> None:
    clean = (0.1, 0.2, 0.3, 0.4)
    observed = ((0.2, 0.3, 0.4, 0.5), (0.3, 0.4, 0.5, 0.6))
    baseline = two_way_product_weight_bootstrap(
        clean_losses=clean, observed_losses_by_seed=observed, resamples=100, seed=19
    )
    scaled = two_way_product_weight_bootstrap(
        clean_losses=tuple(value * scale for value in clean),
        observed_losses_by_seed=tuple(
            tuple(value * scale for value in row) for row in observed
        ),
        resamples=100,
        seed=19,
    )
    assert scaled.point_estimate == pytest.approx(baseline.point_estimate)
    assert scaled.lower_bound == pytest.approx(baseline.lower_bound)
    assert scaled.upper_bound == pytest.approx(baseline.upper_bound)
