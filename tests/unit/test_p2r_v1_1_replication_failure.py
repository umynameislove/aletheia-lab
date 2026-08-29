"""Contracts for the P2R v1.1 replication-failure and positivity audit."""

from __future__ import annotations

import hashlib

import pandas as pd
import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    V3DatasetBindingManifest,
    load_v3_dataset_binding_manifest,
)
from aletheia_lab.benchmark.p2.lightweight_protocol import (
    DEFAULT_DATA_DRIFT_PROTOCOL_PATH,
    DEFAULT_PREPROCESSING_PROTOCOL_PATH,
    load_lightweight_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.p2r_replication_failure import (
    CAPACITY_RESERVE,
    DECLARED_MAGNITUDE,
    P2R_V1_1_EXCEPTION_MESSAGE,
    P2R_V1_1_EXCEPTION_MESSAGE_SHA256,
    P2R_V1_1_TERMINAL_STORE_SHA256,
    P2RFeatureCapacity,
    P2RReplicationFailureError,
    P2RV11ReplicationFailureAudit,
    assess_feature_capacity,
    build_dataset_feasibility_census,
    build_intervention_feasibility_receipt,
    load_intervention_feasibility_receipt,
    load_p2r_v1_1_replication_failure_audit,
)


def _frame(columns: tuple[str, ...], *, balanced: bool) -> pd.DataFrame:
    values = ["a"] * (5 if balanced else 9) + ["b"] * (5 if balanced else 1)
    return pd.DataFrame({column: values for column in columns})


def test_exception_preimage_reproduces_the_immutable_terminal_digest() -> None:
    assert P2R_V1_1_EXCEPTION_MESSAGE == (
        "registered manipulation cannot achieve its declared row count"
    )
    assert hashlib.sha256(P2R_V1_1_EXCEPTION_MESSAGE.encode()).hexdigest() == (
        P2R_V1_1_EXCEPTION_MESSAGE_SHA256
    )


def test_capacity_is_directional_and_reserves_support_before_execution() -> None:
    capacity = assess_feature_capacity(
        feature="VisitorType",
        training_values=("returning",) * 8 + ("new",) * 2,
        sealed_values=("returning",) * 9 + ("new",),
    )

    assert capacity.target_row_count == 2
    assert capacity.reserve_row_count == 1
    assert capacity.data_drift_capacity_count == 1
    assert capacity.preprocessing_capacity_count == 9
    assert not capacity.data_drift_feasible
    assert capacity.preprocessing_feasible
    assert not capacity.jointly_feasible_with_reserve


def test_capacity_rejects_blank_monoculture_and_underived_evidence() -> None:
    with pytest.raises(P2RReplicationFailureError, match="blank"):
        assess_feature_capacity(
            feature="feature", training_values=("a", "b"), sealed_values=("a", "")
        )
    with pytest.raises(P2RReplicationFailureError, match="two training categories"):
        assess_feature_capacity(
            feature="feature", training_values=("a", "a"), sealed_values=("a", "b")
        )

    capacity = assess_feature_capacity(
        feature="feature",
        training_values=("a",) * 6 + ("b",) * 4,
        sealed_values=("a",) * 5 + ("b",) * 5,
    )
    payload = capacity.model_dump()
    payload["data_drift_capacity_count"] = 4
    with pytest.raises(ValidationError, match="not derived"):
        P2RFeatureCapacity.model_validate(payload)


def test_frozen_target_is_retained_when_both_interventions_have_reserve() -> None:
    manifest = load_v3_dataset_binding_manifest()
    protocol = load_lightweight_confirmatory_protocol(DEFAULT_DATA_DRIFT_PROTOCOL_PATH)
    binding = manifest.datasets[0]
    study = protocol.datasets[0]
    columns = tuple(binding.categorical_features)
    frame = _frame(columns, balanced=True)

    census = build_dataset_feasibility_census(
        binding=binding,
        study_dataset=study,
        training_frame=frame,
        sealed_frame=frame,
    )

    assert census.frozen_target_jointly_feasible
    assert census.selected_target_feature == "EDUCATION"
    assert census.selection_reason == "frozen_target_retained"


def test_infeasible_frozen_target_uses_maximum_minimum_capacity_without_outcomes() -> None:
    manifest = load_v3_dataset_binding_manifest()
    protocol = load_lightweight_confirmatory_protocol(DEFAULT_DATA_DRIFT_PROTOCOL_PATH)
    binding = manifest.datasets[1]
    study = protocol.datasets[1]
    columns = tuple(binding.categorical_features)
    training = _frame(columns, balanced=True)
    sealed = _frame(columns, balanced=False)
    sealed["OperatingSystems"] = ["a"] * 5 + ["b"] * 5
    sealed["Browser"] = ["a"] * 6 + ["b"] * 4

    census = build_dataset_feasibility_census(
        binding=binding,
        study_dataset=study,
        training_frame=training,
        sealed_frame=sealed,
    )

    assert not census.frozen_target_jointly_feasible
    assert census.selected_target_feature == "OperatingSystems"
    assert census.selection_reason == (
        "frozen_target_infeasible_capacity_selected_without_outcomes"
    )


def test_dataset_census_fails_closed_when_no_feature_has_capacity_reserve() -> None:
    manifest = load_v3_dataset_binding_manifest()
    protocol = load_lightweight_confirmatory_protocol(DEFAULT_DATA_DRIFT_PROTOCOL_PATH)
    binding = manifest.datasets[1]
    columns = tuple(binding.categorical_features)

    with pytest.raises(P2RReplicationFailureError, match="no outcome-free feature"):
        build_dataset_feasibility_census(
            binding=binding,
            study_dataset=protocol.datasets[1],
            training_frame=_frame(columns, balanced=True),
            sealed_frame=_frame(columns, balanced=False),
        )


def test_capacity_compiler_rejects_noncanonical_or_target_bearing_frames() -> None:
    manifest = load_v3_dataset_binding_manifest()
    protocol = load_lightweight_confirmatory_protocol(DEFAULT_DATA_DRIFT_PROTOCOL_PATH)
    binding = manifest.datasets[0]
    columns = tuple(binding.categorical_features)
    frame = _frame(columns, balanced=True)
    frame["target"] = [0, 1] * 5

    with pytest.raises(P2RReplicationFailureError, match="only manifest categorical"):
        build_dataset_feasibility_census(
            binding=binding,
            study_dataset=protocol.datasets[0],
            training_frame=frame,
            sealed_frame=frame,
        )


def test_feasibility_receipt_rejects_a_manifest_outside_the_protocol_binding() -> None:
    manifest = load_v3_dataset_binding_manifest()
    protocol = load_lightweight_confirmatory_protocol(DEFAULT_DATA_DRIFT_PROTOCOL_PATH)
    altered = V3DatasetBindingManifest.model_validate(
        {
            **manifest.model_dump(),
            "design_sha256": "0" * 64,
        }
    )
    frames = {
        binding.dataset_id: (
            _frame(tuple(binding.categorical_features), balanced=True),
            _frame(tuple(binding.categorical_features), balanced=True),
        )
        for binding in altered.datasets
    }

    with pytest.raises(P2RReplicationFailureError, match="another dataset manifest"):
        build_intervention_feasibility_receipt(
            manifest=altered,
            protocols=(
                protocol,
                load_lightweight_confirmatory_protocol(
                    DEFAULT_PREPROCESSING_PROTOCOL_PATH
                ),
            ),
            frames=frames,
        )


def test_feasibility_receipt_builds_complete_paired_census_without_targets() -> None:
    manifest = load_v3_dataset_binding_manifest()
    drift = load_lightweight_confirmatory_protocol(DEFAULT_DATA_DRIFT_PROTOCOL_PATH)
    preprocessing = load_lightweight_confirmatory_protocol(
        DEFAULT_PREPROCESSING_PROTOCOL_PATH
    )
    frames = {
        binding.dataset_id: (
            _frame(tuple(binding.categorical_features), balanced=True),
            _frame(tuple(binding.categorical_features), balanced=True),
        )
        for binding in manifest.datasets
    }

    receipt = build_intervention_feasibility_receipt(
        manifest=manifest,
        protocols=(drift, preprocessing),
        frames=frames,
    )

    assert tuple(item.dataset_id for item in receipt.datasets) == tuple(
        item.dataset_id for item in manifest.datasets
    )
    assert all(item.frozen_target_jointly_feasible for item in receipt.datasets)
    assert not receipt.target_values_used_for_capacity_or_selection
    assert not receipt.model_fitted
    assert not receipt.predictive_metrics_generated


def test_tracked_feasibility_receipt_reconciles_every_categorical_candidate() -> None:
    receipt = load_intervention_feasibility_receipt()

    assert receipt.declared_manipulation_magnitude == DECLARED_MAGNITUDE
    assert receipt.minimum_capacity_reserve == CAPACITY_RESERVE
    assert tuple(len(item.capacities) for item in receipt.datasets) == (9, 7)
    assert tuple(item.selected_target_feature for item in receipt.datasets) == (
        "EDUCATION",
        "OperatingSystems",
    )
    replication = receipt.datasets[1]
    visitor = next(item for item in replication.capacities if item.feature == "VisitorType")
    operating_system = next(
        item for item in replication.capacities if item.feature == "OperatingSystems"
    )
    assert (visitor.target_row_count, visitor.data_drift_capacity_count) == (493, 343)
    assert not visitor.data_drift_feasible
    assert operating_system.minimum_capacity_count == 1140
    assert operating_system.jointly_feasible_with_reserve
    assert not receipt.target_values_used_for_capacity_or_selection
    assert not receipt.model_fitted
    assert not receipt.predictive_metrics_generated


def test_tracked_failure_is_protocol_feasibility_not_scientific_outcome() -> None:
    audit = load_p2r_v1_1_replication_failure_audit()

    assert audit.terminal_store_sha256 == P2R_V1_1_TERMINAL_STORE_SHA256
    assert audit.failure_stage == "execute_replication"
    assert audit.failed_mechanism == "data_drift"
    assert audit.failed_feature == "VisitorType"
    assert audit.capacity_shortfall == 150
    assert audit.root_cause_classification == "registered_intervention_capacity_defect"
    assert audit.protocol_feasibility_defect
    assert not audit.implementation_bug
    assert not audit.scientific_negative_result
    assert not audit.predictive_outcomes_inspected_for_repair
    assert audit.scientific_semantics_changed_by_repair
    assert audit.rerun_forbidden
    assert audit.required_successor_scope.startswith("prospective_v1_2")

    tampered = audit.model_dump()
    tampered["capacity_shortfall"] = 149
    with pytest.raises(ValidationError, match="shortfall"):
        P2RV11ReplicationFailureAudit.model_validate(tampered)
