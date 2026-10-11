"""Guard tests never execute the selected identity comparisons."""

import json
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation import serving_identity_native as native
from aletheia_lab.evaluation.serving_identity_native import checked_loader


def test_public_source_execution_rejects_unknown_name(tmp_path: Path) -> None:
    source = tmp_path / "untrusted.py"
    source.write_text("raise RuntimeError('must not execute')")
    with pytest.raises(ValueError, match="pinned regular"):
        checked_loader(source)


def test_public_source_execution_rejects_changed_bytes(tmp_path: Path) -> None:
    source = tmp_path / "affected-src-_bentoml_impl-loader.py"
    source.write_text("raise RuntimeError('must not execute')")
    with pytest.raises(ValueError, match="accepted source pin"):
        checked_loader(source)


def test_public_source_execution_rejects_symbolic_link(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    target.write_text("raise RuntimeError('must not execute')")
    source = tmp_path / "affected-src-_bentoml_impl-loader.py"
    source.symlink_to(target)
    with pytest.raises(ValueError, match="pinned regular"):
        checked_loader(source)


def test_unexpected_trajectory_failure_retains_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "affected-src-_bentoml_impl-loader.py"
    source.write_text("public source stand-in never executed")
    fixture_root = tmp_path / "fixtures"
    output = tmp_path / "worker"
    monkeypatch.setattr(native, "_load_fixtures", lambda _: ({}, {"files": []}))
    monkeypatch.setattr(native, "checked_loader", lambda _: object())

    def fail(
        loader: Any,
        manifest: Any,
        destination: Any,
        requests: Any,
        operations: Any,
        progress: Any,
    ) -> None:
        requests.append({"slot": "t0", "closed": True})
        operations.append({"operation": "completed_native_control"})
        progress["entered_slots"].extend(["t0", "t1"])
        progress["active_slot"] = "t1"
        raise RuntimeError("controlled unexpected failure")

    monkeypatch.setattr(native, "_trajectory", fail)
    with pytest.raises(RuntimeError, match="controlled unexpected failure"):
        native.native_worker(source, fixture_root, output)
    retained = json.loads((output / "native-results.json").read_text())
    assert retained["terminal"] == "failed"
    assert retained["requests"] == [{"slot": "t0", "closed": True}]
    assert retained["operations"] == [{"operation": "completed_native_control"}]
    assert retained["entered_slots"] == ["t0", "t1"]
    assert retained["unattempted_slots"] == list(native.SLOTS[2:])
    assert retained["terminal_error"]["type"] == "RuntimeError"


def test_fixture_manifest_rejects_paths_outside_owned_root(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    info = {
        "owned_state": native.OWNED_STATES["A"],
        "package": str(tmp_path / "outside"),
    }
    manifest = {
        "tag": native.TAG,
        "input": native.INPUT,
        "models": {"A": info, "B": {}},
        "package_closure": {"files": []},
    }
    (root / "fixtures.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="inside the fixture root"):
        native._load_fixtures(root)
