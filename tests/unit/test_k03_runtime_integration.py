"""Approved runtime composition and callback response-order regressions."""

import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from pydantic import SecretStr
from test_j01_callback_http import Coordinator
from test_k02_runtime_lifecycle import _settings
from test_k03_auth_abuse import SyntheticAdmissionStore
from test_k03_session_http import _request, _system

from aletheia_lab.hosted import managed_runtime, upload_runtime
from aletheia_lab.hosted_auth.abuse import AuthAdmission, RatePolicy
from aletheia_lab.hosted_auth.boundary import LOGIN_COOKIE
from aletheia_lab.hosted_auth.callback_http import CallbackApplication
from aletheia_lab.hosted_auth.login import LoginStart
from aletheia_lab.hosted_auth.oidc import OidcUnavailable, new_authorization_attempt
from aletheia_lab.hosted_auth.session_http import LOGOUT_ALL_PATH, RENEW_PATH
from aletheia_lab.hosted_auth.settings import AuthPolicy
from aletheia_lab.hosted_auth.start_http import LoginStartApplication
from aletheia_lab.hosted_contract.session_capabilities import Limits


async def _verified_binding(actual_pool, storage, *, strict=False):
    assert type(actual_pool) is object
    assert storage is not None and strict is True
    return True


def test_delayed_callback_never_deletes_login_cookie_refreshed_by_newer_start():
    async def scenario():
        old_response, release_old_response = asyncio.Event(), asyncio.Event()
        policy = AuthPolicy(default_return_to="/app", return_to_paths=("/app",))

        class Begin:
            async def begin(self, cookie, *, return_to=None, reauthenticate=False):
                return LoginStart(SecretStr("https://identity.example.invalid/authorize"),
                                  SecretStr("l" * 43), datetime.now(UTC) + timedelta(seconds=300))

        async def fallback(scope, receive, send):
            raise AssertionError("unhandled auth route")

        callback = CallbackApplication(fallback, Coordinator(), policy,
                                       maximum_cookie_bytes=4096, timeout_seconds=1.0)
        app = LoginStartApplication(callback, Begin(), maximum_cookie_bytes=4096,
                                    timeout_seconds=1.0, clock_check=lambda: _ready())

        async def delayed(scope, receive, send):
            async def hold_callback(event):
                if scope["path"] == "/v1/auth/callback" and event["type"] == "http.response.start":
                    old_response.set()
                    await release_old_response.wait()
                await send(event)
            await app(scope, receive, hold_callback)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=delayed), base_url="https://app.example.invalid") as client:
            client.cookies.set(LOGIN_COOKIE, "l" * 43, domain="app.example.invalid", path="/")
            old = asyncio.create_task(client.get("/v1/auth/callback?state=synthetic"))
            await old_response.wait()
            newer = await client.get("/v1/auth/start", headers={"sec-fetch-site": "same-origin"})
            assert newer.status_code == 303 and client.cookies.get(LOGIN_COOKIE) == "l" * 43
            release_old_response.set()
            assert (await old).status_code == 303
            assert client.cookies.get(LOGIN_COOKIE) == "l" * 43
    asyncio.run(scenario())


async def _ready():
    return True


def _forbid_worker(**kwargs):
    raise AssertionError("API must not construct a worker")


def test_composed_upload_runtime_reaches_renew_and_logout_all(tmp_path, monkeypatch):
    async def scenario():
        base, sessions, _, tokens, headers = _system(tmp_path)
        # Keep actual route composition, without opening resources.
        monkeypatch.setattr(upload_runtime, "ProcessApplication", lambda settings, resources, *, routes: routes(base._fallback))
        app = upload_runtime.compose_upload_runtime(
            settings=base._runtime, sessions=sessions, tokens=tokens, clock_check=_ready,
            workspaces=object(), sources=SimpleNamespace(maximum_upload_bytes=1024), jobs=object(),
            coordinator=object(), auth_policy=AuthPolicy(default_return_to="/app", return_to_paths=("/app",)),
            limits=Limits(question_chars=2000, upload_bundle_bytes=1024, page_max=200, job_poll_min_ms=2000),
            admission=AuthAdmission(SyntheticAdmissionStore(), RatePolicy(10, 5, 60), timeout_seconds=1.0),
            admission_subject_key=SecretStr("synthetic-rate-key-" + "r" * 43),
        )
        # The production route's real-time clock must match the test authority.
        sessions._clock = lambda: datetime.now(UTC)
        for digest, record in list(sessions._store.records.items()):
            delta = datetime.now(UTC) - record.last_seen_at - timedelta(seconds=600)
            sessions._store.records[digest] = record.model_copy(update={
                key: getattr(record, key) + delta for key in
                ("issued_at", "last_seen_at", "rotated_at", "idle_expires_at", "absolute_expires_at")
            })
        sessions._store.now = datetime.now(UTC)
        events, _ = await _request(app, headers, path=LOGOUT_ALL_PATH)
        assert events[0]["status"] == 204
        events, _ = await _request(app, headers, path=RENEW_PATH)
        assert events[0]["status"] == 401
        for _ in range(4):
            events, _ = await _request(app, headers, path=RENEW_PATH)
            assert events[0]["status"] == 401
        events, reads = await _request(app, headers, path=RENEW_PATH)
        assert events[0]["status"] == 429 and reads == []
    asyncio.run(scenario())


class _BlockingManagedProvider:
    def __init__(self):
        self.calls = 0
        self.release = asyncio.Event()

    async def _current(self):
        return self

    async def authorization_url(self, attempt):
        self.calls += 1
        await self.release.wait()
        return SecretStr("https://identity.example.invalid/authorize")


class _PreparedAdmissionStore:
    def __init__(self, *args, **kwargs):
        pass

    async def prepare(self, policy):
        pass

    async def is_ready(self, policy):
        return True


def test_managed_runtime_really_bounds_provider_calls_before_coordinator(tmp_path, monkeypatch):
    captured, close = {"provider": _BlockingManagedProvider()}, []
    monkeypatch.setattr(managed_runtime, "load_runtime_settings", lambda path: _settings(tmp_path))
    policy = AuthPolicy(default_return_to="/app", return_to_paths=("/app",), idp_maximum_inflight=1,
                        admission_namespace="test-runtime")
    monkeypatch.setattr(managed_runtime, "load_auth_policy", lambda path: policy)
    monkeypatch.setattr(managed_runtime, "sys", SimpleNamespace(platform="linux", executable="C:/python.exe"))

    @asynccontextmanager
    async def pool(*args, **kwargs):
        assert kwargs["role"] == "api"
        yield object()
        close.append("pool")

    class Jobs:
        def __init__(self, *args, **kwargs):
            assert kwargs["execution"] is True and kwargs["analysis_model"] == "upload-snapshot-metric-observation/v2"
            self.execution = True
            self.ready = True
            captured["jobs"] = self

        async def execution_ready(self):
            return True

        async def is_ready(self):
            return self.ready

    def coordinator(store, oidc, *args, **kwargs):
        captured["oidc"] = oidc
        return object()

    async def application(scope, receive, send):
        assert (await receive())["type"] == "lifespan.startup"
        assert await captured["clock"]() is True
        captured["jobs"].ready = False
        assert await captured["clock"]() is False
        captured["jobs"].ready = True
        monkeypatch.setattr(_PreparedAdmissionStore, "is_ready", lambda *args: _not_ready())
        assert await captured["clock"]() is False
        attempt = new_authorization_attempt(policy, now=datetime.now(UTC))
        tasks = [asyncio.create_task(captured["oidc"].authorization_url(attempt)) for _ in range(10)]
        await asyncio.sleep(0)
        assert captured["provider"].calls == 1
        captured["provider"].release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert sum(isinstance(value, OidcUnavailable) for value in results) == 9
        await send({"type": "lifespan.startup.complete"})
        assert (await receive())["type"] == "lifespan.shutdown"
        await send({"type": "lifespan.shutdown.complete"})

    monkeypatch.setattr(managed_runtime, "verify_runtime_binding", _verified_binding)
    monkeypatch.setattr(managed_runtime, "postgres_pool", pool)
    monkeypatch.setattr(managed_runtime, "UploadWorkerSupervisor", _forbid_worker, raising=False)
    monkeypatch.setattr(managed_runtime, "PostgresJobs", Jobs)
    monkeypatch.setattr(managed_runtime, "PostgresJobUpdates", lambda jobs, cursors:
                        SimpleNamespace(ready=AsyncMock(return_value=True)))
    monkeypatch.setattr(managed_runtime, "PostgresRetention", lambda pool, catalog:
                        SimpleNamespace(is_ready=AsyncMock(return_value=True)))
    monkeypatch.setattr(managed_runtime, "PostgresWorkspaceManagement", lambda *args, **kwargs:
                        SimpleNamespace(is_ready=_ready))
    monkeypatch.setattr(managed_runtime, "RefreshingOidc", lambda *args, **kwargs: captured["provider"])
    monkeypatch.setattr(managed_runtime, "PostgresAuthAdmission", _PreparedAdmissionStore)
    monkeypatch.setattr(managed_runtime, "LoginCoordinator", coordinator)
    def compose(**kwargs):
        captured["clock"] = kwargs["clock_check"]
        return application

    monkeypatch.setattr(managed_runtime, "probe_clock", lambda *args, **kwargs: _ready())
    monkeypatch.setattr(managed_runtime, "compose_upload_runtime", compose)
    (tmp_path / "objects").mkdir()
    env = {"ALETHEIA_HOSTED_CONFIG": "unused", "ALETHEIA_HOSTED_AUTH_POLICY": "unused",
           "ALETHEIA_HOSTED_DATABASE_URL": "postgresql://u:p@localhost/db",
           "ALETHEIA_HOSTED_LOGIN_PROOF_KEY": base64.b64encode(b"k" * 32).decode(),
           "ALETHEIA_HOSTED_CSRF_SECRET": "s" * 43,
           "ALETHEIA_HOSTED_ADMISSION_SECRET": "r" * 43}

    async def scenario():
        incoming = iter(({"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}))
        output = []

        async def receive():
            return next(incoming)

        async def send(message):
            output.append(message["type"])

        await managed_runtime.ManagedRuntime(env)({"type": "lifespan"}, receive, send)
        assert output == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
        assert close == ["pool"]
    asyncio.run(scenario())


async def _not_ready():
    return False
