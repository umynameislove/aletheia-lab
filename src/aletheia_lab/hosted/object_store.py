"""Managed local object boundary; metadata visibility belongs to PostgreSQL.

This backend requires a shared durable filesystem for API/worker deployments.
It does not implement remote object storage or authorize callers.
"""
from __future__ import annotations

import json
import re
import stat
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from aletheia_lab.filesystem import (
    _fsync_directory,
    publish_immutable_file,
    windows_io_path,
    write_new_file,
)
from aletheia_lab.hosted.content_hashing import content_hasher
from aletheia_lab.project.persistence import _json_bytes

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MARKER = b"aletheia-k05-filesystem/v1\n"


class ObjectIntegrityError(ValueError):
    """Missing, corrupt or unsafe object; messages contain no payload/path."""


@dataclass(frozen=True)
class ObjectDescriptor:
    sha256: str
    byte_size: int
    media_type: str
    schema_version: str | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.sha256, str) or not _SHA256.fullmatch(self.sha256)
                or type(self.byte_size) is not int or self.byte_size < 0
                or not isinstance(self.media_type, str) or not 1 <= len(self.media_type) <= 255
                or self.schema_version is not None and
                (not isinstance(self.schema_version, str) or not 1 <= len(self.schema_version) <= 255)):
            raise ValueError("Invalid object descriptor")


@dataclass(frozen=True)
class ObjectCandidate:
    sha256: str
    relative_path: str
    modified_at: float
    staged: bool


def _regular(path: Path, *, directory: bool = False) -> None:
    try:
        info = path.lstat()
        reparse = getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        if stat.S_ISLNK(info.st_mode) or reparse:
            raise ObjectIntegrityError("Unsafe object path")
        if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
            raise ObjectIntegrityError("Unsafe object path")
    except OSError:
        raise ObjectIntegrityError("Object is unavailable") from None


class FilesystemObjectStore:
    def __init__(self, root: str | Path, *, maximum_object_bytes: int = 64 * 1024 * 1024):
        if type(maximum_object_bytes) is not int or maximum_object_bytes <= 0:
            raise ValueError("Positive object byte budget required")
        supplied = Path(root)
        new_directories = []
        ancestor = supplied
        while not ancestor.exists():
            new_directories.append(ancestor)
            ancestor = ancestor.parent
        supplied.mkdir(parents=True, exist_ok=True)
        _regular(supplied, directory=True)
        self.root = supplied.resolve()
        self.maximum_object_bytes = maximum_object_bytes
        marker = self.root / ".k05-object-store"
        if not marker.exists():
            if any(self.root.iterdir()):
                raise ObjectIntegrityError("Object root must be an empty managed directory")
            with suppress(FileExistsError):
                write_new_file(marker, _MARKER)
        _regular(marker)
        if marker.read_bytes() != _MARKER:
            raise ObjectIntegrityError("Unknown object store format")
        self.object_root = self.root / "sha256"
        self.object_root.mkdir(exist_ok=True)
        _regular(self.object_root, directory=True)
        _fsync_directory(self.root)
        for directory in new_directories:
            _fsync_directory(directory.parent)

    def path(self, sha256: str) -> Path:
        if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
            raise ValueError("Lowercase object SHA-256 required")
        _regular(self.root, directory=True)
        _regular(self.object_root, directory=True)
        return self.object_root / sha256[:2] / sha256[2:]

    def describe(self, payload: bytes, *, media_type: str,
                 schema_version: str | None = None) -> ObjectDescriptor:
        if not isinstance(payload, bytes) or len(payload) > self.maximum_object_bytes:
            raise ValueError("Object byte budget exceeded")
        descriptor = ObjectDescriptor(content_hasher(payload).hexdigest(), len(payload),
                                      media_type, schema_version)
        self._validate(payload, descriptor)
        return descriptor

    @staticmethod
    def _validate(payload: bytes, descriptor: ObjectDescriptor) -> None:
        if (len(payload) != descriptor.byte_size
                or content_hasher(payload).hexdigest() != descriptor.sha256):
            raise ObjectIntegrityError("Object hash or length mismatch")
        if descriptor.schema_version is not None:
            try:
                decoded = json.loads(payload)
                if (not isinstance(decoded, dict)
                        or decoded.get("schema_version") != descriptor.schema_version
                        or _json_bytes(decoded) != payload):
                    raise ValueError
            except (ValueError, TypeError, UnicodeError):
                raise ObjectIntegrityError("Object schema or canonical bytes mismatch") from None

    def publish(self, payload: bytes, *, media_type: str,
                schema_version: str | None = None) -> ObjectDescriptor:
        descriptor = self.describe(payload, media_type=media_type, schema_version=schema_version)
        destination = self.path(descriptor.sha256)
        destination.parent.mkdir(exist_ok=True)
        _regular(destination.parent, directory=True)
        try:
            _fsync_directory(self.object_root)
            publish_immutable_file(windows_io_path(destination), payload)
            _fsync_directory(destination.parent)
        except OSError:
            raise ObjectIntegrityError("Object publication failed") from None
        self.read(descriptor)
        return descriptor

    def read(self, descriptor: ObjectDescriptor) -> bytes:
        if descriptor.byte_size > self.maximum_object_bytes:
            raise ObjectIntegrityError("Object byte budget exceeded")
        candidate = self.path(descriptor.sha256)
        _regular(candidate.parent, directory=True)
        _regular(candidate)
        try:
            with candidate.open("rb") as handle:
                payload = handle.read(descriptor.byte_size + 1)
        except OSError:
            raise ObjectIntegrityError("Object is unavailable") from None
        self._validate(payload, descriptor)
        return payload

    def candidates(self, *, limit: int = 1000, after: str = "") -> tuple[ObjectCandidate, ...]:
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("Bounded object scan required")
        self.path("0" * 64)
        found = []
        for bucket in sorted(self.object_root.iterdir()):
            if not re.fullmatch(r"[0-9a-f]{2}", bucket.name):
                continue
            _regular(bucket, directory=True)
            for candidate in sorted(bucket.iterdir()):
                relative = candidate.relative_to(self.root).as_posix()
                if relative <= after:
                    continue
                staged = candidate.name.startswith(".") and candidate.name.endswith(".stage")
                tail = candidate.name[1:63] if staged else candidate.name
                sha = bucket.name + tail
                if not _SHA256.fullmatch(sha):
                    continue
                _regular(candidate)
                found.append(ObjectCandidate(sha, relative, candidate.stat().st_mtime, staged))
                if len(found) == limit:
                    return tuple(found)
        return tuple(found)

    def remove_candidate(self, candidate: ObjectCandidate) -> bool:
        """Only the reconciler may call this after digest lock/reference checks."""
        expected = self.path(candidate.sha256)
        target = self.root / candidate.relative_path
        if target.parent != expected.parent or (not candidate.staged and target != expected):
            raise ObjectIntegrityError("Invalid managed cleanup target")
        if candidate.staged and not (target.name.startswith("." + expected.name + ".")
                                     and target.name.endswith(".stage")):
            raise ObjectIntegrityError("Invalid managed stage target")
        _regular(target.parent, directory=True)
        if not target.exists() and not target.is_symlink():
            return False
        _regular(target)
        if target.stat().st_mtime != candidate.modified_at:
            return False
        target.unlink()
        _fsync_directory(target.parent)
        return True
