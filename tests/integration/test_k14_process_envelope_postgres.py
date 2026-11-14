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
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
from k14_process_probe import linux_processes, local_postgres_root
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
                'transport':dict(globals().get('_transport_counts',{})),
                'telemetry':process_telemetry().snapshot()})+'\n')
            output.flush()
            _monitor_stop.wait(.1)
_resource_monitor=threading.Thread(target=_monitor_resources,daemon=True)
'''

_INGRESS = r'''
from contextlib import suppress
_transport_counts=dict(accepted=0,active=0,peak_active=0,rejected=0,to_api_bytes=0,to_client_bytes=0)
async def main():
    ports=json.loads(sys.argv[2]);stop=asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,stop.set)
    async def forward(reader,writer):
        if _transport_counts['active']>=32:
            _transport_counts['rejected']+=1;writer.close();await writer.wait_closed();return
        ordinal=_transport_counts['accepted'];_transport_counts['accepted']+=1
        _transport_counts['active']+=1
        _transport_counts['peak_active']=max(_transport_counts['peak_active'],_transport_counts['active'])
        upstream=None
        try:
            remote,upstream=await asyncio.open_connection('127.0.0.1',ports[ordinal%len(ports)])
            async def pipe(source,target,counter):
                while data:=await source.read(65536):
                    _transport_counts[counter]+=len(data);target.write(data);await target.drain()
            pumps = [asyncio.create_task(pipe(reader,upstream,'to_api_bytes')),
                     asyncio.create_task(pipe(remote,writer,'to_client_bytes'))]
            try:
                await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for pump in pumps:
                    pump.cancel()
                await asyncio.gather(*pumps, return_exceptions=True)
        finally:
            _transport_counts['active']-=1
            for stream in (upstream,writer):
                if stream is not None:
                    stream.close()
                    with suppress(OSError): await stream.wait_closed()
    server=await asyncio.start_server(forward,'127.0.0.1',int(sys.argv[1]),backlog=64)
    async with server: await stop.wait()
try:
    _resource_monitor.start();asyncio.run(main())
finally:
    _monitor_stop.set();_resource_monitor.join(2)
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
    if sys.platform == 'linux':
        rows = linux_processes()
        return _tree_totals(roots, rows), rows
    result = subprocess.run(['ps', '-axo', 'pid=,ppid=,rss=,time='],
                            check=True, capture_output=True, text=True, timeout=5)
    rows = {}
    for line in result.stdout.splitlines():
        pid, parent, rss, cpu = line.split()
        parts = cpu.replace('-', ':').split(':')
        seconds = sum(float(value) * 60 ** index for index, value in enumerate(reversed(parts)))
        rows[int(pid)] = {'parent': int(parent), 'rss_bytes': int(rss) * 1024, 'cpu_seconds': seconds}
    return _tree_totals(roots, rows), rows


def _tree_totals(roots, rows):
    observed = {}
    for role, root in roots.items():
        descendants = {root}
        for _ in range(8):
            additions = {pid for pid, values in rows.items() if values['parent'] in descendants}
            if additions <= descendants:
                break
            descendants |= additions
        values = [rows[pid] for pid in descendants if pid in rows]
        fds = [v.get('open_fds') for v in values]
        observed[role] = {'processes': len(values), 'rss_bytes_sum': sum(v['rss_bytes'] for v in values),
                          'live_cpu_seconds_sum': sum(v['cpu_seconds'] for v in values),
                          'open_fds_sum': sum(fds) if fds and None not in fds else None}
    return observed


def _summary(values):
    ordered = sorted(values)
    return {'count': len(values), 'mean_ms': sum(values) * 1000 / len(values),
            'p95_observed_ms': ordered[min(len(values) - 1, int(len(values) * .95))] * 1000,
            'max_ms': max(values) * 1000}


def _container_usage():
    files = {
        'cpu_usage_ns': ('cpuacct/cpuacct.usage',),
        'memory_current_bytes': ('memory/memory.usage_in_bytes', 'memory.current'),
        'memory_peak_bytes': ('memory/memory.max_usage_in_bytes', 'memory.peak'),
    }
    root = Path('/sys/fs/cgroup')
    usage = {}
    for key, options in files.items():
        for name in options:
            with suppress(OSError, ValueError):
                usage[key] = int((root / name).read_text())
                break
    if 'cpu_usage_ns' not in usage:
        with suppress(OSError, ValueError, KeyError):
            counters = dict(line.split() for line in (root / 'cpu.stat').read_text().splitlines())
            usage['cpu_usage_ns'] = int(counters['usage_usec']) * 1000
    usage['monotonic_seconds'] = time.monotonic()
    return usage


@pytest.fixture(autouse=True)
def private_database(monkeypatch):
    monkeypatch.setenv('K17_TEST_DATABASE_URL', os.environ['K14_TEST_DATABASE_URL'])


def _run_envelope(tmp_path, *, replicas=1, steady_seconds=0, verify_queue_pressure=False):
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
        report = {'schema_version': 'k14-native-process-envelope/v2',
                  'scope': f'{replicas} native TLS API and {replicas} two-slot worker replicas with owned PG16; seeded sessions, no IdP. '
                           'Physical S/M/L sources with bounded native result geometry. Application-cold/warm only; OS/PG caches uncontrolled. '
                           'Local fixture only, no external NIC/primary50k or production memory qualification.',
                  'api_replicas': replicas, 'worker_replicas': replicas,
                  'runtime_connection_bound': replicas * (4 + 2), 'observer_admin_reserve': 2,
                  'declared_total_connection_bound': replicas * 6 + 2,
                  'steady_seconds': steady_seconds}
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
                    backend, data_directory = await (await conn.execute(
                        "SELECT pg_backend_pid(),current_setting('data_directory')")).fetchone()
                    _, all_processes = await asyncio.to_thread(_process_tree, {})
                    postgres_pid = local_postgres_root(backend, data_directory, all_processes)
                report['postgres_process_observation'] = {
                    'available': postgres_pid is not None,
                    'binding': 'local PID file and live backend lineage' if postgres_pid is not None
                    else 'server PID namespace is not visible; host process metrics unavailable',
                }
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
                api_ports = []
                tls = ssl.create_default_context(cafile=str(cert))
                (output / 'worker-private').mkdir(mode=0o700)

                ordinals = dict.fromkeys(roles, 0)
                def start(mode):
                    label = mode + '-' + str(ordinals[mode])
                    ordinals[mode] += 1
                    child_port = (port if not api_ports else _port()) if mode == 'api' else port
                    if mode == 'api':
                        api_ports.append(child_port)
                    config = tmp_path / (label + '.json')
                    config.write_text(json.dumps({'environment': 'local', 'public_origin': ORIGIN,
                        'identity': {'issuer': 'https://issuer.example.invalid', 'client_id': 'synthetic-preauthenticated',
                            'redirect_uri': ORIGIN + '/v1/auth/callback', 'client_kind': 'public'},
                        'storage': {'database_url_env': 'ALETHEIA_HOSTED_DATABASE_URL', 'object_root': str(output),
                            'expected_schema': schema, 'expected_database_role': roles[mode]},
                        'api': {'port': child_port, 'database_pool_size': 4, 'graceful_shutdown_seconds': 10},
                        'worker': {'database_pool_size': 2, 'job_slots_per_process': 2}}))
                    config.chmod(0o600)
                    env = {name: os.environ[name] for name in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP') if name in os.environ}
                    env.update(PYTHONPATH=str(root / 'src'), ALETHEIA_HOSTED_CSRF_SECRET=secret,
                        ALETHEIA_RELEASE_ID=source_before, K14_RESOURCE_SAMPLE_FILE=str(tmp_path / (label + '.resources.jsonl')),
                        ALETHEIA_HOSTED_DATABASE_URL=_application_url(_role_url(database, schema, roles[mode], passwords[mode]),
                            'k14-' + label))
                    if replicas > 1:
                        env.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
                    log = (tmp_path / (label + '.safe.jsonl')).open('xb')
                    handles.append(log)
                    child = subprocess.Popen([sys.executable, '-B', '-c', _monitored_child(), mode, str(config), str(cert), str(key)],
                        cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=log)
                    children.append(child)
                    return child

                apis = [start('api') for _ in range(replicas)]
                ingress = None
                if replicas > 1:
                    port = _port()
                    ingress_env = dict(os.environ, PYTHONPATH=str(root / 'src'),
                        K14_RESOURCE_SAMPLE_FILE=str(tmp_path / 'ingress.resources.jsonl'))
                    ingress_code = ('import asyncio,json,os,signal,sys\n'
                        'from aletheia_lab.hosted.observability import process_telemetry\n' + _MONITOR + _INGRESS)
                    ingress_log = (tmp_path / 'ingress.safe.log').open('xb')
                    handles.append(ingress_log)
                    ingress = subprocess.Popen([sys.executable, '-B', '-c', ingress_code, str(port), json.dumps(api_ports)],
                        cwd=root, env=ingress_env, stdout=subprocess.DEVNULL, stderr=ingress_log)
                    children.append(ingress)
                latencies = {name: [] for name in ('cold_result', 'warm_result', 'background_result', 'health',
                    'admin_sql', 'admission', 'reconnect')}
                if steady_seconds:
                    latencies['hot_large_result'] = []
                if verify_queue_pressure:
                    latencies.update(queue_overflow=[], full_queue_replay=[], post_drain_retry=[])
                pressure = []
                process_samples, db_samples, replica_db_samples, concurrent_samples, waves = [], [], [], [], []
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
                        for ready_port in [*api_ports, port] if ingress is not None else api_ports:
                            while True:
                                assert all(api.poll() is None for api in apis), 'Actual API failed; retain owned safe log.'
                                assert ingress is None or ingress.poll() is None, 'Test ingress failed.'
                                try:
                                    ready = await client.get(
                                        f'https://127.0.0.1:{ready_port}/health/ready', headers={'host': 'app.example.invalid'})
                                    if ready.status_code == 200:
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

                    workers = []
                    async def observe():
                        while not stop.is_set():
                            roots = {f'api{index}': api.pid for index, api in enumerate(apis)}
                            if postgres_pid is not None:
                                roots['postgres'] = postgres_pid
                            roots.update({f'worker{index}': worker.pid for index, worker in enumerate(workers)})
                            if ingress is not None:
                                roots['ingress'] = ingress.pid
                            sample, processes = await asyncio.to_thread(_process_tree, roots)
                            generator = processes.get(os.getpid())
                            if generator is not None:
                                sample['generator_self'] = {'processes': 1, 'rss_bytes_sum': generator['rss_bytes'],
                                    'live_cpu_seconds_sum': generator['cpu_seconds'], 'open_fds': generator.get('open_fds')}
                            sample['container_usage'] = _container_usage()
                            process_samples.append(sample)
                            async with pool.connection() as conn:
                                rows = await (await conn.execute('SELECT usename,count(*) FROM pg_stat_activity '
                                    'WHERE usename=ANY(%s) GROUP BY usename', (list(roles.values()),))).fetchall()
                                db_samples.append({mode: next((count for name, count in rows if name == role), 0)
                                                   for mode, role in roles.items()})
                                by_replica = await (await conn.execute('SELECT application_name,count(*) FROM pg_stat_activity '
                                    'WHERE usename=ANY(%s) GROUP BY application_name', (list(roles.values()),))).fetchall()
                                replica_db_samples.append(dict(by_replica))
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
                            started = time.monotonic()
                            async with pool.connection() as conn:
                                assert await (await conn.execute('SELECT 1')).fetchone() == (1,)
                            latencies['admin_sql'].append(time.monotonic() - started)
                            with suppress(TimeoutError):
                                await asyncio.wait_for(stop.wait(), .05)

                    tasks.extend([asyncio.create_task(observe()), asyncio.create_task(background())])
                    measurement_started_utc = datetime.now(UTC).isoformat()
                    measurement_start = time.monotonic()
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
                        if verify_queue_pressure:
                            overflow_actor = chosen[0]
                            route = f"/v1/workspaces/{overflow_actor.values['workspace_id']}/jobs"
                            payload = {'operation': 'preview', 'source_id': overflow_actor.values['source_id']}
                            accepted_headers = {**headers(overflow_actor), 'idempotency-key': f'k14-mixed-burst-{wave}-0'}
                            overflow_headers = {**headers(overflow_actor), 'idempotency-key': f'k14-queue-overflow-{wave}'}
                            rejected = await client.post(route, headers=overflow_headers, json=payload)
                            assert rejected.status_code == 503 and rejected.json()['code'] == 'service_unavailable'
                            retry_after = int(rejected.headers['retry-after'])
                            assert 1 <= retry_after <= 5
                            assert rejected.headers['cache-control'] == 'no-store'
                            rejected_at = time.monotonic()
                            replayed = await client.post(route, headers=accepted_headers, json=payload)
                            assert replayed.status_code == 202 and replayed.json() == receipts[0]
                            async with pool.connection() as conn:
                                queued = await (await conn.execute(
                                    "SELECT workspace_id,count(*) FROM j01_jobs WHERE state='queued' GROUP BY workspace_id")).fetchall()
                            assert dict(queued) == {plan.workspace(0): 8, plan.workspace(1): 2}
                            health = await request(client, 'health', 'GET', '/health/ready')
                            assert health.status_code == 200
                            latencies['queue_overflow'].append(rejected.elapsed.total_seconds())
                            latencies['full_queue_replay'].append(replayed.elapsed.total_seconds())
                        baseline = []
                        for actor, receipt in zip(chosen, receipts, strict=True):
                            response = await client.get(receipt['status_url'] + '/updates', headers=headers(actor))
                            assert response.status_code == 200
                            baseline.append(response.json())
                        if not workers:
                            workers.extend(start('worker') for _ in range(replicas))
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
                        if verify_queue_pressure:
                            await asyncio.sleep(max(0, retry_after - (time.monotonic() - rejected_at)))
                            retried = await client.post(route, headers=overflow_headers, json=payload)
                            assert retried.status_code == 202
                            receipt = retried.json()
                            repeated = await client.post(route, headers=overflow_headers, json=payload)
                            assert repeated.status_code == 202 and repeated.json() == receipt
                            admitted_ids.append(receipt['job_id'])
                            async with asyncio.timeout(20):
                                while True:
                                    response = await client.get(receipt['status_url'], headers=headers(overflow_actor))
                                    assert response.status_code == 200
                                    state = response.json()['status']
                                    assert state in ('queued', 'running', 'succeeded'), state
                                    if state == 'succeeded':
                                        break
                                    await asyncio.sleep(.25)
                            response = await client.get(receipt['status_url'] + '/output', headers=headers(overflow_actor))
                            assert response.status_code == 200
                            assert response.json()['result']['source_sha256'] == overflow_actor.values['source_sha256']
                            async with pool.connection() as conn:
                                retry_attempts = await (await conn.execute(
                                    'SELECT epoch,outcome FROM k09_job_attempts WHERE job_id=%s',
                                    (receipt['job_id'],))).fetchall()
                            assert retry_attempts == [(1, 'succeeded')]
                            latencies['post_drain_retry'].append(retried.elapsed.total_seconds())
                            pressure.append(dict(wave=wave, noisy_queued=8, quiet_queued=2,
                                rejected=503, retry_after_seconds=retry_after, replay_while_full=202,
                                post_drain_same_key=202, duplicate_post_drain_same_key=202,
                                retry_jobs=1, retry_worker_attempts=len(retry_attempts), source_sha_verified=True))
                        for worker in workers:
                            worker.terminate()
                        for worker in workers:
                            await asyncio.to_thread(worker.wait, 15)
                        workers.clear()  # Each wave includes an actual worker startup.
                    if steady_seconds:
                        workers.extend(start('worker') for _ in range(replicas))
                        end = time.monotonic() + steady_seconds
                        while time.monotonic() < end:
                            actor = noisy[2]
                            response = await request(client, 'hot_large_result', 'GET',
                                f"/v1/workspaces/{actor.values['workspace_id']}/results/{actor.values['result_id']}", actor)
                            assert response.status_code == 200 and response.json()['workspace_id'] == actor.values['workspace_id']
                            await asyncio.sleep(.05)
                    stop.set()
                    await asyncio.gather(*tasks)
                    measurement_seconds = time.monotonic() - measurement_start
                    measurement_finished_utc = datetime.now(UTC).isoformat()
                    async with pool.connection() as conn:
                        counts = await (await conn.execute("SELECT count(*),count(*) FILTER(WHERE state='succeeded') FROM j01_jobs")).fetchone()
                        audits = await (await conn.execute("SELECT action,count(*) FROM k06_action_audit WHERE action IN ('job.admit','job.complete','job.publish') GROUP BY action")).fetchall()
                    expected_jobs = 20 + len(pressure)
                    assert counts == (expected_jobs, expected_jobs) and len(set(admitted_ids)) == expected_jobs
                    assert dict(audits) == {'job.admit': expected_jobs, 'job.complete': expected_jobs}
                    assert db_samples and max(v['api'] for v in db_samples) <= 4 * replicas
                    assert max(v['worker'] for v in db_samples) <= 2 * replicas
                    assert all(values['processes'] <= 3 for sample in process_samples for label, values in sample.items() if label.startswith('worker'))
                    for label in {name for sample in replica_db_samples for name in sample}:
                        assert max(sample.get(label, 0) for sample in replica_db_samples) <= (4 if '-api-' in label else 2)
                    assert all(sum(row[2] for row in sample) <= 2 and all(row[2] <= 1 for row in sample) for sample in concurrent_samples)
                    assert len(latencies['background_result']) >= 5 and len(latencies['health']) >= 5
                if ingress is not None:
                    async with asyncio.timeout(3):
                        while True:
                            last = (tmp_path / 'ingress.resources.jsonl').read_text().splitlines()[-1]
                            if json.loads(last)['transport']['active'] == 0:
                                break
                            await asyncio.sleep(.1)
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
                    if 'pool_waiting' in samples[-1]['telemetry']['gauges']:
                        assert samples[-1]['telemetry']['gauges']['pool_waiting'] == 0
                    assert samples[-1]['open_fds'] <= samples[1]['open_fds'] + 16
                transport = resources['ingress.resources'][-1]['transport'] if ingress is not None else None
                summary = {name: _summary(values) for name, values in latencies.items() if values}
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
                    exercised_principals=6, waves=waves, jobs_succeeded=expected_jobs,
                    actual_http_admissions=60 + 3 * len(pressure), queue_pressure=pressure,
                    same_key_duplicate_jobs=0, reconnect_job_observations=6, fresh_tls_client_transports=2,
                    reconnect_fraction_of_jobs=.3,
                    ingress_transport=transport,
                    measurement_seconds=measurement_seconds,
                    measurement_started_utc=measurement_started_utc, measurement_finished_utc=measurement_finished_utc,
                    latency_samples_seconds=latencies,
                    pool_samples=db_samples,
                    per_replica_pool_samples=replica_db_samples,
                    latency=summary,
                    native_pool_peaks={mode: max(values[mode] for values in db_samples) for mode in roles},
                    per_replica_pool_peaks={name: max(values.get(name, 0) for values in replica_db_samples)
                        for name in {name for values in replica_db_samples for name in values}},
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
                    if key not in ('process_samples', 'process_resources', 'physical_strata', 'physical',
                        'latency_samples_seconds', 'pool_samples', 'per_replica_pool_samples')}, sort_keys=True))
                if replicas > 1:
                    assert summary.get('hot_large_result', {}).get('count', 0) >= 30
                    assert transport is not None
                    assert transport['active'] == transport['rejected'] == 0, transport
                    assert 0 < transport['peak_active'] <= 32 and transport['to_api_bytes'] > 0 and transport['to_client_bytes'] > 0
                    for name, bound in (('health', 250), ('admin_sql', 250), ('background_result', 500)):
                        assert summary[name]['p95_observed_ms'] <= bound, (name, bound, summary[name])
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


def test_real_process_mixed_bursts_keep_quiet_tenant_health_and_reconnect_bounded(tmp_path):
    _run_envelope(tmp_path)


@pytest.mark.skipif(os.environ.get('K14_RUN_FLEET') != '1', reason='Explicit bounded local fleet measurement required')
def test_two_api_two_worker_replicas_with_ingress_health_admin_and_mixed_tenant_load(tmp_path):
    _run_envelope(tmp_path, replicas=2, steady_seconds=30)


@pytest.mark.skipif(os.environ.get('K14_RUN_FLEET') != '1', reason='Explicit bounded local fleet measurement required')
def test_queue_pressure_preserves_quiet_tenant_and_same_key_retry_through_ingress(tmp_path):
    _run_envelope(tmp_path, replicas=2, steady_seconds=30, verify_queue_pressure=True)
