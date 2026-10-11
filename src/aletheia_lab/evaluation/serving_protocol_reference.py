"""Separated references for bounded serving protocol predicates.

Bento uses Python argument binding and independently authored named axes.
TorchServe uses JSON and protobuf fields under an explicit core contract.
No native source mapper, converter, adapter or reserved comparison is imported.
"""

from __future__ import annotations

import inspect
import json
import math
import struct
from collections.abc import Callable, Mapping, Sequence
from typing import Any


def bento_operand_reference(
    method: Callable[..., Any],
    args: Sequence[Any],
    kwargs: Mapping[str, Any],
    axes_by_parameter: Mapping[str, int],
) -> dict[str, Any]:
    """Bind supplied operands independently of native BatchDimMapping outputs."""

    signature = inspect.signature(method)
    declared = list(signature.parameters.values())
    if not declared or declared[0].name != "self":
        raise ValueError("reference domain requires explicit instance self")
    binding = signature.bind(object(), *args, **kwargs)
    fixed = [
        parameter
        for parameter in declared[1:]
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
    ]
    vararg = next(
        (parameter for parameter in declared[1:] if parameter.kind == parameter.VAR_POSITIONAL),
        None,
    )
    varkw = next(
        (parameter for parameter in declared[1:] if parameter.kind == parameter.VAR_KEYWORD), None
    )
    expected: dict[int | str, int] = {}
    for position, _ in enumerate(args):
        parameter = fixed[position] if position < len(fixed) else vararg
        if parameter is None:
            raise ValueError("positional operand has no parameter")
        expected[position] = axes_by_parameter[parameter.name]
    for name in kwargs:
        parameter = signature.parameters.get(name)
        if parameter is None:
            parameter = varkw
        if parameter is None or parameter.kind in (
            parameter.POSITIONAL_ONLY,
            parameter.VAR_POSITIONAL,
        ):
            raise ValueError("keyword operand has no eligible parameter")
        expected[name] = axes_by_parameter[parameter.name]
    return {
        "axes": expected,
        "bound_names": list(binding.arguments)[1:],
        "request_operand_census": len(args) + len(kwargs),
    }


def bento_batch_member_reference(members: Sequence[Any], axis: int) -> dict[str, Any]:
    """Specify every member shape and element without native concatenate/split."""

    import numpy as np

    if not members:
        raise ValueError("nonempty complete native batch required")
    rank = members[0].ndim
    normalized_axis = axis if axis >= 0 else rank + axis
    if not 0 <= normalized_axis < rank:
        raise ValueError("invalid batch axis")
    member_rows = []
    start = 0
    for number, value in enumerate(members):
        if value.ndim != rank:
            raise ValueError("batch rank mismatch")
        for dim in range(rank):
            if dim != normalized_axis and value.shape[dim] != members[0].shape[dim]:
                raise ValueError("nonbatch shape mismatch")
        length = value.shape[normalized_axis]
        member_rows.append(
            {
                "member": number,
                "start": start,
                "end": start + length,
                "shape": list(value.shape),
                "elements": value.tolist(),
            }
        )
        start += length
    shape = list(members[0].shape)
    shape[normalized_axis] = start
    batch = np.empty(shape, dtype=np.result_type(*members))
    for row, value in zip(member_rows, members, strict=True):
        selector = [slice(None)] * rank
        selector[normalized_axis] = slice(row["start"], row["end"])
        batch[tuple(selector)] = value
    return {
        "batch": batch,
        "indices": [0] + [row["end"] for row in member_rows],
        "members": member_rows,
    }


def torch_core_reference(prediction_json: bytes | Mapping[str, Any]) -> dict[str, Any]:
    """Specify model_name, optional id and explicit numeric/BYTES tensor fields.

    Model version, extension parameters and raw-output encoding are excluded.
    Floating-point values are rounded to the declared protobuf wire width.
    REST controls concern the returned dict before JSON wire serialization.
    """

    data = (
        json.loads(prediction_json.decode("utf-8"))
        if isinstance(prediction_json, bytes)
        else prediction_json
    )
    if not isinstance(data, Mapping) or not isinstance(data.get("model_name"), str):
        raise ValueError("model_name is required")
    outputs = []
    for output in data["outputs"]:
        name, shape, datatype, values = (
            output[key] for key in ("name", "shape", "datatype", "data")
        )
        if not isinstance(name, str) or not isinstance(values, list):
            raise ValueError("invalid output core fields")
        if not all(isinstance(size, int) and size >= 0 for size in shape) or math.prod(
            shape
        ) != len(values):
            raise ValueError("tensor shape/data mismatch")
        if datatype == "FP32":
            values = [struct.unpack("<f", struct.pack("<f", value))[0] for value in values]
        elif datatype == "FP64":
            values = [float(value) for value in values]
        elif datatype == "BYTES":
            values = [
                value.encode("utf-8") if isinstance(value, str) else value for value in values
            ]
        elif datatype not in {
            "BOOL",
            "INT8",
            "INT16",
            "INT32",
            "INT64",
            "UINT8",
            "UINT16",
            "UINT32",
            "UINT64",
        }:
            raise ValueError("datatype outside explicit core domain")
        outputs.append({"name": name, "shape": list(shape), "datatype": datatype, "data": values})
    return {"id": data.get("id") or "", "model_name": data["model_name"], "outputs": outputs}


def observe_torch_core(value: Any) -> dict[str, Any]:
    """Read representation and fields without normalizing through KServe APIs."""

    descriptor = getattr(value, "DESCRIPTOR", None)
    if descriptor is not None and descriptor.full_name == "inference.ModelInferResponse":
        fields = {
            "BOOL": "bool_contents",
            "INT8": "int_contents",
            "INT16": "int_contents",
            "INT32": "int_contents",
            "INT64": "int64_contents",
            "UINT8": "uint_contents",
            "UINT16": "uint_contents",
            "UINT32": "uint_contents",
            "UINT64": "uint64_contents",
            "FP32": "fp32_contents",
            "FP64": "fp64_contents",
            "BYTES": "bytes_contents",
        }
        outputs = [
            {
                "name": item.name,
                "shape": list(item.shape),
                "datatype": item.datatype,
                "data": list(getattr(item.contents, fields[item.datatype])),
            }
            for item in value.outputs
        ]
        return {
            "representation": descriptor.full_name,
            "core": {"id": value.id, "model_name": value.model_name, "outputs": outputs},
        }
    if isinstance(value, dict):
        return {"representation": "REST dict", "core": torch_core_reference(value)}
    return {
        "representation": descriptor.full_name if descriptor else type(value).__qualname__,
        "core": None,
    }


def torch_conformance(
    value: Any,
    upstream_prediction_json: bytes | Mapping[str, Any],
    required_representation: str,
) -> dict[str, Any]:
    expected = torch_core_reference(upstream_prediction_json)
    observed = observe_torch_core(value)
    return {
        "expected": expected,
        "observed": observed,
        "conforms": observed["representation"] == required_representation
        and observed["core"] == expected,
    }
