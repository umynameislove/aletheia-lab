"""Externally authored MLServer REST lifecycle, not a substitute load scheduler.

Fresh trusted uncompressed artifacts; serial ASGI only. No network server,
loader retry, hostile-host attestation or source-native immutable-hash policy.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from unittest.mock import patch

from aletheia_lab.evaluation.model_load_application_capture import NativeCapture
from aletheia_lab.project.identity import content_sha256

ARMS = ("native", "captured", "path-legal", "path-replaced")
MAX_NATIVE_CALLS = 3


def atomic_artifact(path: Path, payload: bytes) -> None:
    """Only atomic replacement; never modify an inode held by the loader."""
    replacement = path.with_suffix(".replacement")
    with replacement.open("xb") as stream:
        stream.write(payload)
    os.replace(replacement, path)


def prepare_artifacts(directory: Path) -> dict[str, str]:
    joblib = importlib.import_module("joblib")
    model_class = importlib.import_module("sklearn.dummy").DummyClassifier
    directory.mkdir(exist_ok=False)
    digests: dict[str, str] = {}
    for name, constant in (("A", 0), ("B", 1)):
        model = model_class(strategy="constant", constant=constant)
        model.fit([[0.0], [1.0]], [0, 1])
        path = directory / f"{name}.joblib"
        if joblib.dump(model, path, compress=0) != [str(path)]:
            raise ValueError("artifact is not single-file")
        payload = path.read_bytes()
        if not 0 < len(payload) <= 262_144:
            raise ValueError("owned artifact outside byte bound")
        digests[name] = content_sha256(payload)
    if digests["A"] == digests["B"]:
        raise ValueError("owned artifacts must differ")
    return digests


class _EntryCensus:
    """Profile original functions, without replacing the native-arm unpickler."""

    def __init__(self) -> None:
        joblib = importlib.import_module("joblib")
        unpickle = importlib.import_module("joblib.numpy_pickle")._unpickle
        compute = importlib.import_module("mlserver_sklearn").SKLearnModel._get_model_outputs
        self.codes = {
            joblib.load.__code__: "loader_calls",
            unpickle.__code__: "reconstructions",
            compute.__code__: "prediction_computations",
        }
        self.counts = {"loader_calls": 0, "reconstructions": 0, "prediction_computations": 0}
        self.over_budget = False

    def profile(self, frame: Any, event: str, arg: Any) -> None:
        if event == "call" and frame.f_code in self.codes:
            key = self.codes[frame.f_code]
            self.counts[key] += 1
            if self.counts["loader_calls"] > MAX_NATIVE_CALLS:
                # Raising inside a profile callback disables CPython profiling.
                # Persist the flag instead; the request boundary aborts the worker.
                self.over_budget = True


def _application(directory: Path) -> tuple[Any, Any]:
    settings_class = importlib.import_module("mlserver.settings").Settings
    registry = importlib.import_module("mlserver.registry").MultiModelRegistry()
    repository = importlib.import_module(
        "mlserver.repository.repository"
    ).SchemalessModelRepository(str(directory))
    handlers = importlib.import_module("mlserver.handlers")
    settings = settings_class(
        _env_file=None,
        model_repository_root=str(directory),
        parallel_workers=0,
        cache_enabled=True,
        metrics_endpoint=None,
        tracing_server=None,
        kafka_enabled=False,
    )
    app = importlib.import_module("mlserver.rest.app").create_app(
        settings,
        handlers.DataPlane(settings, registry),
        handlers.ModelRepositoryHandlers(repository, registry),
    )
    return app, registry


class _Workflow:
    def __init__(self, arm: str, directory: Path, artifacts: Path) -> None:
        if arm not in ARMS or directory.exists() or artifacts.is_symlink():
            raise ValueError("worker requires a fresh fixed-arm workspace")
        self.arm = arm
        self.payloads = {name: (artifacts / f"{name}.joblib").read_bytes() for name in ("A", "B")}
        self.digests = {name: content_sha256(raw) for name, raw in self.payloads.items()}
        model_dir = directory / "repository/probe"
        model_dir.mkdir(parents=True)
        self.path = model_dir / "model.joblib"
        self.settings = {
            "name": "probe",
            "implementation": "mlserver_sklearn.SKLearnModel",
            "parameters": {"version": "1", "uri": "model.joblib"},
            "cache_enabled": True,
        }
        (model_dir / "model-settings.json").write_text(json.dumps(self.settings), encoding="utf-8")
        atomic_artifact(self.path, self.payloads["A"])
        self.app, self.registry = _application(directory / "repository")
        self.capture = NativeCapture(model_dir)
        self.census = _EntryCensus()
        self.rows: list[dict[str, Any]] = []
        self.scope = ""
        self.client: Any = None
        self.relations: dict[str, bool] = {}

    def restore_path(self, _: Any) -> None:
        if self.arm == "path-replaced" and self.scope == "initial-load":
            atomic_artifact(self.path, self.payloads["A"])

    async def request(
        self, scope: str, method: str, route: str, body: Any = None, *, intent: str | None = None
    ) -> dict[str, Any]:
        self.scope = scope
        self.capture.scope = scope
        before = dict(self.census.counts)
        if route.endswith("/load") and self.census.counts["loader_calls"] >= MAX_NATIVE_CALLS:
            raise RuntimeError("fixed native-call budget exhausted")
        response = await self.client.request(method, route, json=body)
        if self.census.over_budget:
            raise RuntimeError("native call exceeded its declared schedule")
        counts = {key: self.census.counts[key] - value for key, value in before.items()}
        row = {
            "scope": scope,
            "method": method,
            "route": route,
            "status": response.status_code,
            "body": (response.json() if response.content else None)
            if response.status_code < 400
            else {"error": "native_http_failure"},
            "counts": counts,
            "operator_pin_sha256": self.digests[intent] if intent is not None else None,
        }
        self.rows.append(row)
        return row

    async def infer(self, scope: str, identifier: str) -> dict[str, Any]:
        return await self.request(
            scope,
            "POST",
            "/v2/models/probe/infer",
            {
                "id": identifier,
                "inputs": [{"name": "x", "shape": [2, 1], "datatype": "FP64", "data": [0.0, 1.0]}],
            },
        )

    async def lifecycle(self, first: Any) -> None:
        atomic_artifact(self.path, self.payloads["B"])
        await self.infer("resident-after-path-update", "resident")
        retained = await self.registry.get_model("probe", "1")
        self.relations["resident_model_and_object_reused"] = (
            retained is first and retained._model is first._model
        )
        await self.request(
            "explicit-reload", "POST", "/v2/repository/models/probe/load", intent="B"
        )
        second = await self.registry.get_model("probe", "1")
        self.relations["explicit_reload_replaced_model_and_object"] = (
            second is not first and second._model is not first._model
        )
        self.relations["reload_readiness_transition"] = not first.ready and second.ready
        await self.infer("inference-after-reload", "reload")
        # Missing-file failure is retained; no unknown pickle is deserialized.
        self.path.unlink()
        await self.request("failed-reload", "POST", "/v2/repository/models/probe/load", intent="B")
        self.relations["failed_reload_retained_previous_model"] = (
            await self.registry.get_model("probe", "1") is second
        )
        await self.infer("inference-after-failed-reload", "failed-reload")

    async def run(self) -> dict[str, Any]:
        httpx = importlib.import_module("httpx")
        transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
        old_profile = sys.getprofile()
        context = (
            nullcontext()
            if self.arm == "native"
            else self.capture.installed(on_open=self.restore_path)
        )
        path_pre = content_sha256(self.path.read_bytes())
        if self.arm == "path-replaced":
            atomic_artifact(self.path, self.payloads["B"])
        try:
            sys.setprofile(self.census.profile)
            with context:
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://owned-asgi", trust_env=False
                ) as client:
                    self.client = client
                    loaded = await self.request(
                        "initial-load", "POST", "/v2/repository/models/probe/load", intent="A"
                    )
                    first = await self.registry.get_model("probe", "1")
                    ready = await self.request("readiness", "GET", "/v2/models/probe/ready")
                    metadata = await self.request("metadata", "GET", "/v2/models/probe")
                    frame = {
                        "model_settings": self.settings,
                        "load_status": loaded["status"],
                        "ready_status": ready["status"],
                        "metadata": metadata["body"],
                        "path_pre_sha256": path_pre,
                        "path_post_sha256": content_sha256(self.path.read_bytes()),
                    }
                    initial = await self.infer("initial-inference", "initial")
                    repeated = await self.infer("repeat-response-cache", "initial")
                    self.relations["repeat_response_equal"] = initial["body"] == repeated["body"]
                    self.relations["response_cache_avoids_recomputation"] = (
                        repeated["counts"]["prediction_computations"] == 0
                        and initial["counts"]["prediction_computations"] == 1
                    )
                    if self.arm in ("native", "captured"):
                        await self.lifecycle(first)
                    await self.request("unload", "POST", "/v2/repository/models/probe/unload")
                    self.relations["unload_emptied_registry"] = not list(
                        await self.registry.get_models()
                    )
                    await self.infer("inference-after-unload", "unloaded")
        finally:
            sys.setprofile(old_profile)
        return {
            "arm": self.arm,
            "rows": self.rows,
            "counts": self.census.counts,
            "relations": self.relations,
            "path_provenance_frame": frame,
            "reference": self.capture.reference,
            "receipts": self.capture.receipts,
            "unsupported": self.capture.unsupported,
            "artifact_sha256": self.digests,
        }


def worker(arm: str, directory: Path, artifacts: Path) -> dict[str, Any]:
    """Internal trusted inputs only; isolate upstream global metric registration.

    The parent prepares these artifacts. This is not an untrusted-pickle API or
    a provenance authentication boundary against a caller controlling the host.
    """
    connections = 0

    def deny_network(*args: Any, **kwargs: Any) -> None:
        nonlocal connections
        connections += 1
        raise RuntimeError("socket connections are outside this application slice")

    # Child-local settings only, never expose or modify the parent's credential environment.
    for key in tuple(os.environ):
        if key.startswith(("MLSERVER_", "OTEL_", "PROMETHEUS_")):
            os.environ.pop(key)
    os.environ["OTEL_SDK_DISABLED"] = "true"
    with (
        patch.object(socket.socket, "connect", deny_network),
        patch.object(socket.socket, "connect_ex", deny_network),
        patch.object(socket, "create_connection", deny_network),
    ):
        report = asyncio.run(_Workflow(arm, directory, artifacts).run())
    report["socket_connection_attempts"] = connections
    return report
