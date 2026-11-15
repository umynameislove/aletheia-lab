"""Opt-in K03 acceptance using the real server, private PG and real local IdP."""

# ruff: noqa: E402
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import traceback
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, urlencode, urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [
    str(ROOT / "src"),
    str(ROOT / "tests/unit"),
    str(ROOT / "tests/integration"),
    str(ROOT / "scripts"),
]

import httpx
import k03_real_provider_cases as provider_cases
import k03_real_session_cases as session_cases
import psycopg
import test_k03_keycloak_recovery as recovery
import test_k03_postgres_environment as pgfixture
from k18_schema_admin import execute as schema_admin
from psycopg import sql
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr
from test_k02_https_edge import _certificate
from test_k03_postgres_environment import private_postgres
from test_k03_recovery_environment import recovery_environment

from aletheia_lab.hosted.managed_runtime import ManagedRuntime
from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE
from aletheia_lab.hosted_auth.csrf import CSRF_COOKIE
from aletheia_lab.hosted_auth.settings import AuthPolicy


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def runtime_files():
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((ROOT / "src/aletheia_lab").rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }


async def private_database_tls(pg, certificate, key, bootstrap_dsn):
    ids = subprocess.check_output(
        [
            "docker",
            "ps",
            "--filter",
            "label=aletheia.owner=k03-private-postgres",
            "--format",
            "{{.ID}}",
        ],
        text=True,
    ).splitlines()
    matched = []
    for identity in ids:
        item = json.loads(subprocess.check_output(["docker", "inspect", identity]))[0]
        ports = item["NetworkSettings"]["Ports"]["5432/tcp"]
        if ports == [{"HostIp": "127.0.0.1", "HostPort": str(pg.port)}]:
            name = item["Name"].lstrip("/")
            owner = item["Config"]["Labels"]["aletheia.run_id"]
            pgfixture._inspect(item["Id"], name, owner, pgfixture._IMAGE_ID, pg.port)
            matched.append(item["Id"])
    assert len(matched) == 1
    identity = matched[0]
    targets = ["/var/lib/postgresql/data/k03-server.crt", "/var/lib/postgresql/data/k03-server.key"]
    for source, target in zip((certificate, key), targets, strict=True):
        subprocess.run(
            ["docker", "cp", str(source), identity + ":" + target], check=True, capture_output=True
        )
    subprocess.run(
        ["docker", "exec", "--user", "root", identity, "chown", "postgres:postgres", *targets],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["docker", "exec", "--user", "root", identity, "chmod", "0600", targets[1]],
        check=True,
        capture_output=True,
    )
    async with await psycopg.AsyncConnection.connect(bootstrap_dsn, autocommit=True) as connection:
        for name, value in [
            ("ssl", "on"),
            ("ssl_cert_file", targets[0]),
            ("ssl_key_file", targets[1]),
        ]:
            await connection.execute(
                sql.SQL("ALTER SYSTEM SET {} = {}").format(sql.Identifier(name), sql.Literal(value))
            )
        await connection.execute("SELECT pg_reload_conf()")
    secured = make_conninfo(bootstrap_dsn, sslmode="verify-full", sslrootcert=str(certificate))
    for _ in range(15):
        try:
            async with await psycopg.AsyncConnection.connect(secured) as connection:
                assert (
                    await (
                        await connection.execute(
                            "SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()"
                        )
                    ).fetchone()
                ) == (True,)
                return secured
        except psycopg.OperationalError:
            await asyncio.sleep(0.2)
    raise RuntimeError("Private PostgreSQL verified TLS did not become ready")


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)


class HttpFlow:
    def __init__(self, client):
        self.client = client

    async def begin(self, cookie, *, return_to):
        response = await self.client.get(
            "/v1/auth/start",
            params={"return_to": return_to},
            headers={"sec-fetch-site": "same-origin"},
        )
        assert response.status_code == 303
        assert response.headers["cache-control"] == "no-store"
        return SimpleNamespace(authorization_url=SecretStr(response.headers["location"]))


async def exercise(pg, idp, private, output, records, known_secrets, *, auth_abuse_only=False):
    from importlib.resources import files

    suffix = uuid4().hex
    schema, role, realm = "k03_live_" + suffix, "k03_api_" + suffix[:16], "k03-live-" + suffix
    port = free_port()
    origin = f"https://127.0.0.1:{port}"
    recovery.APP, recovery.CALLBACK = origin, origin + "/v1/auth/callback"
    password, seed = secrets.token_urlsafe(24), base64.b32encode(secrets.token_bytes(20)).decode()
    other_password, other_seed = (
        secrets.token_urlsafe(24),
        base64.b32encode(secrets.token_bytes(20)).decode(),
    )
    known_secrets.extend(
        [password, seed, other_password, other_seed, pg.password.get_secret_value()]
    )
    cert, key = _certificate(private)
    ca = private / "idp-ca.pem"
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    ca.write_bytes(
        b"".join(
            x509.load_der_x509_certificate(c).public_bytes(serialization.Encoding.PEM)
            for c in idp.tls_context.get_ca_certs(binary_form=True)
        )
    )
    app_tls = ssl.create_default_context(cafile=str(cert))
    owner_dsn = make_conninfo(
        host="127.0.0.1",
        port=pg.port,
        user="aletheia",
        dbname="aletheia",
        password=pg.password.get_secret_value(),
        connect_timeout=5,
    )
    owner_dsn = await private_database_tls(pg, cert, key, owner_dsn)
    owner_file = private / "owner-url"
    owner_file.write_text(owner_dsn, encoding="utf-8")
    owner_file.chmod(0o600)
    server = None

    def record(case, started, **details):
        value = {
            "case": case,
            "status": "pass",
            "elapsed_seconds": round(time.monotonic() - started, 6),
            **details,
        }
        records.append(value)
        with (output / "cases.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(value, ensure_ascii=False) + "\n")
        print("K03_CASE_PASS=" + case, flush=True)

    async with httpx.AsyncClient(
        base_url=idp.keycloak_origin, verify=idp.tls_context, trust_env=False, timeout=30
    ) as admin:
        base, subject, policy_hash = await recovery._setup(idp, admin, realm, password, seed)
        clients = await recovery._json(
            admin, "GET", base + "/clients", params={"clientId": recovery.CLIENT}
        )
        assert len(clients) == 1
        client_registration = clients[0]
        client_registration["attributes"]["post.logout.redirect.uris"] = origin + "/"
        await recovery._admin_login(idp, admin)
        registration = await admin.put(
            base + "/clients/" + client_registration["id"],
            json={"attributes": client_registration["attributes"]},
        )
        print("K03_CLIENT_LOGOUT_REGISTRATION_HTTP=" + str(registration.status_code), flush=True)
        if registration.status_code != 204:
            error = registration.json()
            print(
                "K03_CLIENT_REGISTRATION_ERROR_KIND=" + str(error.get("error", "unknown"))[:80],
                flush=True,
            )
            raise ValueError("Client logout registration rejected")
        registered = await recovery._json(
            admin, "GET", base + "/clients/" + client_registration["id"]
        )
        assert registered["attributes"]["post.logout.redirect.uris"] == origin + "/"
        policy_hash = hashlib.sha256(
            json.dumps(
                {"flow_hash": policy_hash, "client_attributes": registered["attributes"]},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        subject_b = await recovery._create_user(
            admin, base, "second-user", other_password, other_seed, "second@aletheia.invalid"
        )
        async with await psycopg.AsyncConnection.connect(owner_dsn, autocommit=True) as owner:
            try:
                await owner.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
                args = SimpleNamespace(
                    action="migrate",
                    database_file=owner_file,
                    schema=schema,
                    role=None,
                    profile=None,
                )
                await schema_admin(args)
                await owner.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
                await owner.execute(
                    files("aletheia_lab.hosted_auth").joinpath("admission_schema.sql").read_text()
                )
                role_password = secrets.token_urlsafe(32)
                known_secrets.append(role_password)
                await owner.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT PASSWORD {}"
                    ).format(sql.Identifier(role), sql.Literal(role_password))
                )
                await schema_admin(
                    SimpleNamespace(
                        action="grant",
                        database_file=owner_file,
                        schema=schema,
                        role=role,
                        profile="api",
                    )
                )
                issuer = idp.keycloak_origin + "/realms/" + realm
                bootstrap = time.monotonic()
                for path in ("/.well-known/openid-configuration", "/protocol/openid-connect/certs"):
                    response = await admin.get("/realms/" + realm + path)
                    assert response.status_code == 200
                record("real_idp_bootstrap", bootstrap, scope="outside request latency measurement")
                for user_id, sub, email in [
                    ("k03_a", subject, "recovery-a@aletheia.invalid"),
                    ("k03_b", subject_b, "second@aletheia.invalid"),
                ]:
                    await owner.execute(
                        "INSERT INTO hosted_identity_bindings VALUES "
                        "(%s,%s,%s,'Synthetic',%s,true,true,0,'needs_workspace',NULL,NULL)",
                        (issuer, sub, user_id, email),
                    )
                for user_id, workspace, title in [
                    ("k03_a", "ws_k03_a", "K03 Workspace A"),
                    ("k03_b", "ws_k03_b", "K03 Workspace B"),
                ]:
                    async with owner.transaction():
                        await owner.execute(
                            "INSERT INTO j01_workspaces(workspace_id,name) VALUES(%s,%s)",
                            (workspace, title),
                        )
                        await owner.execute(
                            "INSERT INTO j01_memberships(workspace_id,user_id,role,active,revision) "
                            "VALUES(%s,%s,'owner',true,0)",
                            (workspace, user_id),
                        )
                        await owner.execute(
                            "UPDATE hosted_identity_bindings SET onboarding='ready',last_workspace_id=%s "
                            "WHERE user_id=%s",
                            (workspace, user_id),
                        )
                objects = private / "objects"
                objects.mkdir()
                config = (ROOT / "configs/hosted/staging.example.toml").read_text(encoding="utf-8")
                import re

                for name, value in {
                    "public_origin": origin,
                    "issuer": issuer,
                    "client_id": recovery.CLIENT,
                    "redirect_uri": recovery.CALLBACK,
                    "client_kind": "public",
                    "expected_schema": schema,
                    "expected_database_role": role,
                    "object_root": str(objects).replace("\\", "/"),
                }.items():
                    config = re.sub(rf'^{name} = ".*"$', f'{name} = "{value}"', config, flags=re.M)
                config = config.replace("port = 18766", f"port = {port}")
                (private / "api.toml").write_text(config, encoding="utf-8")
                policy = AuthPolicy(
                    default_return_to="/",
                    return_to_paths=("/",),
                    admission_namespace=schema,
                    required_acr="1",
                )
                values = policy.model_dump(exclude_none=True)
                auth = "\n".join(f"{name} = {json.dumps(value)}" for name, value in values.items())
                (private / "auth.toml").write_text(auth, encoding="utf-8")
                environment = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
                environment.update(
                    PYTHONDONTWRITEBYTECODE="1",
                    PYTHONUTF8="1",
                    PYTHONPATH=str(ROOT / "src"),
                    ALETHEIA_HOSTED_CONFIG=str(private / "api.toml"),
                    ALETHEIA_HOSTED_AUTH_POLICY=str(private / "auth.toml"),
                    ALETHEIA_HOSTED_DATABASE_URL=(
                        f"postgresql://{role}:{quote(role_password, safe='')}@127.0.0.1:{pg.port}/aletheia?"
                        + urlencode(
                            {
                                "options": "-csearch_path=" + schema,
                                "sslmode": "verify-full",
                                "sslrootcert": str(cert),
                            }
                        )
                    ),
                    ALETHEIA_HOSTED_CSRF_SECRET=secrets.token_urlsafe(48),
                    ALETHEIA_HOSTED_ADMISSION_SECRET=secrets.token_urlsafe(48),
                    ALETHEIA_HOSTED_LOGIN_PROOF_KEY=base64.b64encode(
                        secrets.token_bytes(32)
                    ).decode(),
                    ALETHEIA_HOSTED_IDP_CA_FILE=str(ca),
                    ALETHEIA_HOSTED_ACTION_AUDIT="enabled",
                    ALETHEIA_HOSTED_IDP_LOGOUT="enabled",
                    ALETHEIA_RELEASE_ID=hashlib.sha256(
                        json.dumps(runtime_files(), sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest(),
                )
                known_secrets.extend(
                    environment[k]
                    for k in environment
                    if k.startswith("ALETHEIA_HOSTED_")
                    and any(word in k for word in ("SECRET", "KEY", "DATABASE_URL"))
                )
                dependency_started = time.monotonic()
                async with AsyncExitStack() as resources:
                    await ManagedRuntime(environment)._compose(resources)
                record("managed_runtime_dependency_preflight", dependency_started)
                node = shutil.which("node")
                assert node is not None
                assets = private / "assets"
                with (output / "browser-build.log").open("wb") as build_log:
                    built = await asyncio.to_thread(
                        subprocess.run,
                        [
                            node,
                            str(ROOT / "dashboard/web/node_modules/vite/bin/vite.js"),
                            "build",
                            "--mode",
                            "hosted",
                            "--outDir",
                            str(assets),
                        ],
                        cwd=ROOT / "dashboard/web",
                        env={**environment, "VITE_IDP_ORIGIN": idp.keycloak_origin},
                        stdout=build_log,
                        stderr=subprocess.STDOUT,
                        timeout=90,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                assert built.returncode == 0
                driver = private / "cdp.mjs"
                original_driver = (ROOT / "dashboard/web/tests/e2e/cdp.mjs").read_text(
                    encoding="utf-8"
                )
                driver.write_text(
                    original_driver.replace(
                        '"--headless=new",',
                        '"--headless=new", "--ignore-certificate-errors-spki-list=" + process.env.K03_TLS_PINS,',
                    ).replace("maxRetries: 5, retryDelay: 100", "maxRetries: 20, retryDelay: 250"),
                    encoding="utf-8",
                )

                def pin(certificate):
                    return base64.b64encode(
                        hashlib.sha256(
                            certificate.public_key().public_bytes(
                                serialization.Encoding.DER,
                                serialization.PublicFormat.SubjectPublicKeyInfo,
                            )
                        ).digest()
                    ).decode()

                with (
                    socket.create_connection(
                        ("localhost", urlsplit(idp.keycloak_origin).port), timeout=5
                    ) as plain,
                    idp.tls_context.wrap_socket(plain, server_hostname="localhost") as secure,
                ):
                    idp_leaf = x509.load_der_x509_certificate(secure.getpeercert(binary_form=True))
                pins = ",".join(
                    [pin(x509.load_pem_x509_certificate(cert.read_bytes())), pin(idp_leaf)]
                )
                with (private / "api.log").open("wb") as log:
                    server = subprocess.Popen(
                        [
                            sys.executable,
                            "-B",
                            "-m",
                            "aletheia_lab.hosted.server",
                            "--config",
                            str(private / "api.toml"),
                            "--auth-policy",
                            str(private / "auth.toml"),
                            "--tls-cert",
                            str(cert),
                            "--tls-key",
                            str(key),
                            "--static-root",
                            str(assets),
                        ],
                        cwd=ROOT,
                        env=environment,
                        stdout=log,
                        stderr=log,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    async with httpx.AsyncClient(
                        base_url=origin, verify=app_tls, trust_env=False, timeout=20
                    ) as probe:
                        started = time.monotonic()
                        for _ in range(100):
                            if server.poll() is not None:
                                raise RuntimeError("K03 server exited before readiness")
                            try:
                                if (await probe.get("/health/ready")).status_code == 200:
                                    break
                            except httpx.TransportError:
                                pass
                            await asyncio.sleep(0.2)
                        else:
                            raise TimeoutError("K03 bounded readiness")
                        record(
                            "real_managed_runtime_https_ready",
                            started,
                            environment="staging",
                            api_pool=8,
                        )
                        started = time.monotonic()
                        assert (await probe.get("/v1/session")).status_code == 401
                        assert (
                            await probe.get(
                                "/v1/auth/start", headers={"sec-fetch-site": "cross-site"}
                            )
                        ).status_code == 400
                        assert (
                            await probe.get(
                                "/v1/auth/start",
                                params={"return_to": "https://foreign.invalid"},
                                headers={"sec-fetch-site": "same-origin"},
                            )
                        ).status_code == 400
                        record("anonymous_cross_site_and_unsafe_return_denied", started)

                    public = {
                        "origin": origin,
                        "issuer": issuer,
                        "realm_policy_hash": policy_hash,
                        "auth_policy": values,
                        "idp_logout": "enabled",
                        "post_logout_redirect_uri": origin + "/",
                        "keycloak_images": dict(idp.image_digests),
                        "postgres_image": pg.image_digest,
                        "head": subprocess.check_output(
                            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                        ).strip(),
                        "release_id": environment["ALETHEIA_RELEASE_ID"],
                        "source_change_evidence": "result.json and inputs-before/after.json",
                        "scope": "Real private local managed staging runtime and IdP, not team deployment signoff",
                    }
                    (output / "environment.json").write_text(
                        json.dumps(public, indent=2), encoding="utf-8"
                    )
                    if auth_abuse_only:
                        await session_cases.abuse(
                            SimpleNamespace(origin=origin, tls=app_tls, owner=owner, record=record)
                        )
                        return

                    async def login(username, user_password, factor, previous=None):
                        client = httpx.AsyncClient(
                            base_url=origin, verify=app_tls, trust_env=False, timeout=20
                        )
                        provider = httpx.AsyncClient(
                            verify=idp.tls_context, trust_env=False, timeout=20
                        )
                        try:
                            _, callback, step = await recovery._mfa_callback(
                                SimpleNamespace(flow=HttpFlow(client)),
                                provider,
                                user_password,
                                factor,
                                idp,
                                realm,
                                previous,
                                username=username,
                            )
                            known_secrets.append(callback)
                            result = await client.get(callback)
                            assert result.status_code == 303 and result.headers["location"] == "/"
                            flags = result.headers.get_list("set-cookie")
                            assert any(
                                SESSION_COOKIE in h
                                and "Secure" in h
                                and "HttpOnly" in h
                                and "samesite=lax" in h.lower()
                                and "domain=" not in h.lower()
                                for h in flags
                            )
                            session = await client.get("/v1/session")
                            assert session.status_code == 200
                            known_secrets.extend(c.value for c in client.cookies.jar)
                            return client, session.json(), step, callback
                        except BaseException:
                            await client.aclose()
                            raise
                        finally:
                            await provider.aclose()

                    started = time.monotonic()
                    a, session_a, last_a, callback = await login("recovery-user", password, seed)
                    try:
                        record(
                            "real_password_wrong_otp_good_otp_http_callback",
                            started,
                            session_status=200,
                            cookie_secure=True,
                            cookie_http_only=True,
                        )
                        started = time.monotonic()
                        count_before = (
                            await (
                                await owner.execute("SELECT count(*) FROM j01_sessions")
                            ).fetchone()
                        )[0]
                        replay = await a.get(callback)
                        count_after = (
                            await (
                                await owner.execute("SELECT count(*) FROM j01_sessions")
                            ).fetchone()
                        )[0]
                        assert replay.status_code == 303 and "reason=" in replay.headers["location"]
                        assert count_before == count_after == 1
                        record("real_callback_replay_no_new_session", started)
                        started = time.monotonic()
                        denied = await a.post(
                            "/v1/auth/logout-all", json={}, headers={"origin": origin}
                        )
                        assert denied.status_code == 403
                        assert (await a.get("/v1/session")).status_code == 200
                        record("missing_csrf_denied_without_logout", started)
                        started = time.monotonic()
                        b, session_b, last_b, _ = await login(
                            "second-user", other_password, other_seed
                        )
                        try:
                            assert session_a["actor"]["user_id"] != session_b["actor"]["user_id"]
                            assert (await b.get("/v1/session")).status_code == 200
                            record("real_two_accounts_distinct_server_sessions", started)
                            started = time.monotonic()
                            proof = a.cookies.get(CSRF_COOKIE)
                            result = await a.post(
                                "/v1/auth/logout-all",
                                json={},
                                headers={"origin": origin, "x-csrf-token": proof},
                            )
                            assert result.status_code == 204
                            assert (await a.get("/v1/session")).status_code == 401
                            assert (await b.get("/v1/session")).status_code == 200
                            record("real_logout_all_preserves_other_account", started)
                            password, last_a = await provider_cases.run(
                                SimpleNamespace(
                                    last_a=last_a,
                                    password=password,
                                    realm=realm,
                                    origin=origin,
                                    tls=app_tls,
                                    idp=idp,
                                    admin=admin,
                                    subject=subject,
                                    seed=seed,
                                    flow=HttpFlow,
                                    secrets=known_secrets,
                                    owner=owner,
                                    login=login,
                                    record=record,
                                    owner_dsn=owner_dsn,
                                    schema=schema,
                                    issuer=issuer,
                                    new_password=secrets.token_urlsafe(30),
                                    other_cookie="; ".join(
                                        f"{c.name}={c.value}" for c in b.cookies.jar
                                    ),
                                )
                            )
                        finally:
                            await b.aclose()
                    finally:
                        await a.aclose()
                    browser_started = time.monotonic()
                    spec = {
                        "driver": str(driver),
                        "origin": origin,
                        "idp_origin": idp.keycloak_origin,
                        "debug_port": free_port(),
                        "session_cookie": SESSION_COOKIE,
                        "password_a": password,
                        "seed_a": seed,
                        "password_b": other_password,
                        "seed_b": other_seed,
                        "last_a": last_a,
                        "last_b": last_b,
                    }
                    chrome = await asyncio.to_thread(
                        subprocess.run,
                        [node, str(Path(__file__).with_name("k03_real_browser.mjs"))],
                        input=json.dumps(spec).encode(),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env={
                            **environment,
                            "CHROME_PATH": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                            "K03_TLS_PINS": pins,
                        },
                        timeout=240,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    (output / "browser-outcomes.jsonl").write_bytes(chrome.stdout)
                    (output / "browser-errors.log").write_bytes(chrome.stderr)
                    assert chrome.returncode == 0
                    record(
                        "real_chrome_identity_journey",
                        browser_started,
                        browser_cases=sum(
                            json.loads(line).get("status") == "pass"
                            for line in chrome.stdout.splitlines()
                        ),
                    )
                    browser_summary = json.loads(chrome.stdout.splitlines()[-1])
                    await session_cases.run(
                        SimpleNamespace(
                            login=login,
                            password=password,
                            seed=seed,
                            last_a=browser_summary["last_a"],
                            origin=origin,
                            tls=app_tls,
                            owner=owner,
                            record=record,
                            secrets=known_secrets,
                        )
                    )

            finally:
                if server is not None:
                    stop(server)
                await owner.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
                )
                exists = await (
                    await owner.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
                ).fetchone()
                if exists:
                    await owner.execute(
                        sql.SQL("DROP OWNED BY {}; DROP ROLE {}").format(
                            sql.Identifier(role), sql.Identifier(role)
                        )
                    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--auth-abuse-only",
        action="store_true",
        help="Run only remaining auth-start quota qualification",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    records, known_secrets = [], []
    status = "failed"
    source_before = runtime_files()
    used_inputs = [
        p
        for directory in ("src", "configs", "dashboard/web/src", "tests/unit")
        for p in (ROOT / directory).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    ]
    used_inputs += list(Path(__file__).parent.glob("k03_real_*"))

    def input_hashes():
        return {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(used_inputs)
        }

    inputs_before = input_hashes()
    (args.output / "inputs-before.json").write_text(
        json.dumps(inputs_before, indent=2), encoding="utf-8"
    )
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="aletheia-k03-http-") as temp:
            private = Path(temp)
            try:
                with (
                    private_postgres() as pg,
                    recovery_environment() as idp,
                    asyncio.Runner(
                        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
                    ) as runner,
                ):
                    runner.run(
                        exercise(
                            pg,
                            idp,
                            private,
                            args.output,
                            records,
                            known_secrets,
                            auth_abuse_only=args.auth_abuse_only,
                        )
                    )
                status = "passed"
            finally:
                raw = (
                    (private / "api.log").read_text(encoding="utf-8", errors="replace")
                    if (private / "api.log").exists()
                    else ""
                )
                leaked = sum(1 for value in known_secrets if len(value) > 12 and value in raw)
                if leaked:
                    status = "failed"
                    print("K03_SECRET_LOGGING_CHECK=FAILED", flush=True)
                else:
                    (args.output / "api-safe.log").write_text(raw, encoding="utf-8")
    except Exception as error:
        status = "failed"
        print("K03_ATTEMPT_ERROR=" + type(error).__name__, flush=True)
        for frame in traceback.extract_tb(error.__traceback__)[-3:]:
            print(
                f"K03_ERROR_LOCATION={Path(frame.filename).name}:{frame.lineno}:{frame.name}",
                flush=True,
            )
    finally:
        inputs_after = input_hashes()
        (args.output / "inputs-after.json").write_text(
            json.dumps(inputs_after, indent=2), encoding="utf-8"
        )
        source_unchanged = source_before == runtime_files() and inputs_before == inputs_after
        if not source_unchanged:
            status = "failed"
        result = {
            "runtime_source_unchanged": source_unchanged,
            "auth_abuse_only": args.auth_abuse_only,
            "status": status,
            "passed": len(records),
            "elapsed_seconds": time.monotonic() - started,
            "cases": records,
            "scope": "K03 only; real disposable local IdP/managed HTTPS/PG",
            "production_or_joint_acceptance": False,
            "committed": False,
            "pushed": False,
        }
        (args.output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
