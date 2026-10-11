"""Synthetic adapter primitive contracts; no native SDK, fit, worker or run frame."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager, closing
from contextvars import ContextVar
from types import SimpleNamespace

import numpy as np
import pytest

from aletheia_lab.evaluation import cache_lifecycle_source as cache_source
from aletheia_lab.evaluation import joint_inference_source as joint_source
from aletheia_lab.evaluation import response_origin_source as origin_source
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256


class FixtureCache(dict):
    def __init__(self, maxsize):
        super().__init__()
        self.maxsize = maxsize


def cached_boundary(cache, key, lock, info):
    """Explicit memoization boundary; eviction/native races are outside these tests."""

    def decorate(compute):
        def wrapped(*args):
            identity = key(*args)
            with lock:
                if identity in cache:
                    return cache[identity]
            result = compute(*args)
            with lock:
                return cache.setdefault(identity, result)

        wrapped.cache = cache
        wrapped.cache_clear = cache.clear
        return wrapped

    return decorate


class Reply:
    def __init__(self, body, status=200):
        self.status = self.status_code = status
        self.body = encode(body).encode()
        self.content = self.body
        self.text = self.body.decode()
        self.headers = {"content-type": "application/json"}
        self.decoding_failure = False

    def json(self):
        if self.decoding_failure:
            raise ValueError("synthetic decode failure")
        return json.loads(self.body)


class Request:
    def __init__(self, token, body):
        self.headers = {"X-Owned-Token": token}
        self.body = body

    async def json(self):
        return self.body


def lifecycle(tmp_path, monkeypatch, arm="input_key", evidence="none"):
    modules = {
        "cachetools": SimpleNamespace(LRUCache=FixtureCache, cached=cached_boundary),
        "aiohttp.web": SimpleNamespace(json_response=lambda body, status=200: Reply(body, status)),
    }
    original_import = cache_source.importlib.import_module
    monkeypatch.setattr(
        cache_source.importlib,
        "import_module",
        lambda name, *args: modules[name] if name in modules else original_import(name, *args),
    )
    path = tmp_path / "owned-cache"
    path.mkdir()
    owner = cache_source.Lifecycle(
        {"arm": arm, "order": "old_first", "evidence": evidence, "steady_requests": 64}, path
    )
    return owner


@pytest.mark.parametrize(
    "arm,producer", [("input_key", "A"), ("clear", "B"), ("generation_key", "B"), ("isolated", "B")]
)
def test_cache_adapter_reload_records_selected_object_and_actual_returned_producer(
    tmp_path, monkeypatch, arm, producer
):
    owner = lifecycle(tmp_path, monkeypatch, arm)

    async def scenario():
        assert (await owner.infer(Request("warm0", {"x": 3}))).json() == {"y": 3.0}
        assert (await owner.reload(Request("reload", {"artifact": "B"}))).json() == {
            "loaded_generation": "B"
        }
        assert (await owner.infer(Request("after_reload0", {"x": 3}))).json() == {
            "y": 3.0 if producer == "A" else 6.0
        }
        actual = owner.returned["after_reload0"]
        assert actual["generation"] == producer
        assert owner.selected["after_reload0"] == "B"
        assert (await owner.reload(Request("bad", {"artifact": "C"}))).status == 400
        assert owner.resident.generation == "B"
        assert owner.token.get() == ""

    asyncio.run(scenario())
    failed = [event for event in owner.events if event["kind"] == "load_failure"]
    assert [(event["artifact"], event["preserved_generation"]) for event in failed] == [("C", "B")]
    publish = next(event for event in owner.events if event["kind"] == "publish")
    assert publish["previous_generation"] == "A" and publish["generation"] == "B"
    assert publish["clear"] is (arm == "clear")
    assert owner.persist()["records"] == []
    assert not (owner.directory / "audit.sqlite").exists()


@pytest.mark.parametrize(
    "bad_data",
    [
        b"",
        b"x" * 16385,
        b"{}",
        encode({"coefficient": 1, "intercept": 0, "padding": 1}).encode(),
        encode({"coefficient": 1, "intercept": 0, "padding": "x" * 8193}).encode(),
        encode({"coefficient": True, "intercept": 0, "padding": ""}).encode(),
    ],
)
def test_cache_owned_json_failure_never_publishes_or_invents_a_load(
    tmp_path, monkeypatch, bad_data
):
    owner = lifecycle(tmp_path, monkeypatch)
    owner.artifacts["D"] = {"raw_hex": bad_data.hex()}
    write_new_file(owner.directory / "D.json", bad_data)
    with pytest.raises(ValueError):
        owner._load("D")
    assert owner.resident.generation == "A" and "D" not in owner.loads
    assert owner.events[-1]["kind"] == "load_enter"


def test_cache_blocked_old_computation_records_entry_but_never_invents_a_producer(
    tmp_path, monkeypatch
):
    owner = lifecycle(tmp_path, monkeypatch)
    monkeypatch.setattr(owner.release, "wait", lambda timeout: False)

    async def scenario():
        owner.token.set("surrounding-context")
        with pytest.raises(TimeoutError, match="old compute release deadline"):
            await owner.infer(Request("old", {"x": 4}))
        assert owner.token.get() == "surrounding-context"

    asyncio.run(scenario())
    assert owner.entered.is_set()
    assert owner.events[-1]["kind"] == "compute_enter"
    assert "old" not in owner.returned
    assert not any(
        event["kind"] in {"compute_return", "handler_terminal"} for event in owner.events
    )


@pytest.mark.parametrize("evidence", ["none", "sufficient", "full"])
def test_cache_capture_and_actual_committed_payload_census(tmp_path, monkeypatch, evidence):
    owner = lifecycle(tmp_path, monkeypatch, evidence=evidence)
    token = owner.token.set("owned-token")
    owner.event("selected-only", x=2)
    owner.event("response", status=503)
    owner.token.reset(token)
    expected = (
        owner.events
        if evidence == "full"
        else [
            event
            for event in owner.events
            if evidence == "sufficient" and event["kind"] in cache_source.SUFFICIENT
        ]
    )
    result = owner.persist()
    assert result["records"] == expected
    assert result["record_count"] == result["commit_batches"] == len(expected)
    assert result["logical_bytes"] == sum(len(cache_source._encode(event)) for event in expected)
    assert [event["sequence"] for event in owner.events] == list(range(len(owner.events)))
    assert owner.events[-1]["token"] == "owned-token" and owner.token.get() == ""
    if evidence != "none":
        assert (
            result["physical_bytes"]
            == result["main_bytes"] + result["wal_bytes"] + result["shm_bytes"]
        )
        with closing(sqlite3.connect(owner.directory / "audit.sqlite")) as db:
            assert [
                json.loads(row[0])
                for row in db.execute("SELECT payload FROM audit ORDER BY sequence")
            ] == expected


def test_cache_persist_rejects_durable_byte_change_and_closes_connection(tmp_path, monkeypatch):
    owner = lifecycle(tmp_path, monkeypatch, evidence="full")
    owner.connection.execute("DELETE FROM audit WHERE sequence=0")
    owner.connection.commit()
    with pytest.raises(ValueError, match="persisted bytes differ"):
        owner.persist()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        owner.connection.execute("SELECT 1")


def test_cache_sign_failure_preserves_real_database_hash_and_cache_accounting(
    tmp_path, monkeypatch
):
    from aletheia_lab.evaluation import litserve_evidence_provenance as provenance

    owner = lifecycle(tmp_path, monkeypatch, evidence="full")

    def unavailable(*args):
        raise ImportError("synthetic signing boundary unavailable")

    monkeypatch.setattr(provenance, "sign_bundle", unavailable)
    owner.wrappers["alias"] = owner.wrappers["A"]
    service = cache_source._persist_and_sign(owner, owner.directory)
    assert service["signed_status"] == "failed" and service["signature_failure"] == "ImportError"
    assert service["signed_digest"] == content_sha256(
        (owner.directory / "audit.sqlite").read_bytes()
    )
    assert service["cache_state"]["cache_count"] == 2
    assert service["cache_state"]["rows"][-1]["deduplicated_owned_bytes"] == 0
    assert service["audit_query"]["final_physical_bytes"] > 0


@pytest.mark.parametrize("failure", [None, TimeoutError, ValueError])
def test_cache_http_operation_retains_offer_failure_and_capture_boundary(
    tmp_path, monkeypatch, failure
):
    owner = lifecycle(tmp_path, monkeypatch)

    class Context:
        async def __aenter__(self):
            if failure:
                raise failure("synthetic transport")
            return self

        async def __aexit__(self, *args):
            return False

        status = 200

        async def read(self):
            return b'{"y":7}'

    client = SimpleNamespace(post=lambda *args, **kwargs: Context())
    row = asyncio.run(
        owner.operation(client, "http://fixture.invalid", "offered", "warm", "/infer", {"x": 7})
    )
    assert owner.rows == [row] and row["offered_index"] == 0
    assert row["status"] == (None if failure else 200)
    assert row.get("error_type") == (failure.__name__ if failure else None)
    assert row["elapsed_ns"] >= 0 and len(owner.measurements["offered_elapsed_ns"]) == 1
    assert owner.events[-1]["kind"] == "response" and owner.events[-1]["token"] == "offered"
    service = cache_source._persist_and_sign(owner, owner.directory)
    source = cache_source._build_source(
        owner,
        {},
        "failed" if failure else "complete",
        failure.__name__ if failure else None,
        service,
    )
    assert source["rows"][0]["token"] == "offered"
    assert source["planned_inferences"] == 72 and source["planned_reloads"] == 2
    assert source["measurements"]["latencies"][0]["offered_index"] == 0


def probe(tmp_path, arm="native", workflow="version_routes"):
    owner = object.__new__(origin_source.OriginProbe)
    owner.arm, owner.workflow = arm, workflow
    owner.directory = tmp_path / "owned-origin"
    owner.directory.mkdir()
    owner.events, owner.rows, owner.loads, owner.generations = [], [], {}, {}
    owner.token = ContextVar("fixture-token", default="")
    owner.selected = ContextVar("fixture-selected", default="")
    owner.producer = ContextVar("fixture-producer", default="")
    owner.entry_epoch = ContextVar("fixture-entry", default=0)
    owner.epoch, owner.insert_origins = 0, {}
    owner.entered, owner.release = asyncio.Event(), asyncio.Event()
    owner.artifacts = {label: {"raw_hex": ("inert-" + label).encode().hex()} for label in "ABC"}
    owner.model_dirs = {}
    owner._prepare_repository()
    owner.client = owner.registry = None
    memory = {}

    async def lookup(key):
        return memory.get(key, "")

    async def insert(key, value):
        memory[key] = value
        return "inserted"

    owner.cache = SimpleNamespace(cache=memory, lookup=lookup, insert=insert)
    return owner


class Dump:
    def __init__(self, document):
        self.document = document

    def model_dump(self):
        return self.document


def runtime_boundary(owner, monkeypatch):
    class Model:
        def __init__(self, uri=None):
            self.name, self.version = "model", "1"
            self.uri = str(uri or owner.model_dirs["1"] / "model.joblib")
            self.settings = self
            self._model = SimpleNamespace(coef_=np.asarray([2.0]), intercept_=1.0)
            self.failed_load = False

        async def load(self):
            if self.failed_load:
                raise OSError("synthetic loader failure")
            return True

        async def predict(self, payload):
            values = np.asarray(payload.model_dump()["inputs"][0]["data"])
            return Dump(
                {
                    "outputs": [
                        {"data": (values * self._model.coef_[0] + self._model.intercept_).tolist()}
                    ]
                }
            )

    async def uri(settings):
        return settings.uri

    modules = {
        "mlserver_sklearn": SimpleNamespace(SKLearnModel=Model),
        "mlserver.utils": SimpleNamespace(get_model_uri=uri),
    }
    original_import = origin_source.importlib.import_module
    monkeypatch.setattr(
        origin_source.importlib,
        "import_module",
        lambda name, *args: modules[name] if name in modules else original_import(name, *args),
    )
    return Model


def test_origin_runtime_hooks_bind_owned_file_object_operands_and_result(tmp_path, monkeypatch):
    owner = probe(tmp_path, workflow="delayed_fill")
    Model = runtime_boundary(owner, monkeypatch)
    _, load, predict = owner._runtime_hooks()
    model = Model()

    async def scenario():
        assert await load(model) is True
        assert owner.generations[id(model)] == "generation-0"
        owner.token.set("old-inflight")
        task = asyncio.create_task(predict(model, Dump({"inputs": [{"data": [3.0]}]})))
        await asyncio.wait_for(owner.entered.wait(), timeout=1)
        assert [event["kind"] for event in owner.events] == [
            "load_enter",
            "load_return",
            "compute_enter",
        ]
        owner.release.set()
        result = await task
        assert result.model_dump() == {"outputs": [{"data": [7.0]}]}

    asyncio.run(scenario())
    load_record = owner.loads["generation-0"]
    assert load_record["artifact"] == content_sha256(
        (owner.model_dirs["1"] / "model.joblib").read_bytes()
    )
    assert load_record["parameters"] == {"coef": [2.0], "intercept": 1.0}
    assert load_record["runtime_id"] == id(model) and load_record["object_id"] == id(model._model)
    entry, result = owner.events[-2:]
    assert entry["identifier"] == result["identifier"] and result["token"] == "old-inflight"


@pytest.mark.parametrize("failure", ["native_load", "outside_root"])
def test_origin_load_hook_failure_is_not_admitted_as_owned_load(tmp_path, monkeypatch, failure):
    owner = probe(tmp_path)
    Model = runtime_boundary(owner, monkeypatch)
    _, load, _ = owner._runtime_hooks()
    outside = tmp_path / "outside.joblib"
    write_new_file(outside, b"outside owner root")
    model = Model(outside if failure == "outside_root" else None)
    model.failed_load = failure == "native_load"
    with pytest.raises((ValueError, OSError)):
        asyncio.run(load(model))
    assert owner.loads == {}
    assert owner.events[0]["kind"] == "load_enter"
    if failure == "native_load":
        assert (
            owner.events[-1]["kind"] == "load_failure"
            and owner.events[-1]["error_type"] == "OSError"
        )


def test_origin_cache_hit_propagates_actual_stored_producer_and_miss_is_unclaimed(tmp_path):
    owner = probe(tmp_path, arm="generation_key")
    owner.loads["g0"] = {"name": "model", "version": "1"}
    owner.selected.set("g0")
    owner._cache_hooks()

    async def scenario():
        assert await owner.cache.lookup("same-body") == ""
        assert owner.events[-1]["producer"] is None
        owner.producer.set("compute-original")
        assert await owner.cache.insert("same-body", "stored-response") == "inserted"
        owner.producer.set("")
        assert await owner.cache.lookup("same-body") == "stored-response"
        assert owner.producer.get() == "compute-original"

    asyncio.run(scenario())
    assert all(
        event["effective"] == encode([["model", "1", "g0"], "same-body"]) for event in owner.events
    )


@pytest.mark.parametrize("failure", [None, ValueError, RuntimeError, TimeoutError])
def test_origin_http_capture_preserves_offered_row_and_resets_context(tmp_path, failure):
    owner = probe(tmp_path)
    response = Reply({"outputs": [{"data": [1]}]})
    response.decoding_failure = failure is ValueError

    async def post(*args, **kwargs):
        owner.producer.set("actual-compute")
        if failure in {RuntimeError, TimeoutError}:
            raise failure("synthetic transport")
        return response

    owner.client = SimpleNamespace(post=post)

    async def scenario():
        owner.token.set("surrounding-token")
        owner.producer.set("surrounding-producer")
        if failure in {RuntimeError, TimeoutError}:
            with pytest.raises(failure):
                await owner.operation("offered", "/infer", {"x": 1})
        else:
            await owner.operation("offered", "/infer", {"x": 1})
        assert owner.token.get() == "surrounding-token"
        assert owner.producer.get() == "surrounding-producer"

    asyncio.run(scenario())
    row = owner.rows[0]
    assert row["token"] == "offered" and row["elapsed_ns"] >= 0
    assert row["event_start"] == row["event_stop"] == 0
    assert owner.token.get() == owner.producer.get() == ""
    if failure is ValueError:
        assert row["response_decode_error"] is True and row["response"] is None
        assert row["raw_response"] == response.text and row["origin"] == "actual-compute"
    elif failure:
        assert row["error_type"] == failure.__name__
    else:
        assert row["response"] == response.json() and row["origin"] == "actual-compute"


@pytest.mark.parametrize("arm", ["disabled", "fenced"])
def test_origin_application_hook_pins_actual_model_and_restores_selection_context(
    tmp_path, monkeypatch, arm
):
    owner = probe(tmp_path, arm=arm)
    Model = runtime_boundary(owner, monkeypatch)
    model = Model()
    owner.generations[id(model)] = "owned-generation"
    owner.loads["owned-generation"] = owner.object(model)
    owner.epoch = 7
    cache = owner.cache

    class Plane:
        def __init__(self, settings, registry):
            self.settings = settings

        @asynccontextmanager
        async def _infer_contextmanager(self, name, version_id=None):
            assert (name, version_id) == ("model", "1")
            yield model

        def _get_response_cache(self):
            return cache if self.settings.cache_enabled else None

    modules = {
        "mlserver.settings": SimpleNamespace(Settings=lambda **kwargs: SimpleNamespace(**kwargs)),
        "mlserver.registry": SimpleNamespace(MultiModelRegistry=lambda: SimpleNamespace()),
        "mlserver.repository.repository": SimpleNamespace(
            SchemalessModelRepository=lambda path: path
        ),
        "mlserver.handlers": SimpleNamespace(
            DataPlane=Plane, ModelRepositoryHandlers=lambda *args: args
        ),
        "mlserver.rest.app": SimpleNamespace(create_app=lambda settings, plane, handlers: plane),
    }
    previous_import = origin_source.importlib.import_module
    monkeypatch.setattr(
        origin_source.importlib,
        "import_module",
        lambda name, *args: modules[name] if name in modules else previous_import(name, *args),
    )
    app = owner._app()

    async def scenario():
        async with app._infer_contextmanager("model", "1") as selected:
            assert selected is model
            assert owner.selected.get() == "owned-generation" and owner.entry_epoch.get() == 7
        assert owner.selected.get() == "" and owner.entry_epoch.get() == 0

    asyncio.run(scenario())
    assert app.settings.parallel_workers == 0 and app.settings.metrics_endpoint is None
    assert app.settings.cache_enabled is (arm != "disabled")
    assert owner.events[-1]["actual"]["runtime_id"] == id(model)
    assert owner.events[-1]["epoch"] == 7


@pytest.mark.parametrize("workflow", origin_source.WORKFLOWS)
def test_origin_schedule_keeps_ordered_census_reload_failure_and_delayed_fill(tmp_path, workflow):
    owner = probe(tmp_path, arm="fenced", workflow=workflow)
    owner.cache.cache["obsolete"] = "old"
    owner.insert_origins["obsolete"] = "old-producer"

    async def post(route, **kwargs):
        if owner.token.get() == "old-inflight":
            owner.entered.set()
            await owner.release.wait()
        return Reply({}, 500 if owner.token.get() == "failed-reload" else 200)

    owner.client = SimpleNamespace(post=post)
    asyncio.run(owner.schedule())
    expected = (
        ["initial-load", "v1-first", "v1-repeat", "v2-same-body", "v2-zero", "v1-zero"]
        if workflow == "version_routes"
        else [
            "initial-load",
            "same-generation",
            "old-inflight" if workflow == "delayed_fill" else "before-reload",
            "successful-reload",
            "fresh-after-reload",
            "equal-output-after-reload",
            "failed-reload",
            "fresh-after-failed-reload",
        ]
    )
    assert [row["token"] for row in owner.rows] == expected
    assert owner.epoch == (1 if workflow == "version_routes" else 2)
    assert owner.cache.cache == owner.insert_origins == {}
    if workflow != "version_routes":
        assert owner.rows[-2]["status"] == 500
        assert (owner.model_dirs["1"] / "model.held-for-failure").read_bytes() == b"inert-C"
    assert all("id" not in row["body"] for row in owner.rows if row["body"] is not None)


def test_joint_stage_keeps_owned_descriptor_identity_and_real_numpy_dataflow(tmp_path, monkeypatch):
    owner = object.__new__(joint_source.JointSource)
    owner.raw = {"e1": b"inert-scaler", "c1": b"inert-classifier"}
    owner.tags = {"e1": "e1", "c1": "c1"}
    owner.capture = SimpleNamespace(receipts=[], reference=[])
    owner.loads, owner.reference_loads, owner.uses, owner.reference_uses = {}, {}, [], []
    owner.request, owner.attempt = "request-00", 1

    class Model:
        mean_, scale_ = np.asarray([1.0]), np.asarray([2.0])

        def transform(self, values):
            return (np.asarray(values) - self.mean_) / self.scale_

        def predict(self, values):
            return (np.asarray(values)[:, 0] > 0).astype(int)

    class Descriptor:
        def __init__(self, tag):
            self.tag = tag

        def load_model(self):
            raw = owner.raw[self.tag]
            owner.capture.receipts.append({"descriptor_sha256": content_sha256(raw)})
            owner.capture.reference.append({"raw_hex": raw.hex()})
            return Model()

    owner.native = SimpleNamespace(
        models=SimpleNamespace(BentoModel=Descriptor),
        api=lambda fn: fn,
        service=lambda **kwargs: lambda cls: cls,
    )
    encoder, classifier = owner.stage("e1", "encoder-0")(), owner.stage("c1", "classifier-0")()

    async def scenario():
        intermediate = await encoder.apply([[1.0], [5.0]])
        assert intermediate == [[0.0], [2.0]]
        assert await classifier.apply(intermediate) == [0, 1]

    asyncio.run(scenario())
    assert [(use["role"], use["ordinal"]) for use in owner.uses] == [
        ("encoder", 0),
        ("classifier", 1),
    ]
    assert owner.uses[0]["output"] == owner.uses[1]["input"]
    assert owner.reference_uses[0]["output_values"] == owner.reference_uses[1]["input_values"]
    frame = owner.frame(owner.release("11"), [[1.0], [5.0]], [0, 1], False)
    assert frame["expected"] == [content_sha256(owner.raw[key]) for key in ("e1", "c1")]
    assert frame["loads"]["encoder-0"]["artifact"] == frame["expected"][0]
    assert frame["attempt"] == 1 and frame["uses"] == owner.uses
