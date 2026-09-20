from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from aletheia_lab.diagnosis.main_response import (
    DiagnosisMainResponseError,
    validate_main_provider_output,
)


def _payload(*, citations: list[str], status: str = "completed") -> str:
    claims = []
    if status == "completed":
        claims.append(
            {
                "claim_local_id": "claim-1",
                "claim_type": "cause_assertion",
                "claim_text": "The bounded visible pattern is consistent with drift.",
                "material_parts": [
                    {
                        "part_id": "part-1",
                        "text": "The bounded visible pattern is consistent with drift.",
                    }
                ],
                "visible_evidence_ids": citations,
            }
        )
    return json.dumps(
        {
            "schema_version": "diagnosis-main-provider-output/1",
            "output_status": status,
            "atomic_claims": claims,
            "abstention_reason": (
                "Decisive evidence is missing." if status == "abstained" else None
            ),
        }
    )


@pytest.mark.parametrize("variant", ("A1", "B1", "B2"))
def test_non_citation_arms_require_empty_citation_lists(variant: str) -> None:
    validate_main_provider_output(
        _payload(citations=[]),
        variant=variant,  # type: ignore[arg-type]
        visible_evidence_ids={"evidence-1"},
    )

    with pytest.raises(DiagnosisMainResponseError, match="forbidden"):
        validate_main_provider_output(
            _payload(citations=["evidence-1"]),
            variant=variant,  # type: ignore[arg-type]
            visible_evidence_ids={"evidence-1"},
        )


@pytest.mark.parametrize("variant", ("A2", "A3", "CodeGraph", "FULL"))
def test_citation_arms_require_visible_ids_for_diagnostic_claims(variant: str) -> None:
    validate_main_provider_output(
        _payload(citations=["evidence-1"]),
        variant=variant,  # type: ignore[arg-type]
        visible_evidence_ids={"evidence-1"},
    )

    with pytest.raises(DiagnosisMainResponseError, match="omitted required"):
        validate_main_provider_output(
            _payload(citations=[]),
            variant=variant,  # type: ignore[arg-type]
            visible_evidence_ids={"evidence-1"},
        )


def test_non_visible_citation_and_duplicate_keys_fail_closed() -> None:
    with pytest.raises(DiagnosisMainResponseError, match="outside the visible"):
        validate_main_provider_output(
            _payload(citations=["hidden-evidence"]),
            variant="A3",
            visible_evidence_ids={"evidence-1"},
        )

    duplicated = (
        '{"schema_version":"diagnosis-main-provider-output/1",'
        '"output_status":"completed","output_status":"abstained",'
        '"atomic_claims":[],"abstention_reason":null}'
    )
    with pytest.raises(DiagnosisMainResponseError, match="duplicate"):
        validate_main_provider_output(
            duplicated, variant="A3", visible_evidence_ids=set()
        )


def test_abstention_requires_reason_and_forbids_cause_assertion() -> None:
    validate_main_provider_output(
        _payload(citations=[], status="abstained"),
        variant="A3",
        visible_evidence_ids=set(),
    )

    malformed = json.loads(_payload(citations=[], status="abstained"))
    malformed["abstention_reason"] = None
    with pytest.raises(ValidationError):
        validate_main_provider_output(
            json.dumps(malformed), variant="A3", visible_evidence_ids=set()
        )
