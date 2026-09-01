"""Independent attempt-store membership and integrity verification."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.project.identity import content_sha256

from .contracts import (
    _OBJECT_BUCKET,
    _OBJECT_NAME,
    _REQUEST_DIRECTORY,
    AttemptStoreIntegrityError,
    TechnicalFailureReceipt,
    _failure_bytes,
)
from .reader import AttemptStoreReader


class AttemptStoreIntegrityVerifier(AttemptStoreReader):
    """Verify complete store membership without importing write primitives."""

    def __init__(
        self,
        *,
        root: Path,
        object_root: Path,
        request_root: Path,
        terminal_root: Path,
        failure_root: Path,
    ) -> None:
        super().__init__(
            object_root=object_root,
            request_root=request_root,
            terminal_root=terminal_root,
        )
        self.root = root
        self.failure_root = failure_root

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

    def _verify_objects(self) -> None:
        for bucket in self.object_root.iterdir():
            if (
                bucket.is_symlink()
                or not bucket.is_dir()
                or _OBJECT_BUCKET.fullmatch(bucket.name) is None
            ):
                raise AttemptStoreIntegrityError(
                    "integrity_error", "object bucket name or type is invalid"
                )
            for path in bucket.iterdir():
                if path.name.endswith(".stage"):
                    continue
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or _OBJECT_NAME.fullmatch(path.name) is None
                ):
                    raise AttemptStoreIntegrityError(
                        "integrity_error", "object store contains invalid membership"
                    )
                payload = path.read_bytes()
                if content_sha256(payload) != bucket.name + path.name:
                    raise AttemptStoreIntegrityError(
                        "corrupt_artifact", "content-addressed object hash does not match bytes"
                    )
