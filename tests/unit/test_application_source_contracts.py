"""Source adapter metadata and lifecycle at explicit fake SDK boundaries."""

from __future__ import annotations

import asyncio
import pickle
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from aletheia_lab.evaluation import application_audit_sources as sources
from aletheia_lab.project.identity import content_sha256


@pytest.mark.parametrize("stack", ["mlflow", "bentoml"])
def test_source_constructor_pins_and_publishes_owned_sdk_artifacts(tmp_path, monkeypatch, stack):
    models = {name: SimpleNamespace(raw=("inert-" + name).encode()) for name in ("A", "B")}
    monkeypatch.setattr(sources, "estimators", lambda: models)
    monkeypatch.setattr(sources, "tree_fingerprint", lambda model: content_sha256(model.raw))
    monkeypatch.setattr(sources, "version", lambda name: sources.PACKAGES[stack])
    calls = []
    registry = SimpleNamespace(create_registered_model=Mock(), set_registered_model_alias=Mock())

    def model_version(name, path):
        calls.append(("version", name, path))
        return SimpleNamespace(version=str(len(calls)))

    registry.create_model_version = model_version
    sdk = SimpleNamespace(set_tracking_uri=Mock(), set_registry_uri=Mock())

    def ml_save(model, path, **kwargs):
        assert kwargs == {"serialization_format": "pickle", "pip_requirements": []}
        target = Path(path)
        target.mkdir()
        (target / "model.pkl").write_bytes(model.raw)

    saved = {}

    def bento_save(tag, model):
        path = tmp_path / "bento-store" / tag.replace(":", "-")
        path.mkdir(parents=True)
        (path / "saved_model.pkl").write_bytes(model.raw)
        entry = SimpleNamespace(tag=tag, path_of=lambda name: str(path / name))
        saved[tag] = entry
        return entry

    bento = SimpleNamespace(models=SimpleNamespace(get=saved.__getitem__))
    container = SimpleNamespace(model_store=SimpleNamespace(set=Mock()))
    modules = {
        "mlflow": sdk,
        "mlflow.sklearn": SimpleNamespace(save_model=ml_save),
        "mlflow.tracking": SimpleNamespace(MlflowClient=lambda: registry),
        "bentoml": bento,
        "bentoml.sklearn": SimpleNamespace(save_model=bento_save),
        "bentoml._internal.configuration.containers": SimpleNamespace(BentoMLContainer=container),
        "bentoml._internal.models": SimpleNamespace(ModelStore=lambda path: path),
    }
    original_import = sources.importlib.import_module
    monkeypatch.setattr(
        sources.importlib,
        "import_module",
        lambda name: modules[name] if name in modules else original_import(name),
    )
    owner = sources.ApplicationSource(stack, tmp_path / "source")
    assert owner.payloads == {"A": b"inert-A", "B": b"inert-B"}
    assert owner.domain == sorted(content_sha256(raw) for raw in owner.payloads.values())
    assert owner.selected == "A" and owner.start_attempts == owner.http_requests == 0
    owner.select("B")
    assert owner.selected == "B"
    if stack == "mlflow":
        assert sdk.set_tracking_uri.call_args.args[0] == (
            "sqlite:///" + str(owner.directory / "registry.sqlite")
        )
        assert sdk.set_tracking_uri.call_args == sdk.set_registry_uri.call_args
        registry.create_registered_model.assert_called_once_with("audit")
        assert registry.set_registered_model_alias.call_args.args == (
            "audit",
            "champion",
            owner.tags["B"],
        )
    else:
        assert owner.published_b is True and owner.tags["B"] == "audit:v2"
        count = len(saved)
        owner.select("B")
        assert len(saved) == count
    owner.replace("B", "A")
    assert owner.paths["B"].read_bytes() == b"inert-A" and owner.payloads["B"] == b"inert-B"
    owner.replace("B", None)
    assert owner.paths["B"].read_bytes() == b"invalid-owned-artifact"
    with pytest.raises(ValueError, match="unknown fresh model"):
        owner.select("unknown")
    with pytest.raises(ValueError, match="supported stack"):
        sources.ApplicationSource(stack, owner.directory)


def test_pickle_capture_uses_actual_open_stream_and_restores_loader(tmp_path, monkeypatch):
    path = tmp_path / "owned.pkl"
    raw = pickle.dumps({"safe_owned_value": 3})
    path.write_bytes(raw)
    capture = sources.PickleCapture(tmp_path)
    original = pickle.load
    sdk = SimpleNamespace(load=original)
    original_import = sources.importlib.import_module
    monkeypatch.setattr(
        sources.importlib,
        "import_module",
        lambda name: sdk if name == "cloudpickle" else original_import(name),
    )
    with (
        path.open("rb") as stream,
        capture.installed(),
        pytest.raises(RuntimeError, match="unscoped"),
    ):
        pickle.load(stream)
    capture.scope = "owned-scope"
    opened = []
    with path.open("rb") as stream, capture.installed(on_open=opened.append):
        assert pickle.load(stream) == {"safe_owned_value": 3}
        assert opened == [stream]
    assert pickle.load is original
    assert sdk.load is original
    assert capture.reference == [{"scope": "owned-scope", "raw_hex": raw.hex()}]
    assert capture.receipts == [{"scope": "owned-scope", "descriptor_sha256": content_sha256(raw)}]


@pytest.mark.parametrize("bento", [False, True])
def test_native_application_transports_and_lifespan_close_without_sdk_launch(monkeypatch, bento):
    lifespan = SimpleNamespace(__aenter__=AsyncMock(), __aexit__=AsyncMock())
    app = SimpleNamespace(router=SimpleNamespace(lifespan_context=lambda value: lifespan))
    client = SimpleNamespace(
        post=AsyncMock(
            return_value=SimpleNamespace(
                status_code=200, json=lambda: [0, 1] if bento else {"predictions": [0, 1]}
            )
        ),
        get=AsyncMock(return_value=SimpleNamespace(status_code=200)),
        aclose=AsyncMock(),
    )
    original_import = sources.importlib.import_module

    def transport(*, app):
        return app

    def client_factory(**kwargs):
        assert kwargs == {"transport": app, "base_url": "http://owned.invalid"}
        return client

    module = SimpleNamespace(ASGITransport=transport, AsyncClient=client_factory)
    monkeypatch.setattr(
        sources.importlib,
        "import_module",
        lambda name: module if name == "httpx" else original_import(name),
    )
    model = SimpleNamespace(raw=b"inert model")
    monkeypatch.setattr(sources, "tree_fingerprint", lambda value: content_sha256(value.raw))
    owner = SimpleNamespace(http_requests=0)
    native = sources.NativeApplication(app, model, "owned-tag", bento=bento, source=owner)

    async def check():
        await native.open()
        result = await native.request()
        assert result == {
            "http_status": 200,
            "health_status": 200,
            "predictions": [0, 1],
            "object_fingerprint": content_sha256(b"inert model"),
            "selected_tag": "owned-tag",
        }
        await native.close()

    asyncio.run(check())
    assert owner.http_requests == 2
    assert client.post.call_args.args == ("/predict" if bento else "/invocations",)
    assert client.get.call_args.args == ("/readyz" if bento else "/ping",)
    client.aclose.assert_awaited_once()
    lifespan.__aenter__.assert_awaited_once()
    lifespan.__aexit__.assert_awaited_once_with(None, None, None)
