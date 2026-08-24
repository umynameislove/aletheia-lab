"""Adversarial contracts for the outcome-blind v3.3 closeout recovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.confirmatory_v3_2_protocol import (
    load_v3_2_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_3_protocol import (
    DEFAULT_V3_3_PROTOCOL_PATH,
    RECOVERY_IMPLEMENTATION_COMMIT,
    V3_2_FAILURE_AUDIT_SHA256,
    V3_2_PROTOCOL_SHA256,
    V3_2_TERMINAL_STORE_SHA256,
    V3_3_PROTOCOL_SCHEMA_VERSION,
    V3_3_PROTOCOL_SHA256,
    V33ConfirmatoryProtocol,
    load_v3_3_confirmatory_protocol,
    verify_v3_3_protocol_artifacts,
    verify_v3_3_technical_delta,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import V3ProtocolError

_PROTOCOL_FILE_SHA256 = "35d99b92a880ecbb52c13cda0cc8a473b7aa31cd3412870a35bc7ef7062ad975"


def _payload() -> dict[str, object]:
    value = json.loads(DEFAULT_V3_3_PROTOCOL_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_v3_3_protocol_has_stable_identity_and_no_execution_authority() -> None:
    protocol = load_v3_3_confirmatory_protocol()
    assert protocol.schema_version == V3_3_PROTOCOL_SCHEMA_VERSION
    assert protocol.canonical_sha256() == V3_3_PROTOCOL_SHA256
    assert protocol.governance.required_git_tag == "p2-label-noise-shift-factorial-v3.3"
    assert not protocol.governance.registration_authorized_by_this_file
    assert not protocol.governance.execution_authorized_by_this_file
    assert not protocol.model_fitted
    assert not protocol.predictive_metrics_generated
    assert not protocol.sealed_outcomes_generated


def test_v3_3_binds_v3_2_failure_store_audit_and_recovery_commit() -> None:
    protocol = load_v3_3_confirmatory_protocol()
    predecessor, audit = verify_v3_3_protocol_artifacts(protocol)
    assert predecessor.canonical_sha256() == V3_2_PROTOCOL_SHA256
    assert audit.canonical_sha256() == V3_2_FAILURE_AUDIT_SHA256
    assert audit.terminal_store_sha256 == V3_2_TERMINAL_STORE_SHA256
    assert protocol.technical_recovery.recovery_implementation_commit == (
        RECOVERY_IMPLEMENTATION_COMMIT
    )
    assert audit.rerun_forbidden
    assert not audit.scientific_disposition_generated
    assert not audit.outcome_artifacts_available


def test_every_scientific_section_is_identical_to_v3_2() -> None:
    current = load_v3_3_confirmatory_protocol()
    previous = load_v3_2_confirmatory_protocol()
    verify_v3_3_technical_delta(current, previous)
    current_payload = current.model_dump(mode="json")
    previous_payload = previous.model_dump(mode="json")
    for section in (
        "split_algorithm",
        "dataset_splits",
        "preprocessing",
        "models",
        "intervention",
        "prior_shift",
        "shift_estimators",
        "inference",
        "decision",
    ):
        assert current_payload[section] == previous_payload[section]


def test_recovery_scope_discloses_reuse_and_limited_independence() -> None:
    recovery = load_v3_3_confirmatory_protocol().technical_recovery
    assert recovery.predecessor_sealed_partition_opened
    assert recovery.same_sealed_partitions_reused_after_disclosed_technical_failure
    assert recovery.recovery_is_not_independent_new_dataset_replication
    assert recovery.numerical_outcomes_unavailable_to_recovery_design
    assert not recovery.outcome_information_used_for_tuning
    assert not recovery.model_or_calibration_changed
    assert not recovery.estimand_metric_or_inference_changed
    assert not recovery.thresholds_or_decision_rule_changed
    assert recovery.maximum_registered_execution_attempts == 1


@pytest.mark.parametrize(
    ("section", "field", "replacement"),
    [
        ("dataset_splits", "membership_sha256", "0" * 64),
        ("models", "calibration_tolerance", 1e-6),
        ("inference", "minimum_practical_effect", 0.01),
        ("intervention", "corruption_seed_last", 6200),
        ("decision", "additional_grid_or_seed_after_outcomes_forbidden", False),
        ("governance", "execution_authorized_by_this_file", True),
    ],
)
def test_scientific_or_governance_drift_is_rejected(
    section: str,
    field: str,
    replacement: object,
) -> None:
    payload = _payload()
    if section == "dataset_splits":
        splits = payload[section]
        assert isinstance(splits, list)
        first = splits[0]
        assert isinstance(first, dict)
        first[field] = replacement
    else:
        nested = payload[section]
        assert isinstance(nested, dict)
        nested[field] = replacement
    with pytest.raises((ValidationError, V3ProtocolError)):
        changed = V33ConfirmatoryProtocol.model_validate_json(json.dumps(payload))
        verify_v3_3_technical_delta(changed, load_v3_2_confirmatory_protocol())


@pytest.mark.parametrize(
    "field",
    [
        "predecessor_v3_2_protocol_sha256",
        "predecessor_v3_2_protocol_file_sha256",
        "predecessor_v3_2_failure_audit_sha256",
        "predecessor_v3_2_failure_audit_file_sha256",
        "predecessor_v3_2_terminal_store_sha256",
    ],
)
def test_predecessor_rebinding_fails_closed(field: str) -> None:
    protocol = load_v3_3_confirmatory_protocol()
    changed = protocol.model_copy(
        update={"artifacts": protocol.artifacts.model_copy(update={field: "0" * 64})}
    )
    with pytest.raises(V3ProtocolError):
        verify_v3_3_protocol_artifacts(changed)


def test_recovery_delta_cannot_expand_or_hide_previous_sealed_open() -> None:
    payload = _payload()
    recovery = payload["technical_recovery"]
    assert isinstance(recovery, dict)
    allowed = recovery["allowed_changes"]
    assert isinstance(allowed, list)
    allowed.append("change_threshold")
    recovery["predecessor_sealed_partition_opened"] = False
    with pytest.raises(ValidationError):
        V33ConfirmatoryProtocol.model_validate_json(json.dumps(payload))


def test_protocol_file_is_content_addressable_and_loader_fails_closed(
    tmp_path: Path,
) -> None:
    raw_sha256 = hashlib.sha256(DEFAULT_V3_3_PROTOCOL_PATH.read_bytes()).hexdigest()
    assert raw_sha256 == _PROTOCOL_FILE_SHA256
    with pytest.raises(V3ProtocolError, match="unavailable"):
        load_v3_3_confirmatory_protocol(tmp_path / "missing.json")
