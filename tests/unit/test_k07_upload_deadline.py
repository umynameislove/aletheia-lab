"""A driver's cleanup failure cannot turn an expired deadline into a new intent."""
import asyncio
from contextlib import asynccontextmanager

import pytest
from test_k06_transactions import Pool

from aletheia_lab.hosted.transactions import (
    TransactionOutcomeUnknown,
    TransactionUnavailable,
    run_transaction,
)


def test_real_type_error_before_deadline_is_preserved_and_never_retried(monkeypatch):
    async def run():
        pool, outcomes = Pool(), []
        monkeypatch.setattr("aletheia_lab.hosted.transactions.report_outcome", outcomes.append)
        original = TypeError("genuine private bug")

        async def operation(_):
            raise original

        with pytest.raises(TypeError) as caught:
            await run_transaction(pool, operation, timeout_seconds=5)
        assert caught.value is original and pool.entries == 1 and not pool.active
        assert outcomes == ["rolled_back"]
    asyncio.run(run())


@pytest.mark.parametrize("during_commit", [False, True])
def test_masked_deadline_is_typed_once_and_preserves_commit_uncertainty(monkeypatch, during_commit):
    async def run():
        outcomes = []
        monkeypatch.setattr("aletheia_lab.hosted.transactions.report_outcome", outcomes.append)

        async def broken_cancel():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise TypeError("an integer is required") from None

        class CommitFailure(Pool):
            @asynccontextmanager
            async def connection(self):
                async with super().connection() as conn:
                    yield conn
                    await broken_cancel()

        pool = CommitFailure() if during_commit else Pool()

        async def operation(_):
            if not during_commit:
                await broken_cancel()
            return "receipt"

        error = TransactionOutcomeUnknown if during_commit else TransactionUnavailable
        with pytest.raises(error):
            await run_transaction(pool, operation, timeout_seconds=0.01)
        assert pool.entries == 1 and not pool.active
        assert outcomes == ["outcome_unknown" if during_commit else "rolled_back"]
    asyncio.run(run())


@pytest.mark.parametrize("during_commit", [False, True])
def test_masked_new_caller_cancellation_propagates_after_cleanup(monkeypatch, during_commit):
    async def run():
        outcomes, entered = [], asyncio.Event()
        monkeypatch.setattr("aletheia_lab.hosted.transactions.report_outcome", outcomes.append)

        async def broken_cancel():
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise TypeError("an integer is required") from None

        class CommitFailure(Pool):
            @asynccontextmanager
            async def connection(self):
                async with super().connection() as conn:
                    yield conn
                    await broken_cancel()

        pool = CommitFailure() if during_commit else Pool()

        async def operation(_):
            if not during_commit:
                await broken_cancel()
            return "receipt"

        pending = asyncio.create_task(run_transaction(pool, operation, timeout_seconds=5))
        await entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await pending
        assert pool.entries == 1 and not pool.active and pending.cancelled()
        assert ("transaction_outcome_unknown" in getattr(caught.value, "__notes__", ())) == during_commit
        assert outcomes == ["outcome_unknown" if during_commit else "rolled_back"]
    asyncio.run(run())


@pytest.mark.parametrize("during_commit", [False, True])
def test_caller_cancellation_wins_an_already_expired_deadline_exactly_once(monkeypatch, during_commit):
    async def run():
        outcomes, deadline_seen = [], asyncio.Event()
        monkeypatch.setattr("aletheia_lab.hosted.transactions.report_outcome", outcomes.append)

        async def broken_cancel():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                deadline_seen.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    raise TypeError("an integer is required") from None

        class CommitFailure(Pool):
            @asynccontextmanager
            async def connection(self):
                async with super().connection() as conn:
                    yield conn
                    await broken_cancel()

        pool = CommitFailure() if during_commit else Pool()

        async def operation(_):
            if not during_commit:
                await broken_cancel()
            return "receipt"

        pending = asyncio.create_task(run_transaction(pool, operation, timeout_seconds=0.01))
        await deadline_seen.wait()
        pending.cancel()  # a distinct caller signal after the own deadline
        with pytest.raises(asyncio.CancelledError) as caught:
            await pending
        assert pending.cancelled() and pending.cancelling() == 1
        assert pool.entries == 1 and not pool.active
        assert ("transaction_outcome_unknown" in getattr(caught.value, "__notes__", ())) == during_commit
        assert outcomes == ["outcome_unknown" if during_commit else "rolled_back"]
    asyncio.run(run())
