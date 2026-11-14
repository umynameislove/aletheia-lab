"""Small native TLS process envelope, physical mix, noisy tenant and reconnect.

Fresh process means application-cold only. OS/PG caches are not flushed, and
these debug dimensions do not qualify a deployment or the primary 50k campaign.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import ssl
import subprocess
import sys
import time
from contextlib import suppress
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg import sql
from pydantic import SecretStr
from test_k02_https_edge import _certificate
from test_k05_native_results_postgres import _run
from test_k06_api_shutdown_postgres import ORIGIN, _application_url, _port
from test_k06_runtime_privileges_postgres import _role_url
from test_k17_capacity_postgres import _CHILD, _identity
from test_k17_native_seed_postgres import _target

from aletheia_lab.hosted.action_audit_migration import migrate as migrate_audit
from aletheia_lab.hosted.capacity_models import Actor
from aletheia_lab.hosted.capacity_seed import SeedLimits, seed_native
from aletheia_lab.hosted.capacity_seed_models import SeedPlan
from aletheia_lab.hosted.runtime_privileges import grant_runtime

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get('K14_TEST_DATABASE_URL') or os.name != 'posix',
    reason='Explicit private native K14 PostgreSQL and POSIX process runner required')]

_MONITOR = r'''
import resource,threading,time
_monitor_stop=threading.Event()
def _monitor_resources():
    with open(os.environ['K14_RESOURCE_SAMPLE_FILE'],'x') as output:
        while not _monitor_stop.is_set():
            own=resource.getrusage(resource.RUSAGE_SELF)
            children=resource.getrusage(resource.RUSAGE_CHILDREN)
            factor=1 if sys.platform=='darwin' else 1024
            fd_path='/proc/self/fd' if sys.platform=='linux' else '/dev/fd'
            output.write(json.dumps({'monotonic':time.monotonic(),
                'cpu_self_seconds':own.ru_utime+own.ru_stime,
                'cpu_reaped_children_seconds':children.ru_utime+children.ru_stime,
                'peak_rss_self_bytes':own.ru_maxrss*factor,
                'peak_rss_reaped_child_bytes':children.ru_maxrss*factor,
                'open_fds':len(os.listdir(fd_path)),
                'fd_soft_limit':resource.getrlimit(resource.RLIMIT_NOFILE)[0],
                'telemetry':process_telemetry().snapshot()})+'\n')
            output.flush()
            _monitor_stop.wait(.1)
_resource_monitor=threading.Thread(target=_monitor_resources,daemon=True)
'''


def _monitored_child():
    marker = 'owner=configure_process_observability('
    assert _CHILD.count(marker) == 1
    child = _CHILD.replace(marker, _MONITOR + '\n' + marker, 1)
    child = child.replace('try:\n    asyncio.run(main())', 'try:\n    _resource_monitor.start()\n    asyncio.run(main())', 1)
    return child.replace('finally:\n    owner.close()',
                         'finally:\n    _monitor_stop.set()\n    _resource_monitor.join(2)\n    owner.close()', 1)


def _process_tree(roots):
    """Bounded snapshots contain PID/counters only, never command arguments."""
    result = subprocess.run(['ps', '-axo', 'pid=,ppid=,rss=,time='],
                            check=True, capture_output=True, text=True, timeout=5)
    rows = {}
    for line in result.stdout.splitlines():
        pid, parent, rss, cpu = line.split()
        parts = cpu.replace('-', ':').split(':')
        seconds = sum(float(value) * 60 ** index for index, value in enumerate(reversed(parts)))
        rows[int(pid)] = {'parent': int(parent), 'rss_bytes': int(rss) * 1024, 'cpu_seconds': seconds}
    observed = {}
    for role, root in roots.items():
        descendants = {root}
        for _ in range(8):
            additions = {pid for pid, values in rows.items() if values['parent'] in descendants}
            if additions <= descendants:
                break
            descendants |= additions
        values = [rows[pid] for pid in descendants if pid in rows]
        observed[role] = {'processes': len(values), 'rss_bytes_sum': sum(v['rss_bytes'] for v in values),
                          'live_cpu_seconds_sum': sum(v['cpu_seconds'] for v in values)}
    return observed, rows


def _summary(values):
    ordered = sorted(values)
    return {'count': len(values), 'mean_ms': sum(values) * 1000 / len(values),
            'p95_observed_ms': ordered[min(len(values) - 1, int(len(values) * .95))] * 1000,
            'max_ms': max(values) * 1000}


@pytest.fixture(autouse=True)
def private_database(monkeypatch):
    monkeypatch.setenv('K17_TEST_DATABASE_URL', os.environ['K14_TEST_DATABASE_URL'])


def test_real_process_mixed_bursts_keep_quiet_tenant_health_and_reconnect_bounded(tmp_path):
    async def scenario():
        database = os.environ['K14_TEST_DATABASE_URL']
        root = Path(__file__).resolve().parents[2]
        source_before = _identity(root)
        secret = secrets.token_urlsafe(48)
        roles = {mode: 'k14_' + mode + '_' + uuid4().hex for mode in ('api', 'worker')}
        passwords = {mode: secrets.token_urlsafe(32) for mode in roles}
        children, handles, created_roles = [], [], []
        stop = asyncio.Event()
        tasks = []
        report = {'schema_version': 'k14-native-process-envelope/v1',
                  'scope': 'One native TLS API, actual two-slot worker and owned PG16 cluster; seeded sessions, no IdP. '
                           'Physical S/M/L sources with bounded native result geometry. Application-cold/warm only; OS/PG caches uncontrolled. '
                           'No multi-replica/ingress/NIC/primary50k or production memory qualification.'}
        try:
            async with _target() as (pool, schema):
                output = tmp_path / 'private-seed'
                plan = SeedPlan(namespace='k17_k14followup', generations=1, results_per_generation=1,
                                source_rows={'S': 1000, 'M': 10000, 'L': 100000})
                manifest = await seed_native(pool, schema=schema, output=output, plan=plan,
                    csrf_secret=SecretStr(secret), limits=SeedLimits(500 * 1024**2, 300000, 100, maximum_seconds=300))
                actors = [Actor.model_validate_json(line) for line in (output / 'actors.jsonl').read_text().splitlines()]
                owners = [actor for actor in actors if actor.role == 'owner']
                assert len(owners) == len({actor.actor_id for actor in owners}) == 6
                noisy = [actor for actor in owners if actor.values['workspace_id'] == plan.workspace(0)]
                quiet = [actor for actor in owners if actor.values['workspace_id'] == plan.workspace(1)]
                assert {actor.size for actor in noisy} == {actor.size for actor in quiet} == {'S', 'M', 'L'}
                async with pool.connection() as conn:
                    await migrate_audit(conn, schema)
                    await conn.execute(files('aletheia_lab.hosted_auth').joinpath('admission_schema.sql').read_text())
                    backend = (await (await conn.execute('SELECT pg_backend_pid()')).fetchone())[0]
                _, all_processes = await asyncio.to_thread(_process_tree, {})
                postgres_pid = all_processes[backend]['parent']
                async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
                    for mode, role in roles.items():
                        await admin.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD {}').format(
                            sql.Identifier(role), sql.Literal(passwords[mode])))
                        created_roles.append(role)
                async with pool.connection() as conn:
                    for mode, role in roles.items():
                        await grant_runtime(conn, schema, role, profile=mode, audit=True)
                cert, key = _certificate(tmp_path)
                key.chmod(0o600)
                port = _port()
                tls = ssl.create_default_context(cafile=str(cert))
                (output / 'worker-private').mkdir(mode=0o700)

                ordinals = dict.fromkeys(roles, 0)
                def start(mode):
                    label = mode + '-' + str(ordinals[mode])
                    ordinals[mode] += 1
                    config = tmp_path / (label + '.json')
                    config.write_text(json.dumps({'environment': 'local', 'public_origin': ORIGIN,
                        'identity': {'issuer': 'https://issuer.example.invalid', 'client_id': 'synthetic-preauthenticated',
                            'redirect_uri': ORIGIN + '/v1/auth/callback', 'client_kind': 'public'},
                        'storage': {'database_url_env': 'ALETHEIA_HOSTED_DATABASE_URL', 'object_root': str(output),
                            'expected_schema': schema, 'expected_database_role': roles[mode]},
                        'api': {'port': port, 'database_pool_size': 4, 'graceful_shutdown_seconds': 10}}))
                    config.chmod(0o600)
                    env = {name: os.environ[name] for name in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP') if name in os.environ}
                    env.update(PYTHONPATH=str(root / 'src'), ALETHEIA_HOSTED_CSRF_SECRET=secret,
                        ALETHEIA_RELEASE_ID=source_before, K14_RESOURCE_SAMPLE_FILE=str(tmp_path / (label + '.resources.jsonl')),
                        ALETHEIA_HOSTED_DATABASE_URL=_application_url(_role_url(database, schema, roles[mode], passwords[mode]),
                            'k14-' + mode))
                    log = (tmp_path / (label + '.safe.jsonl')).open('xb')
                    handles.append(log)
                    child = subprocess.Popen([sys.executable, '-B', '-c', _monitored_child(), mode, str(config), str(cert), str(key)],
                        cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=log)
                    children.append(child)
                    return child

                api = start('api')
                latencies = {name: [] for name in ('cold_result', 'warm_result', 'background_result', 'health', 'admission', 'reconnect')}
                process_samples, db_samples, concurrent_samples, waves = [], [], [], []
                base_url = f'https://127.0.0.1:{port}'
                client_options = dict(base_url=base_url, verify=tls, trust_env=False, timeout=10,
                                      limits=httpx.Limits(max_connections=8, max_keepalive_connections=4))

                def headers(actor):
                    return {'host': 'app.example.invalid', 'origin': ORIGIN,
                            'cookie': actor.cookie.get_secret_value(), 'x-csrf-token': actor.csrf.get_secret_value()}

                async def request(client, kind, method, url, actor=None, **options):
                    started = time.monotonic()
                    response = await client.request(method, url, headers=headers(actor) if actor else {'host': 'app.example.invalid'}, **options)
                    latencies[kind].append(time.monotonic() - started)
                    return response

                async with httpx.AsyncClient(**client_options) as client:
                    async with asyncio.timeout(20):
                        while True:
                            assert api.poll() is None, 'Actual API failed; retain owned safe log.'
                            try:
                                if (await client.get('/health/ready', headers={'host': 'app.example.invalid'})).status_code == 200:
                                    break
                            except httpx.HTTPError:
                                pass
                            await asyncio.sleep(.05)
                    for actor in owners:
                        session = await client.get('/v1/session', headers=headers(actor))
                        assert session.status_code == 200 and session.json()['actor']['user_id'] == actor.actor_id
                    for phase in ('cold_result', 'warm_result'):
                        for actor in owners:
                            response = await request(client, phase, 'GET',
                                f"/v1/workspaces/{actor.values['workspace_id']}/results/{actor.values['result_id']}", actor)
                            assert response.status_code == 200
                            assert response.json()['workspace_id'] == actor.values['workspace_id']

                    worker = None
                    async def observe():
                        while not stop.is_set():
                            roots = {'api': api.pid, 'postgres': postgres_pid, 'generator': os.getpid()}
                            if worker is not None:
                                roots['worker'] = worker.pid
                            sample, _ = await asyncio.to_thread(_process_tree, roots)
                            sample.pop('generator')  # Generator tree includes SUT descendants; don't double count.
                            process_samples.append(sample)
                            async with pool.connection() as conn:
                                rows = await (await conn.execute('SELECT usename,count(*) FROM pg_stat_activity '
                                    'WHERE usename=ANY(%s) GROUP BY usename', (list(roles.values()),))).fetchall()
                                db_samples.append({mode: next((count for name, count in rows if name == role), 0)
                                                   for mode, role in roles.items()})
                                concurrent_samples.append(await (await conn.execute(
                                    "SELECT workspace_id,operation,count(*) FROM j01_jobs WHERE state IN ('running','cancel_requested') "
                                    'GROUP BY workspace_id,operation')).fetchall())
                            with suppress(TimeoutError):
                                await asyncio.wait_for(stop.wait(), .1)

                    async def background():
                        while not stop.is_set():
                            actor = quiet[len(latencies['background_result']) % len(quiet)]
                            response = await request(client, 'background_result', 'GET',
                                f"/v1/workspaces/{actor.values['workspace_id']}/results/{actor.values['result_id']}", actor)
                            assert response.status_code == 200 and response.json()['workspace_id'] == actor.values['workspace_id']
                            health = await request(client, 'health', 'GET', '/health/ready')
                            assert health.status_code == 200
                            with suppress(TimeoutError):
                                await asyncio.wait_for(stop.wait(), .05)

                    tasks.extend([asyncio.create_task(observe()), asyncio.create_task(background())])
                    admitted_ids = []
                    for wave in range(2):
                        chosen = [noisy[index % 3] for index in range(8)] + [quiet[0], quiet[2]]
                        receipts = []
                        for index, actor in enumerate(chosen):
                            url = f"/v1/workspaces/{actor.values['workspace_id']}/jobs"
                            key_value = f'k14-mixed-burst-{wave}-{index}'
                            async def admit(url=url, actor=actor, key_value=key_value):
                                started = time.monotonic()
                                response = await client.post(url, headers={**headers(actor), 'idempotency-key': key_value},
                                    json={'operation': 'preview', 'source_id': actor.values['source_id']})
                                latencies['admission'].append(time.monotonic() - started)
                                assert response.status_code == 202
                                return response.json()
                            doubled = await asyncio.gather(*(admit() for _ in range(3)))
                            assert doubled[0] == doubled[1] == doubled[2]
                            receipts.append(doubled[0])
                            admitted_ids.append(doubled[0]['job_id'])
                        baseline = []
                        for actor, receipt in zip(chosen, receipts, strict=True):
                            response = await client.get(receipt['status_url'] + '/updates', headers=headers(actor))
                            assert response.status_code == 200
                            baseline.append(response.json())
                        if worker is None:
                            worker = start('worker')
                        async with asyncio.timeout(40):
                            while True:
                                async with pool.connection() as conn:
                                    states = await (await conn.execute('SELECT job_id,state FROM j01_jobs WHERE job_id=ANY(%s)',
                                        ([item['job_id'] for item in receipts],))).fetchall()
                                assert all(state not in ('failed', 'cancelled', 'expired') for _, state in states)
                                if len(states) == 10 and all(state == 'succeeded' for _, state in states):
                                    break
                                assert not any(task.done() for task in tasks), 'Background read/health/resource task stopped.'
                                await asyncio.sleep(.05)
                        await asyncio.sleep(2)  # Respect the minimum advertised pending poll interval.
                        async with httpx.AsyncClient(**client_options) as reconnected:
                            for index in (0, 5, 8):  # Exactly 30% use a fresh verified TLS transport.
                                actor, receipt, previous = chosen[index], receipts[index], baseline[index]
                                response = await request(reconnected, 'reconnect', 'GET', receipt['status_url'] + '/updates', actor,
                                                         params={'cursor': previous['cursor']})
                                current = response.json()
                                assert response.status_code == 200 and current['revision'] >= previous['revision']
                                assert current['disposition'] in ('snapshot', 'unchanged', 'resync')
                                if current['job'] is not None:
                                    assert current['job']['status'] == 'succeeded' and current['job']['job_id'] == receipt['job_id']
                        for actor, receipt in zip(chosen, receipts, strict=True):
                            response = await client.get(receipt['status_url'] + '/output', headers=headers(actor))
                            assert response.status_code == 200
                            assert response.json()['result']['source_sha256'] == actor.values['source_sha256']
                        async with pool.connection() as conn:
                            attempts = await (await conn.execute('SELECT a.workspace_id,a.started_at,a.finished_at,a.epoch,a.outcome '
                                'FROM k09_job_attempts a WHERE job_id=ANY(%s) ORDER BY started_at',
                                ([item['job_id'] for item in receipts],))).fetchall()
                        assert len(attempts) == 10 and all(row[3:] == (1, 'succeeded') for row in attempts)
                        for current in attempts:
                            overlapping = [row for row in attempts if row[1] <= current[1] < row[2]]
                            assert len(overlapping) <= 2
                            assert sum(row[0] == current[0] for row in overlapping) <= 1
                        assert {row[0] for row in attempts[:2]} == {plan.workspace(0), plan.workspace(1)}
                        quiet_finished = min(row[2] for row in attempts if row[0] == plan.workspace(1))
                        assert quiet_finished < max(row[1] for row in attempts if row[0] == plan.workspace(0))
                        waves.append({'wave': wave, 'unique_jobs': 10, 'http_admissions_with_same_key': 30,
                                      'quiet_tenant_in_first_two_claims': True,
                                      'quiet_completion_before_last_noisy_claim': True,
                                      'quiet_first_finish_from_first_claim_ms': (quiet_finished - attempts[0][1]).total_seconds() * 1000})
                        worker.terminate()
                        await asyncio.to_thread(worker.wait, 15)
                        worker = None  # Repeat an actual worker startup with the next burst already durably queued.
                    stop.set()
                    await asyncio.gather(*tasks)
                    async with pool.connection() as conn:
                        counts = await (await conn.execute("SELECT count(*),count(*) FILTER(WHERE state='succeeded') FROM j01_jobs")).fetchone()
                        audits = await (await conn.execute("SELECT action,count(*) FROM k06_action_audit WHERE action IN ('job.admit','job.complete','job.publish') GROUP BY action")).fetchall()
                    assert counts == (20, 20) and len(set(admitted_ids)) == 20
                    assert dict(audits) == {'job.admit': 20, 'job.complete': 20}
                    assert db_samples and max(v['api'] for v in db_samples) <= 4 and max(v['worker'] for v in db_samples) <= 2
                    assert max(sample.get('worker', {}).get('processes', 0) for sample in process_samples) <= 3
                    assert all(sum(row[2] for row in sample) <= 2 and all(row[2] <= 1 for row in sample) for sample in concurrent_samples)
                    assert len(latencies['background_result']) >= 5 and len(latencies['health']) >= 5
                for child in children:
                    child.terminate()
                for child in children:
                    await asyncio.to_thread(child.wait, 15)
                for handle in handles:
                    handle.close()
                resources = {path.stem: [json.loads(line) for line in path.read_text().splitlines()]
                             for path in sorted(tmp_path.glob('*.resources.jsonl'))}
                for samples in resources.values():
                    assert len(samples) > 2
                    assert samples[-1]['telemetry']['gauges'].get('pool_waiting', 0) == 0
                    assert samples[-1]['open_fds'] <= samples[1]['open_fds'] + 16
                resource_summary = {label: {
                    'samples': len(samples),
                    'observed_seconds': samples[-1]['monotonic'] - samples[0]['monotonic'],
                    'cpu_self_seconds': samples[-1]['cpu_self_seconds'] - samples[0]['cpu_self_seconds'],
                    'cpu_reaped_children_seconds': samples[-1]['cpu_reaped_children_seconds'],
                    'kernel_peak_rss_self_bytes': max(item['peak_rss_self_bytes'] for item in samples),
                    'kernel_peak_rss_single_reaped_child_bytes': max(item['peak_rss_reaped_child_bytes'] for item in samples),
                    'open_fds_peak': max(item['open_fds'] for item in samples),
                    'open_fds_last': samples[-1]['open_fds'],
                    'fd_soft_limit': samples[-1]['fd_soft_limit'],
                    'event_loop_lag_observed_max_ms': max(item['telemetry']['gauges'].get('event_loop_lag_ms', 0) for item in samples),
                    'pool_waiters_observed_max': max(item['telemetry']['gauges'].get('pool_waiting', 0) for item in samples),
                } for label, samples in resources.items()}
                async with pool.connection() as conn:
                    assert await (await conn.execute('SELECT count(*) FROM pg_stat_activity WHERE usename=ANY(%s)',
                        (list(roles.values()),))).fetchone() == (0,)
                assert _identity(root) == source_before
                report.update(runtime_tree_sha256=source_before, source_stable=True,
                    physical_strata=manifest['strata'], physical=manifest['physical'], seed_primary_dataset_qualified=False,
                    exercised_principals=6, waves=waves, jobs_succeeded=20, actual_http_admissions=60,
                    same_key_duplicate_jobs=0, reconnect_job_observations=6, fresh_tls_client_transports=2,
                    reconnect_fraction_of_jobs=.3,
                    latency={name: _summary(values) for name, values in latencies.items()},
                    native_pool_peaks={mode: max(values[mode] for values in db_samples) for mode in roles},
                    process_samples=process_samples, process_resources=resources, resource_summary=resource_summary,
                    sampling_limits='100ms per-process kernel/telemetry; ~100ms+ps/query cost tree sampling can miss short children. '
                        'RSS sums double-count shared pages; RUSAGE_CHILDREN includes reaped children CPU and maximum single-child peak, not aggregate concurrent peak. '
                        'No per-operation CPU attribution under mixed load; wall means are measured by endpoint. No machine/NIC headroom claim.',
                    full_release_or_primary50k_pass=False)
                saved = os.environ.get('K14_EVIDENCE_DIR')
                if saved:
                    destination = Path(saved)
                    destination.mkdir(parents=True, exist_ok=True)
                    with (destination / 'native-process-envelope.json').open('x') as handle:
                        json.dump(report, handle, indent=2)
                        handle.write('\n')
                print('K14_PROCESS_ENVELOPE ' + json.dumps({key: value for key, value in report.items()
                    if key not in ('process_samples', 'process_resources', 'physical_strata', 'physical')}, sort_keys=True))
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for child in children:
                if child.poll() is None:
                    child.terminate()
            for child in children:
                try:
                    await asyncio.to_thread(child.wait, 15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    await asyncio.to_thread(child.wait, 5)
            for handle in handles:
                handle.close()
            async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
                for role in created_roles:
                    await admin.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
                    await admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))

    _run(scenario())
