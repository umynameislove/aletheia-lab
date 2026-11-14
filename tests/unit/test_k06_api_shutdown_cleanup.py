"""TLS secrets are removed even when the native integration fixture cannot clean up."""

import importlib.util
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("cryptography")


@pytest.mark.parametrize("drop_role_fails", [False, True], ids=["startup-error", "cleanup-error"])
def test_native_api_fixture_removes_tls_key_after_failure(tmp_path, monkeypatch, drop_role_fails):
    integration = Path(__file__).resolve().parents[1] / "integration"
    monkeypatch.syspath_prepend(str(integration))
    spec = importlib.util.spec_from_file_location(
        "k06_api_shutdown_cleanup_subject", integration / "test_k06_api_shutdown_postgres.py"
    )
    subject = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(subject)
    calls, keys = [], []

    class StartupFailure(RuntimeError):
        pass

    class CleanupFailure(RuntimeError):
        pass

    class Connection:
        async def execute(self, query, *args):
            query = str(query)
            self.query = query
            if "DROP ROLE" in query:
                calls.append("drop-role")
                if drop_role_fails:
                    raise CleanupFailure
            if "CREATE ROLE" in query:
                calls.append("create-role")
            return self

        async def fetchone(self):
            return (None, False) if "to_regnamespace" in self.query else ("k06_synthetic_schema",)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        @staticmethod
        async def connect(*args, **kwargs):
            return Connection()

    @asynccontextmanager
    async def case(_):
        try:
            yield SimpleNamespace(pool=SimpleNamespace(connection=Connection))
        finally:
            calls.append("schema-cleanup")

    async def ignore(*args, **kwargs):
        return {}

    certificate = subject._certificate

    def capture_certificate(directory):
        cert, key = certificate(directory)
        keys.append(key)
        return cert, key

    def refuse_start(*args, **kwargs):
        calls.append("refused-process-start")
        raise StartupFailure

    monkeypatch.setenv("K06_TEST_DATABASE_URL", "postgresql://synthetic.invalid/offline")
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(AsyncConnection=Connection, sql=psycopg.sql))
    monkeypatch.setattr(subject, "_case", case)
    monkeypatch.setattr(subject, "_setup", ignore)
    monkeypatch.setattr(subject, "_identity", ignore)
    monkeypatch.setattr(subject, "grant_runtime", ignore)
    monkeypatch.setattr(subject, "_certificate", capture_certificate)
    monkeypatch.setattr(subject, "_port", lambda: 54321)
    monkeypatch.setattr(subject.subprocess, "Popen", refuse_start)

    expected = CleanupFailure if drop_role_fails else StartupFailure
    with pytest.raises(expected):
        subject.test_native_api_sigterm_drain_two_process_replay_and_restart(tmp_path, False)

    assert calls == ["create-role", "refused-process-start", "schema-cleanup", "drop-role"]
    assert len(keys) == 1 and not keys[0].exists()
