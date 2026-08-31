"""Pinned OpenAI Chat Completions adapter for the provider-neutral gateway.

The adapter owns only transport translation.  Request identity, retry count,
deadline supervision, parsing and terminal publication remain gateway concerns.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
from typing import Final, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from aletheia_lab.evaluation.execution_contracts import (
    ModelPolicyReference,
    canonical_execution_sha256,
)
from aletheia_lab.evaluation.variant_fairness import DiagnosisModelPolicy
from aletheia_lab.model_gateway.contracts import (
    AdapterInvocationError,
    ProviderBinding,
    ProviderCall,
    ProviderEnvelope,
    ProviderErrorCode,
    RawResponseArtifact,
    UsageMetadata,
)
from aletheia_lab.model_gateway.runtime import validate_response_schema

OPENAI_GATEWAY_POLICY_SCHEMA_VERSION: Final = "openai-gateway-policy/v1"
OPENAI_API_BASE_URL: Final = "https://api.openai.com/v1"
OPENAI_GATEWAY_ADAPTER_VERSION: Final = "openai-chat-completions-gateway/1.0.0"
OPENAI_SDK_VERSION: Final = "2.46.0"
OPENAI_MODEL_FAMILY: Final = "gpt-4.1"
OPENAI_SEED: Final = 17
OPENAI_MAX_OUTPUT_TOKENS: Final = 600
OPENAI_TIMEOUT_SECONDS: Final = 60.0
OPENAI_PROVIDER_ATTEMPT_CEILING: Final = 2
_SCHEMA_NAME: Final = "aletheia_diagnosis_output"


class OpenAIGatewayConfigurationError(ValueError):
    """Public-safe failure raised before any provider attempt is authorized."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class OpenAIGatewayPolicy(_StrictFrozenModel):
    """Exact provider settings copied from the outcome-blind fairness freeze."""

    schema_version: Literal["openai-gateway-policy/v1"] = (
        OPENAI_GATEWAY_POLICY_SCHEMA_VERSION
    )
    provider: Literal["openai"]
    api: Literal["chat_completions"] = "chat_completions"
    model: Literal["gpt-4.1"]
    model_version: Literal["gpt-4.1-2025-04-14"]
    sdk_version: Literal["2.46.0"] = "2.46.0"
    temperature: float
    top_p: float
    seed: Literal[17]
    max_output_tokens: Literal[600]
    timeout_seconds: float
    provider_attempt_ceiling: Literal[2]
    hidden_sdk_retries: Literal[0] = 0
    store_provider_response: Literal[False] = False
    silent_provider_or_model_switch: Literal[False]

    @model_validator(mode="after")
    def _matches_frozen_policy(self) -> OpenAIGatewayPolicy:
        if type(self.temperature) is not float or self.temperature != 0.0:
            raise ValueError("temperature differs from the frozen gateway policy")
        if type(self.top_p) is not float or self.top_p != 1.0:
            raise ValueError("top_p differs from the frozen gateway policy")
        if type(self.timeout_seconds) is not float or self.timeout_seconds != 60.0:
            raise ValueError("timeout differs from the frozen gateway policy")
        return self

    @classmethod
    def from_fairness_policy(cls, policy: DiagnosisModelPolicy) -> OpenAIGatewayPolicy:
        """Copy one validated fairness policy into the transport-specific lock."""

        checked = DiagnosisModelPolicy.model_validate(policy.model_dump(mode="python"))
        try:
            return cls.model_validate(
                {
                    "provider": checked.provider,
                    "model": checked.model,
                    "model_version": checked.model_version,
                    "temperature": checked.temperature,
                    "top_p": checked.top_p,
                    "seed": checked.seed,
                    "max_output_tokens": checked.max_output_tokens,
                    "timeout_seconds": checked.timeout_seconds,
                    "provider_attempt_ceiling": checked.provider_attempt_ceiling,
                    "silent_provider_or_model_switch": (
                        checked.silent_provider_or_model_switch
                    ),
                }
            )
        except ValidationError as exc:
            raise OpenAIGatewayConfigurationError(
                "fairness model policy is incompatible with the production adapter"
            ) from exc

    def canonical_sha256(self) -> str:
        """Hash the complete adapter-specific transport policy."""

        return canonical_execution_sha256(self.model_dump(mode="json"))

    def model_policy_sha256(self) -> str:
        """Hash exactly the research-owned model policy represented by this lock."""

        return canonical_execution_sha256(
            {
                "provider": self.provider,
                "model": self.model,
                "model_version": self.model_version,
                "temperature": self.temperature,
                "top_p": self.top_p,
                "seed": self.seed,
                "max_output_tokens": self.max_output_tokens,
                "timeout_seconds": self.timeout_seconds,
                "provider_attempt_ceiling": self.provider_attempt_ceiling,
                "silent_provider_or_model_switch": (
                    self.silent_provider_or_model_switch
                ),
            }
        )


class _OpenAICompletions(Protocol):
    def create(self, **kwargs: object) -> object: ...


class _OpenAIChat(Protocol):
    completions: _OpenAICompletions


class OpenAIGatewayClient(Protocol):
    chat: _OpenAIChat


class OpenAIChatCompletionsGatewayAdapter:
    """Production adapter with no fallback, hidden retry or ambient endpoint."""

    def __init__(
        self,
        *,
        client: OpenAIGatewayClient,
        model_policy: ModelPolicyReference,
        policy: OpenAIGatewayPolicy,
    ) -> None:
        checked_model_policy, checked_policy = _validated_configuration(
            model_policy=model_policy,
            policy=policy,
        )
        self._client = client
        self._policy = checked_policy
        self._binding = ProviderBinding.from_model_policy(checked_model_policy)

    @classmethod
    def from_environment(
        cls,
        *,
        model_policy: ModelPolicyReference,
        policy: OpenAIGatewayPolicy,
    ) -> OpenAIChatCompletionsGatewayAdapter:
        """Create a pinned SDK client without performing a network request."""

        checked_model_policy, checked_policy = _validated_configuration(
            model_policy=model_policy,
            policy=policy,
        )
        try:
            installed_version = importlib.metadata.version("openai")
        except importlib.metadata.PackageNotFoundError as exc:
            raise OpenAIGatewayConfigurationError(
                "the frozen OpenAI SDK is not installed"
            ) from exc
        if installed_version != OPENAI_SDK_VERSION:
            raise OpenAIGatewayConfigurationError(
                "the installed OpenAI SDK differs from the frozen version"
            )
        if any(
            os.environ.get(variable)
            for variable in ("OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID")
        ):
            raise OpenAIGatewayConfigurationError(
                "ambient OpenAI endpoint, organization and project overrides must be unset"
            )
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key or api_key != api_key.strip() or any(
            character in api_key for character in ("\r", "\n", "\x00")
        ):
            raise OpenAIGatewayConfigurationError(
                "OPENAI_API_KEY is unavailable or malformed"
            )
        try:
            module = importlib.import_module("openai")
        except Exception as exc:
            raise OpenAIGatewayConfigurationError(
                "the frozen OpenAI SDK could not be imported"
            ) from exc
        factory = getattr(module, "OpenAI", None)
        if factory is None or not callable(factory):
            raise OpenAIGatewayConfigurationError(
                "the frozen OpenAI SDK exposes no client factory"
            )
        try:
            client = factory(
                api_key=api_key,
                base_url=OPENAI_API_BASE_URL,
                max_retries=0,
            )
        except Exception as exc:
            raise OpenAIGatewayConfigurationError(
                "the frozen OpenAI client could not be constructed"
            ) from exc
        return cls(
            client=cast(OpenAIGatewayClient, client),
            model_policy=checked_model_policy,
            policy=checked_policy,
        )

    @property
    def binding(self) -> ProviderBinding:
        return self._binding

    @property
    def policy(self) -> OpenAIGatewayPolicy:
        return self._policy

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        """Translate one immutable provider call and return exact model text."""

        checked = ProviderCall.model_validate(call.model_dump(mode="python"))
        self._verify_runtime_policy(checked)
        try:
            response_format = _openai_response_format(checked.response_schema_json)
        except OpenAIGatewayConfigurationError as exc:
            raise AdapterInvocationError(
                code="permanent_provider_error",
                retryable=False,
                provider_attempt_ref=_provider_attempt_ref(
                    checked,
                    provider_request_id=None,
                    outcome="response_schema_incompatible",
                ),
            ) from exc
        payload: dict[str, object] = {
            "model": self.policy.model_version,
            "messages": [
                {"role": "system", "content": checked.prompt_text},
                {"role": "user", "content": checked.context_json},
            ],
            "response_format": response_format,
            "temperature": self.policy.temperature,
            "top_p": self.policy.top_p,
            "seed": self.policy.seed,
            "max_tokens": self.policy.max_output_tokens,
            "n": 1,
            "store": self.policy.store_provider_response,
            "stream": False,
            "timeout": checked.runtime_policy.timeout_ns / 1_000_000_000,
            "extra_headers": {"X-Client-Request-Id": checked.attempt_id},
        }
        try:
            response = self._client.chat.completions.create(**payload)
        except Exception as exc:
            raise _translated_provider_error(checked, exc) from exc
        return self._response_envelope(checked, response)

    def _verify_runtime_policy(self, call: ProviderCall) -> None:
        expected_timeout_ns = int(self.policy.timeout_seconds * 1_000_000_000)
        if (
            call.runtime_policy.timeout_ns != expected_timeout_ns
            or call.runtime_policy.max_attempts
            != self.policy.provider_attempt_ceiling
        ):
            raise AdapterInvocationError(
                code="permanent_provider_error",
                retryable=False,
                provider_attempt_ref=_provider_attempt_ref(
                    call,
                    provider_request_id=None,
                    outcome="runtime_policy_mismatch",
                ),
            )

    def _response_envelope(self, call: ProviderCall, response: object) -> ProviderEnvelope:
        response_id = getattr(response, "id", None)
        actual_model = getattr(response, "model", None)
        choices = getattr(response, "choices", None)
        if (
            not isinstance(response_id, str)
            or not response_id.strip()
            or actual_model != self.policy.model_version
            or not isinstance(choices, list)
            or len(choices) != 1
        ):
            raise _permanent_response_error(call, response_id)
        choice = choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None)
        refusal = getattr(message, "refusal", None)
        if (
            finish_reason != "stop"
            or not isinstance(content, str)
            or (isinstance(refusal, str) and refusal.strip())
        ):
            raise _permanent_response_error(call, response_id)
        try:
            raw_content = content.encode("utf-8", errors="strict")
            usage = _usage_metadata(getattr(response, "usage", None))
        except (UnicodeEncodeError, ValueError, ValidationError) as exc:
            raise _permanent_response_error(call, response_id) from exc
        return ProviderEnvelope(
            request_identity_sha256=call.request_identity_sha256,
            binding=self.binding,
            provider_attempt_ref=_provider_attempt_ref(
                call,
                provider_request_id=response_id,
                outcome="response",
            ),
            response_mode=_response_mode(content),
            raw_response=RawResponseArtifact.from_bytes(raw_content),
            usage=usage,
        )


def _validated_configuration(
    *,
    model_policy: ModelPolicyReference,
    policy: OpenAIGatewayPolicy,
) -> tuple[ModelPolicyReference, OpenAIGatewayPolicy]:
    try:
        checked_policy = OpenAIGatewayPolicy.model_validate(
            policy.model_dump(mode="python")
        )
        checked_model_policy = ModelPolicyReference.model_validate(
            model_policy.model_dump(mode="python")
        )
    except ValidationError as exc:
        raise OpenAIGatewayConfigurationError(
            "OpenAI gateway policy binding is invalid"
        ) from exc
    if checked_model_policy.policy_content_sha256 != checked_policy.model_policy_sha256():
        raise OpenAIGatewayConfigurationError(
            "model-policy reference does not bind the frozen OpenAI policy"
        )
    return checked_model_policy, checked_policy


def _openai_response_format(schema_json: str) -> dict[str, object]:
    try:
        schema = json.loads(schema_json)
    except json.JSONDecodeError as exc:
        raise OpenAIGatewayConfigurationError("response schema is not valid JSON") from exc
    if not isinstance(schema, dict):
        raise OpenAIGatewayConfigurationError("response schema must be an object")
    checked_schema = cast(dict[str, object], schema)
    try:
        validate_response_schema(checked_schema)
    except ValueError as exc:
        raise OpenAIGatewayConfigurationError(
            "response schema is outside the gateway safe subset"
        ) from exc
    _ensure_openai_strict_schema(checked_schema, depth=0)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": _SCHEMA_NAME,
            "strict": True,
            "schema": schema,
        },
    }


def _ensure_openai_strict_schema(schema: dict[str, object], *, depth: int) -> None:
    if depth > 8:
        raise OpenAIGatewayConfigurationError("response schema exceeds adapter depth")
    schema_type = schema.get("type")
    if schema_type == "object":
        properties = schema.get("properties")
        required = schema.get("required")
        if (
            schema.get("additionalProperties") is not False
            or not isinstance(properties, dict)
            or not isinstance(required, list)
            or set(required) != set(properties)
        ):
            raise OpenAIGatewayConfigurationError(
                "structured-output objects must be closed and require every property"
            )
        for child in properties.values():
            if not isinstance(child, dict):
                raise OpenAIGatewayConfigurationError(
                    "structured-output property schema is invalid"
                )
            _ensure_openai_strict_schema(cast(dict[str, object], child), depth=depth + 1)
    elif schema_type == "array":
        items = schema.get("items")
        if not isinstance(items, dict):
            raise OpenAIGatewayConfigurationError(
                "structured-output arrays require one item schema"
            )
        _ensure_openai_strict_schema(cast(dict[str, object], items), depth=depth + 1)


def _usage_metadata(usage: object) -> UsageMetadata:
    if usage is None:
        return UsageMetadata(
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            cost_amount=None,
            cost_currency_ref=None,
        )
    return UsageMetadata(
        input_tokens=_optional_nonnegative_int(getattr(usage, "prompt_tokens", None)),
        output_tokens=_optional_nonnegative_int(
            getattr(usage, "completion_tokens", None)
        ),
        total_tokens=_optional_nonnegative_int(getattr(usage, "total_tokens", None)),
        cost_amount=None,
        cost_currency_ref=None,
    )


def _optional_nonnegative_int(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("provider usage contains an invalid token count")
    return value


def _response_mode(content: str) -> Literal["structured", "abstention"]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return "structured"
    return (
        "abstention"
        if isinstance(payload, dict) and payload.get("abstain") is True
        else "structured"
    )


def _translated_provider_error(
    call: ProviderCall,
    error: Exception,
) -> AdapterInvocationError:
    names = {item.__name__ for item in type(error).__mro__}
    status_code = getattr(error, "status_code", None)
    provider_request_id = getattr(error, "request_id", None)
    if not isinstance(provider_request_id, str):
        provider_request_id = None
    if names & {
        "APITimeoutError",
        "ConnectTimeout",
        "PoolTimeout",
        "ReadTimeout",
        "TimeoutError",
        "WriteTimeout",
    }:
        code: ProviderErrorCode = "provider_timeout"
        retryable = True
    elif names & {"CancelledError"}:
        code = "provider_cancelled"
        retryable = False
    elif (
        names & {"APIConnectionError", "InternalServerError", "RateLimitError"}
        or status_code in {408, 409, 425, 429}
        or (isinstance(status_code, int) and status_code >= 500)
    ):
        code = "transient_provider_error"
        retryable = True
    else:
        code = "permanent_provider_error"
        retryable = False
    return AdapterInvocationError(
        code=code,
        retryable=retryable,
        provider_attempt_ref=_provider_attempt_ref(
            call,
            provider_request_id=provider_request_id,
            outcome=code,
        ),
    )


def _permanent_response_error(
    call: ProviderCall,
    response_id: object,
) -> AdapterInvocationError:
    provider_request_id = response_id if isinstance(response_id, str) else None
    return AdapterInvocationError(
        code="permanent_provider_error",
        retryable=False,
        provider_attempt_ref=_provider_attempt_ref(
            call,
            provider_request_id=provider_request_id,
            outcome="invalid_response_envelope",
        ),
    )


def _provider_attempt_ref(
    call: ProviderCall,
    *,
    provider_request_id: str | None,
    outcome: str,
) -> str:
    request_id_sha256 = (
        canonical_execution_sha256({"provider_request_id": provider_request_id})
        if provider_request_id is not None
        else None
    )
    digest = canonical_execution_sha256(
        {
            "adapter": OPENAI_GATEWAY_ADAPTER_VERSION,
            "request_identity_sha256": call.request_identity_sha256,
            "attempt_identity_sha256": call.attempt_identity_sha256,
            "attempt_ordinal": call.attempt_ordinal,
            "provider_request_id_sha256": request_id_sha256,
            "outcome": outcome,
        }
    )
    return f"ev-{digest}"


__all__ = [
    "OPENAI_API_BASE_URL",
    "OPENAI_GATEWAY_ADAPTER_VERSION",
    "OPENAI_GATEWAY_POLICY_SCHEMA_VERSION",
    "OpenAIChatCompletionsGatewayAdapter",
    "OpenAIGatewayConfigurationError",
    "OpenAIGatewayPolicy",
]
