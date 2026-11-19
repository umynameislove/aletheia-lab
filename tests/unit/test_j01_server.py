import asyncio
import sys

import pytest
import uvicorn
from test_k02_runtime_lifecycle import _settings

from aletheia_lab.hosted import server


@pytest.mark.parametrize('worker_processes', [1, 2])
def test_server_uses_explicit_tls_and_starts(tmp_path, monkeypatch, worker_processes):
    files = [tmp_path / name for name in ('runtime.toml','auth.toml','cert.pem','key.pem')]
    for path in files:
        path.write_text('synthetic', encoding='utf-8')
    observed = {}
    class Runtime:
        def __init__(self, environment):
            assert environment['ALETHEIA_HOSTED_CONFIG'] == str(files[0])
            self._settings = _settings(tmp_path)
            self._settings = self._settings.model_copy(update={
                'worker': self._settings.worker.model_copy(update={'processes': worker_processes})})
    class Config:
        def __init__(self, app, **kwargs):
            observed.update(kwargs)
    class Server:
        started = False
        def __init__(self, config):
            pass
        async def serve(self):
            assert asyncio.get_running_loop() is not None
            self.started = True
    monkeypatch.setattr(server, 'ManagedRuntime', Runtime)
    monkeypatch.setattr(uvicorn, 'Config', Config)
    monkeypatch.setattr(uvicorn, 'Server', Server)
    monkeypatch.setattr(sys, 'argv', ['server','--config',str(files[0]),'--auth-policy',str(files[1]),
                                      '--tls-cert',str(files[2]),'--tls-key',str(files[3])])
    assert server.main()==0
    assert observed['ssl_certfile']==str(files[2]) and observed['ssl_keyfile']==str(files[3])
    assert observed['proxy_headers'] is False
    assert observed['workers'] == 1
    assert observed['http']._header_timeout_seconds == _settings(tmp_path).api.header_timeout_seconds
