"""Bounded native Joblib descriptor observation without replacing its input stream.

Only freshly created trusted, uncompressed, single-file artifacts are supported.
The opened inode must be stable FROM FIRST OPEN through reconstruction: the
snapshot cannot certify bytes already buffered by Joblib's format detection.
The observer is sequential and process-local, not hostile-host attestation.
"""

from __future__ import annotations

import importlib
import inspect
import os
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from aletheia_lab.project.identity import content_sha256

MAX_ARTIFACT_BYTES = 262_144
_LOCK = threading.Lock()


def descriptor_bytes(stream: Any, directory: Path) -> bytes:
    """Read the original descriptor, preserving its position and identity."""
    path = Path(stream.name)
    if not path.resolve().is_relative_to(directory.resolve()) or path.is_symlink():
        raise ValueError("native input is outside the owned artifact directory")
    descriptor = stream.fileno()
    before = os.fstat(descriptor)
    position = stream.tell()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_ARTIFACT_BYTES:
        raise ValueError("native input is not a bounded regular artifact")
    if position != 0:
        raise ValueError("native input does not start at the supported boundary")
    if hasattr(os, "pread"):
        payload = os.pread(descriptor, before.st_size + 1, 0)
    else:
        try:
            payload = stream.read(before.st_size + 1)
        finally:
            stream.seek(position)
    after = os.fstat(descriptor)

    def identity(value: os.stat_result) -> tuple[int, int, int, int]:
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns

    if identity(before) != identity(after) or stream.tell() != position:
        raise ValueError("opened artifact changed during observation")
    if len(payload) != before.st_size:
        raise ValueError("incomplete native artifact snapshot")
    return bytes(payload)


class NativeCapture:
    """Two separate sinks share the same trusted offered-descriptor boundary."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.scope: str | None = None
        self.reference: list[dict[str, Any]] = []
        self.receipts: list[dict[str, Any]] = []
        self.unsupported: list[str] = []

    @contextmanager
    def installed(self, *, on_open: Any = None) -> Iterator[None]:
        module: Any = importlib.import_module("joblib.numpy_pickle")
        original = module._unpickle
        signature = inspect.signature(original)
        if not _LOCK.acquire(blocking=False):
            raise RuntimeError("concurrent application capture is not supported")
        owner = threading.get_ident()

        def observed(stream: Any, *args: Any, **kwargs: Any) -> Any:
            if threading.get_ident() != owner or self.scope is None:
                raise RuntimeError("unscoped or concurrent native load")
            if on_open is not None:
                on_open(stream)
            parameters = signature.bind(stream, *args, **kwargs)
            if parameters.arguments.get("mmap_mode") is not None:
                raise ValueError("mapped input is outside the supported boundary")
            try:
                payload = descriptor_bytes(stream, self.directory)
            except (ValueError, OSError, AttributeError) as exc:
                self.unsupported.append(self.scope)
                raise ValueError("unsupported native input boundary") from exc
            self.reference.append({"scope": self.scope, "raw_hex": payload.hex()})
            self.receipts.append(
                {"scope": self.scope, "descriptor_sha256": content_sha256(payload)}
            )
            # The ORIGINAL file object and arguments reach Joblib unchanged.
            return original(stream, *args, **kwargs)

        module._unpickle = observed
        try:
            yield
        finally:
            module._unpickle = original
            _LOCK.release()
