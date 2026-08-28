"""Strict contracts for the paired P2R v1.1 recovery registration."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.p2r_recovery_protocol import (
    DEFAULT_DATA_DRIFT_RECOVERY_PROTOCOL_PATH,
    DEFAULT_PREPROCESSING_RECOVERY_PROTOCOL_PATH,
    P2R_ARCHIVE_READINESS_SHA256,
    P2R_RECOVERY_IMPLEMENTATION_COMMIT,
    P2R_V1_FAILURE_AUDIT_SHA256,
    P2RRecoveryProtocol,
    P2RRecoveryProtocolError,
    load_p2r_recovery_protocol,
    verify_p2r_recovery_protocol,
    verify_p2r_recovery_protocol_pair,
)

DRIFT_RECOVERY_SHA256 = "e9d9dd57f3e92a0825631c11dbf2d570b01a993a04757c8e08a503f1c76c0003"
PREPROCESSING_RECOVERY_SHA256 = "4a166b04da1b801af6d625703a900d542dc66d001c88a486a0a8984c792230f2"


def _protocols() -> tuple[P2RRecoveryProtocol, P2RRecoveryProtocol]:
    return (
        load_p2r_recovery_protocol(DEFAULT_DATA_DRIFT_RECOVERY_PROTOCOL_PATH),
        load_p2r_recovery_protocol(DEFAULT_PREPROCESSING_RECOVERY_PROTOCOL_PATH),
    )


def test_tracked_recovery_pair_reconciles_the_full_predecessor_chain() -> None:
    drift, prep = _protocols()
    checked_drift, checked_prep = verify_p2r_recovery_protocol_pair(drift, prep)

    assert checked_drift.canonical_sha256() == DRIFT_RECOVERY_SHA256
    assert checked_prep.canonical_sha256() == PREPROCESSING_RECOVERY_SHA256
    assert checked_drift.artifacts.failure_audit_sha256 == P2R_V1_FAILURE_AUDIT_SHA256
    assert checked_drift.readiness.expected_receipt_sha256 == P2R_ARCHIVE_READINESS_SHA256
    assert (
        checked_drift.technical_recovery.recovery_implementation_commit
        == P2R_RECOVERY_IMPLEMENTATION_COMMIT
    )
    assert checked_drift.readiness == checked_prep.readiness
    assert checked_drift.technical_recovery == checked_prep.technical_recovery


def test_recovery_verification_preserves_each_scientific_predecessor() -> None:
    expected = {
        "data_drift": "bad097a4298f7925b314f049a762da2f0e4485a24f40860d667ae936b422c289",
        "preprocessing_bug": ("4fcca028153fce45098e8547608d16231c33f9a78cdc243ff9931d119eca4904"),
    }
    for recovery in _protocols():
        _, predecessor, failure = verify_p2r_recovery_protocol(recovery)
        assert predecessor.canonical_sha256() == expected[recovery.mechanism]
        assert predecessor.mechanism == recovery.mechanism
        assert predecessor.execution.seeds == (8201, 8202, 8203, 8204, 8205)
        assert predecessor.endpoint.minimum_practical_effect == 0.01
        assert predecessor.endpoint.minimum_expected_direction_fraction == 0.8
        assert failure.model_fitted is False
        assert failure.scientific_disposition_generated is False
        assert failure.rerun_forbidden is True


@pytest.mark.parametrize(
    "field",
    [
        "datasets_or_roles_changed",
        "split_membership_changed",
        "model_or_preprocessing_changed",
        "seeds_or_candidate_plan_changed",
        "intervention_or_nuisance_comparator_changed",
        "estimand_metric_or_threshold_changed",
        "exclusion_or_disposition_rule_changed",
        "outcome_information_used_for_recovery",
    ],
)
def test_any_declared_scientific_change_is_rejected(field: str) -> None:
    payload = _protocols()[0].model_dump()
    payload["technical_recovery"][field] = True
    with pytest.raises(ValidationError):
        P2RRecoveryProtocol.model_validate(payload)


def test_undeclared_or_reordered_recovery_delta_is_rejected() -> None:
    payload = _protocols()[0].model_dump()
    payload["technical_recovery"]["allowed_changes"] = tuple(
        reversed(payload["technical_recovery"]["allowed_changes"])
    )
    with pytest.raises(ValidationError, match="undeclared change"):
        P2RRecoveryProtocol.model_validate(payload)


def test_predecessor_hash_tag_and_mechanism_cannot_be_rebound() -> None:
    drift = _protocols()[0]
    for mutation in (
        ("artifacts", "predecessor_protocol_sha256", "0" * 64),
        ("governance", "required_git_tag", "p2r-data-drift-confirmatory-v2"),
    ):
        payload = drift.model_dump()
        section, field, value = mutation
        payload[section][field] = value
        with pytest.raises(ValidationError, match="identity differs"):
            P2RRecoveryProtocol.model_validate(payload)

    payload = drift.model_dump()
    payload["mechanism"] = "preprocessing_bug"
    with pytest.raises(ValidationError, match="identity differs"):
        P2RRecoveryProtocol.model_validate(payload)


def test_recovery_pair_rejects_missing_or_reused_mechanism() -> None:
    drift, prep = _protocols()
    with pytest.raises(P2RRecoveryProtocolError, match="both mechanisms"):
        verify_p2r_recovery_protocol_pair(drift, drift)
    with pytest.raises(P2RRecoveryProtocolError, match="both mechanisms"):
        verify_p2r_recovery_protocol_pair(prep, prep)


def test_protocol_loader_rejects_symlinked_registration(tmp_path: Path) -> None:
    target = tmp_path / "protocol.json"
    target.write_text(DEFAULT_DATA_DRIFT_RECOVERY_PROTOCOL_PATH.read_text(), encoding="utf-8")
    link = tmp_path / "linked.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")
    with pytest.raises(P2RRecoveryProtocolError, match="unavailable or invalid"):
        load_p2r_recovery_protocol(link)


def test_outcome_fields_and_execution_authorization_fail_closed() -> None:
    payload = _protocols()[0].model_dump()
    payload["sealed_outcomes_generated"] = True
    with pytest.raises(ValidationError):
        P2RRecoveryProtocol.model_validate(payload)

    payload = _protocols()[0].model_dump()
    payload["governance"]["execution_authorized_by_this_file"] = True
    with pytest.raises(ValidationError):
        P2RRecoveryProtocol.model_validate(payload)
