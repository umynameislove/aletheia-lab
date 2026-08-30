"""Post-P2R scientific-status and downstream-denominator contract tests."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import V3RuntimeError
from aletheia_lab.benchmark.p2.downstream_disposition import (
    load_downstream_disposition_policy,
    verify_frozen_downstream_policy,
)
from aletheia_lab.benchmark.p2.downstream_disposition_v2 import (
    DownstreamDispositionPolicyV2,
    P4P5MechanismFilterManifest,
    load_downstream_disposition_policy_v2,
    load_p4_p5_filter_manifest,
    verify_p4_p5_filter_manifest,
    verify_reconciled_downstream_policy,
)


def _policy_payload() -> dict[str, object]:
    return load_downstream_disposition_policy_v2().model_dump(mode="json")


def _filter_payload() -> dict[str, object]:
    return load_p4_p5_filter_manifest().model_dump(mode="json")


def test_reconciled_policy_preserves_predecessor_and_reports_terminal_denominators() -> None:
    predecessor = verify_frozen_downstream_policy(load_downstream_disposition_policy())
    policy = verify_reconciled_downstream_policy(load_downstream_disposition_policy_v2())
    assert predecessor.denominators.pending_confirmatory_track == (
        "data_drift",
        "preprocessing_mismatch",
    )
    assert policy.predecessor_policy_sha256 == predecessor.canonical_sha256()
    assert policy.n_inventory == 3
    assert policy.n_admitted == 0
    assert policy.denominators.pending_confirmatory_track == ()
    assert policy.denominators.rejected_track == (
        "data_drift",
        "preprocessing_mismatch",
    )
    assert policy.denominators.assumption_limited_track == ("label_noise",)
    assert policy.denominators.diagnostic_ground_truth_track == ()


def test_rejected_mechanism_cannot_be_promoted_by_status_only() -> None:
    payload = _policy_payload()
    mechanisms = payload["mechanisms"]
    assert isinstance(mechanisms, list)
    drift = mechanisms[0]
    assert isinstance(drift, dict)
    drift["scientific_status"] = "admitted"
    with pytest.raises(ValidationError, match="all-gates-pass"):
        DownstreamDispositionPolicyV2.model_validate_json(json.dumps(payload))


def test_rejected_mechanism_requires_complete_closeout_binding() -> None:
    payload = _policy_payload()
    mechanisms = payload["mechanisms"]
    assert isinstance(mechanisms, list)
    drift = mechanisms[0]
    assert isinstance(drift, dict)
    evidence = drift["evidence"]
    assert isinstance(evidence, dict)
    evidence["mechanism_closeout_sha256"] = None
    with pytest.raises(ValidationError, match="execution and closeout evidence"):
        DownstreamDispositionPolicyV2.model_validate_json(json.dumps(payload))


def test_primary_denominator_cannot_include_rejected_mechanism() -> None:
    payload = _policy_payload()
    denominators = payload["denominators"]
    assert isinstance(denominators, dict)
    denominators["primary_admitted_track"] = ["data_drift"]
    with pytest.raises(ValidationError, match="primary admitted denominator"):
        DownstreamDispositionPolicyV2.model_validate_json(json.dumps(payload))


def test_terminal_mechanisms_cannot_return_to_pending_without_new_evidence() -> None:
    payload = _policy_payload()
    denominators = payload["denominators"]
    assert isinstance(denominators, dict)
    denominators["pending_confirmatory_track"] = ["data_drift"]
    with pytest.raises(ValidationError, match="terminal"):
        DownstreamDispositionPolicyV2.model_validate_json(json.dumps(payload))


def test_filter_routes_non_admitted_mechanisms_only_to_validity_endpoints() -> None:
    filters = verify_p4_p5_filter_manifest(load_p4_p5_filter_manifest())
    assert filters.primary_causal_diagnosis_track == ()
    assert filters.assumption_limited_abstention_track == ("label_noise",)
    assert filters.instrument_rejection_track == (
        "data_drift",
        "preprocessing_mismatch",
    )
    assert filters.evidence_accountability_track == (
        "data_drift",
        "preprocessing_mismatch",
        "label_noise",
    )
    assert filters.causal_diagnosis_scoring_for_non_admitted_forbidden is True


def test_filter_rejects_denominator_substitution_and_rubric_erosion() -> None:
    payload = _filter_payload()
    payload["primary_causal_diagnosis_track"] = ["data_drift"]
    with pytest.raises(ValidationError, match="filter routing"):
        P4P5MechanismFilterManifest.model_validate_json(json.dumps(payload))

    payload = _filter_payload()
    endpoints = payload["permitted_rejection_endpoints"]
    assert isinstance(endpoints, list)
    endpoints.pop()
    with pytest.raises(ValidationError, match="filter routing"):
        P4P5MechanismFilterManifest.model_validate_json(json.dumps(payload))


def test_filter_cannot_be_rebound_to_another_policy() -> None:
    payload = _filter_payload()
    payload["disposition_policy_sha256"] = "0" * 64
    filters = P4P5MechanismFilterManifest.model_validate_json(json.dumps(payload))
    with pytest.raises(V3RuntimeError, match="another disposition policy"):
        verify_p4_p5_filter_manifest(filters)
