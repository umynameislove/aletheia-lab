"""Source-pinned native-boundary reproductions, not historical deployments.

Execute the unchanged MLServer registry/settings source and selected unchanged
pool/worker method bodies. Compatibility adapts message transport in-process
and Pydantic v1 settings to installed MLServer 1.7.1. No throughput, process
isolation, natural-incident or untouched-transfer claim follows from this run.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import importlib.metadata
import importlib.util
import json
import logging
import sys
from functools import wraps
from pathlib import Path
from time import perf_counter_ns
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import patch

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.official_model_signing import (
    artifact_closure,
    associated_enrollment,
    sign_local,
    verify_local,
)
from aletheia_lab.evaluation.request_model_audit import digest
from aletheia_lab.filesystem import write_new_file

PINS = {
    "ml581-affected-mlserver-registry.py": "543dbc33c1205d35c180f99c2a34c6ced52deed740a2c743652a4a9014ac974c",
    "ml581-fixed-mlserver-registry.py": "ae5354ddadfd644f169e69c7d490d99c9b5db9a37655f14eb6c93b90ddaa9a13",
    "ml581-affected-mlserver-parallel-pool.py": "169a015f88c0c09b7c0883f6c0753ee17418a3f0ed936f774aed8458dfcd9786",
    "ml581-affected-mlserver-parallel-worker.py": "c79fd2eb8c48b294c15d6df14be6390add506e11e05227d97a68167a2406799d",
    "ml581-affected-mlserver-server.py": "5e3d172fb65a4fbb91d0594575543735a3b171c84a8bb08a6ef106e37b88d4dd",
    "ml581-fixed-mlserver-server.py": "352ebbd249cab1a8f12c8d93c38a4a542e3976500b841f400ddb1b569fd19532",
    "ml705-affected-mlserver-settings.py": "3c2293fe27c3f40e5316bfca90a526233d0afcbb8e7caf676418830324c212e5",
    "ml705-fixed-mlserver-settings.py": "4ed2579f9318403e892b5198e0c4341a289b98afc3f3c347d894cf6dcc821849",
}


def checked_sources(root: Path) -> dict[str, Path]:
    paths = {name: root / name for name in PINS}
    if root.is_symlink() or any(path.is_symlink() for path in paths.values()):
        raise ValueError("source cache must not contain symbolic links")
    if any(file_sha256(path) != PINS[name] for name, path in paths.items()):
        raise ValueError("upstream source pin mismatch; refusing source execution")
    return paths


def _source_module(path: Path, label: str, legacy: bool = False) -> ModuleType:
    _check_execution_source(path)
    name = f"mlserver._development_{label}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("source loader unavailable")
    module = importlib.util.module_from_spec(spec)
    legacy_module = importlib.import_module("pydantic.v1")
    replacement = {"pydantic": legacy_module} if legacy else {}
    with patch.dict(sys.modules, {name: module, **replacement}):
        spec.loader.exec_module(module)
    return module


def _methods(
    path: Path, class_name: str, selected: tuple[str, ...], namespace: dict[str, Any]
) -> Any:
    """Compile original AST bodies with annotations deferred, never rewritten."""
    _check_execution_source(path)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    owner = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    functions = [
        node
        for node in owner.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in selected
    ]
    if {node.name for node in functions} != set(selected):
        raise ValueError("native method census differs")
    wrapper = ast.parse("class NativeMethods:\n    pass\n").body[0]
    assert isinstance(wrapper, ast.ClassDef)
    wrapper.name = class_name
    wrapper.body = [*functions]
    fragment = ast.Module(
        body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            wrapper,
        ],
        type_ignores=[],
    )
    # Only the fixed, hash-checked upstream method census is executable here.
    exec(compile(ast.fix_missing_locations(fragment), str(path), "exec"), namespace)  # nosec B102
    return namespace[class_name]


def _check_execution_source(path: Path) -> None:
    if path.is_symlink() or path.parent.is_symlink() or path.name not in PINS:
        raise ValueError("execution requires a pinned regular source")
    if file_sha256(path) != PINS[path.name]:
        raise ValueError("source pin mismatch before execution")


def _runtime() -> tuple[Any, Any, Any]:
    if importlib.metadata.version("mlserver") != "1.7.1":
        raise ValueError("compatibility bridge requires MLServer 1.7.1")
    model = importlib.import_module("mlserver.model")
    settings = importlib.import_module("mlserver.settings")
    types = importlib.import_module("mlserver.types")
    return model, settings, types


def _transport(paths: dict[str, Path], registry: Any) -> tuple[Any, Any, list[dict[str, str]]]:
    trace: list[dict[str, str]] = []
    kinds = SimpleNamespace(Load=1, Unload=2)
    namespace = {
        "asyncio": asyncio,
        "ModelUpdateMessage": SimpleNamespace,
        "ModelUpdateType": kinds,
        "logger": logging.getLogger(__name__),
    }
    worker_type = _methods(
        paths["ml581-affected-mlserver-parallel-worker.py"],
        "Worker",
        ("_process_model_update",),
        dict(namespace),
    )
    worker = worker_type()
    worker._model_registry = registry.MultiModelRegistry()

    async def send_update(update: Any) -> None:
        trace.append(
            {
                "kind": "load" if update.update_type == 1 else "unload",
                "name": update.model_settings.name,
            }
        )
        await worker._process_model_update(update)

    worker.send_update = send_update
    pool_type = _methods(
        paths["ml581-affected-mlserver-parallel-pool.py"],
        "InferencePool",
        ("load_model", "unload_model", "_should_load_model"),
        dict(namespace),
    )
    pool = pool_type()
    pool._workers = {0: worker}
    pool._settings = SimpleNamespace(parallel_workers=1)

    def parallel(bound_method: Any) -> Any:
        original = bound_method
        while hasattr(original, "__wrapped__"):
            original = original.__wrapped__
        name = original.__self__.name

        @wraps(bound_method)
        async def predict(payload: Any) -> Any:
            actual = await worker._model_registry.get_model(name)
            return await actual.predict(payload)

        return predict

    pool.parallel = parallel
    return pool, worker, trace


async def _registry_case(paths: dict[str, Path], side: str) -> dict[str, Any]:
    native_model, native_settings, native_types = _runtime()
    registry = _source_module(paths[f"ml581-{side}-mlserver-registry.py"], f"registry_{side}")
    pool, worker, trace = _transport(paths, registry)

    async def load(self: Any) -> bool:
        self.value = int(self.settings.parameters.extra["value"])
        return True

    async def predict(self: Any, payload: Any) -> Any:
        return native_types.InferenceResponse(
            model_name=self.name,
            id=payload.id,
            outputs=[
                native_types.ResponseOutput(
                    name="y", shape=[1], datatype="INT64", data=[self.value]
                )
            ],
        )

    scalar = type("Scalar", (native_model.MLModel,), {"load": load, "predict": predict})
    with patch.object(sys.modules[__name__], "Scalar", scalar, create=True):
        return await _registry_requests(
            registry, pool, worker, trace, side, scalar, native_settings, native_types
        )


async def _registry_requests(
    registry: Any,
    pool: Any,
    worker: Any,
    trace: list[dict[str, str]],
    side: str,
    scalar: Any,
    native_settings: Any,
    native_types: Any,
) -> dict[str, Any]:
    kwargs = {"on_model_load": [pool.load_model], "on_model_unload": [pool.unload_model]}
    if side == "fixed":
        kwargs["on_model_reload"] = []  # Native server's reload hook only removes old handlers.
    main = registry.MultiModelRegistry(**kwargs)

    def setting(value: int) -> Any:
        return native_settings.ModelSettings(
            name="reload-probe",
            implementation=scalar,
            parallel_workers=1,
            parameters=native_settings.ModelParameters(extra={"value": value}),
        )

    first = await main.load(setting(1))
    request = native_types.InferenceRequest(id="first", inputs=[])
    cold = (await first.predict(request)).outputs[0].data.root
    second = await main.load(setting(10))
    try:
        after = (
            (await second.predict(native_types.InferenceRequest(id="after", inputs=[])))
            .outputs[0]
            .data.root
        )
        failure = None
    except Exception as error:
        after, failure = None, type(error).__name__
    trace_at_reload = list(trace)
    before = perf_counter_ns()
    await pool.load_model(second)  # Component repair: native worker load, no receipt rewriting.
    repair_ns = perf_counter_ns() - before
    repaired = (
        (await second.predict(native_types.InferenceRequest(id="repair", inputs=[])))
        .outputs[0]
        .data.root
    )
    worker_count = len(list(await worker._model_registry.get_models()))
    return {
        "family": "MLServer#581",
        "side": side,
        "cold": cold,
        "after_reload": after,
        "native_failure": failure,
        "expected_after_reload": [10],
        "reload_trace": trace_at_reload,
        "main_registry_present": True,
        "worker_model_count_after_repair": worker_count,
        "component_repair_output": repaired,
        "component_repair_ns": repair_ns,
        "query": "newly reloaded named model is available for the next worker request",
        "artifact_signature_can_prove_availability": False,
    }


def _model_source(value: int, helper: str | None) -> bytes:
    expression = "VALUE" if helper else str(value)
    prefix = f"from {helper} import VALUE\n" if helper else ""
    return (
        prefix
        + "from mlserver.model import MLModel\nfrom mlserver.types import InferenceResponse, ResponseOutput\n"
        "class Probe(MLModel):\n"
        f"    value = {expression}\n"
        "    async def load(self):\n        return True\n"
        "    async def predict(self, payload):\n"
        "        return InferenceResponse(model_name=self.name, id=payload.id, outputs=[ResponseOutput(name='y',shape=[1],datatype='INT64',data=[self.value])])\n"
    ).encode()


async def _parsed_prediction(parsed: Any, request_id: str) -> tuple[int, int]:
    _, settings, types = _runtime()
    # Only bridge the settings container; retain native-parsed implementation object.
    model = parsed.implementation(
        settings.ModelSettings(name=parsed.name, implementation=parsed.implementation)
    )
    await model.load()
    result = await model.predict(types.InferenceRequest(id=request_id, inputs=[]))
    return int(result.outputs[0].data.root[0]), int(parsed.implementation.value)


async def _settings_case(
    paths: dict[str, Path], root: Path, side: str, helper_case: bool
) -> dict[str, Any]:
    _runtime()
    label = f"{side}_{'helper' if helper_case else 'top'}"
    directory = root / label / "model"
    directory.mkdir(parents=True)
    name, helper = f"_serving_probe_{label}", f"_serving_helper_{label}" if helper_case else None
    module_path = directory / f"{name}.py"
    write_new_file(module_path, _model_source(1, helper))
    if helper:
        write_new_file(directory / f"{helper}.py", b"VALUE = 1\n")
    config = directory / "model-settings.json"
    write_new_file(config, json.dumps({"name": label, "implementation": f"{name}.Probe"}).encode())
    legacy = _source_module(
        paths[f"ml705-{side}-mlserver-settings.py"], f"settings_{label}", legacy=True
    )
    fixed = _source_module(
        paths["ml705-fixed-mlserver-settings.py"], f"settings_repair_{label}", legacy=True
    )
    original_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        initial, _ = await _parsed_prediction(
            legacy.ModelSettings.parse_file(str(config)), "initial"
        )
        # Authored development update changes bytes/size; no timestamp-only pyc ambiguity.
        if helper:
            (directory / f"{helper}.py").write_bytes(b"VALUE = 10\n")
        else:
            module_path.write_bytes(_model_source(10, None))
        importlib.invalidate_caches()
        bundle, key = root / label / "model.sig.json", root / label / "public.pem"
        signed = sign_local(directory, bundle, key)
        verify_local(directory, bundle, key)
        parsed = legacy.ModelSettings.parse_file(str(config))
        after, actual_class_value = await _parsed_prediction(parsed, "after-update")
        started = perf_counter_ns()
        component, _ = await _parsed_prediction(
            fixed.ModelSettings.parse_file(str(config)), "component"
        )
        component_ns = perf_counter_ns() - started
        started = perf_counter_ns()
        for module_name in (name, helper):
            if module_name:
                sys.modules.pop(module_name, None)
        full, _ = await _parsed_prediction(
            fixed.ModelSettings.parse_file(str(config)), "closure-reload"
        )
        full_ns = perf_counter_ns() - started
        verify_local(directory, bundle, key)
        return {
            "family": "MLServer#705",
            "side": side,
            "helper_dependency": helper_case,
            "initial_output": initial,
            "after_update_output": after,
            "expected_after_update": 10,
            "official_disk_signature_valid": True,
            "signed_closure": signed,
            "artifact_only_actual_use_verdict": "unknown",
            "integrated_baseline_verdict": associated_enrollment(
                "value:10", f"value:{actual_class_value}", True, True
            ),
            "candidate_same_witness_verdict": associated_enrollment(
                "value:10", f"value:{actual_class_value}", True, True
            ),
            "component_repair_output": component,
            "component_repair_ns": component_ns,
            "closure_repair_output": full,
            "closure_repair_ns": full_ns,
            "query": "runtime implementation value equals enrolled post-update value",
            "reference": "caller independently requests literal 10; full-vector numerical proof not claimed",
            "closure_complete_assumption": "runtime Probe.value witness covers this scalar predicate; disk footprint alone does not",
        }
    finally:
        sys.dont_write_bytecode = original_bytecode
        for module_name in (name, helper):
            if module_name:
                sys.modules.pop(module_name, None)


async def run_development(sources: Path, output: Path) -> dict[str, Any]:
    paths = checked_sources(sources)
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for side in ("affected", "fixed"):
        for condition in ("registry", "top", "helper"):
            try:
                result = (
                    await _registry_case(paths, side)
                    if condition == "registry"
                    else await _settings_case(paths, output, side, condition == "helper")
                )
            except Exception as error:
                result = {
                    "side": side,
                    "condition": condition,
                    "status": "technical_failure",
                    "error_type": type(error).__name__,
                }
            rows.append({**result, "side": side, "condition": condition})
    body = {
        "schema": "serving-native-development/v1",
        "status": "development_executed",
        "source_file_sha256": PINS,
        "rows": rows,
        "source_family_count": 2,
        "planned_conditions": 6,
        "completed_conditions": sum(row.get("status") != "technical_failure" for row in rows),
        "versions": {
            key: importlib.metadata.version(key)
            for key in ("mlserver", "pydantic", "model-signing")
        },
        "limits": [
            "source-pinned boundary reproduction with in-process transport, not original full deployment",
            "Pydantic v1 compatibility bridge; no socket serving or concurrency",
            "helper perturbation is authored development, not another public issue family",
            "runtime scalar witness is not a general implementation fingerprint",
            "repair serves future requests, not historical certification",
            "single repair timings are descriptive, not evidence of latency superiority",
            "two reserved final families remain unexecuted",
        ],
    }
    result = {**body, "report_sha256": digest(body)}
    write_new_file(
        output / "results.json", (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    )
    return result


def verify_development(sources: Path, output: Path) -> dict[str, Any]:
    """Read-only report/crypto checks, not independent native re-execution proof."""
    checked_sources(sources)
    report = json.loads((output / "results.json").read_text(encoding="utf-8"))
    body = {key: value for key, value in report.items() if key != "report_sha256"}
    if digest(body) != report["report_sha256"] or report["source_file_sha256"] != PINS:
        raise ValueError("report identity or source contract differs")
    if report["schema"] != "serving-native-development/v1" or report["planned_conditions"] != 6:
        raise ValueError("development contract differs")
    rows = report["rows"]
    expected_keys = {
        (side, condition)
        for side in ("affected", "fixed")
        for condition in ("registry", "top", "helper")
    }
    if len(rows) != 6 or {(row["side"], row["condition"]) for row in rows} != expected_keys:
        raise ValueError("condition census differs")
    completed = sum(row.get("status") != "technical_failure" for row in rows)
    if completed != report["completed_conditions"]:
        raise ValueError("failure census differs")
    for row in rows:
        if row.get("status") == "technical_failure":
            continue
        if row["condition"] == "registry":
            _verify_registry_row(row)
        else:
            _verify_settings_row(row, output)
    return {
        "status": "development_read_only_verification_pass"
        if completed == 6
        else "development_incomplete",
        "completed_conditions": completed,
        "planned_conditions": 6,
        "report_sha256": report["report_sha256"],
        "native_reexecuted": False,
        "historical_runtime_capture_independently_authenticated": False,
    }


def _verify_registry_row(row: dict[str, Any]) -> None:
    affected = row["side"] == "affected"
    expected_trace = ["load", "load", "unload"] if affected else ["load", "load"]
    if (
        row["family"] != "MLServer#581"
        or row["cold"] != [1]
        or row["after_reload"] != (None if affected else [10])
        or row["native_failure"] != ("ModelNotFound" if affected else None)
        or row["expected_after_reload"] != [10]
        or row["component_repair_output"] != [10]
        or [entry["kind"] for entry in row["reload_trace"]] != expected_trace
    ):
        raise ValueError("registry observation or query differs")


def _verify_settings_row(row: dict[str, Any], output: Path) -> None:
    helper = row["condition"] == "helper"
    observed = 1 if helper or row["side"] == "affected" else 10
    expected = associated_enrollment("value:10", f"value:{observed}", True, True)
    if (
        row["family"] != "MLServer#705"
        or row["helper_dependency"] != helper
        or row["initial_output"] != 1
        or row["expected_after_update"] != 10
        or row["after_update_output"] != observed
        or row["component_repair_output"] != (1 if helper else 10)
        or row["closure_repair_output"] != 10
        or row["integrated_baseline_verdict"] != expected
        or row["candidate_same_witness_verdict"] != expected
        or row["artifact_only_actual_use_verdict"] != "unknown"
        or row["official_disk_signature_valid"] is not True
    ):
        raise ValueError("settings observation, repair or query differs")
    directory = output / f"{row['side']}_{row['condition']}"
    if artifact_closure(directory / "model") != row["signed_closure"]:
        raise ValueError("retained signed closure differs")
    verify_local(directory / "model", directory / "model.sig.json", directory / "public.pem")
