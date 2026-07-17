"""Provider boundary and deterministic offline adapter for P1-G6."""

from __future__ import annotations

from typing import Protocol

from aletheia_lab.diagnosis.schema import (
    OUTPUT_SCHEMA_VERSION,
    DiagnosisRequest,
    PilotVariant,
    ProviderIdentity,
    ProviderResponse,
    UsageRecord,
)
from aletheia_lab.evidence.schema import canonical_json, sha256_text


class AdapterError(RuntimeError):
    """Typed provider failure that is safe to persist in an attempt record."""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


class DiagnosisAdapter(Protocol):
    """Minimal provider interface; adapters may not mutate a frozen request."""

    @property
    def identity(self) -> ProviderIdentity: ...

    def complete(self, request: DiagnosisRequest) -> ProviderResponse: ...


class DeterministicMockAdapter:
    """Offline contract adapter used for tests and byte-reproducible dry runs.

    It is not a model-quality baseline and its outputs must never be reported as
    empirical LLM performance.
    """

    def __init__(self) -> None:
        self._identity = ProviderIdentity(
            provider="deterministic-mock",
            model="p1-contract-fixture",
            version="1",
        )

    @property
    def identity(self) -> ProviderIdentity:
        return self._identity

    def complete(self, request: DiagnosisRequest) -> ProviderResponse:
        ids = tuple(item.evidence_id for item in request.diagnosis_view.items)
        if request.variant == PilotVariant.A3_EVIDENCE_CONTRACT and len(ids) < 3:
            payload = {
                "schema_version": OUTPUT_SCHEMA_VERSION,
                "root_cause_hypothesis": (
                    "The visible observation is insufficient to identify a bounded cause."
                ),
                "claim_strength": "observation",
                "supporting_evidence_ids": list(ids[:1]),
                "counterevidence_ids": [],
                "missing_evidence": [
                    "Reference distribution, comparison statistic, and outcome metric comparison"
                ],
                "confidence": 0.2,
                "abstain": True,
            }
        elif request.variant == PilotVariant.A3_EVIDENCE_CONTRACT:
            payload = {
                "schema_version": OUTPUT_SCHEMA_VERSION,
                "root_cause_hypothesis": (
                    "The candidate/reference differences support a bounded distribution-shift "
                    "hypothesis; they do not establish an unrestricted causal conclusion."
                ),
                "claim_strength": "bounded_causal_hypothesis",
                "supporting_evidence_ids": list(ids[: min(3, len(ids))]),
                "counterevidence_ids": [],
                "missing_evidence": [],
                "confidence": 0.7,
                "abstain": False,
            }
        else:
            payload = {
                "schema_version": OUTPUT_SCHEMA_VERSION,
                "root_cause_hypothesis": "A distribution change may explain the observed behavior.",
                "claim_strength": "bounded_causal_hypothesis",
                "supporting_evidence_ids": [],
                "counterevidence_ids": [],
                "missing_evidence": [],
                "confidence": 0.55,
                "abstain": False,
            }
        raw_text = canonical_json(payload)
        response_seed = canonical_json(
            {"request_id": request.request_id, "raw_sha256": sha256_text(raw_text)}
        )
        input_size = sum(len(message["content"]) for message in request.provider_messages())
        return ProviderResponse(
            response_id=f"mockresp-{sha256_text(response_seed)}",
            provider_identity=self.identity,
            raw_text=raw_text,
            usage=UsageRecord(
                input_tokens=max(1, input_size // 4),
                output_tokens=max(1, len(raw_text) // 4),
                estimated_cost_usd=0.0,
            ),
            latency_ms=0.0,
        )
