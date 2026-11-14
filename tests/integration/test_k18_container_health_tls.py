"""Actual verified TLS/SNI/Host for the container probe, without Docker or IdP."""
from __future__ import annotations

import asyncio
import os
import socket
import ssl
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from aletheia_lab.hosted.composition import ProcessApplication
from aletheia_lab.hosted.container_health import probe
from aletheia_lab.hosted.runtime import api_server_options
from aletheia_lab.hosted.settings import load_runtime_settings

pytestmark = [pytest.mark.integration, pytest.mark.skipif(os.environ.get('K18_TEST_HTTPS') != '1',
    reason='Explicit native loopback K18 TLS transport opt-in required')]


def _certificate(folder):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    folder.mkdir()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Synthetic K18 health TLS')])
    now = datetime.now(UTC)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('app.example.invalid')]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).sign(key, hashes.SHA256()))
    cert, private = folder / 'certificate.pem', folder / 'private-key.pem'
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    private.chmod(0o600)
    return cert, private


def test_native_container_health_verifies_public_sni_host_ca_and_current_readiness(tmp_path):
    import uvicorn

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    config_file = tmp_path / 'api.toml'
    template = ('environment="local"\npublic_origin="https://{host}:8443"\n'
        '[identity]\nissuer="https://issuer.example.invalid"\nclient_id="synthetic"\n'
        'redirect_uri="https://{host}:8443/callback"\nclient_kind="public"\n'
        '[storage]\ndatabase_url_env="ALETHEIA_HOSTED_DATABASE_URL"\nobject_root="' + str(tmp_path / 'objects') + '"\n'
        '[api]\nbind_host="127.0.0.1"\nport=' + str(port) + '\n')
    config_file.write_text(template.format(host='app.example.invalid'))
    settings = load_runtime_settings(config_file)
    cert, key = _certificate(tmp_path / 'trusted')
    other_cert, other_key = _certificate(tmp_path / 'untrusted')
    state, names, hosts = {'ready': True}, [], []

    class Backend:
        async def is_ready(self):
            return state['ready']
        async def load_session(self, cookie):
            raise AssertionError('Health must not authenticate.')
        async def capabilities(self, session, workspace_id):
            raise AssertionError('Health must not read workspace data.')

    @asynccontextmanager
    async def resources():
        yield Backend()

    def routes(fallback):
        async def observed(scope, receive, send):
            if scope.get('type') == 'http':
                hosts.append(dict(scope['headers'])[b'host'])
            await fallback(scope, receive, send)
        return observed

    app = ProcessApplication(settings, resources, routes=routes)
    options = api_server_options(settings)
    options.update(ssl_certfile=str(cert), ssl_keyfile=str(key), log_config=None, log_level='error')
    config = uvicorn.Config(app, **options)
    config.load()
    config.ssl.set_servername_callback(lambda secured, name, context: names.append(name))
    server = uvicorn.Server(config)
    thread = threading.Thread(target=lambda: asyncio.run(server.serve(sockets=[listener])), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started and thread.is_alive()
        assert probe(config_file, cert) is True
        state['ready'] = False
        assert probe(config_file, cert) is False
        assert probe(config_file, cert, ready=False) is True
        assert names == ['app.example.invalid'] * 3
        assert hosts == [b'app.example.invalid:8443'] * 3
        with pytest.raises(ssl.SSLCertVerificationError):
            probe(config_file, other_cert)
        config_file.write_text(template.format(host='wrong.example.invalid'))
        with pytest.raises(ssl.SSLCertVerificationError):
            probe(config_file, cert)
    finally:
        server.should_exit = True
        thread.join(5)
        listener.close()
        key.unlink(missing_ok=True)
        other_key.unlink(missing_ok=True)
    assert not thread.is_alive() and not key.exists() and not other_key.exists()
