"""Pure attempt-ledger transition rules."""

from __future__ import annotations

from typing import Final

from aletheia_lab.evaluation.execution_contracts import AttemptIdentity

from .contracts import (
    AttemptStoreIntegrityError,
    AttemptStoreTransitionError,
    StoreLedgerEntry,
    StoreState,
)

_ALLOWED_PREVIOUS: Final[dict[StoreState, frozenset[StoreState | None]]] = {
    "prepared": frozenset({None}),
    "started": frozenset({"prepared"}),
    "attempt_recorded": frozenset({"started", "attempt_recorded"}),
    "response_recorded": frozenset({"attempt_recorded"}),
    "parsed_or_failed": frozenset({"attempt_recorded", "response_recorded"}),
    "closeout_pending": frozenset({"parsed_or_failed"}),
    "terminal_published": frozenset({"closeout_pending"}),
}


def validate_transition(
    entries: tuple[StoreLedgerEntry, ...],
    state: StoreState,
    attempt: AttemptIdentity | None,
) -> None:
    """Reject illegal, skipped, duplicated or replayed lifecycle events."""
    previous = entries[-1].state if entries else None
    if previous not in _ALLOWED_PREVIOUS[state]:
        raise AttemptStoreTransitionError(
            "invalid_transition", "store transition is not allowed from current state"
        )
    if state == "attempt_recorded":
        if attempt is None:
            raise AttemptStoreTransitionError(
                "invalid_transition", "attempt_recorded transition requires an attempt"
            )
        expected_ordinal = 1 + sum(entry.state == "attempt_recorded" for entry in entries)
        if attempt.attempt_ordinal != expected_ordinal:
            raise AttemptStoreTransitionError(
                "replay_rejected", "attempt ordinal is duplicate, missing, or replayed"
            )


def verify_stored_transition(
    entries: tuple[StoreLedgerEntry, ...],
    entry: StoreLedgerEntry,
) -> None:
    """Independently validate one persisted transition."""
    previous = entries[-1].state if entries else None
    if previous not in _ALLOWED_PREVIOUS[entry.state]:
        raise AttemptStoreIntegrityError("integrity_error", "persisted store transition is invalid")
    if entry.state == "attempt_recorded":
        expected_ordinal = 1 + sum(existing.state == "attempt_recorded" for existing in entries)
        if entry.attempt_ordinal != expected_ordinal:
            raise AttemptStoreIntegrityError(
                "integrity_error", "persisted attempt ordinal is not contiguous"
            )
