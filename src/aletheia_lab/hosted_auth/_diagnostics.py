"""Fixed auth failure stages; no request/provider values enter diagnostic records."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from time import monotonic
from typing import Literal

Route = Literal["start", "callback"]
Stage = Literal[
    "readiness_before", "coordinator", "readiness_after", "begin.oidc", "begin.intent",
    "callback.claim", "callback.exchange", "callback.identity", "callback.commit",
]
_STAGES = {
    "start": frozenset(("readiness_before", "coordinator", "readiness_after", "begin.oidc", "begin.intent")),
    "callback": frozenset(("readiness_before", "coordinator", "readiness_after", "callback.claim",
                           "callback.exchange", "callback.identity", "callback.commit")),
}
_LOG = logging.getLogger("aletheia.auth.diagnostics")


def _failure(route: Route, stage: Stage, outcome: Literal["unavailable", "interrupted"], started: float) -> None:
    # A diagnostic handler must never replace the original auth/cancellation outcome.
    with suppress(Exception):
        _LOG.warning("auth_stage_failed", extra={
            "auth_route": route, "auth_stage": stage, "auth_outcome": outcome,
            "elapsed_ms": max(0, int((monotonic() - started) * 1000)),
        })


@contextmanager
def auth_stage(route: Route, stage: Stage) -> Iterator[None]:
    if stage not in _STAGES.get(route, frozenset()):
        raise ValueError("A fixed authentication diagnostic stage is required.")
    started = monotonic()
    try:
        yield
    except asyncio.CancelledError:
        _failure(route, stage, "interrupted", started)
        raise
    except TimeoutError:
        _failure(route, stage, "unavailable", started)
        raise
