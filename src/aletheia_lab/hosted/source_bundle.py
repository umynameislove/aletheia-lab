"""Bounded ZIP inspection for hosted sources; no extraction or authorization.

The HTTP/storage owner must bound upload size before buffering, authorize the
workspace, and store accepted immutable bytes before issuing a source receipt.
"""
from __future__ import annotations

import io
import re
import stat
import struct
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass
from typing import Any, Protocol, cast

from aletheia_lab.hosted.content_hashing import content_hasher

MAXIMUM_UPLOAD_BYTES = 200 * 1024 * 1024
MAXIMUM_EXPANDED_BYTES = 512 * 1024 * 1024
MAXIMUM_ENTRIES = 100_000
MAXIMUM_PATH_DEPTH = 32


class SourceRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("Source bundle could not be accepted.")


@dataclass(frozen=True)
class SourceEntry:
    path: str
    byte_size: int
    sha256: str


class _ZipMetadata(Protocol):
    _ECD_DISK_NUMBER: int
    _ECD_DISK_START: int
    _ECD_ENTRIES_THIS_DISK: int
    _ECD_ENTRIES_TOTAL: int
    _ECD_COMMENT_SIZE: int
    _ECD_COMMENT: int
    _ECD_OFFSET: int
    _ECD_SIZE: int
    _ECD_SIGNATURE: int
    _ECD_LOCATION: int
    stringEndArchive64: bytes
    stringEndArchive: bytes
    sizeEndCentDir64: int
    sizeEndCentDir64Locator: int
    sizeEndCentDir: int
    sizeCentralDir: int
    structCentralDir: str
    _CD_FILENAME_LENGTH: int
    _CD_SIGNATURE: int
    stringCentralDir: bytes
    _CD_EXTRA_FIELD_LENGTH: int
    _CD_COMMENT_LENGTH: int

    def _EndRecData(self, source: io.BytesIO) -> list[Any] | None: ...


# Typeshed omits this private stdlib metadata surface; no ZIP definitions are copied.
_ZIP_METADATA = cast(_ZipMetadata, zipfile)


def _preflight_directory(data: bytes, maximum_entries: int) -> None:
    """Use stdlib ZIP metadata definitions to bound allocation before ZipFile."""
    end = _ZIP_METADATA._EndRecData(io.BytesIO(data))
    if (end is None or end[_ZIP_METADATA._ECD_DISK_NUMBER] != 0 or end[_ZIP_METADATA._ECD_DISK_START] != 0
            or end[_ZIP_METADATA._ECD_ENTRIES_THIS_DISK] != end[_ZIP_METADATA._ECD_ENTRIES_TOTAL]
            or not 0 < end[_ZIP_METADATA._ECD_ENTRIES_TOTAL] <= maximum_entries
            or end[_ZIP_METADATA._ECD_COMMENT_SIZE] != len(end[_ZIP_METADATA._ECD_COMMENT])):
        raise SourceRejected()
    start = end[_ZIP_METADATA._ECD_OFFSET]
    stop = start + end[_ZIP_METADATA._ECD_SIZE]
    if start < 0 or not start < stop <= len(data):
        raise SourceRejected()
    _validate_directory_footer(data, end, stop)
    position = start
    count = 0
    while position < stop:
        if count >= maximum_entries or stop - position < _ZIP_METADATA.sizeCentralDir:
            raise SourceRejected()
        row = struct.unpack_from(_ZIP_METADATA.structCentralDir, data, position)
        name_size = row[_ZIP_METADATA._CD_FILENAME_LENGTH]
        if row[_ZIP_METADATA._CD_SIGNATURE] != _ZIP_METADATA.stringCentralDir or not 0 < name_size <= 1024:
            raise SourceRejected()
        position += (_ZIP_METADATA.sizeCentralDir + name_size
                     + row[_ZIP_METADATA._CD_EXTRA_FIELD_LENGTH] + row[_ZIP_METADATA._CD_COMMENT_LENGTH])
        if position > stop:
            raise SourceRejected()
        count += 1
    if count != end[_ZIP_METADATA._ECD_ENTRIES_TOTAL]:
        raise SourceRejected()


def inspect_source_bundle(data: bytes, *, maximum_upload_bytes: int,
                          maximum_expanded_bytes: int, maximum_entries: int,
                          maximum_path_depth: int = MAXIMUM_PATH_DEPTH) -> tuple[SourceEntry, ...]:
    _validate_bundle_input(data, maximum_upload_bytes, maximum_expanded_bytes, maximum_entries, maximum_path_depth)
    try:
        _preflight_directory(data, maximum_entries)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > maximum_entries:
                raise SourceRejected()
            spellings: dict[str, str] = {}
            names: set[str] = set()
            files: set[str] = set()
            directories: set[str] = set()
            result = []
            total = 0
            for entry in entries:
                name = entry.filename
                parts = name.rstrip("/").split("/")
                _validate_entry(entry, name, parts, maximum_path_depth)
                key = _register_entry_path(name, parts, entry, spellings, names, files, directories)
                if entry.is_dir():
                    if key in files:
                        raise SourceRejected()
                    directories.add(key)
                    if entry.file_size:
                        raise SourceRejected()
                    continue
                files.add(key)
                total += entry.file_size
                if total > maximum_expanded_bytes:
                    raise SourceRejected()
                result.append(_read_entry(archive, entry, maximum_expanded_bytes))
            if not result:
                raise SourceRejected()
            return tuple(result)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError, EOFError,
            UnicodeError, ValueError, struct.error, zlib.error):
        raise SourceRejected() from None
def _validate_directory_footer(data: bytes, end: list[Any], stop: int) -> None:
    footer = stop
    if end[_ZIP_METADATA._ECD_SIGNATURE] == _ZIP_METADATA.stringEndArchive64:
        if data[footer:footer + 4] != _ZIP_METADATA.stringEndArchive64:
            raise SourceRejected()
        footer += _ZIP_METADATA.sizeEndCentDir64 + _ZIP_METADATA.sizeEndCentDir64Locator
        if end[_ZIP_METADATA._ECD_LOCATION] not in (stop, footer):
            raise SourceRejected()
    elif end[_ZIP_METADATA._ECD_LOCATION] != stop:
        raise SourceRejected()
    if (data[footer:footer + 4] != _ZIP_METADATA.stringEndArchive
            or footer + _ZIP_METADATA.sizeEndCentDir + len(end[_ZIP_METADATA._ECD_COMMENT]) != len(data)):
        raise SourceRejected()

def _validate_entry(entry: zipfile.ZipInfo, name: str, parts: list[str], maximum_path_depth: int) -> None:
    mode = entry.external_attr >> 16
    # These legacy Unix extra fields can carry link targets.
    extra = entry.extra
    while extra:
        if len(extra) < 4:
            raise SourceRejected()
        kind, size = struct.unpack_from("<HH", extra)
        if size > len(extra) - 4 or kind in (0x000D, 0x756E):
            raise SourceRejected()
        extra = extra[4 + size:]
    if (not name or entry.orig_filename != name or len(name) > 1024 or len(parts) > maximum_path_depth
            or name.startswith("/") or "\\" in name
            or any(part in ("", ".", "..") for part in parts)
            or any(part.casefold() == ".git" for part in parts)
            or any(re.search(r'[\x00-\x1f\x7f:<>"|?*]', part) for part in parts)
            or any(part.endswith((" ", ".")) for part in parts)
            or any(re.fullmatch(r"(?i)(con|prn|aux|nul|conin\$|conout\$|com[1-9Â¹Â²Â³]|lpt[1-9Â¹Â²Â³])(\..*)?", part) for part in parts)
            or entry.flag_bits & 1 or stat.S_ISLNK(mode)
            or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
            or (stat.S_ISDIR(mode) and not entry.is_dir())
            or (stat.S_ISREG(mode) and entry.is_dir())
            or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))):
        raise SourceRejected()

def _register_entry_path(name: str, parts: list[str], entry: zipfile.ZipInfo,
                         spellings: dict[str, str], names: set[str],
                         files: set[str], directories: set[str]) -> str:
    key = unicodedata.normalize("NFC", name.rstrip("/")).casefold()
    for index in range(1, len(parts) + 1):
        spelling = "/".join(parts[:index])
        spelling.encode("utf-8", errors="strict")
        folded = unicodedata.normalize("NFC", spelling).casefold()
        if folded in spellings and spellings[folded] != spelling:
            raise SourceRejected()
        spellings[folded] = spelling
    if key in names:
        raise SourceRejected()
    parents = {unicodedata.normalize("NFC", "/".join(parts[:index])).casefold()
               for index in range(1, len(parts))}
    if parents & files or (not entry.is_dir() and key in directories):
        raise SourceRejected()
    directories.update(parents)
    names.add(key)
    return key

def _read_entry(archive: zipfile.ZipFile, entry: zipfile.ZipInfo, maximum_expanded_bytes: int) -> SourceEntry:
    digest = content_hasher()
    actual = 0
    with archive.open(entry) as source:
        while chunk := source.read(65536):
            actual += len(chunk)
            if actual > entry.file_size or actual > maximum_expanded_bytes:
                raise SourceRejected()
            digest.update(chunk)
    if actual != entry.file_size:
        raise SourceRejected()
    return SourceEntry(entry.filename, actual, digest.hexdigest())

def _validate_bundle_input(data: bytes, maximum_upload_bytes: int, maximum_expanded_bytes: int,
                           maximum_entries: int, maximum_path_depth: int) -> None:
    budgets = (maximum_upload_bytes, maximum_expanded_bytes, maximum_entries, maximum_path_depth)
    if any(type(value) is not int or value <= 0 for value in budgets):
        raise ValueError("positive source budgets required")
    if (not isinstance(data, bytes) or not data or len(data) > maximum_upload_bytes
            or not data.startswith((b"PK\x03\x04", b"PK\x05\x06"))):
        raise SourceRejected()

