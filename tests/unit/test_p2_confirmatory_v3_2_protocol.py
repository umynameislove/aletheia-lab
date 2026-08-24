"""Adversarial contracts for the outcome-blind v3.2 technical recovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.confirmatory_v3_2_protocol import (
    DEFAULT_V3_2_PROTOCOL_PATH,
    RECOVERY_IMPLEMENTATION_COMMIT,
    V3_1_FAILURE_RECEIPT_SHA256,
    V3_1_PROTOCOL_SHA256,
    V3_2_PROTOCOL_SCHEMA_VERSION,
    V32ConfirmatoryProtocol,
    load_v3_2_confirmatory_protocol,
    verify_v3_2_protocol_artifacts,
    verify_v3_2_technical_delta,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    V3ProtocolError,
    load_v3_confirmatory_protocol,
)

_PROTOCOL_SHA256 = "7cba25f08f4e27007bf17fc837b9f11137123f2f83452378c8ac3db5de3ffe27"


def _payload() -> dict[str, object]:
    value = json.loads(DEFAULT_V3_2_PROTOCOL_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_v3_2_protocol_has_stable_identity_and_no_authority() -> None:
    protocol = load_v3_2_confirmatory_protocol()
    assert protocol.schema_version == V3_2_PROTOCOL_SCHEMA_VERSION
    assert protocol.canonical_sha256() == _PROTOCOL_SHA256
    assert protocol.governance.required_git_tag == "p2-label-noise-shift-factorial-v3.2"
    assert not protocol.governance.registration_authorized_by_this_file
    assert not protocol.governance.execution_authorized_by_this_file
    assert not protocol.model_fitted
    assert not protocol.predictive_metrics_generated
    assert not protocol.sealed_outcomes_generated


def test_v3_2_transitively_binds_predecessor_failure_and_recovery_commit() -> None:
    protocol = load_v3_2_confirmatory_protocol()
    _, manifest, receipt, predecessor, failure = verify_v3_2_protocol_artifacts(protocol)
    assert predecessor.canonical_sha256() == V3_1_PROTOCOL_SHA256
    assert failure.canonical_sha256() == V3_1_FAILURE_RECEIPT_SHA256
    assert protocol.technical_recovery.recovery_implementation_commit == (
        RECOVERY_IMPLEMENTATION_COMMIT
    )
    assert manifest.canonical_sha256() == protocol.artifacts.dataset_manifest_sha256
    assert receipt.canonical_sha256() == protocol.artifacts.dataset_receipt_sha256


def test_scientific_sections_are_identical_to_v3_1() -> None:
    current = load_v3_2_confirmatory_protocol()
    previous = load_v3_confirmatory_protocol()
    verify_v3_2_technical_delta(current, previous)
    current_payload = current.model_dump(mode="json")
    previous_payload = previous.model_dump(mode="json")
    for section in (
        "split_algorithm",
        "dataset_splits",
        "preprocessing",
        "intervention",
        "prior_shift",
        "shift_estimators",
        "inference",
        "decision",
    ):
        assert current_payload[section] == previous_payload[section]


def test_calibration_delta_is_explicit_and_only_technical() -> None:
    protocol = load_v3_2_confirmatory_protocol()
    models = protocol.models
    assert models.calibration_objective_scale == "mean_per_development_record"
    assert models.calibration_gradient_scale == "mean_per_development_record"
    assert models.calibration_hessian_scale == "mean_per_development_record"
    assert models.calibration_tolerance == 1e-8
    assert models.calibration_max_iter == 100
    assert models.calibration_failure_action == "structured_abstain"
    assert not models.calibration_abstention_exposes_partial_fit
    assert models.calibration_abstention_blocks_dataset_scoring
    assert models.non_calibration_defects_remain_hard_failures


@pytest.mark.parametrize(
    ("section", "field", "replacement"),
    [
        ("dataset_splits", "membership_sha256", "0" * 64),
        ("inference", "minimum_practical_effect", 0.01),
        ("intervention", "conditional_rates", [0.1, 0.2]),
        ("intervention", "corruption_seed_last", 6200),
        ("models", "calibration_tolerance", 1e-6),
        ("models", "primary_parameters", ["C=0.1"]),
        ("decision", "additional_grid_or_seed_after_outcomes_forbidden", False),
        ("governance", "execution_authorized_by_this_file", True),
    ],
)
def test_scientific_or_governance_tuning_is_rejected(
    section: str, field: str, replacement: object
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
        changed = V32ConfirmatoryProtocol.model_validate_json(json.dumps(payload))
        verify_v3_2_technical_delta(changed, load_v3_confirmatory_protocol())


@pytest.mark.parametrize(
    "field",
    [
        "predecessor_protocol_sha256",
        "technical_failure_receipt_sha256",
        "technical_failure_receipt_file_sha256",
    ],
)
def test_recovery_artifact_rebinding_fails_closed(field: str) -> None:
    protocol = load_v3_2_confirmatory_protocol()
    changed = protocol.model_copy(
        update={"artifacts": protocol.artifacts.model_copy(update={field: "0" * 64})}
    )
    with pytest.raises(V3ProtocolError, match="another"):
        verify_v3_2_protocol_artifacts(changed)


def test_recovery_cannot_hide_outcomes_or_expand_allowed_changes() -> None:
    payload = _payload()
    payload["observed_effect"] = 0.5
    recovery = payload["technical_recovery"]
    assert isinstance(recovery, dict)
    allowed = recovery["allowed_changes"]
    assert isinstance(allowed, list)
    allowed.append("change_threshold")
    with pytest.raises(ValidationError):
        V32ConfirmatoryProtocol.model_validate_json(json.dumps(payload))


def test_v3_1_identity_remains_unchanged_and_retired() -> None:
    predecessor = load_v3_confirmatory_protocol()
    protocol = load_v3_2_confirmatory_protocol()
    assert predecessor.canonical_sha256() == V3_1_PROTOCOL_SHA256
    assert protocol.artifacts.predecessor_protocol_sha256 == V3_1_PROTOCOL_SHA256
    assert protocol.technical_recovery.predecessor_rerun_forbidden
    assert protocol.technical_recovery.maximum_registered_execution_attempts == 1


def test_protocol_file_is_content_addressable_and_loader_fails_closed(
    tmp_path: Path,
) -> None:
    raw_sha256 = hashlib.sha256(DEFAULT_V3_2_PROTOCOL_PATH.read_bytes()).hexdigest()
    assert raw_sha256 == "fe141be9ea83a1f03d03810fc01d49a112bfbd89390d2a5ac90036b694665122"
    with pytest.raises(V3ProtocolError, match="unavailable"):
        load_v3_2_confirmatory_protocol(tmp_path / "missing.json")
