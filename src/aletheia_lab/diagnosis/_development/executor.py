"""Deterministic executor boundary for synthetic development validation."""

from __future__ import annotations

from typing import Protocol

from aletheia_lab.diagnosis._development.contracts import (
    DEVELOPMENT_MODE,
    DEVELOPMENT_RESPONSE_SCHEMA_VERSION,
    DevelopmentCase,
    DevelopmentVariantRequest,
    DevelopmentVariantResponse,
)
from aletheia_lab.diagnosis._development.policy import response_schema_ref
from aletheia_lab.diagnosis.variant_registry import ResolvedDiagnosisVariant
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256


class DevelopmentVariantExecutor(Protocol):
    """Development executor boundary; production adapters are intentionally excluded."""

    @property
    def identity(self) -> str: ...

    @property
    def external_calls(self) -> bool: ...

    def execute(
        self,
        request: DevelopmentVariantRequest,
        variant: ResolvedDiagnosisVariant,
        case: DevelopmentCase,
    ) -> DevelopmentVariantResponse: ...


class DeterministicDevelopmentExecutor:
    """Pure deterministic executor used only to exercise all frozen paths."""

    @property
    def identity(self) -> str:
        return "deterministic-development-executor/1"

    @property
    def external_calls(self) -> bool:
        return False

    def execute(
        self,
        request: DevelopmentVariantRequest,
        variant: ResolvedDiagnosisVariant,
        case: DevelopmentCase,
    ) -> DevelopmentVariantResponse:
        citations = (
            (case.evidence[0].evidence_id,) if variant.capabilities.citation_required else ()
        )
        abstained = (
            variant.capabilities.abstention_required
            and case.expected_evidence_state == "insufficient"
        )
        missing = ("additional corroborating trace",) if abstained else ()
        rule_trace = (
            ("visible-feature-check", "bounded-rule-result")
            if variant.strategy == "deterministic_rules"
            else ()
        )
        native_result = (
            "native fixture contract exercised" if variant.strategy == "native_external" else None
        )
        diagnosis = (
            "Evidence is insufficient for a bounded diagnosis."
            if abstained
            else f"Synthetic {variant.variant_id} path produced a bounded development response."
        )
        payload = {
            "schema_version": DEVELOPMENT_RESPONSE_SCHEMA_VERSION,
            "mode": DEVELOPMENT_MODE,
            "request_sha256": request.request_sha256,
            "variant_id": variant.variant_id,
            "response_schema_ref": response_schema_ref(variant),
            "diagnosis": diagnosis,
            "cited_evidence_ids": citations,
            "missing_evidence": missing,
            "abstained": abstained,
            "rule_trace": rule_trace,
            "native_result": native_result,
            "synthetic_fixture": True,
            "scientific_interpretation_permitted": False,
        }
        return DevelopmentVariantResponse.model_validate(
            {**payload, "response_sha256": canonical_execution_sha256(payload)}
        )
