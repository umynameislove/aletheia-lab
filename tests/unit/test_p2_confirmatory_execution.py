"""Unit contracts for the outcome-agnostic confirmatory execution kernel."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.confirmatory_execution import (
    ConfirmatoryExecutionError,
    ConfirmatorySplit,
    ConfirmatoryTrainingSource,
    ProbabilityMetrics,
    ProbabilityVector,
    apply_class_conditional_noise,
    build_confirmatory_split,
    mutation_spec_for,
    repair_labels,
    score_probabilities,
    serialization_roundtrip,
    symmetric_matched_count,
    validate_complete_target_controls,
    validate_control_predictions,
)
from aletheia_lab.benchmark.p2.confirmatory_protocol import (
    ConfirmatoryProtocol,
    DatasetBinding,
    load_confirmatory_protocol,
)

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64
_SHA_D = "d" * 64


def _protocol() -> ConfirmatoryProtocol:
    return load_confirmatory_protocol()


def _dataset(role: str) -> DatasetBinding:
    protocol = _protocol()
    return next(item for item in protocol.datasets if item.role == role)


def _primary_records() -> tuple[tuple[str, ...], tuple[int, ...]]:
    record_ids = tuple(f"record-{index:03d}" for index in range(100))
    labels = tuple(index % 2 for index in range(100))
    return record_ids, labels


def _primary_split() -> ConfirmatorySplit:
    protocol = _protocol()
    dataset = _dataset("primary")
    record_ids, labels = _primary_records()
    return build_confirmatory_split(
        protocol=protocol,
        dataset=dataset,
        dataset_sha256=dataset.snapshot_sha256,
        record_ids=record_ids,
        labels=labels,
    )


def _training_source() -> ConfirmatoryTrainingSource:
    split = _primary_split()
    _, labels = _primary_records()
    label_by_id = dict(zip(_primary_records()[0], labels, strict=True))
    train_ids = split.membership.train
    return ConfirmatoryTrainingSource(
        dataset_id=split.manifest.dataset_id,
        dataset_role=split.manifest.dataset_role,
        record_ids=train_ids,
        clean_targets=tuple(label_by_id[record_id] for record_id in train_ids),
        dataset_sha256=split.manifest.dataset_sha256,
        split_manifest_sha256=split.manifest.canonical_sha256(),
        feature_matrix_sha256=_SHA_A,
        preprocessing_sha256=_SHA_B,
        model_specification_sha256=_SHA_C,
        protocol_sha256=split.manifest.protocol_sha256,
    )


def _mutation(direction: str = "yes_to_no", rate: float = 0.3, seed: int = 4101):
    protocol = _protocol()
    dataset = _dataset("primary")
    cell = next(
        item
        for item in protocol.intervention_cells
        if item.flip_direction == direction and item.conditional_flip_rate == rate
    )
    spec = mutation_spec_for(protocol=protocol, dataset=dataset, cell=cell, seed=seed)
    return apply_class_conditional_noise(source=_training_source(), spec=spec)


def _target_controls():
    source = _training_source()
    mutation = _mutation()
    serialization = serialization_roundtrip(source)
    symmetric = symmetric_matched_count(source=source, mutation=mutation)
    repair = repair_labels(source=source, mutation=mutation)
    return {
        serialization.control_id: serialization,
        symmetric.control_id: symmetric,
        repair.control_id: repair,
    }


def _probability_vector(
    *,
    role: str,
    probabilities: tuple[float, ...],
    training_targets_sha256: str,
    model_artifact_sha256: str = _SHA_D,
) -> ProbabilityVector:
    return ProbabilityVector(
        role=role,
        record_ids=tuple(f"test-{index:02d}" for index in range(len(probabilities))),
        positive_probabilities=probabilities,
        model_artifact_sha256=model_artifact_sha256,
        training_targets_sha256=training_targets_sha256,
        evaluation_feature_matrix_sha256=_SHA_C,
        split_manifest_sha256=_training_source().split_manifest_sha256,
        protocol_sha256=_protocol().canonical_sha256(),
    )


def test_primary_split_is_stratified_deterministic_and_order_independent() -> None:
    first = _primary_split()
    protocol = _protocol()
    dataset = _dataset("primary")
    record_ids, labels = _primary_records()
    second = build_confirmatory_split(
        protocol=protocol,
        dataset=dataset,
        dataset_sha256=dataset.snapshot_sha256,
        record_ids=tuple(reversed(record_ids)),
        labels=tuple(reversed(labels)),
    )

    assert first == second
    assert (first.manifest.train_count, first.manifest.development_count) == (60, 20)
    assert first.manifest.sealed_test_count == 20
    label_by_id = dict(zip(record_ids, labels, strict=True))
    for partition in (
        first.membership.train,
        first.membership.development,
        first.membership.sealed_test,
    ):
        assert {label_by_id[record_id] for record_id in partition} == {0, 1}
    assert first.membership.all_record_ids() == frozenset(record_ids)


def test_temporal_split_preserves_source_order() -> None:
    protocol = _protocol()
    dataset = _dataset("external_replication")
    record_ids = tuple(f"time-{index:03d}" for index in range(100))
    labels = tuple(index % 2 for index in range(100))

    result = build_confirmatory_split(
        protocol=protocol,
        dataset=dataset,
        dataset_sha256=dataset.snapshot_sha256,
        record_ids=record_ids,
        labels=labels,
    )

    assert result.membership.train == record_ids[:60]
    assert result.membership.development == record_ids[60:80]
    assert result.membership.sealed_test == record_ids[80:]
    assert result.manifest.seed is None


def test_split_rejects_wrong_snapshot_and_temporal_single_class_partition() -> None:
    protocol = _protocol()
    primary = _dataset("primary")
    record_ids, labels = _primary_records()
    with pytest.raises(ConfirmatoryExecutionError, match="snapshot"):
        build_confirmatory_split(
            protocol=protocol,
            dataset=primary,
            dataset_sha256=_SHA_A,
            record_ids=record_ids,
            labels=labels,
        )

    external = _dataset("external_replication")
    temporal_labels = (0,) * 60 + tuple(index % 2 for index in range(40))
    with pytest.raises(ConfirmatoryExecutionError, match="temporal train"):
        build_confirmatory_split(
            protocol=protocol,
            dataset=external,
            dataset_sha256=external.snapshot_sha256,
            record_ids=record_ids,
            labels=temporal_labels,
        )


def test_split_manifest_rejects_forged_membership() -> None:
    split = _primary_split()
    forged_membership = split.membership.model_copy(
        update={"train": (*split.membership.train[:-1], "forged-record")}
    )
    with pytest.raises(ValidationError, match="train membership"):
        ConfirmatorySplit(manifest=split.manifest, membership=forged_membership)


@pytest.mark.parametrize(
    ("direction", "source_label", "mutated_label"),
    [("yes_to_no", 1, 0), ("no_to_yes", 0, 1)],
)
def test_class_conditional_mutation_touches_only_the_declared_source_class(
    direction: str, source_label: int, mutated_label: int
) -> None:
    mutation = _mutation(direction=direction)
    source = _training_source()
    source_by_id = dict(zip(source.record_ids, source.clean_targets, strict=True))

    assert mutation.source_class_count == 30
    assert mutation.mutation_count == 9
    assert mutation.achieved_conditional_rate == 0.3
    assert all(entry.original_label == source_label for entry in mutation.entries)
    assert all(entry.mutated_label == mutated_label for entry in mutation.entries)
    assert all(source_by_id[entry.record_id] == source_label for entry in mutation.entries)
    assert not hasattr(mutation.spec, "features")


def test_mutation_is_deterministic_and_seed_namespace_is_frozen() -> None:
    assert _mutation().canonical_sha256() == _mutation().canonical_sha256()
    assert _mutation(seed=4101).mutation_map_sha256 != _mutation(seed=4102).mutation_map_sha256

    protocol = _protocol()
    dataset = _dataset("primary")
    cell = next(item for item in protocol.intervention_cells if item.cell_id == "ccn-yes-to-no-30")
    with pytest.raises(ConfirmatoryExecutionError, match="seed"):
        mutation_spec_for(protocol=protocol, dataset=dataset, cell=cell, seed=5101)


def test_mutation_rejects_an_unbound_source() -> None:
    mutation = _mutation()
    source = _training_source().model_copy(update={"dataset_id": "another-dataset"})
    with pytest.raises(ConfirmatoryExecutionError, match="not bound"):
        apply_class_conditional_noise(source=source, spec=mutation.spec)


def test_target_controls_are_complete_effective_and_repair_exactly() -> None:
    controls = _target_controls()
    source = _training_source()
    mutation = _mutation()

    validate_complete_target_controls(controls)
    assert controls["serialization_roundtrip"].targets == source.clean_targets
    assert controls["serialization_roundtrip"].changed_record_count == 0
    assert controls["symmetric_matched_count"].changed_record_count == mutation.mutation_count
    assert controls["label_repair"].targets == source.clean_targets
    assert controls["label_repair"].control_targets_sha256 == source.targets_sha256()


def test_target_controls_fail_closed_when_missing_or_cross_source() -> None:
    controls = _target_controls()
    controls.pop("label_repair")
    with pytest.raises(ConfirmatoryExecutionError, match="incomplete"):
        validate_complete_target_controls(controls)

    source = _training_source().model_copy(update={"feature_matrix_sha256": _SHA_D})
    with pytest.raises(ConfirmatoryExecutionError, match="another source"):
        repair_labels(source=source, mutation=_mutation())


def test_probability_scoring_uses_proper_scores_and_clean_labels() -> None:
    truth = (0, 1, 0, 1)
    vector = _probability_vector(
        role="clean_reference",
        probabilities=(0.1, 0.9, 0.4, 0.6),
        training_targets_sha256=_training_source().targets_sha256(),
    )

    metrics = score_probabilities(true_labels=truth, vector=vector)

    expected_log_loss = -math.fsum(math.log(value) for value in (0.9, 0.9, 0.6, 0.6)) / 4
    assert metrics.log_loss == pytest.approx(expected_log_loss)
    assert metrics.brier_score == pytest.approx((0.01 + 0.01 + 0.16 + 0.16) / 4)
    assert metrics.roc_auc == 1.0
    assert metrics.accuracy == 1.0
    assert metrics.positive_recall == metrics.negative_recall == 1.0
    assert len(metrics.per_record_log_loss) == 4


def test_probability_scoring_clips_extreme_values_and_rejects_misalignment() -> None:
    vector = _probability_vector(
        role="class_conditional",
        probabilities=(0.0, 1.0, 1.0, 0.0),
        training_targets_sha256=_SHA_A,
    )
    metrics = score_probabilities(true_labels=(0, 1, 0, 1), vector=vector)
    assert math.isfinite(metrics.log_loss)

    with pytest.raises(ConfirmatoryExecutionError, match="align"):
        score_probabilities(true_labels=(0, 1), vector=vector)


def test_probability_metrics_reject_aggregate_tampering() -> None:
    vector = _probability_vector(
        role="clean_reference",
        probabilities=(0.1, 0.9, 0.4, 0.6),
        training_targets_sha256=_training_source().targets_sha256(),
    )
    metrics = score_probabilities(true_labels=(0, 1, 0, 1), vector=vector)
    with pytest.raises(ValidationError, match="aggregate log loss"):
        ProbabilityMetrics.model_validate(
            {**metrics.model_dump(), "log_loss": metrics.log_loss + 0.1}
        )


def test_prediction_control_gates_bind_targets_models_split_and_protocol() -> None:
    controls = _target_controls()
    clean_targets_hash = _training_source().targets_sha256()
    probabilities = (0.1, 0.9, 0.4, 0.6)
    clean = _probability_vector(
        role="clean_reference",
        probabilities=probabilities,
        training_targets_sha256=clean_targets_hash,
    )
    serialization = _probability_vector(
        role="serialization_roundtrip",
        probabilities=probabilities,
        training_targets_sha256=controls["serialization_roundtrip"].control_targets_sha256,
    )
    symmetric = _probability_vector(
        role="symmetric_matched_count",
        probabilities=(0.2, 0.8, 0.45, 0.55),
        training_targets_sha256=controls["symmetric_matched_count"].control_targets_sha256,
        model_artifact_sha256=_SHA_A,
    )
    repair = _probability_vector(
        role="label_repair",
        probabilities=probabilities,
        training_targets_sha256=controls["label_repair"].control_targets_sha256,
    )

    gates = validate_control_predictions(
        clean=clean,
        serialization=serialization,
        symmetric=symmetric,
        repair=repair,
        target_controls=controls,
    )

    assert tuple(gate.control_id for gate in gates) == (
        "clean_reference",
        "serialization_roundtrip",
        "symmetric_matched_count",
        "label_repair",
    )
    assert all(gate.passed for gate in gates)

    forged_repair = repair.model_copy(update={"model_artifact_sha256": _SHA_B})
    failed = validate_control_predictions(
        clean=clean,
        serialization=serialization,
        symmetric=symmetric,
        repair=forged_repair,
        target_controls=controls,
    )
    assert not failed[-1].passed


def test_prediction_control_gate_rejects_cross_protocol_reuse() -> None:
    controls = _target_controls()
    source_hash = _training_source().targets_sha256()
    probabilities = (0.1, 0.9, 0.4, 0.6)
    clean = _probability_vector(
        role="clean_reference",
        probabilities=probabilities,
        training_targets_sha256=source_hash,
    )
    serialization = _probability_vector(
        role="serialization_roundtrip",
        probabilities=probabilities,
        training_targets_sha256=source_hash,
    ).model_copy(update={"protocol_sha256": _SHA_A})
    symmetric = _probability_vector(
        role="symmetric_matched_count",
        probabilities=probabilities,
        training_targets_sha256=controls["symmetric_matched_count"].control_targets_sha256,
    )
    repair = _probability_vector(
        role="label_repair",
        probabilities=probabilities,
        training_targets_sha256=source_hash,
    )
    with pytest.raises(ConfirmatoryExecutionError, match="same frozen protocol"):
        validate_control_predictions(
            clean=clean,
            serialization=serialization,
            symmetric=symmetric,
            repair=repair,
            target_controls=controls,
        )
