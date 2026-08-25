"""Canonical identity primitives for local-project contracts.

Project identities must remain stable across process, locale, operating-system
and input ordering differences.  This module owns a P3-specific serialization
domain rather than reusing the frozen P1/P2 hash domains.

Filesystem authorization is deliberately outside this module.  Callers may
hash a canonical root identity, but only the permission boundary introduced by
the ingestion layer may decide whether a real path is authorized.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from decimal import Decimal
from pathlib import PurePosixPath, PureWindowsPath
from typing import Final, Literal, NoReturn

PROJECT_CANONICAL_SCHEMA_VERSION: Final[Literal["p3-project-canonical/v1"]] = (
    "p3-project-canonical/v1"
)
PROJECT_ROOT_IDENTITY_SCHEMA_VERSION: Final[Literal["p3-project-root-identity/v1"]] = (
    "p3-project-root-identity/v1"
)
PROJECT_IDENTITY_SCHEMA_VERSION: Final[Literal["p3-project-identity/v1"]] = (
    "p3-project-identity/v1"
)

SHA256_PATTERN: Final[str] = r"^[0-9a-f]{64}$"
PROJECT_ID_PATTERN: Final[str] = r"^p3-project-[0-9a-f]{64}$"
PROJECT_ITEM_ID_PATTERN: Final[str] = r"^p3-item-[0-9a-f]{64}$"
PROJECT_BUNDLE_ID_PATTERN: Final[str] = r"^p3-bundle-[0-9a-f]{64}$"
ARTIFACT_ID_PATTERN: Final[str] = r"^p3-artifact-[0-9a-f]{64}$"
SNAPSHOT_ID_PATTERN: Final[str] = r"^p3-snapshot-[0-9a-f]{64}$"

_MAX_KEY_LENGTH: Final[int] = 256
_MAX_TEXT_LENGTH: Final[int] = 4096
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


class ProjectIdentityError(ValueError):
    """Raised when project content or identity fails a canonical contract."""


def _fail(message: str) -> NoReturn:
    raise ProjectIdentityError(message)


def _unicode_scalar_text(value: str, *, label: str) -> str:
    """Reject lone surrogates that cannot be represented as canonical UTF-8."""

    normalized = unicodedata.normalize("NFC", value)
    try:
        normalized.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ProjectIdentityError(f"{label} must contain valid Unicode scalar values") from exc
    return normalized


def normalize_text(value: str, *, label: str, max_length: int = _MAX_TEXT_LENGTH) -> str:
    """Require trimmed, control-free Unicode NFC text."""

    if not value or value != value.strip():
        _fail(f"{label} must be non-blank and trimmed")
    if len(value) > max_length:
        _fail(f"{label} exceeds the maximum length of {max_length}")
    if _CONTROL_CHARACTERS.search(value):
        _fail(f"{label} must not contain control characters")
    if value != _unicode_scalar_text(value, label=label):
        _fail(f"{label} must already be Unicode NFC")
    return value


def normalize_relative_project_path(value: str) -> str:
    """Require one portable, canonical POSIX-relative project path.

    This is a representation invariant, not a filesystem authorization check.
    Symlink resolution, real-path containment and TOCTOU handling belong to the
    permission boundary and cannot be inferred from a path string alone.
    """

    normalize_text(value, label="project relative path", max_length=1024)
    windows_path = PureWindowsPath(value)
    if (
        "\\" in value
        or windows_path.is_absolute()
        or windows_path.drive
        or windows_path.root
    ):
        _fail("project relative path must not be a Windows drive or UNC path")
    path = PurePosixPath(value)
    if path.is_absolute() or value.startswith("//"):
        _fail("project relative path must not be absolute")
    if value in {".", ".."} or any(part in {"", ".", ".."} for part in path.parts):
        _fail("project relative path must not contain empty, current or parent components")
    if path.as_posix() != value:
        _fail("project relative path must use normalized POSIX syntax")
    return value


def _canonical_number(value: int | float) -> int | float:
    if isinstance(value, bool):
        raise TypeError("bool is not a canonical number")
    if isinstance(value, int):
        return value
    if not math.isfinite(value):
        _fail("non-finite numbers are not canonical")
    if value == 0.0 and math.copysign(1.0, value) < 0:
        _fail("negative zero is not canonical")
    # ``repr`` is the shortest round-trip decimal representation on every
    # supported Python version.  Converting through Decimal rejects accidental
    # non-decimal subclasses while returning the original float preserves JSON
    # numeric type, so integer ``1`` cannot collide with string ``"1"``.
    Decimal(repr(value))
    return value


def _canonicalize(value: object) -> object:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _unicode_scalar_text(value, label="canonical project string")
    if isinstance(value, int | float):
        return _canonical_number(value)
    if isinstance(value, dict):
        canonical: dict[str, object] = {}
        for raw_key, nested in value.items():
            if not isinstance(raw_key, str):
                raise TypeError("canonical project mapping keys must be strings")
            key = _unicode_scalar_text(raw_key, label="canonical project mapping key")
            if not key or len(key) > _MAX_KEY_LENGTH:
                _fail("canonical project mapping key is empty or too long")
            if key in canonical:
                _fail(f"canonical project mapping has a duplicate key after NFC: {key!r}")
            canonical[key] = _canonicalize(nested)
        return canonical
    if isinstance(value, list | tuple):
        return [_canonicalize(item) for item in value]
    raise TypeError(f"value is not canonically serializable: {type(value).__name__}")


def canonical_project_json(payload: object) -> str:
    """Return compact, key-sorted canonical JSON in the P3 identity domain."""

    return json.dumps(
        _canonicalize(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_project_sha256(payload: object) -> str:
    """Hash one canonical P3 payload."""

    return hashlib.sha256(canonical_project_json(payload).encode("utf-8")).hexdigest()


def content_sha256(content: bytes) -> str:
    """Return the SHA-256 digest of exact source or artifact bytes."""

    if not isinstance(content, bytes):
        raise TypeError("project content hashing requires bytes")
    return hashlib.sha256(content).hexdigest()


def granted_root_fingerprint(canonical_root_identity: str) -> str:
    """Hash a previously resolved root identity without retaining its path.

    The caller must supply a canonical, security-resolved identity.  This
    function intentionally neither touches the filesystem nor authorizes it.
    """

    normalize_text(canonical_root_identity, label="canonical root identity", max_length=4096)
    return canonical_project_sha256(
        {
            "schema_version": PROJECT_ROOT_IDENTITY_SCHEMA_VERSION,
            "canonical_root_identity": canonical_root_identity,
        }
    )


def project_id_for_root(root_fingerprint: str) -> str:
    """Derive a namespaced project ID from a validated root fingerprint."""

    if re.fullmatch(SHA256_PATTERN, root_fingerprint) is None:
        _fail("root fingerprint must be a lowercase SHA-256 digest")
    digest = canonical_project_sha256(
        {
            "schema_version": PROJECT_IDENTITY_SCHEMA_VERSION,
            "granted_root_fingerprint": root_fingerprint,
        }
    )
    return f"p3-project-{digest}"
