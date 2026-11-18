"""Explicit API/worker audit activation is validated before resource acquisition."""
import asyncio
import sys
from contextlib import AsyncExitStack
from types import SimpleNamespace

import pytest

from aletheia_lab.hosted import managed_runtime, managed_worker


class ReachedResourceBoundary(Exception):
    pass


@pytest.mark.parametrize("surface", ["api", "worker"])
@pytest.mark.parametrize("mode,schema,valid", [
    (None, None, True), ("disabled", "synthetic", True), ("enabled", "synthetic", True),
    ("enabled", None, False), ("", "synthetic", False), ("yes", "synthetic", False),
])
def test_audit_configuration_gate_precedes_secrets_pool_and_worker_resources(monkeypatch, surface, mode, schema, valid):
    settings = SimpleNamespace(storage=SimpleNamespace(expected_schema=schema))
    environment = {"ALETHEIA_HOSTED_CONFIG": "synthetic", "ALETHEIA_HOSTED_AUTH_POLICY": "synthetic"}
    if mode is not None:
        environment["ALETHEIA_HOSTED_ACTION_AUDIT"] = mode

    def resource_boundary(*args, **kwargs):
        raise ReachedResourceBoundary()

    async def scenario():
        if surface == "api":
            app = object.__new__(managed_runtime.ManagedRuntime)
            app._env, app._settings, app._policy = environment, settings, object()
            monkeypatch.setattr(managed_runtime, "resolve_runtime_secrets", resource_boundary)
            async with AsyncExitStack() as stack:
                await app._compose(stack)
        else:
            monkeypatch.setattr(managed_worker, "load_runtime_settings", lambda path: settings)
            monkeypatch.setattr(managed_worker, "load_auth_policy", resource_boundary)
            await managed_worker.run_worker(environment, asyncio.Event())

    expected = ReachedResourceBoundary if valid else ValueError
    with pytest.raises(expected), asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        runner.run(scenario())
