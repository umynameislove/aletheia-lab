"""Snapshot version, scope, retry and real HTTP boundary invariants."""
import asyncio
import json
import random
from types import SimpleNamespace

import pytest
from itsdangerous.timed import TimestampSigner
from pydantic import SecretStr, ValidationError
from test_k02_job_boundary import PATH, _system

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.cursors import CursorExpired, CursorInvalid, ScopedCursors
from aletheia_lab.hosted.job_polling import SnapshotPolling, reconnect_delay_ms
from aletheia_lab.hosted.poll_admission import PollAdmission
from aletheia_lab.hosted.resource_admission import ResourceBusy
from aletheia_lab.hosted_contract.job_page import Job
from aletheia_lab.hosted_contract.job_updates import JobUpdate

KEY = SecretStr('synthetic-k15-signing-key-' + 'x' * 43)
BINDING = {'workspace_id': 'a', 'job_id': 'job1', 'user_id': 'owner',
           'context_id': 'ctx1', 'generation': 0, 'membership_revision': 1}


def _job(**values):
    return Job(schema_version='product-api/v1-draft', workspace_id='a', job_id='job1',
               status='queued', progress=None).model_copy(update=values)


def _response(policy, job=None, revision=1, cursor=None, **options):
    return policy.response(job=_job() if job is None else job, revision=revision,
        binding=options.get('binding', BINDING), authority_revision=options.get('authority_revision', 1), cursor=cursor)


def test_snapshot_unchanged_dedup_gap_resync_and_terminal_stop():
    policy = SnapshotPolling(ScopedCursors(KEY), random_source=lambda: 0)
    first = _response(policy)
    assert first.disposition == 'snapshot' and first.poll_after_ms == 2000 and first.job.status == 'queued'
    unchanged = _response(policy, cursor=first.cursor)
    assert unchanged.disposition == 'unchanged' and unchanged.job is None and unchanged.revision == 1
    assert unchanged.poll_after_ms == 2400
    running = _response(policy, _job(status='running'), 2, unchanged.cursor)
    assert running.disposition == 'snapshot' and running.poll_after_ms == 2000
    gap = _response(policy, _job(status='succeeded', progress=1.0), 4, running.cursor)
    assert gap.disposition == 'resync' and gap.job.status == 'succeeded' and gap.poll_after_ms is None
    assert _response(policy, gap.job, 4, gap.cursor).disposition == 'unchanged'
    assert _response(policy, gap.job, 4, gap.cursor).poll_after_ms is None
    assert gap.authorization_valid_for_ms == 4000


def test_authority_change_forces_fresh_snapshot_without_inventing_status_event():
    policy = SnapshotPolling(ScopedCursors(KEY), random_source=lambda: .5)
    first = _response(policy)
    refreshed = _response(policy, cursor=first.cursor, authority_revision=2)
    assert refreshed.disposition == 'resync' and refreshed.revision == first.revision
    assert refreshed.job == first.job


@pytest.mark.parametrize('name,value', [('workspace_id', 'b'), ('job_id', 'other'),
    ('context_id', 'other'), ('user_id', 'other'), ('generation', 1), ('membership_revision', 2)])
def test_cursor_is_bound_to_current_actor_scope_session_and_revision(name, value):
    policy = SnapshotPolling(ScopedCursors(KEY))
    first = _response(policy)
    with pytest.raises(CursorInvalid):
        _response(policy, cursor=first.cursor, binding={**BINDING, name: value})


def test_expired_snapshot_cursor_requires_resync_and_scope_still_wins(monkeypatch):
    clock = [1000]
    monkeypatch.setattr(TimestampSigner, 'get_timestamp', lambda self: clock[0])
    policy = SnapshotPolling(ScopedCursors(KEY))
    first = _response(policy)
    clock[0] = 1301
    with pytest.raises(CursorExpired):
        _response(policy, cursor=first.cursor)
    with pytest.raises(CursorInvalid):
        _response(policy, cursor=first.cursor, binding={**BINDING, 'workspace_id': 'b'})
    assert _response(policy).disposition == 'snapshot'


def test_unversioned_state_drift_and_forward_cursor_fail_closed():
    policy = SnapshotPolling(ScopedCursors(KEY))
    first = _response(policy)
    with pytest.raises(ValueError, match='durable revision'):
        _response(policy, _job(status='running'), cursor=first.cursor)
    newer = _response(policy, revision=3)
    with pytest.raises(CursorInvalid):
        _response(policy, cursor=newer.cursor)
    assert _response(policy, _job(retryable=True)).job.retryable is None
    with pytest.raises(ValidationError):
        _response(policy, _job(status='private_marker'))


def test_polling_is_bounded_per_workspace_without_persistent_actor_map():
    async def run():
        polls = PollAdmission(total=3, per_workspace=2)
        polls.acquire('hot')
        polls.acquire('hot')
        with pytest.raises(ResourceBusy):
            polls.acquire('hot')
        polls.acquire('quiet')
        assert polls.active_workspaces == 2 and polls.slots.snapshot().active == 3
        with pytest.raises(ResourceBusy):
            polls.acquire('third')
        polls.release('hot')
        polls.release('quiet')
        polls.release('hot')
        assert polls.active_workspaces == polls.slots.snapshot().active == 0
    asyncio.run(run())


def test_reconnect_30_percent_spreads_100_pending_watchers_without_shortening_retry_after():
    rng = random.Random(1514)
    delays = [reconnect_delay_ms(5, rng.random(), retry_after_ms=30000) for _ in range(30)]
    assert len(set(delays)) == 30 and all(30000 <= value < 45000 for value in delays)
    assert max(delays) - min(delays) > 10000
    # These 30 clients cover/wipe after request-start+4s, well before retry.
    assert all(value > 4000 for value in delays)
    policy = SnapshotPolling(ScopedCursors(KEY), random_source=rng.random)
    updates = [_response(policy) for _ in range(100)]
    assert len({item.poll_after_ms for item in updates}) > 50
    assert all(2000 <= item.poll_after_ms < 2300 for item in updates)


@pytest.mark.parametrize('attempt,jitter,retry', [(True, .5, 0), (31, .5, 0),
    (0, float('nan'), 0), (0, 1, 0), (0, .5, -1), (0, .5, True)])
def test_bad_retry_policy_inputs_are_rejected(attempt, jitter, retry):
    with pytest.raises(ValueError):
        reconnect_delay_ms(attempt, jitter, retry_after_ms=retry)


def _http(tmp_path, *, query=b'', error=None, corrupt=False, busy=False):
    app, jobs, headers = _system(tmp_path)
    policy, calls = SnapshotPolling(ScopedCursors(KEY), random_source=lambda: 0), []

    async def read(**values):
        calls.append(values)
        if error is not None:
            raise error
        job = jobs.job.model_copy(update={'status': 'running', 'progress': None})
        update = policy.response(job=job, revision=2, binding=BINDING, authority_revision=0, cursor=None)
        return update.model_copy(update={'workspace_id': 'private_marker'}) if corrupt else update

    app._updates = SimpleNamespace(read=read)

    async def run():
        messages = []
        async def receive():
            raise AssertionError('Status polling never reads a request body.')
        async def send(event):
            messages.append(event)
        if busy:
            for _ in range(4):
                app._polls.acquire('ws_boundary')
        try:
            await app({'type': 'http', 'scheme': 'https', 'path': PATH + '/job_boundary/updates',
                'method': 'GET', 'headers': headers, 'query_string': query}, receive, send)
        finally:
            if busy:
                for _ in range(4):
                    app._polls.release('ws_boundary')
        assert app._polls.slots.snapshot().active == app._polls.active_workspaces == 0
        return messages[0]['status'], dict(messages[0]['headers']), json.loads(messages[1]['body']), calls
    return asyncio.run(run())


def test_http_poll_returns_valid_small_scope_and_backoff_headers(tmp_path):
    status, headers, body, calls = _http(tmp_path, query=b'cursor=opaque')
    assert status == 200 and headers[b'retry-after'] == b'2' and headers[b'cache-control'] == b'no-store'
    assert body['job_id'] == 'job_boundary' and body['revision'] == 2
    assert calls[0]['cursor'] == 'opaque' and isinstance(calls[0]['credential_digest'], SecretStr)
    assert not {'result', 'request', 'credential_digest'} & set(body)


@pytest.mark.parametrize('query', [b'cursor=', b'cursor=a&cursor=b', b'other=a', b'cursor', b'\xff', b'cursor=' + b'x' * 4097])
def test_http_rejects_bad_queries_before_reading_job(tmp_path, query):
    status, _, _, calls = _http(tmp_path, query=query)
    assert status == 400 and not calls


@pytest.mark.parametrize('error,status,code', [(AuthorizationDenied(), 404, 'not_found'),
    (CursorInvalid(), 400, 'cursor_invalid'), (CursorExpired(), 410, 'cursor_expired')])
def test_http_revocation_and_cursor_failures_disclose_no_job(tmp_path, error, status, code):
    actual, _, body, _ = _http(tmp_path, error=error)
    assert actual == status and body['code'] == code and 'job' not in body


def test_http_poll_scope_corruption_and_workspace_overload_fail_closed(tmp_path):
    status, _, body, _ = _http(tmp_path, corrupt=True)
    assert status == 500 and 'private_marker' not in json.dumps(body)
    status, headers, body, calls = _http(tmp_path, busy=True)
    assert status == 429 and headers[b'retry-after'] == b'1' and not calls


def test_contract_cannot_mark_running_snapshot_as_terminal_or_mix_scopes():
    policy = SnapshotPolling(ScopedCursors(KEY))
    update = _response(policy)
    for patch in ({'poll_after_ms': None}, {'workspace_id': 'b'}, {'job': None},
                  {'job': _job(retryable=True)}):
        with pytest.raises(ValidationError):
            JobUpdate.model_validate({**update.model_dump(), **patch})
