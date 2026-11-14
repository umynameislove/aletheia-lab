"""Bounded retries for a complete database-only transaction."""
from __future__ import annotations

import asyncio
import math
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Any, NoReturn, TypeVar

from aletheia_lab.hosted_auth.postgres_sessions import Connection, Pool

from .action_audit import Outcome, report_outcome
from .observability import timed_stage

T = TypeVar("T")


class TransactionUnavailable(TimeoutError):
    def __init__(self) -> None:
        super().__init__("The transaction is temporarily unavailable.")


class TransactionOutcomeUnknown(TimeoutError):
    def __init__(self) -> None:
        super().__init__("The transaction outcome must be reconciled using the original operation key.")


async def run_transaction(
    pool: Pool, operation: Callable[[Connection], Awaitable[T]], *,
    timeout_seconds: float = 5.0, max_attempts: int = 3,
) -> T:
    """Observe the same-task transaction; its body owns retry/deadline/commit semantics."""
    with timed_stage("database_transaction"):
        return await _run_transaction(pool, operation, timeout_seconds=timeout_seconds, max_attempts=max_attempts)


async def _run_transaction(
    pool: Pool, operation: Callable[[Connection], Awaitable[T]], *,
    timeout_seconds: float = 5.0, max_attempts: int = 3,
) -> T:
    """Reread authority/revision inside operation on every attempt; no external effects.

    The pool context owns commit/rollback. Only PostgreSQL's definite transaction
    aborts are replayed, after the connection has been returned. An outer request
    cancellation remains authoritative and never starts another attempt.
    """
    _validate_limits(timeout_seconds, max_attempts)
    driver = import_module("psycopg")
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    state = _TransactionState()
    timeout = asyncio.timeout_at(deadline)
    task = asyncio.current_task()
    cancellations = task.cancelling() if task is not None else 0
    try:
        async with timeout:
            return await _attempt_transactions(pool, operation, max_attempts, timeout, state, driver)
    except (TransactionUnavailable, TransactionOutcomeUnknown):
        raise
    except TimeoutError:
        _raise_unavailable(state)
    except asyncio.CancelledError as error:
        if state.committing:
            error.add_note("transaction_outcome_unknown")
        report_outcome("outcome_unknown" if state.committing else "rolled_back")
        raise
    except Exception:
        # A driver's cancellation cleanup can replace CancelledError (e.g.
        # psycopg 3.3.8 binary when interrupted during a pending large flush).
        # Only our expired deadline permits timeout classification; unrelated
        # errors, including the same TypeError before it expires, stay intact.
        if task is not None and task.cancelling() > cancellations:
            # The same driver cleanup can mask an explicit caller cancellation.
            # Preserve that newly received signal; it is never a retryable timeout.
            if timeout.expired():
                report_outcome("outcome_unknown" if state.committing else "rolled_back")
            cancelled = asyncio.CancelledError()
            if state.committing:
                cancelled.add_note("transaction_outcome_unknown")
            raise cancelled from None
        if timeout.expired():
            _raise_unavailable(state)
        raise
    raise TransactionUnavailable()


@dataclass
class _TransactionState:
    committing: bool = False
async def _attempt_transactions(pool: Pool, operation: Callable[[Connection], Awaitable[T]],
                                max_attempts: int, timeout: asyncio.Timeout,
                                state: _TransactionState, driver: Any) -> T:
    for attempt in range(max_attempts):
        state.committing = False
        try:
            async with pool.connection() as conn:
                if conn.autocommit:
                    raise RuntimeError("transactional connection required")
                result = await operation(conn)
                state.committing = True
            return result
        except (driver.errors.SerializationFailure, driver.errors.DeadlockDetected):
            state.committing = False
            report_outcome("rolled_back")
            if attempt == max_attempts - 1:
                raise TransactionUnavailable() from None
        except (driver.errors.LockNotAvailable, driver.errors.QueryCanceled):
            report_outcome("rolled_back")
            raise TransactionUnavailable() from None
        except (driver.OperationalError, driver.InterfaceError):
            _raise_unavailable(state)
        except (ValueError, PermissionError):
            _report_attempt_outcome(timeout, "rejected")
            raise
        except TimeoutError:
            raise
        except Exception:
            _report_attempt_outcome(timeout, "outcome_unknown" if state.committing else "rolled_back")
            raise
        # The failed transaction has exited before any backoff.
        await asyncio.sleep(min(0.2, 0.025 * 2 ** attempt) * random.uniform(0.5, 1.5))
    raise TransactionUnavailable()

def _validate_limits(timeout_seconds: float, max_attempts: int) -> None:
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0
            or type(max_attempts) is not int or not 1 <= max_attempts <= 10):
        raise ValueError("A finite transaction deadline and bounded attempt count are required.")



def _raise_unavailable(state: _TransactionState) -> NoReturn:
    if state.committing:
        report_outcome("outcome_unknown")
        raise TransactionOutcomeUnknown() from None
    report_outcome("rolled_back")
    raise TransactionUnavailable() from None


def _report_attempt_outcome(timeout: asyncio.Timeout, outcome: Outcome) -> None:
    if not timeout.expired():
        report_outcome(outcome)
