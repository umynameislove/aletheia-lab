"""Property checks for policy-bound variant implementations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from aletheia_lab.diagnosis.variant_registry import (
    DiagnosisVariantRequestBinding,
    VariantRegistryError,
    bind_variant_request,
    build_variant_registry,
    load_variant_registry,
)
from aletheia_lab.evaluation.variant_fairness import (
    REQUIRED_VARIANTS,
    DiagnosisVariantFairnessFreeze,
)

ROOT = Path(__file__).resolve().parents[2]
TRACKED_FREEZE = ROOT / "configs/evaluation/diagnosis_variant_fairness_freeze.json"


def _payload() -> dict[str, object]:
    payload = json.loads(TRACKED_FREEZE.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


@given(variant_id=st.sampled_from(REQUIRED_VARIANTS))
@settings(max_examples=len(REQUIRED_VARIANTS), deadline=None)
def test_any_variant_factory_alias_is_detected(variant_id: str) -> None:
    payload = _payload()
    variants = payload["variants"]
    assert isinstance(variants, list)
    target = next(item for item in variants if item["variant_id"] == variant_id)
    target["implementation_reference"] = (
        "aletheia_lab.diagnosis.variant_registry:build_a1_variant"
    )
    freeze = DiagnosisVariantFairnessFreeze.model_validate_json(json.dumps(payload))

    if variant_id == "A1":
        build_variant_registry(freeze)
    else:
        with pytest.raises(VariantRegistryError):
            build_variant_registry(freeze)


@given(
    field=st.sampled_from(
        (
            "registry_sha256",
            "variant_content_sha256",
            "implementation_source_sha256",
            "context_sha256",
            "evidence_content_sha256",
        )
    ),
    replacement=st.sampled_from(("0" * 64, "f" * 64)),
)
@settings(max_examples=10, deadline=None)
def test_any_request_identity_mutation_fails_closed(field: str, replacement: str) -> None:
    registry = load_variant_registry(TRACKED_FREEZE)
    binding = bind_variant_request(
        registry,
        variant_id="A3",
        context_sha256="a" * 64,
        evidence_content_sha256="b" * 64,
    )
    payload = binding.model_dump(mode="json")
    payload[field] = replacement

    with pytest.raises(ValidationError):
        DiagnosisVariantRequestBinding.model_validate_json(json.dumps(payload))
