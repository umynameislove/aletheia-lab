"""Write-only immutable publication primitives for the attempt store."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from aletheia_lab.filesystem import (
    ImmutablePublicationConflictError,
    ImmutablePublicationIntegrityError,
    publish_immutable_file,
)
from aletheia_lab.project.identity import content_sha256

from .contracts import (
    AttemptStoreConflictError,
    AttemptStoreIntegrityError,
    validate_request_hash,
)


class AttemptStoreWriter:
    """Own immutable object and ledger publication; never verifies itself."""

    def __init__(self, *, object_root: Path, request_root: Path, terminal_root: Path) -> None:
        self.object_root = object_root
        self.request_root = request_root
        self.terminal_root = terminal_root

    def _ledger_root(self, request_hash: str) -> Path:
        validate_request_hash(request_hash)
        request_dir = self.request_root / request_hash
        ledger = request_dir / "ledger"
        ledger.mkdir(parents=True, exist_ok=True)
        if request_dir.is_symlink() or ledger.is_symlink() or not ledger.is_dir():
            raise AttemptStoreIntegrityError(
                "integrity_error", "request ledger path must be a real directory"
            )
        return ledger

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

    def _atomic_create(self, destination: Path, payload: bytes) -> Literal["created", "identical"]:
        try:
            return publish_immutable_file(destination, payload)
        except ImmutablePublicationConflictError as exc:
            raise AttemptStoreConflictError("conflict", str(exc)) from exc
        except ImmutablePublicationIntegrityError as exc:
            if "persisted immutable bytes differ" in str(exc):
                raise AttemptStoreIntegrityError("io_error", str(exc)) from exc
            raise AttemptStoreIntegrityError("integrity_error", str(exc)) from exc
