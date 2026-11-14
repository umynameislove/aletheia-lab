"""Real admission/cancellation effects, without executor or HTTP test double queues."""
import asyncio
import json
import os
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from test_k02_runtime_lifecycle import _settings

from aletheia_lab.hosted import async_work
from aletheia_lab.hosted.composition import ProcessApplication
from aletheia_lab.hosted.resource_admission import ByteAdmission, ProcessSlots, ResourceBusy
from aletheia_lab.hosted.resource_budget import measured_mean_demand, resource_budget


def test_fifo_overflow_queued_cancel_and_zero_leaked_permits():
    async def run():
        slots, order = ProcessSlots(1, 2), []
        await slots.acquire()

        async def queued(label):
            await slots.acquire()
            try:
                order.append(label)
            finally:
                slots.release()

        cancelled = asyncio.create_task(queued('cancelled'))
        second = asyncio.create_task(queued('second'))
        await asyncio.sleep(0)
        assert slots.snapshot().waiting == 2
        with pytest.raises(ResourceBusy):
            await slots.acquire()
        with pytest.raises(ResourceBusy):
            await slots.acquire(wait=False)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        third = asyncio.create_task(queued('third'))
        await asyncio.sleep(0)
        slots.release()
        await asyncio.gather(second, third)
        assert order == ['second', 'third']
        assert slots.snapshot().active == slots.snapshot().waiting == 0
        assert slots.snapshot().rejected == 2 and slots.snapshot().peak_active == 1
    asyncio.run(run())


def test_cancel_between_grant_and_delivery_returns_the_permit():
    async def run():
        slots = ProcessSlots(1, 1)
        await slots.acquire()
        queued = asyncio.create_task(slots.acquire())
        await asyncio.sleep(0)
        slots.release()
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert slots.snapshot().active == slots.snapshot().waiting == 0
        await slots.acquire(wait=False)
        slots.release()
    asyncio.run(run())


def test_real_thread_cancellation_drains_before_capacity_reuse(monkeypatch):
    slots = ProcessSlots(1, 1)
    monkeypatch.setattr(async_work, '_BLOCKING_SLOTS', slots)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def block():
        entered.set()
        assert release.wait(5)
        calls.append('finished')

    async def run():
        task = asyncio.create_task(async_work.run_blocking(block))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            queued = asyncio.create_task(async_work.run_blocking(lambda: calls.append('queued')))
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            queued.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queued
            assert not task.done() and slots.snapshot().active == 1
            with pytest.raises(ResourceBusy):
                await async_work.run_blocking_nowait(lambda: calls.append('must not start'))
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == ['finished']
        assert slots.snapshot().active == slots.snapshot().waiting == 0
    asyncio.run(run())


def test_fifo_cross_event_loop_handoff():
    slots = ProcessSlots(1, 1)
    waiting, finished = threading.Event(), threading.Event()

    async def other():
        waiting.set()
        await slots.acquire()
        slots.release()
        finished.set()

    async def run():
        await slots.acquire()
        thread = threading.Thread(target=lambda: asyncio.run(other()))
        thread.start()
        assert await asyncio.to_thread(waiting.wait, 2)
        for _ in range(100):
            if slots.snapshot().waiting:
                break
            await asyncio.sleep(.001)
        assert slots.snapshot().waiting == 1
        slots.release()
        assert await asyncio.to_thread(finished.wait, 2)
        await asyncio.to_thread(thread.join, 2)
        assert not thread.is_alive() and slots.snapshot().active == 0
    asyncio.run(run())


def test_http_saturation_keeps_health_lane_and_returns_retry_after(tmp_path):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        @asynccontextmanager
        async def resources():
            raise AssertionError('No resource construction is needed for request admission.')
            yield

        async def backend(scope, receive, send):
            calls.append(scope['path'])
            if scope['path'] == '/work':
                entered.set()
                await release.wait()
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'ok'})

        app = ProcessApplication(_settings(tmp_path, concurrency_limit=2), resources)

        async def request(path, method='GET'):
            messages = []
            async def receive():
                raise AssertionError('No request body may be read.')
            async def send(event):
                messages.append(event)
            await app._http(backend, {'path': path, 'method': method, 'headers': []}, receive, send)
            return messages

        running = asyncio.create_task(request('/work'))
        await entered.wait()
        rejected = await request('/extra', 'HEAD')
        assert rejected[0]['status'] == 503
        assert (b'retry-after', b'1') in rejected[0]['headers']
        assert rejected[1]['body'] == b''
        assert (await request('/health/ready'))[0]['status'] == 200
        assert calls == ['/work', '/health/ready']
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert app.admission.regular.snapshot().active == app.admission.health.snapshot().active == 0
        assert (await request('/extra'))[0]['status'] == 200
    asyncio.run(run())


def test_byte_budget_reserves_before_work_and_never_queues():
    budget = ByteAdmission(400)
    budget.acquire(400)
    with pytest.raises(ResourceBusy):
        budget.acquire(1)
    assert budget.snapshot().active == 400 and budget.snapshot().waiting == 0
    budget.release(400)
    assert budget.snapshot().active == 0 and budget.snapshot().peak_active == 400


def test_fleet_budget_counts_every_process_replica_and_admin_reserve(tmp_path):
    settings = _settings(tmp_path)
    budget = resource_budget(settings, api_replicas=3, worker_replicas=2)
    assert budget['total_database_connections'] == 3 * settings.api.processes * settings.api.database_pool_size + 2 * settings.worker.processes * settings.worker.database_pool_size + settings.storage.admin_connection_reserve
    assert budget['within_database_budget'] is False
    assert budget['blocking_calls_per_process'] == 2 and budget['blocking_call_waiters'] == 32
    estimate = measured_mean_demand(completed_samples=100, arrivals_per_second=20,
        mean_wall_seconds=.2, mean_cpu_seconds=.01, available_cpu_cores=2)
    assert estimate['mean_inflight_littles_law'] == 4 and estimate['mean_cpu_fraction'] == .1


@pytest.mark.parametrize('value', [True, -1, float('nan'), float('inf')])
def test_capacity_estimates_reject_unmeasured_or_invalid_inputs(value):
    with pytest.raises(ValueError):
        measured_mean_demand(completed_samples=1, arrivals_per_second=value,
            mean_wall_seconds=1, mean_cpu_seconds=1, available_cpu_cores=1)


def test_plan_cli_reads_only_toml_and_refuses_an_over_budget_fleet(tmp_path):
    config = tmp_path / 'runtime.toml'
    config.write_text('environment="local"\npublic_origin="https://app.example.invalid"\n'
        '[identity]\nissuer="https://identity.example.invalid"\nclient_id="synthetic"\n'
        'redirect_uri="https://app.example.invalid/callback"\nclient_kind="public"\n'
        '[storage]\ndatabase_url_env="ALETHEIA_HOSTED_DATABASE_URL"\n'
        'object_root=' + json.dumps(str(tmp_path / 'must-not-be-created')) + '\n')
    root = Path(__file__).resolve().parents[2]
    for replicas, expected_code, connections in ((1, 0, 12), (3, 1, 28)):
        result = subprocess.run([sys.executable, str(root / 'scripts/k14_resource_plan.py'),
            '--config', str(config), '--api-replicas', str(replicas)],
            env={**{key: os.environ[key] for key in ('SystemRoot', 'WINDIR') if key in os.environ},
                'PYTHONPATH': str(root / 'src')}, capture_output=True, text=True, timeout=5)
        assert result.returncode == expected_code, result.stderr
        report = json.loads(result.stdout)
        assert report['total_database_connections'] == connections
        assert report['within_database_budget'] is (expected_code == 0)
    assert not (tmp_path / 'must-not-be-created').exists()
