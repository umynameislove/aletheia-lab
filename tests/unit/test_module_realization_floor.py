"""SDK-free native cost worker: one call, no evidence hooks, identical routes."""

from __future__ import annotations

import io
import json
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aletheia_lab.evaluation import module_realization_floor as floor
from aletheia_lab.evaluation.module_realization_source import OWNED_MODULES


class FakeModel:
    def __init__(self, coefficient: float) -> None:
        self.coefficient = coefficient
        self.inputs: list[list[float]] = []
        self.failure = False
        self.outputs: list[float] | None = None

    def predict(self, values: list[float]) -> list[float]:
        self.inputs.append(values)
        if self.failure:
            raise RuntimeError("native prediction failed")
        return self.outputs if self.outputs is not None else [self.coefficient * x for x in values]

    def unwrap_python_model(self) -> None:
        raise AssertionError("cost floor must not inspect a binding")


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Any], list[str], list[FakeModel]]:
    manifest = {
        "models": {
            variant: {label: {"path": label} for label in ("A", "B")}
            for variant in ("collision", "unique")
        },
        "invalid_path": "C",
    }
    calls: list[str] = []
    models: list[FakeModel] = []

    def load(path: str, **kwargs: Any) -> FakeModel:
        calls.append(path)
        assert kwargs == {"suppress_warnings": True}
        if path == "C":
            raise ValueError("invalid native artifact")
        model = FakeModel(1.0 if path == "A" else 2.0)
        models.append(model)
        return model

    monkeypatch.setattr(floor, "_sdk", lambda: SimpleNamespace(load_model=load))
    return manifest, calls, models


@pytest.mark.parametrize(
    "variant,repair", [("collision", "none"), ("collision", "evict"), ("unique", "none")]
)
@pytest.mark.parametrize("order", [("A", "B", "A"), ("B", "A", "B")])
def test_same_three_loads_and_37_scalar_calls_without_observer(
    native: tuple[dict[str, Any], list[str], list[FakeModel]],
    variant: str,
    repair: str,
    order: tuple[str, ...],
) -> None:
    manifest, calls, models = native
    app = floor.NativePlain(manifest, variant, repair)
    count = 0
    for label in order:
        assert app.load(label) == {"status": 200, "body": {"loaded": label}}
        for index in range(12):
            operand = index % 3
            result = app.predict(f"r-{count:03}", operand)
            assert result["body"]["y"] == operand * (1 if label == "A" else 2)
            count += 1
    previous = app.resident
    assert app.load("C") == {"status": 400, "body": {"error": "invalid artifact"}}
    assert app.resident is previous
    app.predict(f"r-{count:03}", 7)
    assert calls == [*order, "C"]
    assert sum(len(model.inputs) for model in models) == 37
    assert all(
        len(value) == 1 and type(value[0]) is float for model in models for value in model.inputs
    )
    snapshot = app.snapshot()
    assert snapshot["native_load_count"] == snapshot["actual_native_loads"] == 4
    assert snapshot["prediction_calls"] == 37
    assert set(snapshot["measurements"]) == {"native_load_ns", "native_predict_ns"}
    assert all(value >= 0 for value in snapshot["measurements"].values())
    assert "binding" not in snapshot
    assert floor._collector_status()["offered_events"] == 0


def test_owned_eviction_is_complete_and_leaves_foreign_modules(
    native: tuple[dict[str, Any], list[str], list[FakeModel]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, _, _ = native
    for name in OWNED_MODULES:
        monkeypatch.setitem(sys.modules, name, SimpleNamespace())
    foreign: Any = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "foreign_module_cost_control", foreign)
    app = floor.NativePlain(manifest, "collision", "evict")
    app.load("A")
    assert not OWNED_MODULES & sys.modules.keys()
    assert sys.modules["foreign_module_cost_control"] is foreign


def test_invalid_input_and_native_failure_never_retry(
    native: tuple[dict[str, Any], list[str], list[FakeModel]],
) -> None:
    manifest, calls, models = native
    app = floor.NativePlain(manifest, "collision", "none")
    assert app.predict("r", 1)["status"] == 503
    with pytest.raises(ValueError, match="labels"):
        app.load("foreign")
    app.load("A")
    for operand in (float("nan"), float("inf"), True):
        with pytest.raises(ValueError, match="finite scalar"):
            app.predict("r", operand)
    models[-1].failure = True
    with pytest.raises(RuntimeError, match="native prediction"):
        app.predict("failure", 1)
    assert calls == ["A"] and models[-1].inputs == [[1.0]]
    assert app.snapshot()["prediction_calls"] == 1
    with pytest.raises(ValueError, match="variant/repair"):
        floor.NativePlain(manifest, "other", "none")


@pytest.mark.parametrize("outputs", [[], [1.0, 2.0], [float("nan")]])
def test_invalid_native_response_is_not_synthesized_or_retried(
    native: tuple[dict[str, Any], list[str], list[FakeModel]],
    outputs: list[float],
) -> None:
    manifest, _, models = native
    app = floor.NativePlain(manifest, "collision", "none")
    app.load("A")
    models[-1].outputs = outputs
    with pytest.raises(ValueError, match="one finite"):
        app.predict("r", 1)
    assert models[-1].inputs == [[1.0]]


def test_http_routes_disable_network_and_create_no_evidence_files(
    tmp_path: Path,
    native: tuple[dict[str, Any], list[str], list[FakeModel]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, calls, _ = native
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "manifest.json").write_text(json.dumps(manifest))
    sdk = floor._sdk

    def guarded_sdk() -> Any:
        assert os.environ["MLFLOW_DISABLE_TELEMETRY"] == "true"
        assert os.environ["MLFLOW_ENABLE_ASYNC_LOGGING"] == "false"
        with pytest.raises(PermissionError, match="outbound"):
            socket.create_connection(("127.0.0.1", 1))
        return sdk()

    monkeypatch.setattr(floor, "_sdk", guarded_sdk)
    results: list[dict[str, Any]] = []
    closed: list[bool] = []

    class FakeServer:
        server_port = 12345

        def __init__(self, address: tuple[str, int], handler: Any) -> None:
            assert address == ("127.0.0.1", 0)
            self.handler = handler

        def serve_forever(self, *, poll_interval: float) -> None:
            assert poll_interval == 0.02
            for route, value in [
                ("load", {"label": "A"}),
                ("predict", {"request_id": "r-000", "x": 2}),
                ("closure", {}),
                ("flush", {}),
                ("status", {}),
                ("stop", {}),
            ]:
                handler: Any = object.__new__(self.handler)
                raw = json.dumps(value).encode()
                handler.path, handler.headers = f"/{route}", {"Content-Length": str(len(raw))}
                handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
                handler.send_response = lambda code: None
                handler.send_header = lambda name, value: None
                handler.end_headers = lambda: None
                handler.do_POST()
                results.append(json.loads(handler.wfile.getvalue()))

        def shutdown(self) -> None:
            pass

        def server_close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(floor, "HTTPServer", FakeServer)
    directory = tmp_path / "worker"
    floor.serve({"variant": "collision", "repair": "none"}, directory, artifacts)
    assert calls == ["A"] and closed == [True]
    assert [path.name for path in directory.iterdir()] == ["ready.json"]
    assert results[0]["body"] == {"loaded": "A"}
    assert results[1]["body"] == {"y": 2.0}
    assert results[2]["status"] == "closed" and results[3]["status"] == "flushed"
    assert results[4]["native"]["prediction_calls"] == 1
    assert all(row["collector"] == floor._collector_status() for row in results)
