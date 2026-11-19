"""Check this API instance through verified TLS with the configured public SNI/Host."""
from __future__ import annotations

import argparse
import http.client
import json
import socket
import ssl
from pathlib import Path
from urllib.parse import urlsplit

from .settings import load_runtime_settings


def probe(config: Path, ca_file: Path, *, ready: bool = True) -> bool:
    settings = load_runtime_settings(config)
    public = urlsplit(settings.public_origin)
    if public.scheme != 'https' or public.hostname is None:
        return False
    context = ssl.create_default_context(cafile=str(ca_file))
    with socket.create_connection(('127.0.0.1', settings.api.port), timeout=3) as raw, \
            context.wrap_socket(raw, server_hostname=public.hostname) as secured:
        path = '/health/ready' if ready else '/health/live'
        request = f'GET {path} HTTP/1.1\r\nHost: {public.netloc}\r\nConnection: close\r\n\r\n'
        secured.sendall(request.encode('ascii'))
        response = http.client.HTTPResponse(secured)
        response.begin()
        body = response.read(4097)
        return response.status == 200 and len(body) <= 4096 and json.loads(body)['status'] == ('ready' if ready else 'alive')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/run/config/api.toml'))
    parser.add_argument('--ca-file', type=Path, default=Path('/run/config/api-ca.pem'))
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    try:
        return 0 if probe(args.config, args.ca_file, ready=not args.live) else 1
    except Exception:
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
