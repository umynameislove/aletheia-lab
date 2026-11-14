"""Real CSRF/session adapters over a synthetic atomic authority; no services."""

import asyncio
from datetime import timedelta
from http.cookies import SimpleCookie

import pytest
from pydantic import SecretStr
from test_k03_csrf_boundary import SIGNING_SECRET, _runtime
from test_k03_session_adapter import (
    NOW,
    POLICY,
    TOKEN_A,
    TOKEN_B,
    TOKEN_C,
    SyntheticAtomicStore,
    _cookie,
    _facts,
)

from aletheia_lab.hosted.application import AccessDenied
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE
from aletheia_lab.hosted_auth.csrf import CSRF_COOKIE, CSRF_HEADER, CsrfTokens
from aletheia_lab.hosted_auth.session_http import (
    LOGOUT_ALL_PATH,
    RENEW_PATH,
    SessionMutationApplication,
)
from aletheia_lab.hosted_auth.sessions import ServerSessions
from aletheia_lab.hosted_contract.session_capabilities import Problem, Session


def _system(tmp_path, *, timeout=5.0, clock_check=None):
    store = SyntheticAtomicStore()
    tokens = CsrfTokens(SecretStr(SIGNING_SECRET))
    record = _facts().model_copy(update={"csrf_token": tokens.issue("ctx_synthetic_a")})
    store.seed(record)
    sessions = ServerSessions(store, POLICY, timeout_seconds=timeout, maximum_cookie_bytes=4096, clock=lambda: store.now)
    settings = _runtime(tmp_path)
    settings = settings.model_copy(update={"api": settings.api.model_copy(update={"backend_timeout_seconds": timeout})})

    async def ready():
        return True

    async def fallback(scope, receive, send):
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = SessionMutationApplication(fallback, sessions, tokens, settings,
        body_limits=BodyLimits(1024, 1.0, 1.0, 16), clock_check=clock_check or ready, clock=lambda: store.now)
    headers = [
        (b"origin", b"https://app.example.invalid"), (b"sec-fetch-site", b"same-origin"),
        (b"content-type", b"application/json"), (b"content-length", b"2"),
        (CSRF_HEADER, record.csrf_token.get_secret_value().encode()),
        (b"cookie", _cookie(TOKEN_A) + b"; " + CSRF_COOKIE.encode() + b"=" + record.csrf_token.get_secret_value().encode()),
    ]
    return app, sessions, store, tokens, headers


async def _request(app, headers, *, path=RENEW_PATH, method="POST", body=b"{}", send_hook=None):
    events, reads = [], []

    async def receive():
        reads.append(1)
        assert len(reads) == 1
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(event):
        events.append(event)
        if send_hook is not None:
            await send_hook(event)

    await app({"type": "http", "scheme": "https", "path": path, "method": method, "headers": headers,
               "client": ("192.0.2.1", 12345)}, receive, send)
    return events, reads


def _cookies(events):
    values = SimpleCookie()
    for name, value in events[0]["headers"]:
        if name == b"set-cookie":
            values.load(value.decode())
    return values


def _denied(events, *, status, code):
    assert events[0]["status"] == status
    assert Problem.model_validate_json(events[1]["body"]).code == code
    assert dict(events[0]["headers"])[b"cache-control"] == b"no-store"
    assert not _cookies(events)


def test_renew_uses_existing_session_dto_and_sets_only_current_secure_cookies(tmp_path):
    async def scenario():
        app, sessions, store, _, headers = _system(tmp_path)
        events, reads = await _request(app, headers)
        assert events[0]["status"] == 200 and reads == [1]
        value = Session.model_validate_json(events[1]["body"])
        assert value.session.context_id == "ctx_synthetic_a"
        assert value.actor.user_id == "usr_synthetic_a"
        assert value.session.expires_at == NOW + timedelta(seconds=900)
        cookies = _cookies(events)
        assert set(cookies) == {SESSION_COOKIE, CSRF_COOKIE}
        for cookie in cookies.values():
            assert cookie["secure"] and cookie["httponly"]
            assert cookie["samesite"] == "Lax" and cookie["path"] == "/" and not cookie["domain"]
            assert cookie["max-age"] == "900"
        assert cookies[CSRF_COOKIE].value == value.csrf_token
        current = await sessions.resolve_cookie(_cookie(cookies[SESSION_COOKIE].value))
        assert current.context_id == value.session.context_id and store.effects == 1
        with pytest.raises(AccessDenied):
            await sessions.resolve_cookie(_cookie(TOKEN_A))
    asyncio.run(scenario())


def test_early_renew_returns_unchanged_expiry_without_any_cookie(tmp_path):
    async def scenario():
        app, _, store, _, headers = _system(tmp_path)
        for digest, record in list(store.records.items()):
            store.records[digest] = record.model_copy(update={"rotated_at": NOW, "last_seen_at": NOW})
        events, _ = await _request(app, headers)
        assert events[0]["status"] == 200 and not _cookies(events)
        assert Session.model_validate_json(events[1]["body"]).session.expires_at == NOW + timedelta(seconds=300)
        assert store.effects == store.rotate_requests == 0
    asyncio.run(scenario())


def test_logout_all_uses_current_actor_and_leaves_another_account_valid(tmp_path):
    async def scenario():
        app, sessions, store, tokens, headers = _system(tmp_path)
        store.seed(_facts(TOKEN_B, "usr_synthetic_b", "ctx_b").model_copy(update={"csrf_token": tokens.issue("ctx_b")}))
        store.seed(_facts(TOKEN_C, "usr_synthetic_a", "ctx_c").model_copy(update={"csrf_token": tokens.issue("ctx_c")}))
        events, _ = await _request(app, headers, path=LOGOUT_ALL_PATH)
        assert events[0]["status"] == 204 and events[1]["body"] == b""
        assert all(cookie["max-age"] == "0" for cookie in _cookies(events).values())
        for token in (TOKEN_A, TOKEN_C):
            with pytest.raises(AccessDenied):
                await sessions.resolve_cookie(_cookie(token))
        assert (await sessions.resolve_cookie(_cookie(TOKEN_B))).actor.user_id == "usr_synthetic_b"
    asyncio.run(scenario())


@pytest.mark.parametrize("path", [RENEW_PATH, LOGOUT_ALL_PATH])
@pytest.mark.parametrize("kind", ["cross_site", "missing_header", "form", "duplicate_cookie"])
def test_csrf_denial_never_reads_body_or_mutates_session(tmp_path, path, kind):
    async def scenario():
        app, _, store, _, headers = _system(tmp_path)
        if kind == "cross_site":
            headers = [(k, b"https://evil.invalid" if k == b"origin" else v) for k, v in headers]
        elif kind == "missing_header":
            headers = [(k, v) for k, v in headers if k != CSRF_HEADER]
        elif kind == "form":
            headers = [(k, b"application/x-www-form-urlencoded" if k == b"content-type" else v) for k, v in headers]
        else:
            headers = [(k, v + b"; " + _cookie(TOKEN_A) if k == b"cookie" else v) for k, v in headers]
        events, reads = await _request(app, headers, path=path)
        _denied(events, status=403, code="csrf_invalid")
        assert reads == [] and store.effects == store.rotate_requests == 0
    asyncio.run(scenario())


def test_logout_all_rejects_client_declared_actor_before_effect(tmp_path):
    async def scenario():
        app, _, store, _, headers = _system(tmp_path)
        body = b'{"user_id":"usr_synthetic_b"}'
        headers = [(k, str(len(body)).encode() if k == b"content-length" else v) for k, v in headers]
        events, _ = await _request(app, headers, path=LOGOUT_ALL_PATH, body=body)
        assert events[0]["status"] in (400, 422) and not _cookies(events)
        assert store.effects == 0
    asyncio.run(scenario())


def test_concurrent_http_renew_has_one_winner_and_loser_never_sets_cookie(tmp_path):
    async def scenario():
        app, _, store, _, headers = _system(tmp_path)
        store.allow_rotate.clear()
        tasks = [asyncio.create_task(_request(app, headers)) for _ in range(2)]
        await store.two_rotates_entered.wait()
        store.allow_rotate.set()
        results = await asyncio.gather(*tasks)
        assert sorted(events[0]["status"] for events, _ in results) == [200, 409]
        for events, _ in results:
            if events[0]["status"] == 409:
                _denied(events, status=409, code="renewal_conflict")
        assert store.effects == 1
    asyncio.run(scenario())


def test_logout_all_fences_already_pending_http_renew(tmp_path):
    async def scenario():
        app, sessions, store, _, headers = _system(tmp_path)
        store.allow_rotate.clear()
        pending = asyncio.create_task(_request(app, headers))
        await store.rotate_entered.wait()
        events, _ = await _request(app, headers, path=LOGOUT_ALL_PATH)
        assert events[0]["status"] == 204
        store.allow_rotate.set()
        late, _ = await pending
        _denied(late, status=409, code="renewal_conflict")
        with pytest.raises(AccessDenied):
            await sessions.resolve_cookie(_cookie(TOKEN_A))
        assert all(record.state != "active" or record.session_epoch != store.epochs[record.actor.user_id]
                   for record in store.records.values())
    asyncio.run(scenario())


def test_clock_failures_publish_no_success_or_cookie_even_after_rotation(tmp_path):
    async def scenario():
        checks = iter((True, False))

        async def clock_check():
            return next(checks)

        app, _, store, _, headers = _system(tmp_path, clock_check=clock_check)
        events, _ = await _request(app, headers)
        _denied(events, status=503, code="service_unavailable")
        assert store.effects == 1
    asyncio.run(scenario())


def test_total_request_timeout_cancels_waiting_renew_and_emits_no_credential(tmp_path):
    async def scenario():
        app, _, store, _, headers = _system(tmp_path, timeout=0.02)
        store.allow_rotate.clear()
        events, _ = await _request(app, headers)
        _denied(events, status=503, code="service_unavailable")
        assert store.effects == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_non_post_methods_do_not_mutate_and_head_has_no_body(tmp_path, method):
    async def scenario():
        app, _, store, _, headers = _system(tmp_path)
        events, reads = await _request(app, headers, method=method)
        assert events[0]["status"] == 405 and dict(events[0]["headers"])[b"allow"] == b"POST"
        assert not _cookies(events) and reads == [] and store.effects == 0
        if method == "HEAD":
            assert events[1]["body"] == b""
    asyncio.run(scenario())


def test_unrelated_route_passes_through(tmp_path):
    async def scenario():
        app, _, store, _, headers = _system(tmp_path)
        events, _ = await _request(app, headers, path="/v1/unrelated")
        assert events[0]["status"] == 404 and store.inspect_calls == []
    asyncio.run(scenario())


def test_response_timeout_after_headers_never_sends_a_second_response(tmp_path):
    async def scenario():
        app, _, _, _, headers = _system(tmp_path, timeout=0.02)
        starts = []

        async def stalled_send(event):
            if event["type"] == "http.response.start":
                starts.append(event)
            else:
                await asyncio.Event().wait()

        with pytest.raises(TimeoutError):
            await _request(app, headers, send_hook=stalled_send)
        assert len(starts) == 1
    asyncio.run(scenario())
