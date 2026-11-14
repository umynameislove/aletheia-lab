"""Fail-closed bound on host/PostgreSQL clock divergence for login readiness.

This check is a prerequisite, not clock synchronization. Callers must schedule
it repeatedly and fail readiness on errors or stale measurements. A successful
one-off check does not establish an operational clock SLA.
"""
from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from time import monotonic

from .postgres_sessions import Pool


def clock_interval_within_bound(*, before: datetime, after: datetime, database: datetime,
                                max_skew_seconds: float, max_round_trip_seconds: float,
                                elapsed_seconds: float) -> bool:
    """Conservatively require the DB reading near *both* host-clock endpoints."""
    values = (max_skew_seconds, max_round_trip_seconds, elapsed_seconds)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
        return False
    if max_skew_seconds <= 0 or max_round_trip_seconds <= 0 or not 0 <= elapsed_seconds <= max_round_trip_seconds:
        return False
    if any(not isinstance(value, datetime) or value.utcoffset() is None for value in (before, after, database)):
        return False
    if after < before:
        return False
    # Detect wall clock jumps against an independent elapsed timer. A scheduler
    # pause is included in both elapsed values, but a clock step is not.
    if abs((after - before).total_seconds() - elapsed_seconds) > 0.25:
        return False
    limit = timedelta(seconds=max_skew_seconds)
    return abs(database - before) <= limit and abs(database - after) <= limit


async def probe_clock(pool: Pool, *, max_skew_seconds: float, timeout_seconds: float,
                      wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                      elapsed_clock: Callable[[], float] = monotonic) -> bool:
    try:
        async with asyncio.timeout(timeout_seconds):
            async with pool.connection() as conn:
                before, start = wall_clock(), elapsed_clock()
                cursor = await conn.execute("SELECT clock_timestamp()")
                row = await cursor.fetchone()
                after, elapsed = wall_clock(), elapsed_clock() - start
        if row is None or len(row) != 1 or not isinstance(row[0], datetime):
            return False
        return clock_interval_within_bound(before=before, after=after, database=row[0],
                                           max_skew_seconds=max_skew_seconds,
                                           max_round_trip_seconds=timeout_seconds, elapsed_seconds=elapsed)
    except Exception:
        return False
