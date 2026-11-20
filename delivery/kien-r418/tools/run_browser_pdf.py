"""Use the existing native test composition and hosted UI to verify a real PDF download."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import socket
import ssl
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ('Release-K01-K18-' + os.environ.get('R4_BROWSER_REVISION', 'R4.1')) / 'source'


async def main() -> None:
    started = time.monotonic()
    started_utc = datetime.now(timezone.utc).isoformat()
    import httpx
    import psycopg
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from psycopg import sql
    from pydantic import SecretStr
    from test_k02_https_edge import _certificate
    from test_k06_runtime_privileges_postgres import _role_url
    from test_k17_capacity_postgres import _CHILD, _target

    from aletheia_lab.hosted.action_audit_migration import migrate as migrate_audit
    from aletheia_lab.hosted.capacity_seed import SeedLimits, seed_native
    from aletheia_lab.hosted.capacity_seed_models import SeedPlan
    from aletheia_lab.hosted.runtime_privileges import grant_runtime
    from aletheia_lab.hosted.preview_migration import migrate as migrate_preview
    from aletheia_lab.hosted.preview_runtime_privileges import grant_preview_runtime
    from aletheia_lab.hosted.upload_staging_migration import migrate as migrate_upload
    from aletheia_lab.hosted_auth.sessions import credential_digest

    output = ROOT / 'evidence' / ('browser-api-pdf' + (sys.argv[1] if len(sys.argv) == 2 else ''))
    output.mkdir()
    downloads = output / 'download'
    downloads.mkdir()
    private = ROOT / '.private' / ('browser-' + uuid4().hex)
    private.mkdir()
    full = os.environ.get('R4_BROWSER_UPLOAD') == '1'
    role, password, csrf = 'k17_browser_' + uuid4().hex, secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    worker_role, worker_password = 'k17_browser_worker_' + uuid4().hex, secrets.token_urlsafe(32)
    database = os.environ['K17_TEST_DATABASE_URL']
    manifest = json.loads((SOURCE.parent / 'source-manifest.json').read_bytes())
    port, debug = [], []
    for target in (port, debug):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            target.append(listener.getsockname()[1])
    origin = f'https://127.0.0.1:{port[0]}'
    cert, key = _certificate(private)
    pin = base64.b64encode(hashlib.sha256(x509.load_pem_x509_certificate(cert.read_bytes()).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).digest()).decode()
    driver = private / 'cdp.mjs'
    session_check = os.environ.get('R4_BROWSER_SESSION_CHECK') == '1'
    if session_check and not full:
        raise ValueError('Browser session checks need a real result from the full upload journey.')
    original = (SOURCE / 'dashboard/web/tests/e2e/cdp.mjs').read_text(encoding='utf-8')
    assert original.count('["--headless=new",') == 1
    original = original.replace('["--headless=new",', '["--headless=new", "--ignore-certificate-errors-spki-list=' + pin + '",')
    driver.write_text(original.replace('maxRetries: 5, retryDelay: 100', 'maxRetries: 20, retryDelay: 250'), encoding='utf-8')
    static_label = os.environ.get('R4_BROWSER_STATIC_LABEL', '')
    if static_label and (not static_label.isascii() or not static_label.isalnum()):
        raise ValueError('Bounded static evidence label required.')
    static = ROOT / ('evidence/ui-builds/hosted-' + static_label if static_label else 'evidence/ui-builds/hosted')
    assets = json.loads((ROOT / ('evidence/ui-hosted-build-manifest-' + static_label + '.json' if static_label else 'evidence/ui-hosted-build-manifest.json')).read_bytes())
    assert all(hashlib.sha256((static / name).read_bytes()).hexdigest() == digest for name, digest in assets.items())
    child_source = _CHILD.replace('server=uvicorn.Server(uvicorn.Config(ObservedApplication(composed),**options))', '''from aletheia_lab.hosted.static_assets import StaticAssets
            async def witness(scope, receive, send):
                collect = scope.get('method') == 'POST' and scope.get('path', '').endswith('/exports')
                status, data = None, bytearray()
                async def record(message):
                    nonlocal status
                    if collect and message['type'] == 'http.response.start': status = message['status']
                    if status == 201 and message['type'] == 'http.response.body':
                        data.extend(message.get('body', b''))
                        if not message.get('more_body', False):
                            value = json.loads(data)
                            Path(os.environ['R4_PDF_DESCRIPTOR']).write_text(json.dumps({k:value[k] for k in ('result_id','byte_size','sha256')}))
                    await send(message)
                await composed(scope, receive, record)
            server=uvicorn.Server(uvicorn.Config(StaticAssets(ObservedApplication(witness),Path(os.environ['R4_STATIC']),settings.public_origin),**options))''')
    assert child_source != _CHILD
    child_source = child_source.replace('composed=compose_upload_runtime(',
        'from aletheia_lab.hosted.workspace_management import WorkspaceManagement\n        composed=compose_upload_runtime(membership_management=WorkspaceManagement(pool,cursors=cursors,audit=True),')
    if full:
        child_source = child_source.replace('membership_management=WorkspaceManagement(', 'resumable_uploads=True,membership_management=WorkspaceManagement(')
        child_source = child_source.replace('from aletheia_lab.hosted.payload_upload_worker import PayloadUploadWorkerSupervisor',
            'from aletheia_lab.hosted.managed_worker import _supervisor_type\n            from aletheia_lab.hosted.preview_leases import PreviewLeases')
        child_source = child_source.replace('loop.add_signal_handler(signal.SIGTERM,stop.set)',
            'if sys.platform != "win32": loop.add_signal_handler(signal.SIGTERM,stop.set)')
        child_source = child_source.replace('supervisor=PayloadUploadWorkerSupervisor(', 'supervisor=_supervisor_type()(')
        child_source = child_source.replace('CanonicalProjectOperations(sources,supervisor,jobs,private_root=', 'CanonicalProjectOperations(sources,supervisor,jobs,leases=PreviewLeases(sources),private_root=')
        child_source = child_source.replace('owner=configure_process_observability(', '''from aletheia_lab.hosted import upload_job_worker as diagnostic_worker
original_run_claim=diagnostic_worker._run_claim
async def observed_claim(*args,**kwargs):
    try:
        return await original_run_claim(*args,**kwargs)
    except Exception as error:
        import traceback
        frames=[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(error.__traceback__)]
        Path(os.environ['R4_JOB_DIAGNOSTIC']).write_text(json.dumps({'exception_type':type(error).__name__,'sqlstate':getattr(error,'sqlstate',None),'frames':frames}))
        raise
diagnostic_worker._run_claim=observed_claim
owner=configure_process_observability(''')
    async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
        await admin.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD {}').format(sql.Identifier(role), sql.Literal(password)))
        try:
            if full:
                await admin.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD {}').format(sql.Identifier(worker_role), sql.Literal(worker_password)))
            async with _target() as (pool, schema):
                seed = private / 'seed'
                await seed_native(pool, schema=schema, output=seed, plan=SeedPlan(namespace='k17_browserr4',
                    generations=1, results_per_generation=1), csrf_secret=SecretStr(csrf),
                    limits=SeedLimits(500*1024**2, 100000, 100, maximum_seconds=180))
                async with pool.connection() as conn:
                    await migrate_audit(conn, schema)
                    await conn.execute((SOURCE / 'src/aletheia_lab/hosted_auth/admission_schema.sql').read_text(encoding='utf-8'))
                    if full:
                        await migrate_upload(conn, schema)
                        await migrate_preview(conn, schema)
                        await grant_preview_runtime(conn, schema, role, profile='api', audit=True)
                        await grant_preview_runtime(conn, schema, worker_role, profile='worker', audit=True)
                    else:
                        await grant_runtime(conn, schema, role, profile='api', audit=True)
                actors = [json.loads(line) for line in (seed / 'actors.jsonl').read_text(encoding='utf-8').splitlines()]
                actor = next(a for a in actors if a['role'] == 'analyst' and a['size'] == 'S')
                job_id = 'job_' + uuid4().hex
                values = actor['values']
                if full:
                    async with pool.connection() as conn:
                        await conn.execute("UPDATE j01_workspaces SET name='R4 browser acceptance workspace' WHERE workspace_id=%s", (values['workspace_id'],))
                    (seed / 'worker-private').mkdir(exist_ok=True)
                token = actor['cookie'].split('; ', 1)[0].split('=', 1)[1]
                digest = credential_digest(SecretStr(token)).get_secret_value()
                async with pool.connection() as conn:
                    await conn.execute("INSERT INTO j01_jobs(job_id, workspace_id, creator_id, credential_digest, operation, "
                        "key_hash, request_hash, request, request_id, state, result) VALUES (%s,%s,%s,%s,'analyze',%s,%s,%s,%s,'succeeded','{}')",
                        (job_id, values['workspace_id'], actor['actor_id'], digest, 'a'*64, 'b'*64,
                         psycopg.types.json.Jsonb({'snapshot_id':values['snapshot_id'], 'question':'Synthetic PDF fixture'}), 'req_'+uuid4().hex))
                config = private / 'api.json'
                config.write_text(json.dumps({'environment':'local','public_origin':origin,'identity':{
                    'issuer':'https://issuer.example.invalid','client_id':'synthetic','redirect_uri':origin+'/v1/auth/callback','client_kind':'public'},
                    'storage':{'database_url_env':'ALETHEIA_HOSTED_DATABASE_URL','object_root':str(seed),'expected_schema':schema,'expected_database_role':role},
                    'api':{'port':port[0],'database_pool_size':4}}),encoding='utf-8')
                child_source = child_source.replace('asyncio.run(main())', 'with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform=="win32" else None) as runner:\n        runner.run(main())')
                env = {key: os.environ[key] for key in ('SYSTEMROOT','WINDIR','TEMP','TMP') if key in os.environ}
                env.update(PYTHONPATH=str(SOURCE/'src'),PYTHONDONTWRITEBYTECODE='1',ALETHEIA_RELEASE_ID=manifest['source_identity'],
                    ALETHEIA_HOSTED_CSRF_SECRET=csrf,ALETHEIA_HOSTED_DATABASE_URL=_role_url(database,schema,role,password),
                    R4_STATIC=str(static),R4_PDF_DESCRIPTOR=str(output/'descriptor.json'),R4_JOB_DIAGNOSTIC=str(output/'job-exception.safe.json'))
                with (output/'api.safe.log').open('wb') as log:
                    child = subprocess.Popen([sys.executable,'-B','-c',child_source,'api',str(config),str(cert),str(key)],cwd=SOURCE,env=env,stdout=log,stderr=log)
                    worker = None
                    try:
                        if full:
                            worker_config = private / 'worker.json'
                            settings = json.loads(config.read_bytes())
                            settings['storage']['expected_database_role'] = worker_role
                            worker_config.write_text(json.dumps(settings),encoding='utf-8')
                            worker_env = dict(env,ALETHEIA_HOSTED_DATABASE_URL=_role_url(database,schema,worker_role,worker_password))
                            worker = subprocess.Popen([sys.executable,'-B','-c',child_source,'worker',str(worker_config),str(cert),str(key)],cwd=SOURCE,env=worker_env,stdout=log,stderr=log)
                        async with httpx.AsyncClient(verify=ssl.create_default_context(cafile=str(cert)),trust_env=False,timeout=2) as http:
                            async with asyncio.timeout(45):
                                while child.poll() is None:
                                    try:
                                        if (await http.get(origin+'/health/ready')).status_code == 200:
                                            break
                                    except httpx.TransportError:
                                        pass
                                    await asyncio.sleep(.2)
                                else:
                                    raise RuntimeError('Native browser API exited before readiness.')
                        node_env = dict(os.environ, TEMP=str(ROOT/'tmp'),TMP=str(ROOT/'tmp'),
                            CHROME_PATH=r'C:\Program Files\Google\Chrome\Application\chrome.exe')
                        spec = {'origin':origin,'debug_port':debug[0],'cookie':actor['cookie'],'values':values,'job':job_id}
                        if full:
                            spec.update(upload_file=str(SOURCE/'fixtures/metric-dimensions-hosted-v2/source.zip'),workspace_name='R4 browser acceptance workspace')
                            spec['repeat_uploads'] = int(os.environ.get('R4_BROWSER_REPEAT_UPLOADS', '1'))
                            assert 1 <= spec['repeat_uploads'] <= 3
                        if session_check:
                            spec['session_checks_file'] = str(output / 'session-cases.json')
                        with (output/'chrome.log').open('wb') as chrome_log:
                            result = await asyncio.to_thread(subprocess.run,[os.environ['R4_NODE'],str(ROOT/'tools/browser_pdf.mjs'),str(driver),str(downloads),str(output/'download.json')],
                                input=json.dumps(spec).encode(),env=node_env,stdout=chrome_log,stderr=chrome_log,timeout=250 if full else 80)
                        assert result.returncode == 0, 'Chrome check failed; inspect chrome.log'
                        if session_check:
                            async with httpx.AsyncClient(verify=ssl.create_default_context(cafile=str(cert)),
                                                         trust_env=False, timeout=5) as http:
                                denied = await http.get(origin+'/v1/session', headers={'cookie':actor['cookie']})
                            assert denied.status_code == 401, 'The old session credential must fail after logout.'
                            session_path = output / 'session-cases.json'
                            observation = json.loads(session_path.read_bytes())
                            observation['previous_credential_replayed_http'] = denied.status_code
                            session_path.write_text(json.dumps(observation, indent=2), encoding='utf-8')
                        actual = json.loads((output/'download.json').read_bytes())
                        descriptor = json.loads((output/'descriptor.json').read_bytes())
                        assert actual['bytes'] == descriptor['byte_size'] and actual['sha256'] == descriptor['sha256'] and actual['result_id'] == descriptor['result_id']
                        (output/'result.json').write_text(json.dumps({**actual,'source_identity':manifest['source_identity'],
                            'runtime_identity':manifest['runtime_identity'],'qualified':True,'worker_started':full,
                            'started_utc':started_utc,'elapsed_seconds':time.monotonic()-started,
                            'recipe':{'api_pool_max':4,'worker_pool_max':2,'worker_slots':2,'lease_seconds':120,
                                'worker_timeout_seconds':90,'browser_process_timeout_seconds':250 if full else 80,
                                'zip_bytes':(SOURCE/'fixtures/metric-dimensions-hosted-v2/source.zip').stat().st_size if full else None,
                                'ui_manifest_sha256':hashlib.sha256((ROOT/('evidence/ui-hosted-build-manifest-'+static_label+'.json' if static_label else 'evidence/ui-hosted-build-manifest.json')).read_bytes()).hexdigest(),
                                'tls':'loopback certificate SPKI pin','session':'synthetically seeded; no IdP login',
                                'optional_browser_session_checks':session_check,
                                'full_release_or_primary50k_pass':False},
                            'scope':'Chrome hosted UI, verified loopback TLS, native PostgreSQL seeded session, actual PDF renderer and full descriptor comparison'},indent=2),encoding='utf-8')
                    finally:
                        for process in (child,worker):
                            if process is not None and process.poll() is None:
                                process.terminate()
                                await asyncio.to_thread(process.wait, 15)
        finally:
            await admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
            if full:
                await admin.execute(sql.SQL('DROP ROLE IF EXISTS {}').format(sql.Identifier(worker_role)))


if __name__ == '__main__':
    sys.path[:0] = [str(SOURCE / part) for part in ('src','tests/integration','tests/unit')]
    os.environ['K17_TEST_DATABASE_URL'] = (ROOT/'.private/database-url.txt').read_text().strip()
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == 'win32' else None) as runner:
        runner.run(main())
