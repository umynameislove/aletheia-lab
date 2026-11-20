"""Callback ASGI behavior with a synthetic coordinator; not real login proof."""

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted_auth.callback_http import CallbackApplication
from aletheia_lab.hosted_auth.identity import IdentityRejected
from aletheia_lab.hosted_auth.login import LoginCompletion, LoginRejected
from aletheia_lab.hosted_auth.oidc import OidcRejected, OidcUnavailable
from aletheia_lab.hosted_auth.repositories import SessionSnapshot
from aletheia_lab.hosted_auth.sessions import credential_digest
from aletheia_lab.hosted_auth.settings import AuthPolicy
from aletheia_lab.hosted_contract.session_capabilities import Actor


class Coordinator:
    def __init__(self, *, denied=False, wrong_digest=False, error=None, return_to="/app"):
        self.calls = []
        self.denied, self.wrong_digest = denied, wrong_digest
        self.error, self.return_to = error, return_to

    async def complete(self, cookie, query):
        self.calls.append((cookie, query))
        if self.error is not None:
            raise self.error
        if self.denied:
            raise LoginRejected()
        now = datetime.now(UTC) - timedelta(seconds=1)
        token = SecretStr("x" * 43)
        snapshot = SessionSnapshot(
            credential_digest=credential_digest(SecretStr("z" * 43) if self.wrong_digest else token),
            actor=Actor(user_id="user1", display_name="Synthetic", email="u@example.invalid", email_verified=True),
            context_id="ctx_1", csrf_token=SecretStr("synthetic-csrf"), onboarding="needs_workspace",
            last_workspace_id=None, state="active", actor_enabled=True, issued_at=now,
            last_seen_at=now, rotated_at=now, idle_expires_at=now + timedelta(minutes=5),
            absolute_expires_at=now + timedelta(hours=1), generation=0, session_epoch=1, account_epoch=1,
        )
        return LoginCompletion(snapshot, token, self.return_to)


def invoke(*, method="GET", scheme="https", denied=False, wrong_digest=False, query=b"state=synthetic", clock_check=None, error=None, return_to="/app", cookie=b"a=b"):
    coordinator = Coordinator(denied=denied, wrong_digest=wrong_digest, error=error, return_to=return_to)

    async def fallback(scope, receive, send):
        raise AssertionError("callback must not reach fallback")

    app = CallbackApplication(fallback, coordinator, AuthPolicy(default_return_to="/app", return_to_paths=("/app",)),
                              maximum_cookie_bytes=2048, timeout_seconds=1, clock_check=clock_check)
    messages = []

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b""}

    asyncio.run(app({"type": "http", "path": "/v1/auth/callback", "method": method,
                     "scheme": scheme, "query_string": query, "headers": [(b"cookie", cookie)]}, receive, send))
    return coordinator, messages


def _failed(messages, reason):
    assert len(messages) == 2 and messages[0]["status"] == 303
    headers = messages[0]["headers"]
    assert (b"location", ("/#/signed-out?reason=" + reason).encode("ascii")) in headers
    assert (b"cache-control", b"no-store") in headers
    assert (b"referrer-policy", b"no-referrer") in headers
    assert (b"x-content-type-options", b"nosniff") in headers
    assert (b"content-length", b"0") in headers
    assert not any(name == b"set-cookie" for name, _ in headers)
    assert messages[1]["body"] == b""


def test_success_sets_session_and_csrf_without_changing_login_binding():
    coordinator, messages = invoke()
    assert len(coordinator.calls) == 1 and messages[0]["status"] == 303
    headers = messages[0]["headers"]
    assert (b"location", b"/app") in headers and (b"cache-control", b"no-store") in headers
    cookies = [value for name, value in headers if name == b"set-cookie"]
    assert len(cookies) == 2 and all(b"Secure" in cookie and b"HttpOnly" in cookie for cookie in cookies)
    assert any(b"__Host-aletheia_session=" in value for value in cookies)
    assert any(b"__Host-aletheia_csrf=" in value for value in cookies)
    assert not any(b"__Host-aletheia_login=" in value for value in cookies)


def test_clock_failure_prevents_login_or_cookie_publication():
    async def bad_clock():
        return False
    coordinator, messages = invoke(clock_check=bad_clock)
    _failed(messages, "idp_unavailable")
    assert not coordinator.calls
    assert not any(name == b"set-cookie" for name, _ in messages[0]["headers"])
    values = iter([True, False])
    async def changing_clock():
        return next(values)
    coordinator, messages = invoke(clock_check=changing_clock)
    _failed(messages, "idp_unavailable")
    assert len(coordinator.calls) == 1
    assert not any(name == b"set-cookie" for name, _ in messages[0]["headers"])


def test_bad_requests_and_denials_never_emit_credentials_or_query():
    for options in ({"method": "HEAD"}, {"scheme": "http"}, {"denied": True}, {"wrong_digest": True}, {"query": b""}, {"query": b"x" * 8193}, {"cookie": b"x" * 2049}):
        coordinator, messages = invoke(**options)
        if "method" in options:
            assert messages[0]["status"] == 405
            assert (b"allow", b"GET") in messages[0]["headers"]
            assert not any(name == b"location" for name, _ in messages[0]["headers"])
        elif "scheme" in options:
            assert messages[0]["status"] == 400
            assert not any(name == b"location" for name, _ in messages[0]["headers"])
        else:
            _failed(messages, "login_failed")
        assert not any(name == b"set-cookie" for name, _ in messages[0]["headers"])
        assert b"synthetic" not in messages[1]["body"]
        if not options.get("denied") and not options.get("wrong_digest"):
            assert not coordinator.calls


def test_callback_with_coordinator_and_synthetic_store_rejects_replay(tmp_path):
    # These deliberately synthetic K-03 fixtures are not a PostgreSQL or IdP test.
    from test_k03_login_coordinator import POLICY, _cookie, _query, _system

    async def scenario():
        flow, store, oidc = _system(tmp_path)
        store.now = datetime.now(UTC) - timedelta(seconds=1)
        start = await flow.begin(None, return_to="/app")

        async def fallback(scope, receive, send):
            raise AssertionError("unexpected fallback")

        async def receive():
            return {"type": "http.request", "body": b""}

        app = CallbackApplication(fallback, flow, POLICY, maximum_cookie_bytes=4096, timeout_seconds=5)
        scope = {"type": "http", "path": "/v1/auth/callback", "method": "GET", "scheme": "https",
                 "query_string": _query(start), "headers": [(b"cookie", _cookie(start))]}
        messages = []

        async def send(message):
            messages.append(message)

        await app(scope, receive, send)
        assert messages[0]["status"] == 303
        assert store.claim_effects == store.commit_effects == oidc.calls == 1
        messages.clear()
        await app(scope, receive, send)
        _failed(messages, "login_failed")
        assert not any(name == b"set-cookie" for name, _ in messages[0]["headers"])
        assert store.claim_effects == store.commit_effects == oidc.calls == 1

    asyncio.run(scenario())


def test_idp_outage_redirect_keeps_the_callback_claim_spent(tmp_path):
    from test_k03_login_coordinator import POLICY, _cookie, _query, _system

    async def scenario():
        flow, store, oidc = _system(tmp_path)
        store.now = datetime.now(UTC) - timedelta(seconds=1)
        start = await flow.begin(None, return_to="/app")
        oidc.error = OidcUnavailable()

        async def fallback(scope, receive, send):
            raise AssertionError("unexpected fallback")

        async def receive():
            return {"type": "http.request", "body": b""}

        app = CallbackApplication(fallback, flow, POLICY, maximum_cookie_bytes=4096, timeout_seconds=5)
        scope = {"type": "http", "path": "/v1/auth/callback", "method": "GET", "scheme": "https",
                 "query_string": _query(start), "headers": [(b"cookie", _cookie(start))]}
        messages = []

        async def send(message):
            messages.append(message)

        await app(scope, receive, send)
        _failed(messages, "idp_unavailable")
        assert store.claim_effects == oidc.calls == 1 and store.commit_effects == 0
        assert not any(name == b"set-cookie" for name, _ in messages[0]["headers"])
        messages.clear()
        oidc.error = None
        await app(scope, receive, send)
        _failed(messages, "login_failed")
        assert store.claim_effects == oidc.calls == 1 and store.commit_effects == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("error,reason", [
    (LoginRejected(), "login_failed"),
    (LoginRejected("provider_cancelled"), "login_cancelled"),
    (OidcRejected(), "login_failed"),
    (IdentityRejected("disabled"), "account_disabled"),
    (IdentityRejected("unprovisioned"), "login_failed"),
    (IdentityRejected("email_unverified"), "login_failed"),
    (IdentityRejected("issuer_mismatch"), "login_failed"),
    (OidcUnavailable(), "idp_unavailable"),
    (TimeoutError("private-provider-detail"), "idp_unavailable"),
    (RuntimeError("private-provider-detail"), "login_failed"),
])
def test_only_coordinator_outcomes_select_fixed_failure_destinations(error, reason, caplog):
    query = (b"state=synthetic&error=access_denied&error_description=private-provider-detail"
             b"&error_uri=https%3A%2F%2Funtrusted.example%2Frecovery&return_to=%2Faccount")
    coordinator, messages = invoke(error=error, query=query)
    assert coordinator.calls == [(b"a=b", query)]
    _failed(messages, reason)
    for private in ("private-provider-detail", "untrusted.example", "/account", "state=synthetic"):
        assert private not in repr(messages) + caplog.text


@pytest.mark.parametrize("return_to", [
    "https://untrusted.example/recovery", "//untrusted.example/recovery",
    "/app\r\nlocation: https://untrusted.example", "/#/signed-out?reason=account_disabled",
])
def test_invalid_completion_destination_cannot_publish_cookies_or_redirect_elsewhere(return_to):
    coordinator, messages = invoke(return_to=return_to)
    assert len(coordinator.calls) == 1
    _failed(messages, "login_failed")
    assert "untrusted.example" not in repr(messages)


@pytest.mark.parametrize("provider_error,reason", [
    ("access_denied", "login_cancelled"),
    ("server_error", "idp_unavailable"),
    ("temporarily_unavailable", "idp_unavailable"),
    ("private-provider-error", "login_failed"),
])
def test_provider_failure_is_presented_only_after_claim_and_replay_is_generic(tmp_path, provider_error, reason, caplog):
    from test_k03_login_coordinator import ISSUER, POLICY, _cookie, _system

    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None)
        state = parse_qs(urlsplit(start.authorization_url.get_secret_value()).query)["state"][0]
        values = {"state": state, "iss": ISSUER, "error": provider_error,
                  "error_description": "private-provider-detail",
                  "error_uri": "https://untrusted.example/recovery"}

        async def fallback(scope, receive, send):
            raise AssertionError("unexpected fallback")

        async def receive():
            raise AssertionError("callback must not consume a body")

        messages = []

        async def send(message):
            messages.append(message)

        app = CallbackApplication(fallback, flow, POLICY, maximum_cookie_bytes=4096, timeout_seconds=5)
        scope = {"type": "http", "path": "/v1/auth/callback", "method": "GET", "scheme": "https",
                 "headers": [(b"cookie", _cookie(start))]}
        await app({**scope, "query_string": urlencode({**values, "state": "z" * 43}).encode()}, receive, send)
        _failed(messages, "login_failed")
        assert store.claim_effects == oidc.calls == store.lookup_calls == store.commit_calls == 0
        messages.clear()
        scope["query_string"] = urlencode(values).encode()
        await app(scope, receive, send)
        _failed(messages, reason)
        assert store.claim_effects == 1 and oidc.calls == store.lookup_calls == 0
        assert store.commit_calls == store.commit_effects == len(store.sessions) == 0
        assert "private-provider" not in repr(messages) + caplog.text
        assert "untrusted.example" not in repr(messages) + caplog.text
        messages.clear()
        await app(scope, receive, send)
        _failed(messages, "login_failed")
        assert store.claim_effects == 1 and oidc.calls == store.commit_calls == 0

    asyncio.run(scenario())


def test_callback_deadline_redirects_without_credentials():
    async def scenario():
        class StalledCoordinator:
            async def complete(self, cookie, query):
                await asyncio.Event().wait()

        async def fallback(scope, receive, send):
            raise AssertionError("unexpected fallback")

        async def receive():
            raise AssertionError("callback must not consume a body")

        messages = []

        async def send(message):
            messages.append(message)

        app = CallbackApplication(fallback, StalledCoordinator(), AuthPolicy(default_return_to="/app", return_to_paths=("/app",)),
                                  maximum_cookie_bytes=2048, timeout_seconds=0.01)
        await app({"type": "http", "path": "/v1/auth/callback", "method": "GET", "scheme": "https",
                   "query_string": b"state=synthetic", "headers": []}, receive, send)
        _failed(messages, "idp_unavailable")

    asyncio.run(scenario())


def test_callback_cancellation_propagates_without_sending_response():
    async def scenario():
        coordinator = Coordinator(error=asyncio.CancelledError())

        async def fallback(scope, receive, send):
            raise AssertionError("unexpected fallback")

        async def receive():
            raise AssertionError("callback must not consume a body")

        messages = []

        async def send(message):
            messages.append(message)

        app = CallbackApplication(fallback, coordinator, AuthPolicy(default_return_to="/app", return_to_paths=("/app",)),
                                  maximum_cookie_bytes=2048, timeout_seconds=1)
        with pytest.raises(asyncio.CancelledError):
            await app({"type": "http", "path": "/v1/auth/callback", "method": "GET", "scheme": "https",
                       "query_string": b"state=synthetic", "headers": []}, receive, send)
        assert len(coordinator.calls) == 1 and messages == []

    asyncio.run(scenario())


@pytest.mark.parametrize("scope", [
    {"type": "http", "path": "/another-route"},
    {"type": "websocket", "path": "/v1/auth/callback"},
])
def test_unrelated_scope_passes_unchanged_to_fallback(scope):
    coordinator = Coordinator(denied=True)
    seen, messages = [], []

    async def fallback(received, receive, send):
        assert received is scope
        seen.append(received)
        await send({"type": "synthetic.fallback"})

    async def receive():
        raise AssertionError("fallback did not need receive")

    async def send(message):
        messages.append(message)

    app = CallbackApplication(fallback, coordinator, AuthPolicy(default_return_to="/app", return_to_paths=("/app",)), maximum_cookie_bytes=2048, timeout_seconds=1)
    asyncio.run(app(scope, receive, send))
    assert seen == [scope] and messages == [{"type": "synthetic.fallback"}] and not coordinator.calls
