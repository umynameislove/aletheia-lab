"""Recovery-only failure taxonomy; preserve the pinned predecessor transport."""

from aletheia_lab.model_gateway.contracts import (
    AdapterInvocationError,
    ProviderCall,
    ProviderEnvelope,
    ProviderErrorCode,
)
from aletheia_lab.model_gateway.openai import (
    OpenAIChatCompletionsGatewayAdapter,
    OpenAIGatewayConfigurationError,
    _provider_attempt_ref,
)


class OpenAIRecoveryAdapter(OpenAIChatCompletionsGatewayAdapter):
    """Use identical transport settings with public-safe terminal reason codes."""

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
        choices = getattr(response, "choices", None)
        if (
            not isinstance(response_id, str)
            or not response_id.strip()
            or getattr(response, "model", None) != self.policy.model_version
            or not isinstance(choices, list)
            or len(choices) != 1
        ):
            raise _failure(call, response_id, "provider_invalid_envelope")
        choice = choices[0]
        finish = getattr(choice, "finish_reason", None)
        refusal = getattr(getattr(choice, "message", None), "refusal", None)
        if finish == "content_filter" or (isinstance(refusal, str) and refusal.strip()):
            raise _failure(call, response_id, "provider_refusal")
        if finish == "length":
            raise _failure(call, response_id, "provider_output_truncated")
        try:
            return super()._response_envelope(call, response)
        except AdapterInvocationError:
            raise _failure(call, response_id, "provider_invalid_envelope") from None


def _failure(
    call: ProviderCall, response_id: object, code: ProviderErrorCode
) -> AdapterInvocationError:
    return AdapterInvocationError(
        code=code,
        retryable=False,
        provider_attempt_ref=_provider_attempt_ref(
            call,
            provider_request_id=response_id if isinstance(response_id, str) else None,
            outcome=code,
        ),
    )
