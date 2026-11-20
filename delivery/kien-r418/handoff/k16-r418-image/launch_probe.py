"""Run both real entrypoints against an owned local PostgreSQL fixture."""
import base64
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import quote, urlsplit
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import httpx2
import psycopg
from psycopg import sql


def certificate(folder):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'K16 local fixture')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
            .not_valid_after(now+timedelta(hours=1)).add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), False)
            .sign(key, hashes.SHA256()))
    (folder/'tls_cert').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    path = folder/'tls_key'
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    path.chmod(0o400)
    return key.public_key().public_numbers()


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(12)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)


def main():
    started = time.monotonic()
    assert os.getuid() == 10001
    parsed = urlsplit(Path('/fixture-dsn').read_text(encoding='utf-8-sig').strip())
    assert parsed.hostname == '127.0.0.1' and parsed.port == 59080
    target = parsed._replace(netloc=parsed.netloc.replace('@127.0.0.1:', '@host.docker.internal:')).geturl()
    suffix = uuid4().hex[:16]
    schema = 'k16_' + suffix
    roles = {mode: 'k16_' + mode + '_' + suffix for mode in ('api', 'worker')}
    with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
        root = Path(temp)
        secret_dir = root/'secrets'
        secret_dir.mkdir(mode=0o700)
        pub = certificate(secret_dir)
        ca = secret_dir/'tls_cert'
        (root/'idp-ca.pem').write_bytes(ca.read_bytes())
        b64 = lambda n: base64.urlsafe_b64encode(n.to_bytes((n.bit_length()+7)//8, 'big')).decode().rstrip('=')
        class Discovery(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                origin = f'https://127.0.0.1:{self.server.server_port}'
                if self.path == '/.well-known/openid-configuration':
                    body = dict(issuer=origin, authorization_endpoint=origin+'/authorize', token_endpoint=origin+'/token',
                                jwks_uri=origin+'/keys', code_challenge_methods_supported=['S256'],
                                authorization_response_iss_parameter_supported=True)
                elif self.path == '/keys':
                    body = {'keys': [dict(kty='RSA', n=b64(pub.n), e=b64(pub.e), kid='local', use='sig', alg='RS256')]}
                else:
                    self.send_error(404)
                    return
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        idp = stack.enter_context(ThreadingHTTPServer(('127.0.0.1', 0), Discovery))
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(ca, secret_dir/'tls_key')
        idp.socket = tls.wrap_socket(idp.socket, server_side=True)
        threading.Thread(target=idp.serve_forever, daemon=True).start()
        stack.callback(idp.shutdown)
        admin = stack.enter_context(psycopg.connect(target, autocommit=True, connect_timeout=5))
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        stack.callback(lambda: admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema))))
        config = Path('/templates')
        owner_file = root/'owner.txt'
        owner_file.write_text(target)
        owner_file.chmod(0o400)
        def migration(action, *extra):
            subprocess.run([sys.executable, '-B', '/scripts/k18_schema_admin.py', action,
                '--database-file', str(owner_file), '--schema', schema, *extra],
                check=True, capture_output=True, timeout=90)
        migration('migrate')
        from importlib.resources import files
        with admin.transaction():
            admin.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
            admin.execute(files('aletheia_lab.hosted_auth').joinpath('admission_schema.sql').read_text())
        public = 'https://127.0.0.1:18766'
        for role, name in roles.items():
            pw = secrets.token_urlsafe(32)
            admin.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD {}').format(
                sql.Identifier(name), sql.Literal(pw)))
            stack.callback(lambda name=name: admin.execute(sql.SQL('DROP OWNED BY {}; DROP ROLE {}').format(
                sql.Identifier(name), sql.Identifier(name))))
            migration('grant', '--role', name, '--profile', role)
            dsn = f'postgresql://{name}:{quote(pw,safe="")}@host.docker.internal:59080{parsed.path}?options=-csearch_path%3D{schema}'
            (secret_dir/('database_'+role)).write_text(dsn)
            content = (config/(role+'.toml')).read_text().replace('environment = "staging"', 'environment = "local"')
            for key, value in dict(public_origin=public, issuer=f'https://127.0.0.1:{idp.server_port}', client_id='k16-local',
                    redirect_uri=public+'/v1/auth/callback', client_kind='public', expected_schema=schema,
                    expected_database_role=name).items():
                import re
                content = re.sub(rf'^{key} = ".*"$', f'{key} = "{value}"', content, flags=re.M)
            (root/(role+'.toml')).write_text(content)
        auth = (config/'auth.toml').read_text().replace('default_return_to = ""', 'default_return_to = "/app"').replace(
            'return_to_paths = []', 'return_to_paths = ["/app"]').replace('admission_namespace = ""', f'admission_namespace = "{schema}"')
        (root/'auth.toml').write_text(auth)
        for name in ('csrf_secret', 'admission_secret', 'login_proof_key'):
            (secret_dir/name).write_text(base64.b64encode(secrets.token_bytes(32)).decode() if name=='login_proof_key' else secrets.token_urlsafe(48))
        for path in secret_dir.iterdir():
            path.chmod(0o400)
        (Path('/var/lib/aletheia')/'volume-probe').write_bytes(b'K16')
        assert (Path('/var/lib/aletheia')/'volume-probe').read_bytes() == b'K16'
        processes = {}
        for role in roles:
            log = stack.enter_context((root/(role+'.log')).open('wb'))
            process = subprocess.Popen([sys.executable,'-B','-m','aletheia_lab.hosted.container_entrypoint',role,
                '--config-dir',str(root),'--secret-dir',str(secret_dir)], stdout=log, stderr=log)
            stack.callback(stop, process)
            processes[role] = process
        with httpx2.Client(verify=ssl.create_default_context(cafile=str(ca)), trust_env=False, timeout=2) as client:
            for _ in range(100):
                if any(p.poll() is not None for p in processes.values()):
                    for role in processes:
                        for line in (root/(role+'.log')).read_text().splitlines():
                            if 'stage.execution' not in line:
                                print(line, file=sys.stderr)
                    raise RuntimeError('Entrypoint exited before readiness')
                try:
                    response = client.get(public+'/health/ready')
                    if response.status_code == 200:
                        break
                except httpx2.TransportError:
                    pass
                time.sleep(.2)
            else:
                raise RuntimeError('API not ready')
            denied = client.get(public+'/health/ready', headers={'Host': 'other.example.invalid'})
            assert denied.status_code == 400
            assert client.get(public+'/v1/session').status_code == 401
            assert client.get(public+'/health/live').status_code == 200
        for _ in range(40):
            active = admin.execute('SELECT count(*) FROM pg_stat_activity WHERE usename=%s', (roles['worker'],)).fetchone()[0]
            if active:
                break
            time.sleep(.25)
        assert active and processes['worker'].poll() is None
        print(json.dumps(dict(status='pass', release=os.environ['ALETHEIA_RELEASE_ID'], elapsed_seconds=time.monotonic()-started,
            api_ready=200, api_live=200, invalid_host=400, anonymous_session=401, worker_database_connections=active,
            volume_uid10001_read_write=True, fixture_idp='TLS discovery and JWKS only', postgres='owned synthetic fixture',
            deployment_gate_pass=False)))


if __name__ == '__main__':
    main()
