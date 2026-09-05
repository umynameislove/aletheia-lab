"""Read claim-corpus terminal payloads without changing the frozen store."""

from __future__ import annotations

from aletheia_lab.evaluation._attempt_store.contracts import (
    AttemptStoreIntegrityError,
    AttemptStoreTransitionError,
    TerminalExecutionInventory,
)
from aletheia_lab.evaluation._attempt_store.integrity import (
    AttemptStoreIntegrityVerifier,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256


class ClaimCorpusTerminalReader(AttemptStoreIntegrityVerifier):
    """Expose verified parsed objects while keeping raw bytes inaccessible."""

    def terminal_inventory(self, request_hash: str) -> TerminalExecutionInventory:
        return self._terminal_inventory(request_hash)

    def terminal_parsed_payload(self, request_hash: str) -> dict[str, object] | None:
        entries, terminal = self._load_chain(request_hash)
        if terminal is None:
            raise AttemptStoreTransitionError(
                "invalid_transition", "request has no published terminal result"
            )
        outcome = next(
            (entry for entry in entries if entry.state == "parsed_or_failed"),
            None,
        )
        if outcome is None:
            raise AttemptStoreIntegrityError(
                "integrity_error", "terminal request has no outcome entry"
            )
        if outcome.parsed_object_sha256 is None:
            if outcome.parsed_response_sha256 is not None:
                raise AttemptStoreIntegrityError(
                    "integrity_error", "parsed response has no immutable object"
                )
            return None
        payload = self._read_object(outcome.parsed_object_sha256)
        parsed = self._read_canonical_mapping(payload, label="parsed response")
        if canonical_execution_sha256(parsed) != outcome.parsed_response_sha256:
            raise AttemptStoreIntegrityError(
                "integrity_error", "parsed response identity differs from ledger"
            )
        return parsed


__all__ = ["ClaimCorpusTerminalReader"]
