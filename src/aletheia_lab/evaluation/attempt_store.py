"""Immutable content-addressed attempt ledger with atomic terminal publication."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Annotated, Final, Literal, Protocol, Self, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aletheia_lab.evaluation.execution_contracts import (
    AttemptIdentity,
    TechnicalIssue,
    canonical_execution_sha256,
)
from aletheia_lab.model_gateway.contracts import (
    AttemptRecord,
    GatewayExecutionResult,
    GatewayRequest,
)
from aletheia_lab.project.identity import (
    SHA256_PATTERN,
    SNAPSHOT_ID_PATTERN,
    canonical_project_json,
    content_sha256,
)

STORE_ENTRY_SCHEMA_VERSION: Final[Literal["evaluation-store-entry/v1"]] = (
    "evaluation-store-entry/v1"
)
STORE_RECEIPT_SCHEMA_VERSION: Final[Literal["evaluation-store-receipt/v1"]] = (
    "evaluation-store-receipt/v1"
)
FAILURE_RECEIPT_SCHEMA_VERSION: Final[Literal["evaluation-store-failure/v1"]] = (
    "evaluation-store-failure/v1"
)

_OPAQUE_PATTERN: Final[str] = r"^ev-[0-9a-f]{64}$"
_CODE_PATTERN: Final[str] = r"^[a-z][a-z0-9_.-]{0,63}$"
_REQUEST_DIRECTORY = re.compile(r"^[0-9a-f]{64}$")
_LEDGER_FILE = re.compile(r"^(?P<sequence>[0-9]{8})\.json$")
_OBJECT_BUCKET = re.compile(r"^[0-9a-f]{2}$")
_OBJECT_NAME = re.compile(r"^[0-9a-f]{62}$")

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
OpaqueReference = Annotated[str, Field(pattern=_OPAQUE_PATTERN)]
TechnicalCode = Annotated[str, Field(pattern=_CODE_PATTERN)]
SnapshotId = Annotated[str, Field(pattern=SNAPSHOT_ID_PATTERN)]

StoreState = Literal[
    "prepared",
    "started",
    "attempt_recorded",
    "response_recorded",
    "parsed_or_failed",
    "closeout_pending",
    "terminal_published",
]
StoreErrorCode = Literal[
    "conflict",
    "corrupt_artifact",
    "integrity_error",
    "invalid_transition",
    "io_error",
    "replay_rejected",
]
WriteDisposition = Literal["created", "idempotent"]

_ALLOWED_PREVIOUS: Final[dict[StoreState, frozenset[StoreState | None]]] = {
    "prepared": frozenset({None}),
    "started": frozenset({"prepared"}),
    "attempt_recorded": frozenset({"started", "attempt_recorded"}),
    "response_recorded": frozenset({"attempt_recorded"}),
    "parsed_or_failed": frozenset({"attempt_recorded", "response_recorded"}),
    "closeout_pending": frozenset({"parsed_or_failed"}),
    "terminal_published": frozenset({"closeout_pending"}),
}


class StoreClock(Protocol):
    """Injectable store timestamp source."""

    def now_ns(self) -> int: ...


class AttemptStoreError(ValueError):
    """Base fail-closed error with a stable public-safe code."""

    def __init__(self, code: StoreErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class AttemptStoreConflictError(AttemptStoreError):
    """Raised when immutable bytes or one ledger sequence conflict."""


class AttemptStoreIntegrityError(AttemptStoreError):
    """Raised when persisted membership, hashes, or links do not reconcile."""


class AttemptStoreTransitionError(AttemptStoreError):
    """Raised when a caller attempts an invalid or replayed transition."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class StoreLedgerEntry(_StrictFrozenModel):
    """One immutable event in a content-linked request ledger."""

    schema_version: Literal["evaluation-store-entry/v1"] = STORE_ENTRY_SCHEMA_VERSION
    entry_id: OpaqueReference
    event_sha256: Sha256
    sequence: int = Field(gt=0)
    previous_entry_sha256: Sha256 | None
    state: StoreState
    recorded_at_ns: int = Field(ge=0)
    request_identity_sha256: Sha256
    manifest_reference_id: OpaqueReference
    manifest_content_sha256: Sha256
    case_reference_id: OpaqueReference
    case_content_sha256: Sha256
    family_id: OpaqueReference
    variant_content_sha256: Sha256
    snapshot_id: SnapshotId
    evidence_content_sha256: Sha256
    visibility_projection_sha256: Sha256
    context_sha256: Sha256
    prompt_sha256: Sha256
    response_schema_sha256: Sha256
    model_policy_sha256: Sha256
    provider_ref: OpaqueReference
    model_ref: OpaqueReference
    model_version_ref: OpaqueReference
    resource_policy_ref: OpaqueReference
    retry_policy_ref: OpaqueReference
    attempt_id: OpaqueReference | None
    attempt_identity_sha256: Sha256 | None
    attempt_ordinal: int | None = Field(default=None, gt=0)
    attempt_record_sha256: Sha256 | None
    raw_response_ref: OpaqueReference | None
    raw_response_sha256: Sha256 | None
    parsed_response_ref: OpaqueReference | None
    parsed_response_sha256: Sha256 | None
    parsed_object_sha256: Sha256 | None
    issue_ref: OpaqueReference | None
    issue_sha256: Sha256 | None
    issue_object_sha256: Sha256 | None
    result_object_sha256: Sha256 | None
    partial_publication: bool

    def event_payload(self) -> dict[str, object]:
        payload = self.model_dump(
            mode="json",
            exclude={
                "entry_id",
                "event_sha256",
                "sequence",
                "previous_entry_sha256",
                "recorded_at_ns",
            },
        )
        return {str(key): value for key, value in payload.items()}

    @model_validator(mode="after")
    def _identity_and_state_shape_reconcile(self) -> Self:
        expected_event = canonical_execution_sha256(self.event_payload())
        if self.event_sha256 != expected_event or self.entry_id != f"ev-{expected_event}":
            raise ValueError("store event identity does not match canonical event fields")
        attempt_values = (
            self.attempt_id,
            self.attempt_identity_sha256,
            self.attempt_ordinal,
            self.attempt_record_sha256,
        )
        if self.state == "attempt_recorded" and any(value is None for value in attempt_values):
            raise ValueError("attempt_recorded entry requires complete attempt linkage")
        if self.state != "attempt_recorded" and any(value is not None for value in attempt_values):
            raise ValueError("attempt linkage is only valid for attempt_recorded entries")
        if (self.raw_response_ref is None) != (self.raw_response_sha256 is None):
            raise ValueError("raw response reference and hash must be present together")
        if self.raw_response_ref is not None and (
            self.raw_response_ref != f"ev-{self.raw_response_sha256}"
            or self.state != "response_recorded"
        ):
            raise ValueError("raw response linkage is invalid for this store state")
        parsed_values = (
            self.parsed_response_ref,
            self.parsed_response_sha256,
            self.parsed_object_sha256,
        )
        if any(value is None for value in parsed_values) and any(
            value is not None for value in parsed_values
        ):
            raise ValueError("parsed response linkage must be complete or absent")
        if self.parsed_response_ref is not None and (
            self.parsed_response_ref != f"ev-{self.parsed_response_sha256}"
            or self.state != "parsed_or_failed"
        ):
            raise ValueError("parsed response linkage is invalid for this store state")
        issue_values = (self.issue_ref, self.issue_sha256, self.issue_object_sha256)
        if any(value is None for value in issue_values) and any(
            value is not None for value in issue_values
        ):
            raise ValueError("technical issue linkage must be complete or absent")
        if self.issue_ref is not None and (
            self.issue_ref != f"ev-{self.issue_sha256}"
            or self.state != "parsed_or_failed"
        ):
            raise ValueError("technical issue linkage is invalid for this store state")
        if self.state == "parsed_or_failed":
            if self.result_object_sha256 is None or (
                (self.parsed_response_ref is None) == (self.issue_ref is None)
            ):
                raise ValueError("parsed_or_failed entry requires result and outcome linkage")
        elif self.state in {"closeout_pending", "terminal_published"}:
            if self.result_object_sha256 is None:
                raise ValueError("closeout and terminal entries require result linkage")
        elif self.result_object_sha256 is not None:
            raise ValueError("result linkage is invalid before parsed_or_failed state")
        if self.partial_publication:
            raise ValueError("ledger entries must never represent partial publication")
        return self


class StoreWriteReceipt(_StrictFrozenModel):
    """Explicit created or idempotent/no-op persistence receipt."""

    schema_version: Literal["evaluation-store-receipt/v1"] = STORE_RECEIPT_SCHEMA_VERSION
    disposition: WriteDisposition
    state: StoreState
    request_identity_sha256: Sha256
    event_sha256: Sha256
    entry_id: OpaqueReference
    entry_serialized_sha256: Sha256
    sequence: int = Field(gt=0)
    counted_attempt: bool
    partial_publication: bool
    store_sha256: Sha256
    recorded_at_ns: int = Field(ge=0)

    @model_validator(mode="after")
    def _receipt_shape_reconciles(self) -> Self:
        if self.counted_attempt != (
            self.disposition == "created" and self.state == "attempt_recorded"
        ):
            raise ValueError("receipt counted_attempt does not match write disposition")
        if self.partial_publication:
            raise ValueError("successful store writes cannot report partial publication")
        return self


class TechnicalFailureReceipt(_StrictFrozenModel):
    """Public-safe technical store failure without scientific disposition."""

    schema_version: Literal["evaluation-store-failure/v1"] = FAILURE_RECEIPT_SCHEMA_VERSION
    receipt_id: OpaqueReference
    receipt_sha256: Sha256
    stage: TechnicalCode
    exception_class: TechnicalCode
    error_code: StoreErrorCode
    public_message: str
    message_sha256: Sha256
    request_identity_sha256: Sha256
    manifest_reference_id: OpaqueReference
    manifest_content_sha256: Sha256
    attempt_id: OpaqueReference | None
    attempt_identity_sha256: Sha256 | None
    partial_publication: bool
    retry_policy_ref: OpaqueReference
    store_sha256: Sha256
    recorded_at_ns: int = Field(ge=0)

    def identity_payload(self) -> dict[str, object]:
        payload = self.model_dump(mode="json", exclude={"receipt_id", "receipt_sha256"})
        return {str(key): value for key, value in payload.items()}

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        if (self.attempt_id is None) != (self.attempt_identity_sha256 is None):
            raise ValueError("failure receipt attempt linkage must be complete or absent")
        expected = canonical_execution_sha256(self.identity_payload())
        if self.public_message != f"error: {self.error_code} at {self.stage}":
            raise ValueError("failure receipt public message is not derived from stable metadata")
        if self.receipt_sha256 != expected or self.receipt_id != f"ev-{expected}":
            raise ValueError("failure receipt identity does not match canonical fields")
        return self


def _canonical_bytes(payload: object) -> bytes:
    return (canonical_project_json(payload) + "\n").encode("utf-8")


def _entry_bytes(entry: StoreLedgerEntry) -> bytes:
    return _canonical_bytes(entry.model_dump(mode="json"))


def _failure_bytes(receipt: TechnicalFailureReceipt) -> bytes:
    return _canonical_bytes(receipt.model_dump(mode="json"))


class ImmutableAttemptStore:
    """File-backed immutable ledger; only terminal index files are public terminal state."""

    def __init__(self, root: str | Path, *, clock: StoreClock) -> None:
        supplied = Path(root)
        supplied.mkdir(parents=True, exist_ok=True)
        if supplied.is_symlink() or not supplied.is_dir():
            raise AttemptStoreIntegrityError("integrity_error", "store root must be a real directory")
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
            issue_object_sha = self._write_object(
                _canonical_bytes(issue.model_dump(mode="json"))
            )
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

    def store_sha256(self) -> str:
        self.verify_integrity()
        inventory: list[dict[str, str]] = []
        for path in sorted(self.root.rglob("*"), key=lambda item: item.as_posix()):
            if not path.is_file() or path.name.endswith(".stage"):
                continue
            relative = path.relative_to(self.root).as_posix()
            inventory.append({"path": relative, "sha256": content_sha256(path.read_bytes())})
        return canonical_execution_sha256(inventory)

    def verify_integrity(self) -> None:
        allowed_root = {"objects", "requests", "terminal", "failures"}
        for child in self.root.iterdir():
            if child.name not in allowed_root:
                raise AttemptStoreIntegrityError(
                    "integrity_error", "store root contains unexpected membership"
                )
        object_members = tuple((self.root / "objects").iterdir())
        if (
            len(object_members) != 1
            or object_members[0].name != "sha256"
            or object_members[0].is_symlink()
            or not object_members[0].is_dir()
        ):
            raise AttemptStoreIntegrityError(
                "integrity_error", "object directory membership does not reconcile"
            )
        self._verify_objects()
        request_hashes: set[str] = set()
        for request_dir in self.request_root.iterdir():
            if request_dir.is_symlink() or not request_dir.is_dir():
                raise AttemptStoreIntegrityError(
                    "integrity_error", "request store member must be a real directory"
                )
            if _REQUEST_DIRECTORY.fullmatch(request_dir.name) is None:
                raise AttemptStoreIntegrityError(
                    "integrity_error", "request directory name is not canonical lowercase SHA-256"
                )
            members = tuple(request_dir.iterdir())
            if len(members) != 1 or members[0].name != "ledger" or not members[0].is_dir():
                raise AttemptStoreIntegrityError(
                    "integrity_error", "request directory membership does not reconcile"
                )
            request_hashes.add(request_dir.name)
            self._load_chain(request_dir.name, verify_terminal=False)
        terminal_hashes: set[str] = set()
        for terminal in self.terminal_root.iterdir():
            if terminal.name.endswith(".stage"):
                continue
            if terminal.is_symlink() or not terminal.is_file() or terminal.suffix != ".json":
                raise AttemptStoreIntegrityError(
                    "integrity_error", "terminal index contains an invalid member"
                )
            request_hash = terminal.stem
            if _REQUEST_DIRECTORY.fullmatch(request_hash) is None:
                raise AttemptStoreIntegrityError(
                    "integrity_error", "terminal request name is not canonical"
                )
            terminal_hashes.add(request_hash)
            self._load_chain(request_hash, verify_terminal=True)
        if not terminal_hashes <= request_hashes:
            raise AttemptStoreIntegrityError(
                "integrity_error", "terminal index references an unknown request ledger"
            )
        for failure in self.failure_root.iterdir():
            if failure.name.endswith(".stage"):
                continue
            if failure.is_symlink() or not failure.is_file() or failure.suffix != ".json":
                raise AttemptStoreIntegrityError(
                    "integrity_error", "failure store contains an invalid member"
                )
            try:
                payload = failure.read_bytes()
                receipt = TechnicalFailureReceipt.model_validate_json(payload)
            except (OSError, ValidationError) as exc:
                raise AttemptStoreIntegrityError(
                    "corrupt_artifact", "failure receipt is invalid"
                ) from exc
            if failure.stem != receipt.receipt_sha256:
                raise AttemptStoreIntegrityError(
                    "integrity_error", "failure receipt filename does not match content"
                )
            if payload != _failure_bytes(receipt):
                raise AttemptStoreIntegrityError(
                    "integrity_error", "failure receipt serialization is not canonical"
                )

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
        self._validate_transition(entries, state, attempt)
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
                selected_attempt.attempt_identity_sha256
                if selected_attempt is not None
                else None
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

    def _validate_transition(
        self,
        entries: tuple[StoreLedgerEntry, ...],
        state: StoreState,
        attempt: AttemptIdentity | None,
    ) -> None:
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
            expected_ordinal = 1 + sum(
                entry.state == "attempt_recorded" for entry in entries
            )
            if attempt.attempt_ordinal != expected_ordinal:
                raise AttemptStoreTransitionError(
                    "replay_rejected", "attempt ordinal is duplicate, missing, or replayed"
                )

    @staticmethod
    def _verify_stored_transition(
        entries: tuple[StoreLedgerEntry, ...],
        entry: StoreLedgerEntry,
    ) -> None:
        previous = entries[-1].state if entries else None
        if previous not in _ALLOWED_PREVIOUS[entry.state]:
            raise AttemptStoreIntegrityError(
                "integrity_error", "persisted store transition is invalid"
            )
        if entry.state == "attempt_recorded":
            expected_ordinal = 1 + sum(
                existing.state == "attempt_recorded" for existing in entries
            )
            if entry.attempt_ordinal != expected_ordinal:
                raise AttemptStoreIntegrityError(
                    "integrity_error", "persisted attempt ordinal is not contiguous"
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

    def _checked_request(self, request: GatewayRequest) -> GatewayRequest:
        self.verify_integrity()
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
            expected_sha = content_sha256(
                _canonical_bytes(record.model_dump(mode="json"))
            )
            if (
                entry.attempt_id != record.attempt.attempt_id
                or entry.attempt_identity_sha256
                != record.attempt.attempt_identity_sha256
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

    def _ledger_root(self, request_hash: str) -> Path:
        self._validate_request_hash(request_hash)
        request_dir = self.request_root / request_hash
        ledger = request_dir / "ledger"
        ledger.mkdir(parents=True, exist_ok=True)
        if request_dir.is_symlink() or ledger.is_symlink() or not ledger.is_dir():
            raise AttemptStoreIntegrityError(
                "integrity_error", "request ledger path must be a real directory"
            )
        return ledger

    def _load_chain(
        self,
        request_hash: str,
        *,
        verify_terminal: bool = True,
    ) -> tuple[tuple[StoreLedgerEntry, ...], StoreLedgerEntry | None]:
        self._validate_request_hash(request_hash)
        request_dir = self.request_root / request_hash
        if not request_dir.exists():
            terminal_path = self.terminal_root / f"{request_hash}.json"
            if terminal_path.exists():
                raise AttemptStoreIntegrityError(
                    "integrity_error", "terminal index has no request ledger"
                )
            return (), None
        ledger = request_dir / "ledger"
        if request_dir.is_symlink() or ledger.is_symlink() or not ledger.is_dir():
            raise AttemptStoreIntegrityError(
                "integrity_error", "request ledger path is invalid"
            )
        members = tuple(request_dir.iterdir())
        if len(members) != 1 or members[0].name != "ledger":
            raise AttemptStoreIntegrityError(
                "integrity_error", "request directory membership does not reconcile"
            )
        entries: list[StoreLedgerEntry] = []
        for path in sorted(ledger.iterdir(), key=lambda item: item.name):
            if path.name.endswith(".stage"):
                continue
            match = _LEDGER_FILE.fullmatch(path.name)
            if path.is_symlink() or not path.is_file() or match is None:
                raise AttemptStoreIntegrityError(
                    "integrity_error", "ledger directory contains unexpected membership"
                )
            sequence = int(match.group("sequence"))
            if sequence != len(entries) + 1:
                raise AttemptStoreIntegrityError(
                    "integrity_error", "ledger sequence is missing or non-contiguous"
                )
            entry = self._read_entry(path)
            expected_previous = content_sha256(_entry_bytes(entries[-1])) if entries else None
            if (
                entry.sequence != sequence
                or entry.request_identity_sha256 != request_hash
                or entry.previous_entry_sha256 != expected_previous
                or entry.state == "terminal_published"
                or (
                    entries
                    and self._execution_linkage(entry)
                    != self._execution_linkage(entries[0])
                )
            ):
                raise AttemptStoreIntegrityError(
                    "integrity_error", "ledger sequence or content link does not reconcile"
                )
            self._verify_stored_transition(tuple(entries), entry)
            self._verify_entry_objects(entry)
            entries.append(entry)
        terminal_path = self.terminal_root / f"{request_hash}.json"
        terminal: StoreLedgerEntry | None = None
        if verify_terminal and terminal_path.exists():
            terminal = self._read_entry(terminal_path)
            expected_previous = content_sha256(_entry_bytes(entries[-1])) if entries else None
            if (
                terminal.state != "terminal_published"
                or terminal.sequence != len(entries) + 1
                or terminal.request_identity_sha256 != request_hash
                or terminal.previous_entry_sha256 != expected_previous
                or (
                    entries
                    and self._execution_linkage(terminal)
                    != self._execution_linkage(entries[0])
                )
            ):
                raise AttemptStoreIntegrityError(
                    "integrity_error", "terminal index does not extend the request ledger"
                )
            self._verify_stored_transition(tuple(entries), terminal)
            self._verify_entry_objects(terminal)
        self._verify_result_links(tuple(entries), terminal)
        return tuple(entries), terminal

    def _read_entry(self, path: Path) -> StoreLedgerEntry:
        try:
            payload = path.read_bytes()
            entry = StoreLedgerEntry.model_validate_json(payload)
        except (OSError, ValidationError) as exc:
            raise AttemptStoreIntegrityError(
                "corrupt_artifact", "ledger entry is truncated or invalid"
            ) from exc
        if payload != _entry_bytes(entry):
            raise AttemptStoreIntegrityError(
                "integrity_error", "ledger entry serialization is not canonical"
            )
        return entry

    def _write_object(self, payload: bytes) -> str:
        digest = content_sha256(payload)
        destination = self.object_root / digest[:2] / digest[2:]
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.parent.is_symlink() or not destination.parent.is_dir():
            raise AttemptStoreIntegrityError(
                "integrity_error", "object bucket must be a real directory"
            )
        self._atomic_create(destination, payload)
        return digest

    def _verify_entry_objects(self, entry: StoreLedgerEntry) -> None:
        if entry.attempt_record_sha256 is not None:
            payload = self._read_object(entry.attempt_record_sha256)
            try:
                record = AttemptRecord.model_validate_json(payload)
            except ValidationError as exc:
                raise AttemptStoreIntegrityError(
                    "corrupt_artifact", "attempt record object is invalid"
                ) from exc
            if (
                payload != _canonical_bytes(record.model_dump(mode="json"))
                or record.attempt.attempt_id != entry.attempt_id
                or record.attempt.attempt_identity_sha256
                != entry.attempt_identity_sha256
                or record.attempt.attempt_ordinal != entry.attempt_ordinal
            ):
                raise AttemptStoreIntegrityError(
                    "integrity_error", "attempt record object linkage does not reconcile"
                )
        if entry.raw_response_sha256 is not None:
            self._read_object(entry.raw_response_sha256)
        if entry.parsed_object_sha256 is not None:
            payload = self._read_object(entry.parsed_object_sha256)
            parsed = self._read_canonical_mapping(payload, label="parsed response")
            if canonical_execution_sha256(parsed) != entry.parsed_response_sha256:
                raise AttemptStoreIntegrityError(
                    "integrity_error", "parsed response object linkage does not reconcile"
                )
        if entry.issue_object_sha256 is not None:
            payload = self._read_object(entry.issue_object_sha256)
            try:
                issue = TechnicalIssue.model_validate_json(payload)
            except ValidationError as exc:
                raise AttemptStoreIntegrityError(
                    "corrupt_artifact", "technical issue object is invalid"
                ) from exc
            if (
                payload != _canonical_bytes(issue.model_dump(mode="json"))
                or issue.issue_id != entry.issue_ref
                or issue.issue_sha256 != entry.issue_sha256
            ):
                raise AttemptStoreIntegrityError(
                    "integrity_error", "technical issue object linkage does not reconcile"
                )
        if entry.result_object_sha256 is not None:
            self._read_result_inventory(entry.result_object_sha256)

    def _verify_result_links(
        self,
        entries: tuple[StoreLedgerEntry, ...],
        terminal: StoreLedgerEntry | None,
    ) -> None:
        outcome = next(
            (entry for entry in entries if entry.state == "parsed_or_failed"),
            None,
        )
        if outcome is None:
            return
        if outcome.result_object_sha256 is None:
            raise AttemptStoreIntegrityError(
                "integrity_error", "parsed outcome has no result inventory"
            )
        inventory = self._read_result_inventory(outcome.result_object_sha256)
        attempts = [
            entry.attempt_record_sha256
            for entry in entries
            if entry.state == "attempt_recorded"
        ]
        response = next(
            (entry for entry in entries if entry.state == "response_recorded"),
            None,
        )
        expected_status = inventory["status"]
        if (
            inventory["request_identity_sha256"] != outcome.request_identity_sha256
            or inventory["attempt_record_sha256"] != attempts
            or inventory["raw_response_ref"]
            != (response.raw_response_ref if response is not None else None)
            or inventory["raw_response_sha256"]
            != (response.raw_response_sha256 if response is not None else None)
            or inventory["parsed_response_ref"] != outcome.parsed_response_ref
            or inventory["parsed_response_sha256"] != outcome.parsed_response_sha256
            or inventory["issue_ref"] != outcome.issue_ref
            or inventory["issue_sha256"] != outcome.issue_sha256
            or (outcome.parsed_response_ref is not None and expected_status != "parsed")
            or (outcome.issue_ref is not None and expected_status == "parsed")
        ):
            raise AttemptStoreIntegrityError(
                "integrity_error", "stored result inventory linkage does not reconcile"
            )
        for entry in (*entries, *((terminal,) if terminal is not None else ())):
            if entry.state in {"closeout_pending", "terminal_published"} and (
                entry.result_object_sha256 != outcome.result_object_sha256
            ):
                raise AttemptStoreIntegrityError(
                    "integrity_error", "closeout result linkage does not reconcile"
                )

    def _read_result_inventory(self, digest: str) -> dict[str, object]:
        payload = self._read_object(digest)
        inventory = self._read_canonical_mapping(payload, label="stored result")
        expected_keys = {
            "schema_version",
            "request_identity_sha256",
            "status",
            "attempt_record_sha256",
            "raw_response_ref",
            "raw_response_sha256",
            "parsed_response_ref",
            "parsed_response_sha256",
            "issue_ref",
            "issue_sha256",
        }
        statuses = {
            "parsed",
            "provider_failed",
            "timed_out",
            "retry_exhausted",
            "cancelled",
            "identity_rejected",
            "oversized_response",
            "parse_failed",
        }
        attempts = inventory.get("attempt_record_sha256")
        request_identity = inventory.get("request_identity_sha256")
        status = inventory.get("status")
        nullable_refs = (
            inventory.get("raw_response_ref"),
            inventory.get("parsed_response_ref"),
            inventory.get("issue_ref"),
        )
        nullable_hashes = (
            inventory.get("raw_response_sha256"),
            inventory.get("parsed_response_sha256"),
            inventory.get("issue_sha256"),
        )
        if (
            set(inventory) != expected_keys
            or inventory.get("schema_version") != "evaluation-stored-result/v1"
            or not isinstance(request_identity, str)
            or _REQUEST_DIRECTORY.fullmatch(request_identity) is None
            or not isinstance(status, str)
            or status not in statuses
            or not isinstance(attempts, list)
            or not attempts
            or any(
                not isinstance(value, str) or _REQUEST_DIRECTORY.fullmatch(value) is None
                for value in attempts
            )
            or any(
                value is not None
                and (not isinstance(value, str) or re.fullmatch(_OPAQUE_PATTERN, value) is None)
                for value in nullable_refs
            )
            or any(
                value is not None
                and (not isinstance(value, str) or _REQUEST_DIRECTORY.fullmatch(value) is None)
                for value in nullable_hashes
            )
        ):
            raise AttemptStoreIntegrityError(
                "corrupt_artifact", "stored result inventory is invalid"
            )
        return inventory

    @staticmethod
    def _read_canonical_mapping(payload: bytes, *, label: str) -> dict[str, object]:
        try:
            decoded: object = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AttemptStoreIntegrityError(
                "corrupt_artifact", f"{label} object is invalid JSON"
            ) from exc
        if not isinstance(decoded, dict) or any(
            not isinstance(key, str) for key in decoded
        ):
            raise AttemptStoreIntegrityError(
                "corrupt_artifact", f"{label} object must be a JSON mapping"
            )
        mapping = cast(dict[str, object], decoded)
        if payload != _canonical_bytes(mapping):
            raise AttemptStoreIntegrityError(
                "integrity_error", f"{label} object serialization is not canonical"
            )
        return mapping

    @staticmethod
    def _execution_linkage(entry: StoreLedgerEntry) -> tuple[object, ...]:
        return (
            entry.request_identity_sha256,
            entry.manifest_reference_id,
            entry.manifest_content_sha256,
            entry.case_reference_id,
            entry.case_content_sha256,
            entry.family_id,
            entry.variant_content_sha256,
            entry.snapshot_id,
            entry.evidence_content_sha256,
            entry.visibility_projection_sha256,
            entry.context_sha256,
            entry.prompt_sha256,
            entry.response_schema_sha256,
            entry.model_policy_sha256,
            entry.provider_ref,
            entry.model_ref,
            entry.model_version_ref,
            entry.resource_policy_ref,
            entry.retry_policy_ref,
        )

    def _read_object(self, digest: str) -> bytes:
        if _REQUEST_DIRECTORY.fullmatch(digest) is None:
            raise AttemptStoreIntegrityError(
                "integrity_error", "object reference is not lowercase SHA-256"
            )
        path = self.object_root / digest[:2] / digest[2:]
        if path.is_symlink() or not path.is_file():
            raise AttemptStoreIntegrityError(
                "integrity_error", "ledger references a missing immutable object"
            )
        payload = path.read_bytes()
        if content_sha256(payload) != digest:
            raise AttemptStoreIntegrityError(
                "corrupt_artifact", "ledger object bytes do not match content hash"
            )
        return payload

    def _verify_objects(self) -> None:
        for bucket in self.object_root.iterdir():
            if bucket.is_symlink() or not bucket.is_dir() or _OBJECT_BUCKET.fullmatch(bucket.name) is None:
                raise AttemptStoreIntegrityError(
                    "integrity_error", "object bucket name or type is invalid"
                )
            for path in bucket.iterdir():
                if path.name.endswith(".stage"):
                    continue
                if path.is_symlink() or not path.is_file() or _OBJECT_NAME.fullmatch(path.name) is None:
                    raise AttemptStoreIntegrityError(
                        "integrity_error", "object store contains invalid membership"
                    )
                payload = path.read_bytes()
                if content_sha256(payload) != bucket.name + path.name:
                    raise AttemptStoreIntegrityError(
                        "corrupt_artifact", "content-addressed object hash does not match bytes"
                    )

    def _atomic_create(self, destination: Path, payload: bytes) -> Literal["created", "identical"]:
        if destination.is_symlink():
            raise AttemptStoreIntegrityError(
                "integrity_error", "immutable destination must not be a symlink"
            )
        if destination.exists():
            if destination.is_file() and destination.read_bytes() == payload:
                return "identical"
            raise AttemptStoreConflictError(
                "conflict", "refusing to overwrite non-identical immutable bytes"
            )
        fd, stage_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".stage",
        )
        stage = Path(stage_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(os.fspath(stage), os.fspath(destination), follow_symlinks=False)
            except FileExistsError:
                if (
                    not destination.is_symlink()
                    and destination.is_file()
                    and destination.read_bytes() == payload
                ):
                    return "identical"
                raise AttemptStoreConflictError(
                    "conflict", "concurrent writer published different immutable bytes"
                ) from None
        finally:
            stage.unlink(missing_ok=True)
        if destination.read_bytes() != payload:
            raise AttemptStoreIntegrityError(
                "io_error", "persisted immutable bytes differ from staged bytes"
            )
        return "created"

    @staticmethod
    def _validate_request_hash(value: str) -> None:
        if _REQUEST_DIRECTORY.fullmatch(value) is None:
            raise AttemptStoreIntegrityError(
                "integrity_error", "request identity must be canonical lowercase SHA-256"
            )


def _result_inventory_bytes(result: GatewayExecutionResult) -> bytes:
    raw = result.raw_response
    parsed = result.parsed_response
    issue = result.issue
    payload = {
        "schema_version": "evaluation-stored-result/v1",
        "request_identity_sha256": result.request_identity_sha256,
        "status": result.status,
        "attempt_record_sha256": [
            content_sha256(_canonical_bytes(record.model_dump(mode="json")))
            for record in result.attempts
        ],
        "raw_response_ref": raw.artifact_ref if raw is not None else None,
        "raw_response_sha256": raw.content_sha256 if raw is not None else None,
        "parsed_response_ref": parsed.artifact_ref if parsed is not None else None,
        "parsed_response_sha256": parsed.content_sha256 if parsed is not None else None,
        "issue_ref": issue.issue_id if issue is not None else None,
        "issue_sha256": issue.issue_sha256 if issue is not None else None,
    }
    return _canonical_bytes(payload)
