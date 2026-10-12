"""Owned adapter contracts at fake SDK boundaries, not native serving outcomes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from aletheia_lab.evaluation import request_model_source as source
from aletheia_lab.project.identity import content_sha256


def serve_boundary():
    classes = []

    def deployment(**kwargs):
        assert kwargs == {"num_replicas": 1, "ray_actor_options": {"num_cpus": 0.5}}

        def decorate(cls):
            classes.append(cls)
            return SimpleNamespace(bind=lambda *args: (cls, args))

        return decorate

    def identity(**kwargs):
        return lambda method: method

    native = SimpleNamespace(
        multiplexed_model_id="generic",
        request_id="request",
        _internal_request_id="internal",
        route="/predict",
        app_name="app",
    )
    batch = [SimpleNamespace(multiplexed_model_id="aaa", request_id="batch-request")]
    serve = SimpleNamespace(
        deployment=deployment,
        multiplexed=identity,
        batch=identity,
        ingress=lambda app: lambda cls: cls,
        context=SimpleNamespace(
            _get_serve_request_context=lambda: native,
            _get_serve_batch_request_context=lambda: batch,
        ),
        get_multiplexed_model_id=lambda: "aaa",
    )
    return serve, classes


class Affine:
    coef_ = np.asarray([2.0])
    intercept_ = 1.0

    def predict(self, values):
        return np.asarray(values)[:, 0] * 2 + 1


@pytest.mark.parametrize("mode", ["original", "owned_ml"])
def test_apply_observes_native_selection_and_actual_generation(mode):
    serve, _ = serve_boundary()
    model = "model_obj_for_aaa" if mode == "original" else Affine()
    owner = SimpleNamespace(
        batches=[], generations={id(model): "generation-A"}, get_model=AsyncMock(return_value=model)
    )
    values = (
        ["3", "0"]
        if mode == "original"
        else [{"token": "first", "arg": "3"}, {"token": "second", "arg": "0"}]
    )
    result = asyncio.run(source.apply_batch(owner, serve, mode, "fixture", values, "batched"))
    assert result == (
        ["Response from model_obj_for_aaa 3", "Response from model_obj_for_aaa 0"]
        if mode == "original"
        else [7.0, 1.0]
    )
    owner.get_model.assert_awaited_once_with("aaa")
    record = owner.batches[0]
    assert record["completed"] is True and record["error"] is None
    assert record["native_context"]["model"] == "aaa"
    assert record["native_context"]["generic_model"] == "generic"
    assert record["actual_generation"] == "generation-A"
    assert [member["output"] for member in record["members"]] == result
    assert record["native_batch_contexts"] == [{"model": "aaa", "request": "batch-request"}]


@pytest.mark.parametrize("failure", ["load", "input", "output_count"])
def test_apply_failure_keeps_attempt_and_never_claims_completion(failure):
    serve, _ = serve_boundary()
    model = Affine()
    owner = SimpleNamespace(
        batches=[], generations={id(model): "generation-A"}, get_model=AsyncMock(return_value=model)
    )
    values = [{"token": "offered", "arg": "3"}]
    if failure == "load":
        owner.get_model.side_effect = OSError("synthetic load failure")
    elif failure == "input":
        values[0]["arg"] = "invalid"
    else:
        model.predict = lambda values: np.asarray([])
    with pytest.raises((OSError, ValueError)):
        asyncio.run(source.apply_batch(owner, serve, "owned_ml", "fixture", values, "batched"))
    record = owner.batches[0]
    assert record["completed"] is False and record["members"][0]["output"] is None
    assert record["members"][0]["token"] == "offered" and record["error"]
    assert (record["actual_object"] is None) is (failure == "load")


@pytest.mark.parametrize("mode", ["original", "owned_ml"])
def test_model_factory_binds_observed_load_to_actual_owned_bytes(tmp_path, monkeypatch, mode):
    serve, _ = serve_boundary()
    path = tmp_path / "owned.bin"
    path.write_bytes(b"inert owned model fixture")
    artifacts = {"aaa": {"path": str(path), "artifact_sha256": content_sha256(path.read_bytes())}}
    model = Affine()
    loader = Mock(return_value=model)
    original_import = source.importlib.import_module
    monkeypatch.setattr(
        source.importlib,
        "import_module",
        lambda name: SimpleNamespace(load=loader) if name == "joblib" else original_import(name),
    )
    cls, args = source.native_model(serve, mode, "fixture", artifacts)
    assert args == ()
    owner = cls()

    async def inspect():
        actual = await owner.get_model("aaa")
        assert owner.generations[id(actual)] == "fixture-load-0"
        record = owner.loads["fixture-load-0"]
        assert record["model"] == "aaa" and record["object_id"] == id(actual)
        assert record["raw_hex"] == (None if mode == "original" else path.read_bytes().hex())
        owner.get_model = AsyncMock(return_value=actual)
        value = "3" if mode == "original" else {"token": "single", "arg": "3"}
        assert await owner.non_batched(value) == (
            "Response from model_obj_for_aaa 3" if mode == "original" else 7.0
        )
        assert await owner.batched([value]) == [
            "Response from model_obj_for_aaa 3" if mode == "original" else 7.0
        ]
        assert (await owner.observations())["batches"] == owner.batches

    asyncio.run(inspect())
    if mode == "owned_ml":
        assert loader.call_args.args[0].getvalue() == path.read_bytes()
        path.write_bytes(b"tampered owned bytes")
        changed = cls()
        with pytest.raises(ValueError, match="bytes changed"):
            asyncio.run(changed.get_model("aaa"))
        assert changed.loads == {} and changed.generations == {}


@pytest.mark.parametrize("mode", ["original", "owned_ml"])
def test_ingress_forwards_requested_model_and_keeps_owned_token_out_of_native_selection(
    monkeypatch, mode
):
    serve, classes = serve_boundary()
    app = SimpleNamespace(post=lambda route: lambda method: method)
    original_import = source.importlib.import_module
    monkeypatch.setattr(
        source.importlib,
        "import_module",
        lambda name: (
            SimpleNamespace(FastAPI=lambda: app) if name == "fastapi" else original_import(name)
        ),
    )
    cls, args = source.native_application(serve, mode, "fixture", {})
    assert cls is classes[0] and args[0][0] is classes[1]
    handle = SimpleNamespace(
        batched=SimpleNamespace(remote=AsyncMock(return_value="batched")),
        non_batched=SimpleNamespace(remote=AsyncMock(return_value="single")),
        observations=SimpleNamespace(remote=AsyncMock(return_value={"loads": {}, "batches": []})),
    )
    handle.options = Mock(return_value=handle)
    owner = cls(handle)

    async def inspect():
        assert await owner.predict("bbb", "3", "batched", "token") == "batched"
        assert await owner.predict("aaa", "0", "non_batched", "token") == "single"
        assert await owner.observations() == {"loads": {}, "batches": []}

    asyncio.run(inspect())
    assert [call.kwargs for call in handle.options.call_args_list] == [
        {"multiplexed_model_id": "bbb"},
        {"multiplexed_model_id": "aaa"},
    ]
    handle.batched.remote.assert_awaited_once_with(
        "3" if mode == "original" else {"token": "token", "arg": "3"}
    )


@pytest.mark.parametrize("scenario", ["non_batched", "same_concurrent"])
@pytest.mark.parametrize("failure", [None, "startup", "observation", "delete"])
def test_scenario_preserves_offers_and_cleanup_failures(monkeypatch, scenario, failure):
    handle = SimpleNamespace(
        observations=SimpleNamespace(
            remote=Mock(
                return_value=SimpleNamespace(
                    result=Mock(
                        return_value={"loads": {"g0": {"model": "aaa"}}, "batches": ["observed"]}
                    )
                )
            )
        )
    )
    serve = SimpleNamespace(run=Mock(return_value=handle), delete=Mock())
    if failure == "startup":
        serve.run.side_effect = RuntimeError("synthetic startup")
    elif failure == "observation":
        handle.observations.remote.side_effect = TimeoutError("synthetic observation")
    elif failure == "delete":
        serve.delete.side_effect = OSError("synthetic cleanup")
    monkeypatch.setattr(source, "native_application", lambda *args: "fixture-app")
    monkeypatch.setattr(source, "sleep", lambda duration: None)
    response = SimpleNamespace(
        status_code=200, text="1", headers={"x-request-id": "native-id"}, json=lambda: 1
    )
    client = SimpleNamespace(post=Mock(return_value=response))
    planned = [row for row in source.planned_rows("owned_ml") if row["scenario"] == scenario]
    output = {
        "planned": planned,
        "startups": [],
        "artifacts": {},
        "rows": [],
        "loads": {},
        "batches": [],
        "failures": [],
    }
    source._scenario(serve, client, output, "owned_ml", scenario, 1234)
    source.complete_census(output)
    assert [row["token"] for row in output["rows"]] == [row["token"] for row in planned]
    assert output["startups"][0]["complete"] is (failure != "startup")
    serve.delete.assert_called_once_with(f"audit_owned_ml_{scenario}")
    assert len(client.post.call_args_list) == (0 if failure == "startup" else len(planned))
    if failure is None:
        assert output["failures"] == [] and output["loads"] == {"g0": {"model": "aaa"}}
    else:
        assert (
            output["failures"][0]["stage"]
            == {"startup": scenario, "observation": "observations", "delete": "delete_application"}[
                failure
            ]
        )
    if failure == "startup":
        assert all(row["error"] == "unexecuted_after_native_failure" for row in output["rows"])


def test_environment_identity_hashes_actual_module_bytes_and_rejects_missing_file(
    tmp_path, monkeypatch
):
    path = tmp_path / "native-source.py"
    path.write_bytes(b"# inert native source fixture\n")
    module = SimpleNamespace(__file__=str(path))
    monkeypatch.setattr(source.importlib, "import_module", lambda name: module)
    monkeypatch.setattr(source, "version", lambda name: "fixture-version")
    identity = source.environment_identity()
    assert set(identity["native_source_sha256"].values()) == {content_sha256(path.read_bytes())}
    assert identity["packages"]["ray"] == "fixture-version"
    assert identity["adapter_sha256"] == content_sha256(Path(source.__file__).read_bytes())
    module.__file__ = None
    with pytest.raises(ValueError, match="module file"):
        source.environment_identity()
