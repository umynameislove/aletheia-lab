"""SDK-free qualification of ordinary memoization's declared affine footprint."""

from __future__ import annotations

import gc
import sys
import weakref
from pathlib import Path
from types import MethodType, ModuleType, SimpleNamespace
from typing import Any

import pytest

from aletheia_lab.evaluation import module_realization_source as source
from aletheia_lab.evaluation.module_realization_binding_cache import (
    BindingCacheApplication,
    _guard,
)
from aletheia_lab.evaluation.module_realization_store import audit
from aletheia_lab.project.identity import content_sha256

HELPER_TEXT = "COEFFICIENT = 2.0\n\ndef affine(value):\n    return COEFFICIENT * value\n"
MODEL_TEXT = (
    "import val02_shared_helper\n\n"
    "class AffineModel:\n"
    "    def __init__(self):\n"
    "        self.COEFFICIENT = val02_shared_helper.COEFFICIENT\n"
    "    def predict(self, context, model_input, params=None):\n"
    "        return [val02_shared_helper.affine(float(value)) for value in model_input]\n"
)


class _PythonModelPyfuncWrapper:
    def __init__(self, native: Any) -> None:
        self.python_model = native

    def predict(self, values: list[float]) -> list[float]:
        return self.python_model.predict(None, values)


class PyFuncModel:
    def __init__(self, native: Any) -> None:
        self._model_impl = _PythonModelPyfuncWrapper(native)
        self._predict_fn = self._model_impl.predict

    def unwrap_python_model(self) -> Any:
        return self._model_impl.python_model

    def predict(self, values: list[float]) -> list[float]:
        return self._predict_fn(values)


@pytest.fixture
def owned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    helper = ModuleType("val02_shared_helper")
    helper.__file__ = str(tmp_path / "val02_shared_helper.py")
    Path(helper.__file__).write_bytes(HELPER_TEXT.encode("utf-8"))
    exec(compile(HELPER_TEXT, helper.__file__, "exec", dont_inherit=True), vars(helper))
    namespace = ModuleType("val02_shared_model")
    namespace.__file__ = str(tmp_path / "val02_shared_model.py")
    namespace.val02_shared_helper = helper
    monkeypatch.setitem(sys.modules, helper.__name__, helper)
    Path(namespace.__file__).write_bytes(MODEL_TEXT.encode("utf-8"))
    # Class-defined native code is intentional: its flags differ from a module
    # function, so this fixture must not replace the qualification shapes.
    exec(compile(MODEL_TEXT, namespace.__file__, "exec", dont_inherit=True), vars(namespace))
    loaded = PyFuncModel(namespace.AffineModel())

    def load(path: str, **kwargs: Any) -> PyFuncModel:
        if path == "invalid-owned":
            raise ValueError("invalid fixture")
        return loaded

    sdk = SimpleNamespace(PyFuncModel=PyFuncModel, load_model=load)
    sdk_module = ModuleType("mlflow.pyfunc.model")
    sdk_module._PythonModelPyfuncWrapper = _PythonModelPyfuncWrapper
    monkeypatch.setitem(sys.modules, sdk_module.__name__, sdk_module)
    monkeypatch.setitem(sys.modules, helper.__name__, helper)
    monkeypatch.setitem(sys.modules, namespace.__name__, namespace)
    monkeypatch.setattr(source, "_sdk", lambda: sdk)
    artifact = {
        "path": str(tmp_path),
        "coefficient": 2.0,
        "source_sha256": content_sha256(MODEL_TEXT.encode()),
        "helper_sha256": content_sha256(HELPER_TEXT.encode()),
    }
    manifest = {
        "models": {"collision": {"A": artifact, "B": artifact}},
        "invalid_path": "invalid-owned",
    }
    return SimpleNamespace(
        helper=helper, namespace=namespace, loaded=loaded, sdk=sdk, manifest=manifest
    )


def _application(owned: SimpleNamespace, mode: str) -> tuple[BindingCacheApplication, list[Any]]:
    observed: list[Any] = []
    app = BindingCacheApplication(
        owned.manifest, "collision", "none", observed.append, lambda _: None, binding_mode=mode
    )
    return app, observed


def test_legitimate_class_defined_chain_is_qualified(owned: SimpleNamespace) -> None:
    guard = _guard(owned.loaded, owned.sdk)
    assert guard.token == _guard(owned.loaded, owned.sdk).token
    assert owned.loaded.predict([3.0]) == [6.0]


def test_fixture_source_bytes_match_enrollment_hashes(owned: SimpleNamespace) -> None:
    artifact = owned.manifest["models"]["collision"]["B"]
    assert Path(owned.helper.__file__).read_bytes() == HELPER_TEXT.encode("utf-8")
    assert Path(owned.namespace.__file__).read_bytes() == MODEL_TEXT.encode("utf-8")
    assert content_sha256(Path(owned.helper.__file__).read_bytes()) == artifact["helper_sha256"]
    assert content_sha256(Path(owned.namespace.__file__).read_bytes()) == artifact["source_sha256"]


def test_scalar_change_invalidates_cached_binding_and_cannot_keep_old_verdict(
    owned: SimpleNamespace,
) -> None:
    app, observed = _application(owned, "guarded_cache")
    app.load("B")
    assert app.predict("before", 3)["body"] == {"y": 6.0}
    assert app.full_scan_count == 1 and app.cache_hit_count == 2
    owned.helper.COEFFICIENT = 3.0
    assert app.predict("after", 3)["body"] == {"y": 9.0}
    assert app.full_scan_count == 2 and app.cache_hit_count == 3
    rows = [{**row, "seq": index + 1} for index, row in enumerate(observed)]
    assert audit(rows, ["before", "after"])["verdicts"] == {
        "before": "compliant",
        "after": "conflict",
    }


def test_unguarded_load_cache_needs_a_stable_binding_for_the_whole_load(
    owned: SimpleNamespace,
) -> None:
    app, observed = _application(owned, "load_cache")
    app.load("B")
    owned.helper.COEFFICIENT = 3.0
    assert app.predict("changed", 3)["body"] == {"y": 9.0}
    prediction = next(row for row in observed if row["kind"] == "predict")
    reference, _ = app.reference_binding()
    assert prediction["binding"]["coefficient"] == 2.0
    assert reference["coefficient"] == 3.0
    assert prediction["binding"] != reference


@pytest.mark.parametrize("mode", ["scan", "guarded_cache"])
@pytest.mark.parametrize("mutation", ["affine", "float", "subclass", "getattr", "descriptor"])
def test_safe_policies_refuse_unsupported_lookup_before_prediction(
    owned: SimpleNamespace, mode: str, mutation: str
) -> None:
    app, observed = _application(owned, mode)
    app.load("B")
    original = owned.helper.affine

    def alternate(value: float) -> float:
        return 99.0 * value

    if mutation == "affine":
        owned.helper.affine = alternate
    elif mutation == "float":
        owned.namespace.float = lambda value: value + 1.0
    elif mutation == "subclass":

        class DivertModule(ModuleType):
            def __getattribute__(self, name: str) -> Any:
                if name == "affine" and sys._getframe(1).f_code.co_name in {
                    "predict",
                    "<listcomp>",
                }:
                    return alternate
                return super().__getattribute__(name)

        owned.helper.__class__ = DivertModule
    elif mutation == "getattr":

        def fallback(name: str) -> Any:
            if name == "affine":
                if sys._getframe(1).f_code.co_name in {"predict", "<listcomp>"}:
                    return alternate
                return original
            raise AttributeError(name)

        del owned.helper.affine
        owned.helper.__getattr__ = fallback
    else:
        native = owned.loaded.unwrap_python_model()
        original_predict = type(native).predict

        def alternate_predict(
            self: Any, context: Any, values: list[float], params: Any = None
        ) -> list[float]:
            return [alternate(value) for value in values]

        class DivertDescriptor:
            def __get__(self, instance: Any, owner: Any) -> Any:
                if instance is None:
                    return original_predict
                selected = (
                    original_predict
                    if sys._getframe(1).f_code.co_name == "_guard"
                    else alternate_predict
                )
                return MethodType(selected, instance)

        type(native).predict = DivertDescriptor()
    with pytest.raises(ValueError, match="unsupported"):
        app.predict("unsupported", 3)
    assert app.actual_prediction_calls == 0
    assert not any(row["kind"] == "predict" for row in observed)


def test_guard_rejects_sdk_wrapper_closure_mismatch(
    owned: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = ModuleType("mlflow.pyfunc.utils.data_validation")
    text = (
        "def _wrap_predict_with_pyfunc(func):\n"
        "    def wrapper(*args, **kwargs):\n"
        "        return func(*args, **kwargs)\n"
        "    wrapper.__wrapped__ = func\n"
        "    return wrapper\n"
    )
    exec(compile(text, "<mock-pinned-wrapper>", "exec", dont_inherit=True), vars(module))
    monkeypatch.setitem(sys.modules, module.__name__, module)
    native = owned.loaded.unwrap_python_model()
    original = type(native).predict
    wrapped = module._wrap_predict_with_pyfunc(original)
    monkeypatch.setattr(type(native), "predict", wrapped)
    assert _guard(owned.loaded, owned.sdk)
    assert wrapped.__closure__ is not None
    wrapped.__closure__[0].cell_contents = lambda *args, **kwargs: [99.0]
    with pytest.raises(ValueError, match="closure mismatch"):
        _guard(owned.loaded, owned.sdk)


def test_guard_retains_old_objects_so_identity_cannot_be_recycled(owned: SimpleNamespace) -> None:
    guard = _guard(owned.loaded, owned.sdk)
    old = weakref.ref(owned.helper.affine)
    exec(compile(HELPER_TEXT, owned.helper.__file__, "exec", dont_inherit=True), vars(owned.helper))
    gc.collect()
    assert old() is not None
    assert any(value is old() for value in guard.references)
    assert guard.token != _guard(owned.loaded, owned.sdk).token


def test_evicted_resident_uses_actual_globals_instead_of_sys_modules(
    owned: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _guard(owned.loaded, owned.sdk)
    monkeypatch.delitem(sys.modules, owned.namespace.__name__)
    replacement = ModuleType(owned.helper.__name__)
    replacement.COEFFICIENT = 99.0
    monkeypatch.setitem(sys.modules, owned.helper.__name__, replacement)
    after = _guard(owned.loaded, owned.sdk)
    assert before.token == after.token
    assert owned.loaded.predict([3.0]) == [6.0]
    assert any(value is vars(owned.namespace) for value in after.references)


@pytest.mark.parametrize("attribute", ["predict", "_predict", "unwrap_python_model"])
def test_sdk_instance_route_override_is_refused(owned: SimpleNamespace, attribute: str) -> None:
    setattr(owned.loaded, attribute, lambda *args, **kwargs: None)
    with pytest.raises(ValueError, match="instance route override"):
        _guard(owned.loaded, owned.sdk)


def test_reference_scans_are_separate_from_candidate_hash_cost(owned: SimpleNamespace) -> None:
    app, _ = _application(owned, "guarded_cache")
    first, _ = app._binding(owned.loaded)
    measured = app.measurements["hash_ns"]
    reference, _ = app.reference_binding()
    assert first == reference
    assert app.measurements["hash_ns"] == measured
    assert app.measurements["reference_hash_ns"] > 0
    assert app.reference_scan_count == 1 and app.full_scan_count == 1


@pytest.mark.parametrize("mode", ["scan", "guarded_cache"])
def test_safe_policy_guard_cost_is_measured_for_both_modes(
    owned: SimpleNamespace, mode: str
) -> None:
    app, _ = _application(owned, mode)
    app._binding(owned.loaded)
    assert app.guard_ns == app.measurements["guard_ns"] > 0
