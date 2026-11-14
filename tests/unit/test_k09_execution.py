"""Execution policy and real task cancellation, without a database substitute."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from aletheia_lab.hosted import upload_job_worker
from aletheia_lab.hosted.job_execution import QueuePolicy
from aletheia_lab.hosted.postgres_jobs import JobClaim
from aletheia_lab.hosted.worker_supervisor import WorkerRejected
from aletheia_lab.hosted.workspace_management import WorkspaceConflict
from aletheia_lab.hosted_contract.job_attempts import JobAttemptPage


def _claim():
    return JobClaim('job1', 'ws1', 'preview', {'source_id': 'source1'}, SecretStr('a' * 64))


class Jobs:
    execution = True

    def __init__(self):
        self.beats = 0
        self.acknowledged = False
        self.failed = []

    async def claim(self, **kwargs):
        return _claim()

    async def heartbeat(self, **kwargs):
        self.beats += 1
        return True

    async def fail(self, **kwargs):
        self.failed.append(kwargs)
        return True

    async def acknowledge_cancel(self, **kwargs):
        self.acknowledged = True
        return True


def test_running_compute_renews_lease_and_only_returns_actual_result(monkeypatch):
    async def scenario():
        jobs = Jobs()
        async def compute(*args):
            await asyncio.sleep(0.045)
            return True
        monkeypatch.setattr(upload_job_worker, '_run_claim', compute)
        assert await upload_job_worker._guard_claim(jobs, None, _claim(), SecretStr('b' * 64), .03, None)
        assert jobs.beats >= 2 and not jobs.acknowledged and jobs.failed == []
    asyncio.run(scenario())


def test_cancel_request_waits_for_compute_cleanup_before_cancelled_ack(monkeypatch):
    async def scenario():
        jobs = Jobs()
        cleaning, release = asyncio.Event(), asyncio.Event()
        async def no_lease(**kwargs):
            return False
        jobs.heartbeat = no_lease
        async def compute(*args):
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                await release.wait()
        monkeypatch.setattr(upload_job_worker, '_run_claim', compute)
        task = asyncio.create_task(upload_job_worker._guard_claim(jobs, None, _claim(), SecretStr('b' * 64), .03, None))
        await asyncio.wait_for(cleaning.wait(), 1)
        assert not jobs.acknowledged and not task.done()
        release.set()
        assert await task and jobs.acknowledged and jobs.failed == []
    asyncio.run(scenario())


def test_repeated_scheduler_cancellation_reaps_child_without_terminal_ack(monkeypatch):
    async def scenario():
        jobs = Jobs()
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def compute(*args):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                await release.wait()
        monkeypatch.setattr(upload_job_worker, '_run_claim', compute)
        task = asyncio.create_task(upload_job_worker._guard_claim(jobs, None, _claim(), SecretStr('b' * 64), 30, None))
        await entered.wait()
        task.cancel()
        await cleaning.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not jobs.acknowledged
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert jobs.failed == [] and not jobs.acknowledged
    asyncio.run(scenario())


@pytest.mark.parametrize(('error', 'retryable'), [
    (TimeoutError('private timeout'), True), (WorkerRejected('worker_busy'), True),
    (WorkerRejected('worker_timeout'), True), (WorkerRejected('worker_input_too_large'), False),
    (ValueError('invalid immutable input'), False),
    (WorkspaceConflict(), False),
])
def test_execution_classifies_bounded_retry_without_exposing_error(monkeypatch, error, retryable):
    async def compute(*args):
        raise error
    monkeypatch.setattr(upload_job_worker, '_run_claim', compute)
    jobs = Jobs()
    assert asyncio.run(upload_job_worker.run_one_upload_job(jobs, None, lease_seconds=10))
    assert len(jobs.failed) == 1
    assert jobs.failed[0].get('retryable', False) is retryable
    if isinstance(error, WorkspaceConflict):
        assert jobs.failed[0]['failure_kind'] == 'conflict'
    assert set(jobs.failed[0]) <= {'claim', 'lease_digest', 'retryable'} | (
        {'failure_kind'} if isinstance(error, WorkspaceConflict) else set())


@pytest.mark.parametrize('options', [
    {'pending_per_workspace': True}, {'pending_per_class': 0}, {'running_per_workspace': 10001},
    {'pending_per_workspace': 2, 'pending_per_class': 3},
    {'running_per_workspace': 1, 'running_per_class': 2},
])
def test_queue_policy_rejects_invalid_or_inconsistent_bounds(options):
    with pytest.raises(ValueError):
        QueuePolicy(**options)


def test_attempt_page_rejects_duplicate_epochs_false_terminal_and_time_travel():
    now = datetime.now(UTC)
    item = {'epoch': 1, 'started_at': now.isoformat(), 'heartbeat_at': now.isoformat(),
            'lease_until': (now + timedelta(seconds=10)).isoformat(), 'finished_at': None,
            'outcome': 'running', 'failure_kind': None}
    page = {'schema_version': 'product-api/v1-draft', 'workspace_id': 'ws1', 'job_id': 'job1',
            'retry_of_job_id': None, 'items': [item]}
    assert JobAttemptPage.model_validate_json(json.dumps(page)).items[0].epoch == 1
    for items in ([item, item], [dict(item, outcome='succeeded')],
                  [dict(item, heartbeat_at=(now - timedelta(seconds=1)).isoformat())],
                  [item, dict(item, epoch=2)]):
        with pytest.raises(ValidationError):
            JobAttemptPage.model_validate_json(json.dumps(dict(page, items=items)))


@pytest.mark.parametrize('change', [{}, {'current_revision': -1}, {'current_revision': True},
                                   {'project_id': 'other-project'}])
def test_conflict_attempt_requires_safe_current_project_metadata(change):
    now = datetime.now(UTC).isoformat()
    item = dict(epoch=1, started_at=now, heartbeat_at=now, lease_until=None,
                finished_at=now, outcome='failed', failure_kind='conflict')
    conflict = dict(project_id='p3-project-' + 'a' * 64, current_revision=3)
    conflict.update(change)
    page = dict(schema_version='product-api/v1-draft', workspace_id='ws1', job_id='job1',
                retry_of_job_id=None, items=[item], conflict=conflict)
    if change:
        with pytest.raises(ValidationError):
            JobAttemptPage.model_validate_json(json.dumps(page))
    else:
        assert JobAttemptPage.model_validate_json(json.dumps(page)).conflict.current_revision == 3
        with pytest.raises(ValidationError):
            JobAttemptPage.model_validate_json(json.dumps(dict(page, conflict=None)))
        with pytest.raises(ValidationError):
            JobAttemptPage.model_validate_json(json.dumps(dict(page, items=[dict(item, outcome='succeeded')])))


def test_managed_worker_infers_interpreter_without_losing_current_virtual_environment(tmp_path, monkeypatch):
    """A symlink's parent selects the venv; resolving it silently drops deps."""
    import subprocess
    from types import SimpleNamespace

    from test_k03_worker_binding import _case

    from aletheia_lab.hosted import managed_worker

    case = _case(tmp_path, monkeypatch, environment="local")
    factory = managed_worker.PayloadUploadWorkerSupervisor
    captured = {}
    def supervisor(**options):
        captured.update(options)
        return factory(**options)
    monkeypatch.setattr(managed_worker, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
    monkeypatch.setattr(managed_worker, "PayloadUploadWorkerSupervisor", supervisor)
    asyncio.run(managed_worker.run_worker(case.environment, asyncio.Event()))
    process = subprocess.run([str(captured["python"]), '-X', 'utf8', '-B', '-c',
        'import sys,pydantic;print(sys.prefix)'], capture_output=True, check=True, text=True, encoding='utf-8')
    assert Path(process.stdout.strip()) == Path(sys.prefix)
    assert Path(sys.executable).absolute().parent == Path(sys.executable).parent.absolute()


@pytest.mark.parametrize('change', [{}, {'workspace_id': 'foreign'}, {'items': [{'epoch': 999}]}])
def test_attempt_http_boundary_is_scoped_and_fail_closed(tmp_path, change):
    from test_k02_job_boundary import PATH, _request, _system
    app, jobs, headers = _system(tmp_path)
    page = JobAttemptPage(schema_version='product-api/v1-draft', workspace_id='ws_boundary',
        job_id='job_boundary', retry_of_job_id=None, items=[])
    async def attempts(**kwargs):
        assert kwargs['workspace_id'] == 'ws_boundary' and kwargs['job_id'] == 'job_boundary'
        return page.model_copy(update=change)
    jobs.attempts = attempts
    status, response_headers, body = _request(app, headers, path=PATH + '/job_boundary/attempts')
    assert status == (500 if change else 200)
    assert b'foreign' not in body
    assert response_headers[b'cache-control'] == b'no-store'
    if not change:
        assert json.loads(body)['items'] == []
        status, head_headers, body = _request(app, headers, method='HEAD', path=PATH + '/job_boundary/attempts')
        assert status == 200 and body == b'' and int(head_headers[b'content-length']) > 0
    status, headers, body = _request(app, headers, method='POST', path=PATH + '/job_boundary/attempts')
    assert status == 405 and headers[b'allow'] == b'GET, HEAD'


@pytest.mark.parametrize('heartbeat_error', [False, True])
@pytest.mark.parametrize('compute_error', [False, True])
def test_guard_preserves_settled_compute_outcome_during_heartbeat_race(monkeypatch, heartbeat_error, compute_error):
    async def scenario():
        jobs = Jobs()
        release, settled = asyncio.Event(), asyncio.Event()
        async def compute(*args):
            await release.wait()
            try:
                if compute_error:
                    raise ValueError('permanent compute failure')
                return True
            finally:
                settled.set()
        async def heartbeat(**kwargs):
            release.set()
            await settled.wait()
            await asyncio.sleep(0)
            if heartbeat_error:
                raise TimeoutError('heartbeat unavailable')
            return False
        jobs.heartbeat = heartbeat
        monkeypatch.setattr(upload_job_worker, '_run_claim', compute)
        if compute_error:
            with pytest.raises(ValueError, match='permanent compute failure'):
                await upload_job_worker._guard_claim(jobs, None, _claim(), SecretStr('b' * 64), .03, None)
        else:
            assert await upload_job_worker._guard_claim(jobs, None, _claim(), SecretStr('b' * 64), .03, None)
        assert not jobs.acknowledged and jobs.failed == []
    asyncio.run(scenario())


def test_blocking_work_cancellation_waits_for_actual_thread_with_repeated_cancel():
    import threading

    from aletheia_lab.hosted.async_work import run_blocking

    async def scenario():
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def operation(value, *, increment):
            entered.set()
            assert release.wait(5)
            finished.set()
            return value + increment
        task = asyncio.create_task(run_blocking(operation, 1, increment=2))
        await asyncio.to_thread(entered.wait)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
        assert await run_blocking(lambda value, *, increment: value + increment, 2, increment=3) == 5
    asyncio.run(scenario())


def test_finished_fenced_compute_acknowledges_pending_cancel_without_waiting_for_lease(monkeypatch):
    async def compute(*args):
        return False
    monkeypatch.setattr(upload_job_worker, '_run_claim', compute)
    jobs = Jobs()
    assert asyncio.run(upload_job_worker._guard_claim(jobs, None, _claim(), SecretStr('b' * 64), 120, None))
    assert jobs.acknowledged and jobs.failed == []
