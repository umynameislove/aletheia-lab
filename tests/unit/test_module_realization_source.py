"""SDK-free application boundaries; native qualification is a separate opt-in run."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aletheia_lab.evaluation import module_realization_source as source


def affine(value: float) -> float:
    return 2.0 * value


class FakeNative:
    COEFFICIENT = 2.0

    def predict(self, context: Any, values: list[float]) -> list[float]:
        return [val02_shared_helper.affine(value) for value in values]


val02_shared_helper: Any = None


class FakeLoaded:
    def __init__(self) -> None:
        self.native = FakeNative()
        self.calls: list[list[float]] = []

    def unwrap_python_model(self) -> FakeNative:
        return self.native

    def predict(self, values: list[float]) -> list[float]:
        self.calls.append(values)
        return self.native.predict(None, values)


@pytest.fixture
def application(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, ...]:
    helper = tmp_path / "owned-helper.py"
    helper.write_text("COEFFICIENT = 2.0\n")
    monkeypatch.setitem(
        globals(),
        "val02_shared_helper",
        SimpleNamespace(
            __name__="val02_shared_helper",
            __file__=str(helper),
            COEFFICIENT=2.0,
            affine=affine,
        ),
    )
    loaded = FakeLoaded()
    calls: list[str] = []

    def load(path: str, **kwargs: Any) -> FakeLoaded:
        calls.append(path)
        if path == "invalid-owned":
            raise ValueError("owned invalid fixture")
        return loaded

    monkeypatch.setattr(source, "_sdk", lambda: SimpleNamespace(load_model=load))
    model = {
        "path": "owned-B",
        "coefficient": 2.0,
        "source_sha256": "a" * 64,
        "helper_sha256": "b" * 64,
    }
    manifest = {"models": {"collision": {"A": model, "B": model}}, "invalid_path": "invalid-owned"}
    observer: list[dict[str, Any]] = []
    reference: list[dict[str, Any]] = []
    app = source.NativeApplication(manifest, "collision", "none", observer.append, reference.append)
    return app, loaded, calls, observer, reference, manifest


def test_one_native_call_and_actual_binding_are_separate_from_requested_metadata(
    application: tuple[Any, ...],
) -> None:
    app, loaded, calls, observer, reference, _ = application
    assert app.load("B") == {"status": 200, "body": {"loaded": "B"}}
    assert app.predict("request-1", 2) == {"status": 200, "body": {"y": 4.0}}
    assert calls == ["owned-B"] and loaded.calls == [[2.0]]
    load = next(event for event in reference if event["kind"] == "load")
    assert load["expected_helper_sha256"] == "b" * 64
    assert load["binding"]["helper_sha256"] != "b" * 64
    assert load["binding"]["coefficient"] == 2.0
    assert len(load["binding"]["code_sha256"]) == 64
    assert [event["kind"] for event in observer] == ["load", "predict"]
    assert observer[0]["binding"] == observer[1]["binding"] == app.snapshot()["binding"]
    observer[-1]["kind"] = "changed observer copy"
    assert reference[-1]["kind"] == "handler_terminal"
    assert app.snapshot()["predictions_attempted"] == 1


def test_failed_load_preserves_actual_resident(application: tuple[Any, ...]) -> None:
    app, _, calls, _, reference, _ = application
    app.load("B")
    prior = app.snapshot()["resident_load_id"]
    assert app.load("C")["status"] == 400
    assert calls == ["owned-B", "invalid-owned"]
    assert app.snapshot()["resident_load_id"] == prior
    assert (
        next(event for event in reference if event["kind"] == "load_failure")["preserved_load_id"]
        == prior
    )
    assert app.predict("after-failed", 1)["body"] == {"y": 2.0}


def test_observer_failure_keeps_reference_and_does_not_retry(application: tuple[Any, ...]) -> None:
    app, _, calls, _, reference, _ = application

    def unavailable(event: dict[str, Any]) -> None:
        raise RuntimeError("observer transport unavailable")

    app.emit_callback = unavailable
    with pytest.raises(RuntimeError, match="observer transport"):
        app.load("B")
    assert [event["kind"] for event in reference] == ["load_enter", "load"]
    assert calls == ["owned-B"]
    assert app.snapshot()["resident_load_id"] is None


def test_repair_evicts_only_owned_module_names(
    application: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _, _, _, _, manifest = application
    monkeypatch.setitem(sys.modules, "val02_shared_model", SimpleNamespace())
    foreign = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "foreign_control_module", foreign)
    app.repair = "evict"
    app.load("B")
    assert "val02_shared_model" not in sys.modules
    assert sys.modules["foreign_control_module"] is foreign
    with pytest.raises(ValueError, match="variant/repair"):
        source.NativeApplication(manifest, "other", "none", lambda _: None, lambda _: None)


def test_missing_resident_and_invalid_operand_make_no_native_calls(
    application: tuple[Any, ...],
) -> None:
    app, loaded, _, _, _, _ = application
    assert app.predict("not-loaded", 1)["status"] == 503
    with pytest.raises(ValueError, match="finite scalar"):
        app.predict("invalid", float("nan"))
    with pytest.raises(ValueError, match="labels"):
        app.load("outside-owned")
    assert loaded.calls == []


def test_fresh_builder_manifest_and_module_namespace_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(source, "_sdk", lambda: object())
    monkeypatch.setattr(
        source,
        "_create_one",
        lambda directory, variant, label, sdk: {"coefficient": 1 if label == "A" else 2},
    )
    directory = tmp_path / "models"
    manifest = source.create_models(directory)
    assert set(manifest["models"]) == {"collision", "unique"}
    assert (directory / "manifest.json").is_file()
    assert (directory / "artifact-invalid" / "MLmodel").read_bytes().startswith(b"{invalid")
    with pytest.raises(ValueError, match="fresh owned"):
        source.create_models(directory)
    monkeypatch.setitem(sys.modules, "val02_shared_helper", SimpleNamespace())
    with pytest.raises(ValueError, match="already imported"):
        source.create_models(tmp_path / "other")


def test_native_failure_has_one_call_and_no_success_witness(
    application: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, loaded, _, observer, reference, _ = application
    app.load("B")

    def failure(values: list[float]) -> list[float]:
        raise ValueError("owned native failure")

    monkeypatch.setattr(loaded, "predict", failure)
    with pytest.raises(ValueError, match="owned native failure"):
        app.predict("failed-request", 1)
    assert app.snapshot()["prediction_calls"] == 1
    assert [event["kind"] for event in observer] == ["load"]
    assert reference[-1]["kind"] == "request_entry"


def test_object_binding_mutation_during_predict_is_rejected(
    application: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, loaded, _, observer, _, _ = application
    app.load("B")

    def mutate(values: list[float]) -> list[float]:
        loaded.native.COEFFICIENT = 9.0
        return [2.0]

    monkeypatch.setattr(loaded, "predict", mutate)
    with pytest.raises(ValueError, match="binding changed"):
        app.predict("mutated-request", 1)
    assert [event["kind"] for event in observer] == ["load"]
