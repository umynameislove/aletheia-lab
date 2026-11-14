"""Renewal uses current creation policy and its current credential, without services."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from test_k03_auth_abuse import SyntheticAdmissionStore
from test_k03_session_adapter import NOW, POLICY, TOKEN_A, _cookie
from test_k03_session_http import _cookies, _request, _system

from aletheia_lab.hosted.upload_runtime import compose_upload_runtime
from aletheia_lab.hosted.workspace_management import WorkspaceDenied
from aletheia_lab.hosted_auth.abuse import AuthAdmission, RatePolicy
from aletheia_lab.hosted_auth.sessions import credential_digest
from aletheia_lab.hosted_contract.session_capabilities import Limits, Problem, Session


class _Management:
    def __init__(self, store):
        self.store, self.allowed, self.error = store, True, None
        self.seen, self.effects_at_call, self.block = [], [], False

    async def is_ready(self):
        return True

    async def can_create_workspace(self, *, credential_digest):
        self.seen.append(credential_digest)
        self.effects_at_call.append(self.store.effects)
        current = self.store.current(credential_digest)
        if current is None or current.state != "active":
            raise WorkspaceDenied()
        if self.error is not None:
            raise self.error
        if self.block:
            await asyncio.Event().wait()
        return self.allowed


def _composed(tmp_path, *, rotate, management=True, timeout=1.0):
    base, sessions, store, tokens, headers = _system(tmp_path, timeout=timeout)
    now = datetime.now(UTC)
    delta = now - NOW
    for digest, record in list(store.records.items()):
        values = {name: getattr(record, name) + delta for name in (
            "issued_at", "last_seen_at", "rotated_at", "idle_expires_at", "absolute_expires_at")}
        if not rotate:
            values["rotated_at"] = now
            values["last_seen_at"] = now
        store.records[digest] = record.model_copy(update=values)
    store.now = now
    policy = _Management(store) if management else None

    class Workspaces:
        async def is_ready(self):
            return True

    async def ready():
        return True

    app = compose_upload_runtime(settings=base._runtime, sessions=sessions, tokens=tokens,
        workspaces=Workspaces(), sources=SimpleNamespace(maximum_upload_bytes=1024), jobs=object(),
        coordinator=object(), auth_policy=POLICY, clock_check=ready,
        limits=Limits(question_chars=2000, upload_bundle_bytes=1024, page_max=200, job_poll_min_ms=2000),
        admission=AuthAdmission(SyntheticAdmissionStore(), RatePolicy(100, 100, 60), timeout_seconds=1.0),
        admission_subject_key=SecretStr("synthetic-hint-admission-" + "a" * 43), membership_management=policy)
    return app, store, policy, [*headers, (b"host", b"app.example.invalid")]


@pytest.mark.parametrize("rotate", [False, True], ids=["early", "rotated"])
def test_composed_renewal_preserves_current_creation_hint_and_uses_current_credential(tmp_path, rotate):
    async def run():
        app, store, policy, headers = _composed(tmp_path, rotate=rotate)
        # Use the production routes/backend composition with only synthetic authorities.
        async with app._resources() as backend:
            app._application.backend = backend
            before, _ = await _request(app, headers, path="/v1/session", method="GET")
            assert Session.model_validate_json(before[1]["body"]).can_create_workspace is True
            renewed, _ = await _request(app, headers)
            assert renewed[0]["status"] == 200
            session = Session.model_validate_json(renewed[1]["body"])
            assert session.can_create_workspace is True
            cookies = _cookies(renewed)
            token = cookies["__Host-aletheia_session"].value if rotate else TOKEN_A
            expected = credential_digest(SecretStr(token))
            assert policy.seen[-1] == credential_digest(SecretStr(TOKEN_A))
            assert policy.effects_at_call == [0, 0]
            assert (expected == credential_digest(SecretStr(TOKEN_A))) is not rotate
            assert store.effects == int(rotate)
            # Creation permission is not inferred from the unchanged onboarding field.
            policy.allowed = False
            current_headers = [(name, _cookie(token) + b"; __Host-aletheia_csrf="
                + session.csrf_token.encode() if name == b"cookie" else value) for name, value in headers]
            again, _ = await _request(app, current_headers)
            assert again[0]["status"] == 200 and not _cookies(again)
            assert Session.model_validate_json(again[1]["body"]).can_create_workspace is False
            assert policy.seen[-1] == expected and store.effects == int(rotate)
    asyncio.run(run())


@pytest.mark.parametrize("error,status,code", [
    (WorkspaceDenied(), 401, "session_expired"),
    (TimeoutError("private-policy-timeout"), 503, "service_unavailable"),
    (RuntimeError("private-policy-failure"), 500, "service_failure"),
])
def test_creation_hint_failure_precedes_rotation_and_emits_no_credentials(tmp_path, error, status, code):
    async def run():
        app, store, policy, headers = _composed(tmp_path, rotate=True)
        policy.error = error
        async with app._resources() as backend:
            app._application.backend = backend
            events, _ = await _request(app, headers)
        assert events[0]["status"] == status and not _cookies(events)
        assert Problem.model_validate_json(events[1]["body"]).code == code
        assert b"private-policy" not in events[1]["body"]
        assert store.effects == 0 and len(policy.seen) == 1
        assert store.current(credential_digest(SecretStr(TOKEN_A))).state == "active"
    asyncio.run(run())


@pytest.mark.parametrize("allowed", [None, 1, "true"])
def test_malformed_creation_policy_never_claims_a_successful_session(tmp_path, allowed):
    async def run():
        app, store, policy, headers = _composed(tmp_path, rotate=True)
        policy.allowed = allowed
        async with app._resources() as backend:
            app._application.backend = backend
            events, _ = await _request(app, headers)
        assert events[0]["status"] == 500 and not _cookies(events)
        assert store.effects == 0
    asyncio.run(run())


def test_creation_hint_wait_is_bounded_without_rotating_the_credential(tmp_path):
    async def run():
        app, store, policy, headers = _composed(tmp_path, rotate=True, timeout=0.02)
        policy.block = True
        async with app._resources() as backend:
            app._application.backend = backend
            events, _ = await asyncio.wait_for(_request(app, headers), 1.0)
        assert events[0]["status"] == 503 and not _cookies(events)
        assert Problem.model_validate_json(events[1]["body"]).code == "service_unavailable"
        assert store.effects == 0 and policy.effects_at_call == [0]
        assert store.current(credential_digest(SecretStr(TOKEN_A))).state == "active"
    asyncio.run(run())


def test_unconfigured_creation_policy_keeps_existing_false_default(tmp_path):
    async def run():
        app, _, _, headers = _composed(tmp_path, rotate=True, management=False)
        async with app._resources() as backend:
            app._application.backend = backend
            events, _ = await _request(app, headers)
        assert events[0]["status"] == 200
        assert Session.model_validate_json(events[1]["body"]).can_create_workspace is False
    asyncio.run(run())
