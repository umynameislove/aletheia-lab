"""Real TLS sockets, PostgreSQL authority, durable queue and separate worker processes.

The session is provisioned as a synthetic test fixture; this is not an IdP/browser run.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest
from pydantic import SecretStr
from test_k07_upload_staging_postgres import _database, _digest, _manifest, _run

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K07_TEST_DATABASE_URL'),
    reason='Explicit K-07 PostgreSQL database required')]


def _source():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('dữ_liệu.csv', 'id,target\n1,0\n2,1\n')
        rows = ['run,name,value', 'baseline,độ_chính_xác,0', 'r1,độ_chính_xác,-0.5', 'r2,độ_chính_xác,1e-300',
                'r3,độ_chính_xác,0.2', 'r4,độ_chính_xác,0.3']
        archive.writestr('metrics.csv', '\n'.join(rows) + '\n')
        archive.writestr('tên_có_dấu/metrics.csv', 'run,name,value\nr4,độ chính xác,0.8\n')
        archive.writestr('config.json', '{"seed":42}')
    return output.getvalue()


def _mapping(candidates):
    target = next(candidate for candidate in candidates['targets'] if candidate['available'])
    metric = next(candidate for candidate in candidates['metric_sources'] if candidate['available'])
    return {'target': {'mapping_id': target['mapping_id'], 'project_item_id': target['project_item_id'],
                       'target_field': 'target', 'identifier_field': 'id'},
        'metric_sources': [{'mapping_id': metric['mapping_id'], 'project_item_id': metric['project_item_id'],
            'format': 'csv', 'records_path': [], 'metric_name_field': 'name', 'metric_value_field': 'value',
            'run_id_field': 'run', 'step_field': None}],
        'runs': [{'run_id': run, 'config_item_ids': []} for run in ('baseline', 'r1', 'r2', 'r3', 'r4')],
        'baseline_run_id': 'baseline',
        'metric_definitions': [{'metric_name': 'độ_chính_xác', 'direction': 'lower_is_better', 'regression_threshold': 0.08}]}


def _certificate(root):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'K07 synthetic local TLS')])
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
        .sign(key, hashes.SHA256()))
    cert, private = root / 'synthetic-cert.pem', root / 'synthetic-key.pem'
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    return cert, private


async def _worker(schema, root):
    """One independently connected worker process; worker itself forks the parser."""
    result = await asyncio.to_thread(subprocess.run,
        [sys.executable, '-B', str(Path(__file__).resolve()), '--k07-worker', schema, str(root)],
        capture_output=True, timeout=45, env=dict(os.environ))
    assert result.returncode == 0, result.stderr.decode(errors='replace')[-1000:]
    assert result.stdout.strip() == b'worked'


def test_tls_upload_preview_confirm_analysis_replay_errors_cancel_and_revoke(tmp_path):
    import httpx2
    import uvicorn

    from aletheia_lab.hosted.import_failures import ImportFailures
    from aletheia_lab.hosted.imports_http import ImportApplication, LeasedJobs
    from aletheia_lab.hosted.job_http import JobApplication
    from aletheia_lab.hosted.postgres_jobs import PostgresJobs
    from aletheia_lab.hosted.postgres_sources import PostgresSources
    from aletheia_lab.hosted.preview_leases import PreviewLeases
    from aletheia_lab.hosted.preview_migration import migrate as migrate_preview
    from aletheia_lab.hosted.request_boundary import BodyLimits
    from aletheia_lab.hosted.settings import RuntimeSettings
    from aletheia_lab.hosted.upload_staging_http import UploadStagingApplication
    from aletheia_lab.hosted_auth.csrf import CsrfTokens
    from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
    from aletheia_lab.hosted_auth.sessions import ServerSessions, credential_digest
    from aletheia_lab.hosted_auth.settings import AuthPolicy

    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate_preview(conn, schema)
                tokens = CsrfTokens(SecretStr('synthetic-k07-key-' + 's' * 43))
                csrf = tokens.issue('ctx_analyst').get_secret_value()
                digest = credential_digest(SecretStr('a' * 43))
                await conn.execute('UPDATE j01_session_credentials SET digest=%s WHERE digest=%s',
                                   (digest.get_secret_value(), _digest().get_secret_value()))
                await conn.execute("UPDATE j01_sessions SET csrf_token=%s,last_seen_at=statement_timestamp(),"
                    "rotated_at=statement_timestamp() WHERE context_id='ctx_analyst'", (csrf,))
            sources = PostgresSources(pool, maximum_upload_bytes=200 * 1024 * 1024,
                                      maximum_expanded_bytes=512 * 1024 * 1024, maximum_entries=100000)
            assert await sources.is_upload_ready()
            leases = PreviewLeases(sources)
            assert await leases.is_ready()
            raw_jobs = PostgresJobs(pool, sources)
            jobs = LeasedJobs(raw_jobs, leases, ImportFailures(pool))
            sessions = ServerSessions(PostgresSessionStore(pool), AuthPolicy(default_return_to='/', return_to_paths=('/',)),
                                      timeout_seconds=5, maximum_cookie_bytes=65536)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            origin = f'https://127.0.0.1:{port}'
            runtime = RuntimeSettings.model_validate({'environment': 'staging', 'public_origin': origin,
                'identity': {'issuer': 'https://id.example.invalid', 'client_id': 'synthetic',
                    'redirect_uri': origin + '/v1/auth/callback', 'client_kind': 'public'},
                'storage': {'database_url_env': 'ALETHEIA_HOSTED_DATABASE_URL', 'object_root': str(tmp_path)}})
            async def fallback(scope, receive, send):
                await send({'type': 'http.response.start', 'status': 404, 'headers': []})
                await send({'type': 'http.response.body', 'body': b''})
            async def clock():
                return True
            limits = BodyLimits(65536, 2, 10, 100)
            app = UploadStagingApplication(fallback, sources, sessions, tokens, runtime, body_limits=limits,
                part_limits=BodyLimits(4 * 1024 * 1024, 2, 10, 100), clock_check=clock)
            app = ImportApplication(app, jobs, sessions, tokens, runtime, leases=leases, body_limits=limits, clock_check=clock)
            app = JobApplication(app, jobs, sessions, tokens, runtime, body_limits=limits, clock_check=clock)
            cert, key = _certificate(tmp_path)
            server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, lifespan='off',
                ssl_certfile=str(cert), ssl_keyfile=str(key), access_log=False, log_level='critical', proxy_headers=False))
            task = asyncio.create_task(server.serve(sockets=[sock]))
            root = tmp_path / 'worker'
            root.mkdir()
            try:
                async with asyncio.timeout(5):
                    while not server.started:
                        if task.done():
                            await task
                        await asyncio.sleep(0.01)
                headers = {'origin': origin, 'x-csrf-token': csrf,
                    'cookie': '__Host-aletheia_session=' + 'a' * 43 + '; __Host-aletheia_csrf=' + csrf}
                async with httpx2.AsyncClient(base_url=origin, verify=ssl.create_default_context(cafile=str(cert)),
                                               headers=headers, timeout=15) as client:
                    payload = _source()
                    manifest, parts = _manifest(payload)
                    base = '/v1/workspaces/a'
                    created = await client.post(base + '/uploads', json=manifest.model_dump(mode='json'),
                                                headers={'idempotency-key': 'tls-upload-key-00001'})
                    assert created.status_code == 201, created.text
                    upload = created.json()['upload_id']
                    path = base + '/uploads/' + upload
                    # Confirmed progress survives a separate HTTP client context.
                    for number in reversed(range(len(parts))):
                        response = await client.put(path + '/parts/' + str(number + 1), content=parts[number],
                                                    headers={'content-type': 'application/octet-stream'})
                        assert response.status_code == 200, response.text
                    status = await client.get(path)
                    assert status.json()['received_bytes'] == len(payload) and status.json()['missing_parts'] == []
                    assert (await client.get('/v1/workspaces/b/uploads/' + upload)).status_code == 404
                    ready = await client.post(path + '/complete', json={})
                    assert ready.status_code == 200, ready.text
                    source_id = ready.json()['source_id']
                    receipt = await client.post(base + '/imports/preview', json={'source_id': source_id},
                                                headers={'idempotency-key': 'tls-preview-key-0001'})
                    assert receipt.status_code == 202, receipt.text
                    preview_job = receipt.json()['job_id']
                    await _worker(schema, root)
                    preview_output = await client.get(base + '/jobs/' + preview_job + '/output')
                    assert preview_output.status_code == 200, preview_output.text
                    preview = preview_output.json()['result']
                    assert preview['source_sha256'] == hashlib.sha256(payload).hexdigest()
                    assert preview['excluded_metrics'][0]['metric_name'] == 'độ chính xác'
                    assert preview['excluded_metrics_total'] == 1
                    preview_path = base + '/imports/' + preview['preview_id']
                    assert (await client.get(preview_path)).status_code == 200
                    # Mapping uses only source-derived IDs and more than three runs.
                    mapping = _mapping(preview['mapping_candidates'])
                    accepted = await client.post(preview_path + '/confirm', json={'mapping': mapping},
                                                 headers={'idempotency-key': 'tls-confirm-key-0001'})
                    assert accepted.status_code == 202, accepted.text
                    confirm_job = accepted.json()['job_id']
                    await _worker(schema, root)
                    confirmed = await client.get(base + '/jobs/' + confirm_job + '/output')
                    assert confirmed.status_code == 200, confirmed.text
                    assert 'snapshot_id' in confirmed.json()['result'], confirmed.text
                    snapshot_id = confirmed.json()['result']['snapshot_id']
                    snapshot = await sources.snapshot(credential_digest=digest, workspace_id='a', snapshot_id=snapshot_id)
                    assert len(snapshot.mapping.runs) == 5 and len(snapshot.mapping_result.metric_observations) == 5
                    analyzed = await client.post(base + '/jobs', json={'operation': 'analyze',
                        'snapshot_id': snapshot_id, 'question': 'What is observed?'},
                        headers={'idempotency-key': 'tls-analysis-key-0001'})
                    assert analyzed.status_code == 202, analyzed.text
                    await _worker(schema, root)
                    result = await client.get(base + '/jobs/' + analyzed.json()['job_id'] + '/output')
                    assert result.status_code == 200, result.text
                    view = result.json()['result']
                    assert view['snapshot']['id'] == snapshot_id
                    assert view['evidence'][0]['source_sha256'] == hashlib.sha256(payload).hexdigest()
                    assert view['runtime']['external_call'] is False
                    assert view['snapshot']['metric_definitions'][0]['metric_name'] == 'độ_chính_xác'
                    invalid = json.loads(json.dumps(mapping))
                    invalid['target']['target_field'] = 'missing_field'
                    failed = await client.post(preview_path + '/confirm', json={'mapping': invalid},
                        headers={'idempotency-key': 'tls-bad-mapping-key01'})
                    assert failed.status_code == 202, failed.text
                    await _worker(schema, root)
                    failure = await client.get(base + '/jobs/' + failed.json()['job_id'] + '/output')
                    assert failure.status_code == 200, failure.text
                    assert failure.json()['result']['code'] == 'mapping_invalid'
                    assert failure.json()['result']['issues'][0]['field'] == 'target.target_field'
                    assert 'missing_field' not in failure.text
                    # Simulated lost ACK: exact same accepted intent returns the original durable receipt after expiry.
                    async with pool.connection() as conn:
                        expired_at = (await (await conn.execute("SELECT clock_timestamp()-interval '10 seconds'")).fetchone())[0]
                        await conn.execute("UPDATE k07_import_previews SET expires_at=%s,"
                            "payload=jsonb_set(payload,'{expires_at}',%s::jsonb) WHERE preview_id=%s",
                            (expired_at, json.dumps(expired_at.isoformat()), preview['preview_id']))
                    recovered = await leases.for_submission(credential_digest=digest, workspace_id='a',
                                                            preview_id=preview['preview_id'])
                    assert recovered.source_id == source_id
                    from aletheia_lab.hosted.preview_leases import PreviewExpired
                    with pytest.raises(PreviewExpired):
                        await leases.validate(credential_digest=digest, workspace_id='a',
                                              source_id=source_id, preview_id=preview['preview_id'])
                    replay_receipt = await jobs.enqueue(credential_digest=digest, workspace_id='a', operation='confirm',
                        request_key=SecretStr('tls-confirm-key-0001'),
                        request={'source_id': source_id, 'mapping': {**mapping, '_preview_id': preview['preview_id']}})
                    assert replay_receipt.job_id == confirm_job
                    replay = await client.post(preview_path + '/confirm', json={'mapping': mapping},
                                               headers={'idempotency-key': 'tls-confirm-key-0001'})
                    assert replay.status_code == 202 and replay.json() == accepted.json(), replay.text
                    denied = await client.post(preview_path + '/confirm', json={'mapping': mapping},
                                               headers={'idempotency-key': 'tls-new-expired-key01'})
                    assert denied.status_code == 410, denied.text
                    assert (await client.get(preview_path)).status_code == 410
                    async with pool.connection() as conn:
                        assert await (await conn.execute('SELECT count(*) FROM j01_upload_snapshots')).fetchone() == (1,)
                    # Revoke gates retained receipts and immutable outputs as well as new submissions.
                    async with pool.connection() as conn:
                        await conn.execute("UPDATE j01_memberships SET active=false,revision=revision+1 "
                                           "WHERE workspace_id='a' AND user_id='analyst'")
                    response = await client.get(base + '/jobs/' + analyzed.json()['job_id'] + '/output')
                    assert response.status_code in (403, 404), response.text
                    assert snapshot_id not in response.text and hashlib.sha256(payload).hexdigest() not in response.text
            finally:
                server.should_exit = True
                await asyncio.wait_for(task, 10)
                sock.close()
    _run(run())


async def _worker_main(schema, root):
    from psycopg_pool import AsyncConnectionPool

    from aletheia_lab.hosted.import_failures import ImportFailures
    from aletheia_lab.hosted.postgres_jobs import PostgresJobs
    from aletheia_lab.hosted.preview_leases import PreviewLeases
    from aletheia_lab.hosted.upload_job_worker import run_one_upload_job
    from aletheia_lab.hosted.upload_operations import UploadOperations
    from aletheia_lab.hosted.windows_worker import WindowsUploadWorker
    from aletheia_lab.hosted.worker_supervisor import UploadWorkerSupervisor
    from aletheia_lab.hosted_auth.worker_sessions import WorkerPostgresSources

    assert schema.startswith('k07_upload_') and len(schema) == 43
    value = urlsplit(os.environ['K07_TEST_DATABASE_URL'])
    query = [(key, item) for key, item in parse_qsl(value.query) if key != 'options']
    query.append(('options', '-csearch_path=' + schema))
    scoped = urlunsplit(value._replace(query=urlencode(query)))
    async with AsyncConnectionPool(scoped, min_size=1, max_size=2, open=False) as pool:
        await pool.wait(timeout=10)
        sources = WorkerPostgresSources(pool, maximum_upload_bytes=200 * 1024 * 1024,
                                       maximum_expanded_bytes=512 * 1024 * 1024, maximum_entries=100000)
        worker_type = WindowsUploadWorker if sys.platform == 'win32' else UploadWorkerSupervisor
        worker = worker_type(python=Path(sys.executable), source_root=Path(__file__).resolve().parents[2] / 'src',
                             private_root=Path(root), slots=1, timeout_seconds=25)
        try:
            worked = await run_one_upload_job(PostgresJobs(pool, sources),
                UploadOperations(sources, worker, leases=PreviewLeases(sources), private_root=Path(root)),
                lease_seconds=60, failures=ImportFailures(pool))
            assert worked
            print('worked')
        finally:
            await worker.aclose()


if __name__ == '__main__' and len(sys.argv) == 4 and sys.argv[1] == '--k07-worker':
    try:
        _run(_worker_main(sys.argv[2], sys.argv[3]))
    except Exception as error:
        print('K07 worker failed: ' + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
