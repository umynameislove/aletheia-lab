"""Internal upload record decoding and safe staging outcomes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from aletheia_lab.hosted_contract.uploads import (
    MAX_PART_BYTES,
    MAX_PARTS,
    MAX_UPLOAD_BYTES,
    UploadManifest,
    UploadStatus,
)

__all__ = ["MAX_PART_BYTES", "MAX_PARTS", "MAX_UPLOAD_BYTES", "UploadManifest", "UploadStatus", "UploadRecord", "UploadRejected"]


class UploadRejected(ValueError):
    def __init__(self, code: str, status: int, detail: str) -> None:
        self.code, self.status, self.detail = code, status, detail
        super().__init__(detail)


@dataclass(frozen=True)
class _ValidatedUpload:
    """Private reference to bytes already assembled, hash-checked and inspected.

    This is never accepted from a request and cannot carry caller SQL or a
    callback. Publication must verify the staged bytes still match this seal.
    """

    workspace_id: str
    upload_id: str
    byte_size: int
    sha256: str
    part_count: int


@dataclass(frozen=True)
class UploadRecord:
    state: Literal["active", "ready", "cancelled", "expired"]
    manifest: UploadManifest
    expires_at: datetime
    source_id: str | None
    expired: bool
    cleaned: bool

    @classmethod
    def from_row(cls, row: tuple[object, ...]) -> UploadRecord:
        if (len(row) != 7 or row[1] not in ("active", "ready", "cancelled", "expired")
                or not isinstance(row[2], str) or not isinstance(row[3], datetime)
                or row[3].utcoffset() is None or (row[4] is not None and not isinstance(row[4], str))
                or type(row[5]) is not bool or type(row[6]) is not bool):
            raise RuntimeError("Invalid upload staging record")
        return cls(row[1],
                   UploadManifest.model_validate_json(row[2]), row[3], row[4], row[5], row[6])
