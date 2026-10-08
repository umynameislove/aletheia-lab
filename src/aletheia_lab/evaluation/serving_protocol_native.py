"""Pinned serving protocol causal slices, not full historical package deployments.

Definition qualification does not invoke reserved mapper or predictor methods.
Bento excludes transport/deserialization; TorchServe replaces only its network
stub and retains native KServe representations and conversion methods. Neither
slice establishes model enrollment or a supported component repair operation.
Use source loading in an isolated process because native package names are used.
"""

from __future__ import annotations

import abc
import ast
import functools
import importlib
import inspect
import itertools
import logging
import sys
import types
import typing as t
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.project.identity import content_sha256

PINS = {
    "bento-affected-0.py": "1ede38bab2883b896a7714d4b5d476fb274e1403ff34eb773540967a5156b9b0",
    "bento-fixed-0.py": "c698b8cffdcb558ccb76b1e9331a031aaf5a0232447c3919af89f76e068a2a43",
    "bento-affected-1.py": "0898bf167a1ea13235a582638cb7c52aff795f5e960b751b5bbda2ab16159802",
    "bento-fixed-1.py": "f6c27ae19c601b39a6c4f5a2d02f533b030fa26458ab16f428b9e88698381caa",
    "torchserve-affected-0.py": "40b0858b5c08b4ee2166dd02ea7d8aa4aa8f17e5df97232e37c73b42136e6b57",
    "torchserve-fixed-0.py": "760ee1724a7ddd324cfd07639316b9047f58264b0941934b3a4f38d4a94e65a1",
    "bento-container.py": "81591419c2254141b67cacf6c3e864630b224e126480af1572c02e29ab4d027e",
    "bento-utils.py": "9feed23aaa208b6228a6f03ef179bdfe77ed28750e1946912ef7275c0f8d5307",
    "bento-handle.py": "a2046cf438c676b7466e20f851b52728feb85ee3e6580af828d8af551d2ce573",
    "torchserve-gprc_utils.py": "b47b024a08979783c1869549b64a4a67b49cfbb5b698ec667e8640910fb149e5",
    "torchserve-inference.proto": "2187f7dc5e81ef3319b1c6968305e3a9d84bd92e9b44b23a7f5527895b65da25",
    "torchserve-inference-descriptor.pb": "774810e67aefeef14d5e1ecd3e39708f312c9a27d420dfe8ba30a53c1bec2b9e",
    "kserve-infer_type.py": "61394d66c8a68e65c5aba67114f8733725560888eadf65cbb51e58f1eb7a6152",
    "kserve-grpc_predict_v2_pb2.py": "402ca3f5c9f36d36d5ea851f0ed675c865852bb96ecb7cc120f3d2754543695d",
    "kserve-model.py": "b3ba92c7bab653dacc1264d683f318febf47c07f4c431f78185d7140ccca8024",
    "kserve-errors.py": "4ef9ecc359da46542d56e82b1c354eda5c07899de50ffe10fd5b2605a238a7ec",
    "kserve-constants.py": "6e2fe5b21b6731dc7fa6d674d3ae03980cf0c6cd68a12e15f41b3066becf26d1",
    "kserve-numpy_codec.py": "c256d7492e4b42cb55d1a5fce139aa23697cb8c3d47d84aab2143ad53980de59",
}


@dataclass(frozen=True)
class SourceRoots:
    """Caller-supplied immutable source and dependency directories."""

    reserved: Path
    dependencies: Path


class NativeSources(SourceRoots):
    """Source-root interface for definition qualification and sealed comparisons."""

    @property
    def pins(self) -> dict[str, str]:
        return dict(PINS)

    def bento_mapping(self, side: str) -> Any:
        return load_bento_mapping(self, side)

    def bento_params_and_ndarray(self) -> tuple[Any, Any]:
        return load_bento_params_and_ndarray(self)

    def bento_local_ref(self, side: str) -> Any:
        return load_bento_local_ref(self, side)

    def torch_dependencies(self) -> TorchDependencies:
        return load_torch_dependencies(self)

    def torch_model(self, side: str, deps: TorchDependencies) -> Any:
        return load_torch_model(self, side, deps)

    def qualify_definitions(self) -> dict[str, Any]:
        return qualify_definitions(self)


def checked_source(roots: SourceRoots, filename: str) -> Path:
    """Reject unpinned names, symbolic links, missing files and changed bytes."""

    if filename not in PINS or Path(filename).name != filename:
        raise ValueError("execution requires an exact pinned source filename")
    reserved = filename.startswith(
        ("bento-affected", "bento-fixed", "torchserve-affected", "torchserve-fixed")
    )
    path = (roots.reserved if reserved else roots.dependencies) / filename
    if any(item.is_symlink() for item in (path, *path.parents)) or not path.is_file():
        raise ValueError("execution requires a pinned regular source without symbolic links")
    if file_sha256(path) != PINS[filename]:
        raise ValueError("source pin mismatch before execution")
    return path


def _source_bytes(roots: SourceRoots, filename: str) -> tuple[Path, bytes]:
    path = checked_source(roots, filename)
    raw = path.read_bytes()
    if content_sha256(raw) != PINS[filename]:
        raise ValueError("source bytes changed while being read")
    return path, raw


def _module_shell(name: str) -> types.ModuleType:
    if name in sys.modules:
        module = sys.modules[name]
        if not getattr(module, "_serving_protocol_source", False):
            raise ValueError("native package name already occupied; use an isolated process")
        return module
    module = types.ModuleType(name)
    module.__path__ = []
    setattr(module, "_serving_protocol_source", True)  # noqa: B010
    sys.modules[name] = module
    if "." in name:
        parent, child = name.rsplit(".", 1)
        setattr(_module_shell(parent), child, module)
    return module


def _source_module(roots: SourceRoots, name: str, filename: str) -> types.ModuleType:
    path, raw = _source_bytes(roots, filename)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = name.rpartition(".")[0]
    setattr(module, "_serving_protocol_source", True)  # noqa: B010
    sys.modules[name] = module
    code = compile(raw, str(path), "exec")
    checked_source(roots, filename)
    # Execution consumes the same exact, pinned buffer checked above.
    exec(code, module.__dict__)  # nosec B102
    if "." in name:
        parent, child = name.rsplit(".", 1)
        setattr(_module_shell(parent), child, module)
    return module


def _exact_class(
    roots: SourceRoots,
    filename: str,
    class_name: str,
    namespace: dict[str, Any],
    methods: set[str] | None = None,
) -> Any:
    """Compile original class AST; optional selection removes whole unused methods.

    Selected bodies, decorators, arguments, defaults and bases remain unchanged.
    Explicit namespaces provide only the dependencies of the selected slice.
    """

    path, raw = _source_bytes(roots, filename)
    tree = ast.parse(raw, filename=str(path))
    node = next(
        item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == class_name
    )
    if methods is not None:
        present = {
            item.name
            for item in node.body
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        if not methods <= present:
            raise ValueError("native method census differs from the selected slice")
        node.body = [
            item
            for item in node.body
            if not isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) or item.name in methods
        ]
    fragment = ast.Module(
        body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            node,
        ],
        type_ignores=[],
    )
    namespace.setdefault("__name__", "serving_protocol_causal_slice")
    code = compile(ast.fix_missing_locations(fragment), str(path), "exec")
    checked_source(roots, filename)
    exec(code, namespace)  # nosec B102
    return namespace[class_name]


def _check_version(version: str) -> None:
    if version not in {"affected", "fixed"}:
        raise ValueError("source version must be affected or fixed")


def load_bento_mapping(roots: SourceRoots, version: str) -> Any:
    _check_version(version)
    namespace = {"t": t, "inspect": inspect, "BatchDimSuper": Mapping}
    return _exact_class(roots, f"bento-{version}-0.py", "BatchDimMapping", namespace)


def load_bento_params_and_ndarray(roots: SourceRoots) -> tuple[Any, Any]:
    """Retain native array batching; no payload or pickle deserializer is loaded."""

    namespace = {
        "t": t,
        "itertools": itertools,
        "abc": abc,
        "T": t.TypeVar("T"),
        "To": t.TypeVar("To"),
        "SingleType": t.TypeVar("SingleType"),
        "BatchType": t.TypeVar("BatchType"),
    }
    params = _exact_class(roots, "bento-utils.py", "Params", namespace)
    _exact_class(roots, "bento-container.py", "DataContainer", namespace)
    ndarray = _exact_class(
        roots,
        "bento-container.py",
        "NdarrayContainer",
        namespace,
        methods={"batches_to_batch", "batch_to_batches"},
    )
    return params, ndarray


def load_bento_local_ref(roots: SourceRoots, version: str) -> Any:
    """Retain the exact local handle, including fixed prepend-self behavior."""

    _check_version(version)
    namespace = {
        "t": t,
        "functools": functools,
        "ABC": abc.ABC,
        "abstractmethod": abc.abstractmethod,
    }
    _exact_class(roots, "bento-handle.py", "RunnerHandle", namespace)
    return _exact_class(roots, f"bento-{version}-1.py", "LocalRunnerRef", namespace)


def load_prediction_schema(roots: SourceRoots) -> Any:
    """Load actual protobuf message classes compiled from the pinned schema."""

    descriptor_pb2 = importlib.import_module("google.protobuf.descriptor_pb2")
    descriptor_pool = importlib.import_module("google.protobuf.descriptor_pool")
    message_factory = importlib.import_module("google.protobuf.message_factory")
    checked_source(roots, "torchserve-inference.proto")
    _, raw = _source_bytes(roots, "torchserve-inference-descriptor.pb")
    records = descriptor_pb2.FileDescriptorSet.FromString(raw)
    pool = descriptor_pool.DescriptorPool()
    for record in records.file:
        pool.Add(record)
    module = types.ModuleType("inference_pb2")
    setattr(module, "_serving_protocol_source", True)  # noqa: B010
    setattr(module, "DESCRIPTOR", pool.FindFileByName("torchserve-inference.proto"))  # noqa: B010
    for name in ("PredictionResponse", "PredictionsRequest", "TorchServeHealthResponse"):
        descriptor = pool.FindMessageTypeByName("org.pytorch.serve.grpc.inference." + name)
        cls = message_factory.GetMessageClass(descriptor)
        cls.__module__ = "inference_pb2"
        setattr(module, name, cls)
    sys.modules["inference_pb2"] = module
    return module


@dataclass(frozen=True)
class TorchDependencies:
    prediction: Any
    grpc: Any
    infer: Any
    converters: Any


def load_torch_dependencies(roots: SourceRoots) -> TorchDependencies:
    """Import native representations and converters without predictor invocation."""

    for name in (
        "kserve",
        "kserve.protocol",
        "kserve.protocol.grpc",
        "kserve.constants",
        "kserve.utils",
    ):
        _module_shell(name)
    errors = _module_shell("kserve.errors")
    _exact_class(roots, "kserve-errors.py", "InvalidInput", errors.__dict__)
    _exact_class(roots, "kserve-errors.py", "ModelMissingError", errors.__dict__)
    _source_module(roots, "kserve.constants.constants", "kserve-constants.py")
    _source_module(roots, "kserve.utils.numpy_codec", "kserve-numpy_codec.py")
    grpc_types = _source_module(
        roots, "kserve.protocol.grpc.grpc_predict_v2_pb2", "kserve-grpc_predict_v2_pb2.py"
    )
    infer_types = _source_module(roots, "kserve.protocol.infer_type", "kserve-infer_type.py")
    prediction = load_prediction_schema(roots)
    converters = _source_module(roots, "gprc_utils", "torchserve-gprc_utils.py")
    return TorchDependencies(prediction, grpc_types, infer_types, converters)


def load_torch_model(roots: SourceRoots, version: str, deps: TorchDependencies) -> Any:
    """Select native init, grpc_client, _grpc_predict and postprocess methods.

    The caller supplies both _grpc_client_stub and grpc_client_stub. Network
    transport and registration/storage are excluded; source bodies are unchanged.
    """

    _check_version(version)
    base_namespace: dict[str, Any] = {"Enum": Enum}
    _exact_class(roots, "kserve-model.py", "PredictorProtocol", base_namespace)
    native_base = _exact_class(
        roots, "kserve-model.py", "Model", base_namespace, methods={"__init__"}
    )
    namespace = {
        "Dict": dict,
        "Union": t.Union,
        "Enum": Enum,
        "Model": native_base,
        "ModelInferRequest": deps.grpc.ModelInferRequest,
        "ModelInferResponse": deps.grpc.ModelInferResponse,
        "InferRequest": deps.infer.InferRequest,
        "InferResponse": deps.infer.InferResponse,
        "PredictionResponse": deps.prediction.PredictionResponse,
        "to_ts_grpc": deps.converters.to_ts_grpc,
        "from_ts_grpc": deps.converters.from_ts_grpc,
        "logging": logging,
    }
    filename = f"torchserve-{version}-0.py"
    _exact_class(roots, filename, "PredictorProtocol", namespace)
    return _exact_class(
        roots,
        filename,
        "TorchserveModel",
        namespace,
        methods={"__init__", "grpc_client", "_grpc_predict", "postprocess"},
    )


def qualify_definitions(roots: SourceRoots) -> dict[str, Any]:
    """Check only definitions, types and signatures, never reserved outcomes."""

    deps = load_torch_dependencies(roots)
    definitions = {}
    for version in ("affected", "fixed"):
        mapping = load_bento_mapping(roots, version)
        local = load_bento_local_ref(roots, version)
        torch = load_torch_model(roots, version, deps)
        definitions[version] = {
            "mapping_methods": sorted(
                key for key in mapping.__dict__ if key.startswith("__") and key.endswith("__")
            ),
            "local_signature": str(inspect.signature(local.run_method)),
            "torch_predict_signature": str(inspect.signature(torch._grpc_predict)),
        }
    params, ndarray = load_bento_params_and_ndarray(roots)
    return {
        "qualified": True,
        "definition_loading_only": True,
        "reserved_methods_invoked": False,
        "classes": {
            "PredictionResponse": deps.prediction.PredictionResponse.DESCRIPTOR.full_name,
            "ModelInferResponse": deps.grpc.ModelInferResponse.DESCRIPTOR.full_name,
            "InferResponse": deps.infer.InferResponse.__module__ + ".InferResponse",
            "Params": params.__name__,
            "NdarrayContainer": ndarray.__name__,
        },
        "definitions": definitions,
        "excluded": [
            "full historical package deployment",
            "multipart transport and payload deserialization",
            "model enrollment",
            "native component repair",
        ],
    }
