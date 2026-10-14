"""Local artifact boundaries and pinned replay; no model or registered runs."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aletheia_lab.diagnosis import study_artifacts
from aletheia_lab.diagnosis.study_artifacts import (
    checked_output,
    read_artifact,
    windows_io_path,
    write_artifact,
)
from aletheia_lab.project.identity import content_sha256

ROOT = Path(__file__).resolve().parents[2]


def test_unicode_long_output_keeps_exact_bytes_and_refuses_replacement(tmp_path):
    supplied = (
        tmp_path
        / "người dùng"
        / ("deep-" * 10)
        / ("nested-" * 10)
        / ("owned-" * 10)
        / "result.json"
    )
    payload = {"result": "không rõ", "missing": None}
    write_artifact(supplied, ROOT, payload)
    value, raw = read_artifact(supplied)
    assert value == payload and raw.endswith(b"\n")
    assert len(str(supplied)) > 260
    with pytest.raises(ValueError, match="new file"):
        write_artifact(supplied, ROOT, {"changed": True})
    assert read_artifact(supplied)[1] == raw
    assert windows_io_path(supplied).is_file()


@pytest.mark.parametrize(
    "text", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}', "[]"]
)
def test_ambiguous_json_never_enters_replay(tmp_path, text):
    source = tmp_path / "source.json"
    source.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        read_artifact(source)


def test_input_byte_cap_is_enforced_before_parse(tmp_path, monkeypatch):
    monkeypatch.setattr(study_artifacts, "MAX_ARTIFACT_BYTES", 8)
    path = tmp_path / "oversized.json"
    path.write_bytes(b'{"large":123456789}')
    with pytest.raises(ValueError, match="byte limit"):
        read_artifact(path)


def test_output_cannot_use_dotdot_to_enter_repository(tmp_path):
    repo, sibling = tmp_path / "repo", tmp_path / "outside"
    repo.mkdir()
    sibling.mkdir()
    with pytest.raises(ValueError, match="outside"):
        checked_output(sibling / ".." / "repo" / "private.json", repo)
    assert not (repo / "private.json").exists()


@pytest.mark.parametrize("kind", ["input", "parent", "dangling"])
def test_symlink_aliases_do_not_grant_artifact_authority(tmp_path, make_symlink, kind):
    owned = tmp_path / "owned"
    owned.mkdir()
    file = owned / "source.json"
    file.write_bytes(b"{}")
    if kind == "input":
        link = tmp_path / "alias.json"
        make_symlink(link, file)
        with pytest.raises(ValueError, match="symbolic link"):
            read_artifact(link)
    else:
        link = tmp_path / "alias"
        make_symlink(link, owned if kind == "parent" else tmp_path / "absent")
        with pytest.raises(ValueError, match="symbolic link"):
            write_artifact(link / "out.json", ROOT, {"success": True})
    assert file.read_bytes() == b"{}"


@pytest.mark.parametrize(
    "absolute,expected",
    [
        ("C:\\owned", "\\\\?\\C:\\owned"),
        ("\\\\server\\share\\owned", "\\\\?\\UNC\\server\\share\\owned"),
        ("\\\\?\\C:\\owned", "\\\\?\\C:\\owned"),
    ],
)
def test_windows_drive_unc_and_existing_namespace(monkeypatch, absolute, expected):
    monkeypatch.setattr(study_artifacts, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(study_artifacts.os.path, "abspath", lambda _path: absolute)
    assert str(windows_io_path(Path("owned"))) == expected


@pytest.mark.parametrize("mode", ["missing", "wrong_pin", "malformed"])
def test_cli_errors_do_not_echo_private_paths_or_create_output(tmp_path, monkeypatch, capsys, mode):
    entrypoint = runpy.run_path(str(ROOT / "scripts/active_diagnosis_study.py"))
    source = tmp_path / "SYNTHETIC_PRIVATE_SOURCE" / "source.json"
    if mode != "missing":
        source.parent.mkdir()
        source.write_bytes(b"{}")
    output = tmp_path / "not-created" / "result.json"
    pin = content_sha256(b"{}") if mode == "malformed" else "f" * 64
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "study",
            "analyze",
            "--input",
            str(source),
            "--expected-input-sha256",
            pin,
            "--output",
            str(output),
        ],
    )
    assert entrypoint["main"]() == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "failed_closed"
    assert "SYNTHETIC_PRIVATE" not in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err
    assert not output.parent.exists()
