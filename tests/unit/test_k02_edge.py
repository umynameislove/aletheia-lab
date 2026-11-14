"""Host and proxy-hint checks using synthetic ASGI requests."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aletheia_lab.hosted.composition import ProcessApplication
from aletheia_lab.hosted.edge import EdgeGuard
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted_contract.session_capabilities import Session


def _request(origin, headers, *, method="GET"):
    async def run():
        seen = []
        sent = []

        async def downstream(scope, receive, send):
            seen.append(scope)
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def receive():
            raise AssertionError("edge should not read request body")

        async def send(message):
            sent.append(message)

        app = EdgeGuard(origin, downstream)
        await app({"type": "http", "method": method, "headers": headers,
                   "scheme": "http", "client": ("192.0.2.10", 40000)}, receive, send)
        return seen, sent

    return asyncio.run(run())


@pytest.mark.parametrize("host", [b"app.internal", b"APP.INTERNAL", b"app.internal:443"])
def test_accepts_only_the_configured_https_authority(host):
    seen, sent = _request("https://app.internal", [(b"host", host)])
    assert sent[0]["status"] == 204
    assert len(seen) == 1


@pytest.mark.parametrize("headers", [
    [], [(b"host", b"other.internal")], [(b"host", b"app.internal:80")],
    [(b"host", b"app.internal"), (b"Host", b"app.internal")],
    [(b"host", b"app.internal,evil.internal")], [(b"host", b"app.internal@evil.internal")],
    [(b"host", b"app.internal/path")], [(b"host", b"app.internal:99999")],
    [(b"host", b"app.internal:")],
    [(b"host", b"app.internal\r\nInjected: x")], [(b"host", b"\xff")],
])
def test_wrong_or_ambiguous_host_never_reaches_application(headers):
    seen, sent = _request("https://app.internal", headers)
    assert seen == []
    assert sent[0]["status"] == 400
    assert dict(sent[0]["headers"])[b"cache-control"] == b"no-store"
    assert sent[1]["body"] == b"Bad Request"


def test_head_rejection_has_no_body_and_does_not_echo_host():
    seen, sent = _request("https://app.internal", [(b"host", b"private.invalid")], method="HEAD")
    assert seen == []
    assert sent[0]["status"] == 400
    assert sent[1]["body"] == b""
    assert b"private.invalid" not in repr(sent).encode()


def test_forwarded_headers_cannot_change_downstream_client_or_scheme():
    headers = [
        (b"host", b"app.internal"), (b"cookie", b"session=synthetic"),
        (b"forwarded", b"for=203.0.113.2;proto=https"),
        (b"x-forwarded-for", b"203.0.113.2"), (b"X-Forwarded-Host", b"evil.internal"),
        (b"x-forwarded-proto", b"https"), (b"x-real-ip", b"203.0.113.3"),
    ]
    seen, sent = _request("https://app.internal", headers)
    assert sent[0]["status"] == 204
    assert seen[0]["client"] == ("192.0.2.10", 40000)
    assert seen[0]["scheme"] == "http"
    assert seen[0]["headers"] == [(b"host", b"app.internal"), (b"cookie", b"session=synthetic")]


def test_nondefault_port_and_ipv6_authorities_are_checked():
    assert _request("https://app.internal:8443", [(b"host", b"app.internal:8443")])[1][0]["status"] == 204
    assert _request("https://app.internal:8443", [(b"host", b"app.internal")])[1][0]["status"] == 400
    assert _request("http://[::1]:18766", [(b"host", b"[::1]:18766")])[1][0]["status"] == 204


def test_process_adapter_serves_synthetic_session_through_checked_host(tmp_path):
    root = Path(__file__).resolve().parents[2]
    fixture = (root / "fixtures/j01/producer-review-v6/session.json").read_bytes()
    session = Session.model_validate_json(fixture)
    info = session.session.model_copy(update={"expires_at": datetime.now(UTC) + timedelta(hours=1)})
    session = session.model_copy(update={"session": info})
    settings = RuntimeSettings.model_validate({
        "environment": "staging", "public_origin": "https://app.internal",
        "identity": {"issuer": "https://identity.internal/realm", "client_id": "synthetic",
                     "redirect_uri": "https://app.internal/callback", "client_kind": "public"},
        "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)},
    })

    class Backend:
        async def is_ready(self):
            return True

        async def load_session(self, cookie):
            assert cookie == b"session=synthetic"
            return session

        async def capabilities(self, session_value, workspace_id):
            raise AssertionError("capabilities are not part of this test")

    @asynccontextmanager
    async def resources():
        yield Backend()

    app = ProcessApplication(settings, resources)

    async def run():
        events = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
        response = []

        async def receive():
            return next(events)

        async def send_http(message):
            response.append(message)

        async def send_lifespan(message):
            if message["type"] == "lifespan.startup.complete":
                scope = {"type": "http", "method": "GET", "path": "/v1/session", "headers": [
                    (b"host", b"app.internal"), (b"cookie", b"session=synthetic"),
                    (b"x-forwarded-for", b"203.0.113.1"), (b"x-p6-bridge", b"public"),
                ]}
                await app(scope, receive, send_http)

        await app({"type": "lifespan"}, receive, send_lifespan)
        return response

    response = asyncio.run(run())
    assert response[0]["status"] == 200
    assert Session.model_validate_json(response[1]["body"]).actor.user_id == session.actor.user_id
