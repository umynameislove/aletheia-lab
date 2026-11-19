"""Start the experimental hosted service using explicit TLS and config files."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from .composition import Application
from .managed_runtime import ManagedRuntime
from .observability import configure_process_observability
from .runtime import api_server_options
from .static_assets import StaticAssets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--auth-policy', required=True, type=Path)
    parser.add_argument('--tls-cert', required=True, type=Path)
    parser.add_argument('--tls-key', required=True, type=Path)
    parser.add_argument('--static-root', type=Path)
    args = parser.parse_args()
    if not all(path.is_file() for path in (args.config, args.auth_policy, args.tls_cert, args.tls_key)):
        print('Runtime configuration is unavailable.', file=sys.stderr)
        return 2
    environment = dict(os.environ)
    environment.update(ALETHEIA_HOSTED_CONFIG=str(args.config), ALETHEIA_HOSTED_AUTH_POLICY=str(args.auth_policy))
    observation = None
    try:
        observation = configure_process_observability(role='api', release_id=environment.get('ALETHEIA_RELEASE_ID'))
        import uvicorn

        runtime = ManagedRuntime(environment)
        settings = runtime._settings
        application: Application = runtime
        if args.static_root is not None:
            application = StaticAssets(application, args.static_root, settings.public_origin)
        if settings.api.processes != 1:
            raise ValueError('one API process per server command required')
        options: dict[str, Any] = api_server_options(settings)
        options['ssl_certfile'], options['ssl_keyfile'] = str(args.tls_cert), str(args.tls_key)
        config = uvicorn.Config(application, log_config=None, **options)
        server = uvicorn.Server(config)
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == 'win32' else None) as runner:
            runner.run(server.serve())
        if not server.started:
            return 1
    except Exception:
        print('Runtime could not start.', file=sys.stderr)
        return 1
    finally:
        if observation is not None:
            observation.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
