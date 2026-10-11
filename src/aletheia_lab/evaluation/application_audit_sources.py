"""Two native local serving applications with separate reference and receipt sinks.

Only fresh trusted sklearn artifacts are deserialized. Selection is native;
released-byte integrity is a declared operator overlay, not a framework policy.
Capture assumes serial loading and stable files from first open, not a hostile host.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Any
from unittest.mock import patch

from aletheia_lab.evaluation.model_load_application import atomic_artifact
from aletheia_lab.evaluation.model_load_application_capture import NativeCapture, descriptor_bytes
from aletheia_lab.evaluation.model_load_serving_capture import tree_fingerprint
from aletheia_lab.project.identity import content_sha256

PROBE = [[-2.0], [-1.0], [1.0], [2.0]]
PACKAGES = {"bentoml": "1.4.39", "mlflow": "3.9.0"}


class PickleCapture(NativeCapture):
    """Observe the original stream passed to MLflow's native pickle loader."""

    @contextmanager
    def installed(self, *, on_open: Any = None) -> Iterator[None]:
        pickle = importlib.import_module("pickle")
        cloudpickle = importlib.import_module("cloudpickle")
        original = pickle.load

        def observed(stream: Any, *args: Any, **kwargs: Any) -> Any:
            if self.scope is None:
                raise RuntimeError("unscoped native deserialization")
            if on_open is not None:
                on_open(stream)
            raw = descriptor_bytes(stream, self.directory)
            self.reference.append({"scope": self.scope, "raw_hex": raw.hex()})
            self.receipts.append({"scope": self.scope, "descriptor_sha256": content_sha256(raw)})
            return original(stream, *args, **kwargs)

        with patch.object(pickle, "load", observed), patch.object(cloudpickle, "load", observed):
            yield


def estimators() -> dict[str, Any]:
    cls = importlib.import_module("sklearn.tree").DecisionTreeClassifier
    return {
        name: cls(max_depth=2, random_state=101).fit(PROBE, labels)
        for name, labels in (("A", [0, 0, 1, 1]), ("B", [1, 1, 0, 0]))
    }


class ApplicationSource:
    """New source process owns all artifacts and native application generations."""

    def __init__(self, stack: str, directory: Path) -> None:
        if stack not in PACKAGES or directory.exists() or directory.is_symlink():
            raise ValueError("supported stack and fresh owned directory required")
        package = "mlflow-skinny" if stack == "mlflow" else "bentoml"
        if version(package) != PACKAGES[stack]:
            raise RuntimeError("serving package differs from selected version")
        directory.mkdir(parents=True)
        self.stack, self.directory = stack, directory
        self.models = estimators()
        self.paths: dict[str, Path] = {}
        self.payloads: dict[str, bytes] = {}
        self.tags: dict[str, str] = {}
        self.capture = (PickleCapture if stack == "mlflow" else NativeCapture)(directory)
        self.native: Any = None
        self.registry: Any = None
        self.published_b = False
        self.start_attempts = 0
        self.http_requests = 0
        self._prepare()
        self.domain = sorted(content_sha256(value) for value in self.payloads.values())
        self.fingerprints = {name: tree_fingerprint(model) for name, model in self.models.items()}

    def _prepare(self) -> None:
        if self.stack == "bentoml":
            self.native = importlib.import_module("bentoml")
            containers = importlib.import_module("bentoml._internal.configuration.containers")
            store = importlib.import_module("bentoml._internal.models").ModelStore
            containers.BentoMLContainer.model_store.set(store(str(self.directory / "store")))
            framework = importlib.import_module("bentoml.sklearn")
            for name, model in self.models.items():
                saved = framework.save_model("audit:v1" if name == "A" else "partner:v1", model)
                saved = self.native.models.get(saved.tag)
                self.tags[name] = str(saved.tag)
                self.paths[name] = Path(saved.path_of("saved_model.pkl"))
        else:
            self.native = importlib.import_module("mlflow")
            framework = importlib.import_module("mlflow.sklearn")
            uri = "sqlite:///" + str(self.directory / "registry.sqlite")
            self.native.set_tracking_uri(uri)
            self.native.set_registry_uri(uri)
            self.registry = importlib.import_module("mlflow.tracking").MlflowClient()
            self.registry.create_registered_model("audit")
            for name, model in self.models.items():
                path = self.directory / name
                framework.save_model(
                    model, str(path), serialization_format="pickle", pip_requirements=[]
                )
                saved = self.registry.create_model_version("audit", str(path))
                self.tags[name] = str(saved.version)
                self.paths[name] = path / "model.pkl"
        self.payloads = {name: path.read_bytes() for name, path in self.paths.items()}
        if any(not 0 < len(value) <= 262144 for value in self.payloads.values()):
            raise ValueError("fresh artifact exceeds supported native capture bound")
        self.select("A")

    def select(self, name: str) -> None:
        """Native alias or latest pointer. No reload is invented for either source."""
        if name not in self.models:
            raise ValueError("unknown fresh model")
        self.selected = name
        if self.stack == "mlflow":
            self.registry.set_registered_model_alias("audit", "champion", self.tags[name])
        elif name == "B" and not self.published_b:
            saved = importlib.import_module("bentoml.sklearn").save_model(
                "audit:v2", self.models["B"]
            )
            saved = self.native.models.get(saved.tag)
            path = Path(saved.path_of("saved_model.pkl"))
            if path.read_bytes() != self.payloads["B"]:
                raise ValueError("fresh release serialization differs from trusted partner")
            self.paths["B"], self.tags["B"] = path, str(saved.tag)
            self.published_b = True

    def replace(self, selected: str, actual: str | None) -> None:
        payload = self.payloads[actual] if actual else b"invalid-owned-artifact"
        atomic_artifact(self.paths[selected], payload)

    async def start(self, scope: str) -> NativeApplication:
        self.start_attempts += 1
        self.capture.scope = scope
        with self.capture.installed():
            if self.stack == "bentoml":
                app = await self._bento_app()
            else:
                server = importlib.import_module("mlflow.pyfunc.scoring_server")
                # Native alias resolution and loader, not a hand-written predict route.
                model = server.load_model_with_mlflow_config("models:/audit@champion")
                app = NativeApplication(
                    server.init(model),
                    model._model_impl.sklearn_model,
                    self.tags[self.selected],
                    source=self,
                )
                await app.open()
        return app

    async def _bento_app(self) -> NativeApplication:
        holder: list[Any] = []
        native = self.native
        tag = "audit:latest" if self.selected == "B" or not self.published_b else self.tags["A"]
        reference = native.models.BentoModel(tag)

        class Serving:
            source = reference

            def __init__(self) -> None:
                self.stored = self.source
                self.model = self.stored.load_model()
                holder.append(self)

            async def predict(self, values: list[list[float]]) -> list[int]:
                return [int(value) for value in self.model.predict(values)]

        service_class: Any = Serving
        service_class.predict = native.api(service_class.predict)
        service = native.service(name="AuditDevelopment", metrics={"enabled": False})(service_class)
        # BentoML expects a module-level discoverable service for configuration.
        globals()["_ACTIVE_SERVICE"] = service
        app = NativeApplication(service.to_asgi(), None, "", bento=True, source=self)
        await app.open()
        app.model = holder[-1].model
        app.selected_tag = str(holder[-1].stored.tag)
        return app

    def environment(self) -> dict[str, Any]:
        modules = (
            ["_bentoml_sdk.models.base", "_bentoml_impl.server.app", "bentoml.sklearn"]
            if self.stack == "bentoml"
            else ["mlflow.pyfunc.scoring_server", "mlflow.sklearn"]
        )
        hashes = {}
        for name in modules:
            module = importlib.import_module(name)
            if module.__file__ is None:
                raise ValueError("native source module has no inspectable file")
            hashes[name] = content_sha256(Path(module.__file__).read_bytes())
        return {
            "stack": self.stack,
            "versions": {name: version(name) for name in ("numpy", "scikit-learn", "httpx")},
            "serving_version": PACKAGES[self.stack],
            "source_sha256": hashes,
        }


class NativeApplication:
    def __init__(
        self,
        app: Any,
        model: Any,
        selected_tag: str,
        *,
        bento: bool = False,
        source: ApplicationSource,
    ) -> None:
        self.app, self.model, self.selected_tag, self.bento = app, model, selected_tag, bento
        self.source = source
        self.lifespan: Any = None
        self.client: Any = None

    async def open(self) -> None:
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        httpx = importlib.import_module("httpx")
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://owned.invalid"
        )

    async def request(self) -> dict[str, Any]:
        route = "/predict" if self.bento else "/invocations"
        body = {"values": PROBE} if self.bento else {"dataframe_split": {"data": PROBE}}
        response = await self.client.post(route, json=body)
        health = await self.client.get("/readyz" if self.bento else "/ping")
        self.source.http_requests += 2
        output = response.json()
        predictions = output if self.bento else output.get("predictions")
        return {
            "http_status": response.status_code,
            "health_status": health.status_code,
            "predictions": predictions,
            "object_fingerprint": tree_fingerprint(self.model),
            "selected_tag": self.selected_tag,
        }

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
        if self.lifespan is not None:
            await self.lifespan.__aexit__(None, None, None)
