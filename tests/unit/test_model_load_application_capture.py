from __future__ import annotations

import importlib
from functools import wraps
from pathlib import Path
from typing import Any

import joblib
import pytest

from aletheia_lab.evaluation.model_load_application_capture import (
    MAX_ARTIFACT_BYTES,
    NativeCapture,
    descriptor_bytes,
)
from aletheia_lab.project.identity import content_sha256


def test_same_original_stream_and_arguments_are_delegated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "owned.joblib"
    joblib.dump({"value": 3}, path, compress=0)
    capture = NativeCapture(tmp_path)
    capture.scope = "load-1"
    module = importlib.import_module("joblib.numpy_pickle")
    original = module._unpickle
    seen = []

    @wraps(original)
    def checked(stream: Any, *args: Any, **kwargs: Any) -> Any:
        seen.append((id(stream), stream.tell(), stream.name))
        assert stream.tell() == 0
        assert Path(stream.name) == path
        return original(stream, *args, **kwargs)

    monkeypatch.setattr(module, "_unpickle", checked)
    with capture.installed():
        assert joblib.load(path) == {"value": 3}
    assert len(seen) == 1 and module._unpickle is checked
    assert capture.receipts == [
        {"scope": "load-1", "descriptor_sha256": content_sha256(path.read_bytes())}
    ]
    assert bytes.fromhex(capture.reference[0]["raw_hex"]) == path.read_bytes()


def test_open_descriptor_not_restored_path_is_the_witness(tmp_path: Path) -> None:
    if not hasattr(__import__("os"), "pread"):
        pytest.skip("POSIX held-descriptor replacement control")
    path, replacement = tmp_path / "current", tmp_path / "replacement"
    path.write_bytes(b"old")
    replacement.write_bytes(b"new")
    with path.open("rb") as stream:
        replacement.replace(path)
        assert descriptor_bytes(stream, tmp_path) == b"old"
        assert path.read_bytes() == b"new" and stream.tell() == 0


@pytest.mark.parametrize("problem", ["foreign", "empty", "oversize", "offset"])
def test_unsupported_descriptor_fails_closed(tmp_path: Path, problem: str) -> None:
    path = tmp_path / "input"
    path.write_bytes(b"x" * (MAX_ARTIFACT_BYTES + 1) if problem == "oversize" else b"abc")
    if problem == "empty":
        path.write_bytes(b"")
    with path.open("rb") as stream:
        if problem == "offset":
            stream.seek(1)
        with pytest.raises(ValueError):
            descriptor_bytes(stream, tmp_path / "other" if problem == "foreign" else tmp_path)


def test_hook_restored_on_exception_and_nested_capture_rejected(tmp_path: Path) -> None:
    module = importlib.import_module("joblib.numpy_pickle")
    original = module._unpickle
    with pytest.raises(ValueError, match="deliberate"), NativeCapture(tmp_path).installed():
        with pytest.raises(RuntimeError, match="concurrent"), NativeCapture(tmp_path).installed():
            pass
        raise ValueError("deliberate")
    assert module._unpickle is original


def test_unscoped_load_fails_without_deserialization(tmp_path: Path) -> None:
    path = tmp_path / "input"
    joblib.dump({"a": 1}, path)
    with NativeCapture(tmp_path).installed(), pytest.raises(RuntimeError, match="unscoped"):
        joblib.load(path)


def test_positional_byte_order_flag_is_not_mmap_mode(tmp_path: Path) -> None:
    path = tmp_path / "input"
    joblib.dump({"value": 3}, path)
    capture = NativeCapture(tmp_path)
    capture.scope = "positional"
    module = importlib.import_module("joblib.numpy_pickle")
    with capture.installed(), path.open("rb") as stream:
        assert module._unpickle(stream, True, str(path), None) == {"value": 3}
    assert len(capture.receipts) == 1


@pytest.mark.parametrize("compress", [3, ("gzip", 3), ("bz2", 3), ("xz", 3)])
def test_compressed_stream_is_not_a_native_descriptor(tmp_path: Path, compress: Any) -> None:
    path = tmp_path / "input"
    joblib.dump({"value": 3}, path, compress=compress)
    capture = NativeCapture(tmp_path)
    capture.scope = "compressed"
    with capture.installed(), pytest.raises(ValueError, match="unsupported"):
        joblib.load(path)
    assert capture.receipts == []


def test_buffered_read_falsifier_limits_snapshot_claim(tmp_path: Path) -> None:
    """Out-of-model same-inode mutation is NOT detected by this observer."""
    if not hasattr(__import__("os"), "pread"):
        pytest.skip("held-descriptor buffered-read counterexample requires POSIX pread")
    path, other = tmp_path / "input", tmp_path / "other"
    joblib.dump({"value": "A"}, path)
    joblib.dump({"value": "B"}, other)
    alternate = other.read_bytes()
    assert len(path.read_bytes()) == len(alternate)
    capture = NativeCapture(tmp_path)
    capture.scope = "out-of-model"

    def mutate_same_inode(_: Any) -> None:
        path.write_bytes(alternate)

    with capture.installed(on_open=mutate_same_inode):
        loaded = joblib.load(path)
    assert loaded == {"value": "A"}
    assert bytes.fromhex(capture.reference[0]["raw_hex"]) == alternate
