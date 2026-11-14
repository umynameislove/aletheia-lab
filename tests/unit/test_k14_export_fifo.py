"""Export admission queues only authorized scalar intent before loading views."""
import asyncio
import hashlib
import threading
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from pydantic import SecretStr
from test_k02_durable_exports import RESULT, Harness

from aletheia_lab.hosted import catalog_access
from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.request_boundary import RequestRejected
from aletheia_lab.hosted.resource_admission import ProcessSlots, ResourceBusy

DIGEST = SecretStr('a' * 64)


@pytest.fixture
def slots(monkeypatch):
    value = ProcessSlots(1, 4)
    monkeypatch.setattr(catalog_access, '_EXPORT_SLOTS', value)
    return value


def _observe(harness):
    reads, connections = [], {'active': 0}
    original = harness.access.result
    async def result(*args, **options):
        reads.append(asyncio.current_task().get_name())
        return await original(*args, **options)
    harness.access.result = result
    connection = harness.pool.connection
    @asynccontextmanager
    async def observed():
        async with connection() as conn:
            connections['active'] += 1
            try:
                yield conn
            finally:
                connections['active'] -= 1
    harness.pool.connection = observed
    return reads, connections


def test_process_fifo_bounds_four_scalar_waiters_no_payload_or_connection_and_cancel_removes_waiter(tmp_path, slots):
    async def run():
        harness = Harness(tmp_path)
        reads, connections = _observe(harness)
        entered, release = asyncio.Event(), asyncio.Event()
        order = []
        async def render(report_format, data):
            order.append(asyncio.current_task().get_name())
            entered.set()
            await release.wait()
            return b'synthetic bounded report'
        harness.renderer.render = render
        async def export(index):
            return await harness.access.create_export(DIGEST, 'ws1', RESULT, 'json', key_hash=f'{index:064x}')
        tasks = [asyncio.create_task(export(0), name='export0')]
        try:
            await entered.wait()
            tasks += [asyncio.create_task(export(index), name='export' + str(index)) for index in range(1, 5)]
            await asyncio.sleep(.01)
            assert slots.snapshot().active == 1 and slots.snapshot().waiting == 4
            assert reads == order == ['export0'] and connections['active'] == 0
            harness.state.export_grant = False
            with pytest.raises(AuthorizationDenied):
                await export(99)
            assert slots.snapshot().rejected == 0
            harness.state.export_grant = True
            with pytest.raises(ResourceBusy):
                await export(5)
            assert reads == ['export0'] and connections['active'] == 0
            tasks[2].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[2]
            assert slots.snapshot().waiting == 3
            release.set()
            completed = await asyncio.gather(tasks[0], tasks[1], tasks[3], tasks[4])
            assert len(completed) == 4 and order == ['export0', 'export1', 'export3', 'export4']
            assert reads == order
        finally:
            release.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert slots.snapshot().active == slots.snapshot().waiting == connections['active'] == 0
    asyncio.run(run())


def test_authorized_committed_receipt_replays_while_full_without_view_or_renderer_and_keeps_ttl_conflict(tmp_path, slots):
    async def run():
        harness = Harness(tmp_path)
        key = 'a' * 64
        existing = await harness.access.create_export(DIGEST, 'ws1', RESULT, 'json', key_hash=key)
        reads, connections = _observe(harness)
        calls = harness.renderer.calls
        await slots.acquire()
        waiters = [asyncio.create_task(slots.acquire()) for _ in range(4)]
        await asyncio.sleep(0)
        try:
            assert await harness.access.create_export(DIGEST, 'ws1', RESULT, 'json', key_hash=key) == existing
            assert reads == [] and harness.renderer.calls == calls and connections['active'] == 0
            with pytest.raises(RequestRejected) as conflict:
                await harness.access.create_export(DIGEST, 'ws1', RESULT, 'markdown', key_hash=key)
            assert conflict.value.status == 409
            harness.state.export_grant = False
            with pytest.raises(AuthorizationDenied):
                await harness.access.create_export(DIGEST, 'ws1', RESULT, 'json', key_hash=key)
            harness.state.export_grant = True
            harness.state.now += timedelta(minutes=16)
            with pytest.raises(RequestRejected) as expired:
                await harness.access.create_export(DIGEST, 'ws1', RESULT, 'json', key_hash=key)
            assert expired.value.status == 410 and reads == []
        finally:
            for task in waiters:
                task.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)
            slots.release()
        assert slots.snapshot().active == slots.snapshot().waiting == 0
    asyncio.run(run())


def test_queued_permission_loss_is_rechecked_before_any_view_or_render(tmp_path, slots):
    async def run():
        first, second = Harness(tmp_path / 'first'), Harness(tmp_path / 'second')
        reads, connections = _observe(second)
        entered, release = asyncio.Event(), asyncio.Event()
        async def render(report_format, data):
            entered.set()
            await release.wait()
            return b'first report'
        first.renderer.render = render
        current = asyncio.create_task(first.access.create_export(DIGEST, 'ws1', RESULT, 'json'))
        await entered.wait()
        queued = asyncio.create_task(second.access.create_export(DIGEST, 'ws1', RESULT, 'json'))
        try:
            await asyncio.sleep(.01)
            assert slots.snapshot().waiting == 1 and reads == [] and connections['active'] == 0
            second.state.export_grant = False
            release.set()
            await current
            with pytest.raises(AuthorizationDenied):
                await queued
            assert reads == [] and second.renderer.calls == 0
        finally:
            release.set()
            current.cancel()
            queued.cancel()
            await asyncio.gather(current, queued, return_exceptions=True)
        assert slots.snapshot().active == slots.snapshot().waiting == 0
    asyncio.run(run())


def test_queue_deadline_cancellation_never_loads_view_or_starts_renderer(tmp_path, slots):
    async def run():
        harness = Harness(tmp_path)
        reads, connections = _observe(harness)
        await slots.acquire()
        try:
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(.02):
                    await harness.access.create_export(DIGEST, 'ws1', RESULT, 'json')
            assert reads == [] and harness.renderer.calls == 0 and connections['active'] == 0
            assert slots.snapshot().active == 1 and slots.snapshot().waiting == 0
        finally:
            slots.release()
    asyncio.run(run())


@pytest.mark.parametrize('workspace,result', [('foreign', RESULT), ('ws1', 'missing')])
def test_unknown_or_foreign_export_scope_consumes_no_permit(tmp_path, slots, workspace, result):
    async def run():
        harness = Harness(tmp_path)
        reads, connections = _observe(harness)
        with pytest.raises(AuthorizationDenied):
            await harness.access.create_export(DIGEST, workspace, result, 'json')
        assert slots.snapshot().peak_active == 0 and reads == [] and connections['active'] == 0
        assert harness.renderer.calls == 0 and not harness.state.exports
    asyncio.run(run())


@pytest.mark.integration
def test_cancelled_actual_report_child_retains_export_slot_until_reaped_before_queued_render(tmp_path, monkeypatch, slots):
    from aletheia_lab.hosted.report_renderer import ReportRenderer
    from aletheia_lab.product.reports import render_product_report

    children = []
    spawn = asyncio.create_subprocess_exec
    async def observed_spawn(*args, **options):
        child = await spawn(*args, **options)
        children.append(child)
        return child
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', observed_spawn)

    async def run():
        harness = Harness(tmp_path)
        reads, connections = _observe(harness)
        renderer = ReportRenderer(slots=1, timeout_seconds=5)
        harness.access.renderer = renderer
        started, cleanup_entered, cleanup_release = threading.Event(), threading.Event(), threading.Event()
        communicate, terminate = renderer._worker._communicate, renderer._worker._terminate

        async def held_communicate(process, payload):
            started.set()
            await asyncio.Event().wait()

        async def held_terminate(process):
            cleanup_entered.set()
            while not cleanup_release.is_set():
                await asyncio.sleep(.005)
            await terminate(process)

        monkeypatch.setattr(renderer._worker, '_communicate', held_communicate)
        monkeypatch.setattr(renderer._worker, '_terminate', held_terminate)
        current = asyncio.create_task(harness.access.create_export(DIGEST, 'ws1', RESULT, 'json', key_hash='1' * 64), name='cancelled-render')
        queued = None
        try:
            async with asyncio.timeout(3):
                while not started.is_set():
                    await asyncio.sleep(.005)
            current.cancel()
            async with asyncio.timeout(3):
                while not cleanup_entered.is_set():
                    await asyncio.sleep(.005)
            current.cancel()
            queued = asyncio.create_task(harness.access.create_export(DIGEST, 'ws1', RESULT, 'json', key_hash='2' * 64), name='queued-render')
            await asyncio.sleep(.02)
            assert not current.done() and children[0].returncode is None
            assert slots.snapshot().active == slots.snapshot().waiting == 1
            assert reads == ['cancelled-render'] and connections['active'] == 0
            monkeypatch.setattr(renderer._worker, '_communicate', communicate)
            cleanup_release.set()
            with pytest.raises(asyncio.CancelledError):
                await current
            item = await asyncio.wait_for(queued, 5)
            expected = render_product_report(harness.view, 'json')
            assert harness.objects.store.read(item.descriptor) == expected
            assert item.descriptor.sha256 == hashlib.sha256(expected).hexdigest()
            assert len(children) == 2 and all(child.returncode is not None for child in children)
        finally:
            cleanup_release.set()
            current.cancel()
            if queued is not None:
                queued.cancel()
            await asyncio.gather(current, *([queued] if queued is not None else []), return_exceptions=True)
            await renderer.aclose()
        assert slots.snapshot().active == slots.snapshot().waiting == 0
        assert not renderer._worker._tasks
    asyncio.run(run())
