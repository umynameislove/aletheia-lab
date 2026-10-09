"""Instrumentation contracts, not framework-transfer or closure validation."""

import copy

import numpy as np
import pytest

from aletheia_lab.evaluation.resident_state_capture import (
    CaptureLimits,
    callable_components,
    capture_components,
)


def capture(value, **options):
    return capture_components(
        {"state": value},
        declared_names=("state",),
        request_id="request",
        generation_before="epoch-1",
        generation_after="epoch-1",
        **options,
    )


def identity(value):
    return capture(value)["component_sha256"]["state"]


def test_live_mutation_changes_fingerprint_without_object_replacement():
    state = {"weights": np.array([2.0, 3.0]), "config": {"scale": 2}}
    before = capture(state)
    identifier = id(state)
    state["weights"][0] = 9.0
    assert id(state) == identifier
    assert before["component_sha256"] != capture(state)["component_sha256"]
    assert before["automatic_closure_discovery"] is False
    assert before["host_or_hook_attested"] is False


def test_dictionary_order_and_array_layout_are_not_semantic_changes():
    assert identity({"a": 1, "b": 2}) == identity({"b": 2, "a": 1})
    a = np.arange(12).reshape(3, 4)
    assert identity(a) == identity(np.asfortranarray(a))


@pytest.mark.parametrize("left,right", [(True, 1), (1, 1.0), (0.0, -0.0), ([1], (1,)), (b"x", "x")])
def test_canonical_encoding_preserves_types(left, right):
    assert identity(left) != identity(right)


def test_shape_dtype_and_content_all_affect_array_fingerprint():
    a = np.array([1, 2], dtype="int32")
    assert identity(a) != identity(a.reshape(1, 2))
    assert identity(a) != identity(a.astype("int64"))
    assert identity(a) != identity(np.array([1, 3], dtype="int32"))


@pytest.mark.parametrize(
    "value",
    [object(), float("nan"), float("inf"), {1: "x"}, np.array([object()], dtype=object), 2**5000],
)
def test_unsupported_components_remain_unknown_without_repr_or_disk_fallback(value):
    report = capture(value)
    assert report["status"] == "partial_or_unstable"
    assert report["component_sha256"] == {}
    assert "state" in report["unknown_components"]


def test_cyclic_state_is_bounded_and_not_claimed_captured():
    state = []
    state.append(state)
    assert capture(state)["unknown_components"] == {"state": "cyclic_component"}


def test_missing_and_opaque_components_do_not_hide_supported_siblings():
    report = capture_components(
        {"a": 1, "b": object()},
        declared_names=("a", "b", "c"),
        request_id="r",
        generation_before="g",
        generation_after="g",
    )
    assert set(report["component_sha256"]) == {"a"}
    assert report["unknown_components"] == {"b": "opaque_or_unsupported_type", "c": "not_acquired"}


def test_unstable_generation_is_not_success_even_when_values_are_serializable():
    report = capture_components(
        {"a": 1},
        declared_names=("a",),
        request_id="r",
        generation_before="g1",
        generation_after="g2",
    )
    assert report["generation_stable"] is False
    assert report["status"] == "partial_or_unstable"


@pytest.mark.parametrize(
    "limits",
    [
        CaptureLimits(maximum_bytes=2),
        CaptureLimits(maximum_nodes=1),
        CaptureLimits(maximum_depth=1),
    ],
)
def test_limits_bound_real_traversal(limits):
    assert capture({"level": {"value": "hello"}}, limits=limits)["status"] == "partial_or_unstable"


def test_capture_does_not_mutate_input_and_does_not_execute_properties():
    class Opaque:
        @property
        def state(self):
            raise AssertionError("instrumentation must not execute unknown properties")

    original = {"scale": [1, 2]}
    before = copy.deepcopy(original)
    capture(original)
    assert original == before
    assert capture(Opaque())["unknown_components"]


def test_callable_defaults_and_declared_global_mutation_change_hash(monkeypatch):
    namespace = {"bias": 3}
    exec("def f(x, scale=2): return scale * x + bias", namespace)
    function = namespace["f"]
    before = identity(callable_components(function, global_names=("bias",)))
    namespace["bias"] = 7
    assert before != identity(callable_components(function, global_names=("bias",)))
    second = identity(callable_components(function, global_names=("bias",)))
    monkeypatch.setattr(function, "__defaults__", (5,))
    assert second != identity(callable_components(function, global_names=("bias",)))


@pytest.mark.parametrize("names", [("missing",), ("bias", "bias")])
def test_callable_requires_declared_existing_unique_global_bindings(names):
    namespace = {"bias": 3}
    exec("def f(x): return x + bias", namespace)
    with pytest.raises(ValueError):
        callable_components(namespace["f"], global_names=names)


def test_callable_nested_code_is_not_silently_flattened():
    def outer():
        return lambda: 1

    assert capture(callable_components(outer, global_names=()))["unknown_components"]


def test_undeclared_components_and_invalid_budgets_fail_closed():
    with pytest.raises(ValueError, match="undeclared"):
        capture_components(
            {"extra": 1},
            declared_names=("state",),
            request_id="r",
            generation_before="g",
            generation_after="g",
        )
    with pytest.raises(ValueError, match="positive"):
        capture(1, limits=CaptureLimits(maximum_bytes=True))


@pytest.mark.parametrize("value", [[1] * 100, {str(i): 1 for i in range(100)}])
def test_container_cardinality_rejected_before_traversal(value):
    report = capture(value, limits=CaptureLimits(maximum_nodes=2))
    assert report["visited_nodes"] == 1
    assert report["unknown_components"] == {"state": "traversal_budget"}


def test_oversized_string_rejected_before_encoding_and_surrogate_is_unknown():
    report = capture("x" * 100, limits=CaptureLimits(maximum_bytes=2))
    assert report["acquired_bytes"] == 0
    assert report["unknown_components"] == {"state": "byte_budget"}
    assert capture("\ud800")["unknown_components"] == {"state": "unsupported_text_encoding"}


@pytest.mark.parametrize("names", [["bias"], ([],), ("x" * 257,)])
def test_global_footprint_rejects_wrong_or_unbounded_names(names):
    def f():
        return 1

    with pytest.raises(ValueError):
        callable_components(f, global_names=names)


def test_component_names_are_bounded():
    with pytest.raises(ValueError):
        capture_components(
            {},
            declared_names=("x" * 257,),
            request_id="r",
            generation_before="g",
            generation_after="g",
        )
