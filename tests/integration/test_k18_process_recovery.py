"""Real API/worker process loss to authorized HTTPS consumer, retained PG/objects.

This starts the existing seeded-session Uvicorn composition, not real IdP login
or full ManagedRuntime discovery. Payloads are small; no Docker is started.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import ssl
import subprocess
import sys
import time
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from psycopg import sql
from pydantic import SecretStr
from test_k02_https_edge import _certificate
from test_k05_native_results_postgres import _case, _run
from test_k06_api_shutdown_postgres import _API, ORIGIN, _application_url, _port
from test_k06_canonical_snapshot_postgres import _setup
from test_k06_follow_up_http_postgres import _identity
from test_k06_runtime_privileges_postgres import _role_url

from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
from aletheia_lab.hosted.postgres_jobs import JobClaim, PostgresJobs
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.runtime_privileges import grant_runtime
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_contract.job_page import JobOutput, JobReceipt

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get('K18_TEST_DATABASE_URL') or os.name != 'posix',
    reason='Explicit private K18 PostgreSQL and native POSIX process control required')]

_WORKER = r'''
import asyncio,json,os,sys
from pathlib import Path
from pydantic import SecretStr
from psycopg_pool import AsyncConnectionPool
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.upload_snapshot import UploadSnapshot

def private_json(path,body):
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as output:
        json.dump(body,output)

async def main():
    mode,objects,snapshot_file,claim_file,release_file,committed_file=sys.argv[1:]
    async with AsyncConnectionPool(os.environ['K18_WORKER_DATABASE_URL'],min_size=1,max_size=2,open=False) as pool:
        await pool.wait(timeout=10)
        sources=PostgresSources(pool,maximum_upload_bytes=10000,maximum_expanded_bytes=20000,maximum_entries=10)
        catalog=PostgresCatalog(pool,PostgresObjects(pool,FilesystemObjectStore(objects)))
        jobs=PostgresJobs(pool,sources,audit=True,catalog=catalog,execution=True)
        assert await jobs.is_ready() and await jobs.execution_ready()
        lease=SecretStr(('1' if mode=='pause-before-effect' else '2')*64)
        async with asyncio.timeout(20):
            while True:
                claim=await jobs.claim(lease_digest=lease,lease_seconds=1 if mode=='pause-before-effect' else 30,operations=('confirm',))
                if claim is not None:break
                await asyncio.sleep(.025)
        private_json(claim_file,{'job_id':claim.job_id,'workspace_id':claim.workspace_id,
            'operation':claim.operation,'request':claim.request,
            'credential_digest':claim.credential_digest.get_secret_value()})
        async with asyncio.timeout(30):
            while not Path(release_file).exists():await asyncio.sleep(.01)
        snapshot=UploadSnapshot.model_validate_json(Path(snapshot_file).read_bytes())
        assert await jobs.complete_snapshot(claim=claim,lease_digest=lease,snapshot=snapshot)
        private_json(committed_file,{'job_id':claim.job_id,'snapshot_id':snapshot.snapshot_id})
        # Remain alive after the commit so the parent can kill a real live process.
        await asyncio.Event().wait()
asyncio.run(main())
'''


@pytest.mark.parametrize('fault', ['before-effect', 'after-acknowledged-effect', 'graceful-inflight-api'])
def test_real_process_recovery_to_authorized_consumer_with_rpo_zero_rto_under_60s(tmp_path, monkeypatch, fault):
    import psycopg

    database = os.environ['K18_TEST_DATABASE_URL']
    monkeypatch.setenv('K05_TEST_DATABASE_URL', database)
    roles = {'api': 'k18_api_' + uuid4().hex, 'worker': 'k18_worker_' + uuid4().hex}
    passwords = {profile: secrets.token_urlsafe(32) for profile in roles}
    csrf_secret = secrets.token_urlsafe(48)
    children, created_roles, credential_files = [], [], []
    key = None
    observation = {'schema_version': 'k18-native-process-recovery/v1', 'fault': fault,
        'scope': 'Native TLS/Uvicorn seeded-session composition and real execution-profile worker; retained PostgreSQL/object filesystem. No ManagedRuntime/IdP discovery, provider, host loss, PG restart or storage disaster proof.'}

    async def run():
        nonlocal key
        try:
            async with _case(tmp_path) as case:
                private = tmp_path / 'retained-runtime'
                private.mkdir(mode=0o700)
                case.catalog = PostgresCatalog(case.pool, PostgresObjects(case.pool, FilesystemObjectStore(private / 'catalog')))
                jobs = await _setup(case, lambda *args, **kwargs: PostgresJobs(*args, **kwargs, execution=True))
                async with case.pool.connection() as conn:
                    schema = (await (await conn.execute('SELECT current_schema()')).fetchone())[0]
                    await conn.execute(files('aletheia_lab.hosted_auth').joinpath('admission_schema.sql').read_text())
                async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
                    for profile, role in roles.items():
                        await admin.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD {}').format(
                            sql.Identifier(role), sql.Literal(passwords[profile])))
                        created_roles.append(role)
                async with case.pool.connection() as conn:
                    for profile, role in roles.items():
                        await grant_runtime(conn, schema, role, profile=profile, audit=True)
                headers = await _identity(case, 'a', secrets.token_urlsafe(32), CsrfTokens(SecretStr(csrf_secret)))
                headers['host'] = 'app.example.invalid'
                cert, key = _certificate(tmp_path)
                key.chmod(0o600)
                tls, port = ssl.create_default_context(cafile=str(cert)), _port()
                root = Path(__file__).resolve().parents[2]
                snapshot_file = private / 'snapshot.json'
                snapshot_file.write_text(case.snapshot.model_dump_json())
                snapshot_file.chmod(0o600)
                api_names, close_markers = [], []

                def environment():
                    result = {name: os.environ[name] for name in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP') if name in os.environ}
                    result['PYTHONPATH'] = str(root / 'src')
                    return result

                def start_api():
                    name = 'k18-api-' + uuid4().hex
                    marker = private / (name + '.closed.json')
                    config = private / (name + '.json')
                    config.write_text(json.dumps({'environment': 'local', 'public_origin': ORIGIN,
                        'identity': {'issuer': 'https://issuer.example.invalid', 'client_id': 'synthetic-no-idp',
                            'redirect_uri': ORIGIN + '/v1/auth/callback', 'client_kind': 'public'},
                        'storage': {'database_url_env': 'ALETHEIA_HOSTED_DATABASE_URL', 'object_root': str(private),
                            'expected_schema': schema, 'expected_database_role': roles['api']},
                        'api': {'port': port, 'database_pool_size': 2, 'graceful_shutdown_seconds': 10}}))
                    config.chmod(0o600)
                    env = environment()
                    env.update(ALETHEIA_HOSTED_DATABASE_URL=_application_url(_role_url(database, schema, roles['api'], passwords['api']), name),
                        ALETHEIA_HOSTED_CSRF_SECRET=csrf_secret)
                    child = subprocess.Popen([sys.executable, '-B', '-c', _API, str(config), str(cert), str(key), str(marker)],
                        cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    children.append(child)
                    api_names.append(name)
                    close_markers.append(marker)
                    return child

                def start_worker(mode, ordinal):
                    claim, release, committed = (private / f'worker-{ordinal}.{suffix}' for suffix in ('claim.json', 'release', 'committed.json'))
                    credential_files.append(claim)
                    env = environment()
                    env['K18_WORKER_DATABASE_URL'] = _application_url(_role_url(database, schema, roles['worker'], passwords['worker']), 'k18-worker-' + str(ordinal))
                    child = subprocess.Popen([sys.executable, '-B', '-c', _WORKER, mode, str(private / 'catalog'),
                        str(snapshot_file), str(claim), str(release), str(committed)], cwd=root, env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    children.append(child)
                    return child, claim, release, committed

                def diagnostic(child):
                    message = child.communicate()[1].decode(errors='replace')[-3000:]
                    for private_value in (database, csrf_secret, *passwords.values()):
                        message = message.replace(private_value, '[REDACTED]')
                    return message

                async def ended(child, graceful=False):
                    await asyncio.wait_for(asyncio.to_thread(child.communicate), 15)
                    assert child.returncode in ((0, -signal.SIGTERM) if graceful else (-signal.SIGKILL,)), diagnostic(child)

                async def exists(path, child):
                    async with asyncio.timeout(20):
                        while not path.exists():
                            assert child.poll() is None, diagnostic(child)
                            await asyncio.sleep(.01)

                async def ready(child, client):
                    async with asyncio.timeout(20):
                        while True:
                            assert child.poll() is None, diagnostic(child)
                            try:
                                if (await client.get('/health/ready')).status_code == 200:
                                    return
                            except httpx.HTTPError:
                                pass
                            await asyncio.sleep(.025)

                body = {'operation': 'confirm', 'source_id': case.receipt.source_id,
                        'mapping': case.snapshot.mapping.model_dump(mode='json')}
                mutation_headers = {'idempotency-key': 'k18-process-recovery-one-intent'}
                first_api = start_api()
                async with httpx.AsyncClient(base_url=f'https://127.0.0.1:{port}', headers=headers,
                    verify=tls, trust_env=False, timeout=10) as client:
                    await ready(first_api, client)
                    if fault == 'graceful-inflight-api':
                        async with case.pool.connection() as held:
                            await held.execute("SELECT workspace_id FROM j01_workspaces WHERE workspace_id='a' FOR UPDATE")
                            pending = asyncio.create_task(client.post('/v1/workspaces/a/jobs', headers=mutation_headers, json=body))
                            async with asyncio.timeout(10):
                                while True:
                                    async with case.pool.connection() as conn:
                                        waiting = await (await conn.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name=%s AND wait_event_type='Lock'", (api_names[0],))).fetchone()
                                    if waiting[0]:
                                        break
                                    assert not pending.done()
                                    await asyncio.sleep(.01)
                            fault_started = time.monotonic()
                            first_api.send_signal(signal.SIGTERM)
                            await asyncio.sleep(.15)
                            assert first_api.poll() is None, 'Graceful drain must retain the in-flight admitted mutation.'
                            await held.commit()
                        acknowledged = await pending
                        assert acknowledged.status_code == 202, acknowledged.text
                        receipt = JobReceipt.model_validate_json(acknowledged.content)
                        await ended(first_api, graceful=True)
                        assert json.loads(close_markers[0].read_text())['pool_close_completed'] is True
                        async with case.pool.connection() as conn:
                            assert await (await conn.execute('SELECT count(*) FROM pg_stat_activity WHERE application_name=%s', (api_names[0],))).fetchone() == (0,)
                        worker, claim_file, release, committed = start_worker('publish', 1)
                    else:
                        acknowledged = await client.post('/v1/workspaces/a/jobs', headers=mutation_headers, json=body)
                        assert acknowledged.status_code == 202, acknowledged.text
                        receipt = JobReceipt.model_validate_json(acknowledged.content)
                        worker, claim_file, release, committed = start_worker('pause-before-effect' if fault == 'before-effect' else 'publish', 0)
                        await exists(claim_file, worker)
                        stale_body = json.loads(claim_file.read_text())
                        stale_body['credential_digest'] = SecretStr(stale_body['credential_digest'])
                        stale = JobClaim(**stale_body)
                        assert stale.job_id == receipt.job_id
                        before_output = None
                        if fault == 'after-acknowledged-effect':
                            release.touch(mode=0o600)
                            await exists(committed, worker)
                            before_response = await client.get(f'/v1/workspaces/a/jobs/{receipt.job_id}/output')
                            assert before_response.status_code == 200
                            before_output = before_response.json()
                            assert before_output['result']['snapshot_id'] == case.snapshot.snapshot_id
                        fault_started = time.monotonic()
                        first_api.kill()
                        worker.kill()
                        await asyncio.gather(ended(first_api), ended(worker))
                        if fault == 'before-effect':
                            worker, claim_file, release, committed = start_worker('publish', 1)

                restarted_api = start_api()
                async with httpx.AsyncClient(base_url=f'https://127.0.0.1:{port}', headers=headers,
                    verify=tls, trust_env=False, timeout=10) as recovered:
                    await ready(restarted_api, recovered)
                    if fault != 'after-acknowledged-effect':
                        await exists(claim_file, worker)
                        if fault == 'before-effect':
                            # A's stale lease cannot publish while B owns the replacement lease.
                            assert not await jobs.complete_snapshot(claim=stale, lease_digest=SecretStr('1' * 64), snapshot=case.snapshot)
                        release.touch(mode=0o600)
                        await exists(committed, worker)
                    async with asyncio.timeout(30):
                        while True:
                            output = await recovered.get(f'/v1/workspaces/a/jobs/{receipt.job_id}/output')
                            if output.status_code == 200:
                                break
                            assert output.status_code == 409, output.text
                            await asyncio.sleep(.025)
                    usable = JobOutput[dict[str, object]].model_validate_json(output.content)
                    assert usable.job_id == receipt.job_id and usable.result['snapshot_id'] == case.snapshot.snapshot_id
                    record = await case.catalog.read_record(credential_digest=case.a, workspace_id='a',
                        project_id=case.snapshot.mapping.project_id, record_id=case.snapshot.snapshot_id)
                    assert record == case.snapshot
                    rto = time.monotonic() - fault_started
                    assert rto <= 60
                    if fault == 'after-acknowledged-effect':
                        assert output.json() == before_output
                    replay = await recovered.post('/v1/workspaces/a/jobs', headers=mutation_headers, json=body)
                    assert replay.status_code == 202 and JobReceipt.model_validate_json(replay.content) == receipt
                    assert (await recovered.get(f'/v1/workspaces/a/jobs/{receipt.job_id}')).json()['status'] == 'succeeded'
                    denied_headers = {name: value for name, value in headers.items() if name != 'cookie'}
                    denied = await recovered.get(f'/v1/workspaces/a/jobs/{receipt.job_id}/output', headers={**denied_headers, 'cookie': ''})
                    assert denied.status_code == 401 and 'snapshot_id' not in denied.text
                    async with case.pool.connection() as conn:
                        assert await (await conn.execute('SELECT count(*) FROM j01_jobs')).fetchone() == (1,)
                        assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='upload_snapshot'")).fetchone() == (1,)
                        assert await (await conn.execute("SELECT action,count(*) FROM k06_action_audit WHERE action IN ('job.admit','job.publish') GROUP BY action ORDER BY action")).fetchall() == [('job.admit', 1), ('job.publish', 1)]
                    restarted_api.send_signal(signal.SIGTERM)
                    await ended(restarted_api, graceful=True)
                    assert json.loads(close_markers[-1].read_text())['pool_close_completed'] is True
                    if worker.poll() is None:
                        worker.kill()
                        await ended(worker)
                observation.update(status='passed', rto_seconds=rto, rpo_acknowledged_jobs_lost=0,
                    rpo_acknowledged_effects_lost=0 if fault == 'after-acknowledged-effect' else None,
                    rto_start='OS kill/termination signal sent',
                    rto_end='Current authorized HTTPS output and canonical object content validated',
                    acknowledged_effect_before_fault=fault == 'after-acknowledged-effect',
                    acknowledged_effect_preserved=fault == 'after-acknowledged-effect',
                    api_process_ids=[first_api.pid, restarted_api.pid], job_id=receipt.job_id,
                    snapshot_id=case.snapshot.snapshot_id, same_key_receipt_preserved=True,
                    canonical_effects=1, admission_audits=1, publication_audits=1,
                    authorized_https_output_validated=True, canonical_object_revalidated=True,
                    unauthenticated_output_denied=True, restart_lifespan_pool_closed=True,
                    stale_worker_fenced=fault == 'before-effect', graceful_inflight_api_drained=fault == 'graceful-inflight-api')
        finally:
            try:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                    await asyncio.to_thread(child.communicate)
                for path in credential_files:
                    path.unlink(missing_ok=True)
                if created_roles:
                    async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
                        for role in created_roles:
                            assert role.startswith(('k18_api_', 'k18_worker_'))
                            await admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
                        assert await (await admin.execute('SELECT to_regnamespace(%s)', (schema,))).fetchone() == (None,)
                observation['owned_schema_roles_removed'] = True
            finally:
                if key is not None:
                    key.unlink(missing_ok=True)
        observation['private_tls_key_removed'] = True
        (tmp_path / 'k18-process-recovery.json').write_text(json.dumps(observation, indent=2))
        print('K18_PROCESS_RECOVERY ' + json.dumps(observation, sort_keys=True))
    _run(run())
