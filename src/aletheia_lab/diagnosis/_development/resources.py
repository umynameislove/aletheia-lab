"""Deterministic resource accounting for development requests."""

from __future__ import annotations

from aletheia_lab.diagnosis._development.contracts import (
    DevelopmentResourceObservation,
    DevelopmentVariantRequest,
)
from aletheia_lab.project.identity import canonical_project_json


def resource_observation_for_request(
    request: DevelopmentVariantRequest,
) -> DevelopmentResourceObservation:
    """Recompute the deterministic resource envelope for one persisted request."""

    checked = DevelopmentVariantRequest.model_validate(request.model_dump(mode="python"))
    return _resource_observation(checked)


def _resource_observation(
    request: DevelopmentVariantRequest,
) -> DevelopmentResourceObservation:
    context_bytes = canonical_project_json(request.context_payload).encode("utf-8")
    ledger = request.tool_ledger
    return DevelopmentResourceObservation(
        # Any token representing the UTF-8 payload consumes at least one byte.
        # Counting bytes is deliberately conservative and cannot understate the
        # token count the way a characters-per-token heuristic can.
        context_tokens_upper_bound=len(context_bytes),
        retrieved_items=(
            sum(len(item.selected_evidence_ids) for item in ledger.events) if ledger else 0
        ),
        turns=len(ledger.events) if ledger else 1,
        tool_calls=len(ledger.events) if ledger else 0,
        provider_calls=0,
    )
