"""Normalized, execution-free adapters for eligible claim-corpus variants."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final

from pydantic import ValidationError

from aletheia_lab.evaluation.claim_corpus_contracts import (
    ELIGIBLE_VARIANTS,
    ClaimCorpusContractError,
    DiagnosisOutputV2,
    EligibleVariant,
)

Adapter = Callable[[Mapping[str, object]], DiagnosisOutputV2]


def _diagnosis_v2(payload: Mapping[str, object]) -> DiagnosisOutputV2:
    try:
        return DiagnosisOutputV2.model_validate(dict(payload))
    except ValidationError as exc:
        raise ClaimCorpusContractError(
            "variant output does not satisfy diagnosis-output/2"
        ) from exc


def _deterministic_v1(payload: Mapping[str, object]) -> DiagnosisOutputV2:
    """Normalize B0's structured rule claims without parsing prose."""

    source = dict(payload)
    if source.get("schema_version") != "deterministic-diagnosis/1":
        raise ClaimCorpusContractError("B0 output has the wrong source schema")
    allowed = {
        "schema_version",
        "output_status",
        "rule_claims",
        "abstention_reason",
        "parse_failure_code",
        "source_record_sha256",
        "output_sha256",
    }
    if set(source) != allowed:
        raise ClaimCorpusContractError("B0 output contains unregistered fields")
    normalized = {
        "schema_version": "diagnosis-output/2",
        "output_status": source["output_status"],
        "atomic_claims": source["rule_claims"],
        "abstention_reason": source["abstention_reason"],
        "parse_failure_code": source["parse_failure_code"],
        "source_record_sha256": source["source_record_sha256"],
        "output_sha256": source["output_sha256"],
    }
    # B0's output hash is defined over the normalized contract.  This makes the
    # adapter a field renaming boundary, never a semantic transformation.
    return _diagnosis_v2(normalized)


_ADAPTERS: Final[dict[EligibleVariant, Adapter]] = {
    "A1": _diagnosis_v2,
    "A2": _diagnosis_v2,
    "A3": _diagnosis_v2,
    "B0": _deterministic_v1,
    "B1": _diagnosis_v2,
    "B2": _diagnosis_v2,
    "CodeGraph": _diagnosis_v2,
    "FULL": _diagnosis_v2,
}


def adapter_for(variant: str) -> Adapter:
    """Return exactly one registered adapter; B3 and unknown IDs fail closed."""

    if variant not in ELIGIBLE_VARIANTS:
        raise ClaimCorpusContractError("variant is not eligible for claim materialization")
    return _ADAPTERS[variant]


def normalize_variant_output(
    variant: str,
    payload: Mapping[str, object],
) -> DiagnosisOutputV2:
    return adapter_for(variant)(payload)


def adapter_inventory() -> tuple[tuple[EligibleVariant, str], ...]:
    """Expose deterministic registry metadata for the frozen manifest."""

    return tuple(
        (variant, f"{adapter.__module__}:{adapter.__name__}")
        for variant, adapter in _ADAPTERS.items()
    )


__all__ = ["adapter_for", "adapter_inventory", "normalize_variant_output"]
