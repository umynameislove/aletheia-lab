"""Checksum-first acquisition of the registered external replication snapshot."""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Final

from aletheia_lab.benchmark.p2.confirmatory_execution import ConfirmatoryExecutionError
from aletheia_lab.benchmark.p2.confirmatory_protocol import DatasetBinding
from aletheia_lab.data.download import ChecksumError, download_pinned_file

_CHUNK: Final[int] = 1 << 20
_BANK_ARCHIVE_MEMBER: Final[str] = "bank-additional.zip"
_BANK_SNAPSHOT_MEMBER: Final[str] = "bank-additional/bank-additional-full.csv"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(_CHUNK), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ConfirmatoryExecutionError(f"cannot hash registered source: {path}") from exc
    return digest.hexdigest()


def download_registered_archive(*, dataset: DatasetBinding, destination: str | Path) -> Path:
    """Download to quarantine, verify bytes, then atomically promote the archive."""

    if dataset.role != "external_replication" or dataset.archive_sha256 is None:
        raise ConfirmatoryExecutionError("only the registered external archive may be downloaded")
    if not dataset.source_uri.startswith("https://"):
        raise ConfirmatoryExecutionError("registered archive source must use HTTPS")
    try:
        return download_pinned_file(
            url=dataset.source_uri,
            sha256=dataset.archive_sha256,
            destination=destination,
        )
    except (OSError, ValueError, ChecksumError) as exc:
        raise ConfirmatoryExecutionError(
            "registered archive download or checksum verification failed"
        ) from exc


def extract_registered_snapshot(
    *, dataset: DatasetBinding, archive_path: str | Path, destination: str | Path
) -> Path:
    """Extract exactly one frozen member without trusting archive paths."""

    if dataset.role != "external_replication" or dataset.archive_sha256 is None:
        raise ConfirmatoryExecutionError("snapshot extraction requires external replication")
    archive = Path(archive_path)
    if sha256_file(archive) != dataset.archive_sha256:
        raise ConfirmatoryExecutionError("registered archive checksum mismatch before extraction")
    output = Path(destination)
    if output.exists():
        if sha256_file(output) != dataset.snapshot_sha256:
            raise ConfirmatoryExecutionError("existing registered snapshot checksum mismatch")
        return output
    try:
        with zipfile.ZipFile(archive) as outer:
            outer_members = {info.filename: info for info in outer.infolist()}
            nested_info = outer_members.get(_BANK_ARCHIVE_MEMBER)
            if nested_info is None or nested_info.is_dir():
                raise ConfirmatoryExecutionError(
                    "registered nested archive is absent from the source"
                )
            nested_bytes = outer.read(nested_info)
        with zipfile.ZipFile(io.BytesIO(nested_bytes)) as nested:
            members = {info.filename: info for info in nested.infolist()}
            info = members.get(_BANK_SNAPSHOT_MEMBER)
            if info is None or info.is_dir():
                raise ConfirmatoryExecutionError("registered CSV is absent from the archive")
            path = PurePosixPath(info.filename)
            if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
                raise ConfirmatoryExecutionError("registered archive member path is unsafe")
            content = nested.read(info)
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise ConfirmatoryExecutionError("cannot read registered external archive") from exc
    if hashlib.sha256(content).hexdigest() != dataset.snapshot_sha256:
        raise ConfirmatoryExecutionError("extracted registered snapshot checksum mismatch")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}-", suffix=".part", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output
