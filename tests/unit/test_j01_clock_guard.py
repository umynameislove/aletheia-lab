"""Clock comparison never converts a stale or ambiguous reading into readiness."""
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from aletheia_lab.hosted_auth.clock_guard import clock_interval_within_bound, probe_clock

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def check(*, before=NOW, after=NOW + timedelta(milliseconds=100), database=NOW,
          skew=1.0, elapsed=0.1, timeout=1.0):
    return clock_interval_within_bound(before=before, after=after, database=database,
        max_skew_seconds=skew, max_round_trip_seconds=timeout, elapsed_seconds=elapsed)


def test_accepts_only_bounded_consistent_clock_sample():
    assert check()
    assert not check(database=NOW - timedelta(seconds=6))
    assert not check(after=NOW - timedelta(seconds=1))
    assert not check(before=NOW.replace(tzinfo=None))
    assert not check(elapsed=2)
    assert not check(elapsed=0.8)
    assert not check(skew=float("nan"))
    assert not check(skew=True)


class FakePool:
    def __init__(self, row):
        self.row = row

    @asynccontextmanager
    async def connection(self):
        yield self

    async def execute(self, query, params=()):
        assert query == "SELECT clock_timestamp()"
        return self

    async def fetchone(self):
        return self.row


@pytest.mark.parametrize("row,expected", [((NOW,), True), (None, False), (("bad",), False), ((NOW - timedelta(seconds=10),), False)])
def test_probe_fails_closed_for_bad_or_skewed_data(row, expected):
    ticks = iter([0.0, 0.1])
    walls = iter([NOW, NOW + timedelta(milliseconds=100)])
    assert asyncio.run(probe_clock(FakePool(row), max_skew_seconds=1, timeout_seconds=1,
                                  wall_clock=lambda: next(walls), elapsed_clock=lambda: next(ticks))) is expected
