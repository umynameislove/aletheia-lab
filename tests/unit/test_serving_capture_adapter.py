"""Synthetic adapter semantics; no framework transfer or native identity claim."""

import asyncio

import pytest

from aletheia_lab.evaluation.serving_capture_adapter import observe_entry


def entry():
    scope = {"__name__": "owned_adapter_test", "weight": 2.0}
    exec("def predict(x): return weight * x", scope)
    return scope["predict"], scope


def observe(function, invoke, generation=lambda: "g0"):
    return asyncio.run(
        observe_entry(
            function,
            invoke,
            package_prefixes=("owned_adapter_test",),
            request_id="r0",
            generation=generation,
        )
    )


def test_calls_actual_selected_entry_once_without_certifying_use():
    function, _ = entry()
    calls = []

    async def invoke(selected):
        calls.append(selected)
        return selected(3.0)

    result = observe(function, invoke)
    assert calls == [function]
    assert result.status == "observed" and result.output == 6.0
    assert result.invocation_completed
    assert result.before == result.after
    assert not result.native_use_verified and not result.capture_through_use_verified
    assert all(
        value >= 0
        for value in (result.capture_before_ns, result.invocation_ns, result.capture_after_ns)
    )


def test_async_mutation_is_not_locked_or_converted_to_qualification():
    function, scope = entry()

    async def invoke(selected):
        loop = asyncio.get_running_loop()
        ready = loop.create_future()
        loop.call_soon(ready.set_result, None)
        await ready
        scope["weight"] = 3.0
        return selected(3.0)

    result = observe(function, invoke)
    assert result.output == 9.0 and result.before != result.after
    assert not result.capture_through_use_verified


def test_aba_equal_endpoints_still_do_not_certify_immutability():
    function, scope = entry()

    async def invoke(selected):
        scope["weight"] = 3.0
        output = selected(3.0)
        scope["weight"] = 2.0
        return output

    result = observe(function, invoke)
    assert result.before == result.after and result.output == 9.0
    assert not result.native_use_verified and not result.capture_through_use_verified


def test_failure_keeps_before_after_and_class_not_private_exception_text():
    function, _ = entry()

    async def invoke(selected):
        raise RuntimeError("private payload must not enter the observation")

    result = observe(function, invoke)
    assert result.status == "invocation_failed"
    assert result.error_type == "RuntimeError" and result.output is None
    assert not result.invocation_completed
    assert result.before is not None and result.after is not None
    assert "private payload" not in repr(result)


def test_cancelled_callback_is_not_reported_as_completed_or_sdk_terminated():
    function, _ = entry()

    async def invoke(selected):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        observe(function, invoke)


def test_generation_change_is_observed_not_silently_normalized():
    function, _ = entry()
    current = ["g0"]

    async def invoke(selected):
        current[0] = "g1"
        return selected(3.0)

    result = observe(function, invoke, lambda: current[0])
    assert result.after["generation_stable"] is False
    assert not result.capture_through_use_verified


def test_none_output_still_records_completed_invocation_if_post_capture_fails():
    function, _ = entry()
    reads = [0]

    def generation():
        reads[0] += 1
        if reads[0] == 3:
            raise ValueError("post capture boundary")
        return "g0"

    async def invoke(selected):
        return None

    result = observe(function, invoke, generation)
    assert result.status == "post_capture_failed" and result.after is None
    assert result.invocation_completed and result.output is None
    assert result.error_type == "ValueError"
