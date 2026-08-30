"""Fixture-driven deterministic fake provider with no ambient I/O access."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.model_gateway.contracts import (
    AdapterInvocationError,
    ProviderBinding,
    ProviderCall,
    ProviderEnvelope,
    RawResponseArtifact,
    Sha256,
    UsageMetadata,
)

FakeStepKind = Literal[
    "valid_response",
    "abstention",
    "malformed_response",
    "empty_response",
    "timeout",
    "transient_error",
    "permanent_error",
    "response_mutation",
    "oversized_response",
    "cancelled",
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class FakeStep(_StrictFrozenModel):
    """One explicit fake-provider event, never an outcome-derived rule."""

    kind: FakeStepKind
    raw_content: bytes | None
    usage: UsageMetadata | None

    @model_validator(mode="after")
    def _shape_matches_kind(self) -> FakeStep:
        response_kinds = {
            "valid_response",
            "abstention",
            "malformed_response",
            "oversized_response",
            "response_mutation",
        }
        if self.kind in response_kinds and (self.raw_content is None or self.usage is None):
            raise ValueError("fake response steps require explicit raw content and usage")
        if self.kind == "empty_response" and (
            self.raw_content not in {None, b""} or self.usage is None
        ):
            raise ValueError("empty fake response requires explicit usage and no content")
        if self.kind not in response_kinds | {"empty_response"} and (
            self.raw_content is not None or self.usage is not None
        ):
            raise ValueError("fake error steps cannot carry response content or usage")
        return self


class FakeFixture(_StrictFrozenModel):
    """Ordered events bound only to one opaque immutable request hash."""

    request_identity_sha256: Sha256
    steps: tuple[FakeStep, ...]

    @model_validator(mode="after")
    def _has_steps(self) -> FakeFixture:
        if not self.steps:
            raise ValueError("fake fixture must contain at least one step")
        return self


class DeterministicFakeAdapter:
    """Pure offline adapter indexed by request hash and attempt ordinal."""

    def __init__(
        self,
        *,
        binding: ProviderBinding,
        fixtures: tuple[FakeFixture, ...],
    ) -> None:
        checked_binding = ProviderBinding.model_validate(binding.model_dump(mode="python"))
        checked_fixtures = tuple(
            FakeFixture.model_validate(fixture.model_dump(mode="python"))
            for fixture in fixtures
        )
        keys = tuple(fixture.request_identity_sha256 for fixture in checked_fixtures)
        if len(keys) != len(set(keys)):
            raise ValueError("fake fixture request hashes must be unique")
        self._binding = checked_binding
        self._fixtures = {fixture.request_identity_sha256: fixture for fixture in checked_fixtures}

    @property
    def binding(self) -> ProviderBinding:
        return self._binding

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        checked = ProviderCall.model_validate(call.model_dump(mode="python"))
        request_hash = checked.request_identity_sha256
        fixture = self._fixtures.get(request_hash)
        ordinal = checked.attempt_ordinal
        provider_attempt_ref = self._provider_attempt_ref(request_hash, ordinal)
        if fixture is None or ordinal > len(fixture.steps):
            raise AdapterInvocationError(
                code="permanent_provider_error",
                retryable=False,
                provider_attempt_ref=provider_attempt_ref,
            )
        step = fixture.steps[ordinal - 1]
        if step.kind == "timeout":
            raise AdapterInvocationError(
                code="provider_timeout",
                retryable=True,
                provider_attempt_ref=provider_attempt_ref,
            )
        if step.kind == "transient_error":
            raise AdapterInvocationError(
                code="transient_provider_error",
                retryable=True,
                provider_attempt_ref=provider_attempt_ref,
            )
        if step.kind == "permanent_error":
            raise AdapterInvocationError(
                code="permanent_provider_error",
                retryable=False,
                provider_attempt_ref=provider_attempt_ref,
            )
        if step.kind == "cancelled":
            raise AdapterInvocationError(
                code="provider_cancelled",
                retryable=False,
                provider_attempt_ref=provider_attempt_ref,
            )

        raw_content = b"" if step.kind == "empty_response" else step.raw_content
        if raw_content is None or step.usage is None:
            raise AssertionError("validated fake response step lost required content")
        binding = self.binding
        echoed_hash = request_hash
        if step.kind == "response_mutation":
            echoed_hash = "f" * 64 if request_hash != "f" * 64 else "e" * 64
        return ProviderEnvelope(
            request_identity_sha256=echoed_hash,
            binding=binding,
            provider_attempt_ref=provider_attempt_ref,
            response_mode="abstention" if step.kind == "abstention" else "structured",
            raw_response=RawResponseArtifact.from_bytes(raw_content),
            usage=step.usage,
        )

    def _provider_attempt_ref(self, request_hash: str, ordinal: int) -> str:
        digest = canonical_execution_sha256(
            {
                "adapter": "deterministic-fake/v1",
                "binding": self.binding.model_dump(mode="json"),
                "request_identity_sha256": request_hash,
                "attempt_ordinal": ordinal,
            }
        )
        return f"ev-{digest}"
