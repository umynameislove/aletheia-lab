"""Read-only attempt-ledger parsing and terminal reconciliation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from aletheia_lab.evaluation.execution_contracts import TechnicalIssue, canonical_execution_sha256
from aletheia_lab.model_gateway.contracts import AttemptRecord, TerminalStatus
from aletheia_lab.project.identity import content_sha256

from .contracts import (
    _LEDGER_FILE,
    _OPAQUE_PATTERN,
    _REQUEST_DIRECTORY,
    TERMINAL_INVENTORY_SCHEMA_VERSION,
    AttemptStoreIntegrityError,
    AttemptStoreTransitionError,
    StoreLedgerEntry,
    TerminalExecutionInventory,
    _canonical_bytes,
    _entry_bytes,
    validate_request_hash,
)
from .transitions import verify_stored_transition


class AttemptStoreReader:
    """Read and reconcile persisted state without any write authority."""

    def __init__(self, *, object_root: Path, request_root: Path, terminal_root: Path) -> None:
        self.object_root = object_root
        self.request_root = request_root
        self.terminal_root = terminal_root

    def _load_chain(
        self,
        request_hash: str,
        *,
        verify_terminal: bool = True,
    ) -> tuple[tuple[StoreLedgerEntry, ...], StoreLedgerEntry | None]:
        validate_request_hash(request_hash)
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
            raise AttemptStoreIntegrityError("integrity_error", "request ledger path is invalid")
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
                    and self._execution_linkage(entry) != self._execution_linkage(entries[0])
                )
            ):
                raise AttemptStoreIntegrityError(
                    "integrity_error", "ledger sequence or content link does not reconcile"
                )
            verify_stored_transition(tuple(entries), entry)
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
                    and self._execution_linkage(terminal) != self._execution_linkage(entries[0])
                )
            ):
                raise AttemptStoreIntegrityError(
                    "integrity_error", "terminal index does not extend the request ledger"
                )
            verify_stored_transition(tuple(entries), terminal)
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

    def _terminal_inventory(self, request_hash: str) -> TerminalExecutionInventory:
        entries, terminal = self._load_chain(request_hash)
        if terminal is None or not entries:
            raise AttemptStoreTransitionError(
                "invalid_transition", "request has no completely published terminal inventory"
            )
        outcome = next(
            (entry for entry in entries if entry.state == "parsed_or_failed"),
            None,
        )
        if outcome is None or outcome.result_object_sha256 is None:
            raise AttemptStoreIntegrityError(
                "integrity_error", "terminal request has no parsed-or-failed inventory"
            )
        response = next(
            (entry for entry in entries if entry.state == "response_recorded"),
            None,
        )
        attempt_hashes = tuple(
            entry.attempt_record_sha256
            for entry in entries
            if entry.state == "attempt_recorded" and entry.attempt_record_sha256 is not None
        )
        attempt_records = tuple(
            AttemptRecord.model_validate_json(self._read_object(digest))
            for digest in attempt_hashes
        )
        result_inventory = self._read_result_inventory(outcome.result_object_sha256)
        first = entries[0]
        fields: dict[str, object] = {
            "schema_version": TERMINAL_INVENTORY_SCHEMA_VERSION,
            "request_identity_sha256": request_hash,
            "manifest_reference_id": first.manifest_reference_id,
            "manifest_content_sha256": first.manifest_content_sha256,
            "case_reference_id": first.case_reference_id,
            "case_content_sha256": first.case_content_sha256,
            "family_id": first.family_id,
            "variant_content_sha256": first.variant_content_sha256,
            "snapshot_id": first.snapshot_id,
            "evidence_content_sha256": first.evidence_content_sha256,
            "visibility_projection_sha256": first.visibility_projection_sha256,
            "context_sha256": first.context_sha256,
            "prompt_sha256": first.prompt_sha256,
            "response_schema_sha256": first.response_schema_sha256,
            "model_policy_sha256": first.model_policy_sha256,
            "provider_ref": first.provider_ref,
            "model_ref": first.model_ref,
            "model_version_ref": first.model_version_ref,
            "resource_policy_ref": first.resource_policy_ref,
            "retry_policy_ref": first.retry_policy_ref,
            "attempt_record_sha256": attempt_hashes,
            "attempt_outcomes": tuple(record.outcome for record in attempt_records),
            "attempt_response_modes": tuple(record.response_mode for record in attempt_records),
            "gateway_status": cast(TerminalStatus, result_inventory["status"]),
            "raw_response_ref": response.raw_response_ref if response is not None else None,
            "raw_response_sha256": (response.raw_response_sha256 if response is not None else None),
            "parsed_response_ref": outcome.parsed_response_ref,
            "parsed_response_sha256": outcome.parsed_response_sha256,
            "issue_ref": outcome.issue_ref,
            "issue_sha256": outcome.issue_sha256,
            "result_object_sha256": outcome.result_object_sha256,
            "terminal_entry_sha256": content_sha256(_entry_bytes(terminal)),
            "ledger_entry_count": terminal.sequence,
        }
        inventory_sha = canonical_execution_sha256(fields)
        return TerminalExecutionInventory.model_validate(
            {
                "inventory_id": f"ev-{inventory_sha}",
                "inventory_sha256": inventory_sha,
                **fields,
            }
        )

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
                or record.attempt.attempt_identity_sha256 != entry.attempt_identity_sha256
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
            entry.attempt_record_sha256 for entry in entries if entry.state == "attempt_recorded"
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
        if not isinstance(decoded, dict) or any(not isinstance(key, str) for key in decoded):
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
