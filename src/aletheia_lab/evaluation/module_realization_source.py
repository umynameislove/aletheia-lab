"""Owned MLflow pyfunc realization, with separate reference and observer callbacks.

This application serializes load/predict operations. Module eviction is a scoped
application repair, not an MLflow isolation guarantee. Optional SDK imports are
lazy. The caller owns loopback HTTP, persistence, process isolation and census.
"""

from __future__ import annotations

import importlib
import inspect
import json
import marshal
import math
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

OWNED_MODULES = frozenset(
    {
        "val02_shared_model",
        "val02_shared_helper",
        "val02_model_a",
        "val02_helper_a",
        "val02_model_b",
        "val02_helper_b",
    }
)
HELPERS = frozenset(name for name in OWNED_MODULES if "helper" in name)
Callback = Callable[[dict[str, Any]], None]


def _sdk() -> Any:
    if version("mlflow-skinny") != "3.9.0":
        raise ValueError("owned realization requires MLflow 3.9.0")
    return importlib.import_module("mlflow.pyfunc")


def _names(variant: str, label: str) -> tuple[str, str]:
    if variant == "collision":
        return "val02_shared_model", "val02_shared_helper"
    return f"val02_model_{label.lower()}", f"val02_helper_{label.lower()}"


def _create_one(directory: Path, variant: str, label: str, sdk: Any) -> dict[str, Any]:
    model_name, helper_name = _names(variant, label)
    coefficient = 1.0 if label == "A" else 2.0
    source = directory / f"source-{variant}-{label}"
    helper = f"COEFFICIENT = {coefficient!r}\n\ndef affine(value):\n    return COEFFICIENT * value\n".encode()
    model = (
        f"import mlflow.pyfunc\nimport {helper_name}\n\n"
        "class AffineModel(mlflow.pyfunc.PythonModel):\n"
        f"    def __init__(self):\n        self.COEFFICIENT = {helper_name}.COEFFICIENT\n\n"
        "    def predict(self, context, model_input, params=None):\n"
        f"        return [{helper_name}.affine(float(value)) for value in model_input]\n"
    ).encode()
    write_new_file(source / f"{model_name}.py", model)
    write_new_file(source / f"{helper_name}.py", helper)
    target = directory / f"artifact-{variant}-{label}"
    sys.path.insert(0, str(source))
    try:
        importlib.invalidate_caches()
        native = importlib.import_module(model_name).AffineModel()
        sdk.save_model(
            path=str(target),
            python_model=native,
            code_paths=[str(source / f"{model_name}.py"), str(source / f"{helper_name}.py")],
            pip_requirements=[],
        )
    finally:
        sys.path.remove(str(source))
        sys.modules.pop(model_name, None)
        sys.modules.pop(helper_name, None)
    return {
        "path": str(target),
        "model_module": model_name,
        "helper_module": helper_name,
        "coefficient": coefficient,
        "intercept": 0.0,
        "source_sha256": content_sha256(model),
        "helper_sha256": content_sha256(helper),
        "pickle_sha256": content_sha256((target / "python_model.pkl").read_bytes()),
    }


def create_models(directory: Path) -> dict[str, Any]:
    """Save four fresh, locally authored pyfunc artifacts with explicit requirements."""
    if directory.exists() or directory.is_symlink():
        raise ValueError("fresh owned model directory required")
    if OWNED_MODULES & sys.modules.keys():
        raise ValueError("owned module names already imported; fresh builder required")
    directory.mkdir(parents=True)
    sdk = _sdk()
    models = {
        variant: {label: _create_one(directory, variant, label, sdk) for label in ("A", "B")}
        for variant in ("collision", "unique")
    }
    invalid = directory / "artifact-invalid"
    write_new_file(invalid / "MLmodel", b"{invalid owned model metadata")
    result = {
        "schema": "owned-module-artifacts/v1",
        "runtime": {"mlflow-skinny": "3.9.0"},
        "models": models,
        "invalid_path": str(invalid),
    }
    write_new_file(directory / "manifest.json", json.dumps(result, sort_keys=True).encode())
    return result


@dataclass(frozen=True)
class Resident:
    load_id: str
    label: str
    model: Any


class NativeApplication:
    """Exactly one native call per offered operation, with no hidden retries."""

    def __init__(
        self,
        artifacts: dict[str, Any],
        variant: str,
        repair: str,
        emit_callback: Callback,
        reference_callback: Callback,
    ) -> None:
        if variant not in ("collision", "unique") or repair not in ("none", "evict"):
            raise ValueError("fixed owned realization variant/repair required")
        self.artifacts, self.variant, self.repair = artifacts, variant, repair
        self.emit_callback, self.reference_callback = emit_callback, reference_callback
        self.lock = threading.RLock()
        self.resident: Resident | None = None
        self.sequence, self.load_count, self.compute_count = 0, 0, 0
        self.actual_native_loads = 0
        self.actual_prediction_calls = 0
        self.resident_binding: dict[str, Any] | None = None
        self.measurements = {
            "hash_ns": 0,
            "capture_ns": 0,
            "native_load_ns": 0,
            "native_predict_ns": 0,
        }
        self.sdk = _sdk()

    def _record(self, kind: str, *, observer: bool = True, **facts: Any) -> dict[str, Any]:
        event = {"sequence": self.sequence, "time_ns": perf_counter_ns(), "kind": kind, **facts}
        self.sequence += 1
        # Independent raw callback is invoked even when observer delivery fails.
        self.reference_callback(json.loads(json.dumps(event)))
        if observer:
            started = perf_counter_ns()
            try:
                self.emit_callback(json.loads(json.dumps(event)))
            finally:
                self.measurements["capture_ns"] += perf_counter_ns() - started
        return event

    def _binding(self, model: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        native = model.unwrap_python_model()
        method = inspect.unwrap(type(native).predict)
        helpers = [value for name, value in method.__globals__.items() if name in HELPERS]
        if len(helpers) != 1:
            raise ValueError("actual callable lacks one owned helper binding")
        helper = helpers[0]
        started = perf_counter_ns()
        helper_digest = content_sha256(Path(helper.__file__).read_bytes())
        code_digest = content_sha256(marshal.dumps(helper.affine.__code__))
        class_digest = content_sha256(Path(method.__globals__["__file__"]).read_bytes())
        self.measurements["hash_ns"] += perf_counter_ns() - started
        binding = {
            "model_object_id": id(native),
            "class_module": type(native).__module__,
            "object_coefficient": native.COEFFICIENT,
            "helper_module": helper.__name__,
            "coefficient": helper.COEFFICIENT,
            "helper_sha256": helper_digest,
            "code_sha256": code_digest,
            "class_source_sha256": class_digest,
        }
        diagnostics = {
            "callable_module": method.__module__,
            "class_source_file": method.__globals__["__file__"],
            "helper_file": helper.__file__,
        }
        return binding, diagnostics

    def load(self, label: str) -> dict[str, Any]:
        with self.lock:
            if label not in ("A", "B", "C"):
                raise ValueError("owned labels A/B/C only")
            attempt = f"load-{self.load_count}"
            self.load_count += 1
            self._record("load_enter", observer=False, load_id=attempt, label=label)
            if self.repair == "evict":
                for name in OWNED_MODULES:
                    sys.modules.pop(name, None)
                importlib.invalidate_caches()
            path = (
                self.artifacts["invalid_path"]
                if label == "C"
                else self.artifacts["models"][self.variant][label]["path"]
            )
            started = perf_counter_ns()
            try:
                self.actual_native_loads += 1
                try:
                    candidate = self.sdk.load_model(path, suppress_warnings=True)
                finally:
                    self.measurements["native_load_ns"] += perf_counter_ns() - started
                binding, diagnostics = self._binding(candidate)
            except Exception as exc:
                self._record(
                    "load_failure",
                    load_id=attempt,
                    label=label,
                    error_type=type(exc).__name__,
                    preserved_load_id=self.resident.load_id if self.resident else None,
                )
                return {"status": 400, "body": {"error": "invalid artifact"}}
            expected = self.artifacts["models"][self.variant][label]
            self._record(
                "load",
                load_id=attempt,
                label=label,
                intended=expected["coefficient"],
                expected_helper_sha256=expected["helper_sha256"],
                fromartifact=Path(diagnostics["helper_file"]).is_relative_to(Path(path)),
                binding=binding,
                diagnostics={
                    **diagnostics,
                    "requested_source_sha256": expected["source_sha256"],
                    "artifact_path": path,
                },
            )
            self.resident = Resident(attempt, label, candidate)
            self.resident_binding = binding
            self._record("publish", observer=False, load_id=attempt, label=label)
            return {"status": 200, "body": {"loaded": label}}

    def predict(self, request_id: str, x: float) -> dict[str, Any]:
        with self.lock:
            if not request_id or type(x) not in (int, float) or not math.isfinite(x):
                raise ValueError("owned request identity and finite scalar required")
            if self.resident is None:
                return {"status": 503, "body": {"error": "no resident"}}
            resident = self.resident
            cid = f"compute-{self.compute_count}"
            self.compute_count += 1
            before, before_diagnostics = self._binding(resident.model)
            self._record(
                "request_entry",
                observer=False,
                request_id=request_id,
                load_id=resident.load_id,
                requested_label=resident.label,
                cid=cid,
                x=x,
                binding=before,
                diagnostics=before_diagnostics,
            )
            started = perf_counter_ns()
            self.actual_prediction_calls += 1
            try:
                values = resident.model.predict([float(x)])
            finally:
                self.measurements["native_predict_ns"] += perf_counter_ns() - started
            after, diagnostics = self._binding(resident.model)
            if before != after:
                raise ValueError("actual helper/object binding changed during native prediction")
            if len(values) != 1 or not math.isfinite(float(values[0])):
                raise ValueError("one finite native response required")
            y = float(values[0])
            self._record(
                "predict",
                request_id=request_id,
                load_id=resident.load_id,
                x=x,
                y=y,
                binding=after,
                diagnostics={
                    **diagnostics,
                    "cid": cid,
                    "native_inputs": [float(x)],
                    "native_outputs": [y],
                },
            )
            response = {"status": 200, "body": {"y": y}}
            self._record(
                "handler_terminal",
                observer=False,
                request_id=request_id,
                load_id=resident.load_id,
                cid=cid,
                status=200,
                raw_response=json.dumps(response["body"], sort_keys=True),
            )
            return response

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "variant": self.variant,
                "repair": self.repair,
                "resident_load_id": self.resident.load_id if self.resident else None,
                "resident_label": self.resident.label if self.resident else None,
                "loads_attempted": self.load_count,
                "predictions_attempted": self.compute_count,
                "actual_native_loads": self.actual_native_loads,
                "prediction_calls": self.actual_prediction_calls,
                "binding": dict(self.resident_binding) if self.resident_binding else None,
                "hash_ns": self.measurements["hash_ns"],
                "capture_ns": self.measurements["capture_ns"],
                "events": self.sequence,
                "measurements": dict(self.measurements),
            }
