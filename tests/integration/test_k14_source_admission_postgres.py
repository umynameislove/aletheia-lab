"""Reserve source memory before fetching large parts; drain cancellation exactly."""
import asyncio
import os
import threading
from contextlib import asynccontextmanager

import pytest
from test_k07_upload_staging_postgres import (
    _bundle,
    _create,
    _database,
    _digest,
    _parts,
    _run,
    _sources,
)

from aletheia_lab.hosted import postgres_sources as module
from aletheia_lab.hosted.resource_admission import ByteAdmission, ResourceBusy

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K14_TEST_DATABASE_URL'),
    reason='Explicit private K14 PostgreSQL URL required')]


@pytest.fixture(autouse=True)
def database(monkeypatch):
    monkeypatch.setenv('K07_TEST_DATABASE_URL', os.environ['K14_TEST_DATABASE_URL'])


def test_memory_overload_never_fetches_parts_and_ready_replay_still_works(monkeypatch):
    async def run():
        async with _database() as (pool, _):
            sources, payload = _sources(pool), _bundle()
            receipt, parts = await _create(sources, payload)
            await _parts(sources, receipt, parts)
            budget = ByteAdmission(2 * len(payload))
            monkeypatch.setattr(module, '_COMPLETION_BYTES', budget)
            budget.acquire(2 * len(payload))
            reads = []

            class Observed:
                @asynccontextmanager
                async def connection(self):
                    async with pool.connection() as actual:
                        class Connection:
                            autocommit = False
                            async def execute(self, query, params=(), **options):
                                if 'SELECT part_number,byte_size,sha256,payload' in query:
                                    reads.append(True)
                                return await actual.execute(query, params, **options)
                        yield Connection()

            observed = _sources(Observed())
            with pytest.raises(ResourceBusy):
                await observed.complete_upload(credential_digest=_digest(), workspace_id='a', upload_id=receipt.upload_id)
            assert reads == [] and budget.snapshot().active == 2 * len(payload)
            budget.release(2 * len(payload))
            ready = await sources.complete_upload(credential_digest=_digest(), workspace_id='a', upload_id=receipt.upload_id)
            assert ready.state == 'ready' and budget.snapshot().active == 0
            budget.acquire(2 * len(payload))
            try:
                assert await observed.complete_upload(credential_digest=_digest(), workspace_id='a', upload_id=receipt.upload_id) == ready
                assert reads == []
            finally:
                budget.release(2 * len(payload))
    _run(run())


def test_cancellation_keeps_byte_reservation_until_real_inspection_finishes(monkeypatch):
    async def run():
        async with _database() as (pool, _):
            sources, payload = _sources(pool), _bundle()
            receipt, parts = await _create(sources, payload)
            await _parts(sources, receipt, parts)
            budget, entered, release = ByteAdmission(2 * len(payload)), threading.Event(), threading.Event()
            monkeypatch.setattr(module, '_COMPLETION_BYTES', budget)
            original = module.inspect_source_bundle
            def inspect(*args, **options):
                entered.set()
                assert release.wait(5)
                return original(*args, **options)
            monkeypatch.setattr(module, 'inspect_source_bundle', inspect)
            completing = asyncio.create_task(sources.complete_upload(credential_digest=_digest(), workspace_id='a', upload_id=receipt.upload_id))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                completing.cancel()
                await asyncio.sleep(.01)
                completing.cancel()
                await asyncio.sleep(.01)
                assert not completing.done() and budget.snapshot().active == 2 * len(payload)
                with pytest.raises(ResourceBusy):
                    await _sources(pool).complete_upload(credential_digest=_digest(), workspace_id='a', upload_id=receipt.upload_id)
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await completing
            assert budget.snapshot().active == 0 and sources._parse_future is None
            async with pool.connection() as conn:
                assert await (await conn.execute('SELECT count(*) FROM j01_sources')).fetchone() == (0,)
            ready = await sources.complete_upload(credential_digest=_digest(), workspace_id='a', upload_id=receipt.upload_id)
            assert ready.state == 'ready'
    _run(run())
