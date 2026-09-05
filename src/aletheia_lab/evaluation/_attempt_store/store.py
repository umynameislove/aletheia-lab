"""Stable immutable attempt-store orchestration over isolated authorities."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from aletheia_lab.evaluation.execution_contracts import AttemptIdentity, canonical_execution_sha256
from aletheia_lab.model_gateway.contracts import (
    AttemptRecord,
    GatewayExecutionResult,
    GatewayRequest,
)
from aletheia_lab.project.identity import content_sha256

from .contracts import (
    FAILURE_RECEIPT_SCHEMA_VERSION,
    STORE_ENTRY_SCHEMA_VERSION,
    AttemptStoreConflictError,
    AttemptStoreIntegrityError,
    AttemptStoreTransitionError,
    StoreClock,
    StoreErrorCode,
    StoreLedgerEntry,
    StoreState,
    StoreWriteReceipt,
    TechnicalFailureReceipt,
    TerminalExecutionInventory,
    WriteDisposition,
    _canonical_bytes,
    _entry_bytes,
    _failure_bytes,
    _result_inventory_bytes,
    validate_request_hash,
)
from .integrity import AttemptStoreIntegrityVerifier
from .reconciliation import AttemptStoreReconciler
from .transitions import validate_transition
from .writer import AttemptStoreWriter


class ImmutableAttemptStore:
    """File-backed immutable ledger with independently verified terminal state."""

    def __init__(self, root: str | Path, *, clock: StoreClock) -> None:
        supplied = Path(root)
        supplied.mkdir(parents=True, exist_ok=True)
        if supplied.is_symlink() or not supplied.is_dir():
            raise AttemptStoreIntegrityError(
                "integrity_error", "store root must be a real directory"
            )
        self.root = supplied.resolve()
        self.clock = clock
        self.object_root = self.root / "objects" / "sha256"
        self.request_root = self.root / "requests"
        self.terminal_root = self.root / "terminal"
        self.failure_root = self.root / "failures"
        for directory in (
            self.object_root,
            self.request_root,
            self.terminal_root,
            self.failure_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)
            if directory.is_symlink() or not directory.is_dir():
                raise AttemptStoreIntegrityError(
                    "integrity_error", "store-owned path must be a real directory"
                )
        self._writer = AttemptStoreWriter(
            object_root=self.object_root,
            request_root=self.request_root,
            terminal_root=self.terminal_root,
        )
        self._verifier = AttemptStoreIntegrityVerifier(
            root=self.root,
            object_root=self.object_root,
            request_root=self.request_root,
            terminal_root=self.terminal_root,
            failure_root=self.failure_root,
        )
        self._reconciler = AttemptStoreReconciler(
            object_root=self.object_root,
            request_root=self.request_root,
            terminal_root=self.terminal_root,
        )
        self.verify_integrity()

    def prepare(self, request: GatewayRequest) -> StoreWriteReceipt:
        checked = self._checked_request(request)
        return self._append(checked, "prepared")

    def start(self, request: GatewayRequest) -> StoreWriteReceipt:
        checked = self._checked_request(request)
        return self._append(checked, "started")

    def record_attempt(
        self,
        request: GatewayRequest,
        record: AttemptRecord,
    ) -> StoreWriteReceipt:
        checked = self._checked_request(request)
        checked_record = AttemptRecord.model_validate(record.model_dump(mode="python"))
        expected = self._expected_attempt(checked, checked_record.attempt.attempt_ordinal)
        if checked_record.attempt != expected:
            raise AttemptStoreTransitionError(
                "replay_rejected", "attempt record does not match immutable request identity"
            )
        record_bytes = _canonical_bytes(checked_record.model_dump(mode="json"))
        record_sha = self._write_object(record_bytes)
        return self._append(
            checked,
            "attempt_recorded",
            attempt=checked_record.attempt,
            attempt_record_sha256=record_sha,
        )

    def record_response(
        self,
        request: GatewayRequest,
        result: GatewayExecutionResult,
    ) -> StoreWriteReceipt:
        checked = self._checked_request(request)
        checked_result = self._checked_result(checked, result)
        if checked_result.raw_response is None:
            raise AttemptStoreTransitionError(
                "invalid_transition", "response_recorded requires a raw response artifact"
            )
        raw = checked_result.raw_response
        entries, _ = self._load_chain(checked.initial_attempt.request_identity_sha256)
        self._verify_attempt_inventory(checked_result, entries)
        raw_sha = self._write_object(raw.content)
        if raw_sha != raw.content_sha256:
            raise AttemptStoreIntegrityError(
                "integrity_error", "raw response storage hash does not match artifact hash"
            )
        return self._append(
            checked,
            "response_recorded",
            raw_response_ref=raw.artifact_ref,
            raw_response_sha256=raw.content_sha256,
        )

    def record_parsed_or_failed(
        self,
        request: GatewayRequest,
        result: GatewayExecutionResult,
    ) -> StoreWriteReceipt:
        checked = self._checked_request(request)
        checked_result = self._checked_result(checked, result)
        entries, _ = self._load_chain(checked.initial_attempt.request_identity_sha256)
        self._verify_attempt_inventory(checked_result, entries)
        latest = entries[-1] if entries else None
        raw = checked_result.raw_response
        if raw is not None:
            if (
                latest is None
                or latest.state != "response_recorded"
                or latest.raw_response_ref != raw.artifact_ref
                or latest.raw_response_sha256 != raw.content_sha256
            ):
                raise AttemptStoreTransitionError(
                    "invalid_transition", "parsed result does not match recorded raw response"
                )
        elif latest is None or latest.state != "attempt_recorded":
            raise AttemptStoreTransitionError(
                "invalid_transition", "provider failure must follow its final attempt record"
            )
        result_sha = self._write_object(_result_inventory_bytes(checked_result))
        parsed_ref: str | None = None
        parsed_sha: str | None = None
        parsed_object_sha: str | None = None
        issue_ref: str | None = None
        issue_sha: str | None = None
        issue_object_sha: str | None = None
        if checked_result.parsed_response is not None:
            parsed = checked_result.parsed_response
            parsed_ref = parsed.artifact_ref
            parsed_sha = parsed.content_sha256
            parsed_object_sha = self._write_object(_canonical_bytes(parsed.payload))
        if checked_result.issue is not None:
            issue = checked_result.issue
            issue_ref = issue.issue_id
            issue_sha = issue.issue_sha256
            issue_object_sha = self._write_object(_canonical_bytes(issue.model_dump(mode="json")))
        return self._append(
            checked,
            "parsed_or_failed",
            parsed_response_ref=parsed_ref,
            parsed_response_sha256=parsed_sha,
            parsed_object_sha256=parsed_object_sha,
            issue_ref=issue_ref,
            issue_sha256=issue_sha,
            issue_object_sha256=issue_object_sha,
            result_object_sha256=result_sha,
        )

    def mark_closeout_pending(
        self,
        request: GatewayRequest,
        result: GatewayExecutionResult,
    ) -> StoreWriteReceipt:
        checked = self._checked_request(request)
        checked_result = self._checked_result(checked, result)
        result_sha = self._write_object(_result_inventory_bytes(checked_result))
        self._require_result_head(checked, "parsed_or_failed", result_sha)
        return self._append(
            checked,
            "closeout_pending",
            result_object_sha256=result_sha,
        )

    def publish_terminal(
        self,
        request: GatewayRequest,
        result: GatewayExecutionResult,
    ) -> StoreWriteReceipt:
        checked = self._checked_request(request)
        checked_result = self._checked_result(checked, result)
        result_sha = self._write_object(_result_inventory_bytes(checked_result))
        self._require_result_head(checked, "closeout_pending", result_sha)
        return self._append(
            checked,
            "terminal_published",
            result_object_sha256=result_sha,
        )

    def current_state(self, request_identity_sha256: str) -> StoreState | None:
        entries, terminal = self._load_chain(request_identity_sha256)
        if terminal is not None:
            return terminal.state
        return entries[-1].state if entries else None

    def is_terminal(self, request_identity_sha256: str) -> bool:
        _, terminal = self._load_chain(request_identity_sha256)
        return terminal is not None

    def list_terminal_requests(self) -> tuple[str, ...]:
        self.verify_integrity()
        return tuple(sorted(path.stem for path in self.terminal_root.glob("*.json")))

    def terminal_inventories(self) -> tuple[TerminalExecutionInventory, ...]:
        """Return canonical sealed inventories without exposing partial ledgers."""

        self.verify_integrity()
        request_hashes = tuple(sorted(path.stem for path in self.terminal_root.glob("*.json")))
        return tuple(self._terminal_inventory(request_hash) for request_hash in request_hashes)

    def terminal_parsed_payload(self, request_identity_sha256: str) -> dict[str, object] | None:
        """Read a verified parsed terminal payload without exposing raw bytes."""

        return self._verifier.terminal_parsed_payload(request_identity_sha256)

    def record_failure(
        self,
        request: GatewayRequest,
        *,
        stage: str,
        exception_class: str,
        error_code: StoreErrorCode,
        private_message: str,
        partial_publication: bool,
        attempt: AttemptIdentity | None = None,
    ) -> TechnicalFailureReceipt:
        checked = self._checked_request(request)
        if attempt is not None:
            checked_attempt = AttemptIdentity.model_validate(attempt.model_dump(mode="python"))
            expected = self._expected_attempt(checked, checked_attempt.attempt_ordinal)
            if checked_attempt != expected:
                raise AttemptStoreTransitionError(
                    "replay_rejected", "failure receipt attempt does not match request"
                )
        else:
            checked_attempt = None
        message_sha = hashlib.sha256(private_message.encode("utf-8", errors="strict")).hexdigest()
        public_message = f"error: {error_code} at {stage}"
        store_sha = self.store_sha256()
        recorded_at_ns = self.clock.now_ns()
        payload = {
            "schema_version": FAILURE_RECEIPT_SCHEMA_VERSION,
            "stage": stage,
            "exception_class": exception_class,
            "error_code": error_code,
            "public_message": public_message,
            "message_sha256": message_sha,
            "request_identity_sha256": checked.initial_attempt.request_identity_sha256,
            "manifest_reference_id": checked.initial_attempt.manifest.reference_id,
            "manifest_content_sha256": checked.initial_attempt.manifest.manifest_content_sha256,
            "attempt_id": checked_attempt.attempt_id if checked_attempt is not None else None,
            "attempt_identity_sha256": (
                checked_attempt.attempt_identity_sha256 if checked_attempt is not None else None
            ),
            "partial_publication": partial_publication,
            "retry_policy_ref": checked.runtime_policy.retry_policy_ref,
            "store_sha256": store_sha,
            "recorded_at_ns": recorded_at_ns,
        }
        receipt_sha = canonical_execution_sha256(payload)
        receipt = TechnicalFailureReceipt(
            receipt_id=f"ev-{receipt_sha}",
            receipt_sha256=receipt_sha,
            stage=stage,
            exception_class=exception_class,
            error_code=error_code,
            public_message=public_message,
            message_sha256=message_sha,
            request_identity_sha256=checked.initial_attempt.request_identity_sha256,
            manifest_reference_id=checked.initial_attempt.manifest.reference_id,
            manifest_content_sha256=checked.initial_attempt.manifest.manifest_content_sha256,
            attempt_id=checked_attempt.attempt_id if checked_attempt is not None else None,
            attempt_identity_sha256=(
                checked_attempt.attempt_identity_sha256 if checked_attempt is not None else None
            ),
            partial_publication=partial_publication,
            retry_policy_ref=checked.runtime_policy.retry_policy_ref,
            store_sha256=store_sha,
            recorded_at_ns=recorded_at_ns,
        )
        destination = self.failure_root / f"{receipt.receipt_sha256}.json"
        self._atomic_create(destination, _failure_bytes(receipt))
        return receipt

    def _append(
        self,
        request: GatewayRequest,
        state: StoreState,
        *,
        attempt: AttemptIdentity | None = None,
        attempt_record_sha256: str | None = None,
        raw_response_ref: str | None = None,
        raw_response_sha256: str | None = None,
        parsed_response_ref: str | None = None,
        parsed_response_sha256: str | None = None,
        parsed_object_sha256: str | None = None,
        issue_ref: str | None = None,
        issue_sha256: str | None = None,
        issue_object_sha256: str | None = None,
        result_object_sha256: str | None = None,
    ) -> StoreWriteReceipt:
        request_hash = request.initial_attempt.request_identity_sha256
        entries, terminal = self._load_chain(request_hash)
        event_fields = self._entry_fields(
            request,
            state,
            attempt=attempt,
            attempt_record_sha256=attempt_record_sha256,
            raw_response_ref=raw_response_ref,
            raw_response_sha256=raw_response_sha256,
            parsed_response_ref=parsed_response_ref,
            parsed_response_sha256=parsed_response_sha256,
            parsed_object_sha256=parsed_object_sha256,
            issue_ref=issue_ref,
            issue_sha256=issue_sha256,
            issue_object_sha256=issue_object_sha256,
            result_object_sha256=result_object_sha256,
        )
        event_sha = canonical_execution_sha256(event_fields)
        for existing in (*entries, *((terminal,) if terminal is not None else ())):
            if existing.event_sha256 == event_sha:
                return self._receipt(existing, "idempotent")
        if terminal is not None:
            raise AttemptStoreTransitionError(
                "replay_rejected", "terminal request ledger cannot accept new events"
            )
        validate_transition(entries, state, attempt)
        sequence = len(entries) + 1
        previous_sha = content_sha256(_entry_bytes(entries[-1])) if entries else None
        entry = self._build_entry(
            event_fields,
            event_sha256=event_sha,
            sequence=sequence,
            previous_entry_sha256=previous_sha,
            recorded_at_ns=self.clock.now_ns(),
        )
        destination = (
            self.terminal_root / f"{request_hash}.json"
            if state == "terminal_published"
            else self._ledger_root(request_hash) / f"{sequence:08d}.json"
        )
        try:
            write_disposition = self._atomic_create(destination, _entry_bytes(entry))
        except AttemptStoreConflictError:
            reloaded, reloaded_terminal = self._load_chain(request_hash)
            for existing in (
                *reloaded,
                *((reloaded_terminal,) if reloaded_terminal is not None else ()),
            ):
                if existing.event_sha256 == event_sha:
                    return self._receipt(existing, "idempotent")
            raise
        if write_disposition == "identical":
            reloaded, reloaded_terminal = self._load_chain(request_hash)
            for existing in (
                *reloaded,
                *((reloaded_terminal,) if reloaded_terminal is not None else ()),
            ):
                if existing.event_sha256 == event_sha:
                    return self._receipt(existing, "idempotent")
            raise AttemptStoreIntegrityError(
                "integrity_error",
                "identical immutable bytes are absent from the reconciled ledger",
            )
        return self._receipt(entry, "created")

    def _entry_fields(
        self,
        request: GatewayRequest,
        state: StoreState,
        **event_fields: object,
    ) -> dict[str, object]:
        attempt = request.initial_attempt
        selected_attempt = event_fields.pop("attempt", None)
        if selected_attempt is not None and not isinstance(selected_attempt, AttemptIdentity):
            raise TypeError("store attempt event requires AttemptIdentity")
        return {
            "schema_version": STORE_ENTRY_SCHEMA_VERSION,
            "state": state,
            "request_identity_sha256": attempt.request_identity_sha256,
            "manifest_reference_id": attempt.manifest.reference_id,
            "manifest_content_sha256": attempt.manifest.manifest_content_sha256,
            "case_reference_id": attempt.case.reference_id,
            "case_content_sha256": attempt.case.case_content_sha256,
            "family_id": attempt.case.family_id,
            "variant_content_sha256": attempt.case.variant_content_sha256,
            "snapshot_id": attempt.case.snapshot_id,
            "evidence_content_sha256": attempt.case.evidence_content_sha256,
            "visibility_projection_sha256": attempt.case.visibility_projection_sha256,
            "context_sha256": attempt.context_sha256,
            "prompt_sha256": attempt.prompt_sha256,
            "response_schema_sha256": attempt.response_schema_sha256,
            "model_policy_sha256": attempt.model_policy.policy_content_sha256,
            "provider_ref": attempt.model_policy.provider_ref,
            "model_ref": attempt.model_policy.model_ref,
            "model_version_ref": attempt.model_policy.model_version_ref,
            "resource_policy_ref": attempt.model_policy.resource_policy_ref,
            "retry_policy_ref": request.runtime_policy.retry_policy_ref,
            "attempt_id": selected_attempt.attempt_id if selected_attempt is not None else None,
            "attempt_identity_sha256": (
                selected_attempt.attempt_identity_sha256 if selected_attempt is not None else None
            ),
            "attempt_ordinal": (
                selected_attempt.attempt_ordinal if selected_attempt is not None else None
            ),
            "partial_publication": False,
            **event_fields,
        }

    def _build_entry(
        self,
        event_fields: dict[str, object],
        *,
        event_sha256: str,
        sequence: int,
        previous_entry_sha256: str | None,
        recorded_at_ns: int,
    ) -> StoreLedgerEntry:
        return StoreLedgerEntry.model_validate(
            {
                "entry_id": f"ev-{event_sha256}",
                "event_sha256": event_sha256,
                "sequence": sequence,
                "previous_entry_sha256": previous_entry_sha256,
                "recorded_at_ns": recorded_at_ns,
                **event_fields,
            }
        )

    def _receipt(
        self,
        entry: StoreLedgerEntry,
        disposition: WriteDisposition,
    ) -> StoreWriteReceipt:
        return StoreWriteReceipt(
            disposition=disposition,
            state=entry.state,
            request_identity_sha256=entry.request_identity_sha256,
            event_sha256=entry.event_sha256,
            entry_id=entry.entry_id,
            entry_serialized_sha256=content_sha256(_entry_bytes(entry)),
            sequence=entry.sequence,
            counted_attempt=disposition == "created" and entry.state == "attempt_recorded",
            partial_publication=False,
            store_sha256=self.store_sha256(),
            recorded_at_ns=entry.recorded_at_ns,
        )

    def store_sha256(self) -> str:
        return self._verifier.store_sha256()

    def verify_integrity(self) -> None:
        self._verifier.verify_integrity()

    def _checked_request(self, request: GatewayRequest) -> GatewayRequest:
        self.verify_integrity()
        return self._reconciler._checked_request(request)

    def _checked_result(
        self, request: GatewayRequest, result: GatewayExecutionResult
    ) -> GatewayExecutionResult:
        return self._reconciler._checked_result(request, result)

    def _expected_attempt(self, request: GatewayRequest, ordinal: int) -> AttemptIdentity:
        return self._reconciler._expected_attempt(request, ordinal)

    def _verify_attempt_inventory(
        self, result: GatewayExecutionResult, entries: tuple[StoreLedgerEntry, ...]
    ) -> None:
        self._reconciler._verify_attempt_inventory(result, entries)

    def _require_result_head(
        self, request: GatewayRequest, expected_state: StoreState, result_sha256: str
    ) -> None:
        self._reconciler._require_result_head(request, expected_state, result_sha256)

    def _load_chain(
        self, request_hash: str, *, verify_terminal: bool = True
    ) -> tuple[tuple[StoreLedgerEntry, ...], StoreLedgerEntry | None]:
        return self._verifier._load_chain(request_hash, verify_terminal=verify_terminal)

    def _terminal_inventory(self, request_hash: str) -> TerminalExecutionInventory:
        return self._verifier._terminal_inventory(request_hash)

    def _ledger_root(self, request_hash: str) -> Path:
        return self._writer._ledger_root(request_hash)

    def _write_object(self, payload: bytes) -> str:
        return self._writer._write_object(payload)

    def _atomic_create(self, destination: Path, payload: bytes) -> Literal["created", "identical"]:
        return self._writer._atomic_create(destination, payload)

    @staticmethod
    def _validate_request_hash(value: str) -> None:
        validate_request_hash(value)
