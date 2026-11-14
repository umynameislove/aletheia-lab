"""Real ASGI CSRF library with synthetic session authority; no server/browser."""

import asyncio
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie

import pytest
from pydantic import SecretStr, ValidationError

from aletheia_lab.hosted.application import AccessDenied
from aletheia_lab.hosted.read_backend import SessionRecord
from aletheia_lab.hosted.request_boundary import BodyLimits, RequestRejected, _collect_body
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted_auth.boundary import LOGIN_COOKIE, SESSION_COOKIE
from aletheia_lab.hosted_auth.csrf import (
    CSRF_COOKIE,
    CSRF_HEADER,
    CsrfBinding,
    CsrfMiddleware,
    CsrfTokens,
    SessionBindingLoader,
    clear_csrf_cookie,
    csrf_cookie,
)
from aletheia_lab.hosted_auth.settings import AuthConfigurationRejected, AuthPolicy
from aletheia_lab.hosted_contract.session_capabilities import Actor, Problem

NOW = datetime(2030, 1, 1, tzinfo=UTC)
ORIGIN = "https://app.example.invalid"
POLICY = AuthPolicy(default_return_to="/app", return_to_paths=("/app",))
SIGNING_SECRET = "synthetic-signing-secret-" + "s" * 43


def _runtime(tmp_path):
    return RuntimeSettings.model_validate({
        "environment": "staging", "public_origin": ORIGIN,
        "identity": {"issuer": "https://identity.example.invalid/realm", "client_id": "synthetic-client", "redirect_uri": ORIGIN + "/auth/callback", "client_kind": "public"},
        "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)},
    })


class SyntheticBindings:
    def __init__(self, token):
        self.binding = CsrfBinding(context_id="ctx_synthetic_a", csrf_token=token, expires_at=NOW + timedelta(seconds=900))
        self.calls = 0
        self.error = None

    async def __call__(self, cookie):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.binding


class EffectApplication:
    def __init__(self):
        self.calls = 0
        self.effects = 0
        self.headers = []
        self.fail_after_effect = False

    async def __call__(self, scope, receive, send):
        self.calls += 1
        self.headers = scope.get("headers", [])
        if scope.get("method") in ("POST", "PUT", "PATCH", "DELETE"):
            event = await receive()
            assert event["body"] == b'{"action":"synthetic-write"}'
            self.effects += 1
            if self.fail_after_effect:
                raise RuntimeError("synthetic-business-error")
        await send({"type": "http.response.start", "status": 204, "headers": [(b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": b""})


class BoundedEmptyDeleteApplication(EffectApplication):
    """Downstream parser contract stub; no claim about K-04 route integration."""

    async def __call__(self, scope, receive, send):
        self.calls += 1
        self.headers = scope.get("headers", [])
        try:
            body = await _collect_body(receive, self.headers, BodyLimits(8, 1, 2, 3))
            if body:
                raise RequestRejected(415, "unsupported_media_type", "An empty body is required.")
        except RequestRejected as error:
            await CsrfMiddleware._problem(send, error.status, error.code)
            return
        self.effects += 1
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})


def _system(tmp_path, *, app=None, **options):
    tokens = CsrfTokens(SecretStr(SIGNING_SECRET))
    token = tokens.issue("ctx_synthetic_a")
    bindings = SyntheticBindings(token)
    app = EffectApplication() if app is None else app
    guard = CsrfMiddleware(app, tokens, bindings, _runtime(tmp_path), lookup_timeout_seconds=1.0, clock=lambda: NOW, **options)
    return guard, tokens, bindings, app


def _headers(token):
    return [
        (b"host", b"app.example.invalid"), (b"origin", ORIGIN.encode()),
        (b"content-type", b"application/json"),
        (b"sec-fetch-site", b"same-origin"), (b"sec-fetch-mode", b"cors"), (b"sec-fetch-dest", b"empty"),
        (b"cookie", f"{SESSION_COOKIE}={'a' * 43}; {CSRF_COOKIE}={token.get_secret_value()}".encode()),
        (CSRF_HEADER, token.get_secret_value().encode()),
    ]


def _replace(headers, name, value):
    return [(key, item) for key, item in headers if key.lower() != name] + ([(name, value)] if value is not None else [])


async def _request(guard, headers, *, method="POST", path="/v1/session/renew", scheme="https", events=None):
    messages, reads = [], []
    events = [{"type": "http.request", "body": b'{"action":"synthetic-write"}', "more_body": False}] if events is None else events

    async def receive():
        reads.append(1)
        assert len(reads) <= len(events)
        return events[len(reads) - 1]

    async def send(event):
        messages.append(event)

    await guard({"type": "http", "method": method, "scheme": scheme, "path": path, "headers": headers}, receive, send)
    return messages, reads


def _denied(messages, reads, app, *, status=403, code="csrf_invalid"):
    assert messages[0]["status"] == status
    assert Problem.model_validate_json(messages[1]["body"]).code == code
    headers = dict(messages[0]["headers"])
    assert headers[b"cache-control"] == b"no-store"
    assert headers[b"content-type"] == b"application/problem+json"
    assert b"set-cookie" not in headers
    assert app.effects == app.calls == 0
    assert reads == []


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_library_allows_matching_current_proof_and_business_reader_gets_original_body(tmp_path, method):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        messages, reads = await _request(guard, _headers(bindings.binding.csrf_token), method=method)
        assert messages[0]["status"] == 204
        assert app.effects == bindings.calls == len(reads) == 1
        assert b"set-cookie" not in dict(messages[0]["headers"])
    asyncio.run(scenario())


@pytest.mark.parametrize("length", [None, b"0"])
def test_missing_media_delete_keeps_proof_and_original_stream_for_bounded_parser(tmp_path, length):
    async def scenario():
        app = BoundedEmptyDeleteApplication()
        guard, _, bindings, _ = _system(tmp_path, app=app)
        headers = _replace(_headers(bindings.binding.csrf_token), b"content-type", None)
        headers = _replace(headers, b"content-length", length)
        messages, reads = await _request(guard, headers, method="DELETE", events=[
            {"type": "http.request", "body": b"", "more_body": True},
            {"type": "http.request", "body": b"", "more_body": False},
        ])
        assert messages[0]["status"] == 204
        assert app.calls == app.effects == bindings.calls == 1 and len(reads) == 2
        assert b"content-type" not in dict(app.headers)
    asyncio.run(scenario())


@pytest.mark.parametrize("name,value", [
    (b"origin", None), (CSRF_HEADER, None), (CSRF_HEADER, b"forged"),
    (b"sec-fetch-site", b"cross-site"), (b"sec-fetch-mode", b"navigate"),
    (b"sec-fetch-dest", b"document"), (b"content-encoding", b"gzip"),
    (b"x-http-method-override", b"POST"),
])
def test_missing_media_delete_does_not_bypass_source_or_proof(tmp_path, name, value):
    async def scenario():
        app = BoundedEmptyDeleteApplication()
        guard, _, bindings, _ = _system(tmp_path, app=app)
        headers = _replace(_headers(bindings.binding.csrf_token), b"content-type", None)
        messages, reads = await _request(guard, _replace(headers, name, value), method="DELETE")
        _denied(messages, reads, app)
        assert bindings.calls == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("method,media", [
    ("POST", None), ("PUT", None), ("PATCH", None),
    ("DELETE", b""), ("DELETE", b"text/plain"),
])
def test_media_exception_is_only_missing_delete_content_type(tmp_path, method, media):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        headers = _replace(_headers(bindings.binding.csrf_token), b"content-type", media)
        messages, reads = await _request(guard, headers, method=method)
        _denied(messages, reads, app)
        assert bindings.calls == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("events,length,status,code", [
    ([{"type": "http.request", "body": b"{}"}], None, 415, "unsupported_media_type"),
    ([{"type": "http.request", "body": b"x"}], b"0", 400, "invalid_body_length"),
    ([{"type": "http.request", "body": "invalid"}], None, 400, "invalid_body_event"),
    ([{"type": "http.request", "body": b"xxxx", "more_body": True},
      {"type": "http.request", "body": b"yyyyy"}], None, 413, "body_too_large"),
    ([{"type": "http.request", "body": b"", "more_body": True}] * 3, None, 413, "body_event_limit"),
    ([{"type": "http.request", "body": b""}], b"9", 413, "body_too_large"),
])
def test_missing_media_delete_still_requires_bounded_empty_body_before_effect(tmp_path, events, length, status, code):
    async def scenario():
        app = BoundedEmptyDeleteApplication()
        guard, _, bindings, _ = _system(tmp_path, app=app)
        headers = _replace(_headers(bindings.binding.csrf_token), b"content-type", None)
        headers = _replace(headers, b"content-length", length)
        messages, _ = await _request(guard, headers, method="DELETE", events=events)
        assert messages[0]["status"] == status
        assert Problem.model_validate_json(messages[1]["body"]).code == code
        assert app.calls == bindings.calls == 1 and app.effects == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("second,value", [(b"if-match", b'"2"'), (b"If-Match", b'"2"'),
                                        (b"IF-MATCH", b'"1"')])
def test_duplicate_if_match_is_rejected_before_session_lookup_or_effect(tmp_path, second, value):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        headers = _headers(bindings.binding.csrf_token) + [(b"if-match", b'"1"'), (second, value)]
        messages, reads = await _request(guard, headers)
        _denied(messages, reads, app)
        assert bindings.calls == 0
    asyncio.run(scenario())


def test_single_if_match_value_is_preserved_without_normalization(tmp_path):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        value = b'W/"0007"'
        headers = _headers(bindings.binding.csrf_token) + [(b"If-Match", value)]
        messages, _ = await _request(guard, headers)
        assert messages[0]["status"] == 204
        assert dict(app.headers)[b"if-match"] == value
    asyncio.run(scenario())


@pytest.mark.parametrize("second,value", [(b"content-length", b"0"), (b"Content-Length", b"1")])
def test_duplicate_lengths_reach_shared_body_collector_and_reject_before_read_or_effect(tmp_path, second, value):
    async def scenario():
        app = BoundedEmptyDeleteApplication()
        guard, _, bindings, _ = _system(tmp_path, app=app)
        headers = _replace(_headers(bindings.binding.csrf_token), b"content-type", None)
        headers += [(b"content-length", b"0"), (second, value)]
        messages, reads = await _request(guard, headers, method="DELETE", events=[
            {"type": "http.request", "body": b"", "more_body": False},
        ])
        assert messages[0]["status"] == 400
        assert Problem.model_validate_json(messages[1]["body"]).code == "invalid_body_length"
        assert app.calls == bindings.calls == 1 and app.effects == 0 and reads == []
        assert [value for name, value in app.headers if name == b"content-length"] == [b"0", value]
    asyncio.run(scenario())


def test_duplicate_idempotency_keys_reach_real_job_parser_and_reject_before_effect(tmp_path):
    from aletheia_lab.hosted.job_http import JobApplication

    class Downstream(EffectApplication):
        async def __call__(self, scope, receive, send):
            self.calls += 1
            self.headers = scope["headers"]
            try:
                JobApplication._request_key(self.headers)
            except RequestRejected as error:
                await CsrfMiddleware._problem(send, error.status, error.code)
                return
            self.effects += 1
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

    async def scenario():
        app = Downstream()
        guard, _, bindings, _ = _system(tmp_path, app=app)
        values = [b"synthetic-first-key", b"synthetic-second-key"]
        headers = _headers(bindings.binding.csrf_token) + [(b"idempotency-key", values[0]), (b"Idempotency-Key", values[1])]
        messages, reads = await _request(guard, headers)
        assert messages[0]["status"] == 400
        assert Problem.model_validate_json(messages[1]["body"]).code == "invalid_request"
        assert [value for name, value in app.headers if name == b"idempotency-key"] == values
        assert app.calls == bindings.calls == 1 and app.effects == 0 and reads == []
    asyncio.run(scenario())


def test_validated_non_cookie_headers_keep_original_values_and_multiplicity(tmp_path):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        headers = _headers(bindings.binding.csrf_token) + [
            (b"X-Trace", b"  original-value  "), (b"x-trace", b"second,value"),
            (b"Idempotency-Key", b"single-synthetic-key"),
            (b"Authorization", b"Bearer synthetic-discarded"),
        ]
        messages, _ = await _request(guard, headers)
        assert messages[0]["status"] == 204
        assert [value for name, value in app.headers if name == b"x-trace"] == [b"  original-value  ", b"second,value"]
        assert dict(app.headers)[b"idempotency-key"] == b"single-synthetic-key"
        assert sum(name == b"cookie" for name, _ in app.headers) == 1
        assert not any(name == b"authorization" for name, _ in app.headers)
    asyncio.run(scenario())


def test_token_factory_is_context_bound_masked_and_compatible_across_instances():
    first = CsrfTokens(SecretStr(SIGNING_SECRET))
    second = CsrfTokens(SecretStr(SIGNING_SECRET))
    token = first.issue("ctx_synthetic_a")
    assert second.matches_context(token, "ctx_synthetic_a") is True
    assert second.matches_context(token, "ctx_synthetic_b") is False
    assert token.get_secret_value() not in repr(token)
    assert SIGNING_SECRET not in repr(first)
    wrong = CsrfTokens(SecretStr("other-synthetic-secret-" + "x" * 43))
    assert wrong.matches_context(token, "ctx_synthetic_a") is False
    assert first.issue("ctx_synthetic_a") != token


def test_secret_resolution_reads_only_declared_reference_and_has_no_fallback():
    with pytest.raises(AuthConfigurationRejected):
        CsrfTokens.from_environment(POLICY, {"UNRELATED_SECRET": SIGNING_SECRET})
    token_factory = CsrfTokens.from_environment(POLICY, {"ALETHEIA_HOSTED_CSRF_SECRET": SIGNING_SECRET})
    assert token_factory.matches_context(token_factory.issue("ctx_a"), "ctx_a") is True
    with pytest.raises(ValidationError):
        AuthPolicy(default_return_to="/app", return_to_paths=("/app",), csrf_secret_env="UNRELATED_SECRET")


@pytest.mark.parametrize("secret", ["", "short", " " * 64, "x" * 32 + "\n", "x" * 4097])
def test_invalid_signing_configuration_fails_closed(secret):
    with pytest.raises(AuthConfigurationRejected):
        CsrfTokens(SecretStr(secret))


def test_csrf_cookie_is_host_only_secure_httponly_lax_and_clears_same_scope():
    token = CsrfTokens(SecretStr(SIGNING_SECRET)).issue("ctx_a")
    cookies = SimpleCookie()
    cookies.load(csrf_cookie(token, max_age=900).decode())
    item = cookies[CSRF_COOKIE]
    assert item.value == token.get_secret_value()
    assert item["secure"] is True
    assert item["httponly"] is True
    assert item["samesite"] == "Lax"
    assert item["path"] == "/" and item["domain"] == ""
    assert item["max-age"] == "900"
    cookies.load(clear_csrf_cookie().decode())
    item = cookies[CSRF_COOKIE]
    assert item["max-age"] == "0" and item.value == ""
    assert item["secure"] is True and item["httponly"] is True
    assert item["path"] == "/" and item["domain"] == ""


@pytest.mark.parametrize("max_age", [True, 0, -1, 604801])
def test_csrf_cookie_lifetime_is_bounded(max_age):
    token = CsrfTokens(SecretStr(SIGNING_SECRET)).issue("ctx_a")
    with pytest.raises(AuthConfigurationRejected):
        csrf_cookie(token, max_age=max_age)


@pytest.mark.parametrize("origin", [
    None, b"null", b"https://evil.invalid", b"https://app.example.invalid.evil.invalid",
    b"https://app.example.invalid@evil.invalid", b"https://app.example.invalid:444",
    b"http://app.example.invalid", b"https://app.example.invalid/", b"https://app.example.invalid/path",
    b"https://app.example.invalid#fragment", b"https://app.example.invalid?query=x",
    b" https://app.example.invalid", b"https://app.example.invalid\r\nX: injected",
])
def test_origin_denials_do_not_read_body_lookup_binding_or_make_effect(tmp_path, origin):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        headers = _replace(_headers(bindings.binding.csrf_token), b"origin", origin)
        messages, reads = await _request(guard, headers)
        _denied(messages, reads, app)
        assert bindings.calls == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("name,value", [
    (b"sec-fetch-site", b"cross-site"), (b"sec-fetch-site", b"same-site"), (b"sec-fetch-site", b"none"),
    (b"sec-fetch-mode", b"no-cors"), (b"sec-fetch-mode", b"navigate"),
    (b"sec-fetch-dest", b"document"), (b"sec-fetch-dest", b"iframe"),
    (b"content-type", b"text/plain"), (b"content-type", b"application/x-www-form-urlencoded"),
    (b"content-type", b"multipart/form-data; boundary=synthetic"), (b"content-type", b"application/json; charset=iso-8859-1"),
    (b"content-type", b"application/json; charset=utf-8; charset=utf-8"),
    (b"content-encoding", b"gzip"),
])
def test_cross_site_metadata_and_unexpected_content_type_are_rejected_before_effect(tmp_path, name, value):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        messages, reads = await _request(guard, _replace(_headers(bindings.binding.csrf_token), name, value))
        _denied(messages, reads, app)
        assert bindings.calls == 0
    asyncio.run(scenario())


def test_missing_fetch_metadata_still_requires_strong_origin_and_proof(tmp_path):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        headers = [(name, value) for name, value in _headers(bindings.binding.csrf_token) if not name.startswith(b"sec-fetch-")]
        headers = _replace(headers, b"origin", b"https://APP.example.invalid:443")
        headers = _replace(headers, b"content-type", b'application/json; charset="UTF-8"')
        messages, reads = await _request(guard, headers)
        assert messages[0]["status"] == 204
        assert app.effects == len(reads) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("name", [b"origin", CSRF_HEADER, b"content-type", b"sec-fetch-site", b"host"])
def test_duplicate_security_headers_are_rejected_even_when_case_differs(tmp_path, name):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        headers = _headers(bindings.binding.csrf_token)
        value = dict(headers)[name]
        headers.append((name.upper(), value))
        messages, reads = await _request(guard, headers)
        _denied(messages, reads, app)
        assert bindings.calls == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["no_cookie", "no_header", "wrong_header", "old_header_name", "duplicate_csrf_cookie", "duplicate_session_cookie", "quoted_csrf_cookie"])
def test_missing_ambiguous_or_wrong_proof_never_reaches_effect(tmp_path, kind):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        token = bindings.binding.csrf_token.get_secret_value().encode()
        headers = _headers(bindings.binding.csrf_token)
        cookie = dict(headers)[b"cookie"]
        if kind == "no_cookie":
            headers = _replace(headers, b"cookie", None)
        elif kind == "no_header":
            headers = _replace(headers, CSRF_HEADER, None)
        elif kind == "wrong_header":
            headers = _replace(headers, CSRF_HEADER, b"wrong-proof")
        elif kind == "old_header_name":
            headers = _replace(headers, CSRF_HEADER, None) + [(b"x-csrftoken", token)]
        elif kind == "duplicate_csrf_cookie":
            headers = _replace(headers, b"cookie", cookie + b"; " + CSRF_COOKIE.encode() + b"=" + token)
        elif kind == "duplicate_session_cookie":
            headers = _replace(headers, b"cookie", cookie + b"; " + SESSION_COOKIE.encode() + b"=" + b"a" * 43)
        else:
            headers = _replace(headers, b"cookie", CSRF_COOKIE.encode() + b'="' + token + b'"')
        messages, reads = await _request(guard, headers)
        _denied(messages, reads, app)
        assert bindings.calls == 0
    asyncio.run(scenario())


def test_valid_other_context_proof_cannot_be_used_with_current_session(tmp_path):
    async def scenario():
        guard, tokens, bindings, app = _system(tmp_path)
        old = tokens.issue("ctx_synthetic_old")
        messages, reads = await _request(guard, _headers(old))
        _denied(messages, reads, app)
        assert bindings.calls == 1
    asyncio.run(scenario())


def test_context_check_rejects_even_corrupt_server_binding_that_reuses_old_signed_proof(tmp_path):
    async def scenario():
        guard, tokens, bindings, app = _system(tmp_path)
        old = tokens.issue("ctx_synthetic_old")
        bindings.binding = bindings.binding.model_copy(update={"csrf_token": old})
        messages, reads = await _request(guard, _headers(old))
        _denied(messages, reads, app)
    asyncio.run(scenario())


def test_invalid_signature_matching_header_cookie_and_server_is_rejected(tmp_path):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        wrong = CsrfTokens(SecretStr("wrong-synthetic-secret-" + "q" * 43)).issue("ctx_synthetic_a")
        bindings.binding = bindings.binding.model_copy(update={"csrf_token": wrong})
        messages, reads = await _request(guard, _headers(wrong))
        _denied(messages, reads, app)
    asyncio.run(scenario())


def test_bearer_header_does_not_exempt_missing_csrf_or_become_identity(tmp_path):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        headers = _headers(bindings.binding.csrf_token) + [(b"authorization", b"Bearer synthetic-forged"), (b"role", b"owner")]
        messages, reads = await _request(guard, _replace(headers, CSRF_HEADER, None))
        _denied(messages, reads, app)
        messages, _ = await _request(guard, headers)
        assert messages[0]["status"] == 204
        assert app.effects == 1
        assert b"authorization" not in dict(app.headers)
    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_method_override_is_not_a_safe_method_bypass(tmp_path, method):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        headers = _headers(bindings.binding.csrf_token) + [(b"X-HTTP-Method-Override", b"DELETE")]
        messages, reads = await _request(guard, headers, method=method)
        _denied(messages, reads, app)
    asyncio.run(scenario())


def test_forwarded_headers_cannot_turn_insecure_scope_into_secure_request(tmp_path):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        headers = _headers(bindings.binding.csrf_token) + [(b"x-forwarded-proto", b"https"), (b"forwarded", b"proto=https")]
        messages, reads = await _request(guard, headers, scheme="http")
        _denied(messages, reads, app)
    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_safe_methods_reach_router_without_generating_csrf_cookie(tmp_path, method):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        messages, reads = await _request(guard, [], method=method, path="/auth/callback")
        assert messages[0]["status"] == 204
        assert app.calls == 1 and app.effects == 0
        assert bindings.calls == len(reads) == 0
        assert b"set-cookie" not in dict(messages[0]["headers"])
    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["TRACE", "CONNECT", "PROPFIND", "pOsT"])
def test_unexpected_methods_cannot_reach_effect(tmp_path, method):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        messages, reads = await _request(guard, _headers(bindings.binding.csrf_token), method=method)
        _denied(messages, reads, app)
    asyncio.run(scenario())


@pytest.mark.parametrize("code", ["session_expired", "session_revoked"])
def test_current_auth_denial_is_preserved_without_csrf_or_business_effect(tmp_path, code):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        bindings.error = AccessDenied(code)
        messages, reads = await _request(guard, _headers(bindings.binding.csrf_token))
        _denied(messages, reads, app, status=401, code=code)
    asyncio.run(scenario())


def test_expired_binding_and_auth_outage_fail_closed_with_safe_problem(tmp_path):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        headers = _headers(bindings.binding.csrf_token)
        bindings.binding = bindings.binding.model_copy(update={"expires_at": NOW})
        messages, reads = await _request(guard, headers)
        _denied(messages, reads, app, status=401, code="session_expired")
        bindings.error = RuntimeError("synthetic-private-store-secret")
        messages, reads = await _request(guard, headers)
        _denied(messages, reads, app, status=503, code="service_unavailable")
        assert b"synthetic-private-store-secret" not in messages[1]["body"]
    asyncio.run(scenario())


def test_corrupt_server_binding_is_service_failure_without_body_or_effect(tmp_path):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        headers = _headers(bindings.binding.csrf_token)
        bindings.binding = bindings.binding.model_copy(update={"expires_at": NOW.replace(tzinfo=None)})
        messages, reads = await _request(guard, headers)
        _denied(messages, reads, app, status=500, code="service_failure")
    asyncio.run(scenario())


@pytest.mark.parametrize("state,enabled", [("expired", True), ("revoked", True), ("active", False)])
def test_session_loader_cannot_turn_inactive_server_record_into_valid_proof(state, enabled):
    async def scenario():
        class Sessions:
            async def resolve_cookie(self, cookie):
                return SessionRecord(
                    actor=Actor(user_id="usr_synthetic_a", display_name="Synthetic", email="synthetic@example.invalid", email_verified=True),
                    context_id="ctx_synthetic_a", expires_at=NOW + timedelta(seconds=900),
                    csrf_token=SecretStr("synthetic-proof"), onboarding="ready", last_workspace_id=None,
                    state=state, actor_enabled=enabled,
                )

        with pytest.raises(AccessDenied) as caught:
            await SessionBindingLoader(Sessions())(b"synthetic-cookie")
        assert caught.value.code == ("session_expired" if state == "expired" else "session_revoked")
    asyncio.run(scenario())


def test_large_cookie_or_headers_are_denied_before_lookup(tmp_path):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        headers = _headers(bindings.binding.csrf_token)
        messages, reads = await _request(guard, _replace(headers, b"cookie", b"x" * 4097))
        _denied(messages, reads, app)
        messages, reads = await _request(guard, headers + [(b"x-extra", b"x" * 16385)])
        _denied(messages, reads, app)
        assert bindings.calls == 0
    asyncio.run(scenario())


def test_multiple_cookie_fields_are_canonicalized_consistently(tmp_path):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        headers = _replace(_headers(bindings.binding.csrf_token), b"cookie", None)
        headers.extend([
            (b"cookie", f"{SESSION_COOKIE}={'a' * 43}".encode()),
            (b"Cookie", f"{CSRF_COOKIE}={bindings.binding.csrf_token.get_secret_value()}".encode()),
        ])
        messages, reads = await _request(guard, headers)
        assert messages[0]["status"] == 204
        assert sum(key == b"cookie" for key, _ in app.headers) == 1
        assert app.effects == len(reads) == 1
    asyncio.run(scenario())


def test_preauth_proof_requires_explicit_authoritative_binding_not_cookie_absence_exemption(tmp_path):
    async def scenario():
        guard, tokens, bindings, app = _system(tmp_path)
        token = tokens.issue("preauth_synthetic_a")
        bindings.binding = CsrfBinding(context_id="preauth_synthetic_a", csrf_token=token, expires_at=NOW + timedelta(seconds=300))
        headers = _replace(_headers(token), b"cookie", f"{LOGIN_COOKIE}={'p' * 43}; {CSRF_COOKIE}={token.get_secret_value()}".encode())
        messages, _ = await _request(guard, headers, path="/auth/start")
        assert messages[0]["status"] == 204
        assert app.effects == 1
        bindings.binding = None
        app.calls = app.effects = 0
        messages, reads = await _request(guard, headers, path="/auth/start")
        _denied(messages, reads, app, status=401, code="session_expired")
    asyncio.run(scenario())


def test_business_exception_is_not_relabelled_as_csrf_denial_after_effect(tmp_path):
    async def scenario():
        guard, _, bindings, app = _system(tmp_path)
        app.fail_after_effect = True
        with pytest.raises(RuntimeError, match="synthetic-business-error"):
            await _request(guard, _headers(bindings.binding.csrf_token))
        assert app.effects == 1
    asyncio.run(scenario())


def test_current_session_binding_loader_uses_server_record_only(tmp_path):
    async def scenario():
        token_factory = CsrfTokens(SecretStr(SIGNING_SECRET))
        token = token_factory.issue("ctx_synthetic_a")

        class Sessions:
            async def resolve_cookie(self, cookie):
                return SessionRecord(
                    actor=Actor(user_id="usr_synthetic_a", display_name="Synthetic", email="synthetic@example.invalid", email_verified=True),
                    context_id="ctx_synthetic_a", expires_at=NOW + timedelta(seconds=900),
                    csrf_token=token, onboarding="ready", last_workspace_id=None,
                    state="active", actor_enabled=True,
                )

        app = EffectApplication()
        loader = SessionBindingLoader(Sessions())
        guard = CsrfMiddleware(app, token_factory, loader, _runtime(tmp_path), lookup_timeout_seconds=1.0, clock=lambda: NOW)
        messages, reads = await _request(guard, _headers(token))
        assert messages[0]["status"] == 204
        assert app.effects == len(reads) == 1
        assert await loader(None) is None
    asyncio.run(scenario())


def test_binding_lookup_timeout_and_cancellation_have_no_effect(tmp_path):
    async def stalled(cookie):
        await asyncio.Event().wait()

    async def cancelled(cookie):
        raise asyncio.CancelledError

    async def scenario():
        _, tokens, bindings, app = _system(tmp_path)
        headers = _headers(bindings.binding.csrf_token)
        guard = CsrfMiddleware(app, tokens, stalled, _runtime(tmp_path), lookup_timeout_seconds=0.01, clock=lambda: NOW)
        messages, reads = await _request(guard, headers)
        _denied(messages, reads, app, status=503, code="service_unavailable")
        guard = CsrfMiddleware(app, tokens, cancelled, _runtime(tmp_path), lookup_timeout_seconds=1.0, clock=lambda: NOW)
        with pytest.raises(asyncio.CancelledError):
            await _request(guard, headers)
        assert app.effects == 0
    asyncio.run(scenario())


def test_websocket_does_not_bypass_http_mutation_policy(tmp_path):
    async def scenario():
        guard, _, _, app = _system(tmp_path)
        messages = []

        async def receive():
            raise AssertionError("websocket must not reach app")

        async def send(event):
            messages.append(event)

        await guard({"type": "websocket", "path": "/ws", "headers": []}, receive, send)
        assert messages == [{"type": "websocket.close", "code": 1008}]
        assert app.calls == app.effects == 0
    asyncio.run(scenario())
