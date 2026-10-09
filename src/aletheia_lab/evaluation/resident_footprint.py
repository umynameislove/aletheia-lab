"""Outcome-independent, bounded acquisition of a Python entry's potential state.

This is not an execution slice, automatic full closure discovery or attestation.
Unsupported edges prevent a complete footprint claim; observed values remain
available to query-specific consumers. No fault names or expected values enter.
"""

from __future__ import annotations

import dis
import inspect
import types
from typing import Any

import numpy as np

from aletheia_lab.evaluation.resident_state_capture import (
    DEFAULT_LIMITS,
    CaptureLimits,
    capture_components,
)

_MISSING = object()
_FIXED_BUILTIN_IDENTITIES = (abs, bool, float, int, len, max, min, sum)
_CLASS_METADATA = {"__module__", "__dict__", "__weakref__", "__doc__", "__annotations__"}


class _Boundary(ValueError):
    pass


class _Discovery:
    def __init__(self, packages: tuple[str, ...], limits: CaptureLimits) -> None:
        self.packages = packages
        self.limits = limits
        self.nodes: list[Any] = []
        self.identities: dict[int, int] = {}
        self.references: list[Any] = []
        self.active_methods: set[tuple[int, int]] = set()
        self.missing: dict[str, str] = {}
        self.work_nodes = 0

    def owned(self, module: Any) -> bool:
        return type(module) is str and any(
            module == prefix or module.startswith(prefix + ".") for prefix in self.packages
        )

    def reject(self, path: str, reason: str) -> list[str]:
        self.missing[path] = reason
        return ["unsupported", reason]

    def cardinality(self, size: int) -> None:
        if size > self.limits.maximum_nodes - self.work_nodes:
            raise _Boundary("node_budget")

    def charge(self) -> None:
        self.cardinality(1)
        self.work_nodes += 1

    def walk(self, value: Any, path: str, depth: int = 0) -> Any:
        try:
            self.charge()
        except _Boundary as exc:
            return self.reject(path, str(exc))
        if depth > self.limits.maximum_depth:
            return self.reject(path, "depth_budget")
        if value is None or type(value) in {bool, int, float, str, bytes, np.ndarray}:
            return ["value", value]
        if type(value) in {types.BuiltinFunctionType, type} and any(
            value is builtin for builtin in _FIXED_BUILTIN_IDENTITIES
        ):
            # Identity only: e.g. abs(x) can dispatch to x.__abs__ outside this graph.
            return ["fixed_python_builtin", value.__name__]
        identity = id(value)
        if identity in self.identities:
            return ["node", self.identities[identity]]
        try:
            self.cardinality(1)
        except _Boundary as exc:
            return self.reject(path, str(exc))
        identifier = len(self.nodes)
        self.identities[identity] = identifier
        # Keep temporary bound methods alive: an expired id must not alias a new edge.
        self.references.append(value)
        self.nodes.append(None)
        try:
            self.nodes[identifier] = self.project(value, path, depth)
        except _Boundary as exc:
            self.nodes[identifier] = self.reject(path, str(exc))
        return ["node", identifier]

    def project(self, value: Any, path: str, depth: int) -> Any:
        if type(value) in {dict, list, tuple}:
            return self.container(value, path, depth)
        if type(value) is types.FunctionType:
            return self.function(value, path, depth)
        if type(value) is types.MethodType:
            return self.method(value, path, depth)
        if type(value) is types.ModuleType:
            namespace = types.ModuleType.__getattribute__(value, "__dict__")
            if not self.owned(namespace.get("__name__")):
                raise _Boundary("outside_package_module")
            # Named edges are resolved separately, not every imported module global.
            return ["owned_module_namespace", namespace["__name__"]]
        if type(value) is type:
            namespace = type.__getattribute__(value, "__dict__")
            if not self.owned(namespace.get("__module__")):
                raise _Boundary("outside_package_class")
            self.cardinality(len(namespace))
            items = {key: item for key, item in namespace.items() if key not in _CLASS_METADATA}
            return ["declared_package_class", self.walk(items, path + "/class", depth + 1)]
        return self.instance(value, path, depth)

    def method(self, value: Any, path: str, depth: int) -> Any:
        key = (id(value.__func__), id(value.__self__))
        if key in self.active_methods:
            return ["bound_method_back_edge"]
        self.active_methods.add(key)
        try:
            return [
                "bound_method",
                self.function(value.__func__, path + "/function", depth + 1, value.__self__),
                self.walk(value.__self__, path + "/receiver", depth + 1),
            ]
        finally:
            self.active_methods.remove(key)

    def container(self, value: Any, path: str, depth: int) -> Any:
        self.cardinality(len(value))
        if type(value) is dict:
            if any(type(key) is not str or len(key) > 256 for key in value):
                raise _Boundary("unsupported_mapping_key")
            return [
                "dict",
                [
                    [key, self.walk(value[key], path + "/" + key, depth + 1)]
                    for key in sorted(value)
                ],
            ]
        return [
            type(value).__name__,
            [self.walk(item, path + "/" + str(i), depth + 1) for i, item in enumerate(value)],
        ]

    def instance(self, value: Any, path: str, depth: int) -> Any:
        cls = type(value)
        if type(cls) is not type:
            raise _Boundary("custom_metaclass")
        namespace = type.__getattribute__(cls, "__dict__")
        if not self.owned(namespace.get("__module__")):
            raise _Boundary("outside_package_or_native_object")
        if (
            inspect.getattr_static(cls, "__getattribute__") is not object.__getattribute__
            or inspect.getattr_static(cls, "__getattr__", _MISSING) is not _MISSING
        ):
            raise _Boundary("dynamic_attribute_lookup")
        descriptor = inspect.getattr_static(cls, "__dict__", _MISSING)
        if type(descriptor) is not types.GetSetDescriptorType or descriptor.__name__ != "__dict__":
            raise _Boundary("unsupported_instance_dictionary")
        state = descriptor.__get__(value, cls)
        if type(state) is not dict:
            raise _Boundary("unsupported_instance_dictionary")
        return ["declared_package_instance", self.walk(state, path + "/state", depth + 1)]

    def function(self, function: Any, path: str, depth: int, receiver: Any = _MISSING) -> Any:
        if depth > self.limits.maximum_depth:
            raise _Boundary("depth_budget")
        if not self.owned(function.__module__):
            raise _Boundary("outside_package_function")
        closure: dict[str, Any] = {}
        for name, cell in zip(
            function.__code__.co_freevars, function.__closure__ or (), strict=True
        ):
            try:
                closure[name] = cell.cell_contents
            except ValueError:
                closure[name] = self.reject(path + "/closure/" + name, "empty_closure_cell")
        bindings = dict(closure)
        if receiver is not _MISSING and function.__code__.co_argcount:
            bindings[function.__code__.co_varnames[0]] = receiver
        return [
            "python_function",
            self.code(
                function.__code__,
                function.__globals__,
                function.__builtins__,
                bindings,
                path + "/code",
                depth + 1,
            ),
            self.walk(function.__defaults__, path + "/defaults", depth + 1),
            self.walk(function.__kwdefaults__, path + "/kwdefaults", depth + 1),
            self.walk(closure, path + "/closure", depth + 1),
        ]

    def attribute(
        self,
        previous: Any,
        name: str,
        namespace: dict[str, Any],
        bindings: dict[str, Any],
        path: str,
        depth: int,
    ) -> Any:
        if previous is None:
            return self.reject(path, "unresolved_attribute_receiver")
        if previous.opname == "LOAD_GLOBAL":
            receiver = namespace.get(previous.argval, _MISSING)
        elif previous.opname in {"LOAD_FAST", "LOAD_DEREF"}:
            receiver = bindings.get(previous.argval, _MISSING)
        else:
            receiver = _MISSING
        if receiver is _MISSING:
            return self.reject(path, "unresolved_attribute_receiver")
        value = inspect.getattr_static(receiver, name, _MISSING)
        if value is _MISSING:
            return self.reject(path, "missing_static_attribute")
        if type(value) is types.FunctionType and type(receiver) not in {type, types.ModuleType}:
            value = types.MethodType(value, receiver)
        if type(value) in {staticmethod, classmethod}:
            return self.reject(path, "unsupported_descriptor_binding")
        return self.walk(value, path, depth)

    def instructions(self, code: Any) -> list[Any]:
        if len(code.co_code) > self.limits.maximum_bytes:
            raise _Boundary("bytecode_budget")
        instructions: list[dis.Instruction] = []
        for instruction in dis.get_instructions(code, adaptive=False):
            self.charge()
            instructions.append(instruction)
        return instructions

    def code(
        self,
        code: Any,
        namespace: dict[str, Any],
        intrinsics: dict[str, Any],
        bindings: dict[str, Any],
        path: str,
        depth: int,
    ) -> Any:
        if depth > self.limits.maximum_depth:
            return self.reject(path, "depth_budget")
        self.cardinality(len(code.co_consts) + len(code.co_names))
        if any(len(name) > 256 for name in code.co_names + code.co_varnames + code.co_freevars):
            raise _Boundary("code_name_budget")
        instructions = self.instructions(code)
        globals_used = sorted(
            {
                instruction.argval
                for instruction in instructions
                if instruction.opname == "LOAD_GLOBAL"
            }
        )
        edges = {
            name: self.walk(
                namespace.get(name, intrinsics.get(name, _MISSING)),
                path + "/global/" + name,
                depth + 1,
            )
            for name in globals_used
        }
        attributes = {}
        for i, instruction in enumerate(instructions):
            location = path + "/edge/" + str(instruction.offset)
            if instruction.opname in {"LOAD_ATTR", "LOAD_METHOD"}:
                attributes[str(instruction.offset)] = self.attribute(
                    instructions[i - 1] if i else None,
                    instruction.argval,
                    namespace,
                    bindings,
                    location,
                    depth + 1,
                )
            elif instruction.opname in {
                "IMPORT_NAME",
                "IMPORT_FROM",
                "STORE_GLOBAL",
                "STORE_DEREF",
                "STORE_ATTR",
                "STORE_SUBSCR",
                "DELETE_GLOBAL",
                "DELETE_DEREF",
                "DELETE_ATTR",
                "DELETE_SUBSCR",
            }:
                self.reject(location, "unsupported_import_or_state_mutation")
            elif instruction.opname == "STORE_FAST" and instruction.argval in bindings:
                self.reject(location, "reassigned_static_receiver")
            elif instruction.opname == "LOAD_DEREF" and instruction.argval not in bindings:
                self.reject(location, "unresolved_live_closure")
            elif instruction.opname in {
                "LOAD_NAME",
                "LOAD_FROM_DICT_OR_GLOBALS",
                "LOAD_FROM_DICT_OR_DEREF",
            }:
                self.reject(location, "unsupported_name_resolution")
        constants = [
            self.code(value, namespace, intrinsics, bindings, path + "/nested/" + str(i), depth + 1)
            if type(value) is types.CodeType
            else self.walk(value, path + "/const/" + str(i), depth + 1)
            for i, value in enumerate(code.co_consts)
        ]
        return {
            "bytecode": code.co_code,
            "constants": constants,
            "globals": edges,
            "attributes": attributes,
            "names": code.co_names,
            "variables": code.co_varnames,
            "freevars": code.co_freevars,
            "cellvars": code.co_cellvars,
            "flags": code.co_flags,
            "stacksize": code.co_stacksize,
            "exceptiontable": code.co_exceptiontable,
            "argcount": (code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount),
        }


def capture_entry(
    entry: Any,
    *,
    package_prefixes: tuple[str, ...],
    request_id: str,
    generation_before: str,
    generation_after: str,
    limits: CaptureLimits = DEFAULT_LIMITS,
) -> dict[str, Any]:
    """Acquire by the same fixed traversal rule; no component-name allowlist.

    The entry must actually be selected at use, under adapter synchronization.
    A complete bounded projection still does not prove executed dependency use.
    Opaque edges cannot be silently replaced by current disk or evaluator truth.
    Declared package names are a traversal boundary, not code-origin attestation.
    Input-dependent implicit dispatch needs separate operand/use evidence; fixed
    builtin identities are not a promise that arbitrary calls are side-effect-free.
    """
    if (
        type(package_prefixes) is not tuple
        or not 0 < len(package_prefixes) <= 16
        or any(type(prefix) is not str or not 0 < len(prefix) <= 128 for prefix in package_prefixes)
        or len(package_prefixes) != len(set(package_prefixes))
    ):
        raise ValueError("bounded unique declared package prefixes required")
    if type(entry) not in {types.FunctionType, types.MethodType}:
        raise ValueError("actual Python entry callable required")
    if any(
        type(value) is not int or value <= 0
        for value in (limits.maximum_bytes, limits.maximum_nodes, limits.maximum_depth)
    ):
        raise ValueError("positive footprint budgets required")
    discovered = _Discovery(package_prefixes, limits)
    root = discovered.walk(entry, "entry")
    capture = capture_components(
        {"potential_graph": {"root": root, "nodes": discovered.nodes}},
        declared_names=("potential_graph",),
        request_id=request_id,
        generation_before=generation_before,
        generation_after=generation_after,
        limits=limits,
    )
    capture.update(
        rule="python-entry-bounded-graph/v1",
        package_prefixes=list(package_prefixes),
        unresolved_edges=discovered.missing,
        projection_complete=not discovered.missing
        and capture["status"] == "captured_declared_components",
        actual_dependency_use_proven=False,
        automatic_closure_discovery=False,
        selection_uses_fault_names=False,
        discovered_nodes=len(discovered.nodes),
        discovery_work_nodes=discovered.work_nodes,
        package_ownership_verified=False,
        input_operand_state_acquired=False,
        implicit_dispatch_covered=False,
        sufficiency_established=False,
    )
    if discovered.missing:
        capture["status"] = "partial_or_unstable"
    return capture
