"""Opt-in real IdP password recovery with retained OTP; pytest is offline only."""

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urljoin, urlsplit

import httpx
import httpx2
import pytest
from pydantic import SecretBytes, SecretStr
from test_k03_postgres_environment import database_port
from test_k03_recovery_environment import recovery_environment

from aletheia_lab.hosted.application import AccessDenied
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted_auth.boundary import LOGIN_COOKIE, SESSION_COOKIE
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.identity import AccountResolver, IdentityRejected
from aletheia_lab.hosted_auth.login import LoginCoordinator, LoginRejected
from aletheia_lab.hosted_auth.login_proof_cipher import LoginProofCipher
from aletheia_lab.hosted_auth.oidc_discovery import RefreshingOidc
from aletheia_lab.hosted_auth.postgres_fresh_login import PostgresFreshLogin
from aletheia_lab.hosted_auth.postgres_identity import PostgresIdentityDirectory
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.sessions import ServerSessions
from aletheia_lab.hosted_auth.settings import AuthPolicy

ROOT = Path(__file__).resolve().parents[2]
APP = "https://app.example.invalid"
CALLBACK = APP + "/v1/auth/callback"
CLIENT = "k03-recovery"
STAGE = "preflight"
JSON_OPERATION = "none"


class _Document(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.forms, self.links, self.current, self.ids = [], [], None, set()
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "form":
            self.current = {"action": values.get("action", ""), "names": set(), "hidden": {}}
        elif tag == "input" and self.current is not None and values.get("name"):
            name = values["name"]
            self.current["names"].add(name)
            if values.get("type") == "hidden":
                self.current["hidden"][name] = values.get("value", "")
        elif tag == "a" and values.get("href"):
            self.links.append(values["href"])

    def handle_endtag(self, tag):
        if tag == "form" and self.current is not None:
            self.forms.append(self.current)
            self.current = None

    def form(self, field):
        matches = [form for form in self.forms if field in form["names"]]
        if len(matches) != 1:
            raise ValueError("Expected one provider form.")
        return matches[0]

    def has(self, field):
        return any(field in form["names"] for form in self.forms)


def _trusted(url, origin, realm, *, callback=False):
    parsed, expected = urlsplit(url), urlsplit(APP if callback else origin)
    path = "/v1/auth/callback" if callback else "/realms/" + realm + "/"
    valid_path = parsed.path == path if callback else parsed.path.startswith(path)
    if (parsed.scheme != expected.scheme or parsed.netloc != expected.netloc or not valid_path
            or parsed.username is not None or parsed.password is not None or parsed.fragment
            or "\\" in url or "%" in parsed.path or any(part in (".", "..") for part in parsed.path.split("/"))
            or len(url) > 16384 or any(ord(char) < 33 or ord(char) > 126 for char in url)):
        raise ValueError("Untrusted recovery navigation.")
    return url


def _totp(seed, timestamp):
    key = base64.b32decode(seed)
    counter = struct.pack(">Q", int(timestamp) // 30)
    digest = hmac.new(key, counter, hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff
    return str(value % 1000000).zfill(6)


def _schema(name):
    if not isinstance(name, str) or not name.startswith("k03_recovery_"):
        raise ValueError("Owned recovery schema required.")
    suffix = name.removeprefix("k03_recovery_")
    if len(suffix) != 32 or any(char not in "0123456789abcdef" for char in suffix):
        raise ValueError("Owned recovery schema required.")
    return name


async def _json(client, method, path, **kwargs):
    global JSON_OPERATION
    JSON_OPERATION = method + " " + urlsplit(path).path
    response = await client.request(method, path, **kwargs)
    if not 200 <= response.status_code < 300 or len(response.content) > 262144:
        raise ValueError("Provider fixture operation rejected: HTTP " + str(response.status_code))
    return response.json() if response.content and method != "DELETE" else None


async def _admin_login(env, admin):
    token = await _json(admin, "POST", "/realms/master/protocol/openid-connect/token",
                        data={"grant_type": "password", "client_id": "admin-cli",
                              "username": env.admin_username, "password": env.admin_password.get_secret_value()})
    admin.headers["authorization"] = "Bearer " + token["access_token"]


async def _create_user(admin, base, username, password, seed, email):
    await _json(admin, "POST", base + "/users", json={
        "username": username, "enabled": True, "email": email, "emailVerified": True,
        "firstName": "Synthetic", "lastName": "Recovery",
        "credentials": [{"type": "password", "value": password, "temporary": False},
                        {"type": "otp", "userLabel": "k03-existing-factor",
                         "secretData": json.dumps({"value": seed}),
                         "credentialData": json.dumps({"subType": "totp", "digits": 6, "counter": 0, "period": 30,
                                                       "algorithm": "HmacSHA1", "secretEncoding": "BASE32"})}],
    })
    users = await _json(admin, "GET", base + "/users", params={"username": username, "exact": "true"})
    assert len(users) == 1 and users[0]["username"] == username
    return users[0]["id"]


async def _setup(env, admin, realm, password, seed):
    await _admin_login(env, admin)
    # The disposable fixture spans real OTP time steps; keep its admin token alive.
    await _json(admin, "PUT", "/admin/realms/master", json={"accessTokenLifespan": 900})
    await _admin_login(env, admin)
    await _json(admin, "POST", "/admin/realms", json={
        "realm": realm, "enabled": True, "resetPasswordAllowed": True,
        "actionTokenGeneratedByUserLifespan": 120,
        "otpPolicyType": "totp", "otpPolicyAlgorithm": "HmacSHA1", "otpPolicyDigits": 6,
        "otpPolicyPeriod": 30, "otpPolicyLookAheadWindow": 1,
        "smtpServer": {"host": env.smtp_host, "port": str(env.smtp_port), "from": "recovery@aletheia.invalid",
                       "auth": "false", "ssl": "false", "starttls": "false"},
    })
    base = "/admin/realms/" + realm
    await _json(admin, "POST", base + "/clients", json={
        "clientId": CLIENT, "enabled": True, "publicClient": True, "standardFlowEnabled": True,
        "directAccessGrantsEnabled": False, "redirectUris": [CALLBACK], "webOrigins": [APP],
        "attributes": {"pkce.code.challenge.method": "S256"},
        "protocolMappers": [{"name": "fixture-acr", "protocol": "openid-connect",
                             "protocolMapper": "oidc-acr-mapper", "config": {"id.token.claim": "true"}}],
    })
    subject = await _create_user(admin, base, "recovery-user", password, seed, "recovery-a@aletheia.invalid")
    browser_flow, reset_flow = "k03-mfa", "k03-password-reset"
    await _json(admin, "POST", base + "/authentication/flows",
                json={"alias": browser_flow, "providerId": "basic-flow", "topLevel": True, "builtIn": False})
    for provider in ("auth-username-password-form", "auth-otp-form"):
        await _json(admin, "POST", base + "/authentication/flows/" + browser_flow + "/executions/execution",
                    json={"provider": provider})
    executions = await _json(admin, "GET", base + "/authentication/flows/" + browser_flow + "/executions")
    for execution in executions:
        execution["requirement"] = "REQUIRED"
        await _json(admin, "PUT", base + "/authentication/flows/" + browser_flow + "/executions", json=execution)
    await _json(admin, "POST", base + "/authentication/flows/" + quote("reset credentials", safe="") + "/copy",
                json={"newName": reset_flow})
    reset = await _json(admin, "GET", base + "/authentication/flows/" + reset_flow + "/executions")
    otp = next(item for item in reset if item.get("authenticationFlow")
               and item.get("displayName") == reset_flow + " Reset - Conditional OTP")
    otp["requirement"] = "DISABLED"
    await _json(admin, "PUT", base + "/authentication/flows/" + reset_flow + "/executions", json=otp)
    email = next(item for item in reset if item.get("providerId") == "reset-credential-email")
    await _json(admin, "POST", base + "/authentication/executions/" + email["id"] + "/config",
                json={"alias": "force-fresh-login", "config": {"force-login": "true"}})
    await _json(admin, "PUT", base, json={"browserFlow": browser_flow, "resetCredentialsFlow": reset_flow})
    policy_hash = hashlib.sha256(json.dumps({"browser": executions, "reset": reset, "force-login": "true",
                                             "otp_period": 30}, sort_keys=True).encode()).hexdigest()
    return base, subject, policy_hash


async def _navigate(browser, page, env, realm):
    for _ in range(8):
        if isinstance(page, str):
            page = await browser.get(_trusted(page, env.keycloak_origin, realm))
        if page.status_code not in (302, 303):
            return page
        destination = urljoin(str(page.url), page.headers["location"])
        if urlsplit(destination).netloc == urlsplit(APP).netloc:
            return _trusted(destination, env.keycloak_origin, realm, callback=True)
        page = await browser.get(_trusted(destination, env.keycloak_origin, realm))
    raise ValueError("Provider redirect budget exceeded.")


def _document(page):
    if isinstance(page, str) or len(page.content) > 262144:
        raise ValueError("Unexpected provider page or premature callback.")
    return _Document(page.text)


def _recovery_denied(page):
    document = _document(page)
    assert page.status_code in (200, 400)
    assert not document.has("password-new") and not document.has("otp")
    recognized = (document.ids.intersection({"kc-error-message", "kc-info-message", "loginRestartLink", "loginContinueLink"})
                  or document.has("password") or "kc-reset-password-form" in document.ids)
    if not recognized:
        # Only fixed HTML structure is diagnostic; never include text, input values or links.
        names = [name for name in sorted(document.ids)
                 if len(name) <= 80 and all(char in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-" for char in name)]
        print("K03_RECOVERY_UNRECOGNIZED_PAGE_STRUCTURE=" + json.dumps({"status": page.status_code, "ids": names}), flush=True)
    assert recognized


def _password_denied(page):
    document = _document(page)
    assert page.status_code == 200 and document.has("password") and document.has("username")
    assert not document.has("otp")


async def _submit(browser, page, field, data, env, realm):
    form = _document(page).form(field)
    destination = _trusted(urljoin(str(page.url), form["action"]), env.keycloak_origin, realm)
    response = await browser.post(destination, data={**form["hidden"], **data})
    return await _navigate(browser, response, env, realm)


async def _password_page(app, browser, password, env, realm, *, username="recovery-user"):
    start = await app.flow.begin(None, return_to="/")
    page = await _navigate(browser, start.authorization_url.get_secret_value(), env, realm)
    if password is not None:
        page = await _submit(browser, page, "password", {"username": username, "password": password}, env, realm)
    return start, page


async def _mail_link(mailbox, env, realm):
    async with asyncio.timeout(15):
        while True:
            payload = await _json(mailbox, "GET", "/api/v1/messages")
            if payload.get("messages"):
                message = await _json(mailbox, "GET", "/api/v1/message/" + payload["messages"][0]["ID"])
                links = _Document(message.get("HTML", "")).links
                choices = [url for url in links if "/login-actions/action-token?" in url]
                assert len(choices) == 1
                return _trusted(choices[0], env.keycloak_origin, realm)
            await asyncio.sleep(0.1)


async def _request_reset(app, browser, mailbox, env, realm):
    await _json(mailbox, "DELETE", "/api/v1/messages")
    _, page = await _password_page(app, browser, None, env, realm)
    links = [urljoin(str(page.url), link) for link in _document(page).links
             if "/login-actions/reset-credentials?" in link]
    assert len(links) == 1
    page = await _navigate(browser, links[0], env, realm)
    page = await _submit(browser, page, "username", {"username": "recovery-user"}, env, realm)
    assert not isinstance(page, str)
    return await _mail_link(mailbox, env, realm)


async def _fresh_otp(seed, last_step):
    step = int(time.time()) // 30
    if step == last_step:
        print("K03_RECOVERY_WAITING_FOR_NEW_OTP_STEP", flush=True)
        while int(time.time()) // 30 == last_step:
            await asyncio.sleep(0.25)
    now = time.time()
    return _totp(seed, now), int(now) // 30


async def _mfa_callback(app, browser, password, seed, env, realm, last_step=None, *, username="recovery-user"):
    start, page = await _password_page(app, browser, password, env, realm, username=username)
    assert _document(page).has("otp")  # Password alone never reaches the app callback.
    nearby = {_totp(seed, time.time() + offset * 30) for offset in (-1, 0, 1)}
    bad = next(str(value).zfill(6) for value in range(100) if str(value).zfill(6) not in nearby)
    page = await _submit(browser, page, "otp", {"otp": bad}, env, realm)
    assert _document(page).has("otp")
    code, step = await _fresh_otp(seed, last_step)
    callback = await _submit(browser, page, "otp", {"otp": code}, env, realm)
    assert isinstance(callback, str) and "code" in parse_qs(urlsplit(callback).query)
    return start, callback, step


async def _login(app, browser, password, seed, env, realm, last_step=None, *, username="recovery-user"):
    start, callback, step = await _mfa_callback(app, browser, password, seed, env, realm, last_step, username=username)
    completed = await app.flow.complete((LOGIN_COOKIE + "=" + start.browser_credential.get_secret_value()).encode(),
                                        urlsplit(callback).query.encode("ascii"))
    assert completed.return_to == "/"
    return completed, step


@asynccontextmanager
async def _application(env, admin, base, subject, password):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg_pool import AsyncConnectionPool

    from aletheia_lab.hosted.persistence_migration import LOGIN_CLOCK_DDL

    schema = _schema("k03_recovery_" + uuid.uuid4().hex)
    db = dict(host="127.0.0.1", port=database_port(), dbname="aletheia", user="aletheia", password=password, connect_timeout=5)
    created = False
    async with await psycopg.AsyncConnection.connect(**db, autocommit=True) as owner:
        try:
            await owner.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            created = True
            await owner.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            for name in ("session", "fresh-login"):
                await owner.execute((ROOT / "docs" / ("j01-experimental-" + name + "-schema.sql")).read_text(encoding="utf-8"))
            await owner.execute(LOGIN_CLOCK_DDL)
            issuer = env.keycloak_origin + "/realms/" + base.split("/")[-1]
            await owner.execute("INSERT INTO hosted_identity_bindings VALUES (%s,%s,'usr_recovery','Synthetic',"
                                "'recovery-a@aletheia.invalid',true,true,0,'needs_workspace',NULL,NULL)", (issuer, subject))
            async with AsyncConnectionPool(conninfo="", kwargs={**db, "options": "-c search_path=" + schema},
                                           min_size=1, max_size=4, timeout=5) as pool:
                runtime = RuntimeSettings.model_validate({
                    "environment": "local", "public_origin": APP,
                    "identity": {"issuer": issuer, "client_id": CLIENT, "redirect_uri": CALLBACK, "client_kind": "public"},
                    "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(ROOT)},
                    "api": {"backend_timeout_seconds": 15.0},
                })
                policy = AuthPolicy(default_return_to="/", return_to_paths=("/",), required_acr="1", idp_timeout_seconds=10.0)
                # Bootstrap key generation is outside the production request deadline.
                async with httpx2.AsyncClient(transport=httpx2.AsyncHTTPTransport(verify=env.tls_context),
                                               trust_env=False, timeout=30, follow_redirects=False) as warm:
                    metadata_response = await warm.get(issuer + "/.well-known/openid-configuration")
                    assert metadata_response.status_code == 200
                    metadata = metadata_response.json()
                    assert metadata["issuer"] == issuer
                    realm = base.split("/")[-1]
                    for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                        _trusted(metadata[field], env.keycloak_origin, realm)
                    key_response = await warm.get(metadata["jwks_uri"])
                    assert key_response.status_code == 200
                    keys = key_response.json()["keys"]
                    print("K03_RECOVERY_PROVIDER_BOOTSTRAP_KEY_SHAPES=" +
                          json.dumps([{"kty": key.get("kty"), "use": key.get("use"),
                                       "operations": key.get("key_ops")} for key in keys]), flush=True)
                provider = RefreshingOidc(runtime, policy,
                    transport_factory=lambda: httpx2.AsyncHTTPTransport(verify=env.tls_context))
                await provider._current()
                flow = LoginCoordinator(PostgresFreshLogin(pool, LoginProofCipher(SecretBytes(secrets.token_bytes(32))),
                                                         clock_skew_seconds=30), provider,
                    AccountResolver(PostgresIdentityDirectory(pool), policy, expected_issuer=issuer, timeout_seconds=2),
                    runtime, policy, csrf_factory=CsrfTokens(SecretStr(secrets.token_urlsafe(32))).issue, timeout_seconds=15)
                sessions = ServerSessions(PostgresSessionStore(pool), policy, timeout_seconds=5, maximum_cookie_bytes=4096)
                yield SimpleNamespace(flow=flow, sessions=sessions, pool=pool, schema=schema,
                                      operator_database=SecretStr(make_conninfo(**db)))
        finally:
            if created:
                # Cleanup must survive a failed/closed long-lived fixture connection.
                async with await psycopg.AsyncConnection.connect(**db, autocommit=True) as cleanup:
                    await cleanup.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(_schema(schema))))
                print("K03_RECOVERY_OWN_DB_SCHEMA_REMOVED=PASS", flush=True)


def _cookie(completed):
    return (SESSION_COOKIE + "=" + completed.credential.get_secret_value()).encode()


async def _otp_ids(admin, base, subject):
    credentials = await _json(admin, "GET", base + "/users/" + subject + "/credentials")
    return sorted(item["id"] for item in credentials if item["type"] == "otp")


def _reset_available(page):
    document = _document(page)
    if document.has("password-new"):
        assert page.status_code == 200 and not document.has("totpSecret") and not document.has("otp")
        return True
    _recovery_denied(page)
    return False


async def _other_account(app, browser, admin, env, realm, base):
    password, seed = secrets.token_urlsafe(24), base64.b32encode(secrets.token_bytes(20)).decode("ascii")
    subject = await _create_user(admin, base, "recovery-user-b", password, seed, "recovery-b@aletheia.invalid")
    async with app.pool.connection() as connection:
        await connection.execute("INSERT INTO hosted_identity_bindings VALUES (%s,%s,'usr_recovery_b','Synthetic',"
            "'recovery-b@aletheia.invalid',true,true,0,'needs_workspace',NULL,NULL)",
            (env.keycloak_origin + "/realms/" + realm, subject))
    completed, step = await _login(app, browser, password, seed, env, realm, username="recovery-user-b")
    assert completed.session.actor.user_id == "usr_recovery_b"
    assert any(cookie.name.startswith("KEYCLOAK_IDENTITY") for cookie in browser.cookies.jar)
    # This HTTPX app-cookie model is separate from hosted browser/UI qualification.
    browser.cookies.set(SESSION_COOKIE, completed.credential.get_secret_value(), domain=urlsplit(APP).hostname, path="/")
    factors = await _otp_ids(admin, base, subject)
    assert len(factors) == 1
    record = await app.sessions.resolve_cookie(_cookie(completed))
    assert record is not None and record.actor.user_id == "usr_recovery_b"
    return SimpleNamespace(browser=browser, subject=subject, password=SecretStr(password), seed=SecretStr(seed),
        completed=completed, step=step, factors=factors, record=record,
        app_cookie=SecretStr(browser.cookies.get(SESSION_COOKIE, domain=urlsplit(APP).hostname, path="/")))


async def _other_account_unchanged(app, other):
    assert other.browser.cookies.get(SESSION_COOKIE, domain=urlsplit(APP).hostname, path="/") == other.app_cookie.get_secret_value()
    assert await app.sessions.resolve_cookie(_cookie(other.completed)) == other.record


async def _operator_revoke(app, issuer, subject):
    environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "PYTHONPATH")
                   if key in os.environ}
    environment.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
                       K03_RECOVERY_OPERATOR_DATABASE_URL=app.operator_database.get_secret_value())
    descriptor, filename = tempfile.mkstemp(prefix="aletheia-k03-revoke-", suffix=".json")
    identity = Path(filename).resolve()
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as document:
            json.dump({"issuer": issuer, "subject": subject}, document)
        arguments = [sys.executable, "-B", str(ROOT / "scripts/k04_workspace_admin.py"), "revoke-sessions",
                     "--schema", _schema(app.schema), "--identity-file", str(identity),
                     "--expected-issuer", issuer, "--database-url-env", "K03_RECOVERY_OPERATOR_DATABASE_URL"]
        def run():
            return subprocess.run(arguments, cwd=ROOT, env=environment, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=45, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        result = await asyncio.to_thread(run)
        assert result.returncode == 0 and not result.stderr
        output = json.loads(result.stdout)
        assert type(output) is dict and set(output) == {"sessions_revoked"} and output["sessions_revoked"] is True
    finally:
        assert identity.parent == Path(tempfile.gettempdir()).resolve() and identity.name.startswith("aletheia-k03-revoke-")
        identity.unlink(missing_ok=True)


async def _cross_identity_recovery(app, browser, mailbox, admin, env, realm, base,
                                   subject, password, seed, last_step, other):
    global STAGE
    reset_password = secrets.token_urlsafe(24)
    factor_a = await _otp_ids(admin, base, subject)
    STAGE = "cross_identity_a_link_in_b_browser"
    link = await _request_reset(app, browser, mailbox, env, realm)
    page = await _navigate(other.browser, link, env, realm)
    applied = _reset_available(page)
    if applied:
        page = await _submit(other.browser, page, "password-new",
            {"password-new": reset_password, "password-confirm": reset_password, "login": "Submit"}, env, realm)
        _recovery_denied(page)
        password = reset_password
    await _other_account_unchanged(app, other)
    assert await _otp_ids(admin, base, subject) == factor_a
    assert await _otp_ids(admin, base, other.subject) == other.factors
    STAGE = "cross_identity_a_still_requires_own_otp"
    completed_a, last_step = await _login(app, browser, password, seed, env, realm, last_step=last_step)
    assert completed_a.session.actor.user_id == "usr_recovery"
    STAGE = "cross_identity_b_credentials_still_work"
    continued_b, other.step = await _login(app, other.browser, other.password.get_secret_value(),
        other.seed.get_secret_value(), env, realm, last_step=other.step, username="recovery-user-b")
    assert continued_b.session.actor.user_id == "usr_recovery_b"
    await _other_account_unchanged(app, other)
    assert await _otp_ids(admin, base, subject) == factor_a
    assert await _otp_ids(admin, base, other.subject) == other.factors
    print("K03_RECOVERY_A_LINK_IN_B_BROWSER_NO_APP_CONTEXT_OR_FACTOR_TRANSFER=PASS", flush=True)
    print("K03_RECOVERY_CROSS_BROWSER_PASSWORD_RESET_APPLIED=" + str(applied), flush=True)
    return password, last_step


async def _new_subject_same_email(app, admin, env, realm, base):
    global STAGE
    STAGE = "new_subject_same_email_fixture"
    # Duplicate email is confined to this disposable realm; login remains by exact username.
    await _json(admin, "PUT", base, json={"duplicateEmailsAllowed": True, "loginWithEmailAllowed": False})
    password = secrets.token_urlsafe(24)
    seed = base64.b32encode(secrets.token_bytes(20)).decode("ascii")
    subject = await _create_user(admin, base, "recovery-new-subject", password, seed, "recovery-a@aletheia.invalid")
    query = "SELECT (SELECT count(*) FROM hosted_identity_bindings),(SELECT count(*) FROM j01_sessions)"
    async with app.pool.connection() as connection:
        before = await (await connection.execute(query)).fetchone()
    async with httpx.AsyncClient(verify=env.tls_context, trust_env=False, timeout=8) as browser:
        STAGE = "new_subject_valid_mfa_signed_oidc_unprovisioned"
        start, callback, _ = await _mfa_callback(app, browser, password, seed, env, realm, username="recovery-new-subject")
        cookie = (LOGIN_COOKIE + "=" + start.browser_credential.get_secret_value()).encode()
        callback_query = urlsplit(callback).query.encode("ascii")
        with pytest.raises(IdentityRejected) as denied:
            await app.flow.complete(cookie, callback_query)
        assert denied.value.reason == "unprovisioned"
        with pytest.raises(LoginRejected):
            await app.flow.complete(cookie, callback_query)
    async with app.pool.connection() as connection:
        assert await (await connection.execute(query)).fetchone() == before
        assert (await (await connection.execute("SELECT count(*) FROM hosted_identity_bindings "
            "WHERE issuer=%s AND subject=%s", (env.keycloak_origin + "/realms/" + realm, subject))).fetchone())[0] == 0
    print("K03_RECOVERY_REAL_NEW_SUBJECT_REUSED_EMAIL_UNPROVISIONED_NO_SESSION_REPLAY_SPENT=PASS", flush=True)


async def _exercise(env, database_password):
    global STAGE
    realm = "k03-recovery-" + uuid.uuid4().hex
    old_password, new_password = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    seed = base64.b32encode(secrets.token_bytes(20)).decode("ascii")
    async with (httpx.AsyncClient(base_url=env.keycloak_origin, verify=env.tls_context, trust_env=False, timeout=30) as admin,
                httpx.AsyncClient(verify=env.tls_context, trust_env=False, timeout=8) as browser,
                httpx.AsyncClient(base_url=env.mailbox_origin, trust_env=False, timeout=5) as mailbox,
                httpx.AsyncClient(verify=env.tls_context, trust_env=False, timeout=8) as browser_b):
        STAGE = "provider_setup"
        base, subject, policy_hash = await _setup(env, admin, realm, old_password, seed)
        factors = await _otp_ids(admin, base, subject)
        assert len(factors) == 1
        async with _application(env, admin, base, subject, database_password) as app:
            STAGE = "baseline_mfa"
            before, step = await _login(app, browser, old_password, seed, env, realm)
            assert (await app.sessions.resolve_cookie(_cookie(before))).actor.user_id == "usr_recovery"
            print("K03_RECOVERY_BASELINE_PASSWORD_AND_EXISTING_OTP=PASS", flush=True)
            STAGE = "expired_recovery"
            await _json(admin, "PUT", base, json={"actionTokenGeneratedByUserLifespan": 2})
            expired = await _request_reset(app, browser, mailbox, env, realm)
            assert (await _json(admin, "GET", base))["actionTokenGeneratedByUserLifespan"] == 2
            await asyncio.sleep(5)
            async with httpx.AsyncClient(verify=env.tls_context, trust_env=False, timeout=8) as fresh:
                page = await _navigate(fresh, expired, env, realm)
            _recovery_denied(page)
            assert await _otp_ids(admin, base, subject) == factors
            _, page = await _password_page(app, browser, old_password, env, realm)
            assert _document(page).has("otp")
            print("K03_RECOVERY_REAL_EXPIRED_LINK_NO_PASSWORD_CHANGE=PASS", flush=True)
            STAGE = "password_reset"
            await _json(admin, "PUT", base, json={"actionTokenGeneratedByUserLifespan": 120})
            link = await _request_reset(app, browser, mailbox, env, realm)
            page = await _navigate(browser, link, env, realm)
            page = await _submit(browser, page, "password-new",
                                 {"password-new": new_password, "password-confirm": new_password, "login": "Submit"}, env, realm)
            assert not isinstance(page, str) and page.status_code == 200 and not _document(page).has("totpSecret")
            assert await _otp_ids(admin, base, subject) == factors
            async with httpx.AsyncClient(verify=env.tls_context, trust_env=False, timeout=8) as fresh:
                replay = await _navigate(fresh, link, env, realm)
            _recovery_denied(replay)
            _recovery_denied(await _navigate(browser, link, env, realm))
            _, denied = await _password_page(app, browser, old_password, env, realm)
            _password_denied(denied)
            _, guarded = await _password_page(app, browser, new_password, env, realm)
            assert _document(guarded).has("otp")
            print("K03_RECOVERY_RESET_REPLAY_DENIED_OLD_PASSWORD_DENIED_EXISTING_OTP_RETAINED=PASS", flush=True)
            STAGE = "other_account_before_operator_revoke"
            other = await _other_account(app, browser_b, admin, env, realm, base)
            STAGE = "trusted_operator_revoke"
            # The real manual CLI is trusted; an automatic IdP event bridge remains separate.
            assert await app.sessions.resolve_cookie(_cookie(before)) is not None
            await _operator_revoke(app, env.keycloak_origin + "/realms/" + realm, subject)
            async with app.pool.connection() as connection:
                epoch = await (await connection.execute(
                    "SELECT account_epoch,logout_all_at FROM hosted_identity_bindings WHERE user_id='usr_recovery'")).fetchone()
                assert epoch[0] == before.session.account_epoch + 1 and epoch[1] is not None
                watermark = (epoch[1],)
            await _other_account_unchanged(app, other)
            # Begin after the real DB watermark; never relax the production pending-intent fence.
            ahead = watermark[0].timestamp() - time.time()
            print("K03_RECOVERY_DB_WATERMARK_AHEAD_SECONDS=" + format(ahead, ".3f"), flush=True)
            assert abs(ahead) <= 30
            if ahead >= 0:
                await asyncio.sleep(ahead + 0.02)
            with pytest.raises(AccessDenied):
                await app.sessions.resolve_cookie(_cookie(before))
            print("K03_RECOVERY_REAL_OPERATOR_REVOKES_A_PRESERVES_B=PASS", flush=True)
            print("K03_RECOVERY_REVOCATION_MODE=TRUSTED_MANUAL_OPERATOR_NOT_AUTOMATIC_EVENT_BRIDGE", flush=True)
            STAGE = "fresh_mfa_after_reset"
            after, step = await _login(app, browser, new_password, seed, env, realm, last_step=step)
            assert after.session.context_id != before.session.context_id
            assert after.session.actor.user_id == before.session.actor.user_id
            assert after.session.account_epoch == after.session.session_epoch == 1
            assert await _otp_ids(admin, base, subject) == factors
            print("K03_RECOVERY_FRESH_LOGIN_REQUIRES_OLD_OTP_AND_SUBJECT_CONTINUITY=PASS", flush=True)
            new_password, step = await _cross_identity_recovery(
                app, browser, mailbox, admin, env, realm, base, subject, new_password, seed, step, other)
            await _new_subject_same_email(app, admin, env, realm, base)
            STAGE = "disabled_provider_account"
            await _json(admin, "PUT", base + "/users/" + subject, json={"enabled": False})
            assert (await _json(admin, "GET", base + "/users/" + subject))["enabled"] is False
            _, denied = await _password_page(app, browser, new_password, env, realm)
            _password_denied(denied)
            print("K03_RECOVERY_DISABLED_IDP_ACCOUNT_NO_CALLBACK=PASS", flush=True)
        print("K03_RECOVERY_POLICY_HASH=" + policy_hash, flush=True)
        for name, digest in env.image_digests:
            print("K03_RECOVERY_IMAGE=" + name + "@" + digest, flush=True)
        print("K03_RECOVERY_SCOPE=LOCAL_POLICY_REAL_IDP_SMTP_AUTHLIB_DATABASE_TRUSTED_MANUAL_OPERATOR_NO_HOSTED_BROWSER_NO_AUTOMATIC_EVENT_BRIDGE", flush=True)


def test_totp_fixture_matches_public_sha1_vector():
    assert _totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", 59) == "287082"


def test_provider_form_preserves_only_hidden_fields_and_unescapes_action():
    document = _Document('<form action="/a?x=1&amp;y=2"><input type="hidden" name="selectedCredentialId" value="fixture">'
                         '<input name="otp"><input type="checkbox" name="rememberMe"></form>')
    form = document.form("otp")
    assert form["action"] == "/a?x=1&y=2" and form["hidden"] == {"selectedCredentialId": "fixture"}


@pytest.mark.parametrize("url", ["https://evil.invalid/realms/fixture/x", "http://localhost:8443/realms/fixture/x",
                                    "https://localhost:8443/realms/other/x", "https://u:p@localhost:8443/realms/fixture/x",
                                    "https://localhost:8443/realms/fixture/../../admin",
                                    "https://localhost:8443/realms/fixture/%2e%2e/admin",
                                    "https://localhost:8443/realms/fixture/a\\\\admin"])
def test_recovery_driver_cannot_follow_untrusted_urls(url):
    with pytest.raises(ValueError):
        _trusted(url, "https://localhost:8443", "fixture")


@pytest.mark.parametrize("name", ["public", "k03_recovery_x", "k03_recovery_" + "a" * 32 + ";drop"])
def test_recovery_cleanup_namespace_is_closed(name):
    with pytest.raises(ValueError):
        _schema(name)


@pytest.mark.parametrize("status", [500, 503])
def test_recovery_denial_does_not_treat_provider_outage_as_rejection(status):
    page = httpx.Response(status, text='<div id="kc-error-message">fixture</div>')
    with pytest.raises(AssertionError):
        _recovery_denied(page)


def test_mailbox_delete_accepts_successful_non_json_and_rejects_failure():
    async def check():
        async with httpx.AsyncClient(base_url="http://127.0.0.1",
                transport=httpx.MockTransport(lambda request: httpx.Response(200, text="Deleted"))) as client:
            assert await _json(client, "DELETE", "/api/v1/messages") is None
        async with httpx.AsyncClient(base_url="http://127.0.0.1",
                transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
            with pytest.raises(ValueError, match="Provider fixture operation rejected: HTTP 503"):
                await _json(client, "DELETE", "/api/v1/messages")
    asyncio.run(check())


@pytest.mark.parametrize("html,expected", [
    ('<form><input name="password-new"></form>', True),
    ('<div id="kc-error-message">fixture</div>', False),
    ('<a id="loginContinueLink">fixture</a>', False),
])
def test_cross_identity_reset_accepts_form_or_noncompleted_challenge(html, expected):
    assert _reset_available(httpx.Response(200, text=html)) is expected


@pytest.mark.parametrize("html", [
    '<form><input name="password-new"><input name="totpSecret"></form>',
    '<form><input name="password-new"><input name="otp"></form>',
])
def test_cross_identity_reset_never_accepts_factor_enrollment_or_otp_challenge(html):
    with pytest.raises(AssertionError):
        _reset_available(httpx.Response(200, text=html))


def test_named_fixture_login_forwards_selected_username(monkeypatch):
    async def scenario():
        captured = {}
        async def navigate(*args):
            return httpx.Response(200, text='<form><input name="password"></form>')
        async def submit(browser, page, field, data, env, realm):
            captured.update(data)
            return page
        class Flow:
            async def begin(self, *args, **kwargs):
                return SimpleNamespace(authorization_url=SecretStr("https://localhost/realms/fixture/authorize"))
        monkeypatch.setattr(sys.modules[__name__], "_navigate", navigate)
        monkeypatch.setattr(sys.modules[__name__], "_submit", submit)
        await _password_page(SimpleNamespace(flow=Flow()), None, "synthetic-password", None, "fixture",
                             username="recovery-user-b")
        assert captured == {"username": "recovery-user-b", "password": "synthetic-password"}
    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", [True, False, 1])
def test_operator_cli_uses_private_identity_file_and_environment_only_database(monkeypatch, outcome):
    captured = {}
    def run(arguments, **kwargs):
        identity = Path(arguments[arguments.index("--identity-file") + 1])
        captured.update(path=identity, identity=json.loads(identity.read_text(encoding="utf-8")),
                        arguments=arguments, environment=kwargs["env"])
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({"sessions_revoked": outcome}))
    async def scenario():
        app = SimpleNamespace(schema="k03_recovery_" + "a" * 32,
                              operator_database=SecretStr("postgresql://fixture:synthetic-private@127.0.0.1/fixture"))
        monkeypatch.setattr(subprocess, "run", run)
        if outcome is True:
            await _operator_revoke(app, "https://identity.example.invalid", "synthetic-subject")
        else:
            with pytest.raises(AssertionError):
                await _operator_revoke(app, "https://identity.example.invalid", "synthetic-subject")
        assert not captured["path"].exists()
        assert captured["identity"] == {"issuer": "https://identity.example.invalid", "subject": "synthetic-subject"}
        assert "synthetic-private" not in " ".join(captured["arguments"])
        assert captured["environment"]["K03_RECOVERY_OPERATOR_DATABASE_URL"] == app.operator_database.get_secret_value()
        assert "K03_DB_PASSWORD" not in captured["environment"]
    asyncio.run(scenario())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-recovery", action="store_true", required=True)
    parser.parse_args()
    database_password = os.environ.pop("K03_DB_PASSWORD")
    try:
        with (recovery_environment() as environment,
              asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner):
            runner.run(_exercise(environment, database_password))
    except Exception as error:
        print("K03_RECOVERY_CHECK=FAIL:" + type(error).__name__, flush=True)
        print("K03_RECOVERY_STAGE=" + STAGE, flush=True)
        print("K03_RECOVERY_JSON_OPERATION=" + JSON_OPERATION, flush=True)
        if isinstance(error, ValueError) and str(error).startswith("Provider fixture operation rejected: HTTP "):
            print("K03_RECOVERY_SAFE_HTTP_STATUS=" + str(error).rsplit(" ", 1)[-1], flush=True)
        frames = traceback.extract_tb(error.__traceback__)
        if frames:
            print("K03_RECOVERY_FAILURE_LINE=" + str(frames[-1].lineno), flush=True)
        raise SystemExit(1) from None
