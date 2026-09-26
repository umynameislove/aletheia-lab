"""The forward P5 analysis keeps failure, abstention and claim harm distinct."""

from __future__ import annotations

import pytest

from aletheia_lab.evaluation._diagnosis_main_analysis_contracts import (
    DiagnosisMainClaim,
    DiagnosisMainObservedRecord,
)
from aletheia_lab.evaluation.diagnosis_main_forward_decomposition import classify_output


def _record(
    *,
    technical_status: str = "success",
    output_status: str | None = "completed",
    labels: tuple[str, ...] = (),
) -> DiagnosisMainObservedRecord:
    claims = tuple(
        DiagnosisMainClaim(
            claim_id=f"claim-{index}",
            claim_type="evidence_statement",
            support_label=label,
            citation_required=False,
            citation_present=False,
            citation_ids_valid=False,
        )
        for index, label in enumerate(labels)
    )
    return DiagnosisMainObservedRecord(
        request_id="request-1",
        request_sha256="a" * 64,
        technical_status=technical_status,
        output_status=output_status,
        claims=claims,
    )


def test_technical_failure_is_not_abstention() -> None:
    failure = _record(technical_status="provider_failure", output_status=None)
    assert classify_output(failure, "full", 0.0) == ("technical_failure", 1.0)
    assert classify_output(failure, "missing_key", 0.5) == ("technical_failure", 1.0)


@pytest.mark.parametrize(
    ("condition", "expected_loss"),
    [("full", 1.0), ("noisy", 1.0), ("missing_key", 0.0), ("counterevidence", 0.0)],
)
def test_actual_abstention_uses_frozen_condition_policy(
    condition: str, expected_loss: float
) -> None:
    record = _record(output_status="abstained")
    assert classify_output(record, condition, 0.0) == ("actual_abstention", expected_loss)


def test_zero_claim_is_not_technical_failure() -> None:
    assert classify_output(_record(), "full", 0.0) == ("zero_claim", 1.0)


def test_partial_support_sensitivity_only_changes_claim_scored_loss() -> None:
    record = _record(labels=("fully_supported", "partially_supported", "unsupported"))
    assert classify_output(record, "full", 0.0) == ("claim_scored", 1 / 3)
    assert classify_output(record, "full", 0.5) == ("claim_scored", 0.5)
