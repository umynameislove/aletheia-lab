"""Ordinary guarded memoization for the fixed owned affine realization.

Files are immutable; no binding mutation occurs during a native call. Python
builtins and the pinned MLflow SDK dispatch are trusted. This is a bounded cache
baseline, not a closed footprint for arbitrary Python or a new audit algorithm.
"""

from __future__ import annotations

import builtins
import inspect
import math
import sys
from dataclasses import dataclass
from time import perf_counter_ns
from types import CodeType, FunctionType, MethodType, ModuleType
from typing import Any, cast

from aletheia_lab.evaluation.module_realization_source import (
    HELPERS,
    OWNED_MODULES,
    Callback,
    NativeApplication,
)

MODES = frozenset({"scan", "load_cache", "guarded_cache"})
_VALIDATION_MODULE = "mlflow.pyfunc.utils.data_validation"


def _code_shape(code: CodeType) -> tuple[Any, ...]:
    return (
        code.co_code,
        tuple(
            _code_shape(value) if isinstance(value, CodeType) else value for value in code.co_consts
        ),
        code.co_names,
        code.co_varnames,
        code.co_argcount,
        code.co_posonlyargcount,
        code.co_kwonlyargcount,
        code.co_flags,
        code.co_freevars,
        code.co_cellvars,
        code.co_stacksize,
        code.co_exceptiontable,
    )


def _compiled_function(text: str) -> CodeType:
    code = compile(text, "<owned-binding-shape>", "exec", dont_inherit=True)
    return next(value for value in code.co_consts if isinstance(value, CodeType))


def _compiled_method(name: str, text: str) -> CodeType:
    # CPython tracks imported module names when compiling method-call flags.
    parent = _compiled_function(f"import {name}\nclass AffineModel:\n" + text)
    return next(value for value in parent.co_consts if isinstance(value, CodeType))


_AFFINE_SHAPE = _code_shape(
    _compiled_function("def affine(value):\n    return COEFFICIENT * value\n")
)
_PREDICT_SHAPES = {
    name: _code_shape(
        _compiled_method(
            name,
            "    def predict(self, context, model_input, params=None):\n"
            f"        return [{name}.affine(float(value)) for value in model_input]\n",
        )
    )
    for name in HELPERS
}


@dataclass(frozen=True)
class _Guard:
    token: tuple[Any, ...]
    # IDs alone permit reuse after collection. Hold the entire selected chain.
    references: tuple[Any, ...]


def _function_refs(function: FunctionType) -> tuple[Any, ...]:
    closure = function.__closure__
    return (
        function,
        function.__code__,
        function.__globals__,
        function.__defaults__,
        function.__kwdefaults__,
        closure,
        *(closure or ()),
    )


def _leaf(method: FunctionType) -> tuple[FunctionType, list[Any]]:
    references: list[Any] = []
    seen: set[int] = set()
    while hasattr(method, "__wrapped__"):
        if id(method) in seen:
            raise ValueError("unsupported predict wrapper cycle")
        seen.add(id(method))
        module = sys.modules.get(_VALIDATION_MODULE)
        factory = getattr(module, "_wrap_predict_with_pyfunc", None)
        if not isinstance(module, ModuleType) or not isinstance(factory, FunctionType):
            raise ValueError("unsupported predict wrapper")
        pinned = [
            value
            for value in factory.__code__.co_consts
            if isinstance(value, CodeType) and value.co_freevars == ("func",)
        ]
        wrapped = method.__wrapped__
        closure = method.__closure__
        if (
            len(pinned) != 1
            or method.__code__ is not pinned[0]
            or method.__globals__ is not module.__dict__
            or method.__defaults__ is not None
            or method.__kwdefaults__ is not None
            or "__signature__" in vars(method)
            or not closure
            or len(closure) != 1
            or not isinstance(wrapped, FunctionType)
            or closure[0].cell_contents is not wrapped
        ):
            raise ValueError("unsupported predict wrapper or closure mismatch")
        references.extend(_function_refs(method))
        method = wrapped
    references.extend(_function_refs(method))
    return method, references


def _text(value: Any) -> str:
    if type(value) is not str:
        raise ValueError("unsupported binding metadata")
    return value


def _coefficient(value: Any) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise ValueError("unsupported affine coefficient")
    return value


def _guard(model: Any, sdk: Any) -> _Guard:
    # Only the pinned SDK's ordinary PythonModel route is in this footprint.
    sdk_class = getattr(sdk, "PyFuncModel", None)
    sdk_model = sys.modules.get("mlflow.pyfunc.model")
    implementation_class = getattr(sdk_model, "_PythonModelPyfuncWrapper", None)
    if (
        sdk_class is None
        or implementation_class is None
        or type(model) is not sdk_class
        or {"predict", "_predict", "unwrap_python_model"} & vars(model).keys()
    ):
        raise ValueError("unsupported SDK model or instance route override")
    native = model.unwrap_python_model()
    selected = native.predict
    if (
        cast(Any, type(native)).__getattribute__ is not object.__getattribute__
        or hasattr(type(native), "__getattr__")
        or "predict" in vars(native)
        or not isinstance(selected, MethodType)
        or selected.__self__ is not native
        or inspect.getattr_static(type(native), "predict") is not selected.__func__
        or selected.__func__ is not cast(Any, type(native)).predict
        or not isinstance(selected.__func__, FunctionType)
    ):
        raise ValueError("unsupported instance predict override")
    method, references = _leaf(selected.__func__)
    helpers = [(name, value) for name, value in method.__globals__.items() if name in HELPERS]
    if method.__globals__.get("__name__") not in OWNED_MODULES - HELPERS or len(helpers) != 1:
        raise ValueError("unsupported owned predict globals")
    helper_name, helper = helpers[0]
    if (
        method.__closure__ is not None
        or method.__defaults__ != (None,)
        or method.__kwdefaults__ is not None
        or method.__annotations__
        or "__signature__" in vars(method)
        or "float" in method.__globals__
        or cast(Any, method).__builtins__ is not vars(builtins)
        or _code_shape(method.__code__) != _PREDICT_SHAPES[helper_name]
        or type(helper) is not ModuleType
        or "__getattr__" in vars(helper)
        or not {"affine", "COEFFICIENT", "__name__", "__file__"}.issubset(vars(helper))
        or not isinstance(helper.affine, FunctionType)
    ):
        raise ValueError("unsupported owned predict footprint")
    affine = helper.affine
    if (
        affine.__globals__ is not helper.__dict__
        or affine.__closure__ is not None
        or affine.__defaults__ is not None
        or affine.__kwdefaults__ is not None
        or cast(Any, affine).__builtins__ is not vars(builtins)
        or _code_shape(affine.__code__) != _AFFINE_SHAPE
    ):
        raise ValueError("unsupported affine globals, defaults or closure")
    route = model.predict
    if (
        not isinstance(route, MethodType)
        or route.__self__ is not model
        or route.__func__ is not sdk_class.predict
    ):
        raise ValueError("unsupported SDK predict route")
    # An evicted module can still be the resident callable's actual namespace.
    # Retain that dictionary rather than requiring it to remain in sys.modules.
    references.extend((model, native, type(native), helper))
    references.extend(_function_refs(route.__func__))
    references.extend(_function_refs(affine))
    dispatch, implementation = cast(Any, model)._predict_fn, cast(Any, model)._model_impl
    if (
        type(implementation) is not implementation_class
        or {"predict", "_convert_input"} & vars(implementation).keys()
        or not isinstance(dispatch, MethodType)
        or dispatch.__self__ is not implementation
        or cast(Any, implementation).python_model is not native
        or dispatch.__func__ is not implementation_class.predict
    ):
        raise ValueError("unsupported pinned SDK dispatch")
    references.extend((dispatch, implementation, implementation_class))
    references.extend(_function_refs(dispatch.__func__))
    metadata = (
        _text(type(native).__module__),
        _coefficient(native.COEFFICIENT),
        _text(method.__module__),
        _text(method.__globals__["__file__"]),
        _text(helper.__name__),
        _text(helper.__file__),
        _coefficient(helper.COEFFICIENT),
        _text(affine.__globals__["__file__"]),
    )
    return _Guard((tuple(id(value) for value in references), metadata), tuple(references))


class BindingCacheApplication(NativeApplication):
    """Same native calls and binding schema, with three ordinary capture policies."""

    def __init__(
        self,
        artifacts: dict[str, Any],
        variant: str,
        repair: str,
        emit_callback: Callback,
        reference_callback: Callback,
        *,
        binding_mode: str,
    ) -> None:
        if binding_mode not in MODES:
            raise ValueError("binding_mode must be scan, load_cache or guarded_cache")
        super().__init__(artifacts, variant, repair, emit_callback, reference_callback)
        self.binding_mode = binding_mode
        self.full_scan_count = 0
        self.cache_hit_count = 0
        self.reference_scan_count = 0
        self.guard_ns = 0
        self.measurements.update(guard_ns=0, reference_hash_ns=0)
        self._latest_model: Any = None
        self._cache_model: Any = None
        self._cache_guard: _Guard | None = None
        self._cache_value: tuple[dict[str, Any], dict[str, Any]] | None = None

    def _scan(
        self, model: Any, *, reference: bool = False
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if reference:
            self.reference_scan_count += 1
        else:
            self.full_scan_count += 1
        before = self.measurements["hash_ns"]
        try:
            return super()._binding(model)
        finally:
            if reference:
                elapsed = self.measurements["hash_ns"] - before
                self.measurements["hash_ns"] -= elapsed
                self.measurements["reference_hash_ns"] += elapsed

    def _binding(self, model: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.binding_mode == "scan":
            # A full byte scan alone does not close dynamic attribute lookup.
            # Both safe policies serve the same explicitly qualified footprint.
            self._timed_guard(model)
            result = self._scan(model)
            self._latest_model = model
            return result
        guard = None
        if self.binding_mode == "guarded_cache":
            guard = self._timed_guard(model)
        if (
            self._cache_model is model
            and self._cache_value is not None
            and (
                guard is None
                or self._cache_guard is not None
                and guard.token == self._cache_guard.token
            )
        ):
            self.cache_hit_count += 1
            result = self._cache_value
        else:
            result = self._scan(model)
            self._cache_model, self._cache_guard = model, guard
            self._cache_value = result
        self._latest_model = model
        return dict(result[0]), dict(result[1])

    def _timed_guard(self, model: Any) -> _Guard:
        started = perf_counter_ns()
        try:
            return _guard(model, self.sdk)
        finally:
            elapsed = perf_counter_ns() - started
            self.guard_ns += elapsed
            self.measurements["guard_ns"] += elapsed

    def reference_binding(self, model: Any = None) -> tuple[dict[str, Any], dict[str, Any]]:
        """Fresh independent scan; its hashing is excluded from candidate costs."""
        with self.lock:
            target = self._latest_model if model is None else model
            if target is None:
                raise ValueError("no model available for reference binding")
            return self._scan(target, reference=True)

    def snapshot(self) -> dict[str, Any]:
        result = super().snapshot()
        return {
            **result,
            "binding_mode": self.binding_mode,
            "full_scan_count": self.full_scan_count,
            "cache_hit_count": self.cache_hit_count,
            "reference_scan_count": self.reference_scan_count,
            "guard_ns": self.guard_ns,
            "reference_hash_ns": self.measurements["reference_hash_ns"],
        }
