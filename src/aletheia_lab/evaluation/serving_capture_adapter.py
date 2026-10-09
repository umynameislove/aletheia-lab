"""Reusable observations around a selected entry, without a new checker.

The native adapter must establish which entry/operands actually execute and the
capture-through-use discipline. These observations do not certify those facts.
The unchanged bounded graph rule handles opaque state conservatively.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic_ns
from typing import Any

from aletheia_lab.evaluation.resident_footprint import capture_entry
from aletheia_lab.evaluation.resident_state_capture import DEFAULT_LIMITS, CaptureLimits


@dataclass(frozen=True)
class EntryObservation:
    request_id: str
    status: str
    before: dict[str, Any]
    after: dict[str, Any] | None
    output: Any
    error_type: str | None
    capture_before_ns: int
    invocation_ns: int
    capture_after_ns: int
    invocation_completed: bool
    # A bridge must provide independent evidence for these, not flip these flags.
    native_use_verified: bool = False
    capture_through_use_verified: bool = False


async def observe_entry(
    entry: Any,
    invoke: Callable[[Any], Awaitable[Any]],
    *,
    package_prefixes: tuple[str, ...],
    request_id: str,
    generation: Callable[[], str],
    limits: CaptureLimits = DEFAULT_LIMITS,
) -> EntryObservation:
    """Capture -> invoke selected entry once -> capture, with disjoint timers.

    No expected state, changed-component list, reference or proof policy is accepted.
    `invoke` owns the actual SDK call; receiving `entry` does not prove it used it.
    No lock is introduced and async suspension is allowed. Equal endpoint hashes or
    generations do not rule out a concurrent/ABA mutation. Cancellation propagates:
    the controller must preserve its census and verify owned SDK cleanup separately.
    """
    started = monotonic_ns()
    initial_generation = generation()
    before = capture_entry(
        entry,
        package_prefixes=package_prefixes,
        request_id=request_id,
        generation_before=initial_generation,
        generation_after=generation(),
        limits=limits,
    )
    captured = monotonic_ns()
    output, error_type, invocation_completed = None, None, False
    try:
        output = await invoke(entry)
        invocation_completed = True
    except Exception as exc:
        # Error classes suffice here; private exception payloads are not emitted.
        error_type = type(exc).__name__
    invoked = monotonic_ns()
    after = None
    try:
        after = capture_entry(
            entry,
            package_prefixes=package_prefixes,
            request_id=request_id,
            generation_before=initial_generation,
            generation_after=generation(),
            limits=limits,
        )
    except Exception as exc:
        if error_type is None:
            error_type = type(exc).__name__
        status = "post_capture_failed"
    else:
        status = "invocation_failed" if error_type else "observed"
    finished = monotonic_ns()
    return EntryObservation(
        request_id=request_id,
        status=status,
        before=before,
        after=after,
        output=output,
        error_type=error_type,
        capture_before_ns=captured - started,
        invocation_ns=invoked - captured,
        capture_after_ns=finished - invoked,
        invocation_completed=invocation_completed,
    )
