"""Request/result reconciliation independent of store publication."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from aletheia_lab.evaluation.execution_contracts import AttemptIdentity
from aletheia_lab.model_gateway.contracts import GatewayExecutionResult, GatewayRequest
from aletheia_lab.project.identity import content_sha256

from .contracts import (
    AttemptStoreIntegrityError,
    AttemptStoreTransitionError,
    StoreLedgerEntry,
    StoreState,
    _canonical_bytes,
)
from .reader import AttemptStoreReader


class AttemptStoreReconciler(AttemptStoreReader):
    """Bind runtime contracts to independently read persisted identities."""

    def __init__(self, *, object_root: Path, request_root: Path, terminal_root: Path) -> None:
        super().__init__(
            object_root=object_root,
            request_root=request_root,
            terminal_root=terminal_root,
        )

    def _checked_request(self, request: GatewayRequest) -> GatewayRequest:
        try:
            return GatewayRequest.model_validate(request.model_dump(mode="python"))
        except (AttributeError, ValidationError) as exc:
            raise AttemptStoreIntegrityError(
                "integrity_error", "store request contract is invalid"
            ) from exc

    def _checked_result(
        self,
        request: GatewayRequest,
        result: GatewayExecutionResult,
    ) -> GatewayExecutionResult:
        try:
            checked = GatewayExecutionResult.model_validate(result.model_dump(mode="python"))
        except (AttributeError, ValidationError) as exc:
            raise AttemptStoreIntegrityError(
                "integrity_error", "gateway result contract is invalid"
            ) from exc
        if checked.request_identity_sha256 != request.initial_attempt.request_identity_sha256:
            raise AttemptStoreTransitionError(
                "replay_rejected", "gateway result belongs to another request"
            )
        for record in checked.attempts:
            if record.attempt != self._expected_attempt(request, record.attempt.attempt_ordinal):
                raise AttemptStoreTransitionError(
                    "replay_rejected", "gateway result contains cross-request attempt identity"
                )
        return checked

    def _expected_attempt(self, request: GatewayRequest, ordinal: int) -> AttemptIdentity:
        initial = request.initial_attempt
        return AttemptIdentity.build(
            manifest=initial.manifest,
            case=initial.case,
            model_policy=initial.model_policy,
            context_sha256=initial.context_sha256,
            prompt_sha256=initial.prompt_sha256,
            response_schema_sha256=initial.response_schema_sha256,
            attempt_ordinal=ordinal,
        )

    def _verify_attempt_inventory(
        self,
        result: GatewayExecutionResult,
        entries: tuple[StoreLedgerEntry, ...],
    ) -> None:
        recorded = tuple(entry for entry in entries if entry.state == "attempt_recorded")
        if len(recorded) != len(result.attempts):
            raise AttemptStoreTransitionError(
                "invalid_transition", "stored attempt count does not match gateway result"
            )
        for entry, record in zip(recorded, result.attempts, strict=True):
            expected_sha = content_sha256(_canonical_bytes(record.model_dump(mode="json")))
            if (
                entry.attempt_id != record.attempt.attempt_id
                or entry.attempt_identity_sha256 != record.attempt.attempt_identity_sha256
                or entry.attempt_record_sha256 != expected_sha
            ):
                raise AttemptStoreTransitionError(
                    "replay_rejected", "stored attempt record differs from gateway result"
                )

    def _require_result_head(
        self,
        request: GatewayRequest,
        expected_state: StoreState,
        result_sha256: str,
    ) -> None:
        entries, terminal = self._load_chain(request.initial_attempt.request_identity_sha256)
        if terminal is not None:
            raise AttemptStoreTransitionError(
                "replay_rejected", "terminal request cannot accept another result"
            )
        latest = entries[-1] if entries else None
        if (
            latest is None
            or latest.state != expected_state
            or latest.result_object_sha256 != result_sha256
        ):
            raise AttemptStoreTransitionError(
                "replay_rejected", "closeout result differs from the recorded result"
            )
