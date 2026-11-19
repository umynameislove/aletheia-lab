"""Current-profile upload generations atomically publish comparisons and parent scopes."""
from __future__ import annotations

import asyncio
import io
import os
import zipfile
from copy import copy

import pytest
from pydantic import SecretStr
from test_k02_refresh_postgres import _changed_metrics, _prepare
from test_k05_native_results_postgres import _case, _run
from test_k06_canonical_snapshot_postgres import _setup
from test_k06_refresh_races_postgres import _PublicationBarrier

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.job_execution import QueuePolicy
from aletheia_lab.hosted.native_upload_records import build_initial_upload_result
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.upload_analysis import analyze_upload_metrics
from aletheia_lab.hosted.upload_comparison import (
    UploadGenerationComparison,
    build_upload_comparison,
)
from aletheia_lab.hosted.workspace_management import WorkspaceConflict

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K08_TEST_DATABASE_URL'),
    reason='Explicit private K08 PostgreSQL URL required')]


@pytest.fixture(autouse=True)
def configured_database(monkeypatch):
    monkeypatch.setenv('K05_TEST_DATABASE_URL', os.environ['K08_TEST_DATABASE_URL'])


async def _pipeline(case, **options):
    jobs = await _setup(case, lambda *args, **kwargs: PostgresJobs(*args, **kwargs, execution=True,
        analysis_model='upload-snapshot-metric-observation/v2', **options))
    receipt = await jobs.enqueue(credential_digest=case.a, workspace_id='a', operation='confirm',
        request_key=SecretStr('k08-generation-initial-confirm'), request={'source_id': case.receipt.source_id,
            'mapping': case.snapshot.mapping.model_dump(mode='json')})
    lease = SecretStr('a' * 64)
    claim = await jobs.claim(lease_digest=lease, lease_seconds=30, operations=('confirm',))
    assert claim and claim.job_id == receipt.job_id
    assert await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=case.snapshot)
    view = analyze_upload_metrics(case.snapshot, 'What is observed in this exact uploaded source?')
    initial = build_initial_upload_result(case.snapshot, view.conversation.turns[0].question, view)
    analysis = await jobs.enqueue_analysis(credential_digest=case.a, workspace_id='a',
        project_id=case.snapshot.mapping.project_id, snapshot_id=case.snapshot.snapshot_id,
        question=view.conversation.turns[0].question, request_key=SecretStr('k08-generation-initial-analysis'))
    claim = await jobs.claim(lease_digest=lease, lease_seconds=30, operations=('analyze',))
    assert claim and claim.job_id == analysis.job_id
    assert await jobs.complete_result(claim=claim, lease_digest=lease, record=initial)
    case.current_initial = initial
    return jobs


async def _source_b(case):
    return await case.sources.put(credential_digest=case.a, workspace_id='a',
        request_key=SecretStr('k08-generation-source-b'), payload=_changed_metrics(case.payload))


async def _state(case):
    async with case.pool.connection() as conn:
        revision = await (await conn.execute('SELECT revision FROM k05_projects')).fetchall()
        pointer = await (await conn.execute("SELECT record_id,revision FROM k05_current_pointers WHERE pointer_name='snapshot'")).fetchall()
        records = await (await conn.execute('SELECT record_id,record_type FROM k05_records ORDER BY record_id')).fetchall()
        projections = await (await conn.execute('SELECT snapshot_id,canonical_record_id FROM j01_upload_snapshots ORDER BY snapshot_id')).fetchall()
        audit = await (await conn.execute("SELECT action,job_id,effect_id FROM k06_action_audit WHERE action='job.publish' ORDER BY job_id")).fetchall()
        parents = await (await conn.execute('SELECT record_id,relation,parent_id FROM k05_record_parents ORDER BY record_id,relation,parent_id')).fetchall()
        invalidation = await (await conn.execute('SELECT revision FROM k10_catalog_revisions')).fetchall()
    return revision, pointer, records, projections, audit, parents, invalidation


async def _comparisons(case, credential=None):
    page = await case.catalog.list_records(credential_digest=credential or case.a, workspace_id='a',
        project_id=case.snapshot.mapping.project_id, record_type='snapshot_comparison')
    return [await case.catalog.read_record(credential_digest=credential or case.a, workspace_id='a',
        project_id=case.snapshot.mapping.project_id, record_id=row.record_id) for row in page.records]


async def _grant_reader(case, sources, *, role='viewer', write=False):
    actions = ['read', 'write'] if write else ['read']
    async with case.pool.connection() as conn:
        await conn.execute("UPDATE j01_memberships SET role=%s,revision=revision+1 WHERE workspace_id='a' AND user_id='owner_b'", (role,))
        await conn.execute("INSERT INTO j01_project_grants VALUES('a',%s,'owner_b',%s,0)",
            (case.snapshot.mapping.project_id, actions))
        for source in sources:
            await conn.execute("INSERT INTO j01_source_grants VALUES('a',%s,'owner_b',%s,0)", (source, actions))


async def _revoke_source(case, source):
    async with case.pool.connection() as conn:
        await conn.execute("UPDATE j01_source_grants SET actions='{}',revision=revision+1 "
            "WHERE workspace_id='a' AND source_id=%s AND user_id='owner_b'", (source,))


def test_confirm_analyze_changed_refresh_noop_and_return_generation_keep_complete_census(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _pipeline(case)
            source = await _source_b(case)
            private = tmp_path / 'generation-worker'
            private.mkdir()
            receipt, claim, lease, changed = await _prepare(case, jobs, private, source=source.source_id,
                revision=2, key='k08-generation-changed-refresh')
            assert await jobs.complete_refresh(claim=claim, lease_digest=lease, snapshot=changed.snapshot)
            output = await jobs.output(credential_digest=case.a, workspace_id='a', job_id=receipt.job_id)
            assert output['result']['status'] == 'new_snapshot' and output['result']['revision'] == 3
            comparisons = await _comparisons(case)
            assert len(comparisons) == 1
            comparison = comparisons[0]
            assert isinstance(comparison, UploadGenerationComparison)
            assert comparison == build_upload_comparison(case.snapshot, changed.snapshot)
            assert comparison.before_rows == comparison.after_rows == 2
            by_run = {item.run_id: item for item in comparison.series}
            assert by_run['baseline'].paired == by_run['baseline'].unchanged == 1
            assert by_run['candidate'].paired == by_run['candidate'].increased == by_run['candidate'].adverse == 1
            assert by_run['candidate'].minimum_delta == by_run['candidate'].maximum_delta == '0.2'
            assert comparison.causal_status == 'unverified'
            assert (comparison.before_source_sha256, comparison.after_source_sha256) == (case.snapshot.source_sha256, changed.snapshot.source_sha256)
            state = await _state(case)
            noop, claim, lease, same = await _prepare(case, jobs, private, source=source.source_id,
                revision=3, key='k08-generation-noop-refresh')
            assert await jobs.complete_refresh(claim=claim, lease_digest=lease, snapshot=same.snapshot)
            noop_output = await jobs.output(credential_digest=case.a, workspace_id='a', job_id=noop.job_id)
            assert noop_output['result']['status'] == 'unchanged' and noop_output['result']['revision'] == 3
            after_noop = await _state(case)
            assert after_noop[:4] == state[:4] and after_noop[5:] == state[5:]
            assert len(after_noop[4]) == len(state[4]) + 1  # Terminal job audit only, no generation change.
            back, claim, lease, restored = await _prepare(case, jobs, private, source=case.receipt.source_id,
                revision=3, key='k08-generation-return-to-a')
            assert restored.snapshot.snapshot_id == case.snapshot.snapshot_id
            assert await jobs.complete_refresh(claim=claim, lease_digest=lease, snapshot=restored.snapshot)
            assert (await jobs.output(credential_digest=case.a, workspace_id='a', job_id=back.job_id))['result']['revision'] == 4
            comparisons = await _comparisons(case)
            assert len(comparisons) == 2
            reverse = next(c for c in comparisons if c.after_snapshot_id == case.snapshot.snapshot_id)
            assert reverse.after_record_sha256 == case.snapshot.record_sha256
            assert reverse == build_upload_comparison(changed.snapshot, case.snapshot)
            assert await case.catalog.read_record(credential_digest=case.a, workspace_id='a',
                project_id=case.snapshot.mapping.project_id, record_id=case.current_initial.record_id) == case.current_initial
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='upload_snapshot'")).fetchone() == (2,)
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='snapshot_comparison'")).fetchone() == (2,)
                assert await (await conn.execute("SELECT count(*) FROM k05_record_parents WHERE relation IN ('before_snapshot','after_snapshot')")).fetchone() == (4,)
    _run(scenario())


def test_refresh_fault_after_job_audit_rolls_back_comparison_snapshot_pointer_and_terminal(tmp_path):
    class Fault(PostgresJobs):
        async def _record_action(self, conn, workspace, job, action, **kwargs):
            await super()._record_action(conn, workspace, job, action, **kwargs)
            if action == 'job.publish':
                raise RuntimeError('controlled failure after refresh terminal audit')

    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _pipeline(case)
            source = await _source_b(case)
            private = tmp_path / 'rollback-worker'
            private.mkdir()
            receipt, claim, lease, changed = await _prepare(case, jobs, private, source=source.source_id,
                revision=2, key='k08-generation-rollback-refresh')
            before = await _state(case)
            faulty = Fault(case.pool, case.sources, catalog=case.catalog, audit=True, execution=True)
            with pytest.raises(RuntimeError, match='controlled failure after refresh'):
                await faulty.complete_refresh(claim=claim, lease_digest=lease, snapshot=changed.snapshot)
            assert await _state(case) == before
            status = await jobs.status(credential_digest=case.a, workspace_id='a', job_id=receipt.job_id)
            assert status.status == 'running' and status.terminal_at is None
            assert await jobs.complete_refresh(claim=claim, lease_digest=lease, snapshot=changed.snapshot)
            assert len(await _comparisons(case)) == 1
            with pytest.raises(WorkspaceConflict):
                await jobs.complete_refresh(claim=claim, lease_digest=lease, snapshot=changed.snapshot)
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.publish' AND job_id=%s", (receipt.job_id,))).fetchone() == (1,)
    _run(scenario())


@pytest.mark.parametrize('which', ['before', 'after'])
def test_generation_comparison_read_requires_both_parent_source_grants(tmp_path, which):
    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _pipeline(case)
            source = await _source_b(case)
            private = tmp_path / 'reader-worker'
            private.mkdir()
            _, claim, lease, changed = await _prepare(case, jobs, private, source=source.source_id,
                revision=2, key='k08-generation-reader-refresh')
            assert await jobs.complete_refresh(claim=claim, lease_digest=lease, snapshot=changed.snapshot)
            comparison = (await _comparisons(case))[0]
            await _grant_reader(case, [case.receipt.source_id, source.source_id])
            assert await _comparisons(case, case.b) == [comparison]
            await _revoke_source(case, case.receipt.source_id if which == 'before' else source.source_id)
            with pytest.raises(AuthorizationDenied):
                await case.catalog.read_record(credential_digest=case.b, workspace_id='a',
                    project_id=case.snapshot.mapping.project_id, record_id=comparison.comparison_id)
            assert await _comparisons(case, case.b) == []
            page = await case.catalog.list_workspace_records(credential_digest=case.b, workspace_id='a', kind='snapshot_comparison', limit=1)
            assert page.records == () and page.next_cursor is None
            with pytest.raises(AuthorizationDenied):
                await case.catalog.check_records(credential_digest=case.b, workspace_id='a', record_ids=(comparison.comparison_id,))
    _run(scenario())


@pytest.mark.parametrize('which', ['before', 'after'])
def test_source_revoke_after_prepare_fences_refresh_comparison_publication(tmp_path, which):
    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _pipeline(case)
            source = await _source_b(case)
            await _grant_reader(case, [case.receipt.source_id, source.source_id], role='analyst', write=True)
            # Prepare as the explicitly granted analyst, not the owner bypass.
            owner = case.a
            case.a = case.b
            private = tmp_path / 'revoked-worker'
            private.mkdir()
            receipt, claim, lease, changed = await _prepare(case, jobs, private, source=source.source_id,
                revision=2, key='k08-generation-revoked-refresh')
            case.a = owner
            before = await _state(case)
            await _revoke_source(case, case.receipt.source_id if which == 'before' else source.source_id)
            with pytest.raises(AuthorizationDenied):
                await jobs.complete_refresh(claim=claim, lease_digest=lease, snapshot=changed.snapshot)
            assert await _state(case) == before
            async with case.pool.connection() as conn:
                assert await (await conn.execute('SELECT state,result FROM j01_jobs WHERE job_id=%s', (receipt.job_id,))).fetchone() == ('running', None)
    _run(scenario())


def _source_c(payload):
    stream = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(payload)) as before, zipfile.ZipFile(stream, 'w') as after:
        for info in before.infolist():
            data = before.read(info.filename)
            if info.filename == 'metrics.csv':
                data = data.replace(b'candidate,loss,0.6', b'candidate,loss,0.9')
            after.writestr(copy(info), data)
    return stream.getvalue()


def test_comparison_source_policy_filters_before_limit_with_hidden_first_record(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _pipeline(case)
            source_b = await _source_b(case)
            source_c = await case.sources.put(credential_digest=case.a, workspace_id='a',
                request_key=SecretStr('k08-generation-source-c'), payload=_source_c(case.payload))
            private = tmp_path / 'pagination-worker'
            private.mkdir()
            _, claim, lease, b = await _prepare(case, jobs, private, source=source_b.source_id,
                revision=2, key='k08-generation-page-a-to-b')
            assert await jobs.complete_refresh(claim=claim, lease_digest=lease, snapshot=b.snapshot)
            _, claim, lease, c = await _prepare(case, jobs, private, source=source_c.source_id,
                revision=3, key='k08-generation-page-b-to-c')
            assert await jobs.complete_refresh(claim=claim, lease_digest=lease, snapshot=c.snapshot)
            comparisons = sorted(await _comparisons(case), key=lambda value: value.comparison_id)
            assert len(comparisons) == 2
            hidden, visible = comparisons
            snapshots = {s.snapshot_id: s for s in (case.snapshot, b.snapshot, c.snapshot)}
            granted = [snapshots[identity].source_id for identity in (visible.before_snapshot_id, visible.after_snapshot_id)]
            assert not {snapshots[identity].source_id for identity in (hidden.before_snapshot_id, hidden.after_snapshot_id)} <= set(granted)
            await _grant_reader(case, granted)
            assert await _comparisons(case, case.b) == [visible]
            for page in (await case.catalog.list_records(credential_digest=case.b, workspace_id='a',
                    project_id=case.snapshot.mapping.project_id, record_type='snapshot_comparison', limit=1),
                    await case.catalog.list_workspace_records(credential_digest=case.b, workspace_id='a', kind='snapshot_comparison', limit=1)):
                assert [row.record_id for row in page.records] == [visible.comparison_id]
                assert page.next_cursor is None
            with pytest.raises(AuthorizationDenied):
                await case.catalog.read_record(credential_digest=case.b, workspace_id='a',
                    project_id=case.snapshot.mapping.project_id, record_id=hidden.comparison_id)
    _run(scenario())


def test_two_current_refresh_publications_at_one_cas_keep_one_comparison_and_effect(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _pipeline(case, queue_policy=QueuePolicy(running_per_workspace=2, running_per_class=2))
            source = await _source_b(case)
            private = tmp_path / 'racing-worker'
            private.mkdir()
            work = [await _prepare(case, jobs, private, source=source.source_id,
                revision=2, key=f'k08-generation-racing-{index}') for index in range(2)]
            barrier = _PublicationBarrier(case.pool)
            competing = PostgresJobs(barrier, case.sources, catalog=case.catalog, audit=True, execution=True)
            outcomes = await asyncio.wait_for(asyncio.gather(*(
                competing.complete_refresh(claim=claim, lease_digest=lease, snapshot=prepared.snapshot)
                for _, claim, lease, prepared in work), return_exceptions=True), 15)
            assert sum(value is True for value in outcomes) == 1, outcomes
            assert sum(isinstance(value, WorkspaceConflict) for value in outcomes) == 1, outcomes
            assert len(barrier.backend_pids) == 2
            assert len(await _comparisons(case)) == 1
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='upload_snapshot'")).fetchone() == (2,)
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='snapshot_comparison'")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs WHERE operation='refresh' AND state='succeeded'")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.publish'")).fetchone() == (3,)
    _run(scenario())


def test_snapshot_decode_runs_off_loop_without_pool_and_rechecks_revoke_after_thread(tmp_path, monkeypatch):
    import threading

    from test_k06_source_snapshot_postgres import CountedPool

    from aletheia_lab.hosted.postgres_sources import PostgresSources
    from aletheia_lab.hosted.upload_snapshot import parse_upload_snapshot

    async def scenario():
        async with _case(tmp_path) as case:
            await _pipeline(case)
            counted = CountedPool(case.pool)
            sources = PostgresSources(counted, maximum_upload_bytes=10000, maximum_expanded_bytes=20000,
                                      maximum_entries=10, catalog=case.catalog)
            main_thread = threading.get_ident()
            entered, release = threading.Event(), threading.Event()
            def decode(value):
                assert threading.get_ident() != main_thread and counted.active == 0
                entered.set()
                assert release.wait(5)
                return parse_upload_snapshot(value)
            monkeypatch.setattr('aletheia_lab.hosted.postgres_sources.parse_upload_snapshot', decode)
            task = asyncio.create_task(sources.snapshot(credential_digest=case.a, workspace_id='a',
                                                        snapshot_id=case.snapshot.snapshot_id))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                assert counted.active == 0
                # The loop and PostgreSQL writer remain usable while decoding is held.
                async with case.pool.connection() as conn:
                    await conn.execute("UPDATE j01_memberships SET active=false,revision=revision+1 "
                                       "WHERE workspace_id='a' AND user_id='owner_a'")
                release.set()
                with pytest.raises(AuthorizationDenied):
                    await asyncio.wait_for(task, 5)
                assert counted.active == 0
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    _run(scenario())


def test_analysis_admission_is_metadata_only_and_worker_still_rejects_corrupt_canonical_parent(tmp_path, monkeypatch):
    import sys
    from pathlib import Path

    from aletheia_lab.hosted.payload_upload_worker import PayloadUploadWorkerSupervisor
    from aletheia_lab.hosted.project_operations import CanonicalProjectOperations
    from aletheia_lab.hosted.upload_job_worker import run_one_upload_job

    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _pipeline(case)
            original_read = case.objects.read
            object_reads = 0
            async def forbidden_read(*args, **kwargs):
                nonlocal object_reads
                object_reads += 1
                raise AssertionError('Heavy canonical object reads belong in the worker, after durable admission.')
            monkeypatch.setattr(case.objects, 'read', forbidden_read)
            args = dict(workspace_id='a', project_id=case.snapshot.mapping.project_id,
                        snapshot_id=case.snapshot.snapshot_id, question='What is observed under this new analysis intent?')
            with pytest.raises(AuthorizationDenied):
                await jobs.enqueue_analysis(credential_digest=case.a, **{**args, 'project_id': 'p3-project-' + 'f' * 64},
                    request_key=SecretStr('k08-ack-wrong-path-project'))
            await _grant_reader(case, [case.receipt.source_id])
            with pytest.raises(AuthorizationDenied):
                await jobs.enqueue_analysis(credential_digest=case.b, **args,
                    request_key=SecretStr('k08-ack-viewer-cannot-analyze'))
            async with case.pool.connection() as conn:
                await conn.execute("UPDATE j01_memberships SET role='analyst',revision=revision+1 "
                                   "WHERE workspace_id='a' AND user_id='owner_b'")
                await conn.execute("UPDATE j01_project_grants SET actions=ARRAY['read','write'],revision=revision+1 "
                                   "WHERE workspace_id='a' AND user_id='owner_b'")
            with pytest.raises(AuthorizationDenied):
                await jobs.enqueue_analysis(credential_digest=case.b, **args,
                    request_key=SecretStr('k08-ack-source-write-required'))
            async with case.pool.connection() as conn:
                await conn.execute("UPDATE j01_source_grants SET actions=ARRAY['read','write'],revision=revision+1 "
                                   "WHERE workspace_id='a' AND user_id='owner_b'")

            class RevokedBetweenAdmissionChecks(PostgresJobs):
                async def enqueue(self, **kwargs):
                    await _revoke_source(case, case.receipt.source_id)
                    return await super().enqueue(**kwargs)

            racing = RevokedBetweenAdmissionChecks(case.pool, case.sources, catalog=case.catalog, audit=True,
                execution=True, analysis_model='upload-snapshot-metric-observation/v2')
            with pytest.raises(AuthorizationDenied):
                await asyncio.wait_for(racing.enqueue_analysis(credential_digest=case.b, **args,
                    request_key=SecretStr('k08-ack-revoke-before-admit')), 3)
            # A metadata-valid parent may be durably accepted; its full bytes remain a worker boundary.
            accepted = await jobs.enqueue_analysis(credential_digest=case.a, **args,
                request_key=SecretStr('k08-ack-corrupt-parent-worker'))
            assert object_reads == 0
            assert (await jobs.status(credential_digest=case.a, workspace_id='a', job_id=accepted.job_id)).status == 'queued'
            monkeypatch.setattr(case.objects, 'read', original_read)
            async with case.pool.connection() as conn:
                sha = (await (await conn.execute('SELECT object_sha256 FROM k05_records WHERE record_id=%s',
                                                (case.snapshot.snapshot_id,))).fetchone())[0]
            case.objects.store.path(sha).write_bytes(b'controlled canonical corruption after durable admission')
            private = tmp_path / 'admission-worker'
            private.mkdir()

            class Supervisor(PayloadUploadWorkerSupervisor):
                calls = 0
                async def run_upload(self, payload):
                    self.calls += 1
                    return await super().run_upload(payload)
            supervisor = Supervisor(python=Path(sys.executable).absolute(),
                source_root=Path(__file__).resolve().parents[2] / 'src', private_root=private, slots=1, timeout_seconds=15)
            try:
                operations = CanonicalProjectOperations(case.sources, supervisor, jobs, private_root=private)
                assert await run_one_upload_job(jobs, operations, lease_seconds=30)
                assert supervisor.calls == 0  # Corrupt parent is rejected before exposing bytes to a compute child.
            finally:
                await supervisor.aclose()
            status = await jobs.status(credential_digest=case.a, workspace_id='a', job_id=accepted.job_id)
            assert status.status == 'failed' and status.attempt == 1 and status.retryable is False
            async with case.pool.connection() as conn:
                assert await (await conn.execute('SELECT count(*) FROM j01_jobs')).fetchone() == (3,)
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='upload_result'")).fetchone() == (1,)
                assert await (await conn.execute('SELECT result,canonical_result_id FROM j01_jobs WHERE job_id=%s',
                                                (accepted.job_id,))).fetchone() == (None, None)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.admit'")).fetchone() == (3,)
    _run(scenario())
