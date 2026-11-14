"""Real slow TLS download + acknowledged queue across graceful API replacement."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import signal
import socket
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
from test_k05_native_results_postgres import _case, _initial, _run
from test_k06_api_shutdown_postgres import _API, ORIGIN, _application_url, _port
from test_k06_canonical_snapshot_postgres import _setup
from test_k06_follow_up_http_postgres import _identity
from test_k06_runtime_privileges_postgres import _role_url
from test_k18_process_recovery import _WORKER

from aletheia_lab.hosted.canonical_results import CanonicalResults
from aletheia_lab.hosted.durable_exports import DurableExports
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.review_migration import migrate as migrate_reviews
from aletheia_lab.hosted.runtime_privileges import grant_runtime
from aletheia_lab.hosted.upload_publication import UploadPublication
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_contract.job_page import JobReceipt
from aletheia_lab.product.reports import render_product_report

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get('K18_TEST_DATABASE_URL') or os.name != 'posix',
    reason='Explicit private K18 PostgreSQL and native POSIX SIGTERM required')]

_DOWNLOAD_API = _API.replace('from aletheia_lab.hosted.postgres_workspaces import PostgresWorkspaceAccess', '''from aletheia_lab.hosted.postgres_workspaces import PostgresWorkspaceAccess
from aletheia_lab.hosted.catalog_access import CatalogAccess
from aletheia_lab.hosted.cursors import ScopedCursors
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.project_mutations import PostgresProjectMutations
from aletheia_lab.hosted.review_repository import PostgresReviews
from aletheia_lab.hosted.report_renderer import ReportRenderer''').replace(
    'jobs=PostgresJobs(pool,sources,audit=True,catalog=catalog)',
    'jobs=PostgresJobs(pool,sources,audit=True,catalog=catalog,execution=True)').replace(
    "rate_policy=RatePolicy(100,50,60)", """renderer=ReportRenderer(timeout_seconds=5)
        catalog_access=CatalogAccess(catalog,ScopedCursors(SecretStr(os.environ['ALETHEIA_HOSTED_CSRF_SECRET'])),renderer)
        rate_policy=RatePolicy(100,50,60)""").replace(
    "admission_subject_key=SecretStr(os.environ['ALETHEIA_HOSTED_CSRF_SECRET']))",
    """admission_subject_key=SecretStr(os.environ['ALETHEIA_HOSTED_CSRF_SECRET']),
            catalog_access=catalog_access,project_access=PostgresProjectAccess(pool),
            reviews=PostgresReviews(pool,catalog),project_mutations=PostgresProjectMutations(pool,catalog))""").replace(
    'await pool.close(timeout=5)', 'await renderer.aclose()\n                    await pool.close(timeout=5)').replace(
    'await send(event)', '''await send(event)
                if str(scope.get('path','')).endswith('/download') and event.get('type')=='http.response.body':
                    with open(marker+'.download.jsonl','a') as observed:
                        observed.write(json.dumps({'bytes':len(event.get('body',b'')),'more_body':event.get('more_body',False)})+'\\n')''').replace(
    "tracked if scope.get('type')=='lifespan' else send", 'tracked')


def test_sigterm_active_slow_download_retains_queued_job_and_authorized_immutable_range(tmp_path, monkeypatch):
    database = os.environ['K18_TEST_DATABASE_URL']
    monkeypatch.setenv('K05_TEST_DATABASE_URL', database)
    roles = {'api': 'k18_dl_api_' + uuid4().hex, 'worker': 'k18_dl_worker_' + uuid4().hex}
    passwords = {profile: secrets.token_urlsafe(32) for profile in roles}
    csrf = secrets.token_urlsafe(48)
    created_roles, children = [], []
    key = claim_file = None
    observation = {'schema_version': 'k18-download-queue-drain/v1',
        'scope': 'Small native restricted-role TLS API/worker fixture; 8MiB whitespace-padded valid JSON distribution payload, not 100k-row analysis/render capacity, IdP or rolling multi-replica qualification.'}

    async def run():
        nonlocal key, claim_file
        try:
            async with _case(tmp_path) as case:
                private = tmp_path / 'retained-runtime'
                private.mkdir(mode=0o700)
                case.objects = PostgresObjects(case.pool, FilesystemObjectStore(private / 'catalog'))
                case.catalog = PostgresCatalog(case.pool, case.objects)
                case.uploads, case.results = UploadPublication(case.objects), CanonicalResults(case.objects)
                await _initial(case)
                jobs = await _setup(case, lambda *args, **kwargs: PostgresJobs(*args, **kwargs, execution=True))
                assert await jobs.execution_ready()
                async with case.pool.connection() as conn:
                    schema = (await (await conn.execute('SELECT current_schema()')).fetchone())[0]
                    await migrate_reviews(conn, schema)
                    await conn.execute(files('aletheia_lab.hosted_auth').joinpath('admission_schema.sql').read_text())
                async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
                    for profile, role in roles.items():
                        await admin.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD {}').format(
                            sql.Identifier(role), sql.Literal(passwords[profile])))
                        created_roles.append(role)
                async with case.pool.connection() as conn:
                    for profile, role in roles.items():
                        await grant_runtime(conn, schema, role, profile=profile, audit=True)
                headers = await _identity(case, 'a', secrets.token_urlsafe(32), CsrfTokens(SecretStr(csrf)))
                headers['host'] = 'app.example.invalid'
                canonical = render_product_report(case.initial.view, 'json')
                payload = canonical + b' ' * (8 * 1024**2)
                assert json.loads(payload) == case.initial.view.model_dump(mode='json')
                item = await DurableExports(case.catalog).publish(case.a, 'a', case.initial.project_id,
                    case.initial.record_id, 'json', payload, None)
                path = f'/v1/workspaces/a/exports/{item.export_id}/download'
                cert, key = _certificate(tmp_path)
                key.chmod(0o600)
                tls, port = ssl.create_default_context(cafile=str(cert)), _port()
                root = Path(__file__).resolve().parents[2]
                markers, names = [], []

                def environment():
                    result = {name: os.environ[name] for name in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP') if name in os.environ}
                    result['PYTHONPATH'] = str(root / 'src')
                    return result

                def start_api():
                    name = 'k18-download-' + uuid4().hex
                    marker, config = private / (name + '.closed'), private / (name + '.json')
                    config.write_text(json.dumps({'environment': 'local', 'public_origin': ORIGIN,
                        'identity': {'issuer': 'https://issuer.example.invalid', 'client_id': 'synthetic-no-idp',
                            'redirect_uri': ORIGIN + '/v1/auth/callback', 'client_kind': 'public'},
                        'storage': {'database_url_env': 'ALETHEIA_HOSTED_DATABASE_URL', 'object_root': str(private),
                            'expected_schema': schema, 'expected_database_role': roles['api']},
                        'api': {'port': port, 'database_pool_size': 1, 'graceful_shutdown_seconds': 10}}))
                    config.chmod(0o600)
                    env = environment()
                    env.update(ALETHEIA_HOSTED_DATABASE_URL=_application_url(_role_url(database, schema, roles['api'], passwords['api']), name),
                        ALETHEIA_HOSTED_CSRF_SECRET=csrf)
                    child = subprocess.Popen([sys.executable, '-B', '-c', _DOWNLOAD_API, str(config), str(cert), str(key), str(marker)],
                        cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    children.append(child)
                    markers.append(marker)
                    names.append(name)
                    return child

                def diagnostic(child):
                    message = child.communicate()[1].decode(errors='replace')[-3000:]
                    for private_value in (database, csrf, *passwords.values()):
                        message = message.replace(private_value, '[REDACTED]')
                    return message

                async def ready(child, client):
                    async with asyncio.timeout(15):
                        while True:
                            assert child.poll() is None, diagnostic(child)
                            try:
                                if (await client.get('/health/ready')).status_code == 200:
                                    return
                            except httpx.HTTPError:
                                pass
                            await asyncio.sleep(.025)

                async def stop(child):
                    await asyncio.wait_for(asyncio.to_thread(child.communicate), 15)
                    assert child.returncode in (0, -signal.SIGTERM), diagnostic(child)

                first = start_api()
                body = {'operation': 'confirm', 'source_id': case.receipt.source_id,
                    'mapping': case.snapshot.mapping.model_dump(mode='json')}
                replay_headers = {'idempotency-key': 'k18-download-retained-job-intent'}
                async with httpx.AsyncClient(base_url=f'https://127.0.0.1:{port}', headers=headers,
                    verify=tls, trust_env=False, timeout=10) as client:
                    await ready(first, client)
                    accepted = await client.post('/v1/workspaces/a/jobs', headers=replay_headers, json=body)
                    assert accepted.status_code == 202, accepted.text
                    receipt = JobReceipt.model_validate_json(accepted.content)
                    reader, writer = await asyncio.open_connection('127.0.0.1', port, ssl=tls,
                        server_hostname='127.0.0.1', limit=1024)
                    try:
                        writer.get_extra_info('socket').setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
                        writer.write((f'GET {path} HTTP/1.1\r\nHost: app.example.invalid\r\n'
                            f"Cookie: {headers['cookie']}\r\nConnection: close\r\n\r\n").encode())
                        await writer.drain()
                        head = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
                        assert b'200 OK' in head and await asyncio.wait_for(reader.readexactly(1), 5) == payload[:1]
                        metadata = await client.get(f'/v1/workspaces/a/results/{case.initial.record_id}/header')
                        assert metadata.status_code == 200 and metadata.json()['result_id'] == case.initial.record_id
                        await asyncio.sleep(.2)
                        delivery = Path(str(markers[0]) + '.download.jsonl')
                        chunks = [json.loads(line) for line in delivery.read_text().splitlines()]
                        assert chunks and all(chunk['more_body'] for chunk in chunks)
                        sent_before_drain = sum(chunk['bytes'] for chunk in chunks)
                        assert sent_before_drain < len(payload)
                        began = time.monotonic()
                        first.send_signal(signal.SIGTERM)
                        await asyncio.sleep(.15)
                        assert first.poll() is None, 'SIGTERM must allow the active request to drain.'
                        async with case.pool.connection() as conn:
                            assert await (await conn.execute('SELECT state FROM j01_jobs WHERE job_id=%s', (receipt.job_id,))).fetchone() == ('queued',)
                    finally:
                        # This client deliberately abandons the old transfer. It is
                        # never counted as a fully delivered file or a job cancel.
                        writer.transport.abort()
                        with suppress(ssl.SSLError, ConnectionError, TimeoutError):
                            await asyncio.wait_for(writer.wait_closed(), 2)
                    await stop(first)
                    assert json.loads(markers[0].read_text())['pool_close_completed'] is True
                    async with case.pool.connection() as conn:
                        assert await (await conn.execute('SELECT count(*) FROM pg_stat_activity WHERE application_name=%s', (names[0],))).fetchone() == (0,)

                restarted = start_api()
                async with httpx.AsyncClient(base_url=f'https://127.0.0.1:{port}', headers=headers,
                    verify=tls, trust_env=False, timeout=10) as client:
                    await ready(restarted, client)
                    ranged = await client.get(path, headers={'range': 'bytes=2-31'})
                    assert ranged.status_code == 206 and ranged.content == payload[2:32]
                    assert ranged.headers['content-range'] == f'bytes 2-31/{len(payload)}'
                    full = await client.get(path)
                    assert full.status_code == 200 and len(full.content) == item.descriptor.byte_size
                    assert hashlib.sha256(full.content).hexdigest() == item.descriptor.sha256
                    replay = await client.post('/v1/workspaces/a/jobs', headers=replay_headers, json=body)
                    assert replay.status_code == 202 and JobReceipt.model_validate_json(replay.content) == receipt
                    snapshot = private / 'snapshot.json'
                    snapshot.write_text(case.snapshot.model_dump_json())
                    snapshot.chmod(0o600)
                    claim_file, release, committed = (private / name for name in ('worker.claim.json', 'worker.release', 'worker.committed'))
                    env = environment()
                    env['K18_WORKER_DATABASE_URL'] = _role_url(database, schema, roles['worker'], passwords['worker'])
                    worker = subprocess.Popen([sys.executable, '-B', '-c', _WORKER, 'publish', str(private / 'catalog'),
                        str(snapshot), str(claim_file), str(release), str(committed)], cwd=root, env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    children.append(worker)
                    release.touch(mode=0o600)
                    async with asyncio.timeout(15):
                        while not committed.exists():
                            assert worker.poll() is None, diagnostic(worker)
                            await asyncio.sleep(.01)
                    output = await client.get(f'/v1/workspaces/a/jobs/{receipt.job_id}/output')
                    assert output.status_code == 200 and output.json()['result']['snapshot_id'] == case.snapshot.snapshot_id
                    rto = time.monotonic() - began
                    assert rto <= 60
                    anonymous = await client.get(path, headers={'cookie': '', 'range': 'bytes=2-31'})
                    assert anonymous.status_code == 401 and 'content-range' not in anonymous.headers and 'etag' not in anonymous.headers
                    async with case.pool.connection() as conn:
                        await conn.execute("UPDATE j01_memberships SET active=false,revision=revision+1 WHERE workspace_id='a' AND user_id='owner_a'")
                    revoked = await client.get(path, headers={'range': 'bytes=2-31'})
                    assert revoked.status_code == 404 and 'content-range' not in revoked.headers and 'etag' not in revoked.headers
                    async with case.pool.connection() as conn:
                        assert await (await conn.execute('SELECT count(*) FROM j01_jobs')).fetchone() == (1,)
                        assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='upload_snapshot'")).fetchone() == (1,)
                        assert await (await conn.execute("SELECT action,count(*) FROM k06_action_audit WHERE action IN ('job.admit','job.publish') GROUP BY action ORDER BY action")).fetchall() == [('job.admit', 1), ('job.publish', 1)]
                    restarted.send_signal(signal.SIGTERM)
                    await stop(restarted)
                    assert json.loads(markers[1].read_text())['pool_close_completed'] is True
                    worker.kill()
                    await asyncio.wait_for(asyncio.to_thread(worker.communicate), 5)
                observation.update(status='passed', bytes=len(payload), sha256=item.descriptor.sha256,
                    server_body_bytes_before_sigterm=sent_before_drain, old_download_completed_by_client=False,
                    deliberate_client_disconnect=True, job_cancel_requested=False, api_pool_max=1,
                    range_bytes_verified=30, whole_file_after_restart_verified=True,
                    queued_job_preserved=True, same_key_receipt_preserved=True, accepted_job_terminal='succeeded',
                    unauthenticated_and_revoked_range_denied=True, rpo_acknowledged_jobs_lost=0,
                    fault_to_authorized_file_and_job_usable_seconds=rto, lifespan_pools_closed=True)
        finally:
            try:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                    await asyncio.to_thread(child.communicate)
                if claim_file is not None:
                    claim_file.unlink(missing_ok=True)
                if created_roles:
                    async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
                        for role in created_roles:
                            assert role.startswith(('k18_dl_api_', 'k18_dl_worker_'))
                            await admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
                observation['owned_schema_roles_removed'] = True
            finally:
                if key is not None:
                    key.unlink(missing_ok=True)
        observation['private_tls_key_removed'] = True
        print('K18_DOWNLOAD_DRAIN ' + json.dumps(observation, sort_keys=True))
    _run(run())
