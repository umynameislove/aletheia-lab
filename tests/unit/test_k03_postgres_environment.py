"""Opt-in private PG16 fixture; pytest exercises guards without starting services."""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field

import pytest
from pydantic import SecretStr
from test_k03_recovery_environment import RecoveryEnvironmentUnavailable, _binding, _docker, _image

_IMAGE = "postgres:16"
_IMAGE_ID = "sha256:f1c3376c26f2609ab9f29f71f824103fe2fcd8ee0346485cb6122a4f93df6f94"
_PREFIX = "aletheia-k03-postgres-"
_LABEL = "aletheia.owner=k03-private-postgres"
_INSPECT = ("{{json .Id}}|{{json .Name}}|{{json .Config.Labels}}|{{json .Image}}|"
            "{{json .HostConfig.PortBindings}}|{{json .NetworkSettings.Ports}}|"
            "{{json .State.Running}}|{{json .Mounts}}")


@dataclass(frozen=True)
class PrivatePostgres:
    port: int
    password: SecretStr = field(repr=False)
    image_digest: str


def database_port() -> int:
    """Manual K03 callers retain their historical port unless explicitly overridden."""
    value = os.environ.get("K03_DB_PORT", "15432")
    if re.fullmatch(r"[0-9]{4,5}", value) is None or not 1024 <= int(value) <= 65535:
        raise ValueError("A bounded local test database port is required.")
    return int(value)


def _private_port(port: int) -> int:
    if type(port) is not int or not 1024 <= port <= 65535 or port == 15432:
        raise ValueError("An isolated loopback test database port is required.")
    return port


def _owned(identity: str, name: str, owner: str) -> None:
    if (not isinstance(identity, str) or re.fullmatch(r"[0-9a-f]{64}", identity) is None
            or not isinstance(owner, str) or re.fullmatch(r"[0-9a-f]{32}", owner) is None
            or name != _PREFIX + owner):
        raise RecoveryEnvironmentUnavailable("postgres_identity")


def _inspect(identity, name, owner, image, expected_port=None):
    _owned(identity, name, owner)
    raw = _docker("container", "inspect", "--format", _INSPECT, identity, operation="postgres_identity")
    try:
        actual, actual_name, labels, actual_image, configured, mapped, running, mounts = map(json.loads, raw.split("|"))
        if (actual != identity or actual_name != "/" + name or actual_image != image
                or labels.get("aletheia.owner") != "k03-private-postgres" or labels.get("aletheia.run_id") != owner
                or type(running) is not bool or set(configured) != {"5432/tcp"}):
            raise ValueError()
        configured_bindings = configured["5432/tcp"]
        if (not isinstance(configured_bindings, list) or len(configured_bindings) != 1
                or configured_bindings[0].get("HostIp") != "127.0.0.1"):
            raise ValueError()
        configured_port = configured_bindings[0].get("HostPort")
        if configured_port not in ("", "0"):
            _private_port(_binding(json.dumps(configured_bindings)))
        port = expected_port
        if running:
            port = _private_port(_binding(json.dumps(mapped["5432/tcp"])))
            if expected_port is not None and port != expected_port:
                raise ValueError()
        if configured_port not in ("", "0", str(port)):
            raise ValueError()
        data = [mount for mount in mounts if mount["Destination"] == "/var/lib/postgresql/data"]
        if (len(data) != 1 or data[0]["Type"] != "volume" or data[0]["RW"] is not True
                or re.fullmatch(r"[0-9a-f]{64}", data[0]["Name"]) is None):
            raise ValueError()
        return port, data[0]["Name"], running
    except (AttributeError, KeyError, TypeError, ValueError):
        raise RecoveryEnvironmentUnavailable("postgres_identity") from None


def _inventory(name, owner):
    if re.fullmatch(r"[0-9a-f]{32}", owner) is None or name != _PREFIX + owner:
        raise RecoveryEnvironmentUnavailable("postgres_identity")
    return (_docker("container", "ls", "--all", "--no-trunc", "--filter", "label=" + _LABEL,
                    "--filter", "label=aletheia.run_id=" + owner, "--filter", "name=" + name,
                    "--format", "{{.ID}}", operation="postgres_inventory") or "").strip()


def _cleanup(identity, name, owner, image, port, volume):
    observed = _inventory(name, owner)
    if observed:
        if identity is not None and observed != identity:
            raise RecoveryEnvironmentUnavailable("postgres_identity")
        identity = observed
        _, actual_volume, _ = _inspect(identity, name, owner, image, port)
        if volume is not None and actual_volume != volume:
            raise RecoveryEnvironmentUnavailable("postgres_identity")
        volume = actual_volume
        _docker("container", "rm", "--force", "--volumes", identity, operation="postgres_cleanup")
        if _inventory(name, owner):
            raise RecoveryEnvironmentUnavailable("postgres_cleanup")
    if volume is not None:
        volumes = (_docker("volume", "ls", "--filter", "name=" + volume, "--format", "{{.Name}}",
                           operation="postgres_volume_inventory") or "").splitlines()
        if volume in volumes:
            raise RecoveryEnvironmentUnavailable("postgres_cleanup")
    print("K03_PRIVATE_POSTGRES_REMOVED=PASS", flush=True)


def _wait_ready(port, password):
    _private_port(port)
    import psycopg

    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(host="127.0.0.1", port=port, dbname="aletheia", user="aletheia",
                                 password=password.get_secret_value(), connect_timeout=1,
                                 options="-c statement_timeout=2000", autocommit=True) as conn:
                if conn.execute("SELECT 1").fetchone() == (1,):
                    return
        except psycopg.OperationalError:
            pass
        time.sleep(0.1)
    raise RecoveryEnvironmentUnavailable("postgres_readiness")


@contextmanager
def private_postgres():
    """Create one cached-image process, never restart/fault or borrow another DB."""
    owner = uuid.uuid4().hex
    name, password = _PREFIX + owner, SecretStr(secrets.token_urlsafe(32))
    image, digest = _image(_IMAGE, allow_pull=False)
    if image != _IMAGE_ID:
        raise RecoveryEnvironmentUnavailable("postgres_cached_image")
    identity = port = volume = None
    child = dict(os.environ, POSTGRES_PASSWORD=password.get_secret_value())
    try:
        identity = _docker("run", "--detach", "--pull=never", "--name", name, "--label", _LABEL,
            "--label", "aletheia.run_id=" + owner, "--memory=512m", "--cpus=1", "--pids-limit=128",
            "--publish", "127.0.0.1::5432", "--env", "POSTGRES_PASSWORD", "--env", "POSTGRES_USER=aletheia",
            "--env", "POSTGRES_DB=aletheia", image, "-c", "max_connections=24", "-c", "shared_buffers=32MB",
            "-c", "work_mem=2MB", operation="postgres_start", environment=child)
        port, volume, running = _inspect(identity, name, owner, image)
        if not running or port is None:
            raise RecoveryEnvironmentUnavailable("postgres_readiness")
        _wait_ready(port, password)
        print("K03_PRIVATE_POSTGRES_READY=PASS", flush=True)
        yield PrivatePostgres(port=port, password=password, image_digest=digest)
    finally:
        child.pop("POSTGRES_PASSWORD", None)
        _cleanup(identity, name, owner, image, port, volume)


@pytest.mark.parametrize("raw", ["", " 32123", "32123\n", "１２３４５", "+32123", "1023", "65536", "5432;drop"])
def test_port_override_rejects_ambiguous_or_nonlocal_input(monkeypatch, raw):
    monkeypatch.setenv("K03_DB_PORT", raw)
    with pytest.raises(ValueError):
        database_port()


def test_port_override_and_legacy_default(monkeypatch):
    monkeypatch.delenv("K03_DB_PORT", raising=False)
    assert database_port() == 15432
    monkeypatch.setenv("K03_DB_PORT", "32123")
    assert database_port() == 32123
    for value in (True, 15432, 1023, 65536, "32123"):
        with pytest.raises(ValueError):
            _private_port(value)


def _fake_docker(monkeypatch, *, change=None, start_failure=False, retain_container=False, retain_volume=False):
    calls, state = [], {"present": False}
    identity, volume = "a" * 64, "b" * 64

    def docker(*args, operation, **kwargs):
        calls.append((args, operation, kwargs))
        if operation == "postgres_start":
            state["name"] = args[args.index("--name") + 1]
            state["owner"] = state["name"].removeprefix(_PREFIX)
            state["present"] = True
            if start_failure:
                raise RecoveryEnvironmentUnavailable("postgres_start")
            return identity
        if operation == "postgres_inventory":
            return identity if state["present"] else ""
        if operation == "postgres_identity":
            values = [identity, "/" + state["name"], {"aletheia.owner": "k03-private-postgres",
                "aletheia.run_id": state["owner"]}, _IMAGE_ID,
                {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": ""}]},
                {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "32123"}]}, True,
                [{"Destination": "/var/lib/postgresql/data", "Type": "volume", "RW": True, "Name": volume}]]
            if change is not None:
                change(values)
            return "|".join(json.dumps(value) for value in values)
        if operation == "postgres_cleanup":
            assert args == ("container", "rm", "--force", "--volumes", identity)
            state["present"] = retain_container
            return identity
        if operation == "postgres_volume_inventory":
            return volume if retain_volume else ""
        raise AssertionError("Unexpected Docker operation")

    module = sys.modules[__name__]
    monkeypatch.setattr(module, "_docker", docker)
    monkeypatch.setattr(module, "_image", lambda image, *, allow_pull: (_IMAGE_ID, image + "@sha256:" + "c" * 64)
                        if image == _IMAGE and allow_pull is False else pytest.fail("cached image only"))
    monkeypatch.setattr(module, "_wait_ready", lambda port, password: _private_port(port))
    return calls, state


@pytest.mark.parametrize("configured", ["", "0", "32123"])
def test_private_process_is_bounded_loopback_secret_only_in_child_env_and_removed(monkeypatch, configured):
    calls, state = _fake_docker(monkeypatch,
        change=lambda row: row[4]["5432/tcp"][0].__setitem__("HostPort", configured))
    with private_postgres() as database:
        assert database.port == 32123 and database.image_digest.startswith("postgres:16@sha256:")
        password = database.password.get_secret_value()
        assert password not in repr(database) and "password=" not in repr(database)
        args, _, options = calls[0]
        assert options["environment"]["POSTGRES_PASSWORD"] == password
        assert all(password not in arg for arg in args)
        assert args[args.index("--publish") + 1] == "127.0.0.1::5432"
        assert all(flag in args for flag in ("--pull=never", "--memory=512m", "--cpus=1", "--pids-limit=128",
                                            "max_connections=24", "shared_buffers=32MB", "work_mem=2MB"))
    assert not state["present"]
    assert any(operation == "postgres_volume_inventory" for _, operation, _ in calls)
    assert all("Config.Env" not in arg for args, _, _ in calls for arg in args)


def test_cleanup_runs_after_start_timeout_using_exact_uuid_inventory(monkeypatch):
    calls, state = _fake_docker(monkeypatch, start_failure=True)
    with pytest.raises(RecoveryEnvironmentUnavailable, match="postgres_start"), private_postgres():
        pytest.fail("failed startup cannot yield")
    assert not state["present"]
    assert any(operation == "postgres_cleanup" for _, operation, _ in calls)


@pytest.mark.parametrize("change", [
    lambda row: row.__setitem__(0, "d" * 64), lambda row: row.__setitem__(1, "/aletheia-j01-postgres-review"),
    lambda row: row[2].__setitem__("aletheia.run_id", "d" * 32), lambda row: row.__setitem__(3, "sha256:" + "d" * 64),
    lambda row: row[5]["5432/tcp"][0].__setitem__("HostIp", "0.0.0.0"),
    lambda row: row[5]["5432/tcp"][0].__setitem__("HostPort", "15432"),
    lambda row: row[7][0].__setitem__("Type", "bind"), lambda row: row[7][0].__setitem__("Name", "shared-volume"),
])
def test_wrong_identity_binding_or_volume_never_permits_removal(monkeypatch, change):
    calls, state = _fake_docker(monkeypatch, change=change)
    with pytest.raises(RecoveryEnvironmentUnavailable, match="postgres_identity"), private_postgres():
        pytest.fail("unverified process cannot yield")
    assert state["present"]
    assert not any(operation == "postgres_cleanup" for _, operation, _ in calls)


def test_body_failure_still_removes_only_own_process(monkeypatch):
    calls, state = _fake_docker(monkeypatch)
    with pytest.raises(RuntimeError, match="synthetic body failure"), private_postgres():
        raise RuntimeError("synthetic body failure")
    assert not state["present"]
    assert sum(operation == "postgres_cleanup" for _, operation, _ in calls) == 1


@pytest.mark.parametrize("leftover", ["container", "volume"])
def test_cleanup_cannot_report_success_with_a_remaining_resource(monkeypatch, leftover, capsys):
    _fake_docker(monkeypatch, retain_container=leftover == "container", retain_volume=leftover == "volume")
    with pytest.raises(RecoveryEnvironmentUnavailable, match="postgres_cleanup"), private_postgres():
        pass
    assert "K03_PRIVATE_POSTGRES_REMOVED=PASS" not in capsys.readouterr().out


def test_cached_image_change_does_not_start_a_process(monkeypatch):
    calls = []
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "_image", lambda *args, **kwargs: ("sha256:" + "d" * 64, "other-image"))
    monkeypatch.setattr(module, "_docker", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(RecoveryEnvironmentUnavailable, match="postgres_cached_image"), private_postgres():
        pytest.fail("unverified image cannot yield")
    assert calls == []


def test_readiness_uses_bounded_private_connection_and_fixed_timeout(monkeypatch):
    from types import SimpleNamespace

    class Unavailable(Exception):
        pass

    calls, waits, times = [], [], iter([0, 0, 46])

    def connect(**kwargs):
        calls.append(kwargs)
        raise Unavailable("synthetic-private-driver-detail")

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=connect, OperationalError=Unavailable))
    monkeypatch.setattr(sys.modules[__name__], "time", SimpleNamespace(monotonic=lambda: next(times), sleep=waits.append))
    with pytest.raises(RecoveryEnvironmentUnavailable, match="postgres_readiness") as caught:
        _wait_ready(32123, SecretStr("synthetic-private-password"))
    assert len(calls) == 1 and waits == [0.1]
    assert calls[0]["host"] == "127.0.0.1" and calls[0]["port"] == 32123
    assert calls[0]["connect_timeout"] == 1 and calls[0]["options"] == "-c statement_timeout=2000"
    assert "synthetic-private" not in str(caught.value) + repr(caught.value)
