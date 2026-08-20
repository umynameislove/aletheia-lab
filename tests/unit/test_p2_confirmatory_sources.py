"""Checksum-first external-source acquisition tests."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from aletheia_lab.benchmark.p2.confirmatory_execution import ConfirmatoryExecutionError
from aletheia_lab.benchmark.p2.confirmatory_protocol import (
    DatasetBinding,
    load_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.confirmatory_sources import (
    download_registered_archive,
    extract_registered_snapshot,
    sha256_file,
)


def _archive(tmp_path: Path, *, member: str, content: bytes) -> Path:
    path = tmp_path / "bank.zip"
    nested = tmp_path / "bank-additional.zip"
    with zipfile.ZipFile(nested, "w") as bundle:
        bundle.writestr(member, content)
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.write(nested, arcname="bank-additional.zip")
    return path


def _binding(archive: Path, snapshot: bytes) -> DatasetBinding:
    original = load_confirmatory_protocol().datasets[1]
    return DatasetBinding.model_validate(
        {
            **original.model_dump(),
            "archive_sha256": sha256_file(archive),
            "snapshot_sha256": hashlib.sha256(snapshot).hexdigest(),
        }
    )


def test_extracts_only_the_exact_pinned_bank_snapshot(tmp_path: Path) -> None:
    content = b"age;duration;y\n30;10;yes\n40;20;no\n"
    archive = _archive(
        tmp_path,
        member="bank-additional/bank-additional-full.csv",
        content=content,
    )
    destination = tmp_path / "out" / "bank.csv"

    result = extract_registered_snapshot(
        dataset=_binding(archive, content),
        archive_path=archive,
        destination=destination,
    )

    assert result.read_bytes() == content
    assert (
        extract_registered_snapshot(
            dataset=_binding(archive, content),
            archive_path=archive,
            destination=destination,
        )
        == destination
    )


def test_extraction_rejects_wrong_member_and_snapshot_hash(tmp_path: Path) -> None:
    content = b"age;duration;y\n30;10;yes\n40;20;no\n"
    wrong_member = _archive(tmp_path, member="../bank.csv", content=content)
    with pytest.raises(ConfirmatoryExecutionError, match="absent"):
        extract_registered_snapshot(
            dataset=_binding(wrong_member, content),
            archive_path=wrong_member,
            destination=tmp_path / "missing.csv",
        )

    archive = _archive(
        tmp_path,
        member="bank-additional/bank-additional-full.csv",
        content=content,
    )
    binding = _binding(archive, b"different expected bytes")
    with pytest.raises(ConfirmatoryExecutionError, match="snapshot checksum"):
        extract_registered_snapshot(
            dataset=binding,
            archive_path=archive,
            destination=tmp_path / "wrong.csv",
        )


def test_existing_tampered_snapshot_is_never_overwritten(tmp_path: Path) -> None:
    content = b"age;duration;y\n30;10;yes\n40;20;no\n"
    archive = _archive(
        tmp_path,
        member="bank-additional/bank-additional-full.csv",
        content=content,
    )
    destination = tmp_path / "bank.csv"
    destination.write_bytes(b"tampered")

    with pytest.raises(ConfirmatoryExecutionError, match="existing"):
        extract_registered_snapshot(
            dataset=_binding(archive, content),
            archive_path=archive,
            destination=destination,
        )
    assert destination.read_bytes() == b"tampered"


def test_existing_archive_is_reused_only_when_checksum_matches(tmp_path: Path) -> None:
    content = b"age;duration;y\n30;10;yes\n40;20;no\n"
    archive = _archive(
        tmp_path,
        member="bank-additional/bank-additional-full.csv",
        content=content,
    )
    binding = _binding(archive, content)

    assert download_registered_archive(dataset=binding, destination=archive) == archive
    archive.write_bytes(b"tampered")
    with pytest.raises(ConfirmatoryExecutionError, match="download or checksum"):
        download_registered_archive(dataset=binding, destination=archive)
