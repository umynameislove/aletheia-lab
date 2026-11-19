"""Durable snapshot versions and current revocation on owned native PG schemas."""
import asyncio
import json
import os
import random
import sys
import time
from contextlib import asynccontextmanager

import pytest
from pydantic import SecretStr
from test_k05_native_results_postgres import _case, _run
from test_k06_canonical_snapshot_postgres import _setup

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.cursors import CursorInvalid, ScopedCursors
from aletheia_lab.hosted.job_polling import reconnect_delay_ms
from aletheia_lab.hosted.job_updates import PostgresJobUpdates
from aletheia_lab.hosted.poll_admission import PollAdmission
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.resource_admission import ResourceBusy

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K15_TEST_DATABASE_URL'),
    reason='Explicit private K15 PostgreSQL URL required')]


@pytest.fixture(autouse=True)
def database(monkeypatch):
    monkeypatch.setenv('K05_TEST_DATABASE_URL', os.environ['K15_TEST_DATABASE_URL'])


async def _updates(case):
    jobs = await _setup(case, lambda *args, **kwargs: PostgresJobs(*args, **kwargs, execution=True))
    updates = PostgresJobUpdates(jobs, ScopedCursors(SecretStr('synthetic-k15-key-' + 'x' * 43)), random_source=lambda: 0)
    assert await updates.ready()
    receipt = await jobs.enqueue(credential_digest=case.a, workspace_id='a', operation='confirm',
        request_key=SecretStr('synthetic-k15-job-key'), request={'source_id': case.receipt.source_id,
            'mapping': case.snapshot.mapping.model_dump(mode='json')})
    return jobs, updates, receipt


async def _read(updates, case, receipt, cursor=None, credential=None, workspace='a'):
    return await updates.read(credential_digest=case.a if credential is None else credential,
        workspace_id=workspace, job_id=receipt.job_id, cursor=cursor)


def test_heartbeat_stable_revision_terminal_gap_resync_and_restart_dedup(tmp_path):
    async def run():
        async with _case(tmp_path) as case:
            jobs, updates, receipt = await _updates(case)
            queued = await _read(updates, case, receipt)
            assert queued.revision == 1 and queued.job.status == 'queued'
            assert (await _read(updates, case, receipt, queued.cursor)).disposition == 'unchanged'
            lease = SecretStr('a' * 64)
            claim = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=('confirm',))
            assert claim is not None
            running = await _read(updates, case, receipt, queued.cursor)
            assert running.job.status == 'running' and running.revision == 2 and running.job.attempt == 1
            assert await jobs.heartbeat(claim=claim, lease_digest=lease, lease_seconds=120)
            unchanged = await _read(updates, case, receipt, running.cursor)
            assert unchanged.revision == running.revision and unchanged.disposition == 'unchanged'
            assert await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=case.snapshot)
            finished = await _read(updates, case, receipt, queued.cursor)
            assert finished.revision == 3 and finished.disposition == 'resync'
            assert finished.job.status == 'succeeded' and finished.poll_after_ms is None
            assert finished.job.terminal_at is not None and finished.job.retryable is None
            assert not await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=case.snapshot)
            restarted = PostgresJobUpdates(jobs, ScopedCursors(SecretStr('synthetic-k15-key-' + 'x' * 43)))
            assert (await _read(restarted, case, receipt, finished.cursor)).disposition == 'unchanged'
            async with case.pool.connection() as conn:
                assert await (await conn.execute('SELECT status_revision FROM j01_jobs WHERE job_id=%s', (receipt.job_id,))).fetchone() == (3,)
                assert await (await conn.execute('SELECT count(*) FROM k05_records WHERE record_type=\'upload_snapshot\'')).fetchone() == (1,)
    _run(run())


@pytest.mark.parametrize('denial', ['session', 'membership', 'account', 'workspace', 'source_grant'])
def test_next_read_checks_current_authority_before_cursor_or_metadata(tmp_path, denial):
    async def run():
        async with _case(tmp_path) as case:
            _, updates, receipt = await _updates(case)
            first = await _read(updates, case, receipt)
            async with case.pool.connection() as conn:
                if denial == 'session':
                    await conn.execute("UPDATE j01_sessions SET state='revoked' WHERE context_id='ctx_a'")
                elif denial == 'membership':
                    await conn.execute("UPDATE j01_memberships SET active=false,revision=revision+1 WHERE workspace_id='a' AND user_id='owner_a'")
                elif denial == 'account':
                    await conn.execute("UPDATE hosted_identity_bindings SET enabled=false WHERE user_id='owner_a'")
                elif denial == 'source_grant':
                    await conn.execute("UPDATE j01_memberships SET role='viewer',revision=revision+1 WHERE workspace_id='a' AND user_id='owner_a'")
                    await conn.execute("UPDATE j01_sources SET creator_id='owner_b' WHERE source_id=%s", (case.receipt.source_id,))
            with pytest.raises(AuthorizationDenied):
                await _read(updates, case, receipt, first.cursor, workspace='b' if denial == 'workspace' else 'a')
    _run(run())


def test_current_member_session_scope_and_authority_revision_resync(tmp_path):
    async def run():
        async with _case(tmp_path) as case:
            _, updates, receipt = await _updates(case)
            first = await _read(updates, case, receipt)
            # Another authorized owner cannot reuse this actor's signed cursor.
            with pytest.raises(CursorInvalid):
                await _read(updates, case, receipt, first.cursor, credential=case.b)
            assert (await _read(updates, case, receipt, credential=case.b)).job.job_id == receipt.job_id
            async with case.pool.connection() as conn:
                await conn.execute("UPDATE j01_memberships SET revision=revision+1 WHERE workspace_id='a' AND user_id='owner_a'")
            with pytest.raises(CursorInvalid):
                await _read(updates, case, receipt, first.cursor)
            fresh = await _read(updates, case, receipt)
            async with case.pool.connection() as conn:
                await conn.execute("INSERT INTO k10_catalog_revisions(workspace_id,revision) VALUES('a',1) "
                    "ON CONFLICT(workspace_id) DO UPDATE SET revision=k10_catalog_revisions.revision+1")
            resync = await _read(updates, case, receipt, fresh.cursor)
            assert resync.disposition == 'resync' and resync.revision == fresh.revision
    _run(run())


def test_readiness_detects_revision_trigger_disabled(tmp_path):
    async def run():
        async with _case(tmp_path) as case:
            _, updates, _ = await _updates(case)
            async with case.pool.connection() as conn:
                await conn.execute('ALTER TABLE j01_jobs DISABLE TRIGGER k15_job_status_revision')
            assert not await updates.ready()
    _run(run())


def test_native_100_pending_jobs_30_percent_reconnect_cost_probe(tmp_path):
    """Local adapter cost only: 13 tenants, default queue and poll admission limits."""
    async def run():
        async with _case(tmp_path) as case:
            jobs, _, _ = await _updates(case)
            scopes = ['poll' + str(index) for index in range(13)]
            async with case.pool.connection() as conn:
                for index, scope in enumerate(scopes):
                    await conn.execute("INSERT INTO j01_workspaces(workspace_id,name) VALUES(%s,'Synthetic poll probe')", (scope,))
                    await conn.execute("INSERT INTO j01_memberships(workspace_id,user_id,role,active,revision) VALUES(%s,'owner_a','owner',true,1)", (scope,))
                    await conn.execute("INSERT INTO j01_sources(source_id,workspace_id,creator_id,request_key_hash,sha256,payload,manifest) "
                        "SELECT %s,%s,'owner_a',request_key_hash,sha256,payload,manifest FROM j01_sources WHERE source_id=%s",
                        ('src_poll_' + str(index), scope, case.receipt.source_id))
            receipts = []
            for index in range(100):
                scope = scopes[index // 8]
                receipt = await jobs.enqueue(credential_digest=case.a, workspace_id=scope,
                    operation='preview', request_key=SecretStr('synthetic-poll-probe-' + str(index)),
                    request={'source_id': 'src_poll_' + str(index // 8)})
                receipts.append(receipt)
            query_count = active_connections = peak_connections = 0
            connection_waits, latencies, bodies = [], [], []

            class ObservedPool:
                @asynccontextmanager
                async def connection(self):
                    nonlocal active_connections, peak_connections
                    waiting = time.monotonic()
                    async with case.pool.connection() as actual:
                        connection_waits.append(time.monotonic() - waiting)
                        active_connections += 1
                        peak_connections = max(peak_connections, active_connections)
                        class Connection:
                            autocommit = False
                            async def execute(self, query, params=(), **options):
                                nonlocal query_count
                                query_count += 1
                                return await actual.execute(query, params, **options)
                        try:
                            yield Connection()
                        finally:
                            active_connections -= 1

            jobs._pool = ObservedPool()
            rng = random.Random(15100)
            updates = PostgresJobUpdates(jobs, ScopedCursors(SecretStr('synthetic-probe-signing-' + 'x' * 43)), random_source=rng.random)
            polls, rejected = PollAdmission(), 0

            async def poll(receipt, delay, cursor=None):
                nonlocal rejected
                await asyncio.sleep(delay)
                started = time.monotonic()
                try:
                    polls.acquire(receipt.workspace_id)
                except ResourceBusy:
                    rejected += 1
                    return None
                try:
                    update = await updates.read(credential_digest=case.a, workspace_id=receipt.workspace_id,
                        job_id=receipt.job_id, cursor=cursor)
                    bodies.append(len(update.model_dump_json().encode()))
                    latencies.append(time.monotonic() - started)
                    return update
                finally:
                    polls.release(receipt.workspace_id)

            started, cpu = time.monotonic(), time.process_time()
            baseline = await asyncio.gather(*(poll(receipt, rng.random() * 2) for receipt in receipts))
            assert all(item is not None and item.job.status == 'queued' for item in baseline)
            selected = rng.sample(range(100), 30)
            reconnect = await asyncio.gather(*(poll(receipts[index], reconnect_delay_ms(0, rng.random()) / 1000,
                baseline[index].cursor) for index in selected))
            elapsed, cpu_seconds = time.monotonic() - started, time.process_time() - cpu
            assert all(item is not None and item.disposition == 'unchanged' for item in reconnect)
            assert rejected == 0 and peak_connections <= 4 and active_connections == polls.active_workspaces == 0
            def percentile(values, fraction):
                return sorted(values)[min(len(values) - 1, int(len(values) * fraction))]
            try:
                import resource
            except ImportError:
                peak_rss = None
            else:
                peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
            report = {'schema_version': 'k15-native-poll-probe/v1', 'pending_jobs': 100, 'workspaces': 13,
                'reconnect_fraction': .3, 'polls_succeeded': len(latencies), 'polls_rejected': rejected,
                'duration_seconds': elapsed, 'requests_per_second_observed': len(latencies) / elapsed,
                'sql_statements': query_count, 'sql_per_poll': query_count / len(latencies),
                'sql_per_second_observed': query_count / elapsed,
                'connection_peak': peak_connections, 'pool_max_connections': 4,
                'connection_wait_p95_ms': percentile(connection_waits, .95) * 1000,
                'latency_p95_ms': percentile(latencies, .95) * 1000,
                'latency_max_ms': max(latencies) * 1000, 'response_bytes_total': sum(bodies),
                'response_bytes_max': max(bodies), 'process_cpu_seconds': cpu_seconds,
                'process_peak_rss_bytes': peak_rss,
                'scope': 'One Python test process and native-loopback PostgreSQL; adapter + actual poll admission; no HTTP/TLS/browser or multi-replica capacity claim. 100 distinct pending jobs across 13 tenants preserve default per-class queue limit 8. Fixture seed and job admission excluded from cost interval; peak RSS includes fixture. 50k gate remains open.'}
            print('K15_POLL_PROBE ' + json.dumps(report, sort_keys=True))
    _run(run())
