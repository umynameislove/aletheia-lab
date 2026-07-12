"""Reproducible dataset download and checksum verification.

The download is content-addressed: a file is only accepted if its SHA-256 equals
the value pinned in ``sources.py``. That makes the raw dataset byte-for-byte
reproducible and turns a moved/renamed/tampered source into a hard failure
instead of a silent drift.

No third-party HTTP client is used; the standard library keeps the dependency
surface small for a step that runs in CI and on fresh machines.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from aletheia_lab.data.sources import DatasetSource

_CHUNK = 1 << 20  # 1 MiB streaming reads, so large files never load into memory.
_TIMEOUT = 60


class ChecksumError(RuntimeError):
    """Raised when a file's SHA-256 does not match the pinned value."""


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 hex digest of a file, streamed in chunks."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path: str | Path, source: DatasetSource) -> str:
    """Verify a file against the pinned checksum; return the digest on success."""

    actual = sha256_file(path)
    if actual != source.sha256:
        msg = (
            f"sha256 mismatch for {Path(path).name}: "
            f"expected {source.sha256}, got {actual}"
        )
        raise ChecksumError(msg)
    return actual


def _download_to_temp(url: str, dest_dir: Path) -> Path:
    """Stream ``url`` into a temporary sibling file and return its path.

    The file is downloaded next to its future destination but is *not* promoted
    to it here. The caller verifies the returned temp file against the pinned
    checksum and only then atomically renames it into place, so a wrong-checksum
    or interrupted download can never reach a real destination. On any download
    failure the temp file is removed before the exception propagates.
    """

    dest_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dest_dir, suffix=".part")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:  # noqa: S310
            for chunk in iter(lambda: resp.read(_CHUNK), b""):
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp


def download_dataset(
    source: DatasetSource,
    dest: str | Path,
    *,
    offline: bool = False,
    force: bool = False,
) -> Path:
    """Ensure ``dest`` holds the pinned, checksum-verified dataset; return its path.

    Idempotent: if the file already exists it is verified and reused. With
    ``offline`` the network is never touched (verify-only). With ``force`` a new
    copy is downloaded.

    Fail-closed guarantee: newly downloaded content is verified *while still in a
    temporary file* and is atomically moved to ``dest`` only after the checksum
    matches. A wrong-checksum download, or a download interrupted by a
    network/read error, therefore leaves no new destination and never replaces a
    previously valid one (including under ``force``). A pre-existing file whose
    checksum does not match is not silently deleted: verification raises and the
    caller decides whether to ``force`` a re-download.
    """

    dest = Path(dest)
    if dest.exists() and not force:
        verify_file(dest, source)
        return dest
    if offline:
        msg = f"offline: {dest} is missing and downloading is disabled"
        raise FileNotFoundError(msg)
    tmp = _download_to_temp(source.url, dest.parent)
    try:
        verify_file(tmp, source)
        os.replace(tmp, dest)
    finally:
        # After a successful replace ``tmp`` no longer exists. On checksum,
        # network-independent verification, or replace failure, remove the
        # quarantined temporary file and leave ``dest`` exactly as it was.
        tmp.unlink(missing_ok=True)
    return dest


def write_provenance(source: DatasetSource, dest: str | Path, out_path: str | Path) -> Path:
    """Record where the raw file came from and when, next to the data.

    The ``file`` field is stored exactly as the caller passes it (contract B):
    the shipped caller in ``scripts/download_dataset.py`` passes a
    repository-relative path (``data/raw/<name>``), so provenance stays
    portable. This function does not rewrite absolute paths to relative; callers
    that pass an absolute path get an absolute ``file`` value. The record is a
    local reproducibility trail (the data dir is gitignored); the authoritative
    pin stays in ``sources.py``.
    """

    record = {
        "dataset_id": source.dataset_id,
        "source_url": source.url,
        "sha256": source.sha256,
        "n_bytes": source.n_bytes,
        "n_rows": source.n_rows,
        "file": str(dest),
        "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return out_path
