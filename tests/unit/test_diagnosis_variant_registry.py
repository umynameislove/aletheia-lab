"""Contracts for the complete outcome-blind diagnosis variant registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.diagnosis.variant_registry import (
    DiagnosisVariantRegistry,
    DiagnosisVariantRequestBinding,
    VariantRegistryError,
    bind_variant_request,
    build_variant_registry,
    load_variant_registry,
    validate_variant_request_binding,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.variant_fairness import (
    MATCHED_MODEL_VARIANTS,
    REQUIRED_VARIANTS,
    DiagnosisVariantFairnessFreeze,
)

ROOT = Path(__file__).resolve().parents[2]
TRACKED_FREEZE = ROOT / "configs/evaluation/diagnosis_variant_fairness_freeze.json"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _payload() -> dict[str, object]:
    payload = json.loads(TRACKED_FREEZE.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _freeze(payload: dict[str, object]) -> DiagnosisVariantFairnessFreeze:
    return DiagnosisVariantFairnessFreeze.model_validate_json(json.dumps(payload))


def _variant(payload: dict[str, object], variant_id: str) -> dict[str, object]:
    variants = payload["variants"]
    assert isinstance(variants, list)
    result = next(item for item in variants if item["variant_id"] == variant_id)
    assert isinstance(result, dict)
    return result


def test_tracked_registry_resolves_exactly_nine_unique_implementations() -> None:
    registry = load_variant_registry(TRACKED_FREEZE)

    assert tuple(item.variant_id for item in registry.variants) == REQUIRED_VARIANTS
    assert len({item.implementation_reference for item in registry.variants}) == 9
    assert len({item.variant_content_sha256 for item in registry.variants}) == 9
    assert registry == load_variant_registry(TRACKED_FREEZE)


def test_registry_preserves_registered_strategies_and_reporting_strata() -> None:
    registry = load_variant_registry(TRACKED_FREEZE)
    by_id = {item.variant_id: item for item in registry.variants}

    assert by_id["B0"].strategy == "deterministic_rules"
    assert by_id["B3"].strategy == "native_external"
    assert by_id["B2"].strategy == "multi_turn_rag"
    assert by_id["CodeGraph"].strategy == "codegraph_retrieval"
    assert by_id["FULL"].strategy == "full_system"
    assert {by_id[item].pooling_policy for item in MATCHED_MODEL_VARIANTS} == {
        "matched_primary"
    }
    assert by_id["B0"].pooling_policy == "separate"
    assert by_id["B3"].pooling_policy == "external_only"
    assert by_id["CodeGraph"].pooling_policy == "separate"
    assert by_id["FULL"].pooling_policy == "separate"


def test_matched_variants_share_model_and_information_hashes() -> None:
    registry = load_variant_registry(TRACKED_FREEZE)
    by_id = {item.variant_id: item for item in registry.variants}

    assert len({by_id[item].model_policy_sha256 for item in MATCHED_MODEL_VARIANTS}) == 1
    assert len(
        {by_id[item].information_budget_sha256 for item in MATCHED_MODEL_VARIANTS}
    ) == 1
    assert by_id["B0"].model_policy_sha256 is None
    assert by_id["B3"].model_policy_sha256 is None


def test_capabilities_exactly_expose_only_registered_information_paths() -> None:
    registry = load_variant_registry(TRACKED_FREEZE)
    by_id = {item.variant_id: item for item in registry.variants}

    assert all(
        not item.capabilities.web
        and not item.capabilities.shell
        and not item.capabilities.project_execution
        for item in registry.variants
    )
    assert by_id["B2"].capabilities.retrieval
    assert not by_id["B2"].capabilities.code_graph
    assert by_id["CodeGraph"].capabilities.code_graph
    assert by_id["FULL"].capabilities.lineage_visible
    assert by_id["FULL"].capabilities.conversation_audit_visible
    assert not by_id["A3"].capabilities.lineage_visible


@pytest.mark.parametrize("variant_id", REQUIRED_VARIANTS)
def test_request_binding_carries_every_policy_and_content_hash(variant_id: str) -> None:
    registry = load_variant_registry(TRACKED_FREEZE)
    variant = registry.require(variant_id)  # type: ignore[arg-type]
    ledger = SHA_C if variant.capabilities.tool_ledger_required else None

    binding = bind_variant_request(
        registry,
        variant_id=variant_id,  # type: ignore[arg-type]
        context_sha256=SHA_A,
        evidence_content_sha256=SHA_B,
        tool_ledger_sha256=ledger,
    )

    assert binding.variant_content_sha256 == variant.variant_content_sha256
    assert binding.implementation_version == variant.implementation_version
    assert binding.implementation_source_sha256 == variant.implementation_source_sha256
    assert binding.context_sha256 == SHA_A
    assert binding.evidence_content_sha256 == SHA_B
    assert binding.tool_ledger_sha256 == ledger


def test_tool_ledger_presence_is_exact_not_optional_or_ambient() -> None:
    registry = load_variant_registry(TRACKED_FREEZE)

    with pytest.raises(VariantRegistryError, match="tool ledger"):
        bind_variant_request(
            registry,
            variant_id="B2",
            context_sha256=SHA_A,
            evidence_content_sha256=SHA_B,
        )
    with pytest.raises(VariantRegistryError, match="tool ledger"):
        bind_variant_request(
            registry,
            variant_id="B1",
            context_sha256=SHA_A,
            evidence_content_sha256=SHA_B,
            tool_ledger_sha256=SHA_C,
        )


def test_fully_rehashed_wrong_factory_is_rejected() -> None:
    payload = _payload()
    _variant(payload, "A1")["implementation_reference"] = (
        "aletheia_lab.diagnosis.variant_registry:build_a2_variant"
    )

    with pytest.raises(VariantRegistryError, match="wrong variant"):
        build_variant_registry(_freeze(payload))


@pytest.mark.parametrize(
    "reference, message",
    [
        ("aletheia_lab.diagnosis.variant_registry:missing", "does not resolve"),
        ("aletheia_lab.diagnosis.variant_registry:VARIANT_KERNEL_SCHEMA_VERSION", "not callable"),
        ("json:dumps", "outside the package"),
    ],
)
def test_unknown_noncallable_and_external_factories_fail_closed(
    reference: str,
    message: str,
) -> None:
    payload = _payload()
    _variant(payload, "A1")["implementation_reference"] = reference

    with pytest.raises(VariantRegistryError, match=message):
        build_variant_registry(_freeze(payload))


def test_policy_capability_mismatch_is_rejected_even_when_reference_resolves() -> None:
    payload = _payload()
    _variant(payload, "A1")["tool_policy_ref"] = "rag_only_v1"

    with pytest.raises(VariantRegistryError, match="capabilities differ"):
        build_variant_registry(_freeze(payload))


def test_implementation_version_must_equal_frozen_prompt_version() -> None:
    payload = _payload()
    prompts = payload["prompt_policies"]
    assert isinstance(prompts, dict)
    prompt = prompts["A1"]
    assert isinstance(prompt, dict)
    prompt["prompt_version"] = "diagnosis-a1/2"
    prompt["prompt_content_sha256"] = canonical_execution_sha256(
        {
            "prompt_version": prompt["prompt_version"],
            "instruction_contract": prompt["instruction_contract"],
            "response_schema_ref": prompt["response_schema_ref"],
        }
    )

    with pytest.raises(VariantRegistryError, match="version differs"):
        build_variant_registry(_freeze(payload))


def test_registry_hash_tamper_is_rejected() -> None:
    payload = load_variant_registry(TRACKED_FREEZE).model_dump(mode="json")
    payload["registry_sha256"] = "0" * 64

    with pytest.raises(ValidationError, match="registry_sha256"):
        DiagnosisVariantRegistry.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "field",
    (
        "variant_content_sha256",
        "implementation_source_sha256",
        "information_budget_sha256",
        "tool_policy_sha256",
        "evidence_policy_sha256",
        "prompt_policy_sha256",
        "context_sha256",
        "evidence_content_sha256",
    ),
)
def test_request_binding_rejects_every_hash_tamper(field: str) -> None:
    registry = load_variant_registry(TRACKED_FREEZE)
    binding = bind_variant_request(
        registry,
        variant_id="B1",
        context_sha256=SHA_A,
        evidence_content_sha256=SHA_B,
    )
    payload = binding.model_dump(mode="json")
    payload[field] = "0" * 64

    with pytest.raises(ValidationError, match="binding_sha256"):
        DiagnosisVariantRequestBinding.model_validate_json(json.dumps(payload))


def test_unknown_variant_cannot_be_selected() -> None:
    registry = load_variant_registry(TRACKED_FREEZE)

    with pytest.raises(VariantRegistryError, match="absent"):
        registry.require("UNKNOWN")  # type: ignore[arg-type]


def test_fully_rehashed_binding_cannot_escape_authoritative_registry() -> None:
    registry = load_variant_registry(TRACKED_FREEZE)
    binding = bind_variant_request(
        registry,
        variant_id="A3",
        context_sha256=SHA_A,
        evidence_content_sha256=SHA_B,
    )
    payload = binding.model_dump(mode="json")
    payload["prompt_policy_sha256"] = "0" * 64
    payload["binding_sha256"] = canonical_execution_sha256(
        {key: value for key, value in payload.items() if key != "binding_sha256"}
    )
    forged = DiagnosisVariantRequestBinding.model_validate(payload)

    with pytest.raises(VariantRegistryError, match="authoritative registry"):
        validate_variant_request_binding(
            registry,
            forged,
            context_sha256=SHA_A,
            evidence_content_sha256=SHA_B,
            tool_ledger_sha256=None,
        )


def test_duplicate_variant_is_rejected_before_registry_resolution() -> None:
    payload = _payload()
    variants = payload["variants"]
    assert isinstance(variants, list)
    variants[1] = variants[0]

    with pytest.raises(ValidationError, match="variant census"):
        _freeze(payload)
