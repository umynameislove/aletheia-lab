"""Cross-platform staged-directory publication regression tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from aletheia_lab import filesystem as filesystem_module
from aletheia_lab.filesystem import publish_staged_directory


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
