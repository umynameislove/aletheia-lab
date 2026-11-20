"""Private PostgreSQL archive_command helper for the opt-in K18 PITR fixture."""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path


def copy_wal(source: Path, archive: Path, name: str) -> None:
    if not re.fullmatch(r"(?:[0-9A-F]{24}(?:\.[0-9A-F]{8}\.backup)?|[0-9A-F]{8}\.history)", name):
        raise ValueError("Unexpected WAL archive filename.")
    destination = archive / name
    if destination.exists():
        with source.open("rb") as incoming, destination.open("rb") as saved:
            if (
                hashlib.file_digest(incoming, "sha256").digest()
                != hashlib.file_digest(saved, "sha256").digest()
            ):
                raise ValueError("An archived WAL file must never be overwritten.")
        return
    temporary = archive / (name + ".copying")
    try:
        with source.open("rb") as incoming, temporary.open("xb") as outgoing:
            while block := incoming.read(1024 * 1024):
                outgoing.write(block)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        temporary.chmod(0o600)
        temporary.rename(destination)
        descriptor = os.open(archive, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    copy_wal(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
