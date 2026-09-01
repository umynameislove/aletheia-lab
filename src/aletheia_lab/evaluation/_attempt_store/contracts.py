"""Immutable content-addressed attempt ledger with atomic terminal publication."""

from __future__ import annotations

import re
from typing import Annotated, Final, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.model_gateway.contracts import (
    AttemptOutcome,
    GatewayExecutionResult,
    ResponseMode,
    TerminalStatus,
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
TERMINAL_INVENTORY_SCHEMA_VERSION: Final[Literal["evaluation-terminal-inventory/v1"]] = (
    "evaluation-terminal-inventory/v1"
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


def validate_request_hash(value: str) -> None:
    """Require the canonical lowercase SHA-256 request directory form."""

    if _REQUEST_DIRECTORY.fullmatch(value) is None:
        raise AttemptStoreIntegrityError(
            "integrity_error", "request identity must be canonical lowercase SHA-256"
        )


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
            self.issue_ref != f"ev-{self.issue_sha256}" or self.state != "parsed_or_failed"
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


class TerminalExecutionInventory(_StrictFrozenModel):
    """Canonical read-only projection of one completely published request."""

    schema_version: Literal["evaluation-terminal-inventory/v1"] = TERMINAL_INVENTORY_SCHEMA_VERSION
    inventory_id: OpaqueReference
    inventory_sha256: Sha256
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
    attempt_record_sha256: tuple[Sha256, ...]
    attempt_outcomes: tuple[AttemptOutcome, ...]
    attempt_response_modes: tuple[ResponseMode | None, ...]
    gateway_status: TerminalStatus
    raw_response_ref: OpaqueReference | None
    raw_response_sha256: Sha256 | None
    parsed_response_ref: OpaqueReference | None
    parsed_response_sha256: Sha256 | None
    issue_ref: OpaqueReference | None
    issue_sha256: Sha256 | None
    result_object_sha256: Sha256
    terminal_entry_sha256: Sha256
    ledger_entry_count: int = Field(gt=0)

    def identity_payload(self) -> dict[str, object]:
        payload = self.model_dump(mode="json", exclude={"inventory_id", "inventory_sha256"})
        return {str(key): value for key, value in payload.items()}

    @model_validator(mode="after")
    def _identity_and_outcome_reconcile(self) -> Self:
        expected = canonical_execution_sha256(self.identity_payload())
        if self.inventory_sha256 != expected or self.inventory_id != f"ev-{expected}":
            raise ValueError("terminal inventory identity does not match canonical fields")
        if not self.attempt_record_sha256:
            raise ValueError("terminal inventory requires at least one attempt")
        if not (
            len(self.attempt_record_sha256)
            == len(self.attempt_outcomes)
            == len(self.attempt_response_modes)
        ):
            raise ValueError("terminal attempt inventory lengths do not reconcile")
        if (self.raw_response_ref is None) != (self.raw_response_sha256 is None):
            raise ValueError("terminal raw response linkage must be complete or absent")
        if (self.parsed_response_ref is None) != (self.parsed_response_sha256 is None):
            raise ValueError("terminal parsed response linkage must be complete or absent")
        if (self.issue_ref is None) != (self.issue_sha256 is None):
            raise ValueError("terminal issue linkage must be complete or absent")
        if (self.parsed_response_ref is None) == (self.issue_ref is None):
            raise ValueError("terminal inventory requires exactly one technical outcome")
        if (self.gateway_status == "parsed") != (self.parsed_response_ref is not None):
            raise ValueError("terminal gateway status does not match parsed outcome")
        if self.gateway_status == "parsed" and self.attempt_response_modes[-1] is None:
            raise ValueError("parsed terminal inventory must retain response mode")
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
