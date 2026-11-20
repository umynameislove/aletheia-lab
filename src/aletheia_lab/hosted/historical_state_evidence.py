"""Bounded authorization decisions over admitted historical state observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from aletheia_lab.project.identity import SHA256_PATTERN

_Token = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")]
_Digest = Annotated[str, Field(pattern=SHA256_PATTERN)]
_CONSUMERS = frozenset({"scoped-two-slice-reader", "state-authorization-reader"})


class _FrozenInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")


class HistoricalState(_FrozenInput):
    namespace: _Token
    generation: _Token
    state_sha256: _Digest


class HistoricalStateReceipt(_FrozenInput):
    schema_version: Literal["historical-state-receipt/v1"] = "historical-state-receipt/v1"
    operation_id: _Token
    request_id: _Token
    consumer_id: _Token
    execution_mode: Literal["fresh", "replay"]
    producer_version: _Token
    source_receipt_sha256: _Digest
    observation: HistoricalState | None
    output: tuple[FiniteFloat, FiniteFloat] | None = None
    graph_sha256: _Digest | None = None
    current_alias: _Token | None = None


class HistoricalStateContract(_FrozenInput):
    schema_version: Literal["historical-state-contract/v1"] = "historical-state-contract/v1"
    consumer_id: _Token
    allowed_state: HistoricalState


class HistoricalStateInput(_FrozenInput):
    """The host pairs an admitted receipt with an independently fixed contract."""

    schema_version: Literal["historical-state-input/v1"] = "historical-state-input/v1"
    receipt: HistoricalStateReceipt
    contract: HistoricalStateContract | None

    @model_validator(mode="after")
    def _consumer_binding(self) -> Self:
        if self.contract is not None and self.contract.consumer_id != self.receipt.consumer_id:
            raise ValueError("The historical consumer does not match its contract.")
        return self


@dataclass(frozen=True)
class HistoricalStateDecision:
    verdict: Literal["allowed", "denied", "unknown", "underdefined", "unsupported"]
    reason: str


def evaluate_historical_state(evidence: HistoricalStateInput) -> HistoricalStateDecision:
    """Evaluate the declared consumer predicate without invoking a runner or sink."""
    receipt, contract = evidence.receipt, evidence.contract
    if receipt.consumer_id not in _CONSUMERS:
        return HistoricalStateDecision("unsupported", "This historical consumer is outside the supported contract family.")
    if contract is None:
        return HistoricalStateDecision("underdefined", "The original consumer contract is unavailable.")
    if receipt.observation is None:
        return HistoricalStateDecision("unknown", "The state used by the original operation is unavailable.")
    if receipt.observation != contract.allowed_state:
        return HistoricalStateDecision("denied", "The observed state differs from the state allowed by the original contract.")
    return HistoricalStateDecision("allowed", "The observed state matches the state allowed by the original contract.")
