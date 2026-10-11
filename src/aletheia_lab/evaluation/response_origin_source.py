"""Opt-in native MLServer cache/version/reload lifecycle, with owned CPU models.

Actual REST handlers, registry, sklearn runtime and LocalCache remain native.
Barrier scheduling does not replace prediction. Key/flush/fence arms are explicit
ordinary repairs, not a new algorithm; observations are a trusted host boundary.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import socket
import sys
from contextlib import asynccontextmanager
from contextvars import ContextVar
from importlib.metadata import version
from pathlib import Path
from time import perf_counter_ns
from typing import Any
from unittest.mock import patch

from aletheia_lab.evaluation.model_load_application import atomic_artifact
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

ARMS = ("native", "disabled", "route_key", "flush", "fenced", "generation_key")
WORKFLOWS = ("version_routes", "serial_replacement", "delayed_fill")
PACKAGES = ("mlserver", "mlserver-sklearn", "scikit-learn", "joblib", "httpx", "uvicorn")


class OriginProbe:
    def __init__(self, arm: str, workflow: str, directory: Path) -> None:
        if arm not in ARMS or workflow not in WORKFLOWS or directory.exists():
            raise ValueError("fresh fixed-arm lifecycle required")
        self.arm, self.workflow, self.directory = arm, workflow, directory
        directory.mkdir(parents=True)
        self.events: list[dict[str, Any]] = []
        self.rows: list[dict[str, Any]] = []
        self.loads: dict[str, dict[str, Any]] = {}
        self.generations: dict[int, str] = {}
        self.token: ContextVar[str] = ContextVar("owned_request_token", default="")
        self.selected: ContextVar[str] = ContextVar("selected_resident", default="")
        self.producer: ContextVar[str] = ContextVar("response_producer", default="")
        self.entry_epoch: ContextVar[int] = ContextVar("request_epoch", default=0)
        self.epoch = 0
        self.insert_origins: dict[str, str] = {}
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.artifacts = self._artifacts()
        self.model_dirs: dict[str, Path] = {}
        self._prepare_repository()
        self.client: Any = None
        self.registry: Any = None
        self.cache: Any = None

    def _artifacts(self) -> dict[str, dict[str, Any]]:
        joblib = importlib.import_module("joblib")
        estimator = importlib.import_module("sklearn.linear_model").LinearRegression
        artifacts = {}
        for label, slope in (("A", 1.0), ("B", 2.0), ("C", 3.0)):
            model = estimator().fit([[-1.0], [0.0], [1.0]], [-slope, 0.0, slope])
            path = self.directory / f"owned-{label}.joblib"
            joblib.dump(model, path, compress=0)
            raw = path.read_bytes()
            if not 0 < len(raw) <= 262144:
                raise ValueError("owned artifact byte bound exceeded")
            artifacts[label] = {
                "raw_hex": raw.hex(),
                "sha256": content_sha256(raw),
                "parameters": self._parameters(model),
            }
        return artifacts

    @staticmethod
    def _parameters(model: Any) -> dict[str, Any]:
        return {"coef": model.coef_.tolist(), "intercept": float(model.intercept_)}

    def _prepare_repository(self) -> None:
        versions = (("1", "A"), ("2", "B")) if self.workflow == "version_routes" else (("1", "A"),)
        for number, label in versions:
            path = self.directory / "repository" / "model" / number
            path.mkdir(parents=True)
            self.model_dirs[number] = path
            atomic_artifact(path / "model.joblib", bytes.fromhex(self.artifacts[label]["raw_hex"]))
            settings = {
                "name": "model",
                "implementation": "mlserver_sklearn.SKLearnModel",
                "parameters": {"version": number, "uri": "model.joblib"},
                "cache_enabled": True,
            }
            write_new_file(path / "model-settings.json", encode(settings).encode())

    def event(self, kind: str, **values: Any) -> dict[str, Any]:
        result = {
            "sequence": len(self.events),
            "time_ns": perf_counter_ns(),
            "token": self.token.get(),
            "kind": kind,
            **values,
        }
        self.events.append(result)
        return result

    def object(self, model: Any) -> dict[str, Any]:
        return {
            "generation": self.generations.get(id(model)),
            "name": model.name,
            "version": model.version,
            "runtime_id": id(model),
            "object_id": id(model._model),
            "parameters": self._parameters(model._model),
            "pid": os.getpid(),
        }

    def _runtime_hooks(self) -> tuple[Any, Any, Any]:
        runtime = importlib.import_module("mlserver_sklearn").SKLearnModel
        original_load, original_predict = runtime.load, runtime.predict

        async def load(model: Any) -> bool:
            self.event("load_enter", name=model.name, version=model.version)
            try:
                result = await original_load(model)
            except Exception as exc:
                self.event("load_failure", error_type=type(exc).__name__)
                raise
            generation = f"generation-{len(self.loads)}"
            self.generations[id(model)] = generation
            observed = self.object(model)
            uri = await importlib.import_module("mlserver.utils").get_model_uri(model.settings)
            artifact_path = Path(uri).resolve()
            if not artifact_path.is_relative_to(self.directory.resolve()):
                raise ValueError("native loader escaped owned artifact root")
            observed["artifact"] = content_sha256(artifact_path.read_bytes())
            self.loads[generation] = observed
            self.event("load_return", actual=observed)
            return bool(result)

        async def predict(model: Any, payload: Any) -> Any:
            observed = self.object(model)
            identifier = f"compute-{len(self.events)}"
            self.event(
                "compute_enter",
                identifier=identifier,
                actual=observed,
                payload=payload.model_dump(),
            )
            if self.workflow == "delayed_fill" and self.token.get() == "old-inflight":
                self.entered.set()
                await asyncio.wait_for(self.release.wait(), timeout=8)
            result = await original_predict(model, payload)
            self.producer.set(identifier)
            self.event(
                "compute_return",
                identifier=identifier,
                actual=self.object(model),
                response=result.model_dump(),
            )
            return result

        return runtime, load, predict

    def _app(self) -> Any:
        settings_type = importlib.import_module("mlserver.settings").Settings
        settings = settings_type(
            _env_file=None,
            parallel_workers=0,
            cache_enabled=self.arm != "disabled",
            cache_size=100,
            metrics_endpoint=None,
            tracing_server=None,
            kafka_enabled=False,
            gzip_enabled=False,
        )
        self.registry = importlib.import_module("mlserver.registry").MultiModelRegistry()
        repository = importlib.import_module(
            "mlserver.repository.repository"
        ).SchemalessModelRepository(str(self.directory / "repository"))
        handlers = importlib.import_module("mlserver.handlers")
        plane = handlers.DataPlane(settings, self.registry)
        original_context = plane._infer_contextmanager

        @asynccontextmanager
        async def bound_context(name: str, version_id: str | None = None) -> Any:
            async with original_context(name, version_id) as model:
                generation = self.generations[id(model)]
                selected_token = self.selected.set(generation)
                epoch_token = self.entry_epoch.set(self.epoch)
                self.event("selected", actual=self.object(model), epoch=self.epoch)
                try:
                    yield model
                finally:
                    self.selected.reset(selected_token)
                    self.entry_epoch.reset(epoch_token)

        plane._infer_contextmanager = bound_context
        self.cache = plane._get_response_cache()
        if self.cache is not None:
            self._cache_hooks()
        return importlib.import_module("mlserver.rest.app").create_app(
            settings, plane, handlers.ModelRepositoryHandlers(repository, self.registry)
        )

    def _key(self, key: str) -> str:
        generation = self.selected.get()
        actual = self.loads[generation]
        if self.arm in {"route_key", "fenced", "generation_key"}:
            prefix: list[Any] = [actual["name"], actual["version"]]
            if self.arm == "generation_key":
                prefix.append(generation)
            return encode([prefix, key])
        return key

    def _cache_hooks(self) -> None:
        lookup_original, insert_original = self.cache.lookup, self.cache.insert

        async def lookup(key: str) -> str:
            effective = self._key(key)
            value = str(await lookup_original(effective))
            producer = self.insert_origins.get(effective)
            self.event(
                "lookup",
                key=key,
                effective=effective,
                hit=bool(value),
                value=value,
                producer=producer,
            )
            if value and producer is not None:
                self.producer.set(producer)
            return value

        async def insert(key: str, value: str) -> Any:
            effective = self._key(key)
            accepted = self.arm != "fenced" or self.entry_epoch.get() == self.epoch
            self.event(
                "insert",
                key=key,
                effective=effective,
                value=value,
                producer=self.producer.get(),
                accepted=accepted,
                entry_epoch=self.entry_epoch.get(),
                current_epoch=self.epoch,
            )
            if not accepted:
                return None
            result = await insert_original(effective, value)
            self.insert_origins[effective] = self.producer.get()
            return result

        self.cache.lookup, self.cache.insert = lookup, insert

    async def operation(self, token: str, route: str, body: Any = None) -> dict[str, Any]:
        if len(self.rows) >= 16:
            raise RuntimeError("native operation bound exceeded")
        row = {"token": token, "route": route, "body": body, "event_start": len(self.events)}
        self.rows.append(row)
        token_handle, producer_handle = self.token.set(token), self.producer.set("")
        started = perf_counter_ns()
        try:
            response = (
                await self.client.post(route, json=body)
                if body is not None
                else await self.client.post(route)
            )
            row.update(
                status=response.status_code,
                raw_response=response.text,
                response_headers=dict(response.headers),
                origin=self.producer.get() or None,
            )
            try:
                row["response"] = response.json() if response.content else None
            except ValueError:
                row["response"] = None
                row["response_decode_error"] = True
        except (ValueError, OSError, RuntimeError, TimeoutError) as exc:
            row["error_type"] = type(exc).__name__
            raise
        finally:
            row.update(elapsed_ns=perf_counter_ns() - started, event_stop=len(self.events))
            self.token.reset(token_handle)
            self.producer.reset(producer_handle)
        return row

    async def infer(self, token: str, number: str = "1", *, zero: bool = False) -> dict[str, Any]:
        # No request ID: native UUID is generated AFTER the cache lookup key.
        # Token is an observer-only ContextVar, not a body/header/cache-key field.
        return await self.operation(
            token,
            f"/v2/models/model/versions/{number}/infer",
            {
                "inputs": [
                    {
                        "name": "x",
                        "shape": [1, 1],
                        "datatype": "FP64",
                        "data": [0.0 if zero else 1.0],
                    }
                ],
            },
        )

    async def reload(self, token: str) -> dict[str, Any]:
        result = await self.operation(token, "/v2/repository/models/model/load")
        if result["status"] == 200:
            self.epoch += 1
            if self.arm in {"flush", "fenced"} and self.cache is not None:
                self.cache.cache.clear()
                self.insert_origins.clear()
                self.event("flush", epoch=self.epoch)
        return result

    async def schedule(self) -> None:
        await self.reload("initial-load")
        if self.workflow == "version_routes":
            await self.infer("v1-first", "1")
            await self.infer("v1-repeat", "1")
            await self.infer("v2-same-body", "2")
            await self.infer("v2-zero", "2", zero=True)
            await self.infer("v1-zero", "1", zero=True)
            return
        await self.infer("same-generation", zero=True)
        pending = None
        if self.workflow == "delayed_fill":
            pending = asyncio.create_task(self.infer("old-inflight"))
            await asyncio.wait_for(self.entered.wait(), timeout=8)
        else:
            await self.infer("before-reload")
        atomic_artifact(
            self.model_dirs["1"] / "model.joblib", bytes.fromhex(self.artifacts["C"]["raw_hex"])
        )
        await self.reload("successful-reload")
        if pending is not None:
            self.release.set()
            await pending
        await self.infer("fresh-after-reload")
        await self.infer("equal-output-after-reload", zero=True)
        path = self.model_dirs["1"] / "model.joblib"
        path.rename(path.with_suffix(".held-for-failure"))
        await self.reload("failed-reload")
        await self.infer("fresh-after-failed-reload")

    async def run(self) -> dict[str, Any]:
        runtime, load, predict = self._runtime_hooks()
        failure = None
        with patch.object(runtime, "load", load), patch.object(runtime, "predict", predict):
            app = self._app()
            httpx = importlib.import_module("httpx")
            # The native REST ASGI path is real; this workload is not network
            # throughput or distributed serving. It exercises concurrent tasks.
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://owned-asgi", trust_env=False, timeout=10
            ) as client:
                self.client = client
                try:
                    await asyncio.wait_for(self.schedule(), timeout=35)
                except (ValueError, OSError, RuntimeError, TimeoutError) as exc:
                    failure = type(exc).__name__
        return self.snapshot(failure)

    def snapshot(self, failure: str | None) -> dict[str, Any]:
        modules = (
            "mlserver.handlers.dataplane",
            "mlserver.registry",
            "mlserver.cache.local.local",
            "mlserver_sklearn.sklearn",
            "mlserver.utils",
        )
        return {
            "schema": "response-origin-native/v1",
            "arm": self.arm,
            "workflow": self.workflow,
            "terminal": "complete" if failure is None else "failed",
            "failure": failure,
            "rows": self.rows,
            "events": self.events,
            "loads": self.loads,
            "artifacts": self.artifacts,
            "environment": {
                "python": sys.version.split()[0],
                "packages": {name: version(name) for name in PACKAGES},
            },
            "native_source_sha256": {
                name: content_sha256(
                    Path(importlib.import_module(name).__file__ or "").read_bytes()
                )
                for name in modules
            },
            "native_boundary": "actual MLServer REST ASGI; same-process concurrent tasks, not TCP throughput",
        }


def worker(arm: str, workflow: str, directory: Path) -> dict[str, Any]:
    """Fresh opt-in worker; all models and files are generated within its root."""
    for key in tuple(os.environ):
        if key.startswith(("MLSERVER_", "OTEL_", "PROMETHEUS_")):
            os.environ.pop(key)
    os.environ["OTEL_SDK_DISABLED"] = "true"
    probe = OriginProbe(arm, workflow, directory)

    def deny(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("native ASGI study cannot use external sockets")

    # Create the loop before guards: Windows uses loopback for its self-pipe.
    with asyncio.Runner() as runner:
        runner.get_loop()
        with (
            patch.object(socket.socket, "connect", deny),
            patch.object(socket.socket, "connect_ex", deny),
            patch.object(socket, "create_connection", deny),
        ):
            try:
                result = runner.run(probe.run())
            except (ValueError, OSError, RuntimeError, TimeoutError) as exc:
                result = probe.snapshot(type(exc).__name__)
            finally:
                # Cancellation/finalizers remain under the same network guard.
                runner.close()
    result["provider_calls"] = 0
    return result
