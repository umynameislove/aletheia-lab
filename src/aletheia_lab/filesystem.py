"""Cross-platform filesystem publication primitives."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Final

_IS_WINDOWS: Final[bool] = os.name == "nt"
_WINDOWS_DIRECTORY_PUBLISH_DELAYS: Final[tuple[float, ...]] = (
    0.05,
    0.10,
    0.20,
    0.40,
    0.80,
)


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
