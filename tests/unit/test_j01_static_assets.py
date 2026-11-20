"""Public static snapshots do not replace hosted authentication or persistence."""
import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from aletheia_lab.hosted.static_assets import StaticAssets

ORIGIN = 'https://app.internal'


@pytest.fixture
def build(tmp_path):
    root = tmp_path / 'build'
    assets = root / 'assets'
    assets.mkdir(parents=True)
    (root / 'index.html').write_bytes(b'<html>built</html>')
    (root / 'theme-init.js').write_bytes(b'public theme')
    (assets / 'app-Abcdef12.js').write_bytes(b'public script')
    (assets / 'app-Abcdef12.css').write_bytes(b'body { color: red; }')
    (assets / 'font-Abcdef12.woff2').write_bytes(b'bundled font')
    return root


async def _unused(scope, receive, send):
    raise AssertionError('A public static request must not reach the private runtime')


def _request(app, path='/', *, method='GET', host=b'app.internal'):
    async def scenario():
        messages = []
        async def receive():
            raise AssertionError('Static responses must not consume request bodies')
        async def send(message):
            messages.append(message)
        headers = [] if host is None else [(b'host', host)]
        await app({'type': 'http', 'path': path, 'method': method, 'headers': headers}, receive, send)
        return messages[0]['status'], dict(messages[0]['headers']), messages[1]['body']
    return asyncio.run(scenario())


@pytest.mark.parametrize('path,media,body', [
    ('/', b'text/html; charset=utf-8', b'<html>built</html>'),
    ('/index.html', b'text/html; charset=utf-8', b'<html>built</html>'),
    ('/theme-init.js', b'text/javascript; charset=utf-8', b'public theme'),
    ('/assets/app-Abcdef12.js', b'text/javascript; charset=utf-8', b'public script'),
    ('/assets/app-Abcdef12.css', b'text/css; charset=utf-8', b'body { color: red; }'),
    ('/assets/font-Abcdef12.woff2', b'font/woff2', b'bundled font'),
])
def test_exact_build_content_headers_and_head(build, path, media, body):
    app = StaticAssets(_unused, build, ORIGIN)
    status, headers, actual = _request(app, path)
    assert status == 200 and actual == body
    assert headers[b'content-type'] == media
    assert headers[b'content-length'] == str(len(body)).encode()
    expected_cache = b'public, max-age=31536000, immutable' if path.startswith('/assets/') else b'no-store'
    assert headers[b'cache-control'] == expected_cache
    assert headers[b'x-content-type-options'] == b'nosniff'
    assert headers[b'referrer-policy'] == b'no-referrer'
    assert b"default-src 'self'" in headers[b'content-security-policy']
    assert b"style-src 'self' 'unsafe-inline'" in headers[b'content-security-policy']
    assert b"font-src 'self' data:" in headers[b'content-security-policy']
    assert b"'unsafe-eval'" not in headers[b'content-security-policy']
    assert b'access-control-allow-origin' not in headers and b'set-cookie' not in headers
    head_status, head_headers, head_body = _request(app, path, method='HEAD')
    assert (head_status, head_headers, head_body) == (status, headers, b'')


def test_snapshot_never_reopens_or_exposes_later_files(build):
    app = StaticAssets(_unused, build, ORIGIN)
    (build / 'assets/app-Abcdef12.js').write_bytes(b'replaced')
    (build / 'index.html').unlink()
    (build / 'assets/extra-Abcdef12.js').write_bytes(b'not in this build snapshot')
    assert _request(app, '/assets/app-Abcdef12.js')[2] == b'public script'
    assert _request(app)[2] == b'<html>built</html>'
    assert _request(app, '/assets/extra-Abcdef12.js')[0] == 404


@pytest.mark.parametrize('host', [None, b'evil.internal', b'app.internal:80', b'app.internal@evil.internal'])
def test_static_requests_use_existing_host_guard(build, host):
    status, headers, body = _request(StaticAssets(_unused, build, ORIGIN), host=host)
    assert status == 400 and body == b'Bad Request'
    assert headers[b'cache-control'] == b'no-store'
    assert b'evil.internal' not in body


@pytest.mark.parametrize('path', [
    '/assets/', '/../outside', '/assets/../index.html', '/%2e%2e/outside',
    '/assets/%2e%2e/index.html', '/assets\\app-Abcdef12.js', '//index.html',
    '/.env', '/private.key', '/package.json', '/source.ts', '/assets/app-Abcdef12.js.map',
    '/assets/private-Abcdef12.html',
    '/assets/private.js', '/assets/.hidden-Abcdef12.js', '/private/hidden-Abcdef12.js',
])
def test_private_unbuilt_and_traversal_paths_are_fixed_404(build, path):
    for name in ('.env', 'private.key', 'package.json', 'source.ts', 'assets/app-Abcdef12.js.map',
                 'assets/private.js', 'assets/.hidden-Abcdef12.js', 'assets/private-Abcdef12.html'):
        (build / name).write_bytes(b'private synthetic content')
    (build / 'private').mkdir()
    (build / 'private/hidden-Abcdef12.js').write_bytes(b'private synthetic content')
    status, headers, body = _request(StaticAssets(_unused, build, ORIGIN), path)
    assert status == 404 and body == b'Not Found'
    assert headers[b'cache-control'] == b'no-store'
    assert path.encode() not in body and b'private synthetic content' not in body


def test_wrong_static_method_never_consumes_body(build):
    status, headers, body = _request(StaticAssets(_unused, build, ORIGIN), method='POST')
    assert status == 405 and body == b'Method Not Allowed'
    assert headers[b'allow'] == b'GET, HEAD'
    assert headers[b'cache-control'] == b'no-store'


@pytest.mark.parametrize('path', ['/v1', '/v1/session', '/v1/auth/callback', '/health', '/health/ready'])
def test_private_routes_preserve_scope_and_request_body(build, path):
    (build / 'v1').mkdir()
    (build / 'v1/session').write_bytes(b'must not shadow authentication')
    async def scenario():
        scope = {'type': 'http', 'path': path, 'method': 'POST', 'headers': []}
        incoming = {'type': 'http.request', 'body': b'private request body'}
        messages = []
        reads = []
        async def receive():
            reads.append(True)
            return incoming
        async def send(message):
            messages.append(message)
        async def runtime(actual_scope, actual_receive, actual_send):
            assert actual_scope is scope and actual_receive is receive and actual_send is send
            assert reads == [] and await actual_receive() is incoming
            await actual_send({'type': 'http.response.start', 'status': 401, 'headers': []})
        await StaticAssets(runtime, build, ORIGIN)(scope, receive, send)
        assert len(reads) == 1 and messages == [{'type': 'http.response.start', 'status': 401, 'headers': []}]
    asyncio.run(scenario())


def test_lifespan_reaches_the_same_runtime_unchanged(build):
    async def scenario():
        scope = {'type': 'lifespan'}
        events = iter([{'type': 'lifespan.startup'}, {'type': 'lifespan.shutdown'}])
        messages = []
        async def receive():
            return next(events)
        async def send(message):
            messages.append(message)
        async def runtime(actual_scope, actual_receive, actual_send):
            assert actual_scope is scope and actual_receive is receive and actual_send is send
            assert (await actual_receive())['type'] == 'lifespan.startup'
            await actual_send({'type': 'lifespan.startup.failed', 'message': 'real dependencies unavailable'})
        await StaticAssets(runtime, build, ORIGIN)(scope, receive, send)
        assert messages == [{'type': 'lifespan.startup.failed', 'message': 'real dependencies unavailable'}]
    asyncio.run(scenario())


@pytest.mark.parametrize('limits', [
    {'maximum_file_bytes': 5}, {'maximum_total_bytes': 20}, {'maximum_entries': 1},
    {'maximum_file_bytes': 0}, {'maximum_total_bytes': True}, {'maximum_entries': -1},
])
def test_snapshot_budgets_fail_closed(build, limits):
    with pytest.raises(ValueError):
        StaticAssets(_unused, build, ORIGIN, **limits)


def test_requires_absolute_build_with_an_index(tmp_path):
    with pytest.raises(ValueError):
        StaticAssets(_unused, Path('relative-build'), ORIGIN)
    with pytest.raises(ValueError):
        StaticAssets(_unused, tmp_path, ORIGIN)
    with pytest.raises(ValueError):
        StaticAssets(_unused, tmp_path / 'missing', ORIGIN)


@pytest.mark.parametrize('root_link', [False, True])
def test_directory_link_or_windows_junction_cannot_escape_build(build, tmp_path, root_link):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'private-Abcdef12.js').write_bytes(b'private bytes')
    link = tmp_path / 'build-alias' if root_link else build / 'assets/alias'
    target = build if root_link else outside
    if os.name == 'nt':
        subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(target)],
                       check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match='unsafe path'):
        StaticAssets(_unused, link if root_link else build, ORIGIN)


def test_file_symlink_cannot_serve_outside_bytes(build, tmp_path):
    outside = tmp_path / 'private.js'
    outside.write_bytes(b'private bytes')
    try:
        (build / 'assets/private-Abcdef12.js').symlink_to(outside)
    except OSError as error:
        if getattr(error, 'winerror', None) == 1314:
            pytest.skip('Windows file symlink creation requires its OS privilege')
        raise
    with pytest.raises(ValueError, match='unsafe path'):
        StaticAssets(_unused, build, ORIGIN)
