"""Serving transitions with owned byte fixtures, fake models and fake ASGI clients."""

from __future__ import annotations

import asyncio
import socket
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aletheia_lab.evaluation import model_load_serving_workload as study
from aletheia_lab.evaluation.model_load_serving_store import ServingStore
from aletheia_lab.project.identity import content_sha256

A, B = "a" * 64, "b" * 64


def frame(**changes: Any) -> dict[str, Any]:
    return {
        "scope": "load-0",
        "kind": "load",
        "step": 0,
        "domain": [A, B],
        "expected": A,
        "observed": [A],
        "count": 1,
        "closed": True,
        "status": 200,
        "generation": "load-0",
        **changes,
    }


@pytest.mark.parametrize(
    ("changes", "verdict", "eligibility"),
    [
        ({}, "compliant", "load"),
        ({"observed": [B]}, "violation", "load"),
        ({"expected": None}, "unknown", "load"),
        ({"observed": []}, "unknown", "load"),
        ({"count": 2}, "violation", "load"),
        ({"observed": [A, B], "count": 2}, "violation", "load"),
        ({"observed": [A], "count": 0}, "conflict", "load"),
        ({"observed": [], "count": 0, "status": 404}, None, "no_new_load"),
        ({"closed": False}, "unknown", "load"),
        ({"closed": False, "observed": [B]}, "violation", "load"),
    ],
)
def test_load_decisions_require_matching_witness_and_closure(
    changes: dict[str, Any], verdict: str | None, eligibility: str
) -> None:
    assert study.decide(frame(**changes)) == {
        "verdict": verdict,
        "eligibility": eligibility,
        "resident": None,
    }


@pytest.mark.parametrize("parent_verdict", ["compliant", "violation"])
def test_resident_inference_has_no_new_load_and_propagates_parent_binding(
    parent_verdict: str,
) -> None:
    parent = frame(observed=[A if parent_verdict == "compliant" else B])
    inference = frame(scope="infer-0", kind="infer", expected=None, observed=[], count=0)
    assert study.decide(inference, parent) == {
        "verdict": None,
        "eligibility": "no_new_load",
        "resident": parent_verdict,
    }


@pytest.mark.parametrize("parent", [None, frame(scope="other-load")])
def test_missing_or_wrong_inference_parent_fails_closed(parent: dict[str, Any] | None) -> None:
    inference = frame(scope="infer-0", kind="infer", expected=None, observed=[], count=0)
    assert study.decide(inference, parent) == {
        "verdict": "unknown",
        "eligibility": "undetermined",
        "resident": None,
    }


def test_reference_truth_and_http_success_cannot_override_durable_binding() -> None:
    value = frame(observed=[B], truth={"verdict": "compliant"}, reference_digest=A)
    assert study.decide(value)["verdict"] == "violation"
    assert study.decide(frame(observed=[], count=0, status=200))["eligibility"] == "no_new_load"


@pytest.fixture
def preparation_modules(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    np = pytest.importorskip("numpy")
    state: dict[str, Any] = {"seeds": [], "fits": [], "dumps": [], "problem": None}

    def default_rng(seed: int) -> Any:
        state["seeds"].append(seed)
        return np.random.default_rng(seed)

    class Classifier:
        def __init__(self, *, max_depth: int, random_state: int) -> None:
            self.depth, self.seed = max_depth, random_state
            self.tree_ = SimpleNamespace(node_count=2 ** (max_depth + 1) - 1)

        def fit(self, features: Any, labels: Any) -> Any:
            self.labels = labels.copy()
            state["fits"].append((self.depth, self.seed, features.shape, self.labels))
            return self

        def predict(self, probe: Any) -> Any:
            assert probe.shape == (12, 8)
            return (
                np.zeros(12, dtype=np.int64)
                if state["problem"] == "predictions"
                else self.labels[:12]
            )

    def dump(model: Any, path: Path, *, compress: int) -> list[str]:
        state["dumps"].append((path.name, compress))
        path.write_bytes(
            b"x" * 262145
            if state["problem"] == "oversize"
            else f"depth={model.depth};label={int(model.labels[0])}".encode()
        )
        return [str(path), "unexpected-sidecar"] if state["problem"] == "multifile" else [str(path)]

    modules = {
        "numpy": SimpleNamespace(
            random=SimpleNamespace(default_rng=default_rng), sin=np.sin, int64=np.int64
        ),
        "joblib": SimpleNamespace(dump=dump),
        "sklearn.tree": SimpleNamespace(DecisionTreeClassifier=Classifier),
    }

    def import_module(name: str) -> Any:
        assert name in modules, f"unexpected runtime import: {name}"
        return modules[name]

    monkeypatch.setattr(study, "importlib", SimpleNamespace(import_module=import_module))
    monkeypatch.setattr(
        study,
        "tree_fingerprint",
        lambda model: (
            "same"
            if state["problem"] == "fingerprint"
            else f"depth={model.depth};label={int(model.labels[0])}"
        ),
    )
    state["np"] = np
    return state


def test_preparation_uses_six_mirrored_fits_fixed_seed_and_owned_single_files(
    tmp_path: Path, preparation_modules: dict[str, Any]
) -> None:
    directory = tmp_path / "models"
    result = study.prepare_models(directory)
    assert result["fits"] == 6 and preparation_modules["seeds"] == [106]
    assert len(result["probe"]) == 12 and all(len(row) == 8 for row in result["probe"])
    fits = preparation_modules["fits"]
    assert [(depth, seed, shape) for depth, seed, shape, _ in fits] == [
        (depth, 106, (8192, 8)) for depth in (2, 6, 10) for _ in range(2)
    ]
    for index in (0, 2, 4):
        assert preparation_modules["np"].array_equal(fits[index + 1][3], 1 - fits[index][3])
    assert preparation_modules["dumps"] == [
        (f"{depth}-{name}.joblib", 0) for depth in (2, 6, 10) for name in ("A", "B")
    ]
    for band in result["bands"].values():
        for record in band.values():
            payload = (directory / record["path"]).read_bytes()
            assert record["bytes"] == len(payload) <= 262144
            assert record["digest"] == content_sha256(payload)
        assert band["A"]["predictions"] != band["B"]["predictions"]
        assert band["A"]["fingerprint"] != band["B"]["fingerprint"]


@pytest.mark.parametrize(
    ("problem", "message"),
    [
        ("oversize", "artifact bound"),
        ("multifile", "single-file"),
        ("predictions", "prediction-discriminating"),
        ("fingerprint", "prediction-discriminating"),
    ],
)
def test_preparation_rejects_unsupported_or_nondiscriminating_partners(
    tmp_path: Path, preparation_modules: dict[str, Any], problem: str, message: str
) -> None:
    preparation_modules["problem"] = problem
    with pytest.raises(ValueError, match=message):
        study.prepare_models(tmp_path / "models")


class FakeCapture:
    def __init__(self, directory: Path) -> None:
        self.events: list[dict[str, Any]] = []
        self.scope: str | None = None
        self.active = False
        self.installations = 0

    @contextmanager
    def installed(self) -> Any:
        self.installations += 1
        self.active = True
        try:
            yield
        finally:
            self.active = False


class FakeStore:
    def __init__(self, path: Path, arm: str, horizon: int) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.parents: list[str | None] = []
        self.retired: list[int] = []
        self.closed = False
        self.clock: Any = None

    def append(self, scope: str, step: int, value: Any, parent: str | None, detail: Any) -> None:
        assert scope not in self.rows
        assert parent is None or parent in self.rows
        self.rows[scope] = {"frame": deepcopy(value), "parent": parent}
        self.clock.value += 11

    def keep_parent(self, scope: str | None) -> None:
        self.parents.append(scope)

    def query(self, scope: str) -> dict[str, Any] | None:
        if scope not in self.rows:
            return None
        row = deepcopy(self.rows[scope])
        row["parent_frame"] = deepcopy(self.rows[row["parent"]]["frame"]) if row["parent"] else None
        return row

    def retire(self, step: int) -> None:
        self.retired.append(step)

    def stats(self) -> dict[str, Any]:
        return {"rows": len(self.rows)}

    def close(self) -> int:
        self.closed = True
        return 0


@pytest.fixture
def fake_workload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    clock = SimpleNamespace(value=0)
    state = SimpleNamespace(current=None, calls=[], instance=None, failure=None)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    band = {}
    for name, label in (("A", 0), ("B", 1)):
        payload = f"owned-{name}".encode()
        (artifacts / f"{name}.fixture").write_bytes(payload)
        band[name] = {
            "path": f"{name}.fixture",
            "digest": content_sha256(payload),
            "fingerprint": f"fp-{name}",
            "predictions": [label] * 12,
        }
    models = {"bands": {"2": band}, "probe": [[float(index)] * 8 for index in range(12)]}

    class Registry:
        async def get_model(self, name: str) -> Any:
            assert name == "probe" and state.current is not None
            clock.value += 100
            return state.current

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["base_url"] == "http://int06.invalid"
            assert kwargs["trust_env"] is False

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def post(self, route: str, *, json: Any) -> Any:
            clock.value += 7
            state.calls.append((route, deepcopy(json)))
            if state.failure is not None:
                raise state.failure
            workload = state.instance
            if route.endswith("/load"):
                if not workload.path.exists():
                    status, body = 404, {"error": "owned missing file"}
                else:
                    raw = workload.path.read_bytes()
                    name = next(
                        name for name, payload in workload.payloads.items() if payload == raw
                    )
                    state.current = SimpleNamespace(
                        _model=SimpleNamespace(fingerprint=f"fp-{name}", name=name)
                    )
                    if workload.capture.active:
                        workload.capture.events.append(
                            {
                                "scope": workload.capture.scope,
                                "digest": content_sha256(raw),
                                "bytes": len(raw),
                                "read_ns": 1,
                                "hash_ns": 1,
                                "reconstruct_ns": 1,
                                "completed": True,
                            }
                        )
                    status, body = 200, {}
            else:
                assert route == "/v2/models/probe/infer"
                assert json["inputs"][0]["shape"] == [12, 8]
                assert len(json["inputs"][0]["data"]) == 96
                assert json["outputs"] == [{"name": "predict"}]
                assert json["inputs"][0]["datatype"] == "FP64"
                status, body = (
                    200,
                    {"outputs": [{"data": band[state.current._model.name]["predictions"]}]},
                )
            return SimpleNamespace(status_code=status, json=lambda: body)

    def fingerprint(model: Any) -> str:
        clock.value += 50
        return str(model.fingerprint)

    def import_module(name: str) -> Any:
        assert name == "httpx"
        return SimpleNamespace(ASGITransport=lambda **_: object(), AsyncClient=Client)

    monkeypatch.setattr(study, "_application", lambda _: (object(), Registry()))
    monkeypatch.setattr(study, "ServingCapture", FakeCapture)
    monkeypatch.setattr(study, "ServingStore", FakeStore)
    monkeypatch.setattr(study, "tree_fingerprint", fingerprint)
    monkeypatch.setattr(study, "perf_counter_ns", lambda: clock.value)
    monkeypatch.setattr(study, "importlib", SimpleNamespace(import_module=import_module))

    def build(arm: str = "static", inferences: int = 16) -> Any:
        config = {"arm": arm, "depth": 2, "horizon": 2, "inferences": inferences}
        workload = study.Workload(
            config, tmp_path / f"worker-{arm}-{inferences}", artifacts, models
        )
        state.instance = workload
        if workload.store is not None:
            workload.store.clock = clock
        return workload

    return SimpleNamespace(
        build=build, state=state, clock=clock, models=models, artifacts=artifacts
    )


@pytest.mark.parametrize("arm", ["native", "static"])
@pytest.mark.parametrize("inferences", [0, 16])
def test_fixed_schedule_fresh_reloads_and_failed_reload_resident_dependencies(
    fake_workload: Any, arm: str, inferences: int
) -> None:
    workload = fake_workload.build(arm, inferences)
    result = asyncio.run(workload.run())
    loads = [row for row in result["rows"] if row["kind"] == "load"]
    assert len(loads) == 12 and len(result["rows"]) == 12 * (1 + inferences)
    assert [row["step"] for row in loads if row["status"] == 404] == [3, 7, 11]
    assert [row["step"] for row in loads if row["truth"]["verdict"] == "violation"] == [2, 6, 10]
    assert sum(row["truth"]["verdict"] == "compliant" for row in loads) == 6
    assert result["captured_reconstructions"] == (0 if arm == "native" else 9)
    assert workload.capture.installations == (0 if arm == "native" else 1)
    assert workload.capture.active is False
    inferences_rows = [row for row in result["rows"] if row["kind"] == "infer"]
    assert all(row["truth"]["eligibility"] == "no_new_load" for row in inferences_rows)
    assert len({row["scope"] for row in inferences_rows}) == 12 * inferences
    for row in inferences_rows:
        if row["step"] in (3, 7, 11):
            assert row["frame"]["generation"] == f"load-{row['step'] - 1}"
            assert row["truth"]["resident"] == "violation"
    if arm == "static":
        assert workload.store.closed
        assert workload.store.retired == list(range(12))
        assert workload.store.parents[-1] == "load-10"
        assert all(audit["decision"] == audit["truth"] for audit in result["audits"])
    else:
        assert all(audit["decision"]["eligibility"] == "undetermined" for audit in result["audits"])
    assert {audit["age"] for audit in result["audits"]} == {0, 2, 8}


def test_reference_validation_is_excluded_from_request_and_end_to_end_timing(
    fake_workload: Any,
) -> None:
    workload = fake_workload.build()
    study.atomic_artifact(workload.path, workload.payloads["A"])
    workload.client = SimpleNamespace(post=None)
    # Obtain the same fake client used by run without executing its full schedule.
    client_type = study.importlib.import_module("httpx").AsyncClient
    workload.client = client_type(base_url="http://int06.invalid", trust_env=False)
    with workload.capture.installed():
        asyncio.run(workload.operation("load-0", 0, "load", "A", "A"))
    row = workload.rows[0]
    assert row["rest_ns"] == 7
    assert row["write_ns"] == 11
    assert row["end_to_end_ns"] == 18
    assert workload.reference_ns == 150
    assert row["frame"]["observed"] == [workload.band["A"]["digest"]]


def test_same_slot_load_and_inference_are_durably_admitted(
    fake_workload: Any, tmp_path: Path
) -> None:
    workload = fake_workload.build()
    store = ServingStore(tmp_path / "actual.sqlite", "static", 2)
    workload.store = store
    client_type = study.importlib.import_module("httpx").AsyncClient
    workload.client = client_type(base_url="http://int06.invalid", trust_env=False)
    study.atomic_artifact(workload.path, workload.payloads["A"])
    try:
        with workload.capture.installed():
            asyncio.run(workload.operation("load-0", 0, "load", "A", "A"))
            asyncio.run(workload.operation("slot-0-infer-0", 0, "infer"))
        snapshot = store.query("slot-0-infer-0")
        assert snapshot is not None and snapshot["parent_frame"]["scope"] == "load-0"
        assert study.decide(snapshot["frame"], snapshot["parent_frame"])["resident"] == "compliant"
        assert store.stats()["append_commits"] == 2
    finally:
        store.close()


@pytest.mark.parametrize("error", [TimeoutError, OSError, ValueError])
def test_failed_reload_records_terminal_error_and_preserves_generation(
    fake_workload: Any, error: type[Exception]
) -> None:
    workload = fake_workload.build()
    client_type = study.importlib.import_module("httpx").AsyncClient
    workload.client = client_type(base_url="http://int06.invalid", trust_env=False)
    study.atomic_artifact(workload.path, workload.payloads["A"])
    with workload.capture.installed():
        asyncio.run(workload.operation("load-0", 0, "load", "A", "A"))
        previous = workload.resident_object
        fake_workload.state.failure = error("owned terminal failure")
        asyncio.run(workload.operation("load-1", 1, "load", "B", None))
    row = workload.rows[-1]
    assert row["status"] is None and row["error"] == error.__name__
    assert row["truth"]["eligibility"] == "no_new_load"
    assert row["frame"]["generation"] == "load-0"
    assert workload.resident_object is previous
    assert workload.store.query("load-1") is not None


def test_worker_blocks_all_socket_entrypoints_and_cleans_only_child_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection)
    environment = {
        "MLSERVER_OWNED_TEST": "x",
        "OTEL_OWNED_TEST": "x",
        "PROMETHEUS_OWNED_TEST": "x",
        "KEEP_OWNED_TEST": "keep",
    }
    monkeypatch.setattr(study.os, "environ", environment)
    phases: list[str] = []
    generators: list[Any] = []
    loops: list[Any] = []

    def denied() -> None:
        for entrypoint in (
            socket.socket.connect,
            socket.socket.connect_ex,
            socket.create_connection,
        ):
            with pytest.raises(RuntimeError, match="cannot use sockets"):
                entrypoint(None, ("127.0.0.1", 1))

    def tcp_socketpair(*args: Any, **kwargs: Any) -> tuple[socket.socket, socket.socket]:
        # Reproduce Windows' self-pipe connect on POSIX as well. This uses the
        # live connect method so initializing after denial cannot pass the test.
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.settimeout(2)
            client.settimeout(2)
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            client.connect(listener.getsockname())
            accepted, _ = listener.accept()
            phases.append("bootstrap")
            return accepted, client
        except BaseException:
            client.close()
            raise
        finally:
            listener.close()

    monkeypatch.setattr(socket, "socketpair", tcp_socketpair)

    class FakeWorker:
        def __init__(self, *args: Any) -> None:
            denied()
            phases.append("constructor")

        async def run(self) -> dict[str, Any]:
            denied()
            phases.append("run")
            assert environment == {"KEEP_OWNED_TEST": "keep", "OTEL_SDK_DISABLED": "true"}
            started = asyncio.Event()

            async def pending() -> None:
                try:
                    started.set()
                    await asyncio.Event().wait()
                finally:
                    denied()
                    phases.append("cancel")

            async def retained_generator() -> Any:
                try:
                    yield None
                finally:
                    denied()
                    phases.append("asyncgen")

            asyncio.create_task(pending())
            await started.wait()
            generator = retained_generator()
            await anext(generator)
            generators.append(generator)
            loop = asyncio.get_running_loop()
            loops.append(loop)
            shutdown = loop.shutdown_default_executor

            async def guarded_shutdown(*args: Any, **kwargs: Any) -> None:
                denied()
                phases.append("executor")
                await shutdown(*args, **kwargs)

            monkeypatch.setattr(loop, "shutdown_default_executor", guarded_shutdown)
            await loop.run_in_executor(None, lambda: None)
            return {"fake": True}

    monkeypatch.setattr(study, "Workload", FakeWorker)
    result = study.worker({}, tmp_path / "new-worker", tmp_path / "owned-artifacts", {})
    assert result["fake"] and result["cpu_ns"] >= 0
    assert sorted(phases) == sorted(
        ["bootstrap", "constructor", "run", "cancel", "asyncgen", "executor"]
    )
    assert len(loops) == 1 and loops[0].is_closed()
    assert original == (socket.socket.connect, socket.socket.connect_ex, socket.create_connection)


def test_worker_constructor_failure_closes_loop_and_restores_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection)
    loops: list[Any] = []

    class BrokenWorker:
        def __init__(self, *args: Any) -> None:
            loops.append(asyncio.get_event_loop())
            with pytest.raises(RuntimeError, match="cannot use sockets"):
                socket.create_connection(("127.0.0.1", 1))
            raise ValueError("owned constructor failure")

    monkeypatch.setattr(study, "Workload", BrokenWorker)
    with pytest.raises(ValueError, match="owned constructor"):
        study.worker({}, tmp_path / "new-worker", tmp_path / "owned-artifacts", {})
    assert len(loops) == 1 and loops[0].is_closed()
    assert original == (socket.socket.connect, socket.socket.connect_ex, socket.create_connection)


@pytest.mark.parametrize("phase", ["run", "shutdown"])
def test_worker_runtime_or_shutdown_failure_keeps_partial_census(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    original = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection)
    loops: list[Any] = []
    store = SimpleNamespace(close=lambda: closed.append(True))
    closed: list[bool] = []

    class BrokenWorker:
        def __init__(self, *args: Any) -> None:
            self.rows = [{"owned": True}]
            self.audits = [{"partial": True}]
            self.capture = SimpleNamespace(events=[None])
            self.store = store

        async def run(self) -> dict[str, Any]:
            loop = asyncio.get_running_loop()
            loops.append(loop)
            if phase == "run":
                raise RuntimeError("owned run failure")

            async def broken_shutdown(*args: Any, **kwargs: Any) -> None:
                with pytest.raises(RuntimeError, match="cannot use sockets"):
                    socket.create_connection(("127.0.0.1", 1))
                raise RuntimeError("owned shutdown failure")

            monkeypatch.setattr(loop, "shutdown_default_executor", broken_shutdown)
            return {"fake": True}

    monkeypatch.setattr(study, "Workload", BrokenWorker)
    result = study.worker({}, tmp_path / "new-worker", tmp_path / "owned-artifacts", {})
    assert result["status"] == "runtime_worker_failure" and result["error_type"] == "RuntimeError"
    assert result["rows"] == [{"owned": True}] and result["audits"] == [{"partial": True}]
    assert result["partial_captured_reconstructions"] == 1 and closed == [True]
    assert len(loops) == 1 and loops[0].is_closed()
    assert original == (socket.socket.connect, socket.socket.connect_ex, socket.create_connection)


def test_worker_rejects_existing_workspace_before_any_runtime_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(study, "Workload", lambda *_: pytest.fail("runtime entered"))
    with pytest.raises(ValueError, match="fresh owned directory"):
        study.worker({}, tmp_path, tmp_path / "owned-artifacts", {})
