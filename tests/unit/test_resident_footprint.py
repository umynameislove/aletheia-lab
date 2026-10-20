"""Fixed-rule acquisition tests, not serving-transfer or executed-use validation."""

import inspect
import types

import pytest

from aletheia_lab.evaluation.resident_footprint import capture_entry
from aletheia_lab.evaluation.resident_state_capture import CaptureLimits


def namespace(source, **values):
    scope = {"__name__": "owned_runtime", **values}
    exec(source, scope)
    return scope


def acquire(entry, **options):
    return capture_entry(
        entry,
        package_prefixes=("owned_runtime",),
        request_id="request-0",
        generation_before="g0",
        generation_after="g0",
        **options,
    )


def fingerprint(entry):
    return acquire(entry)["component_sha256"]["potential_graph"]


def test_live_globals_and_defaults_selected_without_component_names():
    scope = namespace("def entry(x, scale=2): return x * scale + bias", bias=3)
    entry = scope["entry"]
    report = acquire(entry)
    assert report["projection_complete"] is True
    assert report["selection_uses_fault_names"] is False
    assert report["actual_dependency_use_proven"] is False
    assert report["automatic_closure_discovery"] is False
    before = fingerprint(entry)
    scope["bias"] = 7
    assert fingerprint(entry) != before
    before = fingerprint(entry)
    entry.__defaults__ = (5,)
    assert fingerprint(entry) != before


def test_irrelevant_global_name_is_not_selected_after_observing_a_change():
    scope = namespace("def entry(x): return x + selected", selected=1, unrelated=2)
    before = fingerprint(scope["entry"])
    scope["unrelated"] = 100
    assert fingerprint(scope["entry"]) == before


def test_recursively_acquired_helper_is_actual_binding_not_current_disk():
    scope = namespace("def helper(x): return x + bias\ndef entry(x): return helper(x)", bias=2)
    before = fingerprint(scope["entry"])
    scope["bias"] = 9
    assert fingerprint(scope["entry"]) != before
    assert acquire(scope["entry"])["projection_complete"] is True


def test_nested_generator_code_globals_are_not_ignored():
    scope = namespace("def entry(x): return sum(scale * item for item in x)", scale=2)
    before = fingerprint(scope["entry"])
    scope["scale"] = 9
    assert fingerprint(scope["entry"]) != before


def test_live_closure_cell_and_kwdefaults_are_acquired():
    scope = namespace(
        "def factory():\n"
        " value = 3\n"
        " def entry(x, *, shift=1): return x + value + shift\n"
        " return entry\n"
    )
    entry = scope["factory"]()
    before = fingerprint(entry)
    entry.__closure__[0].cell_contents = 4
    assert fingerprint(entry) != before
    before = fingerprint(entry)
    entry.__kwdefaults__ = {"shift": 2}
    assert fingerprint(entry) != before


def test_uses_function_actual_builtins_not_host_builtins():
    custom = namespace("def custom_len(x): return 99")["custom_len"]
    scope = namespace("def entry(x): return len(x)", __builtins__={"len": custom})
    before = fingerprint(scope["entry"])
    custom.__defaults__ = (1,)
    assert fingerprint(scope["entry"]) != before


def test_plain_bound_self_is_acquired_passively_and_not_executed():
    scope = namespace("class Runtime:\n def entry(self, x): return x + self.bias\n")
    runtime = scope["Runtime"]()
    runtime.bias = 2
    report = acquire(runtime.entry)
    assert report["projection_complete"] is True
    before = fingerprint(runtime.entry)
    runtime.bias = 3
    assert fingerprint(runtime.entry) != before


def test_attribute_name_is_not_an_unrelated_function_global():
    scope = namespace("class Runtime:\n def entry(self, x): return x + self.bias\n", bias=99)
    runtime = scope["Runtime"]()
    runtime.bias = 2
    before = fingerprint(runtime.entry)
    scope["bias"] = 100
    assert fingerprint(runtime.entry) == before


def test_owned_module_attribute_tracks_actual_helper_without_binding_it_to_module():
    module = types.ModuleType("owned_runtime.helper")
    exec("def helper(x): return x + bias", module.__dict__)
    module.bias = 2
    scope = namespace("def entry(x): return module.helper(x)", module=module)
    before = fingerprint(scope["entry"])
    assert acquire(scope["entry"])["projection_complete"] is True
    module.bias = 3
    assert fingerprint(scope["entry"]) != before


@pytest.mark.parametrize(
    "source",
    [
        "class Runtime:\n @property\n def value(self): raise AssertionError('property called')\n def entry(self, x): return self.value\n",
        "class Runtime:\n @property\n def __dict__(self): raise AssertionError('dictionary called')\n def entry(self, x): return x\n",
        "class Runtime:\n def __getattribute__(self, name): raise AssertionError('lookup called')\n def entry(self, x): return x\n",
        "class Runtime:\n def __getattr__(self, name): raise AssertionError('fallback called')\n def entry(self, x): return self.missing\n",
    ],
)
def test_descriptors_and_custom_lookup_are_never_executed(source):
    scope = namespace(source)
    runtime = scope["Runtime"]()
    function = inspect.getattr_static(scope["Runtime"], "entry")
    report = acquire(types.MethodType(function, runtime))
    assert report["status"] == "partial_or_unstable"
    assert report["unresolved_edges"]
    assert report["projection_complete"] is False


@pytest.mark.parametrize(
    "source,values",
    [
        ("def entry(x): return getattr(state, 'hidden')(x)", {"state": object()}),
        ("def entry(x):\n import importlib\n return importlib.import_module('hidden').run(x)", {}),
        ("def entry(x): return eval('x+1')", {}),
        ("def entry(x): return state.update(x)", {"state": {}}),
        ("def entry(x): return state.value", {"state": object()}),
    ],
)
def test_dynamic_external_and_mutating_calls_are_unsupported_not_false_confidence(source, values):
    report = acquire(namespace(source, **values)["entry"])
    assert report["projection_complete"] is False
    assert report["status"] == "partial_or_unstable"
    assert report["unresolved_edges"]
    # A partial projection hash may still be useful, but is not a full identity verdict.
    assert report["actual_dependency_use_proven"] is False


def test_state_mutation_invalidates_same_token_and_same_lock_shortcut():
    scope = namespace("def entry(x):\n global scale\n scale += 1\n return x * scale", scale=2)
    report = acquire(scope["entry"])
    assert report["generation_stable"] is True
    assert report["projection_complete"] is False
    assert "unsupported_import_or_state_mutation" in report["unresolved_edges"].values()
    assert scope["scale"] == 2  # capture itself neither executes nor mutates the entry


def test_delete_mutation_is_not_readonly_projection():
    scope = namespace("def entry(x):\n global scale\n del scale\n return x", scale=2)
    report = acquire(scope["entry"])
    assert report["projection_complete"] is False
    assert "unsupported_import_or_state_mutation" in report["unresolved_edges"].values()


def test_module_namespace_code_does_not_claim_load_name_coverage():
    code = compile("result = bias", "owned-module", "exec")
    entry = types.FunctionType(code, {"bias": 2, "__name__": "owned_runtime"})
    report = acquire(entry)
    assert report["projection_complete"] is False
    assert "unsupported_name_resolution" in report["unresolved_edges"].values()


def test_reassigned_bound_receiver_does_not_silently_read_original_self():
    scope = namespace(
        "class Runtime:\n def entry(self, other):\n  self = other\n  return self.value\n"
    )
    runtime = scope["Runtime"]()
    runtime.value = 2
    report = acquire(runtime.entry)
    assert report["projection_complete"] is False
    assert "reassigned_static_receiver" in report["unresolved_edges"].values()


def test_recursive_function_graph_is_bounded_without_losing_back_edges():
    scope = namespace("def entry(x): return 0 if x == 0 else entry(x - 1)")
    assert acquire(scope["entry"])["projection_complete"] is True
    assert fingerprint(scope["entry"]) == fingerprint(scope["entry"])


def test_recursive_bound_method_is_bounded():
    scope = namespace(
        "class Runtime:\n def entry(self, x): return 0 if x == 0 else self.entry(x - 1)\n"
    )
    report = acquire(scope["Runtime"]().entry)
    assert report["discovered_nodes"] < 30
    assert report["visited_nodes"] <= 4096


@pytest.mark.parametrize(
    "limits",
    [
        CaptureLimits(maximum_nodes=2),
        CaptureLimits(maximum_depth=1),
        CaptureLimits(maximum_bytes=2),
    ],
)
def test_budget_failures_remain_partial(limits):
    report = acquire(namespace("def entry(x): return x + value", value=2)["entry"], limits=limits)
    assert report["projection_complete"] is False
    assert report["status"] == "partial_or_unstable"
    assert report["discovery_work_nodes"] <= limits.maximum_nodes


def test_primitive_edges_are_charged_before_final_encoding():
    scope = namespace("def entry(x): return values", values=[[1] * 5 for _ in range(5)])
    report = acquire(scope["entry"], limits=CaptureLimits(maximum_nodes=20))
    assert report["discovery_work_nodes"] <= 20
    assert report["projection_complete"] is False


def test_intrinsic_identity_is_not_input_dependent_dispatch_evidence():
    scope = namespace("def entry(x): return abs(x)")
    report = acquire(scope["entry"])
    assert report["projection_complete"] is True  # the bounded code graph, not x
    assert report["input_operand_state_acquired"] is False
    assert report["implicit_dispatch_covered"] is False
    assert report["sufficiency_established"] is False


def test_module_label_is_not_a_code_origin_attestation():
    scope = namespace("def entry(x): return x")
    report = acquire(scope["entry"])
    assert report["package_ownership_verified"] is False
    assert report["host_or_hook_attested"] is False


@pytest.mark.parametrize("packages", [(), ["owned_runtime"], ("",), ("x" * 129,), ("x", "x")])
def test_scope_is_bounded_unique_not_fault_component_list(packages):
    entry = namespace("def entry(x): return x")["entry"]
    with pytest.raises(ValueError):
        capture_entry(
            entry,
            package_prefixes=packages,
            request_id="r",
            generation_before="g",
            generation_after="g",
        )
