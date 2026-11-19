"""Server CLI wraps only an explicitly supplied public build directory."""

import json
import sys
from types import SimpleNamespace

import pytest
from test_k02_runtime_lifecycle import _settings

from aletheia_lab.hosted import server
from aletheia_lab.hosted.static_assets import StaticAssets


async def _http(app, path, *, host=b"app.internal"):
    messages = []

    async def receive():
        raise AssertionError("GET must not read a body")

    async def send(message):
        messages.append(message)

    await app({"type": "http", "method": "GET", "scheme": "https", "path": path,
               "headers": [(b"host", host)]}, receive, send)
    return messages


@pytest.fixture
def server_case(tmp_path, monkeypatch):
    files = [tmp_path / name for name in ("runtime.toml", "auth.toml", "cert.pem", "key.pem")]
    for path in files:
        path.write_text("synthetic", encoding="utf-8")
    case = SimpleNamespace(app=None, runtime=None, options={}, forwarded=[], checks=None, constructed=False)

    class Runtime:
        def __init__(self, environment):
            assert environment["ALETHEIA_HOSTED_CONFIG"] == str(files[0])
            self._settings = _settings(tmp_path)
            case.runtime = self

        async def __call__(self, scope, receive, send):
            case.forwarded.append((scope["type"], scope.get("path")))
            if scope["type"] == "lifespan":
                assert (await receive())["type"] == "lifespan.startup"
                await send({"type": "lifespan.startup.complete"})
                assert (await receive())["type"] == "lifespan.shutdown"
                await send({"type": "lifespan.shutdown.complete"})
                return
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": b"runtime"})

    class Config:
        def __init__(self, application, **options):
            case.app, case.options = application, options

    class Server:
        started = False

        def __init__(self, config):
            case.constructed = True

        async def serve(self):
            if case.checks is not None:
                await case.checks(case.app)
            self.started = True

    case.files = files
    case.argv = ["server", "--config", str(files[0]), "--auth-policy", str(files[1]),
                 "--tls-cert", str(files[2]), "--tls-key", str(files[3])]
    # Load the actual protocol dependency before replacing only server objects;
    # this fixture must also work when run without another test importing it.
    server.api_server_options(_settings(tmp_path))
    monkeypatch.setattr(server, "ManagedRuntime", Runtime)
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(Config=Config, Server=Server))
    monkeypatch.setattr(sys, "argv", case.argv)
    return case


def test_explicit_static_build_serves_index_and_preserves_runtime_routes(tmp_path, server_case):
    build = tmp_path / "public-build"
    build.mkdir()
    (build / "index.html").write_bytes(b"<h1>Synthetic public build</h1>")
    case = server_case
    case.argv.extend(["--static-root", str(build)])

    async def check(app):
        assert isinstance(app, StaticAssets) and app._application is case.runtime
        start, body = await _http(app, "/")
        assert start["status"] == 200 and body["body"] == b"<h1>Synthetic public build</h1>"
        headers = dict(start["headers"])
        assert headers[b"cache-control"] == b"no-store"
        assert b"default-src 'self'" in headers[b"content-security-policy"]
        assert (await _http(app, "/", host=b"forged.invalid"))[0]["status"] == 400
        for path in ("/v1/session", "/health/ready"):
            assert (await _http(app, path))[1]["body"] == b"runtime"
        incoming = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
        output = []

        async def receive():
            return next(incoming)

        async def send(message):
            output.append(message["type"])

        await app({"type": "lifespan"}, receive, send)
        assert output == ["lifespan.startup.complete", "lifespan.shutdown.complete"]

    case.checks = check
    assert server.main() == 0
    assert case.forwarded == [("http", "/v1/session"), ("http", "/health/ready"), ("lifespan", None)]
    assert case.options["ssl_certfile"] == str(case.files[2])
    assert case.options["ssl_keyfile"] == str(case.files[3])
    assert case.options["proxy_headers"] is False and case.options["access_log"] is False


def test_without_static_root_keeps_runtime_instance(server_case):
    assert server.main() == 0
    assert server_case.app is server_case.runtime and server_case.constructed


@pytest.mark.parametrize("kind", ["relative", "missing", "no-index"])
def test_invalid_static_root_stops_before_server_construction(tmp_path, server_case, capsys, kind):
    root = "relative-private-build" if kind == "relative" else str(tmp_path / "private-build")
    if kind == "no-index":
        (tmp_path / "private-build").mkdir()
    server_case.argv.extend(["--static-root", root])
    assert server.main() == 1
    assert not server_case.constructed
    output = capsys.readouterr().err
    lines = output.splitlines()
    assert lines[-1] == "Runtime could not start."
    for line in lines[:-1]:
        metric = json.loads(line)
        assert metric['schema_version'] == 'hosted-telemetry/v1'
        assert metric['event'] == 'metric.snapshot' and metric['role'] == 'api'
    assert root not in output and str(server_case.files[3]) not in output
