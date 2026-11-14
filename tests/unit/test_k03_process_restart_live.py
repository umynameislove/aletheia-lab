"""Opt-in real API subprocess restart proof; default pytest starts no service."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
from contextlib import ExitStack, suppress
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import httpx
import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SERVER_DEPS = _ROOT.parent / "j01-browser-two-20261110/server-deps"
_WHEEL_SHA = "606e7630d419a5131f85ef9558d2eef5b6e6608eb9d69202e91847c7d4b06705"
_BUILD_INPUTS = frozenset(("pyproject.toml", "tests/unit/test_k03_migration_package.py"))
_FAILURE_FUNCTIONS = frozenset((
    "_main", "_qualified_package", "_qualified_sources", "_origin", "_exercise", "_journey", "_realm",
    "_database", "_database_url", "_configuration", "_environment", "_snapshot", "_begin", "_code", "_complete", "_login",
    "_revoke_a", "_no_jobs", "_restart", "_cold_provider_down", "start", "ready", "stop", "outage",
    "cleanup", "provenance", "_finish_process", "_child_sources", "_api_child",
))
_CRITICAL = (
    "hosted.managed_runtime", "hosted.runtime", "hosted_auth.login",
    "hosted_auth.postgres_fresh_login", "hosted_auth.postgres_sessions",
    "hosted_auth.login_proof_cipher", "hosted_auth._diagnostics",
)
_OUTCOMES = frozenset(("unavailable", "interrupted"))
_STAGES = frozenset(("readiness_before", "coordinator", "readiness_after", "begin.oidc", "begin.intent",
                     "callback.claim", "callback.exchange", "callback.identity", "callback.commit"))
_REASONS = frozenset(("login_cancelled", "login_failed", "account_disabled", "idp_unavailable"))
_PROBLEMS = frozenset(("service_unavailable", "service_failure", "rate_limited", "session_revoked",
                       "session_expired", "permission_denied", "authentication_required", "not_found"))


def _sha(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _origin(value):
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (AttributeError, TypeError, ValueError):
        raise ValueError("An explicit private loopback API origin is required.") from None
    if (parsed.scheme != "https" or parsed.hostname != "localhost" or port is None
            or not 1024 <= port <= 65535 or port in (15432, 18445, 18446)
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("An explicit private loopback API origin is required.")
    return port


def _port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    _origin("https://localhost:" + str(port))
    return port


def _qualified_sources(target, values):
    sources, build_inputs = {}, set()
    for relative, expected in values.items():
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            raise ValueError("The qualified source map is invalid.")
        if relative in _BUILD_INPUTS:
            if _sha(_ROOT / relative) != expected:
                raise ValueError("A qualified repository build input changed.")
            build_inputs.add(relative)
            continue
        if (not relative.startswith("src/aletheia_lab/") or ".." in Path(relative).parts or "\\" in relative):
            raise ValueError("The qualified source map is invalid.")
        path = (target / relative[4:]).resolve()
        if not path.is_relative_to(target.resolve()) or _sha(path) != expected:
            raise ValueError("The qualified installed package changed.")
        sources[relative[4:]] = expected
    if len(sources) != 620 or build_inputs != _BUILD_INPUTS:
        raise ValueError("The complete reviewed local package is required.")
    return sources


def _qualified_package(target, manifest, wheel):
    if _sha(wheel) != _WHEEL_SHA:
        raise ValueError("The reviewed local wheel is required.")
    if manifest.stat().st_size > 1024 * 1024:
        raise ValueError("A bounded qualified source map is required.")
    values = json.loads(manifest.read_text(encoding="utf-8-sig"))
    if not isinstance(values, dict):
        raise ValueError("The qualified source map is invalid.")
    sources = _qualified_sources(target, values)
    critical = {"aletheia_lab/" + name.replace(".", "/") + ".py":
                sources["aletheia_lab/" + name.replace(".", "/") + ".py"] for name in _CRITICAL}
    return critical


def _observation(response, elapsed):
    media = response.headers.get("content-type", "").partition(";")[0].lower().strip()
    media = media if media in ("application/problem+json", "application/json", "text/plain", "") else "other"
    code = "none"
    if media in ("application/problem+json", "application/json") and len(response.content) <= 4096:
        with suppress(ValueError):
            body = response.json()
            if isinstance(body, dict):
                candidate = body.get("code")
                code = candidate if isinstance(candidate, str) and candidate in _PROBLEMS else "other"
    return {"transport": "http", "status": response.status_code, "media": media or "missing", "code": code,
            "elapsed_ms": max(0, int(elapsed * 1000))}


class _SafeStages(logging.Handler):
    def emit(self, record):
        route, stage, outcome = (getattr(record, name, None) for name in ("auth_route", "auth_stage", "auth_outcome"))
        elapsed = getattr(record, "elapsed_ms", None)
        if (record.msg == "auth_stage_failed" and record.args == () and record.exc_info is None
                and route in ("start", "callback") and isinstance(stage, str) and stage in _STAGES
                and isinstance(outcome, str) and outcome in _OUTCOMES
                and type(elapsed) is int and elapsed >= 0):
            print("K03_API_AUTH_STAGE=" + json.dumps(
                {"route": route, "stage": stage, "outcome": outcome, "elapsed_ms": elapsed}, sort_keys=True), flush=True)


def _logging():
    logging.disable(logging.NOTSET)
    logging.getLogger().handlers = [logging.NullHandler()]
    logging.getLogger().setLevel(logging.CRITICAL + 1)
    logger = logging.getLogger("aletheia.auth.diagnostics")
    logger.setLevel(logging.WARNING)
    logger.propagate, logger.handlers = False, [_SafeStages()]


def _child_sources(environment):
    target = Path(environment["K03_PROCESS_PACKAGE_TARGET"]).resolve()
    expected = json.loads(environment["K03_PROCESS_CRITICAL_HASHES"])
    result = {}
    for name in _CRITICAL:
        module = importlib.import_module("aletheia_lab." + name)
        actual = Path(module.__file__).resolve()
        relative = "aletheia_lab/" + name.replace(".", "/") + ".py"
        if not actual.is_relative_to(target) or _sha(actual) != expected.get(relative):
            raise ValueError("API critical package provenance is unavailable.")
        result[relative] = expected[relative]
    import uvicorn
    if not Path(uvicorn.__file__).resolve().is_relative_to(_SERVER_DEPS.resolve()):
        raise ValueError("The existing isolated server dependency is required.")
    version = getattr(uvicorn, "__version__", None)
    if not isinstance(version, str) or re.fullmatch(r"[0-9][A-Za-z0-9.+-]{0,63}", version) is None:
        raise ValueError("A bounded server module dependency version is required.")
    return {"critical_source_sha256": result, "uvicorn_version": version,
            "uvicorn_path": str(Path(uvicorn.__file__).resolve())}


def _api_child():
    environment = os.environ
    if re.fullmatch(r"[0-9a-f]{32}", environment.get("K03_PROCESS_RUN_ID", "")) is None:
        raise ValueError("Explicit owned API process required.")
    _logging()
    provenance = _child_sources(environment)
    import uvicorn

    from aletheia_lab.hosted.managed_runtime import ManagedRuntime
    from aletheia_lab.hosted.runtime import api_server_options

    async def serve():
        runtime = ManagedRuntime(environment)
        _origin(runtime._settings.public_origin)
        if runtime._settings.api.processes != 1 or runtime._settings.api.bind_host != "127.0.0.1":
            raise ValueError("Exactly one loopback API process is required.")
        options = api_server_options(runtime._settings)
        options.update(ssl_certfile=environment["K03_PROCESS_TLS_CERT"], ssl_keyfile=environment["K03_PROCESS_TLS_KEY"],
                       access_log=False, log_config=None, log_level="critical")
        server = uvicorn.Server(uvicorn.Config(runtime, **options))
        loop = asyncio.get_running_loop()

        def control():
            sys.stdin.buffer.readline()
            loop.call_soon_threadsafe(setattr, server, "should_exit", True)

        threading.Thread(target=control, daemon=True).start()
        print("K03_API_CHILD_PROVENANCE=" + json.dumps(provenance, sort_keys=True), flush=True)
        await server.serve()
        if not server.started:
            raise RuntimeError("API startup did not complete.")

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        runner.run(serve())


class ApiProcess:
    """Control only a Popen handle created here; never scan/kill an existing process."""

    def __init__(self, environment, origin, directory, evidence):
        _origin(origin)
        self._environment, self.origin = dict(environment), origin
        self.directory, self.evidence = Path(directory), Path(evidence)
        self._files = {key: _sha(Path(environment[key])) for key in
                       ("ALETHEIA_HOSTED_CONFIG", "ALETHEIA_HOSTED_AUTH_POLICY", "K03_PROCESS_TLS_CERT", "K03_PROCESS_TLS_KEY")}
        self.process, self._owned_process, self._pid, self._generation = None, None, None, 0
        self.lifetimes, self.observations = [], []

    def start(self):
        if self.process is not None and self.process.poll() is None:
            raise RuntimeError("Owned API process is already running.")
        if any(_sha(Path(self._environment[key])) != expected for key, expected in self._files.items()):
            raise RuntimeError("Restart configuration or keys changed.")
        self._generation += 1
        with (self.evidence / ("api-" + str(self._generation) + ".log")).open("xb") as output:
            process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "--api-child"],
                cwd=self.directory, env=dict(self._environment), stdin=subprocess.PIPE, stdout=output, stderr=output,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.process, self._owned_process, self._pid = process, process, process.pid
        self.lifetimes.append({"generation": self._generation, "pid": process.pid})

    async def ready(self, client):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if self.process is None or self.process.poll() is not None:
                raise RuntimeError("Owned API process exited before readiness.")
            started = time.monotonic()
            try:
                response = await client.get(self.origin + "/health/ready", timeout=2)
                self.observations.append(_observation(response, time.monotonic() - started))
                if response.status_code == 200:
                    return
            except httpx.TransportError:
                self.observations.append({"transport": "unavailable", "elapsed_ms":
                                          max(0, int((time.monotonic() - started) * 1000))})
            await asyncio.sleep(0.2)
        raise TimeoutError("Owned API readiness deadline exceeded.")

    def stop(self, *, abrupt=False):
        process = self.process
        if process is None or process is not self._owned_process or process.pid != self._pid:
            raise RuntimeError("Only the exact owned API process may be stopped.")
        if process.poll() is not None:
            raise RuntimeError("Owned API exited before the requested stop.")
        if abrupt:
            process.kill()
            process.communicate(timeout=10)
        else:
            try:
                process.communicate(b"stop\n", timeout=35)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=10)
                raise TimeoutError("Owned API graceful shutdown timed out.") from None
            if process.returncode != 0:
                raise RuntimeError("Owned API graceful shutdown did not complete.")
        if process.poll() is None:
            raise RuntimeError("Owned API process remains alive.")
        self.lifetimes[-1].update(stop="abrupt" if abrupt else "graceful", exit_code=process.returncode)

    async def outage(self, tls):
        if self.process is None or self.process.poll() is None:
            raise RuntimeError("Outage observation requires the owned API to have exited.")
        started = time.monotonic()
        async with httpx.AsyncClient(verify=tls, trust_env=False, follow_redirects=False, timeout=2) as client:
            try:
                response = await client.get(self.origin + "/v1/session")
                observed = _observation(response, time.monotonic() - started)
            except httpx.TransportError:
                observed = {"transport": "unavailable", "elapsed_ms": max(0, int((time.monotonic() - started) * 1000))}
        self.observations.append(observed)
        if observed["transport"] != "unavailable":
            raise RuntimeError("Unexpected HTTP responder while the owned API was stopped.")
        return observed

    def provenance(self):
        expected = json.loads(self._environment["K03_PROCESS_CRITICAL_HASHES"])
        records = []
        for generation in range(1, self._generation + 1):
            path = self.evidence / ("api-" + str(generation) + ".log")
            if path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("Bounded child provenance evidence is required.")
            entries = [json.loads(line.partition("=")[2]) for line in path.read_text(encoding="utf-8").splitlines()
                       if line.startswith("K03_API_CHILD_PROVENANCE=")]
            if len(entries) != 1:
                raise ValueError("Exactly one verified child provenance record is required.")
            item = entries[0]
            if (set(item) != {"critical_source_sha256", "uvicorn_version", "uvicorn_path"}
                    or item["critical_source_sha256"] != expected
                    or not Path(item["uvicorn_path"]).resolve().is_relative_to(_SERVER_DEPS.resolve())
                    or re.fullmatch(r"[0-9][A-Za-z0-9.+-]{0,63}", item["uvicorn_version"]) is None):
                raise ValueError("Child dependency/source provenance is unavailable.")
            records.append({"generation": generation, **item})
        return records

    def cleanup(self):
        process = self.process
        if process is None:
            return
        if process is not self._owned_process or process.pid != self._pid:
            raise RuntimeError("Only the exact owned API process may be cleaned up.")
        if process.poll() is None:
            self.stop()
        else:
            process.communicate(timeout=10)
            self.lifetimes[-1].setdefault("stop", "unexpected_exit")
            self.lifetimes[-1]["exit_code"] = process.returncode
        self.lifetimes[-1]["verified_dead"] = process.poll() is not None


async def _json(client, method, url, **kwargs):
    response = await client.request(method, url, **kwargs)
    if response.status_code not in (200, 201, 204):
        raise RuntimeError("Owned provider administration was rejected.")
    return response.json() if response.content else None


async def _realm(provider, origin):
    import secrets
    import uuid

    from pydantic import SecretStr

    realm = "k03-process-" + uuid.uuid4().hex
    issuer = provider.keycloak_origin + "/realms/" + realm
    client_id = "k03-process-client"
    users = {}
    async with httpx.AsyncClient(verify=provider.tls_context, trust_env=False, follow_redirects=False, timeout=10) as admin:
        token = await _json(admin, "POST", provider.keycloak_origin + "/realms/master/protocol/openid-connect/token",
                            data={"grant_type": "password", "client_id": "admin-cli", "username": provider.admin_username,
                                  "password": provider.admin_password.get_secret_value()})
        admin.headers["authorization"] = "Bearer " + token["access_token"]
        base = provider.keycloak_origin + "/admin/realms/" + realm
        await _json(admin, "POST", provider.keycloak_origin + "/admin/realms",
                    json={"realm": realm, "enabled": True, "sslRequired": "all", "registrationAllowed": False})
        await _json(admin, "POST", base + "/clients", json={
            "clientId": client_id, "protocol": "openid-connect", "enabled": True, "publicClient": True,
            "standardFlowEnabled": True, "directAccessGrantsEnabled": False,
            "redirectUris": [origin + "/v1/auth/callback"], "webOrigins": [origin],
            "attributes": {"pkce.code.challenge.method": "S256"}})
        for suffix in ("a", "b"):
            username, password = "process-user-" + suffix, SecretStr(secrets.token_urlsafe(24))
            await _json(admin, "POST", base + "/users", json={
                "username": username, "email": username + "@example.invalid", "emailVerified": True,
                "enabled": True, "firstName": "Synthetic", "lastName": "Process",
                "credentials": [{"type": "password", "value": password.get_secret_value(), "temporary": False}]})
            matches = await _json(admin, "GET", base + "/users", params={"username": username, "exact": "true"})
            if not isinstance(matches, list) or len(matches) != 1 or matches[0]["username"] != username:
                raise RuntimeError("Owned synthetic provider identity is unavailable.")
            users[suffix] = {"username": username, "password": password, "subject": matches[0]["id"]}
    return issuer, client_id, users


async def _database(database, issuer, users, run_id):
    import secrets

    import psycopg
    from psycopg import sql
    from pydantic import SecretStr

    from aletheia_lab.hosted.persistence_migration import migrate as canonical
    from aletheia_lab.hosted.project_policy_migration import migrate as projects
    from aletheia_lab.hosted.review_migration import migrate as reviews
    from aletheia_lab.hosted.runtime_privileges import grant_runtime
    from aletheia_lab.hosted_auth.admission_migration import migrate as admission

    schema, role, password = "k03_process_" + run_id, "k03_api_" + run_id, SecretStr(secrets.token_urlsafe(32))
    async with await psycopg.AsyncConnection.connect(**database, autocommit=True) as owner:
        await owner.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOINHERIT NOBYPASSRLS PASSWORD {}").format(sql.Identifier(role), sql.Literal(password.get_secret_value())))
        await owner.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    scoped = {**database, "options": "-c search_path=" + schema + ",pg_catalog"}
    async with await psycopg.AsyncConnection.connect(**scoped) as connection:
        for migrate in (canonical, projects, reviews, admission):
            await migrate(connection, schema)
        # Explicit synthetic operator seeds, never an application provisioning path.
        # The real HTTP callback still validates signed tokens for these exact subjects.
        for suffix in ("a", "b"):
            await connection.execute("INSERT INTO hosted_identity_bindings(issuer,subject,user_id,display_name,email,"
                "email_verified,enabled,account_epoch,onboarding,last_workspace_id) "
                "VALUES(%s,%s,%s,%s,%s,true,true,0,'ready',%s)",
                (issuer, users[suffix]["subject"], "usr_process_" + suffix, "Synthetic " + suffix,
                 users[suffix]["username"] + "@example.invalid", "ws_process_" + suffix))
            await connection.execute("INSERT INTO j01_workspaces(workspace_id,name) VALUES(%s,%s)",
                                     ("ws_process_" + suffix, "Synthetic process " + suffix))
            await connection.execute("INSERT INTO j01_memberships VALUES(%s,%s,'owner',true,0)",
                                     ("ws_process_" + suffix, "usr_process_" + suffix))
        await connection.execute("INSERT INTO j01_memberships VALUES('ws_process_a','usr_process_b','viewer',true,0)")
        await grant_runtime(connection, schema, role, profile="api")
    return scoped, schema, role, password


def _database_url(database, schema, role, password):
    port = database.get("port")
    match = re.fullmatch(r"k03_process_([0-9a-f]{32})", schema)
    if (database.get("host") != "127.0.0.1" or database.get("dbname") != "aletheia"
            or type(port) is not int or not 1024 <= port <= 65535 or port == 15432
            or match is None or role != "k03_api_" + match[1]):
        raise ValueError("The exact private test database and scoped role are required.")
    credentials = quote(role, safe="") + ":" + quote(password.get_secret_value(), safe="")
    query = urlencode({"sslmode": "disable", "options": "-c search_path=" + schema + ",pg_catalog"}, quote_via=quote)
    return "postgresql://" + credentials + "@127.0.0.1:" + str(port) + "/aletheia?" + query


def _configuration(directory, origin, issuer, client_id, schema, role):
    config, policy = directory / "runtime.toml", directory / "auth.toml"
    objects = directory / "objects"
    objects.mkdir()
    config.write_text('environment = "local"\npublic_origin = ' + json.dumps(origin) +
        '\n[identity]\nissuer = ' + json.dumps(issuer) + '\nclient_id = ' + json.dumps(client_id) +
        '\nredirect_uri = ' + json.dumps(origin + "/v1/auth/callback") + '\nclient_kind = "public"\n' +
        '[storage]\ndatabase_url_env = "ALETHEIA_HOSTED_DATABASE_URL"\nobject_root = ' + json.dumps(str(objects)) +
        '\nexpected_schema = ' + json.dumps(schema) + '\nexpected_database_role = ' + json.dumps(role) +
        '\n[api]\nbind_host = "127.0.0.1"\nport = ' + str(_origin(origin)) +
        '\nprocesses = 1\nbackend_timeout_seconds = 12.0\nstartup_timeout_seconds = 30.0\n',
        encoding="utf-8")
    policy.write_text('default_return_to = "/"\nreturn_to_paths = ["/"]\n' +
                      'admission_namespace = "k03_process_' + schema.removeprefix("k03_process_") + '"\n', encoding="utf-8")
    return config, policy


def _environment(package, critical, config, policy, provider, database_url, run_id):
    import base64
    import secrets

    selected = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if name in os.environ}
    dependencies = [
        package, _SERVER_DEPS, _ROOT.parent / "j01-local-staging-20261109/postgres-deps",
        Path("C:/Users/TRUNGK~1/AppData/Local/Temp/aletheia-k03-csrf-deps-9f13922825994d1191ee231068e11d5a"),
        Path("C:/Users/TRUNGK~1/AppData/Local/Temp/aletheia-k03-auth-deps-913b444034d04a29a7d8c3bdeff32517"),
    ]
    selected.update(PYTHONPATH=os.pathsep.join(str(path) for path in dependencies), PYTHONDONTWRITEBYTECODE="1",
        PYTHONIOENCODING="utf-8", ALETHEIA_HOSTED_CONFIG=str(config), ALETHEIA_HOSTED_AUTH_POLICY=str(policy),
        ALETHEIA_HOSTED_DATABASE_URL=database_url, ALETHEIA_HOSTED_IDP_CA_FILE=str(provider.tls_directory / "ca.pem"),
        ALETHEIA_HOSTED_LOGIN_PROOF_KEY=base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        ALETHEIA_HOSTED_CSRF_SECRET=secrets.token_urlsafe(32), ALETHEIA_HOSTED_ADMISSION_SECRET=secrets.token_urlsafe(32),
        K03_PROCESS_RUN_ID=run_id, K03_PROCESS_TLS_CERT=str(provider.tls_directory / "cert.pem"),
        K03_PROCESS_TLS_KEY=str(provider.tls_directory / "key.pem"), K03_PROCESS_PACKAGE_TARGET=str(package),
        K03_PROCESS_CRITICAL_HASHES=json.dumps(critical, sort_keys=True))
    return selected


async def _snapshot(client, origin, suffix):
    session = await client.get(origin + "/v1/session")
    directory = await client.get(origin + "/v1/workspaces")
    if session.status_code != 200 or directory.status_code != 200:
        raise AssertionError("Protected snapshot was unavailable.")
    value = session.json()
    if value["actor"]["user_id"] != "usr_process_" + suffix:
        raise AssertionError("Protected snapshot actor changed.")
    return value, directory.json()


async def _begin(client, origin, observations):
    started = time.monotonic()
    response = await client.get(origin + "/v1/auth/start", params={"return_to": "/"},
                                headers={"sec-fetch-site": "same-origin"})
    observations.append(_observation(response, time.monotonic() - started))
    if response.status_code != 303:
        raise AssertionError("Owned login initiation was unavailable.")
    return response.headers["location"]


async def _code(api, browser, origin, issuer, user, observations):
    from test_k03_oidc_outage_live import authentication_code

    authorization = await _begin(api, origin, observations)
    callback, _ = await authentication_code(browser, issuer=issuer, authorization_url=authorization,
        callback_uri=origin + "/v1/auth/callback", username=user["username"],
        password=user["password"].get_secret_value())
    return callback


async def _complete(api, callback, observations, *, reason=None):
    started = time.monotonic()
    response = await api.get(callback)
    observations.append(_observation(response, time.monotonic() - started))
    destination = "/" if reason is None else "/#/signed-out?reason=" + reason
    if (reason is not None and reason not in _REASONS) or response.status_code != 303 or response.headers.get("location") != destination:
        raise AssertionError("Owned callback outcome differed from its fixed expectation.")
    if reason is not None and response.headers.get_list("set-cookie"):
        raise AssertionError("Rejected callback issued credentials.")


async def _login(api, browser, origin, issuer, user, observations):
    callback = await _code(api, browser, origin, issuer, user, observations)
    await _complete(api, callback, observations)
    return callback


async def _revoke_a(scoped, schema, issuer, subject):
    import psycopg
    from pydantic import SecretStr

    from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore

    async with await psycopg.AsyncConnection.connect(**scoped) as connection:
        if not await PostgresSessionStore.revoke_identity(connection, schema=schema, issuer=issuer,
                                                        subject=SecretStr(subject), expected_issuer=issuer):
            raise AssertionError("Exact owned account revocation did not commit.")


async def _no_jobs(scoped):
    import psycopg

    async with await psycopg.AsyncConnection.connect(**scoped) as connection:
        if (await (await connection.execute("SELECT count(*) FROM j01_jobs")).fetchone()) != (0,):
            raise AssertionError("Abrupt process fixture must not contain in-flight jobs.")


async def _journey(process, provider, issuer, users, scoped, schema, report):
    observations = report["http_observations"]
    options = dict(verify=provider.tls_context, trust_env=False, follow_redirects=False, timeout=15,
                   limits=httpx.Limits(max_keepalive_connections=0))
    async with (httpx.AsyncClient(**options) as a, httpx.AsyncClient(**options) as b,
                httpx.AsyncClient(**options) as pending, httpx.AsyncClient(**options) as failed,
                httpx.AsyncClient(**options) as browser_a, httpx.AsyncClient(**options) as browser_b,
                httpx.AsyncClient(**options) as browser_pending, httpx.AsyncClient(**options) as browser_failed):
        origin = process.origin
        report["phase"] = "baseline_login"
        process.start()
        await process.ready(a)
        await _login(a, browser_a, origin, issuer, users["a"], observations)
        await _login(b, browser_b, origin, issuer, users["b"], observations)
        before_a, before_b = await _snapshot(a, origin, "a"), await _snapshot(b, origin, "b")
        authorization = await _begin(pending, origin, observations)
        report["phase"] = "graceful_api_restart"
        await _restart(process, provider.tls_context, a)
        await _same_snapshots(a, b, origin, before_a, before_b)
        from test_k03_oidc_outage_live import authentication_code
        callback, _ = await authentication_code(browser_pending, issuer=issuer, authorization_url=authorization,
            callback_uri=origin + "/v1/auth/callback", username=users["a"]["username"],
            password=users["a"]["password"].get_secret_value())
        await _complete(pending, callback, observations)
        await _complete(pending, callback, observations, reason="login_failed")
        if (await _snapshot(pending, origin, "a"))[0]["session"]["context_id"] == before_a[0]["session"]["context_id"]:
            raise AssertionError("Pending login did not create its own context.")
        report["graceful_existing_and_pending_survive_replay_denied"] = True
        report["phase"] = "provider_outage"
        spent_callback = await _code(failed, browser_failed, origin, issuer, users["a"], observations)
        provider.stop()
        await _same_snapshots(a, b, origin, before_a, before_b)
        await _complete(failed, spent_callback, observations, reason="idp_unavailable")
        await _complete(failed, spent_callback, observations, reason="login_failed")
        report["phase"] = "cold_api_start_while_provider_down"
        await _cold_provider_down(process, provider, observations, report)
        await process.ready(b)
        await _same_snapshots(a, b, origin, before_a, before_b)
        report["provider_outage_does_not_revoke_sessions_callback_spent"] = True
        report["phase"] = "targeted_revoke_and_abrupt_api_restart"
        await _revoke_a(scoped, schema, issuer, users["a"]["subject"])
        await _revoked_snapshots(a, pending, b, origin, before_b)
        await _no_jobs(scoped)
        await _restart(process, provider.tls_context, b, abrupt=True)
        await _revoked_snapshots(a, pending, b, origin, before_b)
        await _complete(failed, spent_callback, observations, reason="login_failed")
        # A new explicit intent proves recovery; the spent callback is never retried as a fresh login.
        async with httpx.AsyncClient(**options) as recovered, httpx.AsyncClient(**options) as recovered_browser:
            await _login(recovered, recovered_browser, origin, issuer, users["a"], observations)
            if (await _snapshot(recovered, origin, "a"))[0]["session"]["context_id"] == before_a[0]["session"]["context_id"]:
                raise AssertionError("Explicit recovery reused the revoked context.")
        report["revoked_contexts_stay_invalid_B_unchanged_spent_callback_survives"] = True


async def _exercise(package, critical, provider, database, directory, evidence, run_id, report):
    origin = "https://localhost:" + str(_port())
    issuer, client_id, users = await _realm(provider, origin)
    from test_k03_oidc_outage_live import prime_provider
    await prime_provider(provider, issuer)
    scoped, schema, role, password = await _database(database, issuer, users, run_id)
    config, policy = _configuration(directory, origin, issuer, client_id, schema, role)
    database_url = _database_url(scoped, schema, role, password)
    environment = _environment(package, critical, config, policy, provider, database_url, run_id)
    process = ApiProcess(environment, origin, directory, evidence)
    try:
        await _journey(process, provider, issuer, users, scoped, schema, report)
    finally:
        _finish_process(process, report, sys.exception())


def _finish_process(process, report, primary):
    errors = []
    try:
        process.cleanup()
    except BaseException as error:
        report["cleanup_failure"] = _failure_observation(error)
        errors.append(error)
    report["api_processes"] = process.lifetimes
    report["api_availability_observations"] = process.observations
    try:
        report["child_provenance"] = process.provenance()
    except BaseException as error:
        report["provenance_failure"] = _failure_observation(error)
        errors.append(error)
    if primary is not None:
        report["primary_failure"] = _failure_observation(primary)
    elif errors:
        raise errors[0]


def _failure_observation(error):
    classes = (AssertionError, ValueError, TimeoutError, RuntimeError, OSError, KeyError, TypeError)
    category = next((kind.__name__ for kind in classes if isinstance(error, kind)), "other")
    leaf_function, leaf_line = "external", None
    trace = error.__traceback__
    while trace is not None:
        frame = trace.tb_frame
        if frame.f_code.co_filename == __file__ and frame.f_code.co_name in _FAILURE_FUNCTIONS:
            leaf_function, leaf_line = frame.f_code.co_name, trace.tb_lineno
        trace = trace.tb_next
    return {"error_class": category, "leaf_function": leaf_function, "leaf_line": leaf_line}


def _main():
    if sys.argv[1:] == ["--api-child"]:
        try:
            _api_child()
            return 0
        except BaseException as error:
            print("K03_API_CHILD_FAILURE=" + json.dumps(_failure_observation(error), sort_keys=True), flush=True)
            print("K03_API_CHILD=FAILED_OR_UNKNOWN", flush=True)
            return 1
    if sys.argv[1:] != ["--run-process-restart"] or os.environ.get("K03_PROCESS_RESTART_ALLOW") != "own-private-fixtures":
        print("K03_PROCESS_RESTART=OPT_IN_REQUIRED", flush=True)
        return 2
    import tempfile
    import uuid

    evidence = Path(os.environ["K03_PROCESS_RESTART_EVIDENCE"]).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    report = {"scope": "Own private PG and Keycloak, real packaged API OS-process restarts; no browser/joint/staging/HA claim",
              "phase": "package_verification", "http_observations": [], "outcome": "failed_or_unknown"}
    code = 1
    try:
        package = Path(os.environ["K03_PROCESS_PACKAGE_TARGET"]).resolve()
        critical = _qualified_package(package, Path(os.environ["K03_PROCESS_PACKAGE_MAP"]),
                                      Path(os.environ["K03_PROCESS_PACKAGE_WHEEL"]))
        sys.path.insert(0, str(package))
        from test_k03_oidc_outage_live import restartable_keycloak
        from test_k03_postgres_environment import private_postgres
        run_id = uuid.uuid4().hex
        with ExitStack() as contexts:
            report["phase"] = "private_postgres_start"
            database = contexts.enter_context(private_postgres())
            report["phase"] = "provider_start"
            provider = contexts.enter_context(restartable_keycloak())
            report["phase"] = "private_fixture_setup"
            temporary = contexts.enter_context(tempfile.TemporaryDirectory(prefix="aletheia-k03-api-" + run_id + "-"))
            directory = Path(temporary)
            parameters = {"host": "127.0.0.1", "port": database.port, "dbname": "aletheia", "user": "aletheia",
                          "password": database.password.get_secret_value(), "connect_timeout": 5}
            report.update(package_wheel_sha256=_WHEEL_SHA, installed_source_count=620,
                          postgres_image=database.image_digest, keycloak_image=provider.image_digest)
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
                runner.run(_exercise(package, critical, provider, parameters, directory, evidence, run_id, report))
        report.update(outcome="pass", owned_contexts_exited=True, phase="complete")
        for marker in ("GRACEFUL_SESSION_AND_SNAPSHOT", "PENDING_CALLBACK_REPLAY", "IDP_FAILURE_SPENT_CALLBACK",
                       "COLD_METADATA_STARTUP", "ABRUPT_REVOKED_A_B_ISOLATION", "SPENT_CALLBACK_AFTER_API_RESTART",
                       "OWN_API_AND_FIXTURES_CLEANUP"):
            print("K03_PROCESS_" + marker + "=PASS", flush=True)
        print("K03_PROCESS_API_PIDS=" + ",".join(str(row["pid"]) for row in report["api_processes"]), flush=True)
        code = 0
    except BaseException as error:
        report["failure"] = _failure_observation(error)
        print("K03_PROCESS_FAILURE=" + json.dumps(report["failure"], sort_keys=True), flush=True)
        print("K03_PROCESS_RESTART_FAILED_STAGE=" + report["phase"], flush=True)
    finally:
        with (evidence / "report.json").open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=True, sort_keys=True, indent=2)
            output.write("\n")
    print("K03_PROCESS_RESTART=" + ("PASS" if code == 0 else "FAILED_OR_UNKNOWN"), flush=True)
    return code


async def _same_snapshots(a, b, origin, before_a, before_b):
    if await _snapshot(a, origin, "a") != before_a or await _snapshot(b, origin, "b") != before_b:
        raise AssertionError("Protected context or workspace snapshot changed.")


async def _revoked_snapshots(a, pending, b, origin, before_b):
    if (await a.get(origin + "/v1/session")).status_code != 401 or (await pending.get(origin + "/v1/session")).status_code != 401:
        raise AssertionError("A revoked account context remained authorized.")
    if await _snapshot(b, origin, "b") != before_b:
        raise AssertionError("Targeted account revocation affected account B.")


async def _restart(process, tls, ready_client, *, abrupt=False):
    old_pid = process.process.pid
    process.stop(abrupt=abrupt)
    await process.outage(tls)
    process.start()
    if process.process.pid == old_pid:
        raise AssertionError("A new operating-system API process is required.")
    await process.ready(ready_client)


async def _cold_provider_down(process, provider, observations, report):
    # Current ManagedRuntime requires fresh discovery before lifespan startup.
    # Record actual exit/unavailability; do not invent an HTTP503 from a stopped process.
    process.stop()
    process.start()
    started = time.monotonic()
    deadline = started + 45
    async with httpx.AsyncClient(verify=provider.tls_context, trust_env=False, follow_redirects=False, timeout=1,
                                 limits=httpx.Limits(max_keepalive_connections=0)) as probe:
        while process.process.poll() is None:
            if time.monotonic() >= deadline:
                raise TimeoutError("Cold API startup did not fail within the fixture deadline.")
            try:
                response = await probe.get(process.origin + "/health/ready")
                observations.append(_observation(response, time.monotonic() - started))
                if response.status_code == 200:
                    raise AssertionError("Current mandatory discovery gate unexpectedly admitted a cold API.")
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.2)
    if process.process.returncode == 0:
        raise AssertionError("Unavailable mandatory discovery was reported as successful API startup.")
    process.lifetimes[-1].update(stop="startup_unavailable", exit_code=process.process.returncode)
    await process.outage(provider.tls_context)
    report["cold_provider_metadata_actual_api_exit_ms"] = max(0, int((time.monotonic() - started) * 1000))
    provider.start()
    process.start()
    report["cold_metadata_startup_failed_closed"] = True


def _process_case(tmp_path, monkeypatch, *, timeout=False):
    from types import SimpleNamespace

    files = {}
    for key in ("ALETHEIA_HOSTED_CONFIG", "ALETHEIA_HOSTED_AUTH_POLICY", "K03_PROCESS_TLS_CERT", "K03_PROCESS_TLS_KEY"):
        path = tmp_path / (key + ".txt")
        path.write_text("synthetic-private-key-material", encoding="utf-8")
        files[key] = str(path)
    environment = {**files, "ALETHEIA_HOSTED_DATABASE_URL": "synthetic-private-database",
                   "K03_PROCESS_RUN_ID": "a" * 32, "PYTHONPATH": "qualified-installed-first"}
    created = []

    class Process:
        def __init__(self):
            self.pid, self.returncode, self.actions = 5000 + len(created), None, []

        def poll(self):
            return self.returncode

        def communicate(self, payload=None, *, timeout):
            self.actions.append(("communicate", payload, timeout))
            if payload is not None and timeout == 35 and not self.returncode:
                if self.actions.count(("communicate", payload, timeout)) == 1 and fail_timeout:
                    raise subprocess.TimeoutExpired("owned-api", timeout)
                self.returncode = 0

        def kill(self):
            self.actions.append(("kill",))
            self.returncode = -9

    fail_timeout = timeout

    def popen(args, **kwargs):
        process = Process()
        created.append(SimpleNamespace(process=process, args=args, options=kwargs))
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    case = ApiProcess(environment, "https://localhost:32123", tmp_path, tmp_path)
    return case, created


@pytest.mark.parametrize("origin", ["http://localhost:32123", "https://foreign.invalid:32123",
    "https://localhost:18446", "https://localhost:15432", "https://localhost:32123/?synthetic-private-query",
    "https://u:synthetic-private-password@localhost:32123", "https://localhost:synthetic-private-port"])
def test_only_exact_private_loopback_origin_is_accepted(origin):
    with pytest.raises(ValueError) as caught:
        _origin(origin)
    assert "synthetic-private" not in str(caught.value) + repr(caught.value)


def test_actual_child_command_has_no_secret_arguments_and_uses_owned_cwd(tmp_path, monkeypatch):
    case, created = _process_case(tmp_path, monkeypatch)
    case.start()
    call = created[0]
    assert call.args == [sys.executable, "-B", str(Path(__file__).resolve()), "--api-child"]
    assert call.options["cwd"] == tmp_path and call.options["stdin"] is subprocess.PIPE
    assert all("synthetic-private" not in value for value in call.args)
    assert call.options["env"]["ALETHEIA_HOSTED_DATABASE_URL"] == "synthetic-private-database"
    with pytest.raises(RuntimeError, match="already running"):
        case.start()
    case.stop()
    assert call.process.actions == [("communicate", b"stop\n", 35)]
    case.start()
    assert created[1].process.pid != call.process.pid
    assert created[1].options["env"] == call.options["env"]
    case.stop(abrupt=True)
    assert created[1].process.actions == [("kill",), ("communicate", None, 10)]


def test_changed_configuration_refuses_restart_before_spawning(tmp_path, monkeypatch):
    case, created = _process_case(tmp_path, monkeypatch)
    Path(case._environment["ALETHEIA_HOSTED_AUTH_POLICY"]).write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="configuration or keys changed"):
        case.start()
    assert created == []


def test_control_refuses_an_unowned_replacement_handle(tmp_path, monkeypatch):
    from types import SimpleNamespace

    case, created = _process_case(tmp_path, monkeypatch)
    case.start()
    case.process = SimpleNamespace(pid=case._pid, poll=lambda: None)
    with pytest.raises(RuntimeError, match="exact owned"):
        case.stop(abrupt=True)
    assert created[0].process.actions == []
    case.process = created[0].process
    case.cleanup()


def test_graceful_timeout_kills_only_owned_handle_and_cannot_claim_graceful_success(tmp_path, monkeypatch):
    case, created = _process_case(tmp_path, monkeypatch, timeout=True)
    case.start()
    with pytest.raises(TimeoutError, match="graceful shutdown timed out"):
        case.stop()
    assert created[0].process.returncode == -9 and ("kill",) in created[0].process.actions
    assert "stop" not in case.lifetimes[0]


def test_package_provenance_cannot_fall_back_to_repository_source(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(importlib, "import_module", lambda name: SimpleNamespace(__file__=str(_ROOT / "src/wrong.py")))
    monkeypatch.setattr(sys.modules[__name__], "_sha", lambda path: "a" * 64)
    with pytest.raises(ValueError, match="provenance"):
        _child_sources({"K03_PROCESS_PACKAGE_TARGET": str(tmp_path),
                        "K03_PROCESS_CRITICAL_HASHES": json.dumps({"wrong": "a" * 64})})


def test_qualified_source_map_requires_complete_hashes_and_no_path_escape(tmp_path, monkeypatch):
    module = sys.modules[__name__]
    critical_paths = ["src/aletheia_lab/" + name.replace(".", "/") + ".py" for name in _CRITICAL]
    entries = {name: "a" * 64 for name in critical_paths}
    entries.update({"src/aletheia_lab/fixture" + str(i) + ".py": "a" * 64 for i in range(620 - len(entries))})
    entries.update({name: "a" * 64 for name in _BUILD_INPUTS})
    manifest, wheel = tmp_path / "map.json", tmp_path / "qualified.whl"
    manifest.write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setattr(module, "_sha", lambda path: _WHEEL_SHA if path == wheel else "a" * 64)
    assert len(_qualified_package(tmp_path, manifest, wheel)) == len(_CRITICAL)
    entries["src/aletheia_lab/../../escape.py"] = "a" * 64
    manifest.write_text(json.dumps(entries), encoding="utf-8")
    with pytest.raises(ValueError, match="source map is invalid"):
        _qualified_package(tmp_path, manifest, wheel)


def test_safe_http_observation_discards_bodies_and_unrecognized_codes():
    response = httpx.Response(503, json={"code": ["synthetic-private-token"], "detail": "synthetic-private-body"})
    result = _observation(response, 0.125)
    assert result == {"transport": "http", "status": 503, "media": "application/json", "code": "other", "elapsed_ms": 125}
    assert "synthetic-private" not in json.dumps(result)


def test_safe_logger_ignores_raw_exception_and_dynamic_stage_values(capsys):
    record = logging.makeLogRecord({"msg": "synthetic-private-url", "args": ()})
    _SafeStages().emit(record)
    record = logging.makeLogRecord({"msg": "auth_stage_failed", "args": (), "auth_route": "callback",
        "auth_stage": {"secret": "synthetic-private-token"}, "auth_outcome": "unavailable", "elapsed_ms": 10})
    _SafeStages().emit(record)
    assert capsys.readouterr().out == ""


def test_default_cli_never_starts_fixtures(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["test_k03_process_restart_live.py"])
    monkeypatch.delenv("K03_PROCESS_RESTART_ALLOW", raising=False)
    assert _main() == 2
    assert capsys.readouterr().out == "K03_PROCESS_RESTART=OPT_IN_REQUIRED\n"




def test_configuration_keeps_default_idp_deadline_and_one_loopback_process(tmp_path):
    import tomllib

    config, policy = _configuration(tmp_path, "https://localhost:32123", "https://localhost:32124/realms/fixture",
                                    "fixture-client", "k03_process_" + "a" * 32, "k03_api_" + "a" * 32)
    runtime = tomllib.loads(config.read_text(encoding="utf-8"))
    auth = tomllib.loads(policy.read_text(encoding="utf-8"))
    assert runtime["environment"] == "local"
    assert runtime["api"]["bind_host"] == "127.0.0.1" and runtime["api"]["processes"] == 1
    assert runtime["api"]["backend_timeout_seconds"] == 12.0
    assert "idp_timeout_seconds" not in auth and auth["return_to_paths"] == ["/"]
    assert "password" not in config.read_text(encoding="utf-8") + policy.read_text(encoding="utf-8")


def test_provider_registration_supplies_complete_synthetic_profiles_without_network(monkeypatch):
    from types import SimpleNamespace

    from pydantic import SecretStr

    calls = []
    provider = SimpleNamespace(keycloak_origin="https://localhost:32124", tls_context=object(),
                               admin_username="fixture-admin", admin_password=SecretStr("synthetic-private-admin"))

    class Admin:
        headers = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    async def request(client, method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/token"):
            return {"access_token": "synthetic-private-access-token"}
        if method == "GET":
            username = kwargs["params"]["username"]
            return [{"id": "subject-" + username, "username": username}]
        return None

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: Admin())
    monkeypatch.setattr(sys.modules[__name__], "_json", request)
    issuer, _, users = asyncio.run(_realm(provider, "https://localhost:32123"))
    assert issuer.startswith(provider.keycloak_origin + "/realms/k03-process-") and set(users) == {"a", "b"}
    payloads = [options["json"] for method, url, options in calls if method == "POST" and url.endswith("/users")]
    assert len(payloads) == 2
    assert all(item["firstName"] == "Synthetic" and item["lastName"] == "Process" and item["emailVerified"]
               for item in payloads)




@pytest.mark.parametrize("relative", ["configs/hosted/staging.example.toml", "tests/unit/unknown.py",
                                     "pyproject.toml/../escape", "../pyproject.toml"])
def test_qualified_source_map_rejects_every_other_non_source_input(tmp_path, monkeypatch, relative):
    module = sys.modules[__name__]
    manifest, wheel = tmp_path / "map.json", tmp_path / "qualified.whl"
    manifest.write_text(json.dumps({relative: "a" * 64}), encoding="utf-8")
    monkeypatch.setattr(module, "_sha", lambda path: _WHEEL_SHA if path == wheel else "a" * 64)
    with pytest.raises(ValueError, match="source map is invalid"):
        _qualified_package(tmp_path, manifest, wheel)


@pytest.mark.parametrize("changed", ["pyproject.toml", "tests/unit/test_k03_migration_package.py"])
def test_exact_build_input_bytes_must_match_the_reviewed_map(tmp_path, monkeypatch, changed):
    module = sys.modules[__name__]
    manifest, wheel = tmp_path / "map.json", tmp_path / "qualified.whl"
    manifest.write_text(json.dumps({changed: "a" * 64}), encoding="utf-8")
    monkeypatch.setattr(module, "_sha", lambda path: _WHEEL_SHA if path == wheel else "b" * 64)
    with pytest.raises(ValueError, match="build input changed"):
        _qualified_package(tmp_path, manifest, wheel)


@pytest.mark.parametrize("expected", ["not-a-hash", "A" * 64, None, ["synthetic-private-value"]])
def test_build_input_requires_strict_hash_before_reading_repository(tmp_path, monkeypatch, expected):
    manifest, wheel = tmp_path / "map.json", tmp_path / "qualified.whl"
    manifest.write_text(json.dumps({"pyproject.toml": expected}), encoding="utf-8")
    calls = []

    def sha(path):
        calls.append(path)
        return _WHEEL_SHA

    monkeypatch.setattr(sys.modules[__name__], "_sha", sha)
    with pytest.raises(ValueError, match="source map is invalid"):
        _qualified_package(tmp_path, manifest, wheel)
    assert calls == [wheel]


def test_failure_observation_has_only_fixed_class_and_own_leaf_location():
    try:
        raise AssertionError("synthetic-private-cookie-url-body")
    except AssertionError as error:
        observed = _failure_observation(error)
    assert observed["error_class"] == "AssertionError" and observed["leaf_function"] == "external"
    assert observed["leaf_line"] is None
    assert "synthetic-private" not in json.dumps(observed)

    class SyntheticPrivateProviderException(Exception):
        pass

    try:
        raise SyntheticPrivateProviderException("synthetic-private-token")
    except Exception as error:
        observed = _failure_observation(error)
    assert observed["error_class"] == "other"
    assert "SyntheticPrivate" not in json.dumps(observed)


def test_failure_observation_records_own_fixed_function_and_line():
    try:
        _origin("https://localhost:synthetic-private-port")
    except ValueError as error:
        observed = _failure_observation(error)
    assert observed["error_class"] == "ValueError" and observed["leaf_function"] == "_origin"
    assert type(observed["leaf_line"]) is int and observed["leaf_line"] > 0
    assert "synthetic-private" not in json.dumps(observed)




def test_child_provenance_reads_validated_module_version_without_dist_info(tmp_path, monkeypatch):
    from importlib import metadata
    from types import SimpleNamespace

    expected = {"aletheia_lab/" + name.replace(".", "/") + ".py": "a" * 64 for name in _CRITICAL}
    monkeypatch.setattr(importlib, "import_module", lambda name: SimpleNamespace(
        __file__=str(tmp_path / (name.replace(".", "/") + ".py"))))
    monkeypatch.setattr(sys.modules[__name__], "_sha", lambda path: "a" * 64)
    server_path = _SERVER_DEPS / "uvicorn/__init__.py"
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(__file__=str(server_path), __version__="0.48.0"))

    def missing_metadata(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "version", missing_metadata)
    observed = _child_sources({"K03_PROCESS_PACKAGE_TARGET": str(tmp_path),
                               "K03_PROCESS_CRITICAL_HASHES": json.dumps(expected)})
    assert observed == {"critical_source_sha256": expected, "uvicorn_version": "0.48.0",
                        "uvicorn_path": str(server_path.resolve())}


@pytest.mark.parametrize("version", [None, "", ["private-version"], "0.48.0\n", "synthetic-private-version"])
def test_child_provenance_rejects_invalid_module_version(tmp_path, monkeypatch, version):
    from types import SimpleNamespace

    expected = {"aletheia_lab/" + name.replace(".", "/") + ".py": "a" * 64 for name in _CRITICAL}
    monkeypatch.setattr(importlib, "import_module", lambda name: SimpleNamespace(
        __file__=str(tmp_path / (name.replace(".", "/") + ".py"))))
    monkeypatch.setattr(sys.modules[__name__], "_sha", lambda path: "a" * 64)
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(
        __file__=str(_SERVER_DEPS / "uvicorn/__init__.py"), __version__=version))
    with pytest.raises(ValueError, match="dependency version") as caught:
        _child_sources({"K03_PROCESS_PACKAGE_TARGET": str(tmp_path),
                        "K03_PROCESS_CRITICAL_HASHES": json.dumps(expected)})
    assert "synthetic-private" not in str(caught.value)


def test_already_exited_owned_child_is_reaped_and_recorded_without_claiming_graceful_stop(tmp_path, monkeypatch):
    case, created = _process_case(tmp_path, monkeypatch)
    case.start()
    created[0].process.returncode = 1
    case.cleanup()
    assert case.lifetimes == [{"generation": 1, "pid": created[0].process.pid,
                              "stop": "unexpected_exit", "exit_code": 1, "verified_dead": True}]
    assert created[0].process.actions == [("communicate", None, 10)]


def test_cleanup_or_provenance_failure_cannot_replace_the_primary_journey_error():
    from types import SimpleNamespace

    calls, report = [], {}

    def cleanup():
        calls.append("cleanup")
        raise TimeoutError("synthetic-private-cleanup")

    def provenance():
        calls.append("provenance")
        raise ValueError("synthetic-private-provenance")

    process = SimpleNamespace(cleanup=cleanup, provenance=provenance, lifetimes=[], observations=[])
    primary = RuntimeError("synthetic-private-primary")
    with pytest.raises(RuntimeError) as caught:
        try:
            raise primary
        finally:
            _finish_process(process, report, sys.exception())
    assert caught.value is primary and calls == ["cleanup", "provenance"]
    assert report["primary_failure"]["error_class"] == "RuntimeError"
    assert report["cleanup_failure"]["error_class"] == "TimeoutError"
    assert report["provenance_failure"]["error_class"] == "ValueError"
    assert "synthetic-private" not in json.dumps(report)
    with pytest.raises(TimeoutError):
        _finish_process(process, {}, None)




@pytest.mark.parametrize("failed_context,expected_phase,events", [
    ("database", "private_postgres_start", ["database_enter"]),
    ("provider", "provider_start", ["database_enter", "provider_enter", "database_exit"]),
])
def test_fixture_context_failure_phase_precedes_services_and_cleans_prior_context(
    tmp_path, monkeypatch, failed_context, expected_phase, events,
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    import test_k03_oidc_outage_live as provider_fixture
    import test_k03_postgres_environment as database_fixture

    observed = []

    @contextmanager
    def database():
        observed.append("database_enter")
        if failed_context == "database":
            raise TimeoutError("synthetic-private-database")
        try:
            yield SimpleNamespace()
        finally:
            observed.append("database_exit")

    @contextmanager
    def provider():
        observed.append("provider_enter")
        raise TimeoutError("synthetic-private-provider")
        yield

    monkeypatch.setattr(database_fixture, "private_postgres", database)
    monkeypatch.setattr(provider_fixture, "restartable_keycloak", provider)
    monkeypatch.setattr(sys.modules[__name__], "_qualified_package", lambda *args: {})
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("No API may start after context entry failure"))
    monkeypatch.setattr(sys, "argv", [__file__, "--run-process-restart"])
    monkeypatch.setattr(sys, "path", list(sys.path))
    for name in ("K03_PROCESS_PACKAGE_TARGET", "K03_PROCESS_PACKAGE_MAP", "K03_PROCESS_PACKAGE_WHEEL"):
        monkeypatch.setenv(name, str(tmp_path / name))
    monkeypatch.setenv("K03_PROCESS_RESTART_ALLOW", "own-private-fixtures")
    monkeypatch.setenv("K03_PROCESS_RESTART_EVIDENCE", str(tmp_path))
    assert _main() == 1 and observed == events
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["phase"] == expected_phase and report["failure"]["error_class"] == "TimeoutError"
    assert report["outcome"] == "failed_or_unknown" and "api_processes" not in report
    assert "synthetic-private" not in json.dumps(report)




def test_generated_local_url_and_configuration_pass_actual_runtime_secret_resolvers(tmp_path):
    from types import SimpleNamespace
    from urllib.parse import parse_qs, unquote

    from psycopg.conninfo import conninfo_to_dict
    from pydantic import SecretStr

    from aletheia_lab.hosted.postgres_pool import PoolUnavailable, _require_staging_tls
    from aletheia_lab.hosted.settings import (
        RuntimeSettings,
        load_runtime_settings,
        resolve_database_url,
        resolve_runtime_secrets,
    )

    run_id = "a" * 32
    schema, role = "k03_process_" + run_id, "k03_api_" + run_id
    password = SecretStr("synthetic-private-p:@/% +x?&#")
    database = {"host": "127.0.0.1", "port": 32125, "dbname": "aletheia"}
    url = _database_url(database, schema, role, password)
    config, policy = _configuration(tmp_path, "https://localhost:32123", "https://localhost:32124/realms/fixture",
                                    "fixture-client", schema, role)
    settings = load_runtime_settings(config)
    environment = _environment(tmp_path / "installed", {}, config, policy,
                               SimpleNamespace(tls_directory=tmp_path), url, run_id)
    actual = resolve_database_url(settings, environment)
    secrets = resolve_runtime_secrets(settings, environment)
    assert actual.get_secret_value() == secrets.database_url.get_secret_value() == url
    assert settings.environment == "local"
    assert settings.storage.expected_schema == schema and settings.storage.expected_database_role == role
    parsed = urlsplit(url)
    assert parsed.scheme == "postgresql" and parsed.hostname == "127.0.0.1" and parsed.port == 32125
    assert unquote(parsed.username) == role and unquote(parsed.password) == password.get_secret_value()
    assert parse_qs(parsed.query) == {"sslmode": ["disable"], "options": ["-c search_path=" + schema + ",pg_catalog"]}
    driver = conninfo_to_dict(url)
    assert driver["user"] == role and driver["password"] == password.get_secret_value()
    assert driver["sslmode"] == "disable" and driver["options"] == "-c search_path=" + schema + ",pg_catalog"
    _require_staging_tls(settings, url)
    staging = RuntimeSettings.model_validate({**settings.model_dump(), "environment": "staging"})
    with pytest.raises(PoolUnavailable, match="pool_tls_required"):
        _require_staging_tls(staging, url)
    assert password.get_secret_value() not in config.read_text(encoding="utf-8") + policy.read_text(encoding="utf-8")


@pytest.mark.parametrize("changes", [
    {"host": "localhost"}, {"host": "foreign.invalid"}, {"port": 15432}, {"port": True},
    {"port": 65536}, {"dbname": "other"},
])
def test_database_url_rejects_other_or_shared_database_scope(changes):
    from pydantic import SecretStr

    database = {"host": "127.0.0.1", "port": 32125, "dbname": "aletheia", **changes}
    with pytest.raises(ValueError) as caught:
        _database_url(database, "k03_process_" + "a" * 32, "k03_api_" + "a" * 32, SecretStr("synthetic-private-password"))
    assert "synthetic-private" not in str(caught.value)


if __name__ == "__main__":
    raise SystemExit(_main())
