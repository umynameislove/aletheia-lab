"""Opt-in disposable Keycloak/Mailpit infrastructure, never started by pytest.

Mailpit v1.33.1: https://github.com/axllent/mailpit/releases/tag/v1.33.1
Official images: https://mailpit.axllent.org/docs/install/docker/
Only the explicit context manager/CLI creates services; tests below are offline.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

KEYCLOAK_IMAGE = "quay.io/keycloak/keycloak:26.8.2"
MAILPIT_IMAGE = "axllent/mailpit:v1.33.1"
_OWNER_LABEL = "com.aletheia.k03.recovery"
_PREFIX = "aletheia-k03-recovery-"
_RESOURCE = re.compile(r"aletheia-k03-recovery-[0-9a-f]{32}-(keycloak|mail|network)")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")


class RecoveryEnvironmentUnavailable(RuntimeError):
    def __init__(self, operation: str) -> None:
        super().__init__("Local recovery environment unavailable: " + operation + ".")


@dataclass(frozen=True)
class RecoveryEnvironment:
    keycloak_origin: str
    mailbox_origin: str
    tls_context: ssl.SSLContext = field(repr=False)
    admin_username: str = field(repr=False)
    admin_password: SecretStr = field(repr=False)
    image_digests: tuple[tuple[str, str], ...]
    smtp_host: str = "mail"
    smtp_port: int = 1025


def _owned_name(value: str, kind: str) -> str:
    match = _RESOURCE.fullmatch(value) if isinstance(value, str) else None
    if kind not in ("keycloak", "mail", "network") or match is None or match[1] != kind:
        raise ValueError("Exact disposable recovery resource required.")
    return value


def _binding(raw: str) -> int:
    try:
        values = json.loads(raw)
        if not isinstance(values, list) or len(values) != 1:
            raise ValueError()
        item = values[0]
        if not isinstance(item, dict) or item.get("HostIp") != "127.0.0.1":
            raise ValueError()
        port = item.get("HostPort")
        if not isinstance(port, str) or not re.fullmatch(r"[0-9]{4,5}", port) or not 1024 <= int(port) <= 65535:
            raise ValueError()
        return int(port)
    except (ValueError, TypeError, KeyError):
        raise ValueError("One explicit loopback ephemeral binding required.") from None


def _owned_directory(directory: Path, temporary: Path, owner: str) -> Path:
    resolved, parent = directory.resolve(), temporary.resolve()
    if (not re.fullmatch(r"[0-9a-f]{32}", owner) or resolved.parent != parent
            or resolved.name != _PREFIX + owner):
        raise ValueError("Exact disposable recovery directory required.")
    return resolved


def _docker(*arguments: str, operation: str, timeout: float = 30,
            environment: dict[str, str] | None = None, required: bool = True) -> str | None:
    try:
        result = subprocess.run(["docker", *arguments], env=environment, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        raise RecoveryEnvironmentUnavailable(operation) from None
    if result.returncode != 0:
        if required:
            raise RecoveryEnvironmentUnavailable(operation) from None
        return None
    return result.stdout.strip()


def _image(image: str, *, allow_pull: bool) -> tuple[str, str]:
    template = '{"id":{{json .Id}},"digests":{{json .RepoDigests}}}'
    output = _docker("image", "inspect", "--format", template, image,
                     operation="image_inspect", required=False)
    if output is None and allow_pull:
        _docker("pull", image, operation="mail_image_pull", timeout=180)
        output = _docker("image", "inspect", "--format", template, image, operation="image_inspect")
    try:
        value = json.loads(output or "")
        identity = value["id"]
        if not isinstance(identity, str) or not _IMAGE_ID.fullmatch(identity):
            raise ValueError()
        digests = value.get("digests") or []
        digest = next((item for item in digests if isinstance(item, str)
                       and re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", item)), identity)
        return identity, digest
    except (ValueError, TypeError, KeyError):
        raise RecoveryEnvironmentUnavailable("image_identity") from None


def _certificate(directory: Path) -> ssl.SSLContext:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.now(UTC)
    key, authority = rsa.generate_private_key(65537, 2048), rsa.generate_private_key(65537, 2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "K03 recovery local test")])
    base = x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
    ca = (base.public_key(authority.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=4))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(authority.public_key()), critical=False)
          .sign(authority, hashes.SHA256()))
    leaf = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    certificate = (x509.CertificateBuilder().subject_name(leaf).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=4))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                       x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(authority.public_key()), critical=False)
        .sign(authority, hashes.SHA256()))
    (directory / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (directory / "cert.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    (directory / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return ssl.create_default_context(cafile=str(directory / "ca.pem"))


def _port(name: str, target: int) -> int:
    kind = "keycloak" if target == 8443 else "mail"
    _owned_name(name, kind)
    template = '{{json (index .NetworkSettings.Ports "' + str(target) + '/tcp")}}'
    raw = _docker("container", "inspect", "--format", template, name, operation="loopback_binding")
    return _binding(raw or "")


def _wait_ready(environment: RecoveryEnvironment) -> None:
    import httpx

    deadline = time.monotonic() + 120
    with httpx.Client(verify=environment.tls_context, trust_env=False, timeout=2,
                      follow_redirects=False) as client:
        while time.monotonic() < deadline:
            try:
                discovery = client.get(environment.keycloak_origin + "/realms/master/.well-known/openid-configuration")
                mail = client.get(environment.mailbox_origin + "/api/v1/messages")
                if (discovery.status_code == mail.status_code == 200
                        and discovery.json().get("issuer") == environment.keycloak_origin + "/realms/master"):
                    return
            except (httpx.TransportError, ValueError):
                pass
            time.sleep(0.25)
    raise RecoveryEnvironmentUnavailable("bounded_readiness")


def _remove_resource(name: str, owner: str, kind: str) -> None:
    _owned_name(name, kind)
    resource = "network" if kind == "network" else "container"
    arguments = [resource, "ls"]
    if resource == "container":
        arguments.append("--all")
    arguments += ["--filter", "label=" + _OWNER_LABEL + "=" + owner,
                  "--filter", "name=" + name, "--format", "{{.ID}}"]
    identity = _docker(*arguments, operation="cleanup_inventory") or ""
    if not identity:
        return
    if not re.fullmatch(r"[0-9a-f]{12,64}", identity):
        raise RecoveryEnvironmentUnavailable("cleanup_identity")
    actual_name = _docker(resource, "inspect", "--format", "{{.Name}}", identity, operation="cleanup_name")
    if actual_name is None or actual_name.lstrip("/") != name:
        raise RecoveryEnvironmentUnavailable("cleanup_name")
    flags = ("--force", "--volumes") if resource == "container" else ()
    _docker(resource, "rm", *flags, identity, operation="owned_cleanup")


def _cleanup(names: dict[str, str], attempted: list[str], directory: Path, temporary: Path, owner: str) -> None:
    failed = False
    for kind in reversed(attempted):
        try:
            _remove_resource(names[kind], owner, kind)
        except RecoveryEnvironmentUnavailable:
            failed = True
    try:
        shutil.rmtree(_owned_directory(directory, temporary, owner))
    except (OSError, ValueError):
        failed = True
    if failed:
        raise RecoveryEnvironmentUnavailable("cleanup") from None
    print("K03_RECOVERY_ENVIRONMENT_REMOVED=PASS", flush=True)


@contextmanager
def recovery_environment() -> Iterator[RecoveryEnvironment]:
    """Start only own loopback fixtures; do not change existing services or realms."""
    owner = uuid.uuid4().hex
    names = {kind: _owned_name(_PREFIX + owner + "-" + kind, kind) for kind in ("network", "mail", "keycloak")}
    temporary = Path(tempfile.gettempdir()).resolve()
    directory = _owned_directory(temporary / (_PREFIX + owner), temporary, owner)
    directory.mkdir()
    attempted: list[str] = []
    try:
        context = _certificate(directory)
        keycloak, kc_digest = _image(KEYCLOAK_IMAGE, allow_pull=False)
        mailpit, mp_digest = _image(MAILPIT_IMAGE, allow_pull=True)
        label = _OWNER_LABEL + "=" + owner
        attempted.append("network")
        # Internal Docker networks do not publish host ports on this Desktop engine.
        _docker("network", "create", "--opt", "com.docker.network.bridge.host_binding_ipv4=127.0.0.1",
                "--label", label, names["network"], operation="network_create")
        common = ["--detach", "--rm", "--pull=never", "--network", names["network"],
                  "--label", label, "--cap-drop=ALL", "--security-opt=no-new-privileges"]
        attempted.append("mail")
        _docker("run", *common, "--name", names["mail"], "--network-alias", "mail",
            "--memory=128m", "--cpus=1", "--read-only",
            "--tmpfs", "/data:rw,noexec,nosuid,size=32m,mode=1777",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m,mode=1777",
            "--publish", "127.0.0.1::8025", "--env", "MP_DATABASE=/data/mailpit.db",
            "--env", "MP_MAX_MESSAGES=20", mailpit, operation="mail_start")
        username, password = "k03-local-admin", SecretStr(secrets.token_urlsafe(32))
        child_environment = dict(os.environ, KC_BOOTSTRAP_ADMIN_USERNAME=username,
                                 KC_BOOTSTRAP_ADMIN_PASSWORD=password.get_secret_value())
        attempted.append("keycloak")
        _docker("run", *common, "--name", names["keycloak"], "--memory=1g", "--cpus=2",
            "--publish", "127.0.0.1::8443", "--mount", "type=bind,src=" + str(directory) + ",dst=/k03,readonly",
            "--env", "KC_BOOTSTRAP_ADMIN_USERNAME", "--env", "KC_BOOTSTRAP_ADMIN_PASSWORD",
            keycloak, "start-dev", "--http-enabled=false", "--hostname-strict=false",
            "--https-certificate-file=/k03/cert.pem", "--https-certificate-key-file=/k03/key.pem",
            operation="identity_start", environment=child_environment)
        environment = RecoveryEnvironment(
            keycloak_origin="https://localhost:" + str(_port(names["keycloak"], 8443)),
            mailbox_origin="http://127.0.0.1:" + str(_port(names["mail"], 8025)),
            tls_context=context, admin_username=username, admin_password=password,
            image_digests=((KEYCLOAK_IMAGE, kc_digest), (MAILPIT_IMAGE, mp_digest)))
        _wait_ready(environment)
        print("K03_RECOVERY_ENVIRONMENT_READY=PASS", flush=True)
        yield environment
    finally:
        _cleanup(names, attempted, directory, temporary, owner)


@pytest.mark.parametrize("name,kind", [
    ("aletheia-j01-keycloak", "keycloak"), ("aletheia-k03-recovery-" + "a" * 32 + "-mail", "keycloak"),
    ("aletheia-k03-recovery-x-network", "network"), ("../network", "network"), ("x", "other"),
])
def test_cleanup_resource_names_cannot_target_other_services(name, kind):
    with pytest.raises(ValueError):
        _owned_name(name, kind)


@pytest.mark.parametrize("kind", ["keycloak", "mail", "network"])
def test_exact_generated_resource_names_are_accepted(kind):
    name = _PREFIX + uuid.uuid4().hex + "-" + kind
    assert _owned_name(name, kind) == name


@pytest.mark.parametrize("raw", [
    "[]", "null", '[{"HostIp":"0.0.0.0","HostPort":"32123"}]',
    '[{"HostIp":"127.0.0.1","HostPort":"443"}]',
    '[{"HostIp":"127.0.0.1","HostPort":32123}]',
    '[{"HostIp":"127.0.0.1","HostPort":"32123"},{"HostIp":"::","HostPort":"32123"}]',
])
def test_only_one_loopback_binding_is_accepted(raw):
    with pytest.raises(ValueError):
        _binding(raw)


def test_valid_loopback_binding_and_directory_boundary(tmp_path):
    assert _binding('[{"HostIp":"127.0.0.1","HostPort":"32123"}]') == 32123
    owner = uuid.uuid4().hex
    expected = tmp_path / (_PREFIX + owner)
    assert _owned_directory(expected, tmp_path, owner) == expected.resolve()
    with pytest.raises(ValueError):
        _owned_directory(tmp_path.parent, tmp_path, owner)
    with pytest.raises(ValueError):
        _owned_directory(tmp_path / "other", tmp_path, owner)


def test_tls_leaf_has_distinct_subject_and_is_signed_by_fixture_ca(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import padding

    context = _certificate(tmp_path)
    ca = x509.load_pem_x509_certificate((tmp_path / "ca.pem").read_bytes())
    certificate = x509.load_pem_x509_certificate((tmp_path / "cert.pem").read_bytes())
    assert certificate.subject != ca.subject and certificate.issuer == ca.subject
    ca.public_key().verify(certificate.signature, certificate.tbs_certificate_bytes,
                           padding.PKCS1v15(), certificate.signature_hash_algorithm)
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED


def test_fixture_launch_is_loopback_and_admin_secret_never_enters_arguments(monkeypatch):
    import sys

    module = sys.modules[__name__]
    commands, images = [], []
    monkeypatch.setattr(module, "_certificate", lambda path: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
    def image(name, *, allow_pull):
        images.append((name, allow_pull))
        return "sha256:" + "a" * 64, name + "@sha256:" + "a" * 64
    def docker(*args, operation, **kwargs):
        commands.append((args, kwargs.get("environment")))
        return "" if "ls" in args else "a" * 64
    monkeypatch.setattr(module, "_image", image)
    monkeypatch.setattr(module, "_docker", docker)
    monkeypatch.setattr(module, "_port", lambda name, port: 16443 if port == 8443 else 18025)
    monkeypatch.setattr(module, "_wait_ready", lambda environment: None)
    with recovery_environment() as environment:
        password = environment.admin_password.get_secret_value()
        assert password not in repr(environment)
        assert environment.keycloak_origin == "https://localhost:16443"
    assert images == [(KEYCLOAK_IMAGE, False), (MAILPIT_IMAGE, True)]
    launches = [(args, env) for args, env in commands if args[0] == "run"]
    assert len(launches) == 2
    for args, _ in launches:
        assert args[args.index("--publish") + 1].startswith("127.0.0.1::")
        assert "1025" not in args and password not in " ".join(args)
    identity_args, child_env = launches[1]
    assert identity_args[identity_args.index("--env") + 1] == "KC_BOOTSTRAP_ADMIN_USERNAME"
    assert child_env["KC_BOOTSTRAP_ADMIN_PASSWORD"] == password
    assert any("com.docker.network.bridge.host_binding_ipv4=127.0.0.1" in args for args, _ in commands)


if __name__ == "__main__":
    try:
        with recovery_environment() as fixture:
            print("K03_RECOVERY_IMAGES=" + json.dumps(dict(fixture.image_digests)), flush=True)
    except Exception as error:
        print("K03_RECOVERY_ENVIRONMENT=FAIL:" + type(error).__name__, flush=True)
        raise SystemExit(1) from None
