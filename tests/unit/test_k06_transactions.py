"""Retry policy checks; PostgreSQL proof is in the integration module."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import pytest

from aletheia_lab.hosted.action_audit import request_context
from aletheia_lab.hosted.transactions import (
    TransactionOutcomeUnknown,
    TransactionUnavailable,
    run_transaction,
)

psycopg = pytest.importorskip("psycopg")


class Pool:
    autocommit = False

    def __init__(self, *, commit_error=None):
        self.active = False
        self.entries = 0
        self.commit_error = commit_error

    @asynccontextmanager
    async def connection(self):
        assert not self.active
        self.active = True
        self.entries += 1
        try:
            yield self
            if self.commit_error:
                raise self.commit_error
        finally:
            self.active = False


@pytest.mark.parametrize("failure", [psycopg.errors.DeadlockDetected, psycopg.errors.SerializationFailure])
def test_definite_abort_replays_whole_callback_after_release(failure, monkeypatch):
    async def run():
        pool, calls, waits = Pool(), [], []

        async def backoff(seconds):
            assert not pool.active
            assert 0 < seconds <= 0.3
            waits.append(seconds)

        monkeypatch.setattr("aletheia_lab.hosted.transactions.asyncio.sleep", backoff)

        async def operation(conn):
            assert conn is pool and pool.active
            calls.append("authority+revision reread")
            if len(calls) == 1:
                raise failure("private driver details")
            return "receipt"

        assert await run_transaction(pool, operation) == "receipt"
        assert len(calls) == 2 and pool.entries == 2 and len(waits) == 1
    asyncio.run(run())


def test_retry_budget_redacts_driver_and_releases_connection(monkeypatch):
    async def run():
        pool = Pool()

        async def backoff(_):
            assert not pool.active

        monkeypatch.setattr("aletheia_lab.hosted.transactions.asyncio.sleep", backoff)

        async def operation(_):
            raise psycopg.errors.SerializationFailure("private body/DSN")

        with pytest.raises(TransactionUnavailable) as failure:
            await run_transaction(pool, operation, max_attempts=3)
        assert "private" not in str(failure.value)
        assert pool.entries == 3 and not pool.active
    asyncio.run(run())


@pytest.mark.parametrize("failure", [psycopg.errors.LockNotAvailable, psycopg.errors.QueryCanceled,
                                     psycopg.OperationalError, psycopg.InterfaceError])
def test_database_timeouts_and_connection_loss_during_body_are_not_retried(failure):
    async def run():
        pool = Pool()

        async def operation(_):
            raise failure("private driver details")

        with pytest.raises(TransactionUnavailable):
            await run_transaction(pool, operation)
        assert pool.entries == 1 and not pool.active
    asyncio.run(run())


@pytest.mark.parametrize("failure", [psycopg.OperationalError, TimeoutError])
def test_commit_loss_is_unknown_and_is_not_automatically_replayed(failure):
    async def run():
        pool = Pool(commit_error=failure("private connection details"))

        async def operation(_):
            return "receipt"

        with pytest.raises(TransactionOutcomeUnknown) as caught:
            await run_transaction(pool, operation)
        assert "private" not in str(caught.value)
        assert pool.entries == 1 and not pool.active
    asyncio.run(run())


def test_total_deadline_includes_backoff_after_connection_release(monkeypatch):
    async def run():
        pool, backed_off = Pool(), []

        async def backoff(_):
            assert not pool.active
            backed_off.append(True)
            await asyncio.Event().wait()

        monkeypatch.setattr("aletheia_lab.hosted.transactions.asyncio.sleep", backoff)

        async def operation(_):
            raise psycopg.errors.SerializationFailure("private driver details")

        with pytest.raises(TransactionUnavailable):
            await run_transaction(pool, operation, timeout_seconds=0.01)
        assert pool.entries == 1 and backed_off == [True] and not pool.active
    asyncio.run(run())


def test_cancellation_during_commit_preserves_cancellation_and_marks_unknown():
    async def run():
        committing = asyncio.Event()

        class CommitWait(Pool):
            @asynccontextmanager
            async def connection(self):
                async with super().connection() as conn:
                    yield conn
                    committing.set()
                    await asyncio.Event().wait()

        pool = CommitWait()

        async def operation(_):
            return "receipt"

        pending = asyncio.create_task(run_transaction(pool, operation))
        await committing.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError) as failure:
            await pending
        assert failure.value.__notes__ == ["transaction_outcome_unknown"]
        assert pool.entries == 1 and not pool.active
    asyncio.run(run())


def test_business_exception_and_spoofed_sqlstate_propagate_without_retry():
    class Conflict(ValueError):
        sqlstate = "40001"

    async def run():
        pool, problem = Pool(), Conflict("stale revision")

        async def operation(_):
            raise problem

        with pytest.raises(Conflict) as failure:
            await run_transaction(pool, operation)
        assert failure.value is problem and pool.entries == 1
    asyncio.run(run())


def test_deadline_is_total_and_external_cancellation_propagates():
    async def run():
        pool = Pool()

        async def operation(_):
            await asyncio.Event().wait()

        with pytest.raises(TransactionUnavailable):
            await run_transaction(pool, operation, timeout_seconds=0.01)
        assert pool.entries == 1 and not pool.active
        entered = asyncio.Event()

        async def cancelled(_):
            entered.set()
            await asyncio.Event().wait()

        pending = asyncio.create_task(run_transaction(pool, cancelled))
        await entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert pool.entries == 2 and not pool.active
    asyncio.run(run())


@pytest.mark.parametrize(("timeout", "attempts"), [(0, 3), (float("nan"), 3), (float("inf"), 3),
                                                   (True, 3), (1, 0), (1, True), (1, 11)])
def test_invalid_limits_fail_before_acquiring_connection(timeout, attempts):
    async def run():
        pool = Pool()

        async def operation(_):
            raise AssertionError("must not call")

        with pytest.raises(ValueError):
            await run_transaction(pool, operation, timeout_seconds=timeout, max_attempts=attempts)
        assert pool.entries == 0
    asyncio.run(run())


def test_autocommit_is_rejected():
    async def run():
        pool = Pool()
        pool.autocommit = True

        async def operation(_):
            raise AssertionError("must not call")

        with pytest.raises(RuntimeError, match="transactional"):
            await run_transaction(pool, operation)
        assert not pool.active
    asyncio.run(run())


@pytest.mark.parametrize(("failure", "commit_error", "outcome"), [
    (psycopg.errors.SerializationFailure, None, "rolled_back"),
    (ValueError, None, "rejected"),
    (None, psycopg.OperationalError("private driver diagnostics"), "outcome_unknown"),
])
def test_failure_logs_have_fixed_outcome_request_correlation_and_no_success(caplog, failure, commit_error, outcome):
    async def run():
        pool = Pool(commit_error=commit_error)

        async def operation(_):
            if failure:
                raise failure("private body/token/driver diagnostics")
            return "receipt"

        with (caplog.at_level(logging.INFO, logger="aletheia.action"), request_context("req_synthetic"),
              pytest.raises((TransactionUnavailable, TransactionOutcomeUnknown, ValueError))):
            await run_transaction(pool, operation, max_attempts=1)
        entries = [record for record in caplog.records if record.name == "aletheia.action"]
        assert len(entries) == 1
        assert entries[0].action_outcome == outcome and entries[0].request_id == "req_synthetic"
        assert entries[0].message == "action_" + outcome
        assert "private" not in caplog.text and "committed" not in caplog.text
    asyncio.run(run())
