"""Principal facts, not DSN strings or SET ROLE, determine the runtime binding."""
import asyncio
from contextlib import asynccontextmanager

import pytest

from aletheia_lab.hosted.runtime_binding import verify_runtime_binding
from aletheia_lab.hosted.settings import StorageSettings


class Cursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class Pool:
    def __init__(self, row, *, shadow=None, error=False):
        self.row, self.shadow, self.error = row, shadow, error
        self.calls = 0

    @asynccontextmanager
    async def connection(self):
        yield self

    async def execute(self, query, params):
        self.calls += 1
        if self.error:
            raise RuntimeError("driver details must stay private")
        if "FROM pg_roles" in query:
            return Cursor(self.row)
        return Cursor((self.shadow or "k04_test",))


def settings(tmp_path, *, bound=True):
    return StorageSettings(database_url_env="ALETHEIA_HOSTED_DATABASE_URL", object_root=str(tmp_path),
        expected_schema="k04_test" if bound else None,
        expected_database_role="k04_api" if bound else None)


def run(coroutine):
    return asyncio.run(coroutine)


def test_real_principal_and_every_relation_must_match_binding(tmp_path):
    good = ("k04_api", "k04_api", "k04_test", *([False] * 8))
    assert run(verify_runtime_binding(Pool(good), settings(tmp_path), strict=True))
    assert not run(verify_runtime_binding(Pool(good, shadow="other"), settings(tmp_path), strict=True))
    assert not run(verify_runtime_binding(Pool(good, error=True), settings(tmp_path), strict=True))
    for index in range(11):
        bad = list(good)
        bad[index] = "other" if index < 3 else True
        assert not run(verify_runtime_binding(Pool(tuple(bad)), settings(tmp_path), strict=True))


def test_unconfigured_legacy_is_not_strict_evidence(tmp_path):
    pool = Pool(None)
    assert run(verify_runtime_binding(pool, settings(tmp_path, bound=False)))
    assert not run(verify_runtime_binding(pool, settings(tmp_path, bound=False), strict=True))
    assert pool.calls == 0


def test_external_cancellation_propagates(tmp_path):
    class Cancelled(Pool):
        async def execute(self, query, params):
            raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        run(verify_runtime_binding(Cancelled(None), settings(tmp_path), strict=True))
