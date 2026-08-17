"""Synthetic-only integration of split, mutation, model, controls and scoring."""

from __future__ import annotations

import pandas as pd
import pytest

from aletheia_lab.benchmark.p2.confirmatory_execution import (
    ConfirmatoryExecutionError,
    ConfirmatoryTrainingSource,
    apply_class_conditional_noise,
    build_confirmatory_split,
    mutation_spec_for,
    repair_labels,
    score_probabilities,
    serialization_roundtrip,
    symmetric_matched_count,
    validate_control_predictions,
)
from aletheia_lab.benchmark.p2.confirmatory_protocol import load_confirmatory_protocol
from aletheia_lab.benchmark.p2.confirmatory_runtime import (
    feature_frame_sha256,
    fit_frozen_probability_vector,
    frozen_model_specification_sha256,
    frozen_preprocessing_sha256,
)


def _synthetic_fixture():
    protocol = load_confirmatory_protocol()
    dataset = protocol.datasets[0]
    record_ids = tuple(f"synthetic-{index:03d}" for index in range(120))
    labels = tuple(index % 2 for index in range(120))
    frame = pd.DataFrame(
        {
            "numeric_signal": [
                float(label * 2 + (index % 7) / 10) for index, label in enumerate(labels)
            ],
            "numeric_auxiliary": [float((index % 11) - 5) for index in range(120)],
            "category": ["a" if index % 3 else "b" for index in range(120)],
        },
        index=record_ids,
    )
    split = build_confirmatory_split(
        protocol=protocol,
        dataset=dataset,
        dataset_sha256=dataset.snapshot_sha256,
        record_ids=record_ids,
        labels=labels,
    )
    label_by_id = dict(zip(record_ids, labels, strict=True))
    train_ids = split.membership.train
    development_ids = split.membership.development
    training_features = frame.loc[list(train_ids)].reset_index(drop=True)
    development_features = frame.loc[list(development_ids)].reset_index(drop=True)
    training_targets = tuple(label_by_id[record_id] for record_id in train_ids)
    development_targets = tuple(label_by_id[record_id] for record_id in development_ids)
    source = ConfirmatoryTrainingSource(
        dataset_id=dataset.dataset_id,
        dataset_role=dataset.role,
        record_ids=train_ids,
        clean_targets=training_targets,
        dataset_sha256=dataset.snapshot_sha256,
        split_manifest_sha256=split.manifest.canonical_sha256(),
        feature_matrix_sha256=feature_frame_sha256(training_features, train_ids),
        preprocessing_sha256=frozen_preprocessing_sha256(),
        model_specification_sha256=frozen_model_specification_sha256(protocol),
        protocol_sha256=protocol.canonical_sha256(),
    )
    cell = next(item for item in protocol.intervention_cells if item.cell_id == "ccn-yes-to-no-30")
    spec = mutation_spec_for(
        protocol=protocol,
        dataset=dataset,
        cell=cell,
        seed=cell.primary_replicate_seeds[0],
    )
    mutation = apply_class_conditional_noise(source=source, spec=spec)
    return (
        protocol,
        dataset,
        source,
        training_features,
        development_features,
        development_ids,
        development_targets,
        mutation,
    )


def _fit(*, role: str, targets: tuple[int, ...]):
    (
        protocol,
        dataset,
        source,
        training_features,
        development_features,
        development_ids,
        _,
        _,
    ) = _synthetic_fixture()
    return fit_frozen_probability_vector(
        protocol=protocol,
        dataset=dataset,
        source=source,
        training_features=training_features,
        training_targets=targets,
        evaluation_features=development_features,
        evaluation_record_ids=development_ids,
        role=role,
    )


def test_synthetic_runtime_executes_all_controls_without_opening_sealed_test() -> None:
    (
        _,
        _,
        source,
        _,
        _,
        development_ids,
        development_targets,
        mutation,
    ) = _synthetic_fixture()
    roundtrip = serialization_roundtrip(source)
    symmetric = symmetric_matched_count(source=source, mutation=mutation)
    repair = repair_labels(source=source, mutation=mutation)
    target_controls = {
        roundtrip.control_id: roundtrip,
        symmetric.control_id: symmetric,
        repair.control_id: repair,
    }
    clean_vector = _fit(role="clean_reference", targets=source.clean_targets)
    observed_vector = _fit(
        role="class_conditional", targets=mutation.mutated_targets
    )
    serialization_vector = _fit(
        role="serialization_roundtrip", targets=roundtrip.targets
    )
    symmetric_vector = _fit(
        role="symmetric_matched_count", targets=symmetric.targets
    )
    repair_vector = _fit(role="label_repair", targets=repair.targets)

    gates = validate_control_predictions(
        clean=clean_vector,
        serialization=serialization_vector,
        symmetric=symmetric_vector,
        repair=repair_vector,
        target_controls=target_controls,
    )
    clean_metrics = score_probabilities(
        true_labels=development_targets, vector=clean_vector
    )
    observed_metrics = score_probabilities(
        true_labels=development_targets, vector=observed_vector
    )

    assert clean_vector.record_ids == development_ids
    assert all(gate.passed for gate in gates)
    assert serialization_vector.model_artifact_sha256 == clean_vector.model_artifact_sha256
    assert repair_vector.model_artifact_sha256 == clean_vector.model_artifact_sha256
    assert serialization_vector.positive_probabilities == clean_vector.positive_probabilities
    assert repair_vector.positive_probabilities == clean_vector.positive_probabilities
    assert observed_vector.training_targets_sha256 == mutation.mutated_targets_sha256
    assert clean_metrics.record_count == observed_metrics.record_count == len(development_ids)


def test_frozen_runtime_is_deterministic() -> None:
    source = _synthetic_fixture()[2]
    first = _fit(role="clean_reference", targets=source.clean_targets)
    second = _fit(role="clean_reference", targets=source.clean_targets)
    assert first == second


def test_runtime_rejects_feature_tampering_and_schema_drift() -> None:
    (
        protocol,
        dataset,
        source,
        training_features,
        development_features,
        development_ids,
        _,
        _,
    ) = _synthetic_fixture()
    tampered = training_features.copy()
    tampered.loc[0, "numeric_signal"] = 999.0
    with pytest.raises(ConfirmatoryExecutionError, match="attestation"):
        fit_frozen_probability_vector(
            protocol=protocol,
            dataset=dataset,
            source=source,
            training_features=tampered,
            training_targets=source.clean_targets,
            evaluation_features=development_features,
            evaluation_record_ids=development_ids,
            role="clean_reference",
        )

    with pytest.raises(ConfirmatoryExecutionError, match="schemas"):
        fit_frozen_probability_vector(
            protocol=protocol,
            dataset=dataset,
            source=source,
            training_features=training_features,
            training_targets=source.clean_targets,
            evaluation_features=development_features.drop(columns="category"),
            evaluation_record_ids=development_ids,
            role="clean_reference",
        )


def test_external_runtime_rejects_the_post_call_duration_feature() -> None:
    protocol = load_confirmatory_protocol()
    dataset = protocol.datasets[1]
    record_ids = tuple(f"external-{index:03d}" for index in range(20))
    frame = pd.DataFrame(
        {"duration": [float(index) for index in range(20)], "safe": list(range(20))}
    )
    source = ConfirmatoryTrainingSource(
        dataset_id=dataset.dataset_id,
        dataset_role=dataset.role,
        record_ids=record_ids,
        clean_targets=tuple(index % 2 for index in range(20)),
        dataset_sha256=dataset.snapshot_sha256,
        split_manifest_sha256="a" * 64,
        feature_matrix_sha256=feature_frame_sha256(frame, record_ids),
        preprocessing_sha256=frozen_preprocessing_sha256(),
        model_specification_sha256=frozen_model_specification_sha256(protocol),
        protocol_sha256=protocol.canonical_sha256(),
    )
    with pytest.raises(ConfirmatoryExecutionError, match="excluded feature"):
        fit_frozen_probability_vector(
            protocol=protocol,
            dataset=dataset,
            source=source,
            training_features=frame,
            training_targets=source.clean_targets,
            evaluation_features=frame,
            evaluation_record_ids=record_ids,
            role="clean_reference",
        )
