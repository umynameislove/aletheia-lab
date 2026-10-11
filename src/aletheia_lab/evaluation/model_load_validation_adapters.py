"""Lazy adapters for the frozen offered-buffer validation protocol.

Importing this module does not import a selected loader, construct artifacts, fit
a model, or enter a native loader. The caller must authorize preparation and
execution separately. Hooks capture native entries, not all consumed bytes or
successful serving, under the protocol's honest local-process trust model.
"""

from __future__ import annotations

import importlib
import platform
import socket
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from unittest.mock import patch

DEPENDENCIES = {
    "onnxruntime": "1.23.2",
    "onnx": "1.19.1",
    "skops": "0.15.0",
    "scikit-learn": "1.9.0",
    "numpy": "2.5.1",
    "in-toto": "3.0.0",
    "securesystemslib": "1.5.1",
}
MAX_ARTIFACT_BYTES = 262_144
_NATIVE_PATCH_LOCK = threading.Lock()


def environment() -> dict[str, Any]:
    """Inventory the exact frozen environment without importing any loader."""
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("validation requires the frozen Python 3.12 environment")
    packages = {}
    for name, expected in DEPENDENCIES.items():
        try:
            observed = version(name)
        except PackageNotFoundError as exc:
            raise RuntimeError(f"missing frozen validation dependency: {name}") from exc
        if observed != expected:
            raise RuntimeError(f"frozen validation dependency differs: {name}")
        packages[name] = observed
    return {
        "python": platform.python_version(),
        "python_build": sys.version,
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": packages,
    }


def _buffer(payload: bytes) -> bytes:
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_ARTIFACT_BYTES:
        raise ValueError("artifact must be a bounded complete immutable bytes object")
    return payload


def _embedded_onnx(message: Any) -> None:
    """Traverse every protobuf message, including sparse tensors and subgraphs."""
    name = message.DESCRIPTOR.full_name
    if name == "onnx.TensorProto" and (message.external_data or message.data_location != 0):
        raise ValueError("external ONNX tensor data is outside the frozen protocol")
    if name == "onnx.NodeProto" and message.domain not in {"", "ai.onnx"}:
        raise ValueError("custom ONNX operators are outside the frozen protocol")
    for field, value in message.ListFields():
        if field.message_type is not None:
            children = value if field.is_repeated else (value,)
            for child in children:
                _embedded_onnx(child)


def build_artifacts() -> dict[str, dict[str, bytes]]:
    """Explicit preparation only: build graphs, make exactly two fits, audit archives.

    This never calls ORT InferenceSession or SKOPS loads. ZIP serialization need
    not be byte-stable; the caller seals these actual buffers before target loads.
    """
    environment()
    onnx = importlib.import_module("onnx")
    numpy = importlib.import_module("numpy")
    skops = importlib.import_module("skops.io")
    linear_model = importlib.import_module("sklearn.linear_model")
    artifacts: dict[str, dict[str, bytes]] = {"onnxruntime": {}, "skops": {}}
    for label, weights in (("A", [1.0, 2.0]), ("B", [2.0, 1.0])):
        graph = onnx.helper.make_graph(
            [
                onnx.helper.make_node("MatMul", ["X", "W"], ["M"]),
                onnx.helper.make_node("Add", ["M", "b"], ["Y"]),
            ],
            f"model-load-{label}",
            [onnx.helper.make_tensor_value_info("X", onnx.TensorProto.FLOAT, ["batch", 2])],
            [onnx.helper.make_tensor_value_info("Y", onnx.TensorProto.FLOAT, ["batch", 1])],
            initializer=[
                onnx.helper.make_tensor("W", onnx.TensorProto.FLOAT, [2, 1], weights),
                onnx.helper.make_tensor("b", onnx.TensorProto.FLOAT, [1], [0.0]),
            ],
        )
        model = onnx.helper.make_model(
            graph, opset_imports=[onnx.helper.make_opsetid("", 13)], ir_version=10
        )
        _embedded_onnx(model)
        onnx.checker.check_model(model)
        artifacts["onnxruntime"][label] = _buffer(model.SerializeToString())
    features = numpy.asarray([[0, 0], [1, 0], [0, 1], [1, 1]], dtype=numpy.float64)
    for label, targets in (("A", [0, 1, 2, 3]), ("B", [0, 2, 1, 3])):
        model = linear_model.LinearRegression(fit_intercept=False).fit(
            features, numpy.asarray(targets, dtype=numpy.float64)
        )
        payload = _buffer(skops.dumps(model))
        if skops.get_untrusted_types(data=payload) != []:
            raise ValueError("SKOPS artifact includes types outside default trust")
        artifacts["skops"][label] = payload
    if any(pair["A"] == pair["B"] for pair in artifacts.values()):
        raise ValueError("the frozen A/B artifact pair must contain distinct buffers")
    return artifacts


class NativeAdapter:
    """Call the frozen API and instrument its actual native-entry boundary.

    ``before`` may block before the original native call. ``after`` runs exactly
    once if that call returned or raised, and never when ``before`` blocked. ORT
    native-constructor completion is separate from SDK initialize-session/load
    completion, which the caller must record around load/reenter.
    """

    def __init__(
        self,
        backend: str,
        before: Callable[[bytes], None],
        after: Callable[[bool], None],
    ) -> None:
        if backend not in {"onnxruntime", "skops"}:
            raise ValueError("loader backend is outside the frozen protocol")
        self.backend, self.before, self.after = backend, before, after

    def _invoke(self, payload: bytes, native: Callable[[], Any]) -> Any:
        self.before(payload)
        try:
            result = native()
        except BaseException:
            self.after(False)
            raise
        self.after(True)
        return result

    @contextmanager
    def _ort_capture(self, payload: bytes) -> Iterator[None]:
        collection = importlib.import_module("onnxruntime.capi.onnxruntime_inference_collection")
        # Global module patching is scoped and serialized; the scheduler must not
        # issue uncaptured ORT entries from other threads in this trusted process.
        with _NATIVE_PATCH_LOCK:
            native = collection.C.InferenceSession

            def capture(*args: Any, **kwargs: Any) -> Any:
                if len(args) != 4 or kwargs or args[1] is not payload or args[2] is not False:
                    raise ValueError("ORT native entry differs from the frozen full-buffer API")
                return self._invoke(payload, lambda: native(*args, **kwargs))

            with patch.object(collection.C, "InferenceSession", capture):
                yield

    def load(self, payload: bytes) -> Any:
        payload = _buffer(payload)
        if self.backend == "skops":
            skops = importlib.import_module("skops.io")
            return self._invoke(payload, lambda: skops.loads(payload, trusted=[]))
        ort = importlib.import_module("onnxruntime")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        options.log_verbosity_level = 0
        with self._ort_capture(payload):
            return ort.InferenceSession(
                payload,
                sess_options=options,
                providers=["CPUExecutionProvider"],
                enable_fallback=False,
                read_config_from_model=False,
            )

    def reenter(self, model: Any, payload: bytes) -> Any:
        payload = _buffer(payload)
        if self.backend == "skops":
            return self.load(payload)
        if getattr(model, "_model_bytes", None) is not payload:
            raise ValueError("ORT reentry must use the same Python session and buffer")
        with self._ort_capture(payload):
            model.set_providers(["CPUExecutionProvider"])
        return model


@contextmanager
def no_network() -> Iterator[None]:
    """Disable Python socket creation and resolution during local execution.

    This is a process-local guard, not malicious-host or native-code attestation.
    The isolated execution environment must also prohibit runtime networking.
    """

    def blocked(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("network access is outside the frozen validation protocol")

    with (
        patch.object(socket, "socket", blocked),
        patch.object(socket, "create_connection", blocked),
        patch.object(socket, "getaddrinfo", blocked),
    ):
        yield
