"""Opt-in real local IdP stop/start; default pytest runs offline guards only.

Own pinned Keycloak, loopback port, TLS directory, bridge and PostgreSQL only.
No public IdP, shared service, browser UI, deployment or production SLO proof.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import secrets
import socket
import ssl
import sys
import tempfile
import time
import traceback
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import httpx
import httpx2
import pytest
from pydantic import SecretBytes, SecretStr
from test_k03_keycloak_recovery import (
    APP,
    CALLBACK,
    CLIENT,
    _Document,
    _fresh_otp,
    _mfa_callback,
    _setup,
)
from test_k03_postgres_environment import private_postgres
from test_k03_recovery_environment import (
    KEYCLOAK_IMAGE,
    _certificate,
    _cleanup,
    _docker,
    _image,
    _owned_directory,
    _owned_name,
)

from aletheia_lab.hosted.application import AdapterLimits, HostedApplication
from aletheia_lab.hosted.read_backend import ReadOnlyHostedBackend
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted_auth.abuse import BoundedOidc
from aletheia_lab.hosted_auth.boundary import LOGIN_COOKIE, SESSION_COOKIE
from aletheia_lab.hosted_auth.callback_http import CallbackApplication
from aletheia_lab.hosted_auth.clock_guard import probe_clock
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.identity import AccountResolver
from aletheia_lab.hosted_auth.login import LoginCoordinator
from aletheia_lab.hosted_auth.login_proof_cipher import LoginProofCipher
from aletheia_lab.hosted_auth.logout_http import LogoutApplication
from aletheia_lab.hosted_auth.oidc import OidcUnavailable
from aletheia_lab.hosted_auth.oidc_discovery import RefreshingOidc
from aletheia_lab.hosted_auth.postgres_fresh_login import PostgresFreshLogin
from aletheia_lab.hosted_auth.postgres_identity import PostgresIdentityDirectory
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.sessions import ServerSessions
from aletheia_lab.hosted_auth.settings import AuthPolicy
from aletheia_lab.hosted_contract.session_capabilities import Limits

ROOT = Path(__file__).resolve().parents[2]
OWNER_LABEL = "com.aletheia.k03.recovery"
KEYCLOAK_DIGEST = "3879c623248408de29a5303178b5816c77e8dfef3890c361f8c0bbeaa979ba3b"
STAGE = "offline"


def _schema(name):
    if not isinstance(name, str) or not re.fullmatch(r"k03_outage_[0-9a-f]{32}", name):
        raise ValueError("An exact owned outage schema is required.")
    return name


def _metadata(name, owner, image, port, identity=None, *, state_observation=None):
    _owned_name(name, "keycloak")
    if name != "aletheia-k03-recovery-" + owner + "-keycloak" or not re.fullmatch("[0-9a-f]{32}", owner):
        raise ValueError("An exact owned restart container is required.")
    template = ('{"id":{{json .Id}},"name":{{json .Name}},"owner":'
                '{{json (index .Config.Labels "com.aletheia.k03.recovery")}},'
                '"image":{{json .Image}},"remove":{{json .HostConfig.AutoRemove}},'
                '"configured":{{json .HostConfig.PortBindings}},"ports":{{json .NetworkSettings.Ports}},'
                '"running":{{json .State.Running}},"exit_code":{{json .State.ExitCode}},'
                '"oom_killed":{{json .State.OOMKilled}}}')
    value = json.loads(_docker("container", "inspect", "--format", template, name,
                              operation="restart_identity") or "")
    binding = [{"HostIp": "127.0.0.1", "HostPort": str(port)}]
    if (not isinstance(value, dict) or not re.fullmatch("[0-9a-f]{64}", str(value.get("id", "")))
            or (identity is not None and value["id"] != identity) or value.get("name") != "/" + name
            or value.get("owner") != owner or value.get("image") != image or value.get("remove") is not False
            or value.get("configured") != {"8443/tcp": binding} or type(value.get("running")) is not bool
            or type(value.get("exit_code")) is not int or not 0 <= value["exit_code"] <= 255
            or type(value.get("oom_killed")) is not bool
            or type(port) is not int or not 1024 <= port <= 65535 or port == 15432):
        raise ValueError("Owned restart container metadata was rejected.")
    if value["running"]:
        ports = value.get("ports")
        if not isinstance(ports, dict) or {key: item for key, item in ports.items() if item} != {"8443/tcp": binding}:
            raise ValueError("Owned restart container binding was rejected.")
    if state_observation is not None:
        state_observation.update(container_state_known=True, running=value["running"],
                                 exit_code=value["exit_code"], oom_killed=value["oom_killed"])
    return value["id"], value["running"]


@dataclass(frozen=True)
class RestartableKeycloak:
    keycloak_origin: str
    tls_context: ssl.SSLContext = field(repr=False)
    admin_username: str = field(repr=False)
    admin_password: SecretStr = field(repr=False)
    tls_directory: Path = field(repr=False)
    image_digest: str
    _identity: str = field(repr=False)
    _name: str = field(repr=False)
    _owner: str = field(repr=False)
    _image: str = field(repr=False)
    _port: int = field(repr=False)
    smtp_host: str = "mail"
    smtp_port: int = 1025

    def _state(self, expected):
        identity, running = _metadata(self._name, self._owner, self._image, self._port, self._identity)
        if running is not expected:
            raise ValueError("Owned restart container state was rejected.")
        return identity

    def stop(self):
        identity = self._state(True)
        _docker("container", "stop", "--time", "3", identity, operation="owned_idp_stop", timeout=15)
        self._state(False)

    def start(self):
        identity = self._state(False)
        _docker("container", "start", identity, operation="owned_idp_start")
        self._state(True)
        _ready(self)


def _readiness_response(observed, response, issuer):
    observed["http_responses"] += 1
    observed["status"] = response.status_code
    media = response.headers.get("content-type", "").partition(";")[0].lower().strip()
    observed["media"] = media if media in (
        "application/problem+json", "application/json", "text/plain", "text/html") else ("missing" if not media else "other")
    try:
        payload = response.json()
    except ValueError:
        observed["invalid_documents"] += 1
        payload = None
    observed["issuer_match"] = isinstance(payload, dict) and payload.get("issuer") == issuer
    return response.status_code == 200 and observed["issuer_match"]


def _readiness_failure(observed, outcome, started):
    observed.update(outcome=outcome, elapsed_ms=max(0, int((time.monotonic() - started) * 1000)))
    print("K03_OUTAGE_READINESS_OBSERVATION=" + json.dumps(observed, sort_keys=True), flush=True)
    if outcome == "timeout":
        raise TimeoutError("Owned Keycloak readiness did not complete.")
    raise RuntimeError("Owned Keycloak readiness became unavailable.")


def _ready(environment):
    started = time.monotonic()
    deadline, next_state = started + 120, started
    observed = {"outcome": "timeout", "status": 0, "media": "missing", "issuer_match": False,
                "transport_errors": 0, "http_responses": 0, "invalid_documents": 0, "elapsed_ms": 0,
                "container_state_known": False, "running": False, "exit_code": 0, "oom_killed": False}
    with httpx.Client(verify=environment.tls_context, trust_env=False, timeout=2, follow_redirects=False) as client:
        while time.monotonic() < deadline:
            if time.monotonic() >= next_state:
                try:
                    _metadata(environment._name, environment._owner, environment._image, environment._port,
                              environment._identity, state_observation=observed)
                except Exception:
                    observed.update(container_state_known=False, running=False, exit_code=0, oom_killed=False)
                    _readiness_failure(observed, "container_metadata_unavailable", started)
                if not observed["running"]:
                    _readiness_failure(observed, "container_exited", started)
                next_state = time.monotonic() + 2
            try:
                response = client.get(environment.keycloak_origin + "/realms/master/.well-known/openid-configuration")
                if _readiness_response(observed, response, environment.keycloak_origin + "/realms/master"):
                    return
            except httpx.TransportError:
                observed["transport_errors"] += 1
            time.sleep(0.25)
    _readiness_failure(observed, "timeout", started)


@contextmanager
def restartable_keycloak():
    """Fresh private fixture; stop/start never address a caller-supplied container."""
    owner = uuid.uuid4().hex
    names = {kind: _owned_name("aletheia-k03-recovery-" + owner + "-" + kind, kind)
             for kind in ("network", "keycloak")}
    temporary = Path(tempfile.gettempdir()).resolve()
    directory = _owned_directory(temporary / ("aletheia-k03-recovery-" + owner), temporary, owner)
    directory.mkdir()
    attempted = []
    image = port = None
    try:
        tls = _certificate(directory)
        image, digest = _image(KEYCLOAK_IMAGE, allow_pull=False)
        if not digest.endswith("@sha256:" + KEYCLOAK_DIGEST):
            raise ValueError("Pinned local Keycloak image required.")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        label = OWNER_LABEL + "=" + owner
        attempted.append("network")
        _docker("network", "create", "--opt", "com.docker.network.bridge.host_binding_ipv4=127.0.0.1",
                "--label", label, names["network"], operation="outage_network")
        password = SecretStr(secrets.token_urlsafe(32))
        child = dict(os.environ, KC_BOOTSTRAP_ADMIN_USERNAME="k03-outage-admin",
                     KC_BOOTSTRAP_ADMIN_PASSWORD=password.get_secret_value())
        attempted.append("keycloak")
        try:
            identity = _docker("run", "--detach", "--pull=never", "--name", names["keycloak"],
                "--network", names["network"], "--label", label, "--memory=1g", "--cpus=2", "--pids-limit=128",
                "--cap-drop=ALL", "--security-opt=no-new-privileges", "--publish", "127.0.0.1:" + str(port) + ":8443",
                "--mount", "type=bind,src=" + str(directory) + ",dst=/k03,readonly",
                "--env", "KC_BOOTSTRAP_ADMIN_USERNAME", "--env", "KC_BOOTSTRAP_ADMIN_PASSWORD",
                image, "start-dev", "--http-enabled=false", "--hostname-strict=false",
                "--https-certificate-file=/k03/cert.pem", "--https-certificate-key-file=/k03/key.pem",
                operation="outage_identity_start", environment=child)
        finally:
            child.pop("KC_BOOTSTRAP_ADMIN_PASSWORD", None)
        actual, _ = _metadata(names["keycloak"], owner, image, port, identity)
        environment = RestartableKeycloak("https://localhost:" + str(port), tls, "k03-outage-admin",
            password, directory, digest, actual, names["keycloak"], owner, image, port)
        _ready(environment)
        yield environment
    finally:
        if "keycloak" in attempted:
            existing = _docker("container", "ls", "--all", "--no-trunc", "--filter", "label=" + OWNER_LABEL + "=" + owner,
                "--filter", "name=" + names["keycloak"], "--format", "{{.ID}}", operation="outage_cleanup_inventory") or ""
            if existing:
                _metadata(names["keycloak"], owner, image, port, existing)
        _cleanup(names, attempted, directory, temporary, owner)


def _provider_target(url, issuer, callback_uri):
    parsed, origin, callback = urlsplit(url), urlsplit(issuer), urlsplit(callback_uri)
    if (origin.scheme != "https" or origin.hostname not in ("localhost", "127.0.0.1")
            or origin.username or origin.password or origin.query or origin.fragment
            or callback.scheme != "https" or callback.username or callback.password or callback.query or callback.fragment
            or parsed.username or parsed.password or parsed.fragment
            or any(ord(char) <= 32 or ord(char) == 127 for char in url)):
        raise ValueError("An exact local provider or captured callback is required.")
    if (parsed.scheme, parsed.netloc, parsed.path) == (callback.scheme, callback.netloc, callback.path):
        return True
    decoded = unquote(parsed.path)
    if (parsed.scheme != origin.scheme or parsed.netloc != origin.netloc
            or not decoded.startswith(unquote(origin.path).rstrip("/") + "/")
            or any(part in (".", "..") for part in decoded.split("/")) or chr(92) in decoded):
        raise ValueError("An exact local provider realm is required.")
    return False


async def _provider_page(browser, page, issuer, callback_uri):
    for _ in range(8):
        if isinstance(page, str):
            if _provider_target(page, issuer, callback_uri):
                return page
            page = await browser.get(page)
        if page.status_code == 200:
            return page
        if page.status_code not in (302, 303):
            raise ValueError("Provider fixture navigation was rejected.")
        page = urljoin(str(page.url), page.headers["location"])
    raise ValueError("Provider fixture navigation limit reached.")


async def authentication_code(browser, *, issuer, authorization_url, callback_uri,
                              username, password, otp_seed=None, last_step=None):
    """Submit only to the own realm; capture exact API callback without fetching it."""
    page = await _provider_page(browser, authorization_url, issuer, callback_uri)
    if isinstance(page, str):
        raise ValueError("An explicit provider login form is required.")

    async def submit(current, field, data):
        form = _Document(current.text).form(field)
        destination = urljoin(str(current.url), form["action"])
        if _provider_target(destination, issuer, callback_uri):
            raise ValueError("Provider form cannot submit credentials to the API.")
        response = await browser.post(destination, data={**form["hidden"], **data})
        return await _provider_page(browser, response, issuer, callback_uri)

    page = await submit(page, "password", {"username": username, "password": password})
    step = None
    if otp_seed is not None:
        if isinstance(page, str):
            raise ValueError("Expected registered factor challenge.")
        code, step = await _fresh_otp(otp_seed, last_step)
        page = await submit(page, "otp", {"otp": code})
    if not isinstance(page, str) or not _provider_target(page, issuer, callback_uri):
        raise ValueError("Provider login did not produce the exact callback.")
    if len(parse_qs(urlsplit(page).query).get("code", [])) != 1:
        raise ValueError("Provider login did not produce one authorization code.")
    return page, step


async def prime_provider(environment, issuer):
    """Own-realm bootstrap only; does not change the production auth deadline."""
    _provider_target(issuer + "/.well-known/openid-configuration", issuer, CALLBACK)
    async with asyncio.timeout(30), httpx2.AsyncClient(
            transport=httpx2.AsyncHTTPTransport(verify=environment.tls_context),
            timeout=30, trust_env=False, follow_redirects=False) as client:
        metadata = await client.get(issuer + "/.well-known/openid-configuration")
        assert metadata.status_code == 200
        configured = metadata.json()
        assert configured["issuer"] == issuer
        keys_uri = issuer + "/protocol/openid-connect/certs"
        assert configured["jwks_uri"] == keys_uri
        keys = await client.get(keys_uri)
        assert keys.status_code == 200 and isinstance(keys.json().get("keys"), list)
    print("K03_OUTAGE_BOOTSTRAP_KEYS_READY=PASS", flush=True)


class _CountingTransport(httpx2.AsyncBaseTransport):
    def __init__(self, tls, calls):
        self.inner = httpx2.AsyncHTTPTransport(verify=tls)
        self.calls = calls

    async def handle_async_request(self, request):
        operation = "token" if request.url.path.endswith("/token") else "material"
        self.calls[operation] += 1
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


@asynccontextmanager
async def _application(environment, database, subject):
    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool

    from aletheia_lab.hosted.persistence_migration import LOGIN_CLOCK_DDL
    from aletheia_lab.hosted.request_boundary import BodyLimits

    schema = _schema("k03_outage_" + uuid.uuid4().hex)
    db = dict(host="127.0.0.1", port=database.port, dbname="aletheia", user="aletheia",
              password=database.password.get_secret_value(), connect_timeout=3)
    issuer = environment.keycloak_origin + "/realms/" + environment.realm
    created = False
    async with await psycopg.AsyncConnection.connect(**db, autocommit=True) as owner:
        try:
            await owner.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            created = True
            await owner.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            for name in ("session", "fresh-login"):
                await owner.execute((ROOT / "docs" / ("j01-experimental-" + name + "-schema.sql")).read_text(encoding="utf-8"))
            await owner.execute(LOGIN_CLOCK_DDL)
            await owner.execute("INSERT INTO hosted_identity_bindings VALUES (%s,%s,'usr_outage','Synthetic',"
                "'outage@aletheia.invalid',true,true,0,'needs_workspace',NULL,NULL)", (issuer, subject))
            async with AsyncConnectionPool(conninfo="", kwargs={**db, "options": "-c search_path=" + schema},
                                           min_size=1, max_size=4, timeout=3) as pool:
                runtime = RuntimeSettings.model_validate({"environment": "local", "public_origin": APP,
                    "identity": {"issuer": issuer, "client_id": CLIENT, "redirect_uri": CALLBACK, "client_kind": "public"},
                    "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(ROOT)},
                    "api": {"backend_timeout_seconds": 5.0}})
                policy = AuthPolicy(default_return_to="/", return_to_paths=("/",), required_acr="1")
                calls = {"token": 0, "material": 0}
                material = RefreshingOidc(runtime, policy,
                    transport_factory=lambda: _CountingTransport(environment.tls_context, calls))
                await prime_provider(environment, issuer)  # Cold key generation is fixture readiness only.
                await material._current()
                provider = BoundedOidc(material, timeout_seconds=policy.idp_timeout_seconds,
                    maximum_inflight=policy.idp_maximum_inflight,
                    initial_backoff_seconds=policy.idp_initial_backoff_seconds,
                    maximum_backoff_seconds=policy.idp_maximum_backoff_seconds)
                tokens = CsrfTokens(SecretStr(secrets.token_urlsafe(32)))
                flow = LoginCoordinator(PostgresFreshLogin(pool, LoginProofCipher(SecretBytes(secrets.token_bytes(32))),
                    clock_skew_seconds=policy.clock_skew_seconds), provider,
                    AccountResolver(PostgresIdentityDirectory(pool), policy, expected_issuer=issuer, timeout_seconds=2),
                    runtime, policy, csrf_factory=tokens.issue, timeout_seconds=runtime.api.backend_timeout_seconds)
                sessions = ServerSessions(PostgresSessionStore(pool), policy, timeout_seconds=5, maximum_cookie_bytes=4096)

                class Workspaces:
                    async def is_ready(self):
                        return True

                async def clock():
                    return await probe_clock(pool, max_skew_seconds=30, timeout_seconds=2)

                backend = ReadOnlyHostedBackend(sessions, Workspaces(),
                    limits=Limits(question_chars=2000, upload_bundle_bytes=1024, page_max=200, job_poll_min_ms=2000))
                http = CallbackApplication(HostedApplication(backend, limits=AdapterLimits(5, 65536, 4096)),
                    flow, policy, maximum_cookie_bytes=4096, timeout_seconds=5, clock_check=clock)
                http = LogoutApplication(http, sessions, tokens, runtime,
                                         body_limits=BodyLimits(1024, 1, 2, 16), clock_check=clock)
                yield SimpleNamespace(flow=flow, sessions=sessions, http=http, pool=pool,
                                      provider=provider, material=material, calls=calls, policy=policy)
        finally:
            if created:
                async with await psycopg.AsyncConnection.connect(**db, autocommit=True) as cleanup:
                    await cleanup.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(_schema(schema))))
                print("K03_OUTAGE_OWN_SCHEMA_REMOVED=PASS", flush=True)


async def _callback(client, start, destination):
    return await client.get(destination,
        headers={"cookie": LOGIN_COOKIE + "=" + start.browser_credential.get_secret_value()})


async def _count_sessions(pool):
    async with pool.connection() as connection:
        return (await (await connection.execute("SELECT count(*) FROM j01_sessions")).fetchone())[0]


async def _clock_sample(pool):
    async with pool.connection() as connection:
        database = (await (await connection.execute("SELECT clock_timestamp()")).fetchone())[0]
    return database, datetime.now(UTC), time.monotonic()


def _clock_observation(before, after):
    return {"host_db_before_ms": int((before[0] - before[1]).total_seconds() * 1000),
            "host_db_after_ms": int((after[0] - after[1]).total_seconds() * 1000),
            "db_minus_monotonic_elapsed_ms": int(((after[0] - before[0]).total_seconds()
                                                 - (after[2] - before[2])) * 1000)}


def _stopped_observation(response, elapsed, *, token_delta, material_delta, session_delta, cache_remaining_ms):
    media = response.headers.get("content-type", "").partition(";")[0].lower().strip()
    media = media if media in ("application/problem+json", "application/json", "text/plain", "text/html", "") else "other"
    reason, code = "none", "none"
    destination = response.headers.get("location", "")
    for allowed in ("idp_unavailable", "login_failed", "login_cancelled", "account_disabled"):
        if destination == "/#/signed-out?reason=" + allowed:
            reason = allowed
    if destination and reason == "none":
        reason = "other"
    if media in ("application/problem+json", "application/json") and len(response.content) <= 4096:
        try:
            value = response.json()
        except ValueError:
            value = None
        if isinstance(value, dict):
            candidate = value.get("code")
            allowed_codes = ("service_unavailable", "service_failure", "rate_limited", "invalid_request",
                             "session_revoked", "session_expired", "permission_denied", "authentication_required")
            code = candidate if isinstance(candidate, str) and candidate in allowed_codes else "other"
    return {"status": response.status_code, "media": media or "missing", "reason": reason, "code": code,
            "elapsed_ms": max(0, int(elapsed * 1000)), "set_cookie": bool(response.headers.get_list("set-cookie")),
            "body_empty": not response.content, "token_delta": token_delta, "material_delta": material_delta,
            "session_delta": session_delta, "cache_valid_at_stop": cache_remaining_ms > 0,
            "cache_remaining_ms": cache_remaining_ms}


async def _exercise(environment, database):
    global STAGE
    realm = "k03-outage-" + uuid.uuid4().hex
    password, seed = secrets.token_urlsafe(24), base64.b32encode(secrets.token_bytes(20)).decode("ascii")
    async with (httpx.AsyncClient(base_url=environment.keycloak_origin, verify=environment.tls_context,
                                  trust_env=False, timeout=30) as admin,
                httpx.AsyncClient(verify=environment.tls_context, trust_env=False, timeout=8) as browser):
        STAGE = "provider_setup"
        _, subject, _ = await _setup(environment, admin, realm, password, seed)
        local = SimpleNamespace(**environment.__dict__, realm=realm)
        async with _application(local, database, subject) as app, httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app.http), base_url=APP, follow_redirects=False) as client:
            STAGE = "healthy_login"
            start, destination, step = await _mfa_callback(app, browser, password, seed, environment, realm)
            completed = await _callback(client, start, destination)
            assert completed.status_code == 303 and completed.headers["location"] == "/"
            baseline = (await client.get("/v1/session")).json()
            old_cookie = client.cookies.get(SESSION_COOKIE)
            pending, destination, pending_step = await _mfa_callback(app, browser, password, seed, environment, realm, step)
            before, token_calls = await _count_sessions(app.pool), app.calls["token"]
            material_calls = app.calls["material"]
            STAGE = "stopped_callback"
            await asyncio.to_thread(environment.stop)
            cache_remaining_ms = int((app.material._expires - datetime.now(UTC)).total_seconds() * 1000)
            clock_before = await _clock_sample(app.pool)
            started = time.monotonic()
            denied = await _callback(client, pending, destination)
            elapsed = time.monotonic() - started
            clock_after = await _clock_sample(app.pool)
            print("K03_OUTAGE_CLOCK_OBSERVATION=" + json.dumps(
                _clock_observation(clock_before, clock_after), sort_keys=True), flush=True)
            session_delta = await _count_sessions(app.pool) - before
            observation = _stopped_observation(denied, elapsed, token_delta=app.calls["token"] - token_calls,
                material_delta=app.calls["material"] - material_calls, session_delta=session_delta,
                cache_remaining_ms=cache_remaining_ms)
            print("K03_OUTAGE_CALLBACK_OBSERVATION=" + json.dumps(observation, sort_keys=True), flush=True)
            assert denied.status_code == 303 and denied.headers["location"] == "/#/signed-out?reason=idp_unavailable"
            assert denied.content == b"" and not denied.headers.get_list("set-cookie")
            assert elapsed <= app.policy.idp_timeout_seconds + 1
            assert app.calls["token"] == token_calls + 1
            attempts = dict(app.calls)
            STAGE = "cooldown"
            results = await asyncio.gather(*(app.flow.begin(None) for _ in range(8)), return_exceptions=True)
            assert all(isinstance(value, OidcUnavailable) for value in results) and app.calls == attempts
            assert app.provider._active == 0 and app.provider._failures == 1
            assert await _count_sessions(app.pool) == before
            replay = await _callback(client, pending, destination)
            assert replay.status_code == 303 and replay.headers["location"] == "/#/signed-out?reason=login_failed"
            assert replay.content == b"" and not replay.headers.get_list("set-cookie") and app.calls == attempts
            print("K03_OUTAGE_REAL_STOP_CALLBACK_BOUNDED_NO_SESSION_REPLAY_SPENT=PASS", flush=True)
            print("K03_OUTAGE_LOCAL_COOLDOWN_EIGHT_REQUESTS_NO_PROVIDER_RETRY=PASS", flush=True)
            STAGE = "local_session_logout"
            assert (await client.get("/v1/session")).json() == baseline
            logout = await client.post("/v1/auth/logout", headers={"origin": APP, "sec-fetch-site": "same-origin",
                                                                 "x-csrf-token": baseline["csrf_token"]}, json={})
            assert logout.status_code == 204 and logout.content == b"" and app.calls == attempts
            assert (await client.get("/v1/session", headers={"cookie": SESSION_COOKIE + "=" + old_cookie})).status_code == 401
            assert app.calls == attempts
            print("K03_OUTAGE_EXISTING_SESSION_READ_AND_LOCAL_LOGOUT_WITHOUT_IDP=PASS", flush=True)
            await asyncio.sleep(max(0, app.provider._blocked_until - time.monotonic()) + 0.02)
            await app.flow.begin(None)  # Warm metadata may form a URL; this does not confirm token recovery.
            assert app.provider._failures == 1 and app.calls == attempts
            STAGE = "provider_restart"
            await asyncio.to_thread(environment.start)
            metadata = await admin.get("/realms/" + realm + "/.well-known/openid-configuration")
            assert metadata.status_code == 200 and metadata.json()["issuer"] == environment.keycloak_origin + "/realms/" + realm
            STAGE = "fresh_login"
            start, destination, _ = await _mfa_callback(app, browser, password, seed, environment, realm, pending_step)
            completed = await _callback(client, start, destination)
            assert completed.status_code == 303 and completed.headers["location"] == "/"
            current = (await client.get("/v1/session")).json()
            assert current["actor"] == baseline["actor"] and current["session"]["context_id"] != baseline["session"]["context_id"]
            assert app.provider._failures == app.provider._active == 0
            attempts = dict(app.calls)
            replay = await _callback(client, pending, destination)
            assert replay.status_code == 303 and replay.headers["location"] == "/#/signed-out?reason=login_failed"
            assert app.calls == attempts
            print("K03_OUTAGE_SAME_REALM_ISSUER_RESTART_FRESH_MFA_RECOVERS_OLD_CALLBACK_STAYS_SPENT=PASS", flush=True)
            print("K03_OUTAGE_CALLBACK_SECONDS=" + format(elapsed, ".3f"), flush=True)


def _valid_metadata():
    owner = "a" * 32
    return {"id": "b" * 64, "name": "/aletheia-k03-recovery-" + owner + "-keycloak", "owner": owner,
            "image": "sha256:" + "c" * 64, "remove": False,
            "configured": {"8443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "32123"}]},
            "ports": {"8443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "32123"}]}, "running": True,
            "exit_code": 0, "oom_killed": False}


@pytest.mark.parametrize("key,value", [("owner", "other"), ("image", "other"), ("id", "bad"),
    ("name", "/aletheia-j01-keycloak"), ("remove", True), ("exit_code", True),
    ("exit_code", 256), ("oom_killed", "synthetic-private-value"),
    ("configured", {"8443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "32123"}]}),
    ("ports", {"8443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "32124"}]})])
def test_restart_control_rejects_wrong_owner_image_identity_or_binding(monkeypatch, key, value):
    metadata = _valid_metadata()
    metadata[key] = value
    monkeypatch.setattr(sys.modules[__name__], "_docker", lambda *args, **kwargs: json.dumps(metadata))
    with pytest.raises(ValueError):
        _metadata("aletheia-k03-recovery-" + "a" * 32 + "-keycloak", "a" * 32, "sha256:" + "c" * 64, 32123)


@pytest.mark.parametrize("name", ["public", "k03_outage_x", "k03_outage_" + "a" * 32 + ";drop"])
def test_outage_schema_guard_cannot_select_another_namespace(name):
    with pytest.raises(ValueError):
        _schema(name)


def test_restart_control_accepts_only_same_container_and_loopback_port(monkeypatch):
    metadata = _valid_metadata()
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("container", "stop"):
            metadata["running"] = False
        elif args[:2] == ("container", "start"):
            metadata["running"] = True
        return json.dumps(metadata)

    monkeypatch.setattr(sys.modules[__name__], "_docker", docker)
    monkeypatch.setattr(sys.modules[__name__], "_ready", lambda environment: None)
    fixture = RestartableKeycloak("https://localhost:32123", None, "admin", SecretStr("synthetic-private"),
        Path(tempfile.gettempdir()), "image-digest", "b" * 64, "aletheia-k03-recovery-" + "a" * 32 + "-keycloak",
        "a" * 32, "sha256:" + "c" * 64, 32123)
    fixture.stop()
    fixture.start()
    operations = [item for item in calls if item[:2] in (("container", "stop"), ("container", "start"))]
    assert operations == [("container", "stop", "--time", "3", "b" * 64), ("container", "start", "b" * 64)]
    assert "synthetic-private" not in repr(fixture)


@pytest.mark.parametrize("url", [
    "https://public.example.invalid/realms/owned/login", "https://localhost:32123/realms/other/login",
    "https://localhost:32123/realms/owned/../other/login", "https://localhost:32123/realms/owned/%2e%2e/other",
    "https://localhost:32123/realms/owned/login#fragment", "https://user@localhost:32123/realms/owned/login",
    "https://localhost:32124/wrong/callback",
])
def test_provider_navigation_never_leaves_own_realm_or_exact_callback(url):
    with pytest.raises(ValueError):
        _provider_target(url, "https://localhost:32123/realms/owned", "https://localhost:32124/v1/auth/callback")


def test_authentication_code_captures_callback_without_fetching_api():
    calls = []
    callback = "https://localhost:32124/v1/auth/callback?code=synthetic-code&state=synthetic-state"

    def handle(request):
        calls.append((request.method, request.url.host, request.url.port))
        assert request.url.host == "localhost" and request.url.port == 32123
        if request.method == "GET":
            return httpx.Response(200, text='<form action="/realms/owned/login"><input name="password"></form>')
        return httpx.Response(303, headers={"location": callback})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=False) as browser:
            captured, step = await authentication_code(browser, issuer="https://localhost:32123/realms/owned",
                authorization_url="https://localhost:32123/realms/owned/authorize",
                callback_uri="https://localhost:32124/v1/auth/callback", username="synthetic",
                password="synthetic-private-password")
            assert captured == callback and step is None

    asyncio.run(scenario())
    assert calls == [("GET", "localhost", 32123), ("POST", "localhost", 32123)]


@pytest.mark.parametrize("reason,expected", [("idp_unavailable", "idp_unavailable"),
    ("login_failed", "login_failed"), ("synthetic-private-reason", "other")])
def test_stopped_observation_never_echoes_redirect_cookie_body_or_unknown_code(reason, expected):
    response = httpx.Response(303, headers={"location": "/#/signed-out?reason=" + reason,
        "set-cookie": "synthetic-private-cookie=value", "content-type": "application/problem+json; synthetic-private=value"},
        json={"code": "synthetic-private-code", "detail": "synthetic-private-body"})
    observed = _stopped_observation(response, 0.123, token_delta=1, material_delta=0,
                                    session_delta=0, cache_remaining_ms=321)
    assert observed == {"status": 303, "media": "application/problem+json", "reason": expected, "code": "other",
        "elapsed_ms": 123, "set_cookie": True, "body_empty": False, "token_delta": 1, "material_delta": 0,
        "session_delta": 0, "cache_valid_at_stop": True, "cache_remaining_ms": 321}
    assert "synthetic-private" not in json.dumps(observed)


@pytest.mark.parametrize("body", [b"not-json-synthetic-private", b"x" * 4097])
def test_stopped_observation_bounds_json_and_only_records_fixed_scalars(body):
    response = httpx.Response(503, headers={"content-type": "application/json"}, content=body)
    observed = _stopped_observation(response, -1, token_delta=0, material_delta=1,
                                    session_delta=0, cache_remaining_ms=-123)
    assert observed["code"] == "none" and observed["reason"] == "none"
    assert observed["elapsed_ms"] == 0 and observed["cache_valid_at_stop"] is False
    assert observed["cache_remaining_ms"] == -123
    assert "synthetic-private" not in json.dumps(observed)


def test_clock_observation_records_only_numeric_deltas_without_clock_repair():
    from datetime import timedelta

    now = datetime(2026, 11, 11, tzinfo=UTC)
    before = now, now, 100.0
    after = now - timedelta(milliseconds=4860), now + timedelta(milliseconds=250), 100.25
    assert _clock_observation(before, after) == {
        "host_db_before_ms": 0, "host_db_after_ms": -5110, "db_minus_monotonic_elapsed_ms": -5110}


def test_readiness_response_records_fixed_fields_without_provider_content():
    observed = {"http_responses": 0, "invalid_documents": 0}
    response = httpx.Response(503, headers={"content-type": "synthetic-private-type"},
                              json={"issuer": "synthetic-private-issuer", "detail": "synthetic-private-body"})
    assert not _readiness_response(observed, response, "https://localhost:32123/realms/master")
    assert observed == {"http_responses": 1, "invalid_documents": 0, "status": 503,
                        "media": "other", "issuer_match": False}
    assert "synthetic-private" not in json.dumps(observed)


@pytest.mark.parametrize("exit_code,oom", [(1, False), (137, True)])
def test_readiness_exited_owned_container_fails_promptly_before_http(monkeypatch, capsys, exit_code, oom):
    module = sys.modules[__name__]
    metadata = _valid_metadata()
    metadata.update(running=False, exit_code=exit_code, oom_killed=oom)
    requests = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url):
            requests.append(url)
            pytest.fail("An exited owned container must not wait for HTTP readiness.")

    monkeypatch.setattr(module, "_docker", lambda *args, **kwargs: json.dumps(metadata))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: Client())
    environment = SimpleNamespace(keycloak_origin="https://localhost:32123", tls_context=None,
        _name="aletheia-k03-recovery-" + "a" * 32 + "-keycloak", _owner="a" * 32,
        _image="sha256:" + "c" * 64, _port=32123, _identity="b" * 64)
    with pytest.raises(RuntimeError, match="readiness became unavailable"):
        _ready(environment)
    marker, _, value = capsys.readouterr().out.partition("=")
    observed = json.loads(value)
    assert marker == "K03_OUTAGE_READINESS_OBSERVATION" and requests == []
    assert observed["outcome"] == "container_exited" and observed["elapsed_ms"] < 1000
    assert observed["running"] is False and observed["container_state_known"] is True
    assert observed["exit_code"] == exit_code and observed["oom_killed"] is oom
    assert len(observed) == 12 and "synthetic-private" not in value


def test_readiness_timeout_preserves_bounded_status_and_transport_counts(monkeypatch, capsys):
    module = sys.modules[__name__]
    clock = [0.0]
    metadata = _valid_metadata()
    responses = [httpx.Response(503, headers={"content-type": "text/html"}, content=b"synthetic-private-body"),
                 httpx.ConnectError("synthetic-private-transport")]
    requests = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url):
            requests.append(url)
            clock[0] += 1
            value = responses.pop(0) if responses else httpx.ConnectError("synthetic-private-transport")
            if isinstance(value, Exception):
                raise value
            return value

    monkeypatch.setattr(module, "_docker", lambda *args, **kwargs: json.dumps(metadata))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: Client())
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0],
                                                     sleep=lambda delay: clock.__setitem__(0, clock[0] + delay)))
    environment = SimpleNamespace(keycloak_origin="https://localhost:32123", tls_context=None,
        _name="aletheia-k03-recovery-" + "a" * 32 + "-keycloak", _owner="a" * 32,
        _image="sha256:" + "c" * 64, _port=32123, _identity="b" * 64)
    with pytest.raises(TimeoutError, match="readiness did not complete"):
        _ready(environment)
    observed = json.loads(capsys.readouterr().out.partition("=")[2])
    assert observed["outcome"] == "timeout" and 120000 <= observed["elapsed_ms"] < 122000
    assert observed["status"] == 503 and observed["media"] == "text/html" and observed["issuer_match"] is False
    assert observed["http_responses"] == observed["invalid_documents"] == 1
    assert observed["transport_errors"] == len(requests) - 1 and observed["running"] is True
    assert len(observed) == 12 and "synthetic-private" not in json.dumps(observed)


def test_readiness_metadata_failure_clears_last_state_and_never_echoes_error(monkeypatch, capsys):
    module = sys.modules[__name__]
    clock = [0.0]
    reads = [0]

    def metadata(*args, state_observation, **kwargs):
        reads[0] += 1
        if reads[0] > 1:
            raise ValueError("synthetic-private-inspect-detail")
        state_observation.update(container_state_known=True, running=True, exit_code=0, oom_killed=False)

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url):
            clock[0] += 3
            raise httpx.ConnectError("synthetic-private-transport")

    monkeypatch.setattr(module, "_metadata", metadata)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: Client())
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda delay: None))
    environment = SimpleNamespace(keycloak_origin="https://localhost:32123", tls_context=None,
        _name="aletheia-k03-recovery-" + "a" * 32 + "-keycloak", _owner="a" * 32,
        _image="sha256:" + "c" * 64, _port=32123, _identity="b" * 64)
    with pytest.raises(RuntimeError, match="readiness became unavailable"):
        _ready(environment)
    raw = capsys.readouterr().out
    observed = json.loads(raw.partition("=")[2])
    assert observed["outcome"] == "container_metadata_unavailable" and observed["container_state_known"] is False
    assert observed["running"] is False and observed["exit_code"] == 0 and observed["oom_killed"] is False
    assert observed["transport_errors"] == 1 and "synthetic-private" not in raw


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-outage", action="store_true", required=True)
    parser.parse_args()
    try:
        with (private_postgres() as database, restartable_keycloak() as environment,
              asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner):
            runner.run(asyncio.wait_for(_exercise(environment, database), timeout=300))
    except Exception as error:
        print("K03_OUTAGE_CHECK=FAIL:" + type(error).__name__, flush=True)
        print("K03_OUTAGE_STAGE=" + STAGE, flush=True)
        frames = traceback.extract_tb(error.__traceback__)
        if frames:
            print("K03_OUTAGE_FAILURE_LINE=" + str(frames[-1].lineno), flush=True)
        raise SystemExit(1) from None
