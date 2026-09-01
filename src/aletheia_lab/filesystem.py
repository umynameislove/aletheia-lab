"""Cross-platform filesystem publication primitives."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Final, Literal, TypeAlias

_IS_WINDOWS: Final[bool] = os.name == "nt"
_WINDOWS_DIRECTORY_PUBLISH_DELAYS: Final[tuple[float, ...]] = (
    0.05,
    0.10,
    0.20,
    0.40,
    0.80,
)

ImmutableFileDisposition: TypeAlias = Literal["created", "identical"]


class ImmutablePublicationConflictError(FileExistsError):
    """A final path already represents different immutable content."""


class ImmutablePublicationIntegrityError(OSError):
    """Publication encountered an invalid path or non-identical final bytes."""


def _fsync_directory(directory: Path) -> None:
    """Durably commit a directory namespace where the platform supports it."""

    if not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_new_file(path: Path, payload: bytes) -> None:
    """Create and fsync one file inside an unpublished staging directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise ImmutablePublicationIntegrityError(
            f"staged file parent must be a real directory: {path.parent}"
        )
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def fsync_directory_tree(root: Path) -> None:
    """Fsync directories bottom-up without reopening immutable payload files."""

    if root.is_symlink() or not root.is_dir():
        raise NotADirectoryError(f"directory tree root is not a real directory: {root}")
    directories = sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    )
    if any(path.is_symlink() for path in directories):
        raise ImmutablePublicationIntegrityError("directory tree contains a symlinked directory")
    for directory in (*directories, root):
        _fsync_directory(directory)


def _existing_file_disposition(destination: Path, payload: bytes) -> ImmutableFileDisposition:
    if destination.is_symlink():
        raise ImmutablePublicationIntegrityError(
            f"immutable destination must not be a symlink: {destination}"
        )
    if destination.is_file() and destination.read_bytes() == payload:
        return "identical"
    raise ImmutablePublicationConflictError(
        f"refusing to overwrite non-identical immutable bytes: {destination}"
    )


def _link_staged_file(stage: Path, destination: Path, payload: bytes) -> ImmutableFileDisposition:
    delays: tuple[float | None, ...]
    delays = (*_WINDOWS_DIRECTORY_PUBLISH_DELAYS, None) if _IS_WINDOWS else (None,)
    for delay in delays:
        try:
            os.link(os.fspath(stage), os.fspath(destination), follow_symlinks=False)
            return "created"
        except FileExistsError:
            return _existing_file_disposition(destination, payload)
        except PermissionError:
            if destination.exists() or destination.is_symlink():
                return _existing_file_disposition(destination, payload)
            if delay is None:
                raise
            time.sleep(delay)
    raise AssertionError("immutable file publication retry loop did not terminate")


def publish_immutable_file(destination: Path, payload: bytes) -> ImmutableFileDisposition:
    """Create one immutable file atomically and idempotently.

    A fully written and fsynced sibling stage is hard-linked to the final name.
    Hard-link creation never replaces an existing path, so a concurrent writer
    can only produce an idempotent byte-identical result or a closed conflict.
    Temporary stages are removed after success, conflict, interruption, or I/O
    failure. Windows retries only transient access denials and remains bounded.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ImmutablePublicationIntegrityError(
            f"immutable destination parent must be a real directory: {destination.parent}"
        )
    if destination.exists() or destination.is_symlink():
        return _existing_file_disposition(destination, payload)

    descriptor, stage_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".stage",
    )
    stage = Path(stage_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        disposition = _link_staged_file(stage, destination, payload)
    finally:
        stage.unlink(missing_ok=True)

    if destination.is_symlink() or not destination.is_file() or destination.read_bytes() != payload:
        raise ImmutablePublicationIntegrityError(
            f"persisted immutable bytes differ from staged bytes: {destination}"
        )
    return disposition


def publish_staged_directory(stage: Path, destination: Path) -> None:
    """Publish one complete same-volume directory without a copy fallback.

    The caller must build ``stage`` beside an absent ``destination``. POSIX
    uses ``os.replace`` for one no-gap publication. Windows cannot reliably
    replace directories with ``os.replace`` even when the destination is
    absent, so it uses ``os.rename`` with bounded retries for transient file
    scanner locks. A destination race and a persistent denial both fail closed.
    """

    if stage.is_symlink() or not stage.is_dir():
        raise NotADirectoryError(f"staged publication is not a real directory: {stage}")
    if stage.parent.resolve() != destination.parent.resolve():
        raise ValueError("staged directory publication must stay on one parent volume")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to replace published directory: {destination}")

    if not _IS_WINDOWS:
        os.replace(stage, destination)
        return

    for delay in (*_WINDOWS_DIRECTORY_PUBLISH_DELAYS, None):
        try:
            os.rename(stage, destination)
            return
        except PermissionError as exc:
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(
                    f"destination appeared during directory publication: {destination}"
                ) from exc
            if delay is None:
                raise
            time.sleep(delay)
