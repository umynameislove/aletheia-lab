"""Owned synthetic adapter contracts, not upstream/native qualification.

Execute the real pin gate, selected-AST loader, transport and request adapter.
The fixture source bodies and SDK types below are inert authored state machines;
no framework, fitted model, frozen study input or external service is involved.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from types import ModuleType, SimpleNamespace

import pytest

from aletheia_lab.evaluation import serving_native_development as native

_REGISTRY = """
class ModelNotFound(Exception):
    pass

class MultiModelRegistry:
    def __init__(self, on_model_load=(), on_model_unload=(), on_model_reload=None):
        self.models = {}
        self.on_load = on_model_load
        self.on_unload = on_model_unload
        self.on_reload = on_model_reload

    async def load(self, settings):
        previous = self.models.get(settings.name)
        model = settings.implementation(settings)
        await model.load()
        self.models[settings.name] = model
        for callback in self.on_load:
            await callback(model)
        # The two authored fixtures differ only in reload callback disposition.
        if previous is not None and self.on_reload is None:
            for callback in self.on_unload:
                await callback(previous)
        return model

    async def unload(self, name):
        self.models.pop(name, None)

    async def get_model(self, name):
        if name not in self.models:
            raise ModelNotFound(name)
        return self.models[name]

    async def get_models(self):
        return list(self.models.values())
"""

_WORKER = """
raise AssertionError("whole worker module must never execute")
class Worker:
    async def _process_model_update(self, update):
        if update.update_type == ModelUpdateType.Load:
            return await self._model_registry.load(update.model_settings)
        if update.update_type == ModelUpdateType.Unload:
            return await self._model_registry.unload(update.model_settings.name)
        raise ValueError("unknown synthetic update")
"""

_POOL = """
raise AssertionError("whole pool module must never execute")
class InferencePool:
    def _should_load_model(self, model):
        return self._settings.parallel_workers > 0 and model.settings.parallel_workers > 0

    async def load_model(self, model):
        if not self._should_load_model(model):
            return model
        for worker in self._workers.values():
            await worker.send_update(ModelUpdateMessage(
                update_type=ModelUpdateType.Load, model_settings=model.settings))
        model.predict = self.parallel(model.predict)
        return model

    async def unload_model(self, model):
        for worker in self._workers.values():
            await worker.send_update(ModelUpdateMessage(
                update_type=ModelUpdateType.Unload, model_settings=model.settings))
"""

_SETTINGS = """
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

RELOAD_TOP = RELOAD_SETTING

class ModelSettings:
    @classmethod
    def parse_file(cls, filename):
        config = Path(filename)
        document = json.loads(config.read_bytes())
        name, attribute = document["implementation"].rsplit(".", 1)
        if RELOAD_TOP or name not in sys.modules:
            path = config.parent / (name + ".py")
            module = ModuleType(name)
            module.__file__ = str(path)
            sys.modules[name] = module
            sys.path.insert(0, str(config.parent))
            try:
                exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
            finally:
                sys.path.pop(0)
        return SimpleNamespace(name=document["name"],
                               implementation=getattr(sys.modules[name], attribute))
"""


@pytest.fixture
def sdk(monkeypatch):
    requests = []

    class Model:
        def __init__(self, settings):
            self.settings, self.name = settings, settings.name

    class Parameters:
        def __init__(self, *, extra):
            self.extra = extra

    class Settings:
        def __init__(self, *, name, implementation, parallel_workers=1, parameters=None):
            self.name, self.implementation = name, implementation
            self.parallel_workers, self.parameters = parallel_workers, parameters

    class Request:
        def __init__(self, *, id, inputs):
            if not isinstance(id, str) or not isinstance(inputs, list):
                raise ValueError("synthetic request schema")
            self.id, self.inputs = id, inputs
            requests.append(id)

    class Output:
        def __init__(self, *, name, shape, datatype, data):
            if name != "y" or shape != [1] or datatype != "INT64" or len(data) != 1:
                raise ValueError("synthetic scalar wire schema")
            self.name, self.shape, self.datatype = name, shape, datatype
            self.data = SimpleNamespace(root=list(data))

    class Response:
        def __init__(self, *, model_name, id, outputs):
            self.model_name, self.id, self.outputs = model_name, id, outputs

    package = ModuleType("mlserver")
    package.__path__ = []
    model, settings, types = (
        ModuleType("mlserver." + name) for name in ("model", "settings", "types")
    )
    model.MLModel = Model
    settings.ModelSettings, settings.ModelParameters = Settings, Parameters
    types.InferenceRequest, types.InferenceResponse, types.ResponseOutput = (
        Request,
        Response,
        Output,
    )
    for name, value in {
        "mlserver": package,
        "mlserver.model": model,
        "mlserver.settings": settings,
        "mlserver.types": types,
    }.items():
        monkeypatch.setitem(sys.modules, name, value)
    monkeypatch.setattr(native.importlib.metadata, "version", lambda name: "1.7.1")
    return SimpleNamespace(model=model, settings=settings, types=types, requests=requests)


@pytest.fixture
def sources(tmp_path, monkeypatch):
    root = tmp_path / "synthetic-source"
    root.mkdir()
    documents = {
        "ml581-affected-mlserver-registry.py": _REGISTRY,
        "ml581-fixed-mlserver-registry.py": _REGISTRY,
        "ml581-affected-mlserver-parallel-worker.py": _WORKER,
        "ml581-affected-mlserver-parallel-pool.py": _POOL,
        "ml705-affected-mlserver-settings.py": _SETTINGS.replace("RELOAD_SETTING", "False"),
        "ml705-fixed-mlserver-settings.py": _SETTINGS.replace("RELOAD_SETTING", "True"),
    }
    pins = {}
    for name, text in documents.items():
        raw = text.encode()
        (root / name).write_bytes(raw)
        pins[name] = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(native, "PINS", pins)
    return native.checked_sources(root)


def test_runtime_accepts_exact_version_and_resolves_only_declared_sdk_modules(sdk):
    assert native._runtime() == (sdk.model, sdk.settings, sdk.types)


@pytest.mark.parametrize("version", ["1.7.0", "1.7.2"])
def test_runtime_version_drift_fails_before_any_sdk_import(monkeypatch, version):
    imported = []
    monkeypatch.setattr(native.importlib.metadata, "version", lambda name: version)
    monkeypatch.setattr(native.importlib, "import_module", lambda name: imported.append(name))
    with pytest.raises(ValueError, match="requires MLServer 1.7.1"):
        native._runtime()
    assert imported == []


def test_runtime_missing_distribution_is_not_silently_accepted(monkeypatch):
    def missing(name):
        raise native.importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(native.importlib.metadata, "version", missing)
    with pytest.raises(native.importlib.metadata.PackageNotFoundError):
        native._runtime()


def test_transport_routes_old_handle_to_current_worker_then_honors_unload(sources, sdk):
    registry = native._source_module(sources["ml581-fixed-mlserver-registry.py"], "toy_registry")
    pool, worker, trace = native._transport(sources, registry)
    delivered = []

    class Scalar(sdk.model.MLModel):
        async def load(self):
            self.value = self.settings.parameters.extra["value"]
            return True

        async def predict(self, request):
            delivered.append((self.value, request.id))
            return sdk.types.InferenceResponse(
                model_name=self.name,
                id=request.id,
                outputs=[
                    sdk.types.ResponseOutput(
                        name="y", shape=[1], datatype="INT64", data=[self.value]
                    )
                ],
            )

    def make(value, workers=1):
        return Scalar(
            sdk.settings.ModelSettings(
                name="named",
                implementation=Scalar,
                parallel_workers=workers,
                parameters=sdk.settings.ModelParameters(extra={"value": value}),
            )
        )

    async def exercise():
        old, current = make(3), make(17)
        await old.load()
        await pool.load_model(old)
        cold = await old.predict(sdk.types.InferenceRequest(id="cold", inputs=[]))
        await current.load()
        await pool.load_model(current)
        rebound = await old.predict(sdk.types.InferenceRequest(id="old-handle", inputs=[]))
        assert (old.value, current.value) == (3, 17)
        assert cold.outputs[0].data.root == [3]
        assert rebound.outputs[0].data.root == [17] and rebound.id == "old-handle"
        assert delivered == [(3, "cold"), (17, "old-handle")]
        disabled = make(99, workers=0)
        await pool.load_model(disabled)
        assert len(trace) == 2
        await pool.unload_model(current)
        with pytest.raises(registry.ModelNotFound):
            await old.predict(sdk.types.InferenceRequest(id="after-unload", inputs=[]))
        with pytest.raises(ValueError, match="unknown synthetic update"):
            await worker._process_model_update(
                SimpleNamespace(update_type=99, model_settings=current.settings)
            )
        assert await worker._model_registry.get_models() == []

    asyncio.run(exercise())
    assert trace == [
        {"kind": "load", "name": "named"},
        {"kind": "load", "name": "named"},
        {"kind": "unload", "name": "named"},
    ]


@pytest.mark.parametrize(
    "side, after, error, kinds",
    [
        ("affected", None, "ModelNotFound", ["load", "load", "unload"]),
        ("fixed", [10], None, ["load", "load"]),
    ],
)
def test_registry_callbacks_keep_request_identity_and_repair_future_requests(
    sources, sdk, side, after, error, kinds
):
    row = asyncio.run(native._registry_case(sources, side))
    assert row["cold"] == [1] and row["after_reload"] == after
    assert row["native_failure"] == error and row["component_repair_output"] == [10]
    assert row["worker_model_count_after_repair"] == 1
    assert [item["kind"] for item in row["reload_trace"]] == kinds
    assert {item["name"] for item in row["reload_trace"]} == {"reload-probe"}
    assert sdk.requests == ["first", "after", "repair"]
    assert row["artifact_signature_can_prove_availability"] is False
    native._verify_registry_row(row)
    with pytest.raises(ValueError, match="observation or query"):
        native._verify_registry_row({**row, "component_repair_output": [1]})


@pytest.mark.parametrize(
    "side, helper, expected",
    [
        ("affected", False, 1),
        ("fixed", False, 10),
        ("affected", True, 1),
        ("fixed", True, 1),
    ],
)
def test_settings_adapter_distinguishes_top_cache_from_helper_closure(
    sources, sdk, tmp_path, monkeypatch, side, helper, expected
):
    signatures, verifications = [], []

    def sign(directory, bundle, key):
        signatures.append(directory)
        bundle.write_bytes(b"inert synthetic signature")
        key.write_bytes(b"inert synthetic key")
        return native.artifact_closure(directory)

    def verify(directory, bundle, key):
        assert bundle.read_bytes() == b"inert synthetic signature"
        assert key.read_bytes() == b"inert synthetic key"
        verifications.append(directory)

    monkeypatch.setattr(native, "sign_local", sign)
    monkeypatch.setattr(native, "verify_local", verify)
    before = sys.dont_write_bytecode
    row = asyncio.run(native._settings_case(sources, tmp_path, side, helper))
    assert sys.dont_write_bytecode is before
    assert row["initial_output"] == 1 and row["after_update_output"] == expected
    assert row["component_repair_output"] == (1 if helper else 10)
    assert row["closure_repair_output"] == 10
    expected_verdict = "compliant" if expected == 10 else "noncompliant"
    assert row["integrated_baseline_verdict"] == expected_verdict
    assert row["candidate_same_witness_verdict"] == expected_verdict
    assert row["artifact_only_actual_use_verdict"] == "unknown"
    assert sdk.requests == ["initial", "after-update", "component", "closure-reload"]
    assert len(signatures) == 1 and verifications == signatures * 2
    condition = "helper" if helper else "top"
    native._verify_settings_row({**row, "condition": condition}, tmp_path)
    assert verifications == signatures * 3
    with pytest.raises(ValueError, match="settings observation"):
        native._verify_settings_row(
            {**row, "condition": condition, "closure_repair_output": 1}, tmp_path
        )
    assert not any(name.startswith(("_serving_probe_", "_serving_helper_")) for name in sys.modules)


def test_transport_rechecks_owned_ast_pin_before_executing_methods(sources, sdk):
    registry = native._source_module(sources["ml581-fixed-mlserver-registry.py"], "toy_registry")
    path = sources["ml581-affected-mlserver-parallel-worker.py"]
    path.write_text("raise AssertionError('tampered source must not execute')\n")
    with pytest.raises(ValueError, match="pin mismatch before execution"):
        native._transport(sources, registry)
