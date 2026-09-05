"""Provider-neutral contracts for bounded, offline evaluation execution."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Annotated, Final, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.context.evaluation_context import EvaluationContextPayload
from aletheia_lab.evaluation.claim_corpus_contracts import ClaimType
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ModelVisibleEvidenceContext,
    ModelVisibleEvidenceItem,
)
from aletheia_lab.evaluation.execution_contracts import (
    ATTEMPT_IDENTITY_SCHEMA_VERSION,
    AttemptIdentity,
    EvaluationManifestReference,
    ModelPolicyReference,
    TechnicalIssue,
    canonical_execution_json,
    canonical_execution_sha256,
)
from aletheia_lab.project.identity import SHA256_PATTERN, content_sha256, normalize_text

RUNTIME_POLICY_SCHEMA_VERSION: Final[Literal["model-gateway-runtime-policy/v1"]] = (
    "model-gateway-runtime-policy/v1"
)
GATEWAY_REQUEST_SCHEMA_VERSION: Final[Literal["model-gateway-request/v1"]] = (
    "model-gateway-request/v1"
)
RAW_RESPONSE_SCHEMA_VERSION: Final[Literal["model-gateway-raw-response/v1"]] = (
    "model-gateway-raw-response/v1"
)
PARSED_RESPONSE_SCHEMA_VERSION: Final[Literal["model-gateway-parsed-response/v1"]] = (
    "model-gateway-parsed-response/v1"
)
GATEWAY_RESULT_SCHEMA_VERSION: Final[Literal["model-gateway-result/v1"]] = (
    "model-gateway-result/v1"
)

_OPAQUE_REFERENCE_PATTERN: Final[str] = r"^ev-[0-9a-f]{64}$"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
OpaqueReference = Annotated[str, Field(pattern=_OPAQUE_REFERENCE_PATTERN)]
ResponseMode = Literal["structured", "abstention"]
AttemptOutcome = Literal[
    "response",
    "transient_error",
    "permanent_error",
    "timeout",
    "cancelled",
    "identity_mismatch",
    "oversized_response",
    "parse_failure",
]
TerminalStatus = Literal[
    "parsed",
    "provider_failed",
    "timed_out",
    "retry_exhausted",
    "cancelled",
    "identity_rejected",
    "oversized_response",
    "parse_failed",
]
ProviderErrorCode = Literal[
    "transient_provider_error",
    "permanent_provider_error",
    "provider_timeout",
    "provider_cancelled",
]


class GatewayContractError(ValueError):
    """Raised when a gateway contract is ambiguous, forged, or inconsistent."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class RuntimePolicyReference(_StrictFrozenModel):
    """Authorized technical bounds with no provider or scientific defaults."""

    schema_version: Literal["model-gateway-runtime-policy/v1"] = (
        RUNTIME_POLICY_SCHEMA_VERSION
    )
    reference_id: OpaqueReference
    policy_sha256: Sha256
    manifest_reference_id: OpaqueReference
    manifest_content_sha256: Sha256
    model_policy_reference_id: OpaqueReference
    resource_policy_ref: OpaqueReference
    retry_policy_ref: OpaqueReference
    timeout_ns: int = Field(gt=0)
    max_attempts: int = Field(gt=0)
    max_response_bytes: int = Field(gt=0)
    authorization_ref: OpaqueReference
    provenance_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "manifest_reference_id": self.manifest_reference_id,
            "manifest_content_sha256": self.manifest_content_sha256,
            "model_policy_reference_id": self.model_policy_reference_id,
            "resource_policy_ref": self.resource_policy_ref,
            "retry_policy_ref": self.retry_policy_ref,
            "timeout_ns": self.timeout_ns,
            "max_attempts": self.max_attempts,
            "max_response_bytes": self.max_response_bytes,
            "authorization_ref": self.authorization_ref,
            "provenance_sha256": self.provenance_sha256,
        }

    @model_validator(mode="after")
    def _identity_matches(self) -> Self:
        expected = canonical_execution_sha256(self.identity_payload())
        if self.policy_sha256 != expected or self.reference_id != f"ev-{expected}":
            raise ValueError("runtime policy identity does not match canonical fields")
        return self

    @classmethod
    def build(
        cls,
        *,
        manifest: EvaluationManifestReference,
        model_policy: ModelPolicyReference,
        retry_policy_ref: str,
        timeout_ns: int,
        max_attempts: int,
        max_response_bytes: int,
        provenance_sha256: str,
    ) -> Self:
        checked_manifest = EvaluationManifestReference.model_validate(
            manifest.model_dump(mode="python")
        )
        checked_model_policy = ModelPolicyReference.model_validate(
            model_policy.model_dump(mode="python")
        )
        if (
            checked_model_policy.manifest_reference_id != checked_manifest.reference_id
            or checked_model_policy.manifest_content_sha256
            != checked_manifest.manifest_content_sha256
            or checked_model_policy.authorization_ref != checked_manifest.authorization_ref
        ):
            raise GatewayContractError("runtime policy references do not share one authorization")
        payload = {
            "schema_version": RUNTIME_POLICY_SCHEMA_VERSION,
            "manifest_reference_id": checked_manifest.reference_id,
            "manifest_content_sha256": checked_manifest.manifest_content_sha256,
            "model_policy_reference_id": checked_model_policy.reference_id,
            "resource_policy_ref": checked_model_policy.resource_policy_ref,
            "retry_policy_ref": retry_policy_ref,
            "timeout_ns": timeout_ns,
            "max_attempts": max_attempts,
            "max_response_bytes": max_response_bytes,
            "authorization_ref": checked_manifest.authorization_ref,
            "provenance_sha256": provenance_sha256,
        }
        policy_sha256 = canonical_execution_sha256(payload)
        return cls(
            reference_id=f"ev-{policy_sha256}",
            policy_sha256=policy_sha256,
            manifest_reference_id=checked_manifest.reference_id,
            manifest_content_sha256=checked_manifest.manifest_content_sha256,
            model_policy_reference_id=checked_model_policy.reference_id,
            resource_policy_ref=checked_model_policy.resource_policy_ref,
            retry_policy_ref=retry_policy_ref,
            timeout_ns=timeout_ns,
            max_attempts=max_attempts,
            max_response_bytes=max_response_bytes,
            authorization_ref=checked_manifest.authorization_ref,
            provenance_sha256=provenance_sha256,
        )


class ProviderBinding(_StrictFrozenModel):
    """Exact provider/model/version references selected by the authorized manifest."""

    provider_ref: OpaqueReference
    model_ref: OpaqueReference
    model_version_ref: OpaqueReference

    @classmethod
    def from_model_policy(cls, policy: ModelPolicyReference) -> Self:
        checked = ModelPolicyReference.model_validate(policy.model_dump(mode="python"))
        return cls(
            provider_ref=checked.provider_ref,
            model_ref=checked.model_ref,
            model_version_ref=checked.model_version_ref,
        )


class UsageMetadata(_StrictFrozenModel):
    """Nullable provider usage; unknown values remain unknown rather than zero."""

    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cost_amount: Decimal | None
    cost_currency_ref: OpaqueReference | None

    @model_validator(mode="after")
    def _known_values_are_non_negative_and_consistent(self) -> Self:
        values = (self.input_tokens, self.output_tokens, self.total_tokens)
        if any(value is not None and value < 0 for value in values):
            raise ValueError("known token counts must be non-negative")
        if (
            self.input_tokens is not None
            and self.output_tokens is not None
            and self.total_tokens is not None
            and self.total_tokens != self.input_tokens + self.output_tokens
        ):
            raise ValueError("known total token count must equal input plus output")
        if self.cost_amount is not None and (
            not self.cost_amount.is_finite() or self.cost_amount < 0
        ):
            raise ValueError("known cost must be finite and non-negative")
        if (self.cost_amount is None) != (self.cost_currency_ref is None):
            raise ValueError("cost amount and currency reference must be known together")
        return self


class ClaimRelationProviderContext(_StrictFrozenModel):
    """Exact blind relation payload allowed to cross the provider boundary."""

    schema_version: Literal["claim-relation-provider-context/v1"] = (
        "claim-relation-provider-context/v1"
    )
    claim_text: str = Field(min_length=1, max_length=2048)
    claim_type: ClaimType
    visible_evidence: tuple[ModelVisibleEvidenceItem, ...] = Field(
        min_length=1, max_length=32
    )
    context_sha256: Sha256

    @field_validator("claim_text")
    @classmethod
    def _claim_text_is_bounded(cls, value: str) -> str:
        return normalize_text(value, label="relation context claim", max_length=2048)

    def model_payload(self) -> dict[str, object]:
        return {
            "claim_text": self.claim_text,
            "claim_type": self.claim_type,
            "visible_evidence": tuple(
                item.model_dump(mode="json") for item in self.visible_evidence
            ),
        }

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        evidence_ids = tuple(item.evidence_id for item in self.visible_evidence)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("relation context evidence identities must be unique")
        if self.context_sha256 != canonical_execution_sha256(self.model_payload()):
            raise ValueError("relation context identity does not match provider payload")
        return self

    @classmethod
    def from_provider_payload(
        cls, payload: dict[str, object]
    ) -> ClaimRelationProviderContext:
        fields = dict(payload)
        if set(fields) != {"claim_text", "claim_type", "visible_evidence"}:
            raise ValueError("relation provider payload contains unauthorized fields")
        return cls.model_validate_json(
            json.dumps(
                {
                    "schema_version": "claim-relation-provider-context/v1",
                    **fields,
                    "context_sha256": canonical_execution_sha256(fields),
                },
                ensure_ascii=False,
            )
        )


class GatewayRequest(_StrictFrozenModel):
    """One immutable outbound request prepared from execution and context contracts."""

    schema_version: Literal["model-gateway-request/v1"] = GATEWAY_REQUEST_SCHEMA_VERSION
    initial_attempt: AttemptIdentity
    context: (
        EvaluationContextPayload
        | ModelVisibleEvidenceContext
        | ClaimRelationProviderContext
    )
    prompt_text: str
    response_schema_json: str
    runtime_policy: RuntimePolicyReference

    @model_validator(mode="after")
    def _all_bound_content_matches(self) -> Self:
        attempt = self.initial_attempt
        if attempt.attempt_ordinal != 1:
            raise ValueError("gateway request initial attempt ordinal must be one")
        if self.context.context_sha256 != attempt.context_sha256:
            raise ValueError("gateway context does not match immutable attempt identity")
        if isinstance(self.context, EvaluationContextPayload) and (
            self.context.case_reference_id != attempt.case.reference_id
            or self.context.project_id != attempt.case.project_id
            or self.context.snapshot_id != attempt.case.snapshot_id
            or self.context.evidence_bundle_id != attempt.case.evidence_bundle_id
            or self.context.visibility_projection_sha256
            != attempt.case.visibility_projection_sha256
        ):
            raise ValueError("gateway context does not match immutable attempt identity")
        if isinstance(self.context, ModelVisibleEvidenceContext) and (
            self.context.context_sha256 != attempt.case.evidence_content_sha256
            or self.context.context_sha256
            != attempt.case.visibility_projection_sha256
        ):
            raise ValueError("visible evidence differs from the bound case projection")
        if isinstance(self.context, ClaimRelationProviderContext) and (
            self.context.context_sha256 != attempt.case.evidence_content_sha256
            or self.context.context_sha256
            != attempt.case.visibility_projection_sha256
        ):
            raise ValueError("relation payload differs from the bound case projection")
        try:
            prompt_sha256 = content_sha256(self.prompt_text.encode("utf-8", errors="strict"))
            schema_sha256 = content_sha256(
                self.response_schema_json.encode("utf-8", errors="strict")
            )
        except UnicodeEncodeError as exc:
            raise ValueError("gateway text must contain valid Unicode scalar values") from exc
        if prompt_sha256 != attempt.prompt_sha256:
            raise ValueError("gateway prompt does not match immutable attempt identity")
        if (
            schema_sha256 != attempt.response_schema_sha256
            or schema_sha256 != attempt.model_policy.response_schema_sha256
        ):
            raise ValueError("gateway response schema does not match model policy")
        policy = self.runtime_policy
        if (
            policy.manifest_reference_id != attempt.manifest.reference_id
            or policy.manifest_content_sha256 != attempt.manifest.manifest_content_sha256
            or policy.model_policy_reference_id != attempt.model_policy.reference_id
            or policy.resource_policy_ref != attempt.model_policy.resource_policy_ref
            or policy.authorization_ref != attempt.manifest.authorization_ref
        ):
            raise ValueError("gateway runtime policy does not match immutable request identity")
        return self


class ProviderCall(_StrictFrozenModel):
    """Exact adapter input; only attempt ordinal may differ across retries."""

    request_identity_sha256: Sha256
    attempt_id: OpaqueReference
    attempt_identity_sha256: Sha256
    attempt_ordinal: int = Field(gt=0)
    context_sha256: Sha256
    prompt_sha256: Sha256
    response_schema_sha256: Sha256
    context_json: str
    prompt_text: str
    response_schema_json: str
    runtime_policy: RuntimePolicyReference

    @model_validator(mode="after")
    def _content_matches_attempt(self) -> Self:
        context: (
            EvaluationContextPayload
            | ModelVisibleEvidenceContext
            | ClaimRelationProviderContext
        )
        try:
            wrapper = json.loads(self.context_json)
            if not isinstance(wrapper, dict):
                raise ValueError("provider context wrapper must be an object")
            raw_payload = wrapper.get("payload")
            if not isinstance(raw_payload, dict):
                raise ValueError("provider context payload must be an object")
            payload_json = json.dumps(raw_payload, ensure_ascii=False)
            if raw_payload.get("schema_version") == "evaluation-context/v1":
                context = EvaluationContextPayload.model_validate_json(payload_json)
            elif raw_payload.get("schema_version") == "claim-visible-evidence-context/v1":
                context = ModelVisibleEvidenceContext.model_validate_json(payload_json)
            elif set(raw_payload) == {"claim_text", "claim_type", "visible_evidence"}:
                context = ClaimRelationProviderContext.from_provider_payload(raw_payload)
            else:
                raise ValueError("provider context schema is not authorized")
        except (TypeError, ValueError) as exc:
            raise ValueError("provider call contains an invalid context") from exc
        canonical_payload = (
            context.model_payload()
            if isinstance(context, ClaimRelationProviderContext)
            else context.model_dump(mode="json")
        )
        if self.context_json != canonical_execution_json(canonical_payload):
            raise ValueError("provider call context is not canonical")
        expected_attempt_sha256 = canonical_execution_sha256(
            {
                "schema_version": ATTEMPT_IDENTITY_SCHEMA_VERSION,
                "request_identity_sha256": self.request_identity_sha256,
                "attempt_ordinal": self.attempt_ordinal,
            }
        )
        if (
            self.attempt_identity_sha256 != expected_attempt_sha256
            or self.attempt_id != f"ev-{expected_attempt_sha256}"
            or context.context_sha256 != self.context_sha256
            or content_sha256(self.prompt_text.encode("utf-8", errors="strict"))
            != self.prompt_sha256
            or content_sha256(self.response_schema_json.encode("utf-8", errors="strict"))
            != self.response_schema_sha256
        ):
            raise ValueError("provider call content differs from immutable attempt identity")
        return self


class RawResponseArtifact(_StrictFrozenModel):
    """Exact provider bytes retained separately from any parsed representation."""

    schema_version: Literal["model-gateway-raw-response/v1"] = RAW_RESPONSE_SCHEMA_VERSION
    artifact_ref: OpaqueReference
    content_sha256: Sha256
    byte_count: int = Field(ge=0)
    media_type: Literal["application/json"]
    content: bytes

    @model_validator(mode="after")
    def _content_matches_reference(self) -> Self:
        expected = content_sha256(self.content)
        if (
            self.content_sha256 != expected
            or self.artifact_ref != f"ev-{expected}"
            or self.byte_count != len(self.content)
        ):
            raise ValueError("raw response artifact does not match exact bytes")
        return self

    @classmethod
    def from_bytes(cls, content: bytes) -> Self:
        digest = content_sha256(content)
        return cls(
            artifact_ref=f"ev-{digest}",
            content_sha256=digest,
            byte_count=len(content),
            media_type="application/json",
            content=content,
        )


class ParsedResponseArtifact(_StrictFrozenModel):
    """Canonical parsed JSON object, never a substitute for the raw response."""

    schema_version: Literal["model-gateway-parsed-response/v1"] = (
        PARSED_RESPONSE_SCHEMA_VERSION
    )
    artifact_ref: OpaqueReference
    content_sha256: Sha256
    payload: dict[str, object]

    @model_validator(mode="after")
    def _content_matches_reference(self) -> Self:
        expected = canonical_execution_sha256(self.payload)
        if self.content_sha256 != expected or self.artifact_ref != f"ev-{expected}":
            raise ValueError("parsed response artifact does not match canonical payload")
        return self

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> Self:
        digest = canonical_execution_sha256(payload)
        return cls(artifact_ref=f"ev-{digest}", content_sha256=digest, payload=payload)


class ProviderEnvelope(_StrictFrozenModel):
    """One adapter response with binding and provider-attempt metadata echoed."""

    request_identity_sha256: Sha256
    binding: ProviderBinding
    provider_attempt_ref: OpaqueReference
    response_mode: ResponseMode
    raw_response: RawResponseArtifact
    usage: UsageMetadata


class AttemptTiming(_StrictFrozenModel):
    """Monotonic timing supplied by the injected clock."""

    started_ns: int = Field(ge=0)
    ended_ns: int = Field(ge=0)
    latency_ns: int = Field(ge=0)

    @model_validator(mode="after")
    def _latency_reconciles(self) -> Self:
        if self.ended_ns < self.started_ns or self.latency_ns != self.ended_ns - self.started_ns:
            raise ValueError("attempt timing is not monotonic")
        return self


class AttemptRecord(_StrictFrozenModel):
    """Public-safe technical record for one provider invocation or cancellation."""

    attempt: AttemptIdentity
    outcome: AttemptOutcome
    timing: AttemptTiming
    provider_attempt_ref: OpaqueReference | None
    response_mode: ResponseMode | None
    usage: UsageMetadata | None
    issue: TechnicalIssue | None

    @model_validator(mode="after")
    def _outcome_shape_is_consistent(self) -> Self:
        response_metadata = self.response_mode is not None and self.usage is not None
        if self.outcome == "response":
            if self.provider_attempt_ref is None or not response_metadata or self.issue is not None:
                raise ValueError("response attempt requires provider metadata without an issue")
        elif self.outcome in {"parse_failure", "oversized_response"}:
            if self.provider_attempt_ref is None or not response_metadata or self.issue is None:
                raise ValueError("rejected response attempt must retain metadata and an issue")
        elif (
            self.response_mode is not None
            or self.usage is not None
            or self.issue is None
        ):
            raise ValueError("non-response attempt cannot contain response metadata")
        return self


class GatewayExecutionResult(_StrictFrozenModel):
    """Terminal technical result without scientific scoring or interpretation."""

    schema_version: Literal["model-gateway-result/v1"] = GATEWAY_RESULT_SCHEMA_VERSION
    request_identity_sha256: Sha256
    status: TerminalStatus
    attempts: tuple[AttemptRecord, ...]
    raw_response: RawResponseArtifact | None
    parsed_response: ParsedResponseArtifact | None
    issue: TechnicalIssue | None

    @model_validator(mode="after")
    def _terminal_shape_is_consistent(self) -> Self:
        if not self.attempts:
            raise ValueError("gateway result must retain at least one attempt record")
        for ordinal, record in enumerate(self.attempts, start=1):
            if (
                record.attempt.attempt_ordinal != ordinal
                or record.attempt.request_identity_sha256 != self.request_identity_sha256
            ):
                raise ValueError("gateway attempt sequence does not preserve request identity")
        expected_outcomes: dict[TerminalStatus, tuple[AttemptOutcome, ...]] = {
            "parsed": ("response",),
            "provider_failed": ("permanent_error",),
            "timed_out": ("timeout",),
            "retry_exhausted": ("transient_error", "timeout"),
            "cancelled": ("cancelled",),
            "identity_rejected": ("identity_mismatch",),
            "oversized_response": ("oversized_response",),
            "parse_failed": ("parse_failure",),
        }
        if self.attempts[-1].outcome not in expected_outcomes[self.status]:
            raise ValueError("gateway terminal status does not match final attempt outcome")
        if any(
            record.outcome not in {"transient_error", "timeout"}
            for record in self.attempts[:-1]
        ):
            raise ValueError("only retryable failures may precede the terminal attempt")
        if self.issue != self.attempts[-1].issue:
            raise ValueError("gateway terminal issue must match the final attempt issue")
        if self.status == "parsed":
            if self.raw_response is None or self.parsed_response is None or self.issue is not None:
                raise ValueError("parsed result requires separate raw and parsed artifacts")
        elif self.status == "parse_failed":
            if self.raw_response is None or self.parsed_response is not None or self.issue is None:
                raise ValueError("parse failure must retain raw response and structured issue")
        elif self.status == "oversized_response":
            if self.raw_response is None or self.parsed_response is not None or self.issue is None:
                raise ValueError("oversized result must retain raw response and structured issue")
        elif self.parsed_response is not None or self.issue is None:
            raise ValueError("non-parsed result cannot contain parsed content")
        elif self.raw_response is not None:
            raise ValueError("provider failures cannot publish raw response content")
        return self


class ProviderAdapter(Protocol):
    """Stable provider-neutral adapter interface."""

    @property
    def binding(self) -> ProviderBinding: ...

    def invoke(self, call: ProviderCall) -> ProviderEnvelope: ...


class Clock(Protocol):
    """Injectable monotonic clock."""

    def now_ns(self) -> int: ...


class CancellationProbe(Protocol):
    """Injectable cancellation boundary with no ambient process state."""

    def is_cancelled(self) -> bool: ...


class AdapterInvocationError(RuntimeError):
    """Typed provider failure; arbitrary provider messages are never persisted."""

    def __init__(
        self,
        *,
        code: ProviderErrorCode,
        retryable: bool,
        provider_attempt_ref: str,
    ) -> None:
        expected_retryable = code in {
            "transient_provider_error",
            "provider_timeout",
        }
        if retryable != expected_retryable:
            raise ValueError("provider error retryability contradicts its taxonomy")
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.provider_attempt_ref = provider_attempt_ref
