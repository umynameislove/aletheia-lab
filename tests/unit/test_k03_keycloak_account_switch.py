"""Local-only manual smoke CLI plus offline tests of its navigation boundary.

Direct execution requires explicit fixture/CA/password environment variables.
Normal pytest runs only the offline parser tests; they make no network call.
The CLI uses a real IdP with a synthetic authority, or an isolated real DB when
K03_DB_PASSWORD is explicitly supplied. Neither mode runs during pytest.
"""

import asyncio
import json
import os
import re
import ssl
import sys
import traceback
import uuid
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
import httpx2
import pytest
from pydantic import SecretBytes, SecretStr
from test_k03_postgres_environment import database_port

from aletheia_lab.hosted.application import AdapterLimits, HostedApplication
from aletheia_lab.hosted.read_backend import ReadOnlyHostedBackend
from aletheia_lab.hosted.settings import load_runtime_settings
from aletheia_lab.hosted_auth.abuse import BoundedOidc
from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE
from aletheia_lab.hosted_auth.callback_http import CallbackApplication
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.identity import AccountResolver
from aletheia_lab.hosted_auth.login import LoginCoordinator
from aletheia_lab.hosted_auth.login_proof_cipher import LoginProofCipher
from aletheia_lab.hosted_auth.oidc_discovery import RefreshingOidc
from aletheia_lab.hosted_auth.postgres_fresh_login import PostgresFreshLogin
from aletheia_lab.hosted_auth.postgres_identity import PostgresIdentityDirectory
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.repositories import AccountBinding
from aletheia_lab.hosted_auth.sessions import ServerSessions
from aletheia_lab.hosted_auth.settings import load_auth_policy
from aletheia_lab.hosted_auth.start_http import LoginStartApplication
from aletheia_lab.hosted_contract.session_capabilities import Actor, Limits

_READY_TIMEOUT_SECONDS = 45.0


class _Form(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.action = self.restart = None
        self.username_visible = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "form" and values.get("id") == "kc-form-login":
            self.action = values.get("action")
        if tag == "a" and values.get("id") == "reset-login":
            self.restart = values.get("href")
        if tag == "button" and values.get("id") == "reset-login":
            match = re.fullmatch(r"location\.href='([^']+)'", values.get("onclick", ""))
            if match:
                self.restart = match[1]
        if tag == "input" and values.get("name") == "username" and values.get("type", "text") != "hidden":
            self.username_visible = True


def _trusted(url, *, callback=False):
    parsed = urlsplit(url)
    expected_port = 18446 if callback else 18445
    if (parsed.scheme != "https" or parsed.hostname != "localhost" or parsed.port != expected_port
            or parsed.username is not None or parsed.password is not None or parsed.fragment
            or any(ord(char) <= 32 or ord(char) == 127 for char in url)):
        raise ValueError("Unexpected local test destination.")
    if callback and parsed.path != "/v1/auth/callback":
        raise ValueError("Unexpected local test callback.")
    if not callback and not parsed.path.startswith("/realms/j01-two-review/"):
        raise ValueError("Unexpected local test realm.")
    return url


async def _page(browser, url):
    for _ in range(6):
        response = await browser.get(_trusted(url))
        if response.status_code == 200:
            return response
        if response.status_code not in (302, 303):
            raise ValueError("Unexpected local IdP response.")
        url = urljoin(str(response.url), response.headers["location"])
    raise ValueError("Local IdP navigation budget exceeded.")


async def _wait_ready(browser, issuer):
    try:
        async with asyncio.timeout(_READY_TIMEOUT_SECONDS):
            for _ in range(45):
                try:
                    response = await browser.get(_trusted(issuer + "/.well-known/openid-configuration"))
                    if response.status_code == 200:
                        return
                except httpx.TransportError:
                    pass
                await asyncio.sleep(1)
    except TimeoutError:
        pass
    raise ValueError("Local IdP did not become ready.")


async def _login(client, browser, name, password):
    switching = name.endswith("b")
    response = await client.get("/v1/auth/start", params={"mode": "switch" if switching else "login"},
                                headers={"sec-fetch-site": "same-origin"})
    assert response.status_code == 303, "login-start status " + str(response.status_code)
    page = await _page(browser, response.headers["location"])
    form = _Form()
    form.feed(page.text)
    if switching and not form.username_visible:
        assert form.restart is not None, "IdP account restart control unavailable"
        page = await _page(browser, urljoin(str(page.url), form.restart))
        form = _Form()
        form.feed(page.text)
        print("K03_IDP_EXPLICIT_ACCOUNT_SELECTION=PASS", flush=True)
    assert form.username_visible and form.action is not None, "account selection form unavailable"
    response = await browser.post(_trusted(urljoin(str(page.url), form.action)),
        data={"username": name, "password": password})
    assert response.status_code in (302, 303), "IdP submit status " + str(response.status_code)
    callback = _trusted(urljoin(str(response.url), response.headers["location"]), callback=True)
    response = await client.get(callback)
    assert response.status_code == 303, "callback status " + str(response.status_code)
    current = await client.get("/v1/session")
    assert current.status_code == 200, "session read status " + str(current.status_code)
    value = current.json()
    assert value["actor"]["user_id"] == "usr_" + name[-1], "unexpected test actor"
    return value


async def _live(root, ca, password, *, database_pool=None):
    from test_k03_login_coordinator import SyntheticLoginAuthority

    runtime = load_runtime_settings(root / "runtime.toml")
    policy = load_auth_policy(root / "auth.toml")
    assert runtime.identity.issuer == "https://localhost:18445/realms/j01-two-review"
    assert runtime.identity.redirect_uri == "https://localhost:18446/v1/auth/callback"
    fixtures = json.loads((root / "realm.json").read_text(encoding="utf-8"))
    users = {user["username"]: user for user in fixtures["users"]}
    assert set(users) == {"j01-user-a", "j01-user-b"}
    tls = ssl.create_default_context(cafile=str(ca))

    class Authority(SyntheticLoginAuthority):
        async def begin_intent(self, command):
            self.now = datetime.now(UTC)
            return await super().begin_intent(command)

        async def claim_intent(self, **kwargs):
            self.now = datetime.now(UTC)
            return await super().claim_intent(**kwargs)

        async def commit_login(self, command):
            self.now = datetime.now(UTC)
            return await super().commit_login(command)

        async def find_identity(self, *, issuer, subject):
            return self.accounts.get(subject.get_secret_value()) if issuer == runtime.identity.issuer else None

    class Workspaces:
        async def is_ready(self):
            return True

    store = Authority()
    store.accounts = {
        user["id"]: AccountBinding(issuer=runtime.identity.issuer, subject=SecretStr(user["id"]),
            actor=Actor(user_id="usr_" + name[-1], display_name="Synthetic", email=user["email"], email_verified=True),
            enabled=True, account_epoch=0, onboarding="needs_workspace", last_workspace_id=None)
        for name, user in users.items()
    }
    transactions = directory = session_store = store
    if database_pool is not None:
        transactions = PostgresFreshLogin(database_pool, LoginProofCipher(SecretBytes(os.urandom(32))),
                                           clock_skew_seconds=policy.clock_skew_seconds)
        directory = PostgresIdentityDirectory(database_pool)
        session_store = PostgresSessionStore(database_pool)
    tokens = CsrfTokens(SecretStr("synthetic-local-smoke-key-" + "k" * 43))
    material = RefreshingOidc(runtime, policy, transport_factory=lambda: httpx2.AsyncHTTPTransport(verify=tls))
    provider = BoundedOidc(material, timeout_seconds=policy.idp_timeout_seconds,
        maximum_inflight=policy.idp_maximum_inflight, initial_backoff_seconds=policy.idp_initial_backoff_seconds,
        maximum_backoff_seconds=policy.idp_maximum_backoff_seconds)
    flow = LoginCoordinator(transactions, provider,
        AccountResolver(directory, policy, expected_issuer=runtime.identity.issuer, timeout_seconds=5.0),
        runtime, policy, csrf_factory=tokens.issue, timeout_seconds=runtime.api.backend_timeout_seconds)
    sessions = ServerSessions(session_store, policy, timeout_seconds=5.0, maximum_cookie_bytes=4096)
    backend = ReadOnlyHostedBackend(sessions, Workspaces(),
        limits=Limits(question_chars=2000, upload_bundle_bytes=1024, page_max=200, job_poll_min_ms=2000))
    app = CallbackApplication(HostedApplication(backend, limits=AdapterLimits(5.0, 65536, 4096)),
        flow, policy, maximum_cookie_bytes=4096, timeout_seconds=runtime.api.backend_timeout_seconds)

    async def ready():
        return True

    app = LoginStartApplication(app, flow, maximum_cookie_bytes=4096,
        timeout_seconds=runtime.api.backend_timeout_seconds, clock_check=ready)
    async with httpx.AsyncClient(verify=tls, trust_env=False, follow_redirects=False, timeout=10.0) as browser:
        await _wait_ready(browser, runtime.identity.issuer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=runtime.public_origin,
                                      follow_redirects=False) as client:
            first = await _login(client, browser, "j01-user-a", password)
            old_cookie = client.cookies.get(SESSION_COOKIE)
            second = await _login(client, browser, "j01-user-b", password)
            assert second["session"]["context_id"] != first["session"]["context_id"]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=runtime.public_origin) as old:
                denied = await old.get("/v1/session", headers={"cookie": SESSION_COOKIE + "=" + old_cookie})
                assert denied.status_code == 401 and denied.json()["code"] == "session_revoked"
    if database_pool is None:
        assert store.commit_effects == 2
    print("K03_REAL_KEYCLOAK_ASGI_SWITCH_A_TO_B=PASS", flush=True)
    print("K03_OLD_APP_CONTEXT_REJECTED=PASS", flush=True)
    print("K03_STORAGE_SCOPE=" + ("SYNTHETIC_AUTHORITY_NO_DATABASE" if database_pool is None else "REAL_POSTGRES_ISOLATED_SCHEMA"), flush=True)


async def _database_live(root, ca, password, db_password):
    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool
    from test_k03_postgres_http import _schema_name

    from aletheia_lab.hosted.persistence_migration import LOGIN_CLOCK_DDL

    schema = _schema_name("k03_http_test_" + uuid.uuid4().hex)
    db = {"host": "127.0.0.1", "port": database_port(), "dbname": "aletheia", "user": "aletheia", "password": db_password}
    users = json.loads((root / "realm.json").read_text(encoding="utf-8"))["users"]
    runtime = load_runtime_settings(root / "runtime.toml")
    created = False
    async with await psycopg.AsyncConnection.connect(**db, autocommit=True) as admin:
        try:
            await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            created = True
            await admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            for name in ("j01-experimental-session-schema.sql", "j01-experimental-fresh-login-schema.sql"):
                await admin.execute((Path.cwd() / "docs" / name).read_text(encoding="utf-8"))
            for user in users:
                await admin.execute("INSERT INTO hosted_identity_bindings VALUES (%s,%s,%s,%s,%s,true,true,0,'needs_workspace',NULL,NULL)",
                    (runtime.identity.issuer, user["id"], "usr_" + user["username"][-1], "Synthetic", user["email"]))
            await admin.execute(LOGIN_CLOCK_DDL)
            async with AsyncConnectionPool(conninfo="", kwargs={**db, "options": "-c search_path=" + schema}, min_size=1, max_size=5) as pool:
                from aletheia_lab.hosted_auth.clock_guard import probe_clock
                assert await probe_clock(pool, max_skew_seconds=30, timeout_seconds=2)
                await _live(root, ca, password, database_pool=pool)
                async with pool.connection() as connection:
                    row = await (await connection.execute("SELECT count(*) FROM j01_sessions WHERE state='active'")).fetchone()
                    assert row == (1,)
                print("K03_REAL_IDP_AND_DB_SINGLE_ACTIVE_CONTEXT=PASS", flush=True)
        finally:
            if created:
                await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(_schema_name(schema))))
                print("K03_COMBINED_DB_OWN_SCHEMA_REMOVED=PASS", flush=True)


def test_form_distinguishes_real_account_selection_from_hidden_username():
    form = _Form()
    form.feed('<form id="kc-form-login" action="/action"><input name="username" type="hidden"><a id="reset-login" href="/restart?a=1&amp;b=2">Again</a></form>')
    assert form.username_visible is False and form.restart == "/restart?a=1&b=2"
    form.feed('<input name="username" type="text">')
    assert form.username_visible is True


def test_keycloak_v2_restart_button_is_parsed_without_executing_javascript():
    form = _Form()
    form.feed('<button id="reset-login" onclick="location.href=\'/restart?a=1&amp;b=2\'">Again</button>')
    assert form.restart == "/restart?a=1&b=2"
    form = _Form()
    form.feed('<button id="reset-login" onclick="alert(1)">Again</button>')
    assert form.restart is None


@pytest.mark.parametrize("url", ["http://localhost:18445/realms/j01-two-review/a", "https://evil.invalid:18445/realms/j01-two-review/a", "https://localhost:18445/realms/other/a", "https://u:p@localhost:18445/realms/j01-two-review/a"])
def test_live_harness_never_follows_an_untrusted_destination(url):
    with pytest.raises(ValueError, match="Unexpected local test"):
        _trusted(url)


def test_readiness_deadline_cancels_a_slow_request(monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "_READY_TIMEOUT_SECONDS", 0.01)

    class SlowBrowser:
        cancelled = False

        async def get(self, url):
            try:
                await asyncio.sleep(10)
            finally:
                self.cancelled = True

    browser = SlowBrowser()

    async def probe():
        with pytest.raises(ValueError, match="Local IdP did not become ready"):
            await asyncio.wait_for(_wait_ready(browser, "https://localhost:18445/realms/j01-two-review"), 1)

    asyncio.run(probe())
    assert browser.cancelled is True


if __name__ == "__main__":
    try:
        root = Path(os.environ.pop("K03_KEYCLOAK_TEST_ROOT"))
        ca = Path(os.environ.pop("K03_KEYCLOAK_TEST_CA"))
        password = os.environ.pop("K03_SYNTHETIC_PASSWORD")
        db_password = os.environ.pop("K03_DB_PASSWORD", None)
        if db_password is not None:
            if sys.platform == "win32":
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            asyncio.run(_database_live(root, ca, password, db_password))
        else:
            asyncio.run(_live(root, ca, password))
    except Exception as error:
        print("K03_LOCAL_IDP_CHECK=FAIL:" + type(error).__name__, flush=True)
        frames = traceback.extract_tb(error.__traceback__)
        if frames:
            print("K03_FAILURE_LINE=" + str(frames[-1].lineno), flush=True)
        sys.exit(1)
