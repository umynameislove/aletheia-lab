"""Bounded fingerprints of explicitly acquired resident components.

This is an instrumentation primitive, not automatic dependency discovery or a
proof of actual use. The adapter owns acquisition and request association. Opaque
objects are never replaced by disk bytes, output, repr, or expected state.
"""

from __future__ import annotations

import math
import types
from dataclasses import dataclass
from typing import Any

import numpy as np

from aletheia_lab.evaluation.request_model_audit import digest
from aletheia_lab.project.identity import content_sha256


@dataclass(frozen=True)
class CaptureLimits:
    maximum_bytes: int = 8_388_608
    maximum_nodes: int = 4096
    maximum_depth: int = 16


DEFAULT_LIMITS = CaptureLimits()


class _Unsupported(ValueError):
    pass


class _Encoder:
    def __init__(self, limits: CaptureLimits) -> None:
        self.limits = limits
        self.nodes = 0
        self.bytes = 0
        self.active: set[int] = set()

    def charge(self, size: int) -> None:
        self.bytes += size
        if self.bytes > self.limits.maximum_bytes:
            raise _Unsupported("byte_budget")

    def encode(self, value: Any, depth: int = 0) -> Any:
        self.nodes += 1
        if depth > self.limits.maximum_depth or self.nodes > self.limits.maximum_nodes:
            raise _Unsupported("traversal_budget")
        if value is None or type(value) is bool:
            self.charge(1)
            return [type(value).__name__, value]
        if type(value) in {int, float, str, bytes}:
            return self.scalar(value)
        if type(value) is np.ndarray:
            return self.array(value)
        if type(value) in {dict, list, tuple}:
            return self.container(value, depth)
        raise _Unsupported("opaque_or_unsupported_type")

    def scalar(self, value: Any) -> Any:
        if type(value) is float:
            if not math.isfinite(value):
                raise _Unsupported("nonfinite_scalar")
            text = value.hex()
        elif type(value) is bytes:
            self.charge(len(value))
            return ["bytes", content_sha256(value), len(value)]
        else:
            if type(value) is int and value.bit_length() > 4096:
                raise _Unsupported("integer_budget")
            text = str(value)
        if len(text) > self.limits.maximum_bytes - self.bytes:
            raise _Unsupported("byte_budget")
        try:
            self.charge(len(text.encode("utf-8")))
        except UnicodeEncodeError as exc:
            raise _Unsupported("unsupported_text_encoding") from exc
        return [type(value).__name__, text]

    def array(self, value: Any) -> Any:
        if value.dtype.hasobject or value.dtype.fields is not None:
            raise _Unsupported("unsupported_array_dtype")
        self.charge(value.nbytes)
        return ["ndarray", value.dtype.str, list(value.shape), content_sha256(value.tobytes("C"))]

    def container(self, value: Any, depth: int) -> Any:
        required_nodes = len(value) * (2 if type(value) is dict else 1)
        if required_nodes > self.limits.maximum_nodes - self.nodes:
            raise _Unsupported("traversal_budget")
        identity = id(value)
        if identity in self.active:
            raise _Unsupported("cyclic_component")
        self.active.add(identity)
        try:
            if type(value) is dict:
                if not all(type(key) is str for key in value):
                    raise _Unsupported("nonstring_mapping_key")
                return [
                    "dict",
                    [
                        [self.encode(k, depth + 1), self.encode(value[k], depth + 1)]
                        for k in sorted(value)
                    ],
                ]
            return [type(value).__name__, [self.encode(v, depth + 1) for v in value]]
        finally:
            self.active.remove(identity)


def callable_components(function: Any, *, global_names: tuple[str, ...]) -> dict[str, Any]:
    """Acquire a Python callable's code/defaults/closure and declared globals.

    No global-name selection is made from the experimental intervention. This
    does not discover transitive calls, native code, dynamic imports or exec.
    The frozen footprint must cover those separately or the query is unknown.
    """
    if type(function) is not types.FunctionType:
        raise ValueError("Python function required")
    if (
        type(global_names) is not tuple
        or len(global_names) > 1024
        or any(type(name) is not str or not 0 < len(name) <= 256 for name in global_names)
        or len(global_names) != len(set(global_names))
        or any(name not in function.__globals__ for name in global_names)
    ):
        raise ValueError("unique existing declared globals required")
    code = function.__code__
    return {
        "bytecode": code.co_code,
        "constants": code.co_consts,
        "names": code.co_names,
        "variables": code.co_varnames,
        "freevars": code.co_freevars,
        "cellvars": code.co_cellvars,
        "flags": code.co_flags,
        "stacksize": code.co_stacksize,
        "exceptiontable": code.co_exceptiontable,
        "argcount": (code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount),
        "defaults": function.__defaults__,
        "kwdefaults": function.__kwdefaults__,
        "closure": tuple(cell.cell_contents for cell in function.__closure__ or ()),
        "globals": {name: function.__globals__[name] for name in global_names},
    }


def capture_components(
    components: dict[str, Any],
    *,
    declared_names: tuple[str, ...],
    request_id: str,
    generation_before: str,
    generation_after: str,
    limits: CaptureLimits = DEFAULT_LIMITS,
) -> dict[str, Any]:
    """Hash supported acquired values without reading any expected-state oracle.

    Both generation tokens are adapter observations, not an atomicity proof.
    A lock/version discipline must cover capture and actual execution. Individual
    omissions affect only audit queries requiring those components.
    """
    if any(
        type(value) is not str or not 0 < len(value) <= 256
        for value in (request_id, generation_before, generation_after)
    ):
        raise ValueError("request and actual generation association required")
    if (
        type(components) is not dict
        or type(declared_names) is not tuple
        or not 0 < len(declared_names) <= 1024
        or any(type(name) is not str or not 0 < len(name) <= 256 for name in declared_names)
        or len(declared_names) != len(set(declared_names))
    ):
        raise ValueError("nonempty unique declared component names required")
    if len(components) > len(declared_names) or set(components) - set(declared_names):
        raise ValueError("undeclared acquired component")
    if any(
        type(value) is not int or value <= 0
        for value in (limits.maximum_bytes, limits.maximum_nodes, limits.maximum_depth)
    ):
        raise ValueError("positive capture budgets required")
    encoder = _Encoder(limits)
    hashes: dict[str, str] = {}
    missing: dict[str, str] = {}
    for name in sorted(declared_names):
        if name not in components:
            missing[name] = "not_acquired"
            continue
        try:
            hashes[name] = digest(encoder.encode(components[name]))
        except _Unsupported as exc:
            missing[name] = str(exc)
    stable = generation_before == generation_after
    return {
        "schema": "declared-resident-capture/v1",
        "request_id": request_id,
        "generation_before": generation_before,
        "generation_after": generation_after,
        "generation_stable": stable,
        "status": "captured_declared_components"
        if stable and not missing
        else "partial_or_unstable",
        "component_sha256": hashes,
        "unknown_components": missing,
        "acquired_bytes": encoder.bytes,
        "visited_nodes": encoder.nodes,
        "automatic_closure_discovery": False,
        "host_or_hook_attested": False,
    }
