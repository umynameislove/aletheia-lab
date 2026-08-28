"""Property checks for conservative P2R inference and immutable evidence."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.lightweight_protocol import (
    DEFAULT_DATA_DRIFT_PROTOCOL_PATH,
    load_lightweight_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.p2r_closeout import decide_dataset
from aletheia_lab.benchmark.p2.p2r_runtime import (
    DATASET_MEASUREMENT_SCHEMA_VERSION,
    DatasetSeedMeasurement,
)


def _measurement(seed: int, delta: float, nuisance: float) -> DatasetSeedMeasurement:
    protocol = load_lightweight_confirmatory_protocol(DEFAULT_DATA_DRIFT_PROTOCOL_PATH)
    dataset = protocol.datasets[0]
    payload: dict[str, object] = {
        "schema_version": DATASET_MEASUREMENT_SCHEMA_VERSION,
        "protocol_sha256": protocol.canonical_sha256(),
        "mechanism": protocol.mechanism,
        "dataset_id": dataset.dataset_id,
        "dataset_role": dataset.role,
        "split_membership_sha256": dataset.split_membership_sha256,
        "sealed_membership_sha256": dataset.sealed_membership_sha256,
        "target_feature": dataset.target_feature,
        "seed": seed,
        "declared_manipulation_magnitude": 0.20,
        "achieved_manipulation_magnitude": 0.20,
        "clean_accuracy": 0.80,
        "manipulated_accuracy": 0.80 + delta,
        "nuisance_accuracy": 0.80 - nuisance,
        "target_metric_delta": delta,
        "nuisance_effect_magnitude": nuisance,
        "model_sha256": f"{seed:064x}",
        "source_binding_sha256": f"{seed + 100:064x}",
        "intervention_sha256": f"{seed + 200:064x}",
        "nuisance_comparator_sha256": f"{seed + 300:064x}",
    }
    return DatasetSeedMeasurement.model_validate(
        {**payload, "measurement_sha256": canonical_sha256(payload)}
    )


@given(st.permutations((8201, 8202, 8203, 8204, 8205)))
@settings(max_examples=30)
def test_seed_input_permutation_does_not_change_dataset_decision(
    order: list[int],
) -> None:
    protocol = load_lightweight_confirmatory_protocol(DEFAULT_DATA_DRIFT_PROTOCOL_PATH)
    canonical = tuple(_measurement(seed, -0.02, 0.005) for seed in protocol.execution.seeds)
    shuffled = tuple(next(item for item in canonical if item.seed == seed) for seed in order)

    assert decide_dataset(protocol=protocol, measurements=shuffled) == decide_dataset(
        protocol=protocol, measurements=canonical
    )


@given(
    st.lists(
        st.floats(min_value=0.0, max_value=0.04, allow_nan=False, allow_infinity=False),
        min_size=5,
        max_size=5,
    )
)
@settings(max_examples=50)
def test_increasing_nuisance_cannot_turn_a_failed_dominance_gate_into_pass(
    nuisances: list[float],
) -> None:
    protocol = load_lightweight_confirmatory_protocol(DEFAULT_DATA_DRIFT_PROTOCOL_PATH)
    baseline = tuple(
        _measurement(seed, -0.01, nuisance)
        for seed, nuisance in zip(protocol.execution.seeds, nuisances, strict=True)
    )
    increased = tuple(
        _measurement(seed, -0.01, min(0.05, nuisance + 0.02))
        for seed, nuisance in zip(protocol.execution.seeds, nuisances, strict=True)
    )
    first = decide_dataset(protocol=protocol, measurements=baseline)
    second = decide_dataset(protocol=protocol, measurements=increased)

    if not first.dominance_pass:
        assert not second.dominance_pass
