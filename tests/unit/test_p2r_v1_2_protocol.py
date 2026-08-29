"""Strict contracts for the prospective P2R v1.2 amendment pair."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.p2r_v1_2_protocol import (
    DEFAULT_DATA_DRIFT_V1_2_PROTOCOL_PATH,
    DEFAULT_PREPROCESSING_V1_2_PROTOCOL_PATH,
    P2R_V1_1_FAILURE_AUDIT_SHA256,
    P2R_V1_1_FEASIBILITY_SHA256,
    P2R_V1_1_TERMINAL_STORE_SHA256,
    P2R_V1_2_AMENDMENT_IMPLEMENTATION_COMMIT,
    P2RV12MethodologicalAmendmentProtocol,
    P2RV12ProtocolError,
    load_p2r_v1_2_protocol,
    verify_p2r_v1_2_protocol,
    verify_p2r_v1_2_protocol_pair,
)

DRIFT_PROTOCOL_SHA256 = "1d811be01d2faf2500b79d21bbd034752a2e8e9d349dbcd11bf8cfa0af6c24b3"
PREPROCESSING_PROTOCOL_SHA256 = "59ebbec62f2e34258b1d39617cfad65ac993bd19fd2167255c78e313c5ad4993"


def _protocols() -> tuple[
    P2RV12MethodologicalAmendmentProtocol,
    P2RV12MethodologicalAmendmentProtocol,
]:
    return (
        load_p2r_v1_2_protocol(DEFAULT_DATA_DRIFT_V1_2_PROTOCOL_PATH),
        load_p2r_v1_2_protocol(DEFAULT_PREPROCESSING_V1_2_PROTOCOL_PATH),
    )


def test_tracked_pair_reconciles_audited_methodological_chain() -> None:
    drift, prep = verify_p2r_v1_2_protocol_pair(*_protocols())

    assert drift.canonical_sha256() == DRIFT_PROTOCOL_SHA256
    assert prep.canonical_sha256() == PREPROCESSING_PROTOCOL_SHA256
    assert drift.artifacts.failure_audit_sha256 == P2R_V1_1_FAILURE_AUDIT_SHA256
    assert drift.artifacts.feasibility_receipt_sha256 == P2R_V1_1_FEASIBILITY_SHA256
    assert drift.artifacts.predecessor_terminal_store_sha256 == P2R_V1_1_TERMINAL_STORE_SHA256
    assert (
        drift.artifacts.amendment_implementation_commit == P2R_V1_2_AMENDMENT_IMPLEMENTATION_COMMIT
    )
    assert drift.scientific_invariants == prep.scientific_invariants
    assert tuple(item.selected_target_feature for item in drift.datasets) == tuple(
        item.selected_target_feature for item in prep.datasets
    )


def test_only_outcome_blind_target_change_is_admitted() -> None:
    drift, prep = _protocols()
    primary, replication = drift.datasets

    assert primary.selected_target_feature == "EDUCATION"
    assert primary.predecessor_target_feature == "EDUCATION"
    assert primary.target_changed is False
    assert (primary.target_row_count, primary.reserve_row_count) == (1200, 300)
    assert primary.minimum_capacity_count == 2795

    assert replication.selected_target_feature == "OperatingSystems"
    assert replication.predecessor_target_feature == "VisitorType"
    assert replication.target_changed is True
    assert (replication.target_row_count, replication.reserve_row_count) == (493, 124)
    assert replication.minimum_capacity_count == 1140
    assert replication.minimum_capacity_count >= (
        replication.target_row_count + replication.reserve_row_count
    )
    assert tuple(item.selected_target_feature for item in prep.datasets) == (
        "EDUCATION",
        "OperatingSystems",
    )


def test_inherited_scientific_sections_remain_exact() -> None:
    for amendment in _protocols():
        checked, predecessor, recovery, audit, feasibility = verify_p2r_v1_2_protocol(amendment)
        assert checked.mechanism == predecessor.mechanism == recovery.mechanism
        assert predecessor.execution.seeds == (8201, 8202, 8203, 8204, 8205)
        assert predecessor.endpoint.minimum_practical_effect == 0.01
        assert predecessor.endpoint.minimum_expected_direction_fraction == 0.8
        assert audit.rerun_forbidden is True
        assert audit.v1_1_attempt_retired is True
        assert audit.scientific_negative_result is False
        assert feasibility.target_values_used_for_capacity_or_selection is False
        assert feasibility.model_fitted is False
        assert feasibility.predictive_metrics_generated is False


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("artifacts", "failure_audit_sha256", "0" * 64),
        ("artifacts", "feasibility_receipt_sha256", "1" * 64),
        ("artifacts", "predecessor_terminal_store_sha256", "2" * 64),
        ("artifacts", "amendment_implementation_commit", "3" * 40),
        ("governance", "required_git_tag", "unregistered-replacement"),
    ],
)
def test_evidence_chain_and_registration_identity_cannot_be_rebound(
    section: str,
    field: str,
    value: object,
) -> None:
    payload = _protocols()[0].model_dump()
    payload[section][field] = value
    with pytest.raises((ValidationError, P2RV12ProtocolError)):
        candidate = P2RV12MethodologicalAmendmentProtocol.model_validate(payload)
        verify_p2r_v1_2_protocol(candidate)


@pytest.mark.parametrize(
    ("dataset_index", "field", "value"),
    [
        (0, "selected_target_feature", "OperatingSystems"),
        (0, "split_membership_sha256", "0" * 64),
        (1, "sealed_membership_sha256", "1" * 64),
        (1, "target_row_count", 492),
        (1, "reserve_row_count", 123),
        (1, "minimum_capacity_count", 1139),
        (1, "data_drift_capacity_count", 600),
        (1, "selected_feature_capacity_sha256", "2" * 64),
    ],
)
def test_dataset_identity_dose_and_capacity_fail_closed(
    dataset_index: int,
    field: str,
    value: object,
) -> None:
    payload = _protocols()[0].model_dump()
    payload["datasets"][dataset_index][field] = value
    with pytest.raises((ValidationError, P2RV12ProtocolError)):
        candidate = P2RV12MethodologicalAmendmentProtocol.model_validate(payload)
        verify_p2r_v1_2_protocol(candidate)


@pytest.mark.parametrize(
    "field",
    [
        "datasets_or_roles_changed",
        "split_or_sealed_membership_changed",
        "model_or_preprocessing_changed",
        "seeds_or_candidate_count_changed",
        "intervention_dose_changed",
        "nuisance_comparator_semantics_changed",
        "endpoint_estimand_metric_or_threshold_changed",
        "exclusion_or_disposition_rule_changed",
        "predictive_outcomes_used_for_selection_or_tuning",
    ],
)
def test_any_undeclared_scientific_change_is_rejected(field: str) -> None:
    payload = _protocols()[0].model_dump()
    payload["scientific_invariants"][field] = True
    with pytest.raises(ValidationError):
        P2RV12MethodologicalAmendmentProtocol.model_validate(payload)


def test_allowed_delta_order_and_dose_are_frozen() -> None:
    protocol = _protocols()[0]
    payload = protocol.model_dump()
    payload["scientific_invariants"]["allowed_changes"] = tuple(
        reversed(protocol.scientific_invariants.allowed_changes)
    )
    with pytest.raises(ValidationError, match="undeclared methodological change"):
        P2RV12MethodologicalAmendmentProtocol.model_validate(payload)

    payload = protocol.model_dump()
    payload["scientific_invariants"]["declared_manipulation_magnitude"] = 0.19
    with pytest.raises(ValidationError, match="declared dose"):
        P2RV12MethodologicalAmendmentProtocol.model_validate(payload)


def test_pair_rejects_reused_mechanism() -> None:
    drift, prep = _protocols()
    with pytest.raises(P2RV12ProtocolError, match="both mechanisms"):
        verify_p2r_v1_2_protocol_pair(drift, drift)
    with pytest.raises(P2RV12ProtocolError, match="both mechanisms"):
        verify_p2r_v1_2_protocol_pair(prep, prep)


def test_pair_rejects_cross_mechanism_target_divergence() -> None:
    drift, prep = _protocols()
    payload = prep.model_dump()
    payload["datasets"][1]["selected_target_feature"] = "EDUCATION"
    with pytest.raises(ValidationError):
        P2RV12MethodologicalAmendmentProtocol.model_validate(payload)
    assert tuple(item.selected_target_feature for item in drift.datasets) == tuple(
        item.selected_target_feature for item in prep.datasets
    )


def test_outcome_or_execution_authorization_fails_closed() -> None:
    for section, field in (
        (None, "model_fitted"),
        (None, "predictive_metrics_generated"),
        (None, "sealed_outcomes_generated"),
        ("governance", "registration_authorized_by_this_file"),
        ("governance", "execution_authorized_by_this_file"),
    ):
        payload = _protocols()[0].model_dump()
        if section is None:
            payload[field] = True
        else:
            payload[section][field] = True
        with pytest.raises(ValidationError):
            P2RV12MethodologicalAmendmentProtocol.model_validate(payload)


def test_protocol_loader_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "protocol.json"
    target.write_text(DEFAULT_DATA_DRIFT_V1_2_PROTOCOL_PATH.read_text(), encoding="utf-8")
    link = tmp_path / "linked.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")
    with pytest.raises(P2RV12ProtocolError, match="unavailable or invalid"):
        load_p2r_v1_2_protocol(link)
