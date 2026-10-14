"""Cross-platform staged-directory publication regression tests."""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from aletheia_lab import filesystem as filesystem_module
from aletheia_lab.filesystem import (
    ImmutablePublicationConflictError,
    ImmutablePublicationIntegrityError,
    publish_immutable_file,
    publish_staged_directory,
    windows_io_path,
    write_new_file,
)


@pytest.mark.parametrize(
    "absolute,expected",
    [
        ("C:\\store", "\\\\?\\C:\\store"),
        ("D:\\a space\\café", "\\\\?\\D:\\a space\\café"),
        ("\\\\server\\share\\store", "\\\\?\\UNC\\server\\share\\store"),
        ("\\\\?\\C:\\store", "\\\\?\\C:\\store"),
        ("\\\\?\\UNC\\server\\share\\store", "\\\\?\\UNC\\server\\share\\store"),
    ],
)
def test_windows_io_namespace_uses_absolute_paths_without_resolving_links(
    monkeypatch: pytest.MonkeyPatch, absolute: str, expected: str,
) -> None:
    supplied = Path("relative") / ".." / "store"
    calls: list[Path] = []

    def abspath(path: Path) -> str:
        calls.append(path)
        return absolute

    monkeypatch.setattr(filesystem_module, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(filesystem_module.os.path, "abspath", abspath)
    assert str(windows_io_path(supplied)) == expected
    assert calls == [supplied]


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_non_windows_io_paths_keep_the_supplied_spelling(monkeypatch, platform) -> None:
    monkeypatch.setattr(filesystem_module, "sys", SimpleNamespace(platform=platform))
    supplied = Path("relative") / ".." / "store"
    assert windows_io_path(supplied) is supplied


def test_tree_snapshot_reads_deep_unicode_paths_without_namespace_in_keys(
    tmp_path: Path,
    file_tree_snapshot: Callable[[Path], dict[Path, bytes]],
) -> None:
    supplied = tmp_path / "người dùng" / ("deep-" * 10) / ("owned-" * 10) / ("nested-" * 10)
    io_root = windows_io_path(supplied)
    request = "a" * 64
    relative = Path("requests") / request / "requests" / request / "ledger" / "000001.json"
    expected = {relative: b'{"state":"terminal"}\n', Path("payload.bin"): b"\x00\xff\r\n"}
    for name, payload in expected.items():
        write_new_file(io_root / name, payload)
    assert len(str(io_root / relative)) > 400

    assert file_tree_snapshot(supplied) == expected
    assert file_tree_snapshot(io_root) == expected
    assert all(not key.is_absolute() for key in file_tree_snapshot(supplied))


@pytest.mark.parametrize("mutation", ["bytes", "missing", "additional"])
def test_tree_snapshot_detects_byte_and_membership_changes(
    tmp_path: Path,
    file_tree_snapshot: Callable[[Path], dict[Path, bytes]],
    mutation: str,
) -> None:
    io_root = windows_io_path(tmp_path)
    target = io_root / "nested" / "payload.json"
    write_new_file(target, b"original\n")
    before = file_tree_snapshot(tmp_path)
    if mutation == "bytes":
        target.write_bytes(b"changed\n")
    elif mutation == "missing":
        target.unlink()
    else:
        write_new_file(io_root / "unexpected.json", b"extra\n")

    assert before == {Path("nested/payload.json"): b"original\n"}
    assert before != file_tree_snapshot(tmp_path)


def test_tree_snapshot_rejects_missing_or_non_directory_roots(
    tmp_path: Path,
    file_tree_snapshot: Callable[[Path], dict[Path, bytes]],
) -> None:
    with pytest.raises(FileNotFoundError):
        file_tree_snapshot(tmp_path / "missing")
    target = tmp_path / "not-a-directory"
    target.write_bytes(b"file")
    with pytest.raises(ValueError, match="real directory"):
        file_tree_snapshot(target)


@pytest.mark.parametrize("error", [FileNotFoundError, PermissionError])
def test_tree_snapshot_propagates_directory_scan_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    file_tree_snapshot: Callable[[Path], dict[Path, bytes]],
    error: type[OSError],
) -> None:
    blocked = windows_io_path(tmp_path / "blocked")
    blocked.mkdir()
    write_new_file(blocked / "payload.json", b"must not silently disappear")
    original_scandir = os.scandir

    def fail_owned_scan(path):
        if path == blocked:
            raise error("synthetic owned scan failure")
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", fail_owned_scan)
    with pytest.raises(error, match="owned scan failure"):
        file_tree_snapshot(tmp_path)


def test_tree_snapshot_propagates_payload_read_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    file_tree_snapshot: Callable[[Path], dict[Path, bytes]],
) -> None:
    target = windows_io_path(tmp_path / "payload.json")
    write_new_file(target, b"must not silently disappear")
    original_read = Path.read_bytes

    def fail_owned_read(path):
        if path == target:
            raise PermissionError("synthetic owned read failure")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", fail_owned_read)
    with pytest.raises(PermissionError, match="owned read failure"):
        file_tree_snapshot(tmp_path)


@pytest.mark.parametrize("kind", ["root", "directory", "file"])
def test_tree_snapshot_rejects_symlink_aliases(
    tmp_path: Path,
    make_symlink: Callable[[Path, str | Path], None],
    file_tree_snapshot: Callable[[Path], dict[Path, bytes]],
    kind: str,
) -> None:
    target = tmp_path / "owned"
    target.mkdir()
    payload = target / "payload.json"
    payload.write_bytes(b"owned")
    root = tmp_path / "snapshot"
    if kind == "root":
        make_symlink(root, target)
        message = "real directory"
    else:
        root.mkdir()
        make_symlink(root / "alias", target if kind == "directory" else payload)
        message = "real files or directories"
    with pytest.raises(ValueError, match=message):
        file_tree_snapshot(root)


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
