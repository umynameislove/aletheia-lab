"""Bounded local study artifacts; no execution authority or public delivery."""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Any

from aletheia_lab.filesystem import write_new_file

MAX_ARTIFACT_BYTES = 32 * 1024 * 1024


def windows_io_path(path: Path) -> Path:
    """Use extended Windows drive/UNC paths for I/O without resolving aliases."""
    if sys.platform != "win32":
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith(("\\\\?\\", "\\\\.\\")):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate artifact member")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("nonfinite artifact number")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite artifact number")
    return result


def checked_artifact_path(path: Path) -> Path:
    """Keep aliases visible to checks before any resolution or long-path I/O."""
    absolute = windows_io_path(path.absolute())
    if any(part.is_symlink() for part in (absolute, *absolute.parents)):
        raise ValueError("artifact path contains a symbolic link")
    return windows_io_path(absolute.resolve())


def read_artifact_bytes(path: Path) -> bytes:
    source = checked_artifact_path(path)
    if not source.is_file() or source.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ValueError("artifact is unavailable or exceeds its byte limit")
    with source.open("rb") as stream:
        raw = stream.read(MAX_ARTIFACT_BYTES + 1)
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError("artifact exceeds its byte limit")
    return raw


def parse_artifact(raw: bytes) -> dict[str, Any]:
    """Parse an already bounded snapshot with the same strict JSON semantics."""
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError("artifact exceeds its byte limit")
    value = json.loads(
        raw,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
        parse_float=_finite_float,
    )
    if not isinstance(value, dict):
        raise ValueError("artifact must be an object")
    return value


def read_artifact(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = read_artifact_bytes(path)
    return parse_artifact(raw), raw


def checked_output(path: Path, repository: Path) -> Path:
    destination = checked_artifact_path(path)
    repo = windows_io_path(repository.resolve())
    if destination.is_relative_to(repo) or destination.exists():
        raise ValueError("output must be a new file outside the repository")
    return destination


def write_artifact(path: Path, repository: Path, value: dict[str, Any]) -> None:
    destination = checked_output(path, repository)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    write_new_file(destination, payload.encode("utf-8") + b"\n")
