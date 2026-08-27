"""Tests for outcome-blind mechanism-specific registration candidates."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.lightweight_protocol import (
    DEFAULT_DATA_DRIFT_PROTOCOL_PATH,
    DEFAULT_PREPROCESSING_PROTOCOL_PATH,
    LightweightConfirmatoryProtocol,
    LightweightProtocolError,
    load_lightweight_confirmatory_protocol,
    verify_lightweight_confirmatory_protocol,
    verify_protocol_pair,
)


def _protocols() -> tuple[
    LightweightConfirmatoryProtocol,
    LightweightConfirmatoryProtocol,
]:
    return (
        load_lightweight_confirmatory_protocol(DEFAULT_DATA_DRIFT_PROTOCOL_PATH),
        load_lightweight_confirmatory_protocol(DEFAULT_PREPROCESSING_PROTOCOL_PATH),
    )


def _changed(
    protocol: LightweightConfirmatoryProtocol,
    **changes: object,
) -> LightweightConfirmatoryProtocol:
    return protocol.model_copy(update=changes)


def test_two_protocols_freeze_distinct_pending_mechanisms() -> None:
    drift, preprocessing = verify_protocol_pair(*_protocols())

    assert drift.mechanism == "data_drift"
    assert preprocessing.mechanism == "preprocessing_bug"
    assert drift.canonical_sha256() != preprocessing.canonical_sha256()
    assert {item.dataset_id for item in drift.datasets} == {
        "uci_default_of_credit_card_clients",
        "uci_online_shoppers_purchasing_intention",
    }


@pytest.mark.parametrize("index", [0, 1])
def test_each_protocol_freezes_endpoint_attempts_exclusions_and_dispositions(
    index: int,
) -> None:
    protocol = verify_lightweight_confirmatory_protocol(_protocols()[index])

    assert protocol.endpoint.minimum_practical_effect == 0.01
    assert protocol.endpoint.minimum_expected_direction_fraction == 0.8
    assert protocol.endpoint.cross_dataset_rule == "both_datasets_must_pass"
    assert protocol.execution.seeds == (8201, 8202, 8203, 8204, 8205)
    assert protocol.execution.maximum_registered_execution_attempts == 1
    assert protocol.execution.outcomes_released_together
    assert not protocol.execution.execution_authorized
    assert not protocol.execution.sealed_outcomes_generated
    assert protocol.exclusions.exclude_nuisance_dominated_candidates
    assert protocol.exclusions.exclude_incomplete_sibling_bundles
    assert protocol.model.parameters == (
        "C=1.0",
        "solver=lbfgs",
        "max_iter=1000",
        "random_state=42",
    )
    assert protocol.model.hyperparameter_search_forbidden
    assert protocol.dispositions.pass_disposition == "admitted"
    assert protocol.dispositions.scientific_abstention_disposition == (
        "assumption_limited"
    )
    assert protocol.dispositions.valid_negative_disposition == "rejected"
    assert protocol.dispositions.contract_failure_disposition == "technical_failure"
    assert protocol.dispositions.missing_or_ambiguous_evidence_action == "fail_closed"
    assert protocol.governance.reuses_partitions_opened_for_another_mechanism
    assert not protocol.governance.independent_new_dataset_replication
    assert not protocol.governance.target_mechanism_outcomes_inspected_before_freeze


def test_seed_grid_and_dataset_roles_cannot_be_changed_silently() -> None:
    protocol = _protocols()[0]
    execution = protocol.execution.model_copy(update={"seeds": (8201, 8202, 8203)})
    with pytest.raises(ValidationError, match="five-seed census"):
        LightweightConfirmatoryProtocol.model_validate(
            protocol.model_copy(update={"execution": execution}).model_dump()
        )

    reversed_datasets = tuple(reversed(protocol.datasets))
    with pytest.raises(ValidationError, match="canonical role order"):
        LightweightConfirmatoryProtocol.model_validate(
            protocol.model_copy(update={"datasets": reversed_datasets}).model_dump()
        )


def test_artifact_file_and_canonical_hashes_fail_closed(tmp_path: Path) -> None:
    protocol = _protocols()[0]
    forged = tmp_path / "instrument.json"
    forged.write_text("{}\n", encoding="utf-8")
    bindings = protocol.artifacts.model_copy(
        update={"instrument_protocol_uri": str(forged)}
    )

    with pytest.raises(LightweightProtocolError, match="canonical instrument protocol path"):
        verify_lightweight_confirmatory_protocol(
            _changed(protocol, artifacts=bindings)
        )

    bindings = protocol.artifacts.model_copy(
        update={"dataset_manifest_file_sha256": "0" * 64}
    )
    with pytest.raises(LightweightProtocolError, match="manifest file hash"):
        verify_lightweight_confirmatory_protocol(
            _changed(protocol, artifacts=bindings)
        )

    bindings = protocol.artifacts.model_copy(
        update={"dataset_receipt_sha256": "0" * 64}
    )
    with pytest.raises(LightweightProtocolError, match="receipt canonical hash"):
        verify_lightweight_confirmatory_protocol(
            _changed(protocol, artifacts=bindings)
        )


def test_cross_mechanism_pair_reuse_is_rejected() -> None:
    drift, _ = _protocols()
    with pytest.raises(LightweightProtocolError, match="cover data drift"):
        verify_protocol_pair(drift, drift)


def test_unknown_or_malformed_protocol_is_rejected(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(LightweightProtocolError, match="unavailable or invalid"):
        load_lightweight_confirmatory_protocol(missing)

    malformed = tmp_path / "malformed.json"
    malformed.write_text("[]", encoding="utf-8")
    with pytest.raises(LightweightProtocolError, match="unavailable or invalid"):
        load_lightweight_confirmatory_protocol(malformed)
