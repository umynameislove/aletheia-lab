"""Exact-byte hashing for hosted storage and credentials."""

from __future__ import annotations

import hashlib
from typing import Protocol


class ContentHasher(Protocol):
    def update(self, content: bytes | bytearray | memoryview, /) -> None: ...
    def digest(self) -> bytes: ...
    def hexdigest(self) -> str: ...
    def copy(self) -> ContentHasher: ...


def content_hasher(content: bytes | bytearray | memoryview = b"", *, usedforsecurity: bool = True) -> ContentHasher:
    """Create exact-byte SHA-256 state; callers retain their encoding and domain."""

    return hashlib.sha256(content, usedforsecurity=usedforsecurity)
