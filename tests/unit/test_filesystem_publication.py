"""Cross-platform staged-directory publication regression tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from aletheia_lab import filesystem as filesystem_module
from aletheia_lab.filesystem import (
    ImmutablePublicationConflictError,
    ImmutablePublicationIntegrityError,
    publish_immutable_file,
    publish_staged_directory,
)


def test_immutable_file_publication_is_create_only_and_idempotent(tmp_path: Path) -> None:
    destination = tmp_path / "objects" / "payload.json"

    assert publish_immutable_file(destination, b'{"value":1}\n') == "created"
    assert publish_immutable_file(destination, b'{"value":1}\n') == "identical"
    assert destination.read_bytes() == b'{"value":1}\n'
    assert not tuple(destination.parent.glob("*.stage"))


def test_immutable_file_publication_rejects_conflicting_bytes(tmp_path: Path) -> None:
    destination = tmp_path / "receipt.json"
    publish_immutable_file(destination, b"first\n")

    with pytest.raises(ImmutablePublicationConflictError, match="non-identical"):
        publish_immutable_file(destination, b"second\n")

    assert destination.read_bytes() == b"first\n"
    assert not tuple(tmp_path.glob("*.stage"))


def test_concurrent_identical_publishers_converge_without_replacement(tmp_path: Path) -> None:
    destination = tmp_path / "objects" / "same"

    with ThreadPoolExecutor(max_workers=8) as pool:
        dispositions = tuple(
            pool.map(
                lambda _index: publish_immutable_file(destination, b"same bytes\n"),
                range(24),
            )
        )

    assert dispositions.count("created") == 1
    assert dispositions.count("identical") == 23
    assert destination.read_bytes() == b"same bytes\n"
    assert not tuple(destination.parent.glob("*.stage"))


def test_file_publication_failure_removes_the_sibling_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "receipt.json"

    def interrupt(*_args: object, **_kwargs: object) -> None:
        raise OSError("synthetic interrupted publication")

    monkeypatch.setattr(filesystem_module.os, "link", interrupt)

    with pytest.raises(OSError, match="synthetic interrupted"):
        publish_immutable_file(destination, b"terminal\n")

    assert not destination.exists()
    assert not tuple(tmp_path.glob("*.stage"))


def test_post_link_byte_drift_is_detected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "receipt.json"

    def publish_wrong_bytes(_stage: str, final: str, **_kwargs: object) -> None:
        Path(final).write_bytes(b"wrong\n")

    monkeypatch.setattr(filesystem_module.os, "link", publish_wrong_bytes)

    with pytest.raises(ImmutablePublicationIntegrityError, match="differ"):
        publish_immutable_file(destination, b"expected\n")


def test_windows_file_publication_retries_bounded_access_denials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "receipt.json"
    attempts = 0
    delays: list[float] = []

    def deny(*_args: object, **_kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        raise PermissionError("persistent synthetic Windows denial")

    monkeypatch.setattr(filesystem_module, "_IS_WINDOWS", True)
    monkeypatch.setattr(filesystem_module.os, "link", deny)
    monkeypatch.setattr(filesystem_module.time, "sleep", delays.append)

    with pytest.raises(PermissionError, match="persistent synthetic"):
        publish_immutable_file(destination, b"receipt\n")

    assert attempts == len(filesystem_module._WINDOWS_DIRECTORY_PUBLISH_DELAYS) + 1
    assert delays == list(filesystem_module._WINDOWS_DIRECTORY_PUBLISH_DELAYS)
    assert not destination.exists()
    assert not tuple(tmp_path.glob("*.stage"))


def test_publication_requires_a_real_staged_directory(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.write_text("not a directory\n", encoding="utf-8")

    with pytest.raises(NotADirectoryError, match="not a real directory"):
        publish_staged_directory(stage, tmp_path / "published")


def test_publication_requires_one_parent_volume(tmp_path: Path) -> None:
    stage_parent = tmp_path / "stage-parent"
    destination_parent = tmp_path / "destination-parent"
    stage_parent.mkdir()
    destination_parent.mkdir()
    stage = stage_parent / "stage"
    stage.mkdir()

    with pytest.raises(ValueError, match="one parent volume"):
        publish_staged_directory(stage, destination_parent / "published")


def test_publication_refuses_an_existing_destination(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    destination = tmp_path / "published"
    stage.mkdir()
    destination.mkdir()

    with pytest.raises(FileExistsError, match="refusing to replace"):
        publish_staged_directory(stage, destination)


def test_windows_publication_fails_after_bounded_access_denials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stage = tmp_path / "stage"
    destination = tmp_path / "published"
    stage.mkdir()
    attempts = 0
    delays: list[float] = []

    def deny(_stage: Path, _destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise PermissionError("persistent synthetic Windows denial")

    monkeypatch.setattr(filesystem_module, "_IS_WINDOWS", True)
    monkeypatch.setattr(filesystem_module.os, "rename", deny)
    monkeypatch.setattr(filesystem_module.time, "sleep", delays.append)

    with pytest.raises(PermissionError, match="persistent synthetic"):
        publish_staged_directory(stage, destination)

    assert attempts == len(filesystem_module._WINDOWS_DIRECTORY_PUBLISH_DELAYS) + 1
    assert delays == list(filesystem_module._WINDOWS_DIRECTORY_PUBLISH_DELAYS)
    assert stage.is_dir()
    assert not destination.exists()
