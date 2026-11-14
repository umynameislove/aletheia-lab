"""Filesystem census primitives used by the read-only project importer."""
from __future__ import annotations

import os
import stat
from pathlib import Path


def _is_reparse_point(stat_result: os.stat_result) -> bool:
    file_attributes = int(getattr(stat_result, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return bool(reparse_flag and file_attributes & reparse_flag)


def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_size),
        int(value.st_mtime_ns),
    )


def _directory_signature(value: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_mtime_ns),
    )


def _directory_entry_names(entries: list[os.DirEntry[str]]) -> tuple[bytes, ...]:
    """Return a lossless, ordering-independent directory membership snapshot."""

    return tuple(sorted(os.fsencode(entry.name) for entry in entries))


def _scan_directory(directory: Path) -> tuple[list[os.DirEntry[str]], tuple[bytes, ...]]:
    """Enumerate one directory without following or inspecting its children."""

    with os.scandir(directory) as iterator:
        entries = list(iterator)
    return entries, _directory_entry_names(entries)


def _contained(root: Path, candidate: Path) -> bool:
    try:
        root_text = os.path.normcase(os.path.realpath(os.fspath(root)))
        candidate_text = os.path.normcase(os.path.realpath(os.fspath(candidate)))
        return os.path.commonpath((root_text, candidate_text)) == root_text
    except (OSError, ValueError):
        return False

