"""Generated artifact and SDK-boundary regressions, not MLServer qualification."""

import asyncio
import hashlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import joblib
import pytest

from aletheia_lab.evaluation import model_load_application as source


@pytest.fixture
def sdk(monkeypatch):
    registry, repository, settings = SimpleNamespace(), object(), []

    class Settings:
        def __init__(self, **values):
            self.values = values
            settings.append(self)

    class Model:
        def __init__(self, model=None):
            self._model, self.ready = model, True

        def _get_model_outputs(self):
            return self._model.predict([[0.0], [1.0]]).tolist()

    modules = {
        "mlserver.settings": SimpleNamespace(Settings=Settings),
        "mlserver.registry": SimpleNamespace(MultiModelRegistry=lambda: registry),
        "mlserver.repository.repository": SimpleNamespace(
            SchemalessModelRepository=lambda path: repository
        ),
        "mlserver.handlers": SimpleNamespace(
            DataPlane=lambda config, models: (config, models),
            ModelRepositoryHandlers=lambda repo, models: (repo, models),
        ),
        "mlserver.rest.app": SimpleNamespace(
            create_app=lambda config, data, handlers: (config, data, handlers)
        ),
        "mlserver_sklearn": SimpleNamespace(SKLearnModel=Model),
    }
    original_import = source.importlib.import_module
    monkeypatch.setattr(
        source.importlib,
        "import_module",
        lambda name: modules[name] if name in modules else original_import(name),
    )
    return SimpleNamespace(registry=registry, repository=repository, settings=settings, Model=Model)


def test_generated_single_file_artifacts_bind_actual_bytes_and_constant_models(tmp_path):
    directory = tmp_path / "models"
    digests = source.prepare_artifacts(directory)
    assert set(digests) == {"A", "B"} and digests["A"] != digests["B"]
    for name, expected in (("A", [0, 0]), ("B", [1, 1])):
        path = directory / (name + ".joblib")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digests[name]
        assert joblib.load(path).predict([[0.0], [1.0]]).tolist() == expected
    with pytest.raises(FileExistsError):
        source.prepare_artifacts(directory)


@pytest.mark.parametrize("format_failure", ["split", "empty", "oversize", "identical"])
def test_unsupported_owned_serialization_is_rejected_before_a_worker_can_load(
    tmp_path, monkeypatch, format_failure
):
    def dump(model, path, compress):
        assert compress == 0
        raw = (
            b""
            if format_failure == "empty"
            else bytes(262145)
            if format_failure == "oversize"
            else b"owned identical serialization"
        )
        path.write_bytes(raw)
        return [str(path), "additional-fragment"] if format_failure == "split" else [str(path)]

    monkeypatch.setattr(joblib, "dump", dump)
    message = {
        "split": "single-file",
        "empty": "byte bound",
        "oversize": "byte bound",
        "identical": "must differ",
    }[format_failure]
    with pytest.raises(ValueError, match=message):
        source.prepare_artifacts(tmp_path / "unsupported")


def test_application_configuration_disables_external_runtime_surfaces(tmp_path, sdk):
    app, registry = source._application(tmp_path)
    settings, data, handlers = app
    assert registry is sdk.registry and data == (settings, sdk.registry)
    assert handlers == (sdk.repository, sdk.registry)
    assert settings.values == {
        "_env_file": None,
        "model_repository_root": str(tmp_path),
        "parallel_workers": 0,
        "cache_enabled": True,
        "metrics_endpoint": None,
        "tracing_server": None,
        "kafka_enabled": False,
    }


def test_entry_census_keeps_over_budget_flag_without_replacing_original_code(sdk):
    census = source._EntryCensus()
    loader = SimpleNamespace(f_code=joblib.load.__code__)
    for _ in range(source.MAX_NATIVE_CALLS + 1):
        census.profile(loader, "call", None)
    census.profile(loader, "return", None)
    census.profile(SimpleNamespace(f_code=sdk.Model._get_model_outputs.__code__), "call", None)
    assert census.counts == {"loader_calls": 4, "reconstructions": 0, "prediction_computations": 1}
    assert census.over_budget


@pytest.fixture
def workflow(tmp_path, sdk):
    artifacts = tmp_path / "models"
    digests = source.prepare_artifacts(artifacts)
    owner = source._Workflow("path-replaced", tmp_path / "worker", artifacts)
    assert owner.digests == digests and owner.path.read_bytes() == owner.payloads["A"]
    assert owner.settings["parameters"]["uri"] == "model.joblib"
    return owner


@pytest.mark.parametrize("status", [200, 500])
def test_request_records_scoped_counts_and_safe_failure_without_sdk(workflow, status):
    owner = workflow

    async def request(method, route, **kwargs):
        owner.census.counts["prediction_computations"] += 1
        return SimpleNamespace(status_code=status, content=b"owned", json=lambda: {"raw": "owned"})

    owner.client = SimpleNamespace(request=request)
    row = asyncio.run(owner.request("owned", "POST", "/v2/infer", {"x": 1}, intent="B"))
    assert row["counts"] == {"loader_calls": 0, "reconstructions": 0, "prediction_computations": 1}
    assert row["body"] == ({"raw": "owned"} if status == 200 else {"error": "native_http_failure"})
    assert owner.rows == [row] and owner.capture.scope == owner.scope == "owned"
    assert row["operator_pin_sha256"] == owner.digests["B"]


def test_budget_stops_dispatch_and_path_restore_is_scope_specific(workflow):
    owner = workflow
    owner.client = SimpleNamespace(request=AsyncMock())
    owner.census.counts["loader_calls"] = source.MAX_NATIVE_CALLS
    with pytest.raises(RuntimeError, match="budget exhausted"):
        asyncio.run(owner.request("blocked", "POST", "/v2/load"))
    owner.client.request.assert_not_awaited()
    source.atomic_artifact(owner.path, owner.payloads["B"])
    owner.scope = "not-initial"
    owner.restore_path(None)
    assert owner.path.read_bytes() == owner.payloads["B"]
    owner.scope = "initial-load"
    owner.restore_path(None)
    assert owner.path.read_bytes() == owner.payloads["A"]


def test_mid_request_budget_overrun_cannot_emit_success(workflow):
    owner = workflow

    async def exceeded(*args, **kwargs):
        owner.census.over_budget = True
        return SimpleNamespace(status_code=200)

    owner.client = SimpleNamespace(request=exceeded)
    with pytest.raises(RuntimeError, match="declared schedule"):
        asyncio.run(owner.request("offered", "POST", "/v2/infer"))
    assert owner.rows == []


def test_lifecycle_uses_resident_then_replacement_and_keeps_failed_reload(workflow):
    owner = workflow
    first, second = (
        SimpleNamespace(_model=object(), ready=False),
        SimpleNamespace(_model=object(), ready=True),
    )
    owner.registry = SimpleNamespace(get_model=AsyncMock(side_effect=[first, second, second]))
    owner.infer = AsyncMock()
    owner.request = AsyncMock()
    asyncio.run(owner.lifecycle(first))
    assert owner.relations == {
        "resident_model_and_object_reused": True,
        "explicit_reload_replaced_model_and_object": True,
        "reload_readiness_transition": True,
        "failed_reload_retained_previous_model": True,
    }
    assert [call.args[0] for call in owner.infer.await_args_list] == [
        "resident-after-path-update",
        "inference-after-reload",
        "inference-after-failed-reload",
    ]
    assert [call.args[0] for call in owner.request.await_args_list] == [
        "explicit-reload",
        "failed-reload",
    ]
    assert all(call.kwargs == {"intent": "B"} for call in owner.request.await_args_list)
    assert not owner.path.exists()


def test_invalid_arm_is_rejected_before_artifact_access(tmp_path):
    with pytest.raises(ValueError, match="fresh fixed-arm"):
        source._Workflow("unknown", tmp_path / "worker", tmp_path / "foreign-models")
    assert list(tmp_path.iterdir()) == []


def test_workflow_driver_keeps_cache_reload_failure_and_unload_census(workflow, sdk, monkeypatch):
    """Real owned Joblib/compute calls behind a explicitly synthetic HTTP boundary."""
    owner, selected, responses = workflow, None, {}
    owner.arm = "native"

    async def get_model(*args):
        return selected

    async def get_models():
        return [] if selected is None else [selected]

    sdk.registry.get_model, sdk.registry.get_models = get_model, get_models

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, route, json):
            nonlocal selected
            status, result = 200, None
            if route.endswith("/load"):
                try:
                    model = joblib.load(owner.path)
                except FileNotFoundError:
                    status = 500
                else:
                    if selected is not None:
                        selected.ready = False
                    selected = sdk.Model(model)
                    responses.clear()
            elif route.endswith("/unload"):
                selected = None
            elif route.endswith("/infer"):
                if selected is None:
                    status = 404
                else:
                    identifier = json["id"]
                    if identifier not in responses:
                        responses[identifier] = {"predictions": selected._get_model_outputs()}
                    result = responses[identifier]
            elif not route.endswith("/ready"):
                result = {"name": "probe", "versions": ["1"]}
            return SimpleNamespace(status_code=status, content=b"owned", json=lambda: result)

    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(
            ASGITransport=lambda **kwargs: kwargs, AsyncClient=lambda **kwargs: Client()
        ),
    )
    profile = sys.getprofile()
    result = asyncio.run(owner.run())
    assert sys.getprofile() is profile
    assert result["counts"] == {
        "loader_calls": 3,
        "reconstructions": 2,
        "prediction_computations": 4,
    }
    assert len(result["rows"]) == 12
    assert [row["status"] for row in result["rows"]].count(500) == 1
    assert result["rows"][-1]["status"] == 404
    assert all(result["relations"].values())
    assert result["receipts"] == result["reference"] == []
