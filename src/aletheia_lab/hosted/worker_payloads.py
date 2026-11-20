"""Hashed, bounded private-file exchange for native snapshots larger than IPC input."""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from aletheia_lab.hosted.content_hashing import content_hasher

from .upload_project import _native_path, _plain_directory
from .worker_limits import MAX_UPLOAD_OUTPUT_BYTES


class PayloadReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: str = Field(pattern=r"^private_payload_v1$")
    exchange_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    byte_size: int = Field(gt=0, le=MAX_UPLOAD_OUTPUT_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _directory(root: Path, exchange_id: str) -> Path:
    if not root.is_absolute() or re.fullmatch(r"[0-9a-f]{32}", exchange_id) is None:
        raise ValueError("Invalid private exchange.")
    root = _native_path(root)
    _plain_directory(root)
    parent = root / "payload-exchanges"
    _plain_directory(parent)
    directory = parent / exchange_id
    _plain_directory(directory)
    return directory


def _signature(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _plain_file(info: os.stat_result) -> bool:
    return (stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)
            and not getattr(info, "st_file_attributes", 0) & 1024 and info.st_nlink == 1)


def _write(path: Path, data: bytes) -> None:
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_UPLOAD_OUTPUT_BYTES:
        raise ValueError("Private payload exceeds its bound.")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def _read(path: Path, reference: PayloadReference) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        before, named = os.fstat(fd), path.lstat()
        if (not _plain_file(before) or not _plain_file(named)
                or before.st_size != reference.byte_size or _signature(before) != _signature(named)):
            raise ValueError("Invalid private payload.")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(reference.byte_size + 1)
        after, current = os.fstat(fd), path.lstat()
        if (len(data) != reference.byte_size or content_hasher(data).hexdigest() != reference.sha256
                or _signature(before) != _signature(after) or _signature(before) != _signature(current)):
            raise ValueError("Private payload changed.")
        return data
    finally:
        os.close(fd)


def create_exchange(root: Path, exchange_id: str, payload: bytes) -> bytes:
    root = _native_path(root)
    _plain_directory(root)
    parent = root / "payload-exchanges"
    parent.mkdir(mode=0o700, exist_ok=True)
    _plain_directory(parent)
    if re.fullmatch(r"[0-9a-f]{32}", exchange_id) is None:
        raise ValueError("Invalid private exchange.")
    (parent / exchange_id).mkdir(mode=0o700)
    _write(_directory(root, exchange_id) / "input.json", payload)
    return PayloadReference(operation="private_payload_v1", exchange_id=exchange_id,
        byte_size=len(payload), sha256=content_hasher(payload).hexdigest()).model_dump_json().encode()


def read_input(root: Path, reference: PayloadReference) -> bytes:
    return _read(_directory(root, reference.exchange_id) / "input.json", reference)


def write_output(root: Path, reference: PayloadReference, output: bytes) -> bytes:
    _write(_directory(root, reference.exchange_id) / "output.json", output)
    return reference.model_copy(update={"byte_size": len(output), "sha256": content_hasher(output).hexdigest()}).model_dump_json().encode()


def read_output(root: Path, exchange_id: str, envelope: bytes) -> bytes:
    value = PayloadReference.model_validate_json(envelope)
    if value.exchange_id != exchange_id:
        raise ValueError("Private output belongs to another exchange.")
    return _read(_directory(root, exchange_id) / "output.json", value)


def cleanup_exchange(root: Path, exchange_id: str) -> None:
    parent = _native_path(root) / "payload-exchanges"
    if not (parent / exchange_id).exists():
        return
    directory = _directory(root, exchange_id)
    for entry in directory.iterdir():
        if entry.name not in {"input.json", "output.json"} or not _plain_file(entry.lstat()):
            raise ValueError("Invalid private exchange cleanup.")
        entry.unlink()
    directory.rmdir()


def is_reference(payload: bytes) -> bool:
    if len(payload) > 4096:
        return False
    try:
        value = json.loads(payload)
    except (ValueError, UnicodeError):
        return False
    return isinstance(value, dict) and value.get("operation") == "private_payload_v1"
