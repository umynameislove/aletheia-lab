"""Native local Bento dependencies and whole-pipeline architectural control.

The operator pins an ordered release tuple before each serial request. Bento
does not itself enforce that overlay. Only fresh owned sklearn artifacts load.
"""

from __future__ import annotations

import importlib
from importlib.metadata import version
from pathlib import Path
from pickle import UnpicklingError
from time import perf_counter_ns
from typing import Any

from aletheia_lab.evaluation.application_audit_sources import PROBE
from aletheia_lab.evaluation.model_load_application import atomic_artifact
from aletheia_lab.evaluation.model_load_application_capture import NativeCapture
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.model_load_serving_capture import tree_fingerprint
from aletheia_lab.project.identity import content_sha256

CASES = (
    ("initial", "11", "11", 0),
    ("authorized_old", "11", "11", 0),
    ("stale_route", "11", "22", 0),
    ("new_release", "22", "22", 0),
    ("attempt_one", "22", "22", 1),
    ("invalid_input", "22", "22", 0),
    ("forbidden_mix", "12", "22", 0),
    ("authorized_mix", "12", "12", 0),
    ("reverse_mix", "21", "11", 0),
    ("authorized_reverse", "21", "21", 0),
    ("failed_startup", "22", "22", 0),
    ("restored", "22", "22", 0),
)


def digest(value: Any) -> str:
    return content_sha256(encode(value).encode())


def fingerprint(model: Any) -> str:
    if hasattr(model, "tree_"):
        return tree_fingerprint(model)
    return digest({"mean": model.mean_.tolist(), "scale": model.scale_.tolist()})


class JointSource:
    def __init__(self, directory: Path) -> None:
        if version("bentoml") != "1.4.39" or directory.exists():
            raise ValueError("selected Bento runtime and fresh owned source required")
        directory.mkdir(parents=True)
        self.directory = directory
        self.native = importlib.import_module("bentoml")
        containers = importlib.import_module("bentoml._internal.configuration.containers")
        store = importlib.import_module("bentoml._internal.models").ModelStore
        containers.BentoMLContainer.model_store.set(store(str(directory / "store")))
        self.framework = importlib.import_module("bentoml.sklearn")
        scaler = importlib.import_module("sklearn.preprocessing").StandardScaler
        tree = importlib.import_module("sklearn.tree").DecisionTreeClassifier
        numpy = importlib.import_module("numpy")
        self.models: dict[str, Any] = {}
        for revision in (1, 2):
            encoder = scaler().fit(numpy.asarray(PROBE) + (0 if revision == 1 else 10))
            classifier = tree(max_depth=2, random_state=101).fit(
                encoder.transform(PROBE), [0, 0, 1, 1]
            )
            self.models.update({f"e{revision}": encoder, f"c{revision}": classifier})
        self.tags: dict[str, str] = {}
        self.paths: dict[str, Path] = {}
        self.raw: dict[str, bytes] = {}
        self.capture = NativeCapture(directory)
        self.loads: dict[str, dict[str, Any]] = {}
        self.reference_loads: dict[str, dict[str, Any]] = {}
        self.uses: list[dict[str, Any]] = []
        self.reference_uses: list[dict[str, Any]] = []
        self.request = ""
        self.attempt = 0
        self.startups = self.http_requests = 0
        self._save_models()

    def _save_models(self) -> None:
        for key, model in self.models.items():
            saved = self.framework.save_model(f"{key}:owned", model)
            saved = self.native.models.get(saved.tag)
            self.tags[key] = str(saved.tag)
            self.paths[key] = Path(saved.path_of("saved_model.pkl"))
            self.raw[key] = self.paths[key].read_bytes()

    def release(self, pair: str) -> list[str]:
        return [content_sha256(self.raw[f"e{pair[0]}"]), content_sha256(self.raw[f"c{pair[1]}"])]

    def stage(self, key: str, generation: str) -> Any:
        owner = self
        role = "encoder" if key.startswith("e") else "classifier"
        reference = self.native.models.BentoModel(self.tags[key])

        class Stage:
            model_reference = reference

            def __init__(self) -> None:
                offset = len(owner.capture.receipts)
                stored = self.model_reference
                self.model = stored.load_model()
                receipts = owner.capture.receipts[offset:]
                raw = owner.capture.reference[offset:]
                if len(receipts) != 1 or len(raw) != 1:
                    raise ValueError("stage requires one native descriptor observation")
                owner.loads[generation] = {
                    "role": role,
                    "artifact": receipts[0]["descriptor_sha256"],
                    "selected": content_sha256(owner.raw[key]),
                    "fingerprint": fingerprint(self.model),
                    "count": 1,
                    "closed": True,
                }
                owner.reference_loads[generation] = {
                    "raw_hex": raw[0]["raw_hex"],
                    "selected_key": key,
                    "fingerprint": fingerprint(self.model),
                    "native_tag": str(stored.tag),
                }

            async def apply(self, values: list[list[float]]) -> Any:
                before = fingerprint(self.model)
                output = (
                    self.model.transform(values)
                    if role == "encoder"
                    else self.model.predict(values)
                ).tolist()
                ordinal = len(owner.uses)
                owner.uses.append(
                    {
                        "request": owner.request,
                        "attempt": owner.attempt,
                        "ordinal": ordinal,
                        "role": role,
                        "generation": generation,
                        "artifact": owner.loads[generation]["artifact"],
                        "fingerprint": before,
                        "input": digest(values),
                        "output": digest(output),
                    }
                )
                # This reference sink retains actual operands/results, not checker
                # verdicts. It shares the trusted serial invocation boundary.
                owner.reference_uses.append(
                    {
                        "generation": generation,
                        "object_fingerprint": before,
                        "input_values": values,
                        "output_values": output,
                    }
                )
                return output

        service_class: Any = Stage
        service_class.apply = self.native.api(service_class.apply)
        return self.native.service(name=generation.replace("-", ""), metrics={"enabled": False})(
            service_class
        )

    async def open(self, pair: str, number: int) -> tuple[Any, Any]:
        owner = self
        encoder = self.stage(f"e{pair[0]}", f"g{number}-e")
        classifier = self.stage(f"c{pair[1]}", f"g{number}-c")

        class Serving:
            encoding = owner.native.depends(encoder)
            prediction = owner.native.depends(classifier)

            async def predict(self, values: list[list[float]]) -> list[int]:
                transformed = await self.encoding.apply(values)
                return [int(value) for value in await self.prediction.apply(transformed)]

        service_class: Any = Serving
        service_class.predict = self.native.api(service_class.predict)
        service = self.native.service(name=f"Joint{number}", metrics={"enabled": False})(
            service_class
        )
        globals()["_ACTIVE_JOINT_SERVICE"] = service
        app = service.to_asgi()
        lifespan = app.router.lifespan_context(app)
        self.startups += 1
        self.capture.scope = f"startup-{number}"
        with self.capture.installed():
            await lifespan.__aenter__()
        return app, lifespan

    async def request_row(
        self, app: Any, index: int, case: tuple[str, str, str, int]
    ) -> dict[str, Any]:
        name, _, authorized, attempt = case
        values = [[1.0, 2.0]] if name == "invalid_input" else PROBE
        self.request, self.attempt = f"request-{index:02d}", attempt
        self.uses, self.reference_uses = [], []
        expected = self.release(authorized)  # Frozen BEFORE dispatch.
        started = perf_counter_ns()
        httpx = importlib.import_module("httpx")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://owned.invalid",
        ) as client:
            response = await client.post("/predict", json={"values": values})
            health = await client.get("/readyz")
        self.http_requests += 2
        output = response.json() if response.status_code == 200 else None
        frame = self.frame(expected, values, output, response.status_code != 200)
        return {
            "case": name,
            "frame": frame,
            "reference_uses": self.reference_uses,
            "http_status": response.status_code,
            "health_status": health.status_code,
            "input_values": values,
            "output_values": output,
            "native_request_ns": perf_counter_ns() - started,
        }

    def frame(self, expected: list[str], values: Any, output: Any, failed: bool) -> dict[str, Any]:
        return {
            "request": self.request,
            "attempt": self.attempt,
            "revision": 0,
            "expected": expected,
            "input": digest(values),
            "output": digest(output) if output is not None else None,
            "declared": 2,
            "closed": True,
            "failed": failed,
            "loads": {use["generation"]: self.loads[use["generation"]] for use in self.uses},
            "uses": self.uses,
        }


async def native_capture(directory: Path) -> dict[str, Any]:
    source = JointSource(directory / "source")
    rows: list[dict[str, Any]] = []
    groups = ((0, 3, "11"), (3, 6, "22"), (6, 8, "12"), (8, 10, "21"))
    for generation, (start, stop, pair) in enumerate(groups):
        app, lifespan = await source.open(pair, generation)
        try:
            for index in range(start, stop):
                rows.append(await source.request_row(app, index, CASES[index]))
        finally:
            await lifespan.__aexit__(None, None, None)
    atomic_artifact(source.paths["c2"], b"invalid-owned-classifier")
    failure = None
    try:
        _, lifespan = await source.open("22", 4)
    except (
        ValueError,
        OSError,
        RuntimeError,
        EOFError,
        ImportError,
        AttributeError,
        UnpicklingError,
    ) as exc:
        failure = type(exc).__name__
    else:
        await lifespan.__aexit__(None, None, None)
    source.request, source.attempt, source.uses = "request-10", 0, []
    rows.append(
        {
            "case": "failed_startup",
            "frame": source.frame(source.release("22"), PROBE, None, True),
            "reference_uses": [],
            "startup_error": failure,
            "input_values": PROBE,
            "output_values": None,
            "http_status": None,
            "health_status": None,
        }
    )
    atomic_artifact(source.paths["c2"], source.raw["c2"])
    app, lifespan = await source.open("22", 5)
    try:
        rows.append(await source.request_row(app, 11, CASES[11]))
    finally:
        await lifespan.__aexit__(None, None, None)
    pipeline = await pipeline_control(source)
    modules = (
        "_bentoml_sdk.service.dependency",
        "_bentoml_sdk.service.factory",
        "_bentoml_impl.server.app",
        "bentoml.sklearn",
    )
    return {
        "rows": rows,
        "pipeline": pipeline,
        "releases": {
            key: {"raw_hex": raw.hex(), "fingerprint": fingerprint(source.models[key])}
            for key, raw in source.raw.items()
        },
        "reference_loads": source.reference_loads,
        "descriptor_observations": source.capture.reference,
        "startups": source.startups,
        "http_requests": source.http_requests,
        "native_source_sha256": {name: module_digest(name) for name in modules},
        "versions": {name: version(name) for name in ("bentoml", "scikit-learn", "numpy", "httpx")},
    }


def module_digest(name: str) -> str:
    path = importlib.import_module(name).__file__
    if path is None:
        raise ValueError("native module source is unavailable")
    return content_sha256(Path(path).read_bytes())


async def pipeline_control(source: JointSource) -> dict[str, Any]:
    pipeline = importlib.import_module("sklearn.pipeline").Pipeline(
        [("encoder", source.models["e1"]), ("classifier", source.models["c1"])]
    )
    saved = source.framework.save_model("whole-pipeline:owned", pipeline)
    saved = source.native.models.get(saved.tag)
    raw = Path(saved.path_of("saved_model.pkl")).read_bytes()
    reference = source.native.models.BentoModel(str(saved.tag))

    class Serving:
        model_reference = reference

        def __init__(self) -> None:
            self.model = self.model_reference.load_model()

        async def predict(self, values: list[list[float]]) -> list[int]:
            return [int(value) for value in self.model.predict(values)]

    service_class: Any = Serving
    service_class.predict = source.native.api(service_class.predict)
    service = source.native.service(name="WholePipeline", metrics={"enabled": False})(service_class)
    globals()["_ACTIVE_JOINT_SERVICE"] = service
    app = service.to_asgi()
    lifespan = app.router.lifespan_context(app)
    offset = len(source.capture.reference)
    source.capture.scope = "whole-pipeline"
    source.startups += 1
    with source.capture.installed():
        await lifespan.__aenter__()
    try:
        httpx = importlib.import_module("httpx")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://owned.invalid"
        ) as client:
            response = await client.post("/predict", json={"values": PROBE})
            health = await client.get("/readyz")
        source.http_requests += 2
        return {
            "stage_count": len(pipeline.steps),
            "raw_hex": raw.hex(),
            "reference": source.capture.reference[offset:],
            "http_status": response.status_code,
            "health_status": health.status_code,
            "output": response.json(),
            "independent_output": pipeline.predict(PROBE).tolist(),
        }
    finally:
        await lifespan.__aexit__(None, None, None)
