"""Prospective V2 pacing, retry and public-safe OpenAI failure translation."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Final

from aletheia_lab.model_gateway.contracts import (
    AdapterInvocationError,
    ProviderAdapter,
    ProviderBinding,
    ProviderCall,
    ProviderEnvelope,
    ProviderErrorCode,
    ProviderFailureCategory,
)
from aletheia_lab.model_gateway.openai import (
    OpenAIChatCompletionsGatewayAdapter,
    OpenAIGatewayConfigurationError,
    _provider_attempt_ref,
)
from aletheia_lab.model_gateway.openai_recovery import OpenAIRecoveryAdapter

_RETRYABLE_CATEGORIES: Final[frozenset[ProviderFailureCategory]] = frozenset(
    {
        "rate_limited",
        "timeout",
        "connection",
        "http_408",
        "http_409",
        "http_425",
        "server_error",
    }
)
_RETRY_AFTER_CEILING_MS: Final = 60_000


class OpenAIValidationV2Adapter(OpenAIRecoveryAdapter):
    """Preserve the recovery schema while exposing only allowlisted failure facts."""

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        # Call the transport base directly so the V1 recovery compatibility
        # wrapper cannot collapse the prospective V2 taxonomy.
        try:
            return OpenAIChatCompletionsGatewayAdapter.invoke(self, call)
        except AdapterInvocationError as exc:
            if exc.provider_failure_category is not None or exc.code == "provider_cancelled":
                raise
            category: ProviderFailureCategory = (
                "schema_incompatible"
                if isinstance(exc.__cause__, OpenAIGatewayConfigurationError)
                else "request_rejected"
            )
            raise AdapterInvocationError(
                code="provider_schema_incompatible" if category == "schema_incompatible"
                else "permanent_provider_error",
                retryable=False,
                provider_attempt_ref=exc.provider_attempt_ref,
                provider_failure_category=category,
            ) from None

    def _translate_provider_error(
        self, call: ProviderCall, error: Exception
    ) -> AdapterInvocationError:
        if any(cls.__name__ == "CancelledError" for cls in type(error).__mro__):
            return AdapterInvocationError(
                code="provider_cancelled", retryable=False,
                provider_attempt_ref=_provider_attempt_ref(
                    call, provider_request_id=None, outcome="provider_cancelled"
                ),
            )
        category = _provider_failure_category(error)
        retryable = category in _RETRYABLE_CATEGORIES
        code: ProviderErrorCode
        if category == "timeout":
            code = "provider_timeout"
        elif retryable:
            code = "transient_provider_error"
        else:
            code = "permanent_provider_error"
        provider_request_id = getattr(error, "request_id", None)
        if not isinstance(provider_request_id, str):
            provider_request_id = None
        return AdapterInvocationError(
            code=code,
            retryable=retryable,
            provider_attempt_ref=_provider_attempt_ref(
                call,
                provider_request_id=provider_request_id,
                outcome=category,
            ),
            provider_failure_category=category,
            retry_after_ms=(
                _retry_after_ms(error) if category in _RETRYABLE_CATEGORIES else None
            ),
        )

    def _response_envelope(self, call: ProviderCall, response: object) -> ProviderEnvelope:
        try:
            return super()._response_envelope(call, response)
        except AdapterInvocationError as exc:
            categories: dict[ProviderErrorCode, ProviderFailureCategory] = {
                "provider_output_truncated": "truncated",
                "provider_refusal": "refusal",
                "provider_invalid_envelope": "invalid_envelope",
                "provider_schema_incompatible": "schema_incompatible",
                "provider_http_error": "request_rejected",
                "permanent_provider_error": "invalid_envelope",
            }
            category = categories.get(exc.code, "invalid_envelope")
            raise AdapterInvocationError(
                code=exc.code,
                retryable=False,
                provider_attempt_ref=exc.provider_attempt_ref,
                diagnostics=exc.diagnostics,
                provider_failure_category=category,
            ) from None


class GloballyPacedProviderAdapter:
    """Serialize provider starts and keep their monotonic spacing at one bound."""

    def __init__(
        self,
        delegate: ProviderAdapter,
        *,
        minimum_interval_ms: int,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if type(minimum_interval_ms) is not int or minimum_interval_ms != 1000:
            raise ValueError("V2 provider start interval must be at least 1000 ms")
        self._delegate = delegate
        self._minimum_interval_seconds = minimum_interval_ms / 1000
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last_started: float | None = None

    @property
    def binding(self) -> ProviderBinding:
        return self._delegate.binding

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        with self._lock:
            now = self._monotonic()
            if self._last_started is not None:
                remaining = self._minimum_interval_seconds - (now - self._last_started)
                if remaining > 0:
                    self._sleep(remaining)
            self._last_started = self._monotonic()
            return self._delegate.invoke(call)


class V2RetryController:
    """Apply the frozen bounded exponential delay and optional Retry-After."""

    def __init__(
        self,
        *,
        initial_backoff_ms: int = 5000,
        multiplier: int = 2,
        backoff_ceiling_ms: int = 60_000,
        retry_after_ceiling_ms: int = 60_000,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if (
            isinstance(initial_backoff_ms, bool)
            or initial_backoff_ms != 5000
            or isinstance(multiplier, bool)
            or multiplier != 2
            or isinstance(backoff_ceiling_ms, bool)
            or backoff_ceiling_ms != 60_000
            or isinstance(retry_after_ceiling_ms, bool)
            or retry_after_ceiling_ms != 60_000
        ):
            raise ValueError("retry controller differs from the frozen V2 policy")
        self._initial = initial_backoff_ms
        self._multiplier = multiplier
        self._backoff_ceiling = backoff_ceiling_ms
        self._retry_after_ceiling = retry_after_ceiling_ms
        self._sleep = sleep

    def wait_before_retry(
        self,
        *,
        completed_attempt_ordinal: int,
        provider_failure_category: ProviderFailureCategory,
        retry_after_ms: int | None,
    ) -> None:
        if provider_failure_category not in _RETRYABLE_CATEGORIES:
            raise ValueError("V2 attempted to retry a non-retryable provider category")
        if type(completed_attempt_ordinal) is not int or completed_attempt_ordinal < 1:
            raise ValueError("completed attempt ordinal must be positive")
        if retry_after_ms is not None and (
            type(retry_after_ms) is not int or not 0 <= retry_after_ms <= 60_000
        ):
            raise ValueError("Retry-After differs from its bounded contract")
        registered = min(
            self._initial * self._multiplier ** min(completed_attempt_ordinal - 1, 4),
            self._backoff_ceiling,
        )
        provider_delay = min(retry_after_ms or 0, self._retry_after_ceiling)
        self._sleep(max(registered, provider_delay) / 1000)


def _provider_failure_category(error: Exception) -> ProviderFailureCategory:
    names = {item.__name__ for item in type(error).__mro__}
    status = getattr(error, "status_code", None)
    if names & {
        "APITimeoutError",
        "ConnectTimeout",
        "PoolTimeout",
        "ReadTimeout",
        "TimeoutError",
        "WriteTimeout",
    }:
        return "timeout"
    if names & {"RateLimitError"} or status == 429:
        return "rate_limited"
    if names & {"APIConnectionError", "ConnectError", "NetworkError"}:
        return "connection"
    if status == 408:
        return "http_408"
    if status == 409:
        return "http_409"
    if status == 425:
        return "http_425"
    if names & {"InternalServerError"} or (
        isinstance(status, int) and not isinstance(status, bool) and status >= 500
    ):
        return "server_error"
    return "request_rejected"


def _retry_after_ms(error: Exception) -> int | None:
    headers: object = getattr(error, "headers", None)
    response = getattr(error, "response", None)
    if headers is None and response is not None:
        headers = getattr(response, "headers", None)
    if not isinstance(headers, Mapping):
        return None
    raw = next(
        (value for key, value in headers.items() if str(key).lower() == "retry-after"),
        None,
    )
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        seconds = float(raw)
    elif isinstance(raw, str):
        try:
            seconds = float(raw.strip())
        except ValueError:
            try:
                target = parsedate_to_datetime(raw)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=UTC)
                seconds = (target - datetime.now(UTC)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return None
    else:
        return None
    if not math.isfinite(seconds):
        return None
    if seconds < 0:
        return 0
    return math.ceil(min(seconds, _RETRY_AFTER_CEILING_MS / 1000) * 1000)


__all__ = [
    "GloballyPacedProviderAdapter",
    "OpenAIValidationV2Adapter",
    "V2RetryController",
]
