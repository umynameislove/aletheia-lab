"""Real PG authority and report children under bounded process export admission."""
import asyncio
import hashlib
import os

import pytest
from test_k04_native_catalog_http_postgres import _native_application
from test_k04_runtime_principal_postgres import _run

from aletheia_lab.hosted import catalog_access
from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.project_mutations import PostgresProjectMutations
from aletheia_lab.hosted.resource_admission import ProcessSlots, ResourceBusy
from aletheia_lab.hosted.workspace_management import WorkspaceManagement
from aletheia_lab.product.reports import render_product_report

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K14_TEST_DATABASE_URL'),
    reason='Explicit private K14 PostgreSQL URL required')]


@pytest.fixture(autouse=True)
def private_runtime(monkeypatch):
    monkeypatch.setenv('K04_TEST_DATABASE_URL', os.environ['K14_TEST_DATABASE_URL'])
    monkeypatch.setattr(catalog_access, '_EXPORT_SLOTS', ProcessSlots(1, 4))


async def _queued(slots, count):
    async with asyncio.timeout(3):
        while slots.snapshot().waiting != count:
            await asyncio.sleep(.005)


def test_native_five_export_burst_serializes_one_real_child_and_replays_durable_receipt_while_full(tmp_path):
    async def run():
        async with _native_application(tmp_path) as case:
            await case.api.resize(min_size=1, max_size=1)
            await case.api.wait(timeout=10)
            access, slots = case.app._access, catalog_access._EXPORT_SLOTS
            args = (case.digests['owner_a'], case.workspace, case.child.record_id, 'json')
            existing = await access.create_export(*args, key_hash='f' * 64)
            original_result, original_render = access.result, access.renderer.render
            reads, renders = [], []
            entered, release = asyncio.Event(), asyncio.Event()

            async def result(*values, **options):
                reads.append(asyncio.current_task().get_name())
                return await original_result(*values, **options)

            async def render(report_format, data):
                renders.append(asyncio.current_task().get_name())
                entered.set()
                await release.wait()
                return await original_render(report_format, data)
            access.result, access.renderer.render = result, render
            tasks = [asyncio.create_task(access.create_export(*args, key_hash=f'{index:064x}'),
                name='native-export' + str(index)) for index in range(5)]
            try:
                await asyncio.wait_for(entered.wait(), 3)
                await _queued(slots, 4)
                assert reads == renders == ['native-export0']
                async with case.api.connection(timeout=1) as conn:
                    assert await (await conn.execute('SELECT 1')).fetchone() == (1,)
                assert await access.create_export(*args, key_hash='f' * 64) == existing
                assert reads == renders == ['native-export0']
                with pytest.raises(ResourceBusy):
                    await access.create_export(*args, key_hash='e' * 64)
                release.set()
                items = await asyncio.wait_for(asyncio.gather(*tasks), 10)
                assert reads == renders == ['native-export' + str(index) for index in range(5)]
                expected = render_product_report(case.child.view, 'json')
                for item in items:
                    saved, payload = await access.download_export(case.digests['owner_a'], case.workspace, item.export_id)
                    assert saved == item and payload == expected
                    assert item.descriptor.sha256 == hashlib.sha256(expected).hexdigest()
                async with case.owner.connection() as conn:
                    assert await (await conn.execute('SELECT count(*) FROM k02_exports')).fetchone() == (6,)
            finally:
                release.set()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            assert slots.snapshot().active == slots.snapshot().waiting == 0
    _run(run())


@pytest.mark.parametrize('loss', ['source_grant', 'session', 'tombstone'])
def test_native_queued_export_rechecks_revoked_or_deleted_scope_before_reading_private_view(tmp_path, loss):
    async def run():
        async with _native_application(tmp_path) as case:
            access, slots = case.app._access, catalog_access._EXPORT_SLOTS
            entered, release = asyncio.Event(), asyncio.Event()
            original_result, original_render = access.result, access.renderer.render
            queued_reads = []

            async def result(digest, *values, **options):
                if digest == case.digests['analyst']:
                    queued_reads.append(True)
                return await original_result(digest, *values, **options)

            async def render(report_format, data):
                entered.set()
                await release.wait()
                return await original_render(report_format, data)
            access.result, access.renderer.render = result, render
            active = asyncio.create_task(access.create_export(case.digests['owner_a'], case.workspace,
                case.child.record_id, 'json', key_hash='a' * 64))
            await asyncio.wait_for(entered.wait(), 3)
            queued = asyncio.create_task(access.create_export(case.digests['analyst'], case.workspace,
                case.child.record_id, 'json', key_hash='b' * 64))
            try:
                await _queued(slots, 1)
                assert queued_reads == []
                if loss == 'source_grant':
                    await WorkspaceManagement(case.api).set_source_grant(credential_digest=case.digests['owner_a'],
                        workspace_id=case.workspace, source_id=case.source.source_id, user_id='analyst',
                        actions=['read'], expected_revision=1)
                elif loss == 'session':
                    async with case.owner.connection() as conn:
                        await conn.execute("UPDATE j01_sessions SET state='revoked' WHERE context_id='ctx_analyst'")
                else:
                    await PostgresProjectMutations(case.api, access.catalog).delete(credential_digest=case.digests['owner_a'],
                        workspace_id=case.workspace, project_id=case.initial.project_id, expected_revision=3)
                release.set()
                if loss == 'tombstone':
                    with pytest.raises(AuthorizationDenied):
                        await active
                else:
                    await active
                with pytest.raises(AuthorizationDenied):
                    await queued
                assert queued_reads == []
            finally:
                release.set()
                active.cancel()
                queued.cancel()
                await asyncio.gather(active, queued, return_exceptions=True)
            assert slots.snapshot().active == slots.snapshot().waiting == 0
    _run(run())
