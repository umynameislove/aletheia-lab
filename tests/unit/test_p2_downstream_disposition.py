"""Tests for frozen P2 mechanism status and downstream denominators."""

from __future__ import annotations

import json

import pytest

from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import V3RuntimeError
from aletheia_lab.benchmark.p2.downstream_disposition import (
    DownstreamDispositionPolicy,
    MechanismDisposition,
    load_downstream_disposition_policy,
    verify_frozen_downstream_policy,
)


def _payload() -> dict[str, object]:
    return load_downstream_disposition_policy().model_dump(mode="json")


def _validate(payload: dict[str, object]) -> DownstreamDispositionPolicy:
    return DownstreamDispositionPolicy.model_validate_json(json.dumps(payload))


def test_frozen_policy_reports_three_inventory_zero_admitted() -> None:
    policy = verify_frozen_downstream_policy(load_downstream_disposition_policy())
    assert policy.n_inventory == 3
    assert policy.n_admitted == 0
    assert policy.denominators.assumption_limited_track == ("label_noise",)
    assert policy.denominators.pending_confirmatory_track == (
        "data_drift",
        "preprocessing_mismatch",
    )
    assert policy.admission_governance.p4_or_p5_outcomes_may_change_status is False


def test_merged_pending_mechanism_cannot_be_declared_admitted_without_study() -> None:
    payload = _payload()
    mechanisms = payload["mechanisms"]
    assert isinstance(mechanisms, list)
    drift = mechanisms[0]
    assert isinstance(drift, dict)
    drift["scientific_status"] = "admitted"
    with pytest.raises(ValueError, match="completed content-addressed study"):
        _validate(payload)


def test_primary_denominator_cannot_include_pending_mechanism() -> None:
    payload = _payload()
    denominators = payload["denominators"]
    assert isinstance(denominators, dict)
    denominators["primary_admitted_track"] = ["data_drift"]
    with pytest.raises(ValueError, match="primary denominator"):
        _validate(payload)


def test_assumption_limited_status_cannot_allow_cross_dataset_claim() -> None:
    payload = _payload()
    mechanisms = payload["mechanisms"]
    assert isinstance(mechanisms, list)
    label_noise = mechanisms[2]
    assert isinstance(label_noise, dict)
    label_noise["cross_dataset_claim_allowed"] = True
    with pytest.raises(ValueError, match="fail-closed abstention"):
        _validate(payload)


def test_admission_requires_cross_environment_gates() -> None:
    with pytest.raises(ValueError, match="every registered gate"):
        MechanismDisposition(
            mechanism_id="label_noise",
            implementation_merged=True,
            scientific_status="admitted",
            registered_study_completed=True,
            protocol_sha256="1" * 64,
            terminal_store_sha256="2" * 64,
            terminal_status="cross_dataset_admission",
            cross_environment_assumption_gates_passed=False,
            cross_dataset_claim_allowed=True,
        )


def test_duplicate_mechanism_inventory_fails_closed() -> None:
    payload = _payload()
    mechanisms = payload["mechanisms"]
    assert isinstance(mechanisms, list)
    mechanisms[1] = json.loads(json.dumps(mechanisms[0]))
    with pytest.raises(ValueError, match="canonical mechanism"):
        _validate(payload)


def test_evidence_snapshot_cannot_be_rebound() -> None:
    payload = _payload()
    payload["evidence_snapshot_commit"] = "0" * 40
    policy = _validate(payload)
    with pytest.raises(V3RuntimeError, match="another evidence snapshot"):
        verify_frozen_downstream_policy(policy)


def test_abstention_rubric_cannot_be_dropped_silently() -> None:
    payload = _payload()
    rubric = payload["abstention_evaluation"]
    assert isinstance(rubric, dict)
    required = rubric["required_facts"]
    assert isinstance(required, list)
    required.pop()
    policy = _validate(payload)
    with pytest.raises(V3RuntimeError, match="rubric has changed"):
        verify_frozen_downstream_policy(policy)
