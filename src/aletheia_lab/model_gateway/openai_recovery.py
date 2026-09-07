"""Recovery transport projection and public-safe failure diagnostics."""

from typing import Literal, cast

from aletheia_lab.model_gateway.contracts import (
    AdapterInvocationError,
    ProviderCall,
    ProviderEnvelope,
    ProviderErrorCode,
    ProviderFailureDiagnostics,
)
from aletheia_lab.model_gateway.openai import (
    OpenAIChatCompletionsGatewayAdapter,
    OpenAIGatewayConfigurationError,
    _openai_response_format,
    _provider_attempt_ref,
    _usage_metadata,
)
from aletheia_lab.model_gateway.recovery_transport import (
    validate_recovery_text,
    wire_schema_json,
)
from aletheia_lab.project.identity import content_sha256


class OpenAIRecoveryAdapter(OpenAIChatCompletionsGatewayAdapter):
    """Use identical transport settings with public-safe terminal reason codes."""

    def _prepare_payload(self, call: ProviderCall, payload: dict[str, object]) -> dict[str, object]:
        if '"diagnosis-provider-output/2"' in call.response_schema_json:
            return {
                **payload,
                "response_format": _openai_response_format(
                    wire_schema_json(call.response_schema_json)
                ),
            }
        return payload

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        try:
            return super().invoke(call)
        except AdapterInvocationError as exc:
            if exc.code != "permanent_provider_error":
                raise
            cause = exc.__cause__
            code: ProviderErrorCode
            if isinstance(cause, OpenAIGatewayConfigurationError):
                code = "provider_schema_incompatible"
            elif isinstance(getattr(cause, "status_code", None), int):
                code = "provider_http_error"
            else:
                raise
            # Persist only an allowlisted code, never arbitrary provider text.
            raise AdapterInvocationError(
                code=code,
                retryable=False,
                provider_attempt_ref=exc.provider_attempt_ref,
            ) from None

    def _response_envelope(self, call: ProviderCall, response: object) -> ProviderEnvelope:
        response_id = getattr(response, "id", None)
        diagnostics = _diagnostics(
            response, self.policy.max_output_tokens, self.policy.model_version
        )
        choices = getattr(response, "choices", None)
        if (
            not isinstance(response_id, str)
            or not response_id.strip()
            or getattr(response, "model", None) != self.policy.model_version
            or not isinstance(choices, list)
            or len(choices) != 1
        ):
            raise _failure(call, response_id, "provider_invalid_envelope", diagnostics)
        choice = choices[0]
        finish = getattr(choice, "finish_reason", None)
        refusal = getattr(getattr(choice, "message", None), "refusal", None)
        if finish == "content_filter" or (isinstance(refusal, str) and refusal.strip()):
            raise _failure(call, response_id, "provider_refusal", diagnostics)
        if finish == "length":
            raise _failure(call, response_id, "provider_output_truncated", diagnostics)
        try:
            if '"diagnosis-provider-output/2"' in call.response_schema_json:
                content = getattr(getattr(choice, "message", None), "content", None)
                if not isinstance(content, str):
                    raise ValueError("missing recovery content")
                validate_recovery_text(content)
            return super()._response_envelope(call, response)
        except (AdapterInvocationError, ValueError):
            raise _failure(call, response_id, "provider_invalid_envelope", diagnostics) from None


def _failure(
    call: ProviderCall,
    response_id: object,
    code: ProviderErrorCode,
    diagnostics: ProviderFailureDiagnostics,
) -> AdapterInvocationError:
    return AdapterInvocationError(
        code=code,
        retryable=False,
        diagnostics=diagnostics,
        provider_attempt_ref=_provider_attempt_ref(
            call,
            provider_request_id=response_id if isinstance(response_id, str) else None,
            outcome=code,
        ),
    )


def _diagnostics(response: object, limit: int, model: str) -> ProviderFailureDiagnostics:
    choices = getattr(response, "choices", None)
    choice = choices[0] if isinstance(choices, list) and len(choices) == 1 else None
    finish = getattr(choice, "finish_reason", None)
    allowed = {"length", "stop", "content_filter", "tool_calls", "function_call"}
    content = getattr(getattr(choice, "message", None), "content", None)
    try:
        raw = content.encode("utf-8") if isinstance(content, str) else None
    except UnicodeError:
        raw = None
    try:
        usage = _usage_metadata(getattr(response, "usage", None))
    except ValueError:
        usage = None
    return ProviderFailureDiagnostics(
        finish_reason=cast(
            Literal["length", "stop", "content_filter", "tool_calls", "function_call", "unknown"],
            finish if isinstance(finish, str) and finish in allowed else "unknown",
        ),
        configured_output_token_limit=limit,
        usage=usage,
        content_utf8_bytes=len(raw) if raw is not None else None,
        content_sha256=content_sha256(raw) if raw is not None else None,
        model_snapshot_matched=getattr(response, "model", None) == model,
    )
