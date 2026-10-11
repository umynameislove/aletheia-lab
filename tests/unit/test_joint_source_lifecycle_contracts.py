"""Owned SDK/ASGI boundaries with generated data, not native Bento qualification."""

import asyncio
import hashlib
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import joblib
import pytest

from aletheia_lab.evaluation import joint_inference_source as source


@pytest.fixture
def sdk(tmp_path, monkeypatch):
    records, client_calls, instances = [], [], []
    setting = SimpleNamespace(value=None)
    setting.set = lambda value: setattr(setting, "value", value)

    def save(tag, model):
        path = Path(setting.value) / tag.replace(":", "-")
        path.mkdir(parents=True)
        joblib.dump(model, path / "saved_model.pkl")
        artifact = SimpleNamespace(tag=tag, path_of=lambda name: str(path / name))
        records.append((tag, model, artifact))
        return artifact

    class Context:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    def service(**kwargs):
        def decorate(cls):
            cls.to_asgi = lambda: SimpleNamespace(
                router=SimpleNamespace(lifespan_context=lambda app: Context())
            )
            instances.append(cls)
            return cls

        return decorate

    native = SimpleNamespace(
        models=SimpleNamespace(
            get=lambda tag: next(record[2] for record in records if record[0] == tag)
        ),
        api=lambda function: function,
        service=service,
        depends=lambda cls: cls,
    )
    modules = {
        "bentoml": native,
        "bentoml._internal.configuration.containers": SimpleNamespace(
            BentoMLContainer=SimpleNamespace(model_store=setting)
        ),
        "bentoml._internal.models": SimpleNamespace(ModelStore=lambda name: name),
        "bentoml.sklearn": SimpleNamespace(save_model=save),
    }
    original_import = source.importlib.import_module
    monkeypatch.setattr(
        source.importlib,
        "import_module",
        lambda name: modules[name] if name in modules else original_import(name),
    )
    monkeypatch.setattr(source, "version", lambda name: "1.4.39")
    monkeypatch.delitem(source.__dict__, "_ACTIVE_JOINT_SERVICE", raising=False)
    yield SimpleNamespace(
        native=native, modules=modules, records=records, instances=instances, calls=client_calls
    )
    source.__dict__.pop("_ACTIVE_JOINT_SERVICE", None)


def test_constructor_generates_only_owned_models_and_records_exact_serializations(tmp_path, sdk):
    directory = tmp_path / "generated"
    owner = source.JointSource(directory)
    assert list(owner.models) == ["e1", "c1", "e2", "c2"]
    assert owner.startups == owner.http_requests == 0
    assert owner.loads == owner.reference_loads == {} and owner.uses == owner.reference_uses == []
    assert len(sdk.records) == 4
    for key, path in owner.paths.items():
        assert path.is_relative_to(directory)
        assert owner.raw[key] == path.read_bytes() and owner.tags[key] == key + ":owned"
        restored = joblib.load(path)
        assert type(restored) is type(owner.models[key])
    expected = [hashlib.sha256(owner.raw[key]).hexdigest() for key in ("e1", "c2")]
    assert owner.release("12") == expected
    with pytest.raises(ValueError, match="fresh owned source"):
        source.JointSource(directory)


def test_wrong_runtime_fails_before_any_sdk_import(tmp_path, monkeypatch):
    monkeypatch.setattr(source, "version", lambda name: "wrong")
    monkeypatch.setattr(
        source.importlib,
        "import_module",
        lambda name: pytest.fail("version mismatch cannot import SDK"),
    )
    with pytest.raises(ValueError, match="selected Bento runtime"):
        source.JointSource(tmp_path / "never-created")
    assert not (tmp_path / "never-created").exists()


def test_service_open_acquires_two_declared_stages_and_scopes_startup(tmp_path, sdk, monkeypatch):
    owner = object.__new__(source.JointSource)
    owner.native, owner.startups = sdk.native, 0
    entered, stages = [], []

    @contextmanager
    def installed():
        entered.append(owner.capture.scope)
        yield

    owner.capture = SimpleNamespace(installed=installed, scope=None)

    class Encoder:
        apply = AsyncMock(return_value=[[2.0], [4.0]])

    class Classifier:
        apply = AsyncMock(return_value=[0, 1])

    def stage(key, generation):
        stages.append((key, generation))
        return Encoder if key.startswith("e") else Classifier

    monkeypatch.setattr(owner, "stage", stage, raising=False)

    async def exercise():
        app, lifespan = await owner.open("12", 9)
        assert app.router.lifespan_context(app) is not None
        assert await sdk.instances[0]().predict([[1.0], [2.0]]) == [0, 1]
        await lifespan.__aexit__(None, None, None)

    asyncio.run(exercise())
    assert stages == [("e1", "g9-e"), ("c2", "g9-c")]
    assert entered == ["startup-9"] and owner.startups == 1
    Encoder.apply.assert_awaited_once_with([[1.0], [2.0]])
    Classifier.apply.assert_awaited_once_with([[2.0], [4.0]])


@pytest.mark.parametrize(
    "status,case", [(200, ("new_release", "22", "22", 1)), (500, ("invalid_input", "22", "22", 0))]
)
def test_request_keeps_actual_offer_error_and_authorized_release(
    tmp_path, monkeypatch, status, case
):
    calls = []
    owner = object.__new__(source.JointSource)
    owner.raw = {key: key.encode() for key in ("e1", "c1", "e2", "c2")}
    owner.loads = {}
    owner.uses = owner.reference_uses = []
    owner.http_requests = 0
    response = SimpleNamespace(status_code=status, json=lambda: [0, 1])
    health = SimpleNamespace(status_code=200)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, path, **kwargs):
            calls.append((path, kwargs))
            return response

        async def get(self, path):
            calls.append((path, {}))
            return health

    http = SimpleNamespace(
        AsyncClient=lambda **kwargs: Client(), ASGITransport=lambda **kwargs: kwargs
    )
    monkeypatch.setitem(sys.modules, "httpx", http)
    row = asyncio.run(owner.request_row(object(), 7, case))
    expected_input = [[1.0, 2.0]] if case[0] == "invalid_input" else source.PROBE
    assert calls == [("/predict", {"json": {"values": expected_input}}), ("/readyz", {})]
    assert owner.http_requests == 2 and owner.request == "request-07" and owner.attempt == case[3]
    assert row["frame"]["expected"] == [
        hashlib.sha256(key.encode()).hexdigest() for key in ("e2", "c2")
    ]
    assert row["http_status"] == status and row["health_status"] == 200
    assert row["output_values"] == ([0, 1] if status == 200 else None)
    assert row["frame"]["failed"] is (status != 200) and row["frame"]["uses"] == []
    assert row["native_request_ns"] >= 0


def test_module_identity_hashes_real_source_and_rejects_opaque_module(tmp_path, monkeypatch):
    path = tmp_path / "owned.py"
    path.write_bytes(b"# owned source\n")
    module = SimpleNamespace(__file__=str(path))
    monkeypatch.setattr(source.importlib, "import_module", lambda name: module)
    assert source.module_digest("owned") == hashlib.sha256(path.read_bytes()).hexdigest()
    module.__file__ = None
    with pytest.raises(ValueError, match="source is unavailable"):
        source.module_digest("owned")


def test_whole_pipeline_control_loads_owned_bytes_and_closes_lifespan(tmp_path, sdk, monkeypatch):
    """The SDK/HTTP wrapper is fake; Joblib and generated sklearn Pipeline are real."""
    owner = source.JointSource(tmp_path / "generated")
    lifecycle = []

    def reference(tag):
        artifact = sdk.native.models.get(tag)
        return SimpleNamespace(load_model=lambda: joblib.load(artifact.path_of("saved_model.pkl")))

    sdk.native.models.BentoModel = reference

    def service(**kwargs):
        def decorate(cls):
            app = SimpleNamespace(instance=None)

            class Context:
                async def __aenter__(self):
                    lifecycle.append("enter")
                    app.instance = cls()

                async def __aexit__(self, *args):
                    lifecycle.append("exit")

            app.router = SimpleNamespace(lifespan_context=lambda value: Context())
            cls.to_asgi = lambda: app
            return cls

        return decorate

    sdk.native.service = service

    class Client:
        def __init__(self, **kwargs):
            self.app = kwargs["transport"]["app"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, path, json):
            assert path == "/predict"
            output = await self.app.instance.predict(json["values"])
            return SimpleNamespace(status_code=200, json=lambda: output)

        async def get(self, path):
            assert path == "/readyz"
            return SimpleNamespace(status_code=200)

    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(ASGITransport=lambda **kwargs: kwargs, AsyncClient=Client),
    )
    result = asyncio.run(source.pipeline_control(owner))
    assert result["stage_count"] == 2 and result["output"] == result["independent_output"] == [
        0,
        0,
        1,
        1,
    ]
    assert lifecycle == ["enter", "exit"] and owner.startups == 1 and owner.http_requests == 2
    assert result["http_status"] == result["health_status"] == 200
    assert len(result["reference"]) == 1
    assert result["reference"][0]["raw_hex"] == result["raw_hex"]
