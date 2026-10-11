"""Descriptor-boundary checks without fitting or unpickling any estimator."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aletheia_lab.evaluation import model_load_serving_capture as study
from aletheia_lab.project.identity import content_sha256


@pytest.fixture
def native_module(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[Any]]:
    calls: list[Any] = []

    def original(
        stream: Any,
        ensure_native_byte_order: bool = False,
        filename: str = "",
        mmap_mode: str | None = None,
    ) -> object:
        calls.append((stream, ensure_native_byte_order, filename, mmap_mode, stream.tell()))
        return stream

    module = SimpleNamespace(_unpickle=original)

    def import_module(name: str) -> Any:
        assert name == "joblib.numpy_pickle"
        return module

    monkeypatch.setattr(study, "importlib", SimpleNamespace(import_module=import_module))
    return module, calls


def test_original_descriptor_arguments_and_position_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_module: tuple[Any, list[Any]]
) -> None:
    module, calls = native_module
    original = module._unpickle
    path = tmp_path / "owned"
    path.write_bytes(b"owned stable artifact")
    capture = study.ServingCapture(tmp_path)
    capture.scope = "load-0"
    ticks = iter((10, 30, 37, 80))
    monkeypatch.setattr(study, "perf_counter_ns", lambda: next(ticks))
    with capture.installed(), path.open("rb") as stream:
        assert module._unpickle(stream, True, str(path), None) is stream
        assert calls == [(stream, True, str(path), None, 0)]
        assert stream.tell() == 0
    assert module._unpickle is original
    assert capture.events == [
        {
            "scope": "load-0",
            "digest": content_sha256(b"owned stable artifact"),
            "bytes": 21,
            "read_ns": 20,
            "hash_ns": 7,
            "reconstruct_ns": 43,
            "completed": True,
        }
    ]
    assert "raw_hex" not in capture.events[0]


def test_each_serial_load_uses_its_current_scope(
    tmp_path: Path, native_module: tuple[Any, list[Any]]
) -> None:
    module, calls = native_module
    capture = study.ServingCapture(tmp_path)
    path = tmp_path / "owned"
    path.write_bytes(b"owned")
    with capture.installed():
        for scope in ("load-0", "load-1"):
            capture.scope = scope
            with path.open("rb") as stream:
                module._unpickle(stream)
    assert [event["scope"] for event in capture.events] == ["load-0", "load-1"]
    assert len(calls) == 2


@pytest.mark.parametrize("positional", [False, True])
def test_mmap_rejected_before_snapshot_or_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    native_module: tuple[Any, list[Any]],
    positional: bool,
) -> None:
    module, calls = native_module
    capture = study.ServingCapture(tmp_path)
    capture.scope = "mapped"
    monkeypatch.setattr(study, "descriptor_bytes", lambda *_: pytest.fail("snapshot entered"))
    with capture.installed(), pytest.raises(ValueError, match="mmap"):
        if positional:
            module._unpickle(object(), False, "owned", "r")
        else:
            module._unpickle(object(), mmap_mode="r")
    assert capture.events == [] and calls == []


def test_missing_scope_fails_before_snapshot_or_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_module: tuple[Any, list[Any]]
) -> None:
    module, calls = native_module
    capture = study.ServingCapture(tmp_path)
    monkeypatch.setattr(study, "descriptor_bytes", lambda *_: pytest.fail("snapshot entered"))
    with capture.installed(), pytest.raises(RuntimeError, match="unscoped"):
        module._unpickle(object())
    assert capture.events == [] and calls == []


def test_other_thread_cannot_reconstruct(
    tmp_path: Path, native_module: tuple[Any, list[Any]]
) -> None:
    module, calls = native_module
    capture = study.ServingCapture(tmp_path)
    capture.scope = "load"
    with capture.installed(), ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(module._unpickle, object())
        with pytest.raises(RuntimeError, match="concurrent"):
            result.result(timeout=1)
    assert capture.events == [] and calls == []


@pytest.mark.parametrize("problem", ["offset", "foreign", "empty", "oversize"])
def test_unsupported_descriptor_never_reaches_original(
    tmp_path: Path, native_module: tuple[Any, list[Any]], problem: str
) -> None:
    module, calls = native_module
    path = tmp_path / "owned"
    path.write_bytes(b"" if problem == "empty" else b"x" * (262145 if problem == "oversize" else 3))
    capture = study.ServingCapture(tmp_path / "other" if problem == "foreign" else tmp_path)
    capture.scope = problem
    with capture.installed(), path.open("rb") as stream:
        if problem == "offset":
            stream.seek(1)
        with pytest.raises(ValueError):
            module._unpickle(stream)
    assert capture.events == [] and calls == []


@pytest.mark.parametrize("error", [ValueError, OSError, AttributeError])
def test_snapshot_error_restores_hook_and_releases_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    native_module: tuple[Any, list[Any]],
    error: type[Exception],
) -> None:
    module, calls = native_module
    original = module._unpickle
    capture = study.ServingCapture(tmp_path)
    capture.scope = "snapshot-error"

    def failed_snapshot(*_: Any) -> bytes:
        raise error("deliberate snapshot failure")

    monkeypatch.setattr(study, "descriptor_bytes", failed_snapshot)
    with pytest.raises(error, match="snapshot failure"), capture.installed():
        module._unpickle(object())
    assert module._unpickle is original
    assert capture.events == [] and calls == []
    with study.ServingCapture(tmp_path).installed():
        pass
    assert module._unpickle is original


def test_original_failure_is_an_incomplete_event_and_hook_is_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_module: tuple[Any, list[Any]]
) -> None:
    module, _ = native_module

    def failed_original(stream: Any, mmap_mode: str | None = None) -> None:
        raise RuntimeError("reconstruction failed")

    module._unpickle = failed_original
    path = tmp_path / "owned"
    path.write_bytes(b"owned")
    capture = study.ServingCapture(tmp_path)
    capture.scope = "incomplete"
    ticks = iter((0, 4, 7, 16))
    monkeypatch.setattr(study, "perf_counter_ns", lambda: next(ticks))
    with (
        pytest.raises(RuntimeError, match="reconstruction failed"),
        capture.installed(),
        path.open("rb") as stream,
    ):
        module._unpickle(stream)
    assert module._unpickle is failed_original
    assert capture.events[0]["completed"] is False
    assert capture.events[0]["reconstruct_ns"] == 9
    with study.ServingCapture(tmp_path).installed():
        pass


def test_nested_capture_rejected_and_context_failure_releases_lock(
    tmp_path: Path, native_module: tuple[Any, list[Any]]
) -> None:
    module, _ = native_module
    original = module._unpickle
    with (
        pytest.raises(ValueError, match="outer failure"),
        study.ServingCapture(tmp_path).installed(),
    ):
        with (
            pytest.raises(RuntimeError, match="serial"),
            study.ServingCapture(tmp_path).installed(),
        ):
            pass
        raise ValueError("outer failure")
    assert module._unpickle is original
    with study.ServingCapture(tmp_path).installed():
        pass


def test_object_fingerprint_binds_tree_classes_and_dimensions() -> None:
    import numpy as np

    nodes = np.zeros(
        2,
        dtype={
            "names": ["left", "feature"],
            "formats": ["i8", "i8"],
            "offsets": [0, 16],
            "itemsize": 24,
        },
    )
    values = np.array([[[0.2, 0.8]], [[0.6, 0.4]]])
    model = SimpleNamespace(
        tree_=SimpleNamespace(__getstate__=lambda: {"nodes": nodes, "values": values}),
        classes_=np.array([0, 1]),
        n_features_in_=8,
        n_outputs_=1,
    )
    expected = study.tree_fingerprint(model)
    nodes.view("u1")[8:16] = 255  # Undefined padding has no model meaning.
    assert study.tree_fingerprint(model) == expected
    nodes["feature"][0] = 3
    assert study.tree_fingerprint(model) != expected
    nodes["feature"][0] = 0
    model.n_features_in_ = 7
    assert study.tree_fingerprint(model) != expected
    model.n_features_in_ = 8
    model.classes_ = np.array([1, 0])
    assert study.tree_fingerprint(model) != expected
