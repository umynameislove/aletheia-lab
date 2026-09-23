"""Shared provider budget guard for the diagnosis main pipeline."""

from __future__ import annotations

import threading
from decimal import ROUND_UP, Decimal

from aletheia_lab.evaluation.execution_contracts import (
    canonical_execution_json,
    canonical_execution_sha256,
)
from aletheia_lab.model_gateway import (
    AdapterInvocationError,
    AttemptRecord,
    GatewayRequest,
    ProviderAdapter,
    ProviderBinding,
    ProviderCall,
    ProviderEnvelope,
)

INPUT_USD_PER_MILLION_TOKENS = 2.0
OUTPUT_USD_PER_MILLION_TOKENS = 8.0
_INPUT_USD_PER_TOKEN = Decimal(str(INPUT_USD_PER_MILLION_TOKENS)) / Decimal("1000000")
_OUTPUT_USD_PER_TOKEN = Decimal(str(OUTPUT_USD_PER_MILLION_TOKENS)) / Decimal("1000000")
_MAXIMUM_OUTPUT_TOKENS = 600
_CHAT_AND_RESPONSE_FORMAT_TOKEN_ALLOWANCE = 1024


class SharedProviderBudget:
    """Conservatively reserve each call before it can reach the provider."""

    def __init__(self, ceiling_usd: float) -> None:
        self._ceiling = Decimal(str(ceiling_usd))
        self._committed = Decimal("0")
        self._exhausted = False
        self._lock = threading.Lock()

    @property
    def ceiling_usd(self) -> float:
        return float(self._ceiling)

    @property
    def committed_usd(self) -> float:
        with self._lock:
            return float(self._committed.quantize(Decimal("0.000001"), rounding=ROUND_UP))

    @property
    def exhausted(self) -> bool:
        with self._lock:
            return self._exhausted

    @staticmethod
    def _reservation(call: ProviderCall) -> Decimal:
        # GPT tokenization is byte based, so UTF-8 bytes are a strict upper
        # bound on token count.  The additional allowance covers ChatML and the
        # structured-output wrapper without relying on provider internals.
        input_token_ceiling = (
            len(call.context_json.encode("utf-8"))
            + len(call.prompt_text.encode("utf-8"))
            + len(call.response_schema_json.encode("utf-8"))
            + _CHAT_AND_RESPONSE_FORMAT_TOKEN_ALLOWANCE
        )
        return (
            Decimal(input_token_ceiling) * _INPUT_USD_PER_TOKEN
            + Decimal(_MAXIMUM_OUTPUT_TOKENS) * _OUTPUT_USD_PER_TOKEN
        )

    @staticmethod
    def _request_reservation(request: GatewayRequest) -> Decimal:
        context_json = canonical_execution_json(request.context.model_payload())  # type: ignore[union-attr]
        input_token_ceiling = (
            len(context_json.encode("utf-8"))
            + len(request.prompt_text.encode("utf-8"))
            + len(request.response_schema_json.encode("utf-8"))
            + _CHAT_AND_RESPONSE_FORMAT_TOKEN_ALLOWANCE
        )
        return (
            Decimal(input_token_ceiling) * _INPUT_USD_PER_TOKEN
            + Decimal(_MAXIMUM_OUTPUT_TOKENS) * _OUTPUT_USD_PER_TOKEN
        )

    @staticmethod
    def _attempt_cost(reservation: Decimal, record: AttemptRecord) -> Decimal:
        usage = record.usage
        if usage is None or usage.input_tokens is None or usage.output_tokens is None:
            return reservation
        actual = (
            Decimal(usage.input_tokens) * _INPUT_USD_PER_TOKEN
            + Decimal(usage.output_tokens) * _OUTPUT_USD_PER_TOKEN
        )
        return actual

    def restore(self, request: GatewayRequest, records: tuple[AttemptRecord, ...]) -> None:
        """Restore prior sealed attempt cost before a resumed run can dispatch."""

        reservation = self._request_reservation(request)
        restored = sum(
            (self._attempt_cost(reservation, record) for record in records),
            start=Decimal("0"),
        )
        with self._lock:
            self._committed += restored
            if self._committed > self._ceiling:
                self._exhausted = True

    def reserve(self, call: ProviderCall) -> Decimal:
        amount = self._reservation(call)
        with self._lock:
            if self._exhausted or self._committed + amount > self._ceiling:
                self._exhausted = True
                raise AdapterInvocationError(
                    code="permanent_provider_error",
                    retryable=False,
                    provider_attempt_ref=(
                        f"ev-{canonical_execution_sha256({'budget_refusal': call.attempt_identity_sha256})}"
                    ),
                    provider_failure_category="request_rejected",
                )
            self._committed += amount
        return amount

    def settle(self, reservation: Decimal, envelope: ProviderEnvelope) -> None:
        usage = envelope.usage
        if usage.input_tokens is None or usage.output_tokens is None:
            return
        actual = (
            Decimal(usage.input_tokens) * _INPUT_USD_PER_TOKEN
            + Decimal(usage.output_tokens) * _OUTPUT_USD_PER_TOKEN
        )
        if actual > reservation:
            # The reservation is deliberately a byte-level upper bound.  If a
            # provider reports more, retain the safe reservation and stop the
            # run before another request can be dispatched.
            with self._lock:
                self._committed += actual - reservation
                self._exhausted = True
            return
        with self._lock:
            self._committed -= reservation - actual


class BudgetedProviderAdapter:
    """Apply one shared cap across the main and relation provider stages."""

    def __init__(self, delegate: ProviderAdapter, budget: SharedProviderBudget) -> None:
        self._delegate = delegate
        self._budget = budget

    @property
    def binding(self) -> ProviderBinding:
        return self._delegate.binding

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        reservation = self._budget.reserve(call)
        envelope = self._delegate.invoke(call)
        self._budget.settle(reservation, envelope)
        return envelope


__all__ = [
    "INPUT_USD_PER_MILLION_TOKENS",
    "OUTPUT_USD_PER_MILLION_TOKENS",
    "BudgetedProviderAdapter",
    "SharedProviderBudget",
]
