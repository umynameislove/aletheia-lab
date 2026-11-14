"""Read-backend checks with synthetic repositories, not a login/storage test."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from aletheia_lab.hosted.application import AccessDenied, AdapterLimits, HostedApplication
from aletheia_lab.hosted.read_backend import (
    ReadOnlyHostedBackend,
    SessionRecord,
    WorkspaceAccessRecord,
)
from aletheia_lab.hosted_contract.session_capabilities import (
    Actor,
    Limits,
    Membership,
    Problem,
    Session,
)

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2030, 1, 1, tzinfo=UTC)


def _facts():
    fixture = json.loads((ROOT / "fixtures" / "j01" / "producer-review-v6" / "session.json").read_bytes())
    return SessionRecord(
        actor=Actor.model_validate(fixture["actor"]),
        context_id=fixture["session"]["context_id"],
        expires_at=NOW + timedelta(hours=1),
        csrf_token=SecretStr(fixture["csrf_token"]),
        onboarding=fixture["onboarding"], last_workspace_id=fixture["last_workspace_id"],
        state="active", actor_enabled=True,
    )


class SyntheticSessions:
    def __init__(self):
        self.record = _facts()
        self.calls = 0
        self.ready = True

    async def is_ready(self):
        return self.ready

    async def resolve_cookie(self, cookie):
        self.calls += 1
        return self.record if cookie == b"session=synthetic" else None


class SyntheticWorkspaces:
    def __init__(self, sessions):
        self.sessions = sessions
        self.calls = []
        self.membership = Membership(role="analyst", revision=1)
        self.active = True
        self.ready = True
        self.wrong_scope = None

    async def is_ready(self):
        return self.ready

    async def access_for_context(self, *, user_id, context_id, workspace_id):
        self.calls.append((user_id, context_id, workspace_id))
        if workspace_id != "ws_synthetic_001":
            return None
        return WorkspaceAccessRecord(
            session=self.sessions.record,
            workspace_id=self.wrong_scope or workspace_id,
            workspace_name="Synthetic Workspace",
            membership=self.membership, membership_active=self.active,
        )


def _backend():
    sessions = SyntheticSessions()
    workspaces = SyntheticWorkspaces(sessions)
    source = json.loads((ROOT / "fixtures" / "j01" / "producer-review-v6" / "capabilities.json").read_bytes())
    backend = ReadOnlyHostedBackend(sessions, workspaces, limits=Limits.model_validate(source["limits"]), clock=lambda: NOW)
    return backend, sessions, workspaces


def test_session_is_serialized_from_current_server_facts():
    backend, sessions, _ = _backend()
    session = asyncio.run(backend.load_session(b"session=synthetic"))
    assert session.actor.user_id == sessions.record.actor.user_id
    assert session.session.context_id == sessions.record.context_id
    assert session.session.expires_at == sessions.record.expires_at
    assert Session.model_validate_json(session.model_dump_json()) == session
    assert sessions.calls == 1
    assert "synthetic-not-a-credential" not in repr(sessions.record)


@pytest.mark.parametrize("cookie", [None, b"", b"session=forged"])
def test_missing_or_unknown_cookie_never_creates_a_session(cookie):
    backend, _, _ = _backend()
    with pytest.raises(AccessDenied) as caught:
        asyncio.run(backend.load_session(cookie))
    assert caught.value.code == "session_expired"


@pytest.mark.parametrize("changes,code", [
    ({"state": "expired"}, "session_expired"),
    ({"state": "revoked"}, "session_revoked"),
    ({"actor_enabled": False}, "session_revoked"),
    ({"state": "unknown"}, "session_revoked"),
    ({"expires_at": NOW}, "session_expired"),
])
def test_current_session_state_denies_access(changes, code):
    backend, sessions, _ = _backend()
    sessions.record = replace(sessions.record, **changes)
    with pytest.raises(AccessDenied) as caught:
        asyncio.run(backend.load_session(b"session=synthetic"))
    assert caught.value.code == code


def test_timezone_and_corrupt_model_data_fail_closed():
    backend, sessions, _ = _backend()
    sessions.record = replace(sessions.record, expires_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="timezones"):
        asyncio.run(backend.load_session(b"session=synthetic"))
    sessions.record = replace(_facts(), actor=_facts().actor.model_copy(update={"email_verified": "yes"}))
    with pytest.raises(ValidationError):
        asyncio.run(backend.load_session(b"session=synthetic"))


def test_caps_are_scoped_and_never_enable_unimplemented_actions():
    backend, _, workspaces = _backend()

    async def run():
        session = await backend.load_session(b"session=synthetic")
        caps = await backend.capabilities(session, "ws_synthetic_001")
        assert workspaces.calls == [(session.actor.user_id, session.session.context_id, "ws_synthetic_001")]
        assert caps.allowed_actions == []
        assert caps.features == {}
        assert caps.analysis_backends == []
        assert caps.supported_source_types == []
        assert caps.supported_export_formats == []
        assert caps.membership.role == "analyst"

    asyncio.run(run())


@pytest.mark.parametrize("workspace", ["ws_missing", "ws_other_actor"])
def test_no_membership_returns_same_denial_without_workspace_metadata(workspace):
    backend, _, _ = _backend()

    async def run():
        session = await backend.load_session(b"session=synthetic")
        with pytest.raises(AccessDenied) as caught:
            await backend.capabilities(session, workspace)
        assert caught.value.code == "permission_denied"
        assert "Synthetic Workspace" not in str(caught.value)

    asyncio.run(run())


@pytest.mark.parametrize("changes,code", [
    ({"state": "revoked"}, "session_revoked"),
    ({"actor_enabled": False}, "session_revoked"),
    ({"expires_at": NOW}, "session_expired"),
])
def test_revocation_or_expiry_after_session_read_is_seen_before_capabilities(changes, code):
    backend, sessions, workspaces = _backend()

    async def run():
        session = await backend.load_session(b"session=synthetic")
        await backend.capabilities(session, "ws_synthetic_001")
        sessions.record = replace(sessions.record, **changes)
        with pytest.raises(AccessDenied) as caught:
            await backend.capabilities(session, "ws_synthetic_001")
        assert caught.value.code == code
        assert len(workspaces.calls) == 2

    asyncio.run(run())


def test_role_revision_and_membership_revocation_are_not_cached():
    backend, _, workspaces = _backend()

    async def run():
        session = await backend.load_session(b"session=synthetic")
        assert (await backend.capabilities(session, "ws_synthetic_001")).membership.role == "analyst"
        workspaces.membership = Membership(role="viewer", revision=2)
        caps = await backend.capabilities(session, "ws_synthetic_001")
        assert (caps.membership.role, caps.membership.revision) == ("viewer", 2)
        assert caps.allowed_actions == []
        workspaces.active = False
        with pytest.raises(AccessDenied) as caught:
            await backend.capabilities(session, "ws_synthetic_001")
        assert caught.value.code == "membership_revoked"

    asyncio.run(run())


@pytest.mark.parametrize("mismatch", ["actor", "context", "workspace"])
def test_repository_scope_mismatch_does_not_serve_other_records(mismatch):
    backend, sessions, workspaces = _backend()

    async def run():
        session = await backend.load_session(b"session=synthetic")
        if mismatch == "actor":
            sessions.record = replace(sessions.record, actor=sessions.record.actor.model_copy(update={"user_id": "usr_other"}))
        elif mismatch == "context":
            sessions.record = replace(sessions.record, context_id="ctx_other")
        else:
            workspaces.wrong_scope = "ws_other"
        with pytest.raises(AccessDenied) as caught:
            await backend.capabilities(session, "ws_synthetic_001")
        assert caught.value.code == "permission_denied"

    asyncio.run(run())


def test_readiness_requires_both_repositories():
    backend, sessions, workspaces = _backend()
    assert asyncio.run(backend.is_ready()) is True
    sessions.ready = False
    assert asyncio.run(backend.is_ready()) is False
    sessions.ready, workspaces.ready = True, False
    assert asyncio.run(backend.is_ready()) is False


def test_readiness_exception_is_unavailable_but_external_cancellation_propagates():
    backend, sessions, _ = _backend()

    async def broken():
        raise RuntimeError("private-readiness-synthetic-secret")

    sessions.is_ready = broken
    assert asyncio.run(backend.is_ready()) is False

    async def cancelled():
        raise asyncio.CancelledError

    sessions.is_ready = cancelled
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(backend.is_ready())


def test_provider_error_is_redacted_by_existing_http_boundary():
    backend, sessions, _ = _backend()

    async def broken(cookie):
        raise RuntimeError("private-dsn-synthetic-secret")

    sessions.resolve_cookie = broken

    async def run():
        messages = []

        async def receive():
            raise AssertionError("GET should not read a body")

        async def send(message):
            messages.append(message)

        app = HostedApplication(backend, limits=AdapterLimits(1.0, 10000, 4096))
        await app({"type": "http", "method": "GET", "path": "/v1/session",
                   "headers": [(b"cookie", b"session=synthetic")]}, receive, send)
        assert messages[0]["status"] == 500
        body = messages[1]["body"]
        assert Problem.model_validate_json(body).code == "service_failure"
        assert b"synthetic-secret" not in body

    asyncio.run(run())
