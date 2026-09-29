"""Shared exact-byte hashing for artifacts too large to load into memory."""

from __future__ import annotations

import hashlib
from pathlib import Path


def file_sha256(path: Path) -> str:
    """Stream a file into SHA-256 and return its lowercase hexadecimal digest."""

    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


__all__ = ["file_sha256"]
