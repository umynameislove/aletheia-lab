"""Bounded source-informed protocol transfer, not model-enrollment validation.

Affected/fixed native causal methods are executed unchanged. The transport is
local; inputs and audit contracts are authored. Source eligibility is narrower
than complete historic deployments or natural-operator incidents.
"""

from __future__ import annotations

import importlib.metadata
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.serving_protocol_native import NativeSources
from aletheia_lab.evaluation.serving_protocol_reference import (
    bento_batch_member_reference,
    bento_operand_reference,
    torch_conformance,
)
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

CODE_PATHS = (
    "scripts/serving_protocol_transfer.py",
    *(
        f"src/aletheia_lab/evaluation/{name}.py"
        for name in (
            "serving_protocol_native",
            "serving_protocol_reference",
            "serving_protocol_transfer",
            "model_load_retention",
        )
    ),
    "src/aletheia_lab/content_hashing.py",
    "src/aletheia_lab/project/identity.py",
    "src/aletheia_lab/filesystem.py",
)


def _methods() -> dict[str, Any]:
    def pair(self: Any, x: Any, y: Any) -> None:
        pass

    def keyword(self: Any, x: Any, *, mask: Any) -> None:
        pass

    def positional(self: Any, x: Any, /, y: Any, *, z: Any) -> None:
        pass

    def varargs(self: Any, x: Any, *extras: Any) -> None:
        pass

    def varkw(self: Any, x: Any, **extras: Any) -> None:
        pass

    def empty(self: Any) -> None:
        pass

    return {
        "pair": pair,
        "keyword": keyword,
        "positional": positional,
        "varargs": varargs,
        "varkw": varkw,
        "empty": empty,
    }


def mapping_specs() -> list[dict[str, Any]]:
    return [
        {
            "id": "uniform-control",
            "method": "pair",
            "dim": 0,
            "axes": {"x": 0, "y": 0},
            "args": 2,
            "kwargs": [],
        },
        {
            "id": "heterogeneous",
            "method": "pair",
            "dim": ([0, 1], 0),
            "axes": {"x": 0, "y": 1},
            "args": 2,
            "kwargs": [],
        },
        {
            "id": "keyword-only",
            "method": "keyword",
            "dim": ([0, 1], 0),
            "axes": {"x": 0, "mask": 1},
            "args": 1,
            "kwargs": ["mask"],
        },
        {
            "id": "positional-only",
            "method": "positional",
            "dim": ([0, 1, 0], 0),
            "axes": {"x": 0, "y": 1, "z": 0},
            "args": 2,
            "kwargs": ["z"],
        },
        {
            "id": "vararg",
            "method": "varargs",
            "dim": ([0, 1], 0),
            "axes": {"x": 0, "extras": 1},
            "args": 3,
            "kwargs": [],
        },
        {
            "id": "varkw",
            "method": "varkw",
            "dim": ([0, 1], 0),
            "axes": {"x": 0, "extras": 1},
            "args": 1,
            "kwargs": ["alpha", "beta"],
        },
        {
            "id": "no-operands",
            "method": "empty",
            "dim": ([], 0),
            "axes": {},
            "args": 0,
            "kwargs": [],
        },
        {
            "id": "negative-axis",
            "method": "pair",
            "dim": ([-1, 0], 0),
            "axes": {"x": -1, "y": 0},
            "args": 2,
            "kwargs": [],
        },
    ]


def response_specs() -> list[dict[str, Any]]:
    common = {"model_name": "protocol-probe", "id": "request-core"}
    tensors = [
        [{"name": "float", "shape": [2], "datatype": "FP32", "data": [0.1, -2.4]}],
        [{"name": "integer", "shape": [2], "datatype": "INT64", "data": [2**60, -3]}],
        [{"name": "boolean", "shape": [2], "datatype": "BOOL", "data": [True, False]}],
        [
            {"name": "first", "shape": [2], "datatype": "INT32", "data": [4, 8]},
            {"name": "second", "shape": [1], "datatype": "FP64", "data": [0.125]},
        ],
        [{"name": "text", "shape": [2], "datatype": "BYTES", "data": ["a", "beta"]}],
        [{"name": "empty", "shape": [0], "datatype": "FP32", "data": []}],
    ]
    return [
        {"case_id": f"tensor-{index}", "payload": {**common, "outputs": outputs}}
        for index, outputs in enumerate(tensors)
    ]


def _bento_case(native: NativeSources, spec: dict[str, Any], side: str) -> dict[str, Any]:
    method = _methods()[spec["method"]]
    args = tuple(range(spec["args"]))
    kwargs = dict.fromkeys(spec["kwargs"], 0)
    reference = bento_operand_reference(method, args, kwargs, spec["axes"])
    error, observed, members = None, {}, []
    try:
        mapping = native.bento_mapping(side)(spec["dim"], inspect.signature(method).parameters)
        observed = {str(key): mapping[key] for key in reference["axes"]}
        _, container = native.bento_params_and_ndarray()
        for key, axis in reference["axes"].items():
            normalized = axis if axis >= 0 else 2 + axis
            shapes = [[2, 2], [2, 2]]
            shapes[0][normalized], shapes[1][normalized] = 1, 3
            inputs = [
                np.arange(np.prod(shape)).reshape(shape) + index * 100
                for index, shape in enumerate(shapes)
            ]
            expected = bento_batch_member_reference(inputs, axis)
            actual, indices = container.batches_to_batch(inputs, observed[str(key)])
            recovered = container.batch_to_batches(actual, indices, observed[str(key)])
            correct = indices == expected["indices"] and np.array_equal(actual, expected["batch"])
            correct = correct and all(
                np.array_equal(a, b) for a, b in zip(inputs, recovered, strict=True)
            )
            members.append(
                {
                    "operand": str(key),
                    "native_axis": observed[str(key)],
                    "expected_axis": axis,
                    "shape": list(actual.shape),
                    "indices": indices,
                    "all_member_elements_correct": correct,
                }
            )
        conforms = observed == {str(key): axis for key, axis in reference["axes"].items()} and all(
            row["all_member_elements_correct"] for row in members
        )
    except (IndexError, KeyError, ValueError, TypeError) as exc:
        error, conforms = type(exc).__name__, False
    return {
        "family": "BentoML#2469",
        "condition": spec["id"],
        "side": side,
        "query": "parameter-axis and member association",
        "native_conforms": conforms,
        "native_error": error,
        "observed_axes": observed,
        "reference_axes": {str(key): axis for key, axis in reference["axes"].items()},
        "members": members,
        "baseline_verdict": "conforms" if conforms else "violation",
        "signature_only_verdict": "unknown",
        "enrollment_eligible": False,
    }


async def _local_controls(native: NativeSources, side: str) -> list[dict[str, Any]]:
    class Runnable:
        def apply(self, x: int, *, offset: int = 1) -> int:
            return x + offset

    owner = native.bento_local_ref(side)(SimpleNamespace(runnable_class=Runnable))
    rows = []
    for route in ("sync", "async"):
        try:
            result = (
                owner.run_method("apply", 2, offset=3)
                if route == "sync"
                else await owner.async_run_method("apply", 2, offset=3)
            )
            error = None
        except TypeError as exc:
            result, error = None, type(exc).__name__
        rows.append(
            {
                "family": "BentoML#2469",
                "condition": f"native-local-{route}",
                "side": side,
                "query": "ordinary instance-method call",
                "native_conforms": result == 5,
                "observed_result": result,
                "expected_result": 5,
                "native_error": error,
                "baseline_verdict": "conforms" if result == 5 else "violation",
                "signature_only_verdict": "unknown",
                "enrollment_eligible": False,
            }
        )
    return rows


async def _torch_case(
    native: NativeSources, deps: Any, spec: dict[str, Any], side: str, request_kind: str
) -> list[dict[str, Any]]:
    prediction = encode(spec["payload"]).encode()
    inputs = [{"name": "x", "shape": [2], "datatype": "FP32", "data": [1.0, 2.0]}]
    request = deps.infer.InferRequest(
        model_name="protocol-probe",
        infer_inputs=[deps.infer.InferInput(**row) for row in inputs],
        request_id="request-core",
    )
    if request_kind == "protobuf":
        request = request.to_grpc()

    class Stub:
        async def Predictions(self, received: Any) -> Any:
            # Transport reference reads native TS request bytes independently.
            expected = {"id": "request-core", "inputs": inputs}
            if (
                received.model_name != "protocol-probe"
                or json.loads(received.input["data"]) != expected
            ):
                raise ValueError("native request conversion violates supplied operands")
            return deps.prediction.PredictionResponse(prediction=prediction)

    model = native.torch_model(side, deps)(
        "protocol-probe",
        "http://owned:8080",
        "http://owned:8081",
        "owned:7070",
        "grpc-v2",
        "unused",
    )
    model._grpc_client_stub = model.grpc_client_stub = Stub()
    try:
        result, error = await model._grpc_predict(request), None
    except (ValueError, TypeError, IndexError) as exc:
        result, error = None, type(exc).__name__
    routes = (
        ("direct", None, "inference.ModelInferResponse"),
        ("grpc", {"user-agent": "grpc-python"}, "inference.ModelInferResponse"),
        ("json", {"content-type": "application/json"}, "REST dict"),
        ("no-header", None, "inference.ModelInferResponse"),
    )
    rows = []
    for route, headers, representation in routes:
        try:
            observed = result if route == "direct" else model.postprocess(result, headers)
            checked = torch_conformance(observed, prediction, representation)
            route_error = error
            reference_evaluable = True
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            checked, route_error = (
                {"conforms": False, "expected": None, "observed": None},
                type(exc).__name__,
            )
            reference_evaluable = False
        rows.append(
            {
                "family": "TorchServe#2566",
                "condition": spec["case_id"],
                "side": side,
                "request_kind": request_kind,
                "route": route,
                "query": "declared response representation/core tensor fields",
                "native_conforms": checked["conforms"],
                "native_error": route_error,
                "core_check": _json_safe(checked),
                "reference_evaluable": reference_evaluable,
                "baseline_verdict": ("conforms" if checked["conforms"] else "violation")
                if reference_evaluable
                else "unknown",
                "signature_only_verdict": "unknown",
                "enrollment_eligible": False,
            }
        )
    return rows


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return value


def prepare_transfer(
    root: Path, reserved: Path, dependencies: Path, design: Path, output: Path
) -> dict[str, Any]:
    native = NativeSources(reserved, dependencies)
    qualification = native.qualify_definitions()
    code = {name: file_sha256(root / name) for name in CODE_PATHS}
    plan = {
        "reserved": str(reserved.absolute()),
        "dependencies": str(dependencies.absolute()),
        "root": str(root.absolute()),
        "qualification": qualification,
        "source_pins": native.pins,
        "design_sha256": file_sha256(design),
        "code_sha256": code,
        "mapping_specs": mapping_specs(),
        "response_specs": response_specs(),
        "planned_conditions": 116,
        "full_deployment_qualified": False,
        "supported_component_repair_qualified": False,
        "runtime": {
            name: importlib.metadata.version(name) for name in ("numpy", "protobuf", "anyio")
        },
        "forecast": "complete native/source history determines narrow protocol predicates; fixed conformance is testable, not guaranteed",
    }
    plan["plan_sha256"] = content_sha256(encode(plan).encode())
    output.mkdir(exist_ok=False)
    for name in CODE_PATHS:
        write_new_file(output / "executed-code" / name, (root / name).read_bytes())
    write_new_file(output / "design.md", design.read_bytes())
    write_new_file(output / "plan.json", encode(plan).encode())
    return plan


def check_transfer_plan(path: Path) -> dict[str, Any]:
    plan: dict[str, Any] = json.loads(path.read_bytes())
    claimed = plan.pop("plan_sha256")
    if content_sha256(encode(plan).encode()) != claimed:
        raise ValueError("protocol execution seal differs")
    plan["plan_sha256"] = claimed
    root = Path(plan["root"])
    if set(plan["code_sha256"]) != set(CODE_PATHS):
        raise ValueError("protocol executable census differs")
    for name, expected in plan["code_sha256"].items():
        if file_sha256(root / name) != expected:
            raise ValueError("protocol executable changed after seal")
    if (
        plan["mapping_specs"] != _json_safe(mapping_specs())
        or plan["response_specs"] != response_specs()
    ):
        raise ValueError("protocol case census changed")
    if plan["runtime"] != {
        name: importlib.metadata.version(name) for name in ("numpy", "protobuf", "anyio")
    }:
        raise ValueError("protocol runtime differs")
    native = NativeSources(Path(plan["reserved"]), Path(plan["dependencies"]))
    if plan["source_pins"] != native.pins or plan["planned_conditions"] != 116:
        raise ValueError("protocol source or condition census differs")
    return plan


def check_condition_census(rows: list[dict[str, Any]]) -> None:
    """Retained rows must contain each declared condition exactly once."""
    expected: set[tuple[str, str, str, str | None, str | None]] = set()
    for side in ("affected", "fixed"):
        for spec in mapping_specs():
            expected.add(("BentoML#2469", side, spec["id"], None, None))
        for route in ("sync", "async"):
            expected.add(("BentoML#2469", side, f"native-local-{route}", None, None))
        for spec in response_specs():
            for request_kind in ("infer", "protobuf"):
                for route in ("direct", "grpc", "json", "no-header"):
                    expected.add(("TorchServe#2566", side, spec["case_id"], request_kind, route))
    observed = [
        (row["family"], row["side"], row["condition"], row.get("request_kind"), row.get("route"))
        for row in rows
    ]
    if len(observed) != len(expected) or set(observed) != expected:
        raise ValueError("protocol retained condition census differs")


def check_transfer_result(result: dict[str, Any], plan: dict[str, Any]) -> None:
    body = {key: value for key, value in result.items() if key != "results_sha256"}
    if (
        result["plan_sha256"] != plan["plan_sha256"]
        or content_sha256(encode(body).encode()) != result["results_sha256"]
    ):
        raise ValueError("protocol retained result binding differs")
    check_condition_census(result["rows"])
    if result["analysis"] != summarize_transfer(result["rows"]):
        raise ValueError("retained protocol analysis differs")


def summarize_transfer(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = []
    for family in ("BentoML#2469", "TorchServe#2566"):
        for side in ("affected", "fixed"):
            selected = [row for row in rows if row["family"] == family and row["side"] == side]
            counts.append(
                {
                    "family": family,
                    "side": side,
                    "conditions": len(selected),
                    "native_conforms": sum(row["native_conforms"] for row in selected),
                    "native_failures": sum(row["native_error"] is not None for row in selected),
                    "complete_history_conclusive": sum(
                        row["baseline_verdict"] != "unknown" for row in selected
                    ),
                    "signature_only_unknown": len(selected),
                }
            )
    all_evaluable = len(rows) == 116 and all(row["baseline_verdict"] != "unknown" for row in rows)
    return {
        "source_cluster_count": 2,
        "conditions": len(rows),
        "groups": counts,
        "F1_new_enrollment": "outside eligible domain",
        "F2": "supported in declared complete-history protocol domain"
        if all_evaluable
        else "unidentified in some reference conditions",
        "F3_component_repair": "unidentified: native component repair API not qualified",
        "limits": "source-informed causal slices; authored cases; no natural deployment, Java backend or model enrollment",
    }


async def execute_transfer(plan: dict[str, Any]) -> list[dict[str, Any]]:
    native = NativeSources(Path(plan["reserved"]), Path(plan["dependencies"]))
    deps = native.torch_dependencies()
    rows: list[dict[str, Any]] = []
    for side in ("affected", "fixed"):
        rows.extend(_bento_case(native, spec, side) for spec in mapping_specs())
        rows.extend(await _local_controls(native, side))
        for spec in response_specs():
            for request_kind in ("infer", "protobuf"):
                rows.extend(await _torch_case(native, deps, spec, side, request_kind))
    if len(rows) != plan["planned_conditions"]:
        raise ValueError("native protocol condition census differs")
    check_condition_census(rows)
    return rows
