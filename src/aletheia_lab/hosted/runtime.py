"""Offline runtime planning. No socket, IdP, database or worker is started."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

from .settings import (
    ConfigurationRejected,
    RuntimeSettings,
    load_runtime_settings,
    resolve_runtime_secrets,
)


def api_server_options(settings: RuntimeSettings) -> dict[str, object]:
    """Explicit Uvicorn options for future composition with the real backend.

    Multi-process serving will require an importable app factory. This function
    returns an options plan, not a server and not a process supervisor.
    """
    from .header_protocol import header_deadline_protocol

    api = settings.api
    return {
        "host": api.bind_host,
        "port": api.port,
        "workers": api.processes,
        "loop": "asyncio",
        "http": header_deadline_protocol(api.header_timeout_seconds),
        "interface": "asgi3",
        "lifespan": "on",
        "ws": "none",
        "reload": False,
        "access_log": False,
        "server_header": False,
        "proxy_headers": False,
        "forwarded_allow_ips": "",
        "limit_concurrency": api.concurrency_limit,
        "backlog": api.backlog,
        "timeout_keep_alive": api.keep_alive_seconds,
        "timeout_graceful_shutdown": api.graceful_shutdown_seconds,
        "h11_max_incomplete_event_size": api.h11_max_incomplete_event_bytes,
    }


def preflight(config_path: Path, environment: Mapping[str, str]) -> dict[str, object]:
    settings = load_runtime_settings(config_path)
    resolve_runtime_secrets(settings, environment)
    return {
        "configuration_valid": True,
        "environment": settings.environment,
        "planned_api_processes": settings.api.processes,
        "planned_worker_processes": settings.worker.processes,
        "planned_database_connections": settings.planned_database_connections,
        "postgres_connection_budget": settings.storage.postgres_connection_budget,
        "dependencies_probed": False,
        "runtime_started": False,
        "hosted_ready": False,
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check-config"])
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    output = stdout if stdout is not None else sys.stdout
    errors = stderr if stderr is not None else sys.stderr
    try:
        report = preflight(args.config, os.environ if environment is None else environment)
    except ConfigurationRejected as error:
        print(str(error), file=errors)
        return 2
    print(json.dumps(report, sort_keys=True), file=output)
    return 0
