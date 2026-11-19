"""K09 current execution profile on real PostgreSQL, owned UUID schemas only."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr
from test_k05_native_results_postgres import _case, _run
from test_k06_canonical_snapshot_postgres import _setup

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.job_execution import QueueFull, QueuePolicy
from aletheia_lab.hosted.postgres_jobs import JobClaim, JobStateConflict, PostgresJobs
from aletheia_lab.hosted.postgres_sources import SourceNotFound

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K09_TEST_DATABASE_URL'),
    reason='Explicit private K09 PostgreSQL URL required')]


@pytest.fixture(autouse=True)
def configured_database(monkeypatch):
    monkeypatch.setenv('K05_TEST_DATABASE_URL', os.environ['K09_TEST_DATABASE_URL'])


async def _jobs(case, **options):
    return await _setup(case, lambda *args, **kwargs: PostgresJobs(*args, **kwargs, execution=True, **options))


async def _admit(jobs, case, key='k09-accepted-job-key', operation='preview', workspace='a', source=None):
    return await jobs.enqueue(credential_digest=case.a if workspace == 'a' else case.b,
        workspace_id=workspace, operation=operation, request_key=SecretStr(key),
        request={'source_id': source or case.receipt.source_id,
                 **({'mapping': case.snapshot.mapping.model_dump(mode='json')} if operation == 'confirm' else {})})


async def _status(jobs, case, receipt):
    return await jobs.status(credential_digest=case.a, workspace_id='a', job_id=receipt.job_id)


async def _history(jobs, case, receipt):
    return await jobs.attempts(credential_digest=case.a, workspace_id='a', job_id=receipt.job_id)


async def _wait_database(pool, job_id, expression):
    for _ in range(160):
        async with pool.connection() as conn:
            ready = await (await conn.execute('SELECT ' + expression + ' FROM j01_jobs WHERE job_id=%s', (job_id,))).fetchone()
        if ready == (True,):
            return
        await asyncio.sleep(.05)
    raise AssertionError('Database-owned bounded timer did not become due.')


_CLAIM_AND_DIE = r'''
import asyncio,json,os,sys
from pathlib import Path
from pydantic import SecretStr
from psycopg import sql
from psycopg_pool import AsyncConnectionPool
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources
async def run():
    schema,path=sys.argv[1:]
    async def configure(conn):
        await conn.execute(sql.SQL('SET search_path TO {},public').format(sql.Identifier(schema)))
        await conn.commit()
    async with AsyncConnectionPool(os.environ['K09_TEST_DATABASE_URL'],min_size=1,max_size=2,configure=configure,open=False) as pool:
        sources=PostgresSources(pool,maximum_upload_bytes=10000,maximum_expanded_bytes=20000,maximum_entries=10)
        jobs=PostgresJobs(pool,sources,execution=True)
        claim=await jobs.claim(lease_digest=SecretStr('1'*64),lease_seconds=1,operations=('confirm',))
        assert claim is not None
        body={'job_id':claim.job_id,'workspace_id':claim.workspace_id,'operation':claim.operation,
              'request':claim.request,'credential_digest':claim.credential_digest.get_secret_value()}
        target=Path(path);target.write_text(json.dumps(body));target.chmod(0o600)
        os._exit(73)
with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform=='win32' else None) as runner:
    runner.run(run())
'''


def test_ack_process_loss_lease_takeover_stale_fence_and_after_commit_replay(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _jobs(case)
            assert await jobs.is_ready() and await jobs.execution_ready()
            receipt = await _admit(jobs, case, operation='confirm')
            assert (await _status(jobs, case, receipt)).status == 'queued'
            assert (await _history(jobs, case, receipt)).items == []
            async with case.pool.connection() as conn:
                schema = (await (await conn.execute('SELECT current_schema()')).fetchone())[0]
            path = tmp_path / 'lost-claim.json'
            child = await asyncio.to_thread(subprocess.run, [sys.executable, '-B', '-c',
                _CLAIM_AND_DIE, schema, str(path)], capture_output=True, timeout=20)
            assert child.returncode == 73, child.stderr.decode(errors='replace')[-1500:]
            body = json.loads(path.read_text())
            path.unlink()
            body['credential_digest'] = SecretStr(body['credential_digest'])
            stale = JobClaim(**body)
            assert stale.job_id == receipt.job_id
            await _wait_database(case.pool, receipt.job_id, 'lease_until<clock_timestamp()')
            restarted = PostgresJobs(case.pool, case.sources, catalog=case.catalog, audit=True, execution=True)
            lease = SecretStr('2' * 64)
            current = await restarted.claim(lease_digest=lease, lease_seconds=30, operations=('confirm',))
            assert current and current.job_id == receipt.job_id
            assert not await restarted.heartbeat(claim=stale, lease_digest=SecretStr('1' * 64), lease_seconds=30)
            assert not await restarted.complete_snapshot(claim=stale, lease_digest=SecretStr('1' * 64), snapshot=case.snapshot)
            before = await _history(restarted, case, receipt)
            assert [item.outcome for item in before.items] == ['lease_lost', 'running']
            assert await restarted.heartbeat(claim=current, lease_digest=lease, lease_seconds=30)
            assert await restarted.complete_snapshot(claim=current, lease_digest=lease, snapshot=case.snapshot)
            # A lost post-commit response/redelivery must not create another canonical effect.
            assert not await restarted.complete_snapshot(claim=current, lease_digest=lease, snapshot=case.snapshot)
            assert await _admit(restarted, case, operation='confirm') == receipt
            assert (await _status(restarted, case, receipt)).status == 'succeeded'
            history = await _history(restarted, case, receipt)
            assert [item.epoch for item in history.items] == [1, 2]
            assert [item.outcome for item in history.items] == ['lease_lost', 'succeeded']
            assert all(item.started_at and item.heartbeat_at and item.finished_at for item in history.items)
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='upload_snapshot'")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.publish'")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.admit'")).fetchone() == (1,)
    _run(scenario())


@pytest.mark.parametrize('order', ['queued', 'cancel_first', 'commit_first', 'cancel_owner_lost'])
def test_cancel_requested_is_not_cancelled_and_actual_publication_orders(tmp_path, order):
    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _jobs(case)
            receipt = await _admit(jobs, case, operation='confirm')
            lease = SecretStr('3' * 64)
            claim = None if order == 'queued' else await jobs.claim(lease_digest=lease,
                lease_seconds=1 if order == 'cancel_owner_lost' else 30, operations=('confirm',))
            if order == 'commit_first':
                assert await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=case.snapshot)
            cancelled = await jobs.cancel(credential_digest=case.a, workspace_id='a', job_id=receipt.job_id)
            if order == 'queued':
                assert cancelled.status == 'cancelled' and cancelled.terminal_at
                assert (await _history(jobs, case, receipt)).items == []
            elif order == 'commit_first':
                assert cancelled.status == 'succeeded'
                assert not await jobs.acknowledge_cancel(claim=claim, lease_digest=lease)
            else:
                assert cancelled.status == 'cancel_requested' and cancelled.terminal_at is None
                assert (await _history(jobs, case, receipt)).items[-1].outcome == 'cancel_requested'
                assert not await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=case.snapshot)
                if order == 'cancel_owner_lost':
                    await _wait_database(case.pool, receipt.job_id, 'lease_until<clock_timestamp()')
                    assert await jobs.reconcile() == 1
                    assert not await jobs.acknowledge_cancel(claim=claim, lease_digest=lease)
                else:
                    assert await jobs.acknowledge_cancel(claim=claim, lease_digest=lease)
                assert (await _status(jobs, case, receipt)).status == 'cancelled'
                assert (await _history(jobs, case, receipt)).items[-1].outcome == 'cancelled'
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='upload_snapshot'")).fetchone() == (int(order == 'commit_first'),)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.publish'")).fetchone() == (int(order == 'commit_first'),)
    _run(scenario())


def test_transient_retries_have_backoff_same_id_fixed_deadline_and_terminal_exhaustion(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _jobs(case)
            receipt = await _admit(jobs, case)
            deadline = (await _status(jobs, case, receipt)).deadline_at
            lease = SecretStr('4' * 64)
            for epoch in (1, 2, 3):
                claim = await jobs.claim(lease_digest=lease, lease_seconds=30)
                assert claim and claim.job_id == receipt.job_id
                assert (await _status(jobs, case, receipt)).attempt == epoch
                assert await jobs.fail(claim=claim, lease_digest=lease, retryable=True)
                status = await _status(jobs, case, receipt)
                assert status.deadline_at == deadline
                if epoch < 3:
                    assert status.status == 'queued' and status.terminal_at is None
                    assert await jobs.claim(lease_digest=lease, lease_seconds=30) is None
                    await _wait_database(case.pool, receipt.job_id, 'available_at<=clock_timestamp()')
            status = await _status(jobs, case, receipt)
            assert status.status == 'failed' and status.retryable is False and status.terminal_at
            assert await jobs.claim(lease_digest=lease, lease_seconds=30) is None
            with pytest.raises(JobStateConflict):
                await jobs.retry(credential_digest=case.a, workspace_id='a', job_id=receipt.job_id,
                                 request_key=SecretStr('k09-no-reset-attempt-budget'))
            history = await _history(jobs, case, receipt)
            assert [item.outcome for item in history.items] == ['retrying', 'retrying', 'failed']
            assert history.items[-1].failure_kind == 'attempts_exhausted'
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.retrying'")).fetchone() == (2,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.fail'")).fetchone() == (1,)
    _run(scenario())


def test_permanent_failure_and_revoke_cannot_publish_or_gain_retry_budget(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _jobs(case)
            first = await _admit(jobs, case, key='k09-permanent-failure-key')
            lease = SecretStr('5' * 64)
            claim = await jobs.claim(lease_digest=lease, lease_seconds=30)
            assert await jobs.fail(claim=claim, lease_digest=lease)
            assert (await _status(jobs, case, first)).retryable is False
            with pytest.raises(JobStateConflict):
                await jobs.retry(credential_digest=case.a, workspace_id='a', job_id=first.job_id,
                    request_key=SecretStr('k09-permanent-no-new-job'))
            second = await _admit(jobs, case, key='k09-revoke-failure-key')
            claim = await jobs.claim(lease_digest=lease, lease_seconds=30)
            async with case.pool.connection() as conn:
                await conn.execute("UPDATE j01_memberships SET active=false,revision=revision+1 WHERE workspace_id='a' AND user_id='owner_a'")
            assert not await jobs.heartbeat(claim=claim, lease_digest=lease, lease_seconds=30)
            with pytest.raises((SourceNotFound, AuthorizationDenied)):
                await _history(jobs, case, second)
            with pytest.raises((SourceNotFound, AuthorizationDenied)):
                await jobs.complete(claim=claim, lease_digest=lease, result={'private': 'never-published'})
            assert await jobs.fail(claim=claim, lease_digest=lease, failure_kind='authorization')
            async with case.pool.connection() as conn:
                assert await (await conn.execute('SELECT state,failure_kind,result FROM j01_jobs WHERE job_id=%s', (second.job_id,))).fetchone() == ('failed', 'authorization', None)
    _run(scenario())


def test_manual_retry_inherits_original_deadline_and_consumed_attempts(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _jobs(case)
            first = await _admit(jobs, case)
            lease = SecretStr('6' * 64)
            claim = await jobs.claim(lease_digest=lease, lease_seconds=30)
            deadline = (await _status(jobs, case, first)).deadline_at
            await jobs.cancel(credential_digest=case.a, workspace_id='a', job_id=first.job_id)
            assert await jobs.acknowledge_cancel(claim=claim, lease_digest=lease)
            retry = await jobs.retry(credential_digest=case.a, workspace_id='a', job_id=first.job_id,
                                    request_key=SecretStr('k09-manual-preserves-budget'))
            assert retry.job_id != first.job_id
            assert (await _status(jobs, case, first)).retryable is False
            assert await jobs.retry(credential_digest=case.a, workspace_id='a', job_id=first.job_id,
                request_key=SecretStr('k09-manual-preserves-budget')) == retry
            with pytest.raises(JobStateConflict):
                await jobs.retry(credential_digest=case.a, workspace_id='a', job_id=first.job_id,
                    request_key=SecretStr('k09-manual-cannot-fan-out'))
            pending = await _status(jobs, case, retry)
            assert pending.deadline_at == deadline and pending.attempt == 1
            claim = await jobs.claim(lease_digest=lease, lease_seconds=30)
            assert claim and claim.job_id == retry.job_id
            status = await _status(jobs, case, retry)
            assert status.deadline_at == deadline and status.attempt == 2
            page = await _history(jobs, case, retry)
            assert page.retry_of_job_id == first.job_id and [item.epoch for item in page.items] == [2]
            # Deadline is an immutable original budget, never extended to legalize a late result.
            import psycopg
            with pytest.raises(psycopg.errors.RaiseException):
                async with case.pool.connection() as conn:
                    await conn.execute("UPDATE j01_jobs SET deadline_at=deadline_at+interval '1 hour' WHERE job_id=%s", (retry.job_id,))
    _run(scenario())


def test_real_deadline_expires_and_blocks_publication_with_a_live_lease(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _jobs(case)
            receipt = await _admit(jobs, case, operation='confirm')
            lease = SecretStr('7' * 64)
            claim = await jobs.claim(lease_digest=lease, lease_seconds=30, operations=('confirm',))
            async with case.pool.connection() as conn:
                await conn.execute("UPDATE j01_jobs SET deadline_at=clock_timestamp()+interval '150 milliseconds' WHERE job_id=%s", (receipt.job_id,))
            await _wait_database(case.pool, receipt.job_id, 'deadline_at<clock_timestamp()')
            assert not await jobs.heartbeat(claim=claim, lease_digest=lease, lease_seconds=30)
            assert not await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=case.snapshot)
            assert await jobs.reconcile() == 1
            status = await _status(jobs, case, receipt)
            assert status.status == 'expired' and status.terminal_at and status.retryable is False
            history = await _history(jobs, case, receipt)
            assert history.items[-1].outcome == 'expired' and history.items[-1].failure_kind == 'deadline'
            async with case.pool.connection() as conn:
                assert await (await conn.execute('SELECT count(*) FROM k05_records')).fetchone() == (0,)
    _run(scenario())


def test_tenant_and_class_fairness_caps_and_full_queue_replay(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            policy = QueuePolicy(pending_per_workspace=4, pending_per_class=3,
                                 running_per_workspace=2, running_per_class=1)
            jobs = await _jobs(case, queue_policy=policy)
            first = await _admit(jobs, case, key='k09-fair-a-preview-0')
            await _admit(jobs, case, key='k09-fair-a-preview-1')
            await _admit(jobs, case, key='k09-fair-a-preview-2')
            with pytest.raises(QueueFull):
                await _admit(jobs, case, key='k09-fair-a-preview-overflow')
            await _admit(jobs, case, key='k09-fair-a-confirm', operation='confirm')
            with pytest.raises(QueueFull):
                await _admit(jobs, case, key='k09-fair-a-total-overflow', operation='confirm')
            assert await _admit(jobs, case, key='k09-fair-a-preview-0') == first
            source_b = await case.sources.put(credential_digest=case.b, workspace_id='b',
                request_key=SecretStr('k09-fair-tenant-b-source'), payload=case.payload)
            await _admit(jobs, case, key='k09-fair-b-preview', workspace='b', source=source_b.source_id)
            a = await jobs.claim(lease_digest=SecretStr('8' * 64), lease_seconds=30)
            b = await jobs.claim(lease_digest=SecretStr('9' * 64), lease_seconds=30)
            other_class = await jobs.claim(lease_digest=SecretStr('a' * 64), lease_seconds=30)
            assert a and a.workspace_id == 'a' and a.operation == 'preview'
            assert b and b.workspace_id == 'b'
            assert other_class and other_class.workspace_id == 'a' and other_class.operation == 'confirm'
            assert await jobs.claim(lease_digest=SecretStr('b' * 64), lease_seconds=30) is None
            async with case.pool.connection() as conn:
                assert await (await conn.execute('SELECT count(*) FROM j01_jobs')).fetchone() == (5,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.admit'")).fetchone() == (5,)
    _run(scenario())


def test_parallel_admission_and_claim_do_not_overbook_queue_or_live_class(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            policy = QueuePolicy(pending_per_workspace=4, pending_per_class=4)
            jobs = await _jobs(case, queue_policy=policy)
            same = await asyncio.gather(*(_admit(jobs, case) for _ in range(12)))
            assert len({item.job_id for item in same}) == 1
            candidates = await asyncio.gather(*(_admit(jobs, case, key=f'k09-parallel-distinct-{index:02}')
                for index in range(12)), return_exceptions=True)
            assert sum(not isinstance(item, Exception) for item in candidates) == 3
            assert sum(isinstance(item, QueueFull) for item in candidates) == 9
            claims = await asyncio.gather(*(jobs.claim(lease_digest=SecretStr(f'{index:064x}'), lease_seconds=30)
                                           for index in range(12)))
            assert sum(item is not None for item in claims) == 1
            async with case.pool.connection() as conn:
                assert await (await conn.execute('SELECT count(*) FROM j01_jobs')).fetchone() == (4,)
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs WHERE state='running'")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.admit'")).fetchone() == (4,)
    _run(scenario())


def test_cancellation_reaps_real_isolated_child_before_terminal_ack(tmp_path):
    from aletheia_lab.hosted.upload_job_worker import run_one_upload_job
    from aletheia_lab.hosted.upload_operations import UploadOperations
    from aletheia_lab.hosted.windows_worker import WindowsUploadWorker
    from aletheia_lab.hosted.worker_supervisor import UploadWorkerSupervisor

    async def scenario():
        async with _case(tmp_path) as case:
            child_root = tmp_path / 'controlled-child'
            child_root.mkdir()
            (child_root / 'k09_waiting_worker.py').write_text(
                'import sys,time\nsys.stdin.buffer.read()\nwhile True: time.sleep(.1)\n')
            started = asyncio.Event()
            test_loop = asyncio.get_running_loop()

            class Supervisor(UploadWorkerSupervisor):
                process = None
                def _command(self, report_format):
                    return 'k09_waiting_worker', 'controlled-compute'
                async def _communicate(self, process, payload):
                    self.process = process
                    test_loop.call_soon_threadsafe(started.set)
                    return await super()._communicate(process, payload)

            worker_args = dict(python=Path(sys.executable).absolute(), source_root=child_root,
                private_root=tmp_path, slots=1, timeout_seconds=15)
            supervisor = Supervisor(**worker_args)
            worker = supervisor
            if sys.platform == 'win32':
                worker = WindowsUploadWorker(**worker_args)
                worker._worker = supervisor

            class ReapCheckedJobs(PostgresJobs):
                async def acknowledge_cancel(self, **kwargs):
                    assert supervisor.process is not None and supervisor.process.returncode is not None
                    return await super().acknowledge_cancel(**kwargs)

            jobs = await _setup(case, lambda *args, **kwargs: ReapCheckedJobs(*args, **kwargs, execution=True))
            receipt = await _admit(jobs, case)
            operations = UploadOperations(case.sources, worker, private_root=tmp_path)
            task = asyncio.create_task(run_one_upload_job(jobs, operations, lease_seconds=2))
            try:
                await asyncio.wait_for(started.wait(), 5)
                # Confirm at least one persisted live heartbeat before requesting cancellation.
                for _ in range(60):
                    page = await _history(jobs, case, receipt)
                    if page.items[0].heartbeat_at > page.items[0].started_at:
                        break
                    await asyncio.sleep(.025)
                assert page.items[0].heartbeat_at > page.items[0].started_at
                assert supervisor.process.returncode is None
                requested = await jobs.cancel(credential_digest=case.a, workspace_id='a', job_id=receipt.job_id)
                assert requested.status == 'cancel_requested'
                assert await asyncio.wait_for(task, 5)
                assert supervisor.process.returncode is not None
                assert (await _status(jobs, case, receipt)).status == 'cancelled'
                assert (await _history(jobs, case, receipt)).items[-1].outcome == 'cancelled'
                async with case.pool.connection() as conn:
                    assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.cancelled'")).fetchone() == (1,)
                    assert await (await conn.execute("SELECT count(*) FROM j01_jobs WHERE result IS NOT NULL")).fetchone() == (0,)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await worker.aclose()
    _run(scenario())


def test_current_api_worker_roles_can_use_attempt_triggers_without_history_rewrite(tmp_path):
    import secrets
    from contextlib import asynccontextmanager
    from uuid import uuid4

    import psycopg
    from psycopg import sql
    from test_k06_runtime_privileges_postgres import _role_url

    from aletheia_lab.hosted.postgres_sources import PostgresSources
    from aletheia_lab.hosted.runtime_privileges import grant_runtime

    class RolePool:
        def __init__(self, url):
            self.url = url
        @asynccontextmanager
        async def connection(self):
            async with await psycopg.AsyncConnection.connect(self.url) as conn:
                yield conn

    async def scenario():
        created = []
        suffix = uuid4().hex
        try:
            async with _case(tmp_path) as case:
                await _jobs(case)
                async with case.pool.connection() as conn:
                    schema = (await (await conn.execute('SELECT current_schema()')).fetchone())[0]
                    for profile in ('api', 'worker'):
                        role, password = 'k09_' + profile + '_' + suffix, secrets.token_urlsafe(32)
                        await conn.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
                            'NOINHERIT NOBYPASSRLS PASSWORD {}').format(sql.Identifier(role), sql.Literal(password)))
                        created.append((profile, role, password))
                        await grant_runtime(conn, schema, role, profile=profile, audit=True)
                pools = {profile: RolePool(_role_url(os.environ['K09_TEST_DATABASE_URL'], schema, role, password))
                         for profile, role, password in created}
                jobs = {}
                for profile, pool in pools.items():
                    sources = PostgresSources(pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000, maximum_entries=10)
                    jobs[profile] = PostgresJobs(pool, sources, execution=True, audit=True)
                    assert await jobs[profile].execution_ready()
                api, worker = jobs['api'], jobs['worker']
                receipt = await _admit(api, case)
                lease = SecretStr('c' * 64)
                claim = await worker.claim(lease_digest=lease, lease_seconds=30)
                assert claim and await worker.heartbeat(claim=claim, lease_digest=lease, lease_seconds=30)
                assert (await api.cancel(credential_digest=case.a, workspace_id='a', job_id=receipt.job_id)).status == 'cancel_requested'
                assert await worker.acknowledge_cancel(claim=claim, lease_digest=lease)
                assert (await _history(api, case, receipt)).items[-1].outcome == 'cancelled'
                retry = await api.retry(credential_digest=case.a, workspace_id='a', job_id=receipt.job_id,
                                       request_key=SecretStr('k09-role-manual-retry-budget'))
                claim = await worker.claim(lease_digest=lease, lease_seconds=30)
                assert claim and claim.job_id == retry.job_id
                assert await worker.fail(claim=claim, lease_digest=lease, retryable=True)
                assert (await _history(api, case, retry)).items[-1].outcome == 'retrying'
                # Runtime may append/finish attempts via trusted code but cannot change finished history.
                with pytest.raises(psycopg.errors.RaiseException):
                    async with pools['worker'].connection() as conn:
                        await conn.execute("UPDATE k09_job_attempts SET outcome='failed' WHERE job_id=%s", (receipt.job_id,))
                for pool in pools.values():
                    with pytest.raises(psycopg.errors.InsufficientPrivilege):
                        async with pool.connection() as conn:
                            await conn.execute('DELETE FROM k09_job_attempts')
        finally:
            async with await psycopg.AsyncConnection.connect(os.environ['K09_TEST_DATABASE_URL'], autocommit=True) as admin:
                for profile, role, _password in created:
                    assert role == 'k09_' + profile + '_' + suffix
                    await admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
    _run(scenario())



def test_cancelled_job_aged_past_original_deadline_does_not_advertise_retry(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _jobs(case)
            receipt = await _admit(jobs, case)
            async with case.pool.connection() as conn:
                await conn.execute("UPDATE j01_jobs SET deadline_at=clock_timestamp()+interval '200 milliseconds' WHERE job_id=%s", (receipt.job_id,))
            before = await jobs.cancel(credential_digest=case.a, workspace_id='a', job_id=receipt.job_id)
            assert before.status == 'cancelled' and before.retryable is True
            await _wait_database(case.pool, receipt.job_id, 'deadline_at<clock_timestamp()')
            after = await _status(jobs, case, receipt)
            assert after.status == 'cancelled' and after.retryable is False
            with pytest.raises(JobStateConflict):
                await jobs.retry(credential_digest=case.a, workspace_id='a', job_id=receipt.job_id,
                    request_key=SecretStr('k09-cancel-no-deadline-reset'))
    _run(scenario())


def test_analysis_receipt_replays_across_defaults_and_publication_keeps_admitted_model(tmp_path):
    from aletheia_lab.hosted.native_upload_records import build_initial_upload_result
    from aletheia_lab.hosted.postgres_sources import SourceConflict
    from aletheia_lab.hosted.upload_analysis import analyze_upload_metrics

    async def scenario():
        async with _case(tmp_path) as case:
            old = await _jobs(case)
            await _admit(old, case, operation='confirm')
            lease = SecretStr('d' * 64)
            claim = await old.claim(lease_digest=lease, lease_seconds=30, operations=('confirm',))
            assert await old.complete_snapshot(claim=claim, lease_digest=lease, snapshot=case.snapshot)
            new = PostgresJobs(case.pool, case.sources, catalog=case.catalog, audit=True, execution=True,
                              analysis_model='upload-snapshot-metric-observation/v2')
            request = {'snapshot_id': case.snapshot.snapshot_id, 'question': case.view.conversation.turns[0].question}
            args = dict(credential_digest=case.a, workspace_id='a', operation='analyze', request=request)
            first = await old.enqueue(**args, request_key=SecretStr('k09-analysis-before-upgrade'))
            assert await new.enqueue(**args, request_key=SecretStr('k09-analysis-before-upgrade')) == first
            old_claim = await new.claim(lease_digest=lease, lease_seconds=30, operations=('analyze',))
            assert old_claim and 'analysis_model' not in old_claim.request
            assert await new.complete_result(claim=old_claim, lease_digest=lease, record=case.initial)
            second = await new.enqueue(**args, request_key=SecretStr('k09-analysis-after-upgrade'))
            assert await old.enqueue(**args, request_key=SecretStr('k09-analysis-after-upgrade')) == second
            pinned = await old.claim(lease_digest=lease, lease_seconds=30, operations=('analyze',))
            assert pinned and pinned.request['analysis_model'] == 'upload-snapshot-metric-observation/v2'
            with pytest.raises(ValueError, match='does not match'):
                await old.complete_result(claim=pinned, lease_digest=lease, record=case.initial)
            view = analyze_upload_metrics(case.snapshot, request['question'])
            current = build_initial_upload_result(case.snapshot, request['question'], view)
            assert await old.complete_result(claim=pinned, lease_digest=lease, record=current)
            output = await new.output(credential_digest=case.a, workspace_id='a', job_id=second.job_id)
            assert output['result']['runtime']['model'] == pinned.request['analysis_model']
            with pytest.raises(SourceConflict):
                await new.enqueue(**{**args, 'request': dict(request, question='A different question?')},
                    request_key=SecretStr('k09-analysis-after-upgrade'))
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs WHERE operation='analyze'")).fetchone() == (2,)
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='upload_result'")).fetchone() == (2,)
    _run(scenario())


def test_implicit_model_receipt_race_keeps_insert_winner_without_queue_lock(tmp_path):
    """Two supported legacy compatibility instances can still race at INSERT."""
    async def scenario():
        async with _case(tmp_path) as case:
            current = await _jobs(case)
            await _admit(current, case, operation='confirm')
            lease = SecretStr('e' * 64)
            claim = await current.claim(lease_digest=lease, lease_seconds=30, operations=('confirm',))
            assert await current.complete_snapshot(claim=claim, lease_digest=lease, snapshot=case.snapshot)
            # Deliberately disable execution-profile queue locking to exercise ON CONFLICT replay.
            old = PostgresJobs(case.pool, case.sources, execution=False)
            new = PostgresJobs(case.pool, case.sources, execution=False,
                               analysis_model='upload-snapshot-metric-observation/v2')
            args = dict(credential_digest=case.a, workspace_id='a', operation='analyze',
                request_key=SecretStr('k09-racing-model-defaults'),
                request={'snapshot_id': case.snapshot.snapshot_id, 'question': 'What is observed?'})
            receipts = await asyncio.gather(*((old if index % 2 else new).enqueue(**args) for index in range(12)))
            assert all(item == receipts[0] for item in receipts)
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs WHERE operation='analyze'")).fetchone() == (1,)
    _run(scenario())


def test_attempt_pulse_changes_only_on_worker_claim_or_renewal_not_cancel_or_terminal(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _jobs(case)
            receipt = await _admit(jobs, case)
            lease = SecretStr('f' * 64)
            claim = await jobs.claim(lease_digest=lease, lease_seconds=30)
            initial = (await _history(jobs, case, receipt)).items[-1]
            assert initial.started_at == initial.heartbeat_at
            assert await jobs.heartbeat(claim=claim, lease_digest=lease, lease_seconds=30)
            renewed = (await _history(jobs, case, receipt)).items[-1]
            assert renewed.heartbeat_at > initial.heartbeat_at
            await jobs.cancel(credential_digest=case.a, workspace_id='a', job_id=receipt.job_id)
            requested = (await _history(jobs, case, receipt)).items[-1]
            assert requested.outcome == 'cancel_requested'
            assert requested.heartbeat_at == renewed.heartbeat_at and requested.lease_until == renewed.lease_until
            assert await jobs.acknowledge_cancel(claim=claim, lease_digest=lease)
            terminal = (await _history(jobs, case, receipt)).items[-1]
            assert terminal.outcome == 'cancelled' and terminal.finished_at >= terminal.heartbeat_at
            assert terminal.heartbeat_at == renewed.heartbeat_at
    _run(scenario())


def test_successful_retried_attempt_does_not_inherit_prior_transient_failure_kind(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _jobs(case)
            receipt = await _admit(jobs, case, operation='confirm')
            first_lease, second_lease = SecretStr('1' * 64), SecretStr('2' * 64)
            first = await jobs.claim(lease_digest=first_lease, lease_seconds=30, operations=('confirm',))
            assert await jobs.fail(claim=first, lease_digest=first_lease, retryable=True)
            earlier = (await _history(jobs, case, receipt)).items[-1]
            assert earlier.outcome == 'retrying' and earlier.failure_kind == 'transient'
            await _wait_database(case.pool, receipt.job_id, 'available_at<=clock_timestamp()')
            second = await jobs.claim(lease_digest=second_lease, lease_seconds=30, operations=('confirm',))
            assert second and second.job_id == first.job_id
            async with case.pool.connection() as conn:
                assert await (await conn.execute('SELECT failure_kind FROM j01_jobs WHERE job_id=%s', (receipt.job_id,))).fetchone() == (None,)
            assert await jobs.complete_snapshot(claim=second, lease_digest=second_lease, snapshot=case.snapshot)
            history = await _history(jobs, case, receipt)
            assert [item.outcome for item in history.items] == ['retrying', 'succeeded']
            assert [item.failure_kind for item in history.items] == ['transient', None]
            assert (await _status(jobs, case, receipt)).status == 'succeeded'
    _run(scenario())
