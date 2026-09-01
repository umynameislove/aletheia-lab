"""Generative integrity checks for diagnosis development artifacts."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from aletheia_lab.diagnosis.development import (
    DevelopmentVariantResponse,
    build_development_case,
    build_development_evidence_item,
    load_development_plan,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.project.identity import content_sha256

ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = ROOT / "configs/evaluation/diagnosis_development_pilot_plan.json"


@given(
    content=st.text(
        alphabet=st.characters(categories=("L", "N", "P", "Z")),
        min_size=1,
        max_size=200,
    ).filter(str.strip)
)
@settings(max_examples=30, deadline=None)
def test_evidence_identity_is_content_bound(content: str) -> None:
    normalized = content.strip()
    item = build_development_evidence_item(
        evidence_id="devev-generated",
        kind="artifact",
        title="Generated evidence",
        content=normalized,
    )

    assert item.content_sha256 == content_sha256(
        item.content.encode("utf-8", errors="strict")
    )
    changed = item.model_dump(mode="json")
    changed["content"] = f"{item.content} changed"
    with pytest.raises(ValidationError):
        type(item).model_validate(changed)


@given(order=st.permutations((0, 1, 2)))
@settings(max_examples=6, deadline=None)
def test_case_identity_preserves_evidence_order(order: tuple[int, ...]) -> None:
    source = load_development_plan(PLAN_PATH).cases[0]
    evidence = tuple(source.evidence[index] for index in order)

    candidate = build_development_case(
        case_id="devcase-order-property",
        expected_evidence_state="sufficient",
        evidence=evidence,
    )

    canonical = build_development_case(
        case_id="devcase-order-property",
        expected_evidence_state="sufficient",
        evidence=source.evidence,
    ).case_sha256
    if tuple(order) == (0, 1, 2):
        assert candidate.case_sha256 == canonical
    else:
        assert candidate.case_sha256 != canonical


@given(
    field=st.sampled_from(
        (
            "request_sha256",
            "variant_id",
            "response_schema_ref",
            "diagnosis",
            "cited_evidence_ids",
            "abstained",
        )
    )
)
@settings(max_examples=12, deadline=None)
def test_any_response_identity_mutation_is_rejected(field: str) -> None:
    payload: dict[str, object] = {
        "schema_version": "diagnosis-development-response/v1",
        "mode": "development_synthetic",
        "request_sha256": "a" * 64,
        "variant_id": "A1",
        "response_schema_ref": "diagnosis-output/2",
        "diagnosis": "Bounded synthetic response.",
        "cited_evidence_ids": ("devev-visible",),
        "missing_evidence": (),
        "abstained": False,
        "rule_trace": (),
        "native_result": None,
        "synthetic_fixture": True,
        "scientific_interpretation_permitted": False,
    }
    response = DevelopmentVariantResponse.model_validate(
        {**payload, "response_sha256": canonical_execution_sha256(payload)}
    )
    changed = response.model_dump(mode="json")
    replacements: dict[str, object] = {
        "request_sha256": "b" * 64,
        "variant_id": "A2",
        "response_schema_ref": "changed-schema/1",
        "diagnosis": "Changed response.",
        "cited_evidence_ids": ("devev-other",),
        "abstained": True,
    }
    changed[field] = replacements[field]

    with pytest.raises(ValidationError):
        DevelopmentVariantResponse.model_validate(changed)
