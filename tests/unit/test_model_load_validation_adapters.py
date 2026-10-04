"""Synthetic adapter checks; never import or execute the selected loader SDKs."""

from __future__ import annotations

import builtins
import importlib
import socket
from importlib.metadata import PackageNotFoundError
from types import SimpleNamespace
from typing import Any

import pytest

from aletheia_lab.evaluation import model_load_validation_adapters as adapters


@pytest.fixture
def frozen_metadata(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    queries: list[str] = []

    def metadata(name: str) -> str:
        queries.append(name)
        return adapters.DEPENDENCIES[name]

    monkeypatch.setattr(adapters.sys, "version_info", (3, 12, 14))
    monkeypatch.setattr(adapters.sys, "version", "synthetic-python-build")
    monkeypatch.setattr(adapters, "version", metadata)
    monkeypatch.setattr(adapters.platform, "python_version", lambda: "3.12.14")
    monkeypatch.setattr(adapters.platform, "python_implementation", lambda: "CPython")
    monkeypatch.setattr(adapters.platform, "platform", lambda: "synthetic-platform")
    monkeypatch.setattr(adapters.platform, "machine", lambda: "synthetic-machine")
    return queries


def test_environment_records_exact_pins_and_full_python_platform(
    frozen_metadata: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_loader(name: str) -> Any:
        raise AssertionError(f"environment imported a loader: {name}")

    monkeypatch.setattr(adapters.importlib, "import_module", no_loader)
    assert adapters.environment() == {
        "python": "3.12.14",
        "python_build": "synthetic-python-build",
        "implementation": "CPython",
        "platform": "synthetic-platform",
        "machine": "synthetic-machine",
        "packages": adapters.DEPENDENCIES,
    }
    assert frozen_metadata == list(adapters.DEPENDENCIES)


@pytest.mark.parametrize("name", list(adapters.DEPENDENCIES))
@pytest.mark.parametrize("failure", ["missing", "drift"])
def test_environment_fails_closed_on_any_dependency(
    name: str, failure: str, frozen_metadata: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def metadata(package: str) -> str:
        if package == name:
            if failure == "missing":
                raise PackageNotFoundError(package)
            return "unfrozen-version"
        return adapters.DEPENDENCIES[package]

    monkeypatch.setattr(adapters, "version", metadata)
    with pytest.raises(RuntimeError, match="validation dependency"):
        adapters.environment()


def test_environment_rejects_other_python_before_package_queries(
    frozen_metadata: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapters.sys, "version_info", (3, 13, 0))
    with pytest.raises(RuntimeError, match="Python 3.12"):
        adapters.environment()
    assert frozen_metadata == []


def test_module_import_and_adapter_construction_are_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    imports: list[str] = []
    original_import = builtins.__import__

    def direct_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.split(".")[0] in {"onnxruntime", "onnx", "skops", "numpy", "sklearn"}:
            raise AssertionError(f"eager SDK import: {name}")
        return original_import(name, *args, **kwargs)

    def forbidden(name: str) -> Any:
        imports.append(name)
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr(adapters.importlib, "import_module", forbidden)
    monkeypatch.setattr(builtins, "__import__", direct_import)
    importlib.reload(adapters)
    adapters.NativeAdapter("onnxruntime", lambda _: None, lambda _: None)
    adapters.NativeAdapter("skops", lambda _: None, lambda _: None)
    assert imports == []


@pytest.fixture
def ort_stub(monkeypatch: pytest.MonkeyPatch) -> Any:
    state = SimpleNamespace(
        entries=[],
        initializations=[],
        outer=[],
        reentries=[],
        native_error=None,
        initialize_error=None,
    )

    class NativeSession:
        def initialize_session(self, providers: list[str], options: Any, optimizers: Any) -> None:
            state.initializations.append((providers, options, optimizers))
            if state.initialize_error is not None:
                raise state.initialize_error

    def native(*args: Any, **kwargs: Any) -> NativeSession:
        state.entries.append((args, kwargs))
        if state.native_error is not None:
            raise state.native_error
        return NativeSession()

    collection = SimpleNamespace(C=SimpleNamespace(InferenceSession=native))

    class Session:
        """Pinned ORT subset: retained bytes, native ctor then initialize, reset reentry."""

        def __init__(self, payload: bytes, *, sess_options: Any, providers: list[str], **kw: Any):
            state.outer.append((payload, sess_options, providers, kw))
            self._model_bytes = payload
            self._sess_options = self._sess_options_initial = sess_options
            self._read_config_from_model = kw["read_config_from_model"]
            self._enable_fallback = int(kw.get("enable_fallback", 1)) == 1
            try:
                self._create_inference_session(providers)
            except (ValueError, RuntimeError):
                if not self._enable_fallback:
                    raise
                self._create_inference_session(["CPUExecutionProvider"])

        def _create_inference_session(self, providers: list[str]) -> None:
            self._sess = collection.C.InferenceSession(
                self._sess_options, self._model_bytes, False, self._read_config_from_model
            )
            self._sess.initialize_session(providers, [], set())

        def set_providers(self, providers: list[str]) -> None:
            state.reentries.append(self)
            self._sess = None
            self._sess_options = self._sess_options_initial
            self._create_inference_session(providers)

    ort = SimpleNamespace(SessionOptions=SimpleNamespace, InferenceSession=Session)

    def modules(name: str) -> Any:
        if name == "onnxruntime":
            return ort
        if name == "onnxruntime.capi.onnxruntime_inference_collection":
            return collection
        raise AssertionError(f"unexpected SDK import: {name}")

    monkeypatch.setattr(adapters.importlib, "import_module", modules)
    state.collection, state.original_native = collection, native
    return state


def test_ort_same_python_object_reentry_counts_two_native_entries(ort_stub: Any) -> None:
    payload = b"complete-embedded-synthetic-model"
    before: list[bytes] = []
    after: list[bool] = []
    adapter = adapters.NativeAdapter("onnxruntime", before.append, after.append)
    model = adapter.load(payload)
    assert (
        adapters.NativeAdapter("onnxruntime", before.append, after.append).reenter(model, payload)
        is model
    )
    assert len(ort_stub.outer) == 1
    assert ort_stub.reentries == [model]
    assert len(ort_stub.entries) == 2
    assert all(item is payload for item in before)
    assert after == [True, True]
    _, options, providers, keywords = ort_stub.outer[0]
    assert providers == ["CPUExecutionProvider"]
    assert keywords == {"enable_fallback": False, "read_config_from_model": False}
    assert vars(options) == {
        "intra_op_num_threads": 1,
        "inter_op_num_threads": 1,
        "log_severity_level": 3,
        "log_verbosity_level": 0,
    }
    for args, kwargs in ort_stub.entries:
        assert args == (options, payload, False, False)
        assert args[1] is payload
        assert kwargs == {}
    assert ort_stub.collection.C.InferenceSession is ort_stub.original_native


@pytest.mark.parametrize("failure_boundary", ["native", "initialize"])
def test_ort_failure_has_one_entry_without_fallback(ort_stub: Any, failure_boundary: str) -> None:
    error = RuntimeError("synthetic ORT failure")
    setattr(ort_stub, f"{failure_boundary}_error", error)
    before: list[bytes] = []
    after: list[bool] = []
    with pytest.raises(RuntimeError, match="synthetic ORT failure") as caught:
        adapters.NativeAdapter("onnxruntime", before.append, after.append).load(b"entire-model")
    assert caught.value is error
    assert len(before) == len(ort_stub.entries) == 1
    assert after == [failure_boundary == "initialize"]
    assert len(ort_stub.initializations) == int(failure_boundary == "initialize")
    assert ort_stub.collection.C.InferenceSession is ort_stub.original_native


def test_ort_block_before_reentry_never_enters_native_or_calls_after(ort_stub: Any) -> None:
    class Blocked(RuntimeError):
        pass

    granted = False
    after: list[bool] = []

    def once(_: bytes) -> None:
        nonlocal granted
        if granted:
            raise Blocked("one invocation token consumed")
        granted = True

    adapter = adapters.NativeAdapter("onnxruntime", once, after.append)
    payload = b"complete-model"
    model = adapter.load(payload)
    with pytest.raises(Blocked, match="token consumed"):
        adapter.reenter(model, payload)
    assert len(ort_stub.entries) == 1
    assert after == [True]
    assert ort_stub.collection.C.InferenceSession is ort_stub.original_native


def test_ort_reentry_rejects_equal_but_different_buffer_object(ort_stub: Any) -> None:
    payload = b"complete-model-for-identity"
    before: list[bytes] = []
    adapter = adapters.NativeAdapter("onnxruntime", before.append, lambda _: None)
    model = adapter.load(payload)
    copy = memoryview(payload).tobytes()
    assert copy == payload and copy is not payload
    with pytest.raises(ValueError, match="same Python session and buffer"):
        adapter.reenter(model, copy)
    assert len(before) == len(ort_stub.entries) == 1


@pytest.mark.parametrize("malformed", ["copy", "path_flag", "missing_argument", "keyword"])
def test_ort_malformed_native_call_is_not_recorded_as_an_entry(
    ort_stub: Any, malformed: str
) -> None:
    payload = b"complete-model-not-a-path"
    before: list[bytes] = []
    after: list[bool] = []
    adapter = adapters.NativeAdapter("onnxruntime", before.append, after.append)
    args: tuple[Any, ...] = (object(), payload, False, False)
    kwargs: dict[str, Any] = {}
    if malformed == "copy":
        args = (args[0], memoryview(payload).tobytes(), False, False)
    elif malformed == "path_flag":
        args = (args[0], payload, True, False)
    elif malformed == "missing_argument":
        args = args[:-1]
    else:
        kwargs = {"unexpected": True}
    with adapter._ort_capture(payload), pytest.raises(ValueError, match="full-buffer API"):
        ort_stub.collection.C.InferenceSession(*args, **kwargs)
    assert before == after == ort_stub.entries == []
    assert ort_stub.collection.C.InferenceSession is ort_stub.original_native


@pytest.fixture
def skops_stub(monkeypatch: pytest.MonkeyPatch) -> Any:
    state = SimpleNamespace(entries=[], models=[], error=None)

    def loads(payload: bytes, *, trusted: list[str]) -> Any:
        state.entries.append((payload, trusted))
        if state.error is not None:
            raise state.error
        model = object()
        state.models.append(model)
        return model

    def modules(name: str) -> Any:
        assert name == "skops.io"
        return SimpleNamespace(loads=loads)

    monkeypatch.setattr(adapters.importlib, "import_module", modules)
    return state


def test_skops_reentry_is_second_actual_load_of_full_archive_with_empty_trust(
    skops_stub: Any,
) -> None:
    payload = b"entire-synthetic-archive-including-members-and-directory"
    before: list[bytes] = []
    after: list[bool] = []
    adapter = adapters.NativeAdapter("skops", before.append, after.append)
    model = adapter.load(payload)
    second = adapter.reenter(model, payload)
    assert second is not model
    assert skops_stub.models == [model, second]
    assert len(skops_stub.entries) == 2
    assert all(actual is payload and trusted == [] for actual, trusted in skops_stub.entries)
    assert all(type(trusted) is list for _, trusted in skops_stub.entries)
    assert all(actual is payload for actual in before)
    assert after == [True, True]


def test_skops_raised_entry_is_counted_once(skops_stub: Any) -> None:
    error = ValueError("synthetic archive audit failure")
    skops_stub.error = error
    before: list[bytes] = []
    after: list[bool] = []
    with pytest.raises(ValueError, match="audit failure") as caught:
        adapters.NativeAdapter("skops", before.append, after.append).load(b"entire-archive")
    assert caught.value is error
    assert len(before) == len(skops_stub.entries) == 1
    assert after == [False]


def test_skops_block_does_not_load_or_record_completion(skops_stub: Any) -> None:
    after: list[bool] = []

    def block(_: bytes) -> None:
        raise RuntimeError("synthetic prevention block")

    with pytest.raises(RuntimeError, match="prevention block"):
        adapters.NativeAdapter("skops", block, after.append).load(b"entire-archive")
    assert skops_stub.entries == after == []


@pytest.mark.parametrize("backend", ["onnxruntime", "skops"])
@pytest.mark.parametrize(
    "payload", [b"", bytearray(b"mutable"), memoryview(b"view"), b"x" * 262_145]
)
def test_invalid_artifact_never_imports_loader_or_calls_hooks(
    backend: str, payload: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected(*_: Any) -> Any:
        raise AssertionError("invalid buffer reached loader import or native hook")

    monkeypatch.setattr(adapters.importlib, "import_module", unexpected)
    with pytest.raises(ValueError, match="immutable bytes"):
        adapters.NativeAdapter(backend, unexpected, unexpected).load(payload)


def test_network_guard_blocks_creation_and_resolution_and_restores_after_error() -> None:
    original = (socket.socket, socket.create_connection, socket.getaddrinfo)
    with pytest.raises(ValueError, match="body failed"), adapters.no_network():
        for call in (
            lambda: socket.socket(),
            lambda: socket.create_connection(("example.invalid", 443)),
            lambda: socket.getaddrinfo("example.invalid", 443),
        ):
            with pytest.raises(RuntimeError, match="network access"):
                call()
        raise ValueError("body failed")
    assert (socket.socket, socket.create_connection, socket.getaddrinfo) == original


def _message(name: str, *, children: list[Any] | None = None, **values: Any) -> Any:
    fields = []
    if children is not None:
        fields = [(SimpleNamespace(message_type=object(), is_repeated=True), children)]
    return SimpleNamespace(
        DESCRIPTOR=SimpleNamespace(full_name=name), ListFields=lambda: fields, **values
    )


@pytest.mark.parametrize("external_data,location", [(["sidecar"], 0), ([], 1)])
def test_recursive_onnx_external_data_prohibition_reaches_nested_sparse_tensor(
    external_data: list[str], location: int
) -> None:
    tensor = _message("onnx.TensorProto", external_data=external_data, data_location=location)
    sparse = _message("onnx.SparseTensorProto", children=[tensor])
    nested_graph = _message("onnx.GraphProto", children=[sparse])
    model = _message("onnx.ModelProto", children=[nested_graph])
    with pytest.raises(ValueError, match="external ONNX tensor"):
        adapters._embedded_onnx(model)


def test_recursive_onnx_custom_operator_prohibition_and_embedded_default() -> None:
    tensor = _message("onnx.TensorProto", external_data=[], data_location=0)
    adapters._embedded_onnx(_message("onnx.ModelProto", children=[tensor]))
    custom = _message("onnx.NodeProto", domain="custom.operators")
    with pytest.raises(ValueError, match="custom ONNX operators"):
        adapters._embedded_onnx(_message("onnx.ModelProto", children=[custom]))
