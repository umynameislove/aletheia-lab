"""Provider boundary and deterministic offline adapter for P1-G6."""

from __future__ import annotations

import importlib.metadata
import os
import time
from copy import deepcopy
from typing import Protocol, cast

from aletheia_lab.diagnosis.schema import (
    OUTPUT_SCHEMA_VERSION,
    DiagnosisRequest,
    PilotVariant,
    ProviderIdentity,
    ProviderResponse,
    UsageRecord,
)
from aletheia_lab.evidence.schema import canonical_json, sha256_text

OPENAI_SDK_VERSION = "2.46.0"
OPENAI_MODEL_SNAPSHOT = "gpt-4.1-2025-04-14"
OPENAI_MODEL_VERSION = "2025-04-14"
OPENAI_INPUT_PRICE_PER_MILLION = 2.0
OPENAI_OUTPUT_PRICE_PER_MILLION = 8.0

_OPENAI_OUTPUT_JSON_SCHEMA: dict[str, object] = {
    "type": "json_schema",
    "json_schema": {
        "name": "p1_diagnosis_output",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "schema_version": {"type": "string", "const": "diagnosis-output/1"},
                "root_cause_hypothesis": {"type": "string"},
                "claim_strength": {
                    "type": "string",
                    "enum": ["observation", "comparison", "bounded_causal_hypothesis"],
                },
                "supporting_evidence_ids": {"type": "array", "items": {"type": "string"}},
                "counterevidence_ids": {"type": "array", "items": {"type": "string"}},
                "missing_evidence": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number"},
                "abstain": {"type": "boolean"},
            },
            "required": [
                "schema_version",
                "root_cause_hypothesis",
                "claim_strength",
                "supporting_evidence_ids",
                "counterevidence_ids",
                "missing_evidence",
                "confidence",
                "abstain",
            ],
        },
    },
}


def openai_output_json_schema() -> dict[str, object]:
    """Return an isolated copy of the one frozen provider response contract."""

    return deepcopy(_OPENAI_OUTPUT_JSON_SCHEMA)


class _OpenAICompletions(Protocol):
    def create(self, **kwargs: object) -> object: ...


class _OpenAIChat(Protocol):
    completions: _OpenAICompletions


class OpenAIClient(Protocol):
    chat: _OpenAIChat


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


class OpenAIChatCompletionsAdapter:
    """Pinned GPT-4.1 adapter; construction alone never performs a network call."""

    def __init__(
        self,
        *,
        client: OpenAIClient,
    ) -> None:
        self._client = client
        self._model_snapshot = OPENAI_MODEL_SNAPSHOT
        self._identity = ProviderIdentity(
            provider="openai",
            model=OPENAI_MODEL_SNAPSHOT,
            version=OPENAI_MODEL_VERSION,
        )

    @classmethod
    def from_environment(
        cls,
    ) -> OpenAIChatCompletionsAdapter:
        """Create the client only after checking the SDK pin and environment secret."""

        try:
            installed_version = importlib.metadata.version("openai")
        except importlib.metadata.PackageNotFoundError as exc:
            raise AdapterError("missing_sdk", "frozen OpenAI SDK is not installed") from exc
        if installed_version != OPENAI_SDK_VERSION:
            raise AdapterError("sdk_version_mismatch", "installed OpenAI SDK is not frozen")
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise AdapterError("missing_api_key", "OPENAI_API_KEY is not set")
        from openai import OpenAI

        client = cast(OpenAIClient, OpenAI(api_key=api_key))
        return cls(client=client)

    @property
    def identity(self) -> ProviderIdentity:
        return self._identity

    def complete(self, request: DiagnosisRequest) -> ProviderResponse:
        """Send one frozen request and return exact model text plus provider metadata."""

        if request.provider_identity != self.identity:
            raise AdapterError("request_identity_mismatch", "request is not bound to this adapter")
        started = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=self._model_snapshot,
                messages=list(request.provider_messages()),
                response_format=openai_output_json_schema(),
                temperature=request.settings.temperature,
                top_p=request.settings.top_p,
                seed=request.settings.seed,
                max_tokens=request.settings.max_output_tokens,
                timeout=request.settings.timeout_seconds,
            )
        except Exception as exc:
            raise AdapterError("openai_api_error", f"OpenAI request failed: {type(exc).__name__}") from exc
        latency_ms = (time.perf_counter() - started) * 1000.0

        response_id = getattr(response, "id", None)
        actual_model = getattr(response, "model", None)
        choices = getattr(response, "choices", None)
        usage = getattr(response, "usage", None)
        if not isinstance(response_id, str) or not response_id.strip():
            raise AdapterError("incomplete_response", "OpenAI response has no response ID")
        if not isinstance(actual_model, str) or not actual_model.strip():
            raise AdapterError("incomplete_response", "OpenAI response has no model identity")
        if not isinstance(choices, list) or len(choices) != 1:
            raise AdapterError("incomplete_response", "OpenAI response must contain exactly one choice")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        refusal = getattr(message, "refusal", None)
        if not isinstance(content, str):
            if isinstance(refusal, str) and refusal.strip():
                content = canonical_json({"provider_refusal": refusal})
            else:
                raise AdapterError("incomplete_response", "OpenAI response contains no text")
        input_tokens = getattr(usage, "prompt_tokens", None)
        output_tokens = getattr(usage, "completion_tokens", None)
        if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
            raise AdapterError("missing_usage", "OpenAI response has incomplete token usage")

        actual_identity = (
            self.identity
            if actual_model == self._model_snapshot
            else ProviderIdentity(provider="openai", model=actual_model, version="unverified")
        )
        estimated_cost = (
            input_tokens * OPENAI_INPUT_PRICE_PER_MILLION
            + output_tokens * OPENAI_OUTPUT_PRICE_PER_MILLION
        ) / 1_000_000
        return ProviderResponse(
            response_id=response_id,
            provider_identity=actual_identity,
            raw_text=content,
            usage=UsageRecord(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=estimated_cost,
            ),
            latency_ms=latency_ms,
        )
