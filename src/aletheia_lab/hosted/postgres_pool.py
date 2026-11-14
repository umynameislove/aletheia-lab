"""Optional process-local Psycopg pool boundary; no schema or repository."""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from importlib import import_module
from typing import Literal, Protocol, cast
from urllib.parse import parse_qs, urlsplit

from aletheia_lab.hosted_auth.postgres_sessions import Connection

from .settings import RuntimeSettings, resolve_database_url

Role = Literal["api", "worker"]


class PoolUnavailable(RuntimeError):
    """A fixed internal failure that does not expose driver diagnostics."""


class Pool(Protocol):
    async def open(self, *, wait: bool, timeout: float) -> None: ...

    def connection(self, *, timeout: float | None = None) -> AbstractAsyncContextManager[Connection]: ...

    async def close(self, *, timeout: float) -> None: ...


class PoolFactory(Protocol):
    def __call__(
        self, *, conninfo: str, min_size: int, max_size: int, max_waiting: int,
        timeout: float, open: bool, num_workers: int,
    ) -> Pool: ...


def _default_factory(**kwargs: object) -> Pool:
    module = import_module("psycopg_pool")
    return cast(Pool, module.AsyncConnectionPool(**kwargs))


def _require_staging_tls(settings: RuntimeSettings, database_url: str) -> None:
    if settings.environment != "staging":
        return
    try:
        query = parse_qs(urlsplit(database_url).query, keep_blank_values=True, max_num_fields=64)
    except ValueError:
        raise PoolUnavailable("pool_tls_required") from None
    if query.get("sslmode") != ["verify-full"]:
        raise PoolUnavailable("pool_tls_required")


@asynccontextmanager
async def postgres_pool(
    settings: RuntimeSettings,
    environment: Mapping[str, str],
    *,
    role: Role,
    factory: PoolFactory = _default_factory,
) -> AsyncIterator[Pool]:
    """Open and close one bounded pool per process, using only its DB secret.

    Opening the pool is not a connectivity/readiness check. The future process
    factory must call `probe_postgres` and also check its other dependencies.
    """
    if role not in ("api", "worker"):
        raise ValueError("pool role must be api or worker")
    database_url = resolve_database_url(settings, environment).get_secret_value()
    _require_staging_tls(settings, database_url)
    wait = settings.storage.pool_wait_timeout_seconds
    maximum = settings.api.database_pool_size if role == "api" else settings.worker.database_pool_size
    max_waiting = settings.api.concurrency_limit if role == "api" else settings.worker.job_slots_per_process
    close_timeout = settings.api.resource_close_timeout_seconds if role == "api" else settings.worker.shutdown_grace_seconds
    try:
        pool = factory(
            conninfo=database_url, min_size=0, max_size=maximum, max_waiting=max_waiting,
            timeout=wait, open=False, num_workers=1,
        )
    except ImportError:
        raise PoolUnavailable("pool_driver_unavailable") from None
    except Exception:
        raise PoolUnavailable("pool_create_failed") from None
    cancelled: asyncio.CancelledError | None = None
    try:
        try:
            await pool.open(wait=False, timeout=wait)
        except Exception:
            raise PoolUnavailable("pool_open_failed") from None
        yield pool
    except asyncio.CancelledError as error:
        cancelled = error
        raise
    finally:
        try:
            await pool.close(timeout=close_timeout)
        except Exception:
            if cancelled is not None:
                cancelled.add_note("pool_cleanup_failed")
            else:
                raise PoolUnavailable("pool_close_failed") from None


async def probe_postgres(pool: Pool, *, timeout: float) -> bool:
    """Bounded connection and query probe; not an application-readiness proof."""
    if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("probe timeout must be finite and positive")
    try:
        async with asyncio.timeout(timeout):
            async with pool.connection(timeout=timeout) as connection:
                cursor = await connection.execute("SELECT 1")
                return await cursor.fetchone() == (1,)
    except Exception:
        return False
