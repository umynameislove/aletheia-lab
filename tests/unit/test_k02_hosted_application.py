"""ASGI adapter checks with a synthetic backend, not an auth/staging test."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aletheia_lab.hosted.application import AccessDenied, AdapterLimits, HostedApplication
from aletheia_lab.hosted_contract.session_capabilities import Capabilities, Problem, Session

ROOT = Path(__file__).resolve().parents[2]
LIMITS = AdapterLimits(1.0, 10000, 4096)
CAP_PATH = "/v1/workspaces/ws_synthetic_001/capabilities"
COOKIE = [(b"cookie", b"session=synthetic")]


class SyntheticBackend:
    """Test double only; no assertion that a real cookie or grant is valid."""

    def __init__(self):
        root = ROOT / "fixtures" / "j01" / "producer-draft"
        self.session = Session.model_validate_json((root / "session.json").read_bytes())
        future = datetime.now(UTC) + timedelta(hours=1)
        info = self.session.session.model_copy(update={"expires_at": future})
        self.session = self.session.model_copy(update={"session": info})
        self.answer = Capabilities.model_validate_json((root / "capabilities.json").read_bytes())
        self.cookie = None
        self.seen_scope = None
        self.denial = None
        self.membership_denial = None
        self.capability_calls = 0
        self.ready = True

    async def is_ready(self):
        return self.ready

    async def load_session(self, cookie):
        self.cookie = cookie
        if self.denial is not None:
            raise AccessDenied(self.denial)
        return self.session

    async def capabilities(self, session, workspace_id):
        self.capability_calls += 1
        self.seen_scope = (session.actor.user_id, workspace_id)
        if self.membership_denial is not None:
            raise AccessDenied(self.membership_denial)
        return self.answer


def _request(backend, route, *, method="GET", headers=COOKIE, limits=LIMITS):
    async def run():
        messages = []

        async def receive():
            raise AssertionError("GET adapter must not read a request body")

        async def send(message):
            messages.append(message)

        scope = {"type": "http", "path": route, "method": method, "headers": headers}
        await HostedApplication(backend, limits=limits)(scope, receive, send)
        return messages

    start, end = asyncio.run(run())
    return start["status"], dict(start["headers"]), end["body"]


def test_unconfigured_adapter_reports_liveness_but_never_readiness_or_fake_session():
    assert _request(None, "/health/live")[0] == 200
    for route in ("/health/ready", "/v1/session", CAP_PATH):
        status, headers, body = _request(None, route)
        assert status == 503
        assert headers[b"cache-control"] == b"no-store"
        assert Problem.model_validate_json(body).code == "service_unavailable"


def test_readiness_depends_on_backend_probe():
    backend = SyntheticBackend()
    assert _request(backend, "/health/ready")[0] == 200
    backend.ready = False
    assert _request(backend, "/health/ready")[0] == 503


def test_session_serializer_uses_shared_model_and_does_not_accept_identity_headers():
    backend = SyntheticBackend()
    headers = [(b"cookie", b"session=synthetic"), (b"x-user-id", b"wrong-actor"), (b"x-p6-bridge", b"public")]
    status, response_headers, body = _request(backend, "/v1/session", headers=headers)
    assert status == 200
    assert backend.cookie == b"session=synthetic"
    assert Session.model_validate_json(body) == backend.session
    assert response_headers[b"cache-control"] == b"no-store"
    assert int(response_headers[b"content-length"]) == len(body)


def test_capabilities_uses_server_actor_and_exact_url_workspace():
    backend = SyntheticBackend()
    status, _, body = _request(backend, CAP_PATH, headers=COOKIE + [(b"x-role", b"owner")])
    assert status == 200
    assert backend.seen_scope == (backend.session.actor.user_id, "ws_synthetic_001")
    assert Capabilities.model_validate_json(body) == backend.answer


@pytest.mark.parametrize("denial,status", [
    ("session_expired", 401),
    ("session_revoked", 401),
    ("membership_revoked", 403),
    ("permission_denied", 403),
])
def test_access_denials_are_shared_problem_payloads(denial, status):
    backend = SyntheticBackend()
    backend.denial = denial
    observed, headers, body = _request(backend, CAP_PATH)
    problem = Problem.model_validate_json(body)
    assert observed == problem.status == status
    assert problem.code == denial
    assert problem.request_id.encode() == headers[b"x-request-id"]
    assert headers[b"content-type"] == b"application/problem+json"


def test_wrong_workspace_and_mutated_producer_fields_fail_closed():
    backend = SyntheticBackend()
    backend.answer = backend.answer.model_copy(update={"workspace_id": "ws_other"})
    status, _, body = _request(backend, CAP_PATH)
    assert status == 500
    assert b"ws_other" not in body
    backend.answer = backend.answer.model_copy(update={"workspace_id": "ws_synthetic_001", "schema_version": "unsupported"})
    assert _request(backend, CAP_PATH)[0] == 500


def test_each_capability_read_checks_the_backend_again_after_a_denial():
    backend = SyntheticBackend()
    assert _request(backend, CAP_PATH)[0] == 200
    backend.membership_denial = "membership_revoked"
    status, _, body = _request(backend, CAP_PATH)
    assert status == 403
    assert Problem.model_validate_json(body).code == "membership_revoked"
    assert backend.capability_calls == 2


def test_missing_or_oversized_cookie_never_reaches_session_lookup():
    backend = SyntheticBackend()
    status, _, body = _request(backend, "/v1/session", headers=[])
    assert status == 401
    assert Problem.model_validate_json(body).code == "session_expired"
    assert backend.cookie is None
    status, _, body = _request(backend, "/v1/session", headers=[(b"cookie", b"x" * 4097)])
    assert status == 431
    assert Problem.model_validate_json(body).code == "headers_too_large"
    assert backend.cookie is None


def test_absolute_expiry_is_checked_even_if_backend_returns_stale_session():
    backend = SyntheticBackend()
    past = datetime.now(UTC) - timedelta(seconds=1)
    info = backend.session.session.model_copy(update={"expires_at": past})
    backend.session = backend.session.model_copy(update={"session": info})
    status, _, body = _request(backend, "/v1/session")
    assert status == 401
    assert Problem.model_validate_json(body).code == "session_expired"


def test_backend_exception_does_not_disclose_credentials_or_path():
    class BrokenBackend(SyntheticBackend):
        async def load_session(self, cookie):
            raise RuntimeError("C:/private/example credential=synthetic-sensitive-value")

    status, _, body = _request(BrokenBackend(), "/v1/session")
    assert status == 500
    assert b"private" not in body
    assert b"synthetic-sensitive-value" not in body


def test_backend_timeout_and_external_cancellation_have_different_outcomes():
    class SlowBackend(SyntheticBackend):
        async def load_session(self, cookie):
            await asyncio.Event().wait()

    limits = AdapterLimits(0.01, 10000, 4096)
    status, _, body = _request(SlowBackend(), "/v1/session", limits=limits)
    assert status == 503
    assert Problem.model_validate_json(body).code == "service_unavailable"

    class CancelledBackend(SyntheticBackend):
        async def load_session(self, cookie):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        _request(CancelledBackend(), "/v1/session")


def test_methods_head_unknown_routes_and_generated_request_ids():
    backend = SyntheticBackend()
    status, headers, body = _request(backend, "/v1/session", method="POST")
    assert status == 405
    assert headers[b"allow"] == b"GET, HEAD"
    assert Problem.model_validate_json(body).code == "method_not_allowed"
    status, headers, body = _request(backend, "/v1/session", method="HEAD")
    assert status == 200 and body == b""
    assert int(headers[b"content-length"]) > 0
    assert _request(backend, "/v1/unknown")[0] == 404
    _, first, _ = _request(backend, "/v1/session", headers=COOKIE + [(b"x-request-id", b"attacker-controlled")])
    _, second, _ = _request(backend, "/v1/session")
    assert first[b"x-request-id"] != b"attacker-controlled"
    assert first[b"x-request-id"] != second[b"x-request-id"]


def test_oversized_output_is_replaced_with_a_small_safe_problem():
    backend = SyntheticBackend()
    backend.answer.allowed_actions.extend([f"action_{i}_" + "x" * 200 for i in range(32)])
    status, _, body = _request(backend, CAP_PATH, limits=AdapterLimits(1.0, 4096, 4096))
    assert status == 500
    assert Problem.model_validate_json(body).code == "response_invalid"
    assert len(body) < 4096


def test_invalid_nested_mutation_is_revalidated_before_serialization():
    backend = SyntheticBackend()
    backend.answer.features["events.sse"] = "not-a-boolean"
    status, _, body = _request(backend, CAP_PATH)
    assert status == 500
    assert Problem.model_validate_json(body).code == "service_failure"
    assert b"not-a-boolean" not in body


def test_lifespan_completion_does_not_make_an_unconfigured_backend_ready():
    async def run():
        events = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
        messages = []

        async def receive():
            return next(events)

        async def send(message):
            messages.append(message)

        await HostedApplication(None, limits=LIMITS)({"type": "lifespan"}, receive, send)
        return messages

    assert asyncio.run(run()) == [
        {"type": "lifespan.startup.complete"},
        {"type": "lifespan.shutdown.complete"},
    ]
    assert _request(None, "/health/ready")[0] == 503
