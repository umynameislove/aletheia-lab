"""Exploratory bounded binding memoization: real SDK calls, unchanged old studies."""

from __future__ import annotations

import inspect
import json
import socket
import statistics
import subprocess
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from time import perf_counter_ns
from types import CodeType, FunctionType, ModuleType
from typing import Any
from unittest.mock import patch

from aletheia_lab.evaluation.cache_lifecycle_study import read_sealed, seal
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.module_realization_binding_cache import BindingCacheApplication
from aletheia_lab.evaluation.module_realization_source import HELPERS
from aletheia_lab.evaluation.module_realization_store import Collector, audit, recovered
from aletheia_lab.evaluation.module_realization_study import _environment
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

FILES = tuple(
    f"src/aletheia_lab/evaluation/module_realization_{name}.py"
    for name in ("source", "store", "binding_cache", "binding_study")
) + ("scripts/module_realization_validation.py",)
MODES = ("scan", "load_cache", "guarded_cache")
MUTATIONS = ("coefficient", "function", "float_shadow", "module_lookup")


def cells() -> list[dict[str, Any]]:
    fixed = [
        {
            "variant": variant,
            "repair": repair,
            "order": order,
            "replicate": repeat,
            "binding_mode": mode,
            "scenario": "immutable",
        }
        for repeat in range(2)
        for variant, repair in (("collision", "none"), ("collision", "evict"), ("unique", "none"))
        for order in (["A", "B", "A"], ["B", "A", "B"])
        for mode in MODES
    ]
    fixed.extend(
        {
            "variant": "unique",
            "repair": "none",
            "order": ["B"],
            "replicate": 0,
            "binding_mode": mode,
            "scenario": mutation,
        }
        for mutation in MUTATIONS
        for mode in MODES
    )
    return fixed


def _mutate(app: BindingCacheApplication, scenario: str) -> Callable[[], None]:
    if app.resident is None:
        raise ValueError("mutation requires an owned resident")
    native = app.resident.model.unwrap_python_model()
    namespace = inspect.unwrap(type(native).predict).__globals__
    helper = next(value for name, value in namespace.items() if name in HELPERS)
    if scenario == "coefficient":
        original = helper.COEFFICIENT
        helper.COEFFICIENT = 3.0
        return lambda: setattr(helper, "COEFFICIENT", original)
    if scenario == "function":
        original = helper.affine
        # Same output, different real code identity, immutable source files.
        changed = compile(
            "def affine(value):\n    return COEFFICIENT * value\n",
            "<owned-rebind>",
            "exec",
            dont_inherit=True,
        )
        function_code = next(value for value in changed.co_consts if isinstance(value, CodeType))
        helper.affine = FunctionType(function_code, helper.__dict__, "affine")
        return lambda: setattr(helper, "affine", original)
    if scenario == "float_shadow":
        namespace["float"] = lambda value: float(value) * 9
        return lambda: namespace.pop("float")
    if scenario == "module_lookup":

        class DynamicHelper(ModuleType):
            def __getattribute__(self, name: str) -> Any:
                if name == "affine":
                    frame = inspect.currentframe()
                    caller = frame.f_back if frame else None
                    if caller and caller.f_code.co_name in {"predict", "<listcomp>"}:
                        return lambda value: 99.0 * value
                return super().__getattribute__(name)

        helper.__class__ = DynamicHelper
        return lambda: setattr(helper, "__class__", ModuleType)
    raise ValueError("fixed owned mutation required")


def _offer(app: BindingCacheApplication, identity: str, x: int, phase: str) -> dict[str, Any]:
    if app.resident is None:
        raise ValueError("owned load did not qualify")
    before, _ = app.reference_binding(app.resident.model)
    native_before = app.actual_prediction_calls
    started = perf_counter_ns()
    response, error = None, None
    try:
        response = app.predict(identity, x)
    except ValueError as exc:
        error = str(exc)
    elapsed = perf_counter_ns() - started
    after, _ = app.reference_binding(app.resident.model)
    return {
        "request_id": identity,
        "x": x,
        "phase": phase,
        "response": response,
        "error": error,
        "fresh_before": before,
        "fresh_after": after,
        "native_calls": app.actual_prediction_calls - native_before,
        "candidate_ns": elapsed,
    }


def worker(config: dict[str, Any], directory: Path, artifacts: Path) -> dict[str, Any]:
    directory.mkdir()
    observer = Collector(directory, {"evidence": "compact", "durability": "event"})
    references: list[dict[str, Any]] = []
    app = BindingCacheApplication(
        json.loads((artifacts / "manifest.json").read_bytes()),
        config["variant"],
        config["repair"],
        observer.emit,
        references.append,
        binding_mode=config["binding_mode"],
    )
    offers: list[dict[str, Any]] = []
    loads = []
    try:
        for label in config["order"]:
            loads.append(app.load(label))
            if loads[-1]["status"] != 200:
                raise ValueError("owned load did not qualify")
            count = 12 if config["scenario"] == "immutable" else 3
            for index in range(count):
                offers.append(_offer(app, f"r-{len(offers):03}", index % 3, "normal"))
        if config["scenario"] == "immutable":
            loads.append(app.load("C"))
            offers.append(_offer(app, f"r-{len(offers):03}", 7, "after_failed_load"))
        else:
            restore = _mutate(app, config["scenario"])
            try:
                for index in range(3):
                    offers.append(_offer(app, f"r-{len(offers):03}", index, "changed"))
            finally:
                restore()
            for index in range(3):
                offers.append(_offer(app, f"r-{len(offers):03}", index, "restored"))
        observer.emit({"kind": "closure", "requests": [row["request_id"] for row in offers]})
        observer.flush()
        snapshot, status = app.snapshot(), observer.status()
    finally:
        observer.close()
    return {
        "config": config,
        "loads": loads,
        "offers": offers,
        "native": snapshot,
        "collector": status,
        "reference_events": references,
        "records": recovered(directory / "evidence.sqlite"),
    }


def _expect(row: dict[str, Any], config: dict[str, Any]) -> tuple[bool, float]:
    changed = row["phase"] == "changed"
    refused = (
        changed
        and config["scenario"] in {"float_shadow", "module_lookup"}
        and config["binding_mode"] != "load_cache"
    )
    if config["scenario"] == "immutable":
        index = int(row["request_id"][2:])
        label = config["order"][min(index // 12, 2)]
        actual = (
            config["order"][0]
            if config["variant"] == "collision" and config["repair"] == "none"
            else label
        )
        coefficient = 1.0 if actual == "A" else 2.0
    else:
        coefficient = (
            {"coefficient": 3.0, "float_shadow": 18.0, "module_lookup": 99.0}.get(
                config["scenario"], 2.0
            )
            if changed
            else 2.0
        )
    return refused, coefficient * row["x"]


def _offer_census(result: dict[str, Any]) -> tuple[int, list[str], dict[str, Any]]:
    config, offers, records = result["config"], result["offers"], result["records"]
    count = 37 if config["scenario"] == "immutable" else 9
    identities = [f"r-{index:03}" for index in range(count)]
    if [row["request_id"] for row in offers] != identities:
        raise ValueError("binding offers differ from complete fixed census")
    expected_inputs = [index % 3 for index in range(count)]
    expected_phases = ["normal"] * count
    if count == 37:
        expected_inputs[-1], expected_phases[-1] = 7, "after_failed_load"
    else:
        expected_phases[3:6], expected_phases[6:] = ["changed"] * 3, ["restored"] * 3
    if [row["x"] for row in offers] != expected_inputs or [
        row["phase"] for row in offers
    ] != expected_phases:
        raise ValueError("binding fixed input and phase census differs")
    statuses = [row["status"] for row in result["loads"]]
    if statuses != ([200, 200, 200, 400] if count == 37 else [200]):
        raise ValueError("binding native load census differs")
    prediction_rows = [row for row in records if row["kind"] == "predict"]
    predictions = {row["request_id"]: row for row in prediction_rows}
    accepted = [row["request_id"] for row in offers if not _expect(row, config)[0]]
    if len(predictions) != len(prediction_rows) or list(predictions) != accepted:
        raise ValueError("binding captured prediction census differs")
    return count, identities, predictions


def _acknowledged(result: dict[str, Any], count: int, rejected: int) -> None:
    native, collector = result["native"], result["collector"]
    if native["prediction_calls"] != count - rejected or native["actual_native_loads"] != len(
        result["loads"]
    ):
        raise ValueError("binding actual native calls differ")
    digests = {str(row["seq"]): content_sha256(encode(row).encode()) for row in result["records"]}
    if digests != {str(key): value for key, value in collector["durable_ack_digests"].items()}:
        raise ValueError("binding acknowledged payloads differ")


def analyze(result: dict[str, Any]) -> dict[str, Any]:
    config, offers, records = result["config"], result["offers"], result["records"]
    count, identities, predictions = _offer_census(result)
    stale, rejected, unqualified = 0, 0, 0
    latencies = []
    for row in offers:
        refused, expected_y = _expect(row, config)
        if refused:
            if row["error"] is None or row["native_calls"] or row["request_id"] in predictions:
                raise ValueError("unsupported footprint was certified")
            rejected += 1
        else:
            if (
                row["error"] is not None
                or row["response"] != {"status": 200, "body": {"y": expected_y}}
                or row["native_calls"] != 1
            ):
                raise ValueError("binding actual response prediction contradicted")
            prediction = predictions[row["request_id"]]
            generation = min(int(row["request_id"][2:]) // 12, 2) if count == 37 else 0
            if (
                prediction["x"] != row["x"]
                or prediction["y"] != expected_y
                or prediction["load_id"] != f"load-{generation}"
            ):
                raise ValueError("binding captured prediction differs from actual response/load")
            observed = prediction["binding"]
            stale += observed != row["fresh_before"] or observed != row["fresh_after"]
            unqualified += row["phase"] == "changed" and config["scenario"] in {
                "float_shadow",
                "module_lookup",
            }
            latencies.append(row["candidate_ns"])
    native, collector = result["native"], result["collector"]
    _acknowledged(result, count, rejected)
    verdicts = audit(records, identities)
    if not verdicts["closure"]:
        raise ValueError("binding closure missing")
    if config["binding_mode"] != "load_cache" and stale:
        raise ValueError("qualified safe policy retained stale witness")
    return {
        "verification": "pass",
        "offered": count,
        "native_predictions": count - rejected,
        "rejected_unsupported": rejected,
        "stale_witnesses": stale,
        "unqualified_certifications": unqualified,
        "verdicts": dict(Counter(verdicts["verdicts"].values())),
        "median_candidate_ms": statistics.median(latencies) / 1e6,
        "full_scan_count": native["full_scan_count"],
        "cache_hits": native["cache_hit_count"],
        "hash_ms": native["hash_ns"] / 1e6,
        "guard_ms": native["guard_ns"] / 1e6,
        "reference_hash_ms": native["reference_hash_ns"] / 1e6,
        "durable_write_ms": collector["write_ns"] / 1e6,
        "database_bytes": result["database_bytes"],
    }


def _summary(executions: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in executions:
        config = row["config"]
        key = "/".join(config[k] for k in ("scenario", "variant", "repair", "binding_mode"))
        groups.setdefault(key, []).append(row["finding"])
    return {
        key: {
            "cells": len(rows),
            "verified_cells": sum(row["verification"] == "pass" for row in rows),
            "offered": sum(row.get("offered", 0) for row in rows),
            "native_predictions": sum(row.get("native_predictions", 0) for row in rows),
            "stale_witnesses": sum(row.get("stale_witnesses", 0) for row in rows),
            "rejected_unsupported": sum(row.get("rejected_unsupported", 0) for row in rows),
            "unqualified_certifications": sum(
                row.get("unqualified_certifications", 0) for row in rows
            ),
            "median_cell_candidate_ms": statistics.median(
                row["median_candidate_ms"] for row in rows
            )
            if all(row["verification"] == "pass" for row in rows)
            else None,
        }
        for key, rows in groups.items()
    }


def run(
    root: Path, directory: Path, artifacts: Path, executable: Path, site: Path
) -> dict[str, Any]:
    if directory.exists() or directory.is_symlink() or directory.is_relative_to(root):
        raise ValueError("fresh private binding directory required")
    plan = seal(
        {
            "schema": "module-realization-binding-plan/v1",
            "cells": cells(),
            "bindings": {name: content_sha256((root / name).read_bytes()) for name in FILES},
            "manifest_sha256": content_sha256((artifacts / "manifest.json").read_bytes()),
            "scope": "post-result exploratory qualified affine footprint; immutable files, trusted SDK/builtins; no in-call mutation; ordinary memoization prior; component timing not HTTP or global minimum",
        }
    )
    directory.mkdir()
    write_new_file(directory / "plan.json", encode(plan).encode())
    for name in FILES:
        write_new_file(directory / "code-snapshot" / name, (root / name).read_bytes())
    executions = []
    for index, config in enumerate(plan["cells"]):
        target = directory / f"cell-{index:03}"
        path = directory / f"config-{index:03}.json"
        write_new_file(path, encode(config).encode())
        command = [
            str(executable),
            str(root / "scripts/module_realization_validation.py"),
            "binding-worker",
            "--study-dir",
            str(target),
            "--config",
            str(path),
            "--artifacts",
            str(artifacts),
        ]
        env = {
            **_environment(root, site),
            "MLFLOW_TRACKING_URI": f"sqlite:///{directory / 'owned-tracking.sqlite'}",
        }
        _check_bindings(root, plan["bindings"])
        try:
            completed = subprocess.run(command, env=env, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired as exc:
            completed = subprocess.CompletedProcess(
                command, -1, stdout=exc.stdout or b"", stderr=exc.stderr or b""
            )
        write_new_file(directory / f"stdout-{index:03}.log", completed.stdout)
        write_new_file(directory / f"stderr-{index:03}.log", completed.stderr)
        finding: dict[str, Any] = {"verification": "fail", "error_type": "worker_failure"}
        if completed.returncode == 0:
            try:
                result = read_sealed(target / "results.json")
                if result["config"] != config:
                    raise ValueError("binding worker configuration differs")
                finding = analyze(result)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                finding = {"verification": "fail", "error_type": type(exc).__name__}
        executions.append(
            {"config": config, "returncode": completed.returncode, "finding": finding}
        )
        print(
            json.dumps(
                {
                    "status": "binding_memoization_progress",
                    "completed": index + 1,
                    "maximum": len(plan["cells"]),
                }
            ),
            flush=True,
        )
    _check_bindings(root, plan["bindings"])
    report = seal(
        {
            "schema": "module-realization-binding-results/v1",
            "plan_sha256": plan["sha256"],
            "executions": executions,
            "analysis": _summary(executions),
        }
    )
    write_new_file(directory / "results.json", encode(report).encode())
    return {
        "verification": "pass"
        if all(row["finding"]["verification"] == "pass" for row in executions)
        else "fail",
        "analysis": report["analysis"],
    }


def native_worker(config: dict[str, Any], directory: Path, artifacts: Path) -> dict[str, Any]:
    def deny(*args: Any, **kwargs: Any) -> None:
        raise PermissionError("owned binding worker cannot use network")

    with (
        patch.object(socket.socket, "connect", deny),
        patch.object(socket.socket, "connect_ex", deny),
        patch.object(socket, "create_connection", deny),
    ):
        result = worker(config, directory, artifacts)
    result["database_bytes"] = (directory / "evidence.sqlite").stat().st_size
    # JSON object keys must have their serialized type before canonical hashing.
    result = seal(json.loads(encode(result)))
    write_new_file(directory / "results.json", encode(result).encode())
    return {"status": "binding_worker_complete"}


def _check_bindings(root: Path, bindings: dict[str, str]) -> None:
    if any(
        content_sha256((root / name).read_bytes()) != digest for name, digest in bindings.items()
    ):
        raise ValueError("binding implementation changed during execution")


def verify(directory: Path) -> dict[str, Any]:
    plan, report = read_sealed(directory / "plan.json"), read_sealed(directory / "results.json")
    if (
        plan["cells"] != cells()
        or [row["config"] for row in report["executions"]] != plan["cells"]
        or report["plan_sha256"] != plan["sha256"]
    ):
        raise ValueError("binding fixed census differs")
    for name, digest in plan["bindings"].items():
        if content_sha256((directory / "code-snapshot" / name).read_bytes()) != digest:
            raise ValueError("binding executed code differs")
    for index, row in enumerate(report["executions"]):
        if row["returncode"] != 0:
            raise ValueError("binding failed worker remains in census")
        path = directory / f"cell-{index:03}"
        result = read_sealed(path / "results.json")
        if (
            result["config"] != row["config"]
            or recovered(path / "evidence.sqlite") != result["records"]
            or (path / "evidence.sqlite").stat().st_size != result["database_bytes"]
            or analyze(result) != row["finding"]
        ):
            raise ValueError("binding raw replay differs")
    if _summary(report["executions"]) != report["analysis"]:
        raise ValueError("binding aggregate differs")
    return {"verification": "pass", "analysis": report["analysis"]}
