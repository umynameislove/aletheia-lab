"""Serve an explicit public build snapshot beside the unchanged hosted runtime."""
from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path

from .application import Send
from .edge import Application, EdgeGuard
from .request_boundary import Receive

_MIME = {'.html': b'text/html; charset=utf-8', '.js': b'text/javascript; charset=utf-8',
         '.css': b'text/css; charset=utf-8', '.woff2': b'font/woff2', '.woff': b'font/woff',
         '.ttf': b'font/ttf', '.otf': b'font/otf', '.png': b'image/png', '.jpg': b'image/jpeg',
         '.jpeg': b'image/jpeg', '.svg': b'image/svg+xml', '.webp': b'image/webp',
         '.gif': b'image/gif', '.ico': b'image/x-icon', '.avif': b'image/avif'}
_HASHED = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*-[A-Za-z0-9_-]{8,}\.[a-z0-9]+')
_CSP = (b"default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        b"font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
Asset = tuple[bytes, bytes, bytes]


def _safe_stat(path: Path) -> os.stat_result:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError('Static build contains an unsafe path.')
    return info


def _asset(path: Path, root: Path, maximum: int) -> Asset | None:
    relative = path.relative_to(root).as_posix()
    media = _MIME.get(path.suffix)
    hashed = relative.startswith('assets/') and path.suffix != '.html' and _HASHED.fullmatch(path.name) is not None
    if media is None or (relative not in ('index.html', 'theme-init.js') and not hashed):
        return None
    if not path.resolve(strict=True).is_relative_to(root):
        raise ValueError('Static build contains an unsafe path.')
    with path.open('rb') as stream:
        body = stream.read(maximum + 1)
    if len(body) > maximum:
        raise ValueError('Static build exceeds its byte budget.')
    cache = b'public, max-age=31536000, immutable' if hashed else b'no-store'
    return body, media, cache


def _snapshot(root: Path, file_bytes: int, total_bytes: int, maximum_entries: int) -> dict[str, Asset]:
    pending, assets, entries, total = [root], {}, 0, 0
    while pending:
        with os.scandir(pending.pop()) as directory:
            for entry in directory:
                entries += 1
                if entries > maximum_entries:
                    raise ValueError('Static build exceeds its entry budget.')
                path = Path(entry.path)
                info = _safe_stat(path)
                if entry.name.startswith('.'):
                    continue
                relative = path.relative_to(root).as_posix()
                if stat.S_ISDIR(info.st_mode):
                    if relative == 'assets' or relative.startswith('assets/'):
                        pending.append(path)
                elif stat.S_ISREG(info.st_mode):
                    asset = _asset(path, root, min(file_bytes, total_bytes - total))
                    if asset is not None:
                        total += len(asset[0])
                        assets['/' + relative] = asset
    return assets


class StaticAssets:
    def __init__(self, application: Application, build_root: Path, public_origin: str, *,
                 maximum_file_bytes: int = 16 * 1024 * 1024,
                 maximum_total_bytes: int = 64 * 1024 * 1024, maximum_entries: int = 2000) -> None:
        if any(type(value) is not int or value <= 0 for value in
               (maximum_file_bytes, maximum_total_bytes, maximum_entries)):
            raise ValueError('Static build budgets must be positive integers.')
        try:
            root = Path(build_root)
            if not root.is_absolute():
                raise ValueError('An absolute static build root is required.')
            for ancestor in (root, *root.parents):
                _safe_stat(ancestor)
            if not root.is_dir():
                raise ValueError('Static build root must be a directory.')
            # Snapshot the operator-controlled build once; requests never reopen files.
            self._assets = _snapshot(root.resolve(strict=True), maximum_file_bytes,
                                     maximum_total_bytes, maximum_entries)
        except OSError:
            raise ValueError('Static build is unavailable.') from None
        if '/index.html' not in self._assets:
            raise ValueError('Static index is unavailable.')
        self._assets['/'] = self._assets['/index.html']
        self._application = application
        self._edge = EdgeGuard(public_origin, self._serve)

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        path = str(scope.get('path', ''))
        if scope.get('type') != 'http' or path in ('/v1', '/health') or path.startswith(('/v1/', '/health/')):
            await self._application(scope, receive, send)
        else:
            await self._edge(scope, receive, send)

    async def _serve(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        asset = self._assets.get(str(scope.get('path', '')))
        status = 200 if asset is not None else 404
        body, media, cache = asset or (b'Not Found', b'text/plain; charset=utf-8', b'no-store')
        if scope.get('method') not in ('GET', 'HEAD'):
            status, body, media, cache = 405, b'Method Not Allowed', b'text/plain; charset=utf-8', b'no-store'
        headers = [(b'content-type', media), (b'content-length', str(len(body)).encode('ascii')),
                   (b'cache-control', cache), (b'content-security-policy', _CSP),
                   (b'x-content-type-options', b'nosniff'), (b'referrer-policy', b'no-referrer')]
        if status == 405:
            headers.append((b'allow', b'GET, HEAD'))
        await send({'type': 'http.response.start', 'status': status, 'headers': headers})
        await send({'type': 'http.response.body', 'body': b'' if scope.get('method') == 'HEAD' else body})
