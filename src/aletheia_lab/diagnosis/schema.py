"""Strict contracts for the P1 matched diagnosis pilot.

The request deliberately contains only the diagnosis-safe evidence projection.
Internal case IDs, evidence-condition labels and evaluator expectations never
cross this boundary.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.evidence.schema import (
    DiagnosisEvidenceView,
    canonical_json,
    sha256_text,
)

REQUEST_SCHEMA_VERSION: Final[Literal["diagnosis-request/1"]] = "diagnosis-request/1"
OUTPUT_SCHEMA_VERSION: Final[Literal["diagnosis-output/1"]] = "diagnosis-output/1"
ATTEMPT_SCHEMA_VERSION: Final[Literal["diagnosis-attempt/1"]] = "diagnosis-attempt/1"
RUN_SCHEMA_VERSION: Final[Literal["diagnosis-run/1"]] = "diagnosis-run/1"
PILOT_SCHEMA_VERSION: Final[Literal["p1-matched-pilot/1"]] = "p1-matched-pilot/1"

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PilotVariant(StrEnum):
    """The minimal P1 matched comparison frozen for G6."""

    B1_PLAIN = "b1_plain"
    A3_EVIDENCE_CONTRACT = "a3_evidence_contract"


class ProviderIdentity(_StrictFrozenModel):
    provider: str
    model: str
    version: str

    @field_validator("provider", "model", "version")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("provider identity fields must be non-blank and trimmed")
        return value


class GenerationSettings(_StrictFrozenModel):
    temperature: float = Field(ge=0.0, le=2.0, allow_inf_nan=False)
    top_p: float = Field(gt=0.0, le=1.0, allow_inf_nan=False)
    max_output_tokens: int = Field(gt=0)
    seed: int | None = None
    timeout_seconds: float = Field(gt=0.0, allow_inf_nan=False)
    max_attempts: int = Field(ge=1, le=3)


def observable_facts_payload(view: DiagnosisEvidenceView) -> dict[str, object]:
    """Return only semantic values intentionally observable by every variant."""

    return {
        "items": [
            {
                "evidence_id": item.evidence_id,
                "kind": item.kind,
                "evidence_roles": list(item.evidence_roles),
                "title": item.title,
                "content": item.content,
            }
            for item in view.items
        ]
    }


def observable_facts_sha256(view: DiagnosisEvidenceView) -> str:
    return sha256_text(canonical_json(observable_facts_payload(view)))


def prompt_sha256_for(
    *,
    variant: PilotVariant,
    prompt_version: str,
    rendering_version: str,
    system_prompt: str,
    response_format: str,
) -> str:
    return sha256_text(
        canonical_json(
            {
                "variant": variant.value,
                "prompt_version": prompt_version,
                "rendering_version": rendering_version,
                "system_prompt": system_prompt,
                "response_format": response_format,
            }
        )
    )


class DiagnosisRequest(_StrictFrozenModel):
    """One provider-agnostic request with a separately hashable facts payload."""

    schema_version: Literal["diagnosis-request/1"]
    request_id: str = Field(pattern=r"^diagreq-[0-9a-f]{64}$")
    variant: PilotVariant
    provider_identity: ProviderIdentity
    settings: GenerationSettings
    prompt_version: str
    rendering_version: str
    system_prompt: str
    response_format: str
    prompt_sha256: str = Field(pattern=_SHA256_PATTERN)
    diagnosis_view: DiagnosisEvidenceView
    facts_sha256: str = Field(pattern=_SHA256_PATTERN)
    rendered_evidence: str
    rendered_evidence_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator(
        "prompt_version",
        "rendering_version",
        "system_prompt",
        "response_format",
        "rendered_evidence",
    )
    @classmethod
    def _prompt_fields_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt fields must not be blank")
        return value

    @model_validator(mode="after")
    def _hashes_match(self) -> Self:
        expected_facts = observable_facts_sha256(self.diagnosis_view)
        if self.facts_sha256 != expected_facts:
            raise ValueError("facts_sha256 does not match the diagnosis view")
        expected_prompt = prompt_sha256_for(
            variant=self.variant,
            prompt_version=self.prompt_version,
            rendering_version=self.rendering_version,
            system_prompt=self.system_prompt,
            response_format=self.response_format,
        )
        if self.prompt_sha256 != expected_prompt:
            raise ValueError("prompt_sha256 does not match the frozen prompt")
        if self.rendered_evidence_sha256 != sha256_text(self.rendered_evidence):
            raise ValueError("rendered_evidence_sha256 does not match the outbound evidence")
        payload = self.model_dump(mode="json", exclude={"request_id"})
        expected_request = f"diagreq-{sha256_text(canonical_json(payload))}"
        if self.request_id != expected_request:
            raise ValueError("request_id does not match the canonical request")
        return self

    @classmethod
    def build(
        cls,
        *,
        variant: PilotVariant,
        provider_identity: ProviderIdentity,
        settings: GenerationSettings,
        prompt_version: str,
        rendering_version: str,
        system_prompt: str,
        response_format: str,
        diagnosis_view: DiagnosisEvidenceView,
        rendered_evidence: str,
    ) -> DiagnosisRequest:
        prompt_sha = prompt_sha256_for(
            variant=variant,
            prompt_version=prompt_version,
            rendering_version=rendering_version,
            system_prompt=system_prompt,
            response_format=response_format,
        )
        base = {
            "schema_version": REQUEST_SCHEMA_VERSION,
            "variant": variant,
            "provider_identity": provider_identity,
            "settings": settings,
            "prompt_version": prompt_version,
            "rendering_version": rendering_version,
            "system_prompt": system_prompt,
            "response_format": response_format,
            "prompt_sha256": prompt_sha,
            "diagnosis_view": diagnosis_view,
            "facts_sha256": observable_facts_sha256(diagnosis_view),
            "rendered_evidence": rendered_evidence,
            "rendered_evidence_sha256": sha256_text(rendered_evidence),
        }
        serialized = {
            key: value.model_dump(mode="json") if isinstance(value, BaseModel) else value
            for key, value in base.items()
        }
        serialized["variant"] = variant.value
        request_id = f"diagreq-{sha256_text(canonical_json(serialized))}"
        return cls(
            schema_version=REQUEST_SCHEMA_VERSION,
            request_id=request_id,
            variant=variant,
            provider_identity=provider_identity,
            settings=settings,
            prompt_version=prompt_version,
            rendering_version=rendering_version,
            system_prompt=system_prompt,
            response_format=response_format,
            prompt_sha256=prompt_sha,
            diagnosis_view=diagnosis_view,
            facts_sha256=observable_facts_sha256(diagnosis_view),
            rendered_evidence=rendered_evidence,
            rendered_evidence_sha256=sha256_text(rendered_evidence),
        )

    def provider_messages(self) -> tuple[dict[str, str], dict[str, str]]:
        """Return the exact outbound messages; evidence remains untrusted data."""

        user_payload = (
            "UNTRUSTED_EVIDENCE\n"
            f"{self.rendered_evidence}\n"
            "END_UNTRUSTED_EVIDENCE\n\n"
            f"{self.response_format}"
        )
        return (
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_payload},
        )


ClaimStrength = Literal["observation", "comparison", "bounded_causal_hypothesis"]


class DiagnosisOutput(_StrictFrozenModel):
    """Parseable output contract shared by both matched variants."""

    schema_version: Literal["diagnosis-output/1"]
    root_cause_hypothesis: str
    claim_strength: ClaimStrength
    supporting_evidence_ids: tuple[str, ...] = ()
    counterevidence_ids: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    abstain: bool

    @field_validator("root_cause_hypothesis")
    @classmethod
    def _hypothesis_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("root_cause_hypothesis must not be blank")
        return value

    @field_validator("supporting_evidence_ids", "counterevidence_ids")
    @classmethod
    def _valid_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("evidence ID lists must not contain duplicates")
        for evidence_id in value:
            if not evidence_id or len(evidence_id) > 128:
                raise ValueError("invalid evidence ID")
        return value

    @field_validator("missing_evidence")
    @classmethod
    def _missing_not_blank(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("missing-evidence requests must not be blank")
        return value

    @model_validator(mode="after")
    def _internally_consistent(self) -> Self:
        if set(self.supporting_evidence_ids) & set(self.counterevidence_ids):
            raise ValueError("one evidence ID cannot be both supporting and counterevidence")
        if self.abstain and self.claim_strength == "bounded_causal_hypothesis":
            raise ValueError("an abstention cannot assert a causal hypothesis")
        return self


class UsageRecord(_StrictFrozenModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0.0, allow_inf_nan=False)


class ProviderResponse(_StrictFrozenModel):
    """Raw adapter response metadata returned before parsing."""

    response_id: str
    provider_identity: ProviderIdentity
    raw_text: str
    usage: UsageRecord
    latency_ms: float = Field(ge=0.0, allow_inf_nan=False)

    @field_validator("response_id")
    @classmethod
    def _response_id_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("response_id must not be blank")
        return value


AttemptStatus = Literal["success", "adapter_error", "identity_mismatch", "parse_failure"]


class AttemptRecord(_StrictFrozenModel):
    schema_version: Literal["diagnosis-attempt/1"]
    attempt_index: int = Field(ge=1, le=3)
    status: AttemptStatus
    response_id: str | None = None
    provider_identity: ProviderIdentity | None = None
    raw_relative_path: str | None = None
    raw_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    parsed_relative_path: str | None = None
    parsed_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    usage: UsageRecord | None = None
    latency_ms: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    error_type: str | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def _status_contract(self) -> Self:
        has_raw = self.raw_relative_path is not None and self.raw_sha256 is not None
        has_parsed = self.parsed_relative_path is not None and self.parsed_sha256 is not None
        if (self.raw_relative_path is None) != (self.raw_sha256 is None):
            raise ValueError("raw path and hash must be present together")
        if (self.parsed_relative_path is None) != (self.parsed_sha256 is None):
            raise ValueError("parsed path and hash must be present together")
        if self.status == "adapter_error" and (
            has_raw
            or has_parsed
            or self.response_id is not None
            or self.provider_identity is not None
            or self.usage is not None
            or self.latency_ms is not None
        ):
            raise ValueError("adapter errors cannot claim provider-response metadata")
        if self.status != "adapter_error" and not has_raw:
            raise ValueError("provider responses must preserve a raw artifact")
        if self.status != "adapter_error" and (
            self.response_id is None
            or self.provider_identity is None
            or self.usage is None
            or self.latency_ms is None
        ):
            raise ValueError("provider responses require complete immutable metadata")
        if self.status == "success" and not has_parsed:
            raise ValueError("successful attempts require a parsed artifact")
        if self.status != "success" and has_parsed:
            raise ValueError("failed attempts cannot claim a parsed artifact")
        if self.status == "success" and (self.error_type or self.error_message):
            raise ValueError("successful attempts cannot contain an error")
        if self.status != "success" and not (self.error_type and self.error_message):
            raise ValueError("failed attempts require typed error details")
        if self.error_type is not None and not self.error_type.strip():
            raise ValueError("error_type must not be blank")
        if self.error_message is not None and not self.error_message.strip():
            raise ValueError("error_message must not be blank")
        return self


class DiagnosisRunRecord(_StrictFrozenModel):
    schema_version: Literal["diagnosis-run/1"]
    request: DiagnosisRequest
    attempts: tuple[AttemptRecord, ...]
    final_status: Literal["success", "unresolved"]

    @model_validator(mode="after")
    def _run_contract(self) -> Self:
        if not self.attempts:
            raise ValueError("a run must contain at least one attempt")
        expected = tuple(range(1, len(self.attempts) + 1))
        if tuple(attempt.attempt_index for attempt in self.attempts) != expected:
            raise ValueError("attempt indexes must be contiguous and one-based")
        if len(self.attempts) > self.request.settings.max_attempts:
            raise ValueError("attempt count exceeds the frozen retry budget")
        successes = [attempt for attempt in self.attempts if attempt.status == "success"]
        if self.final_status == "success":
            if len(successes) != 1 or self.attempts[-1].status != "success":
                raise ValueError("a successful run must end in exactly one success")
        elif successes:
            raise ValueError("an unresolved run cannot contain a successful attempt")
        return self


class PilotRunEntry(_StrictFrozenModel):
    request_id: str = Field(pattern=r"^diagreq-[0-9a-f]{64}$")
    diagnosis_context_id: str = Field(pattern=r"^p1-context-[0-9a-f]{64}$")
    variant: PilotVariant
    final_status: Literal["success", "unresolved"]
    relative_path: str
    file_sha256: str = Field(pattern=_SHA256_PATTERN)


class PilotManifest(_StrictFrozenModel):
    schema_version: Literal["p1-matched-pilot/1"]
    source_evidence_store_sha256: str = Field(pattern=_SHA256_PATTERN)
    provider_identity: ProviderIdentity
    settings: GenerationSettings
    context_count: int = Field(gt=0)
    variant_count: int = Field(ge=2)
    run_count: int = Field(gt=0)
    success_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)
    entries: tuple[PilotRunEntry, ...]

    @model_validator(mode="after")
    def _manifest_contract(self) -> Self:
        if self.run_count != len(self.entries):
            raise ValueError("run_count does not match entries")
        if self.success_count + self.unresolved_count != self.run_count:
            raise ValueError("pilot outcome counts do not match run_count")
        if self.run_count != self.context_count * self.variant_count:
            raise ValueError("pilot matrix is incomplete")
        contexts = {entry.diagnosis_context_id for entry in self.entries}
        variants = {entry.variant for entry in self.entries}
        if len(contexts) != self.context_count or len(variants) != self.variant_count:
            raise ValueError("pilot matrix dimensions do not match entries")
        pairs = {(entry.diagnosis_context_id, entry.variant) for entry in self.entries}
        if len(pairs) != self.run_count:
            raise ValueError("pilot matrix contains duplicate context/variant pairs")
        return self


def parse_diagnosis_output(raw_text: str, visible_evidence_ids: set[str]) -> DiagnosisOutput:
    """Parse one exact JSON object and reject citations outside the visible view."""

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"response contains duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        _ = json.loads(raw_text, object_pairs_hook=reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(f"response is not valid JSON: {exc.msg}") from exc
    # Validate from the original JSON so strict tuple fields accept JSON arrays
    # while still rejecting Python-side coercions.
    output = DiagnosisOutput.model_validate_json(raw_text)
    cited = set(output.supporting_evidence_ids) | set(output.counterevidence_ids)
    unknown = cited - visible_evidence_ids
    if unknown:
        raise ValueError(f"response cites non-visible evidence IDs: {sorted(unknown)}")
    return output
