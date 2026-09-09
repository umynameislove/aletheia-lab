from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.evaluation.claim_validation_v2 import (
    ClaimSupportValidationV2Protocol,
    ClaimValidationV2Error,
    ClosedV1FailureAudit,
    load_v1_failure_audit,
    load_v2_protocol,
    verify_tracked_v2_protocol,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "configs/evaluation/claim_support_validation_v1_failure_audit.json"
PROTOCOL = ROOT / "configs/evaluation/claim_support_validation_v2_protocol.json"


def _json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_tracked_v1_audit_closes_the_observed_cohort_without_overclaiming() -> None:
    audit = load_v1_failure_audit(AUDIT)

    assert audit.parsed_terminal_count == 282
    assert audit.technical_failure_terminal_count == 78
    assert audit.retried_request_count == 100
    assert audit.recovered_on_second_attempt_count == 22
    assert audit.failed_after_second_attempt_count == 78
    assert audit.failure_diagnostics_present_count == 0
    assert audit.exact_provider_failure_cause_known is False
    assert audit.rate_limit_cause_established is False
    assert audit.normalization_rejection_count == 0
    assert audit.blocking_label_strata == ("contradicted", "unsupported")
    assert audit.v1_results_poolable_with_v2 is False


def test_v2_protocol_is_prospective_non_authorizing_and_balanced() -> None:
    protocol = load_v2_protocol(PROTOCOL)

    assert protocol.frozen_before_v2_provider_calls
    assert protocol.source_frame.diagnosis_request_count == 360
    assert protocol.source_frame.execution_schedule == "balanced_interleave_sha256/v1"
    assert protocol.source_frame.relation_request_ceiling == 1440
    assert protocol.source_frame.frame_assignment_uses_relation_outcomes is False
    assert protocol.source_frame.frame_intent_is_design_stratum_not_ground_truth
    assert protocol.reliability_policy.minimum_provider_start_interval_ms == 1000
    assert protocol.reliability_policy.retry_initial_backoff_ms == 5000
    assert protocol.reliability_policy.maximum_relation_output_tokens_per_request == 600
    assert protocol.scientific_scope.missingness_exchangeability_claim_authorized is False
    assert protocol.scientific_scope.technical_failure_sensitivity_analysis_required
    assert protocol.sampling_policy.sample_target == 200
    assert protocol.sampling_policy.automatic_label_quota == 50
    assert protocol.sampling_policy.cluster_unit == "case_family_id"
    assert protocol.sampling_policy.bootstrap_replicates == 2000
    assert protocol.sampling_policy.model_as_human_rater_forbidden
    assert protocol.cost_plan.authorization_created_by_this_protocol is False
    assert protocol.provider_calls_executed is False
    assert protocol.v1_outputs_reused_in_v2_sample is False


def test_tracked_lineage_verifies_and_stays_outcome_free() -> None:
    receipt = verify_tracked_v2_protocol(ROOT)

    assert receipt["status"] == (
        "claim_support_validation_v2_protocol_frozen_implementation_pending"
    )
    assert receipt["v1_exact_selection_feasible"] is False
    assert receipt["provider_calls_executed"] is False
    assert receipt["claims_materialized"] is False
    assert receipt["blind_packets_generated"] is False
    assert receipt["human_annotations_collected"] is False
    assert receipt["main_or_sealed_outcomes_opened"] is False


def test_v1_audit_hash_rejects_count_tampering(tmp_path: Path) -> None:
    payload = _json(AUDIT)
    payload["parsed_terminal_count"] = 283
    path = tmp_path / "audit.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ClaimValidationV2Error, match="unavailable or invalid"):
        load_v1_failure_audit(path)


def test_v2_protocol_rejects_adaptive_reuse_even_with_recomputed_hash() -> None:
    payload = _json(PROTOCOL)
    payload["v1_outputs_reused_in_v2_sample"] = True
    payload["protocol_sha256"] = canonical_execution_sha256(
        {key: value for key, value in payload.items() if key != "protocol_sha256"}
    )

    with pytest.raises(ValidationError, match="v1_outputs_reused_in_v2_sample"):
        ClaimSupportValidationV2Protocol.model_validate(payload)


def test_v1_audit_model_rejects_a_rate_limit_claim_without_evidence() -> None:
    payload = _json(AUDIT)
    payload["rate_limit_cause_established"] = True
    payload["audit_sha256"] = canonical_execution_sha256(
        {key: value for key, value in payload.items() if key != "audit_sha256"}
    )

    with pytest.raises(ValidationError, match="rate_limit_cause_established"):
        ClosedV1FailureAudit.model_validate(payload)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    (
        ("source_frame", "frame_assignment_uses_relation_outcomes", True),
        ("scientific_scope", "missingness_exchangeability_claim_authorized", True),
        ("sampling_policy", "model_as_human_rater_forbidden", False),
    ),
)
def test_v2_protocol_rejects_scientific_boundary_relaxation_even_when_rehashed(
    section: str,
    field: str,
    value: bool,
) -> None:
    payload = _json(PROTOCOL)
    nested = payload[section]
    assert isinstance(nested, dict)
    nested[field] = value
    payload["protocol_sha256"] = canonical_execution_sha256(
        {key: item for key, item in payload.items() if key != "protocol_sha256"}
    )

    with pytest.raises(ValidationError):
        ClaimSupportValidationV2Protocol.model_validate(payload)
