"""Input/cookie policy only; passing these tests does not qualify OIDC login."""

from http.cookies import SimpleCookie
from urllib.parse import urlencode

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted_auth.boundary import (
    LOGIN_COOKIE,
    SESSION_COOKIE,
    AuthInputRejected,
    allowed_return_to,
    clear_opaque_cookie,
    new_opaque_secret,
    parse_callback_query,
    read_opaque_cookie,
    set_opaque_cookie,
)
from aletheia_lab.hosted_auth.settings import AuthPolicy

ISSUER = "https://identity.example.invalid/realm"
STATE = "s" * 43
CODE = "synthetic-sensitive-code"
POLICY = AuthPolicy(default_return_to="/app", return_to_paths=("/app", "/account"))


def _query(**changes):
    return urlencode({"state": STATE, "iss": ISSUER, "code": CODE, **changes}).encode("ascii")


def test_return_to_requires_exact_allowed_path_and_explicit_default():
    assert allowed_return_to(None, POLICY) == "/app"
    assert allowed_return_to("/account", POLICY) == "/account"


@pytest.mark.parametrize("value", [
    "", "https://evil.invalid", "//evil.invalid", "/\\evil.invalid",
    "/app/../account", "/app/.", "/app//account", "/app/private", "/application",
    "/app?next=https://evil.invalid", "/app#https://evil.invalid", "/app%2fprivate",
    "/%2f%2fevil.invalid", "/%252f%252fevil.invalid", "/app\r\nLocation:evil",
    " /app", "/app ", "/app\x00", "/app/",
])
def test_open_redirect_and_prefix_bypasses_are_rejected(value):
    with pytest.raises(AuthInputRejected):
        allowed_return_to(value, POLICY)


def test_callback_shape_accepts_code_without_mistaking_it_for_verified_identity():
    result = parse_callback_query(_query(), expected_issuer=ISSUER, policy=POLICY)
    assert result.state.get_secret_value() == STATE
    assert result.code.get_secret_value() == CODE
    assert result.provider_denied is False
    assert STATE not in repr(result)
    assert CODE not in repr(result)


@pytest.mark.parametrize("query", [
    b"",
    b"state=short&code=c",
    _query(state=""),
    _query(code=""),
    _query(code="white space"),
    _query(code="\u00e9"),
    _query(code="x" * 2049),
    _query(iss="https://evil.invalid"),
    _query() + b"&state=" + STATE.encode(),
    _query() + b"&iss=" + ISSUER.encode(),
    _query() + b"&error=access_denied",
    _query() + b"&error=",
    _query() + b"&error_description=sensitive-description",
    _query() + b"&error_uri=https://evil.invalid",
    _query() + b"&role=owner",
    _query() + b"&code=%GG",
    _query() + b"&session_state=%FF",
    _query() + b"&session_state=%00",
    _query() + b"&session_state=%0D%0A",
    _query() + b"&session_state=x&session_state=y",
    _query() + b"&missing_equals",
    _query() + b"\xff",
    b"x" * 8193,
    urlencode({"state": STATE, "code": CODE}).encode(),
    urlencode({"state": STATE, "iss": ISSUER}).encode(),
])
def test_invalid_callback_input_never_produces_parsed_success(query):
    with pytest.raises(AuthInputRejected) as caught:
        parse_callback_query(query, expected_issuer=ISSUER, policy=POLICY)
    assert CODE not in repr(caught.value)
    assert STATE not in repr(caught.value)
    assert str(caught.value) == "Authentication request could not be verified."


def test_provider_error_retains_no_untrusted_description_or_recovery_url():
    query = urlencode({
        "state": STATE, "iss": ISSUER, "error": "access_denied",
        "error_description": "synthetic-provider-private-detail",
        "error_uri": "https://evil.invalid/unsafe-recovery",
    }).encode()
    result = parse_callback_query(query, expected_issuer=ISSUER, policy=POLICY)
    assert result.provider_denied is True
    assert result.code is None
    assert "synthetic-provider-private-detail" not in repr(result)
    assert "evil.invalid" not in repr(result)


def test_optional_response_issuer_requires_explicit_policy_and_still_checks_if_present():
    policy = POLICY.model_copy(update={"require_response_issuer": False})
    query = urlencode({"state": STATE, "code": CODE}).encode()
    assert parse_callback_query(query, expected_issuer=ISSUER, policy=policy).code is not None
    with pytest.raises(AuthInputRejected):
        parse_callback_query(_query(iss="https://evil.invalid"), expected_issuer=ISSUER, policy=policy)


@pytest.mark.parametrize("name", [SESSION_COOKIE, LOGIN_COOKIE])
def test_generated_cookie_is_opaque_host_only_secure_httponly_lax(name):
    value = new_opaque_secret()
    assert len(value.get_secret_value()) == 43
    header = set_opaque_cookie(name=name, value=value, max_age=300)
    cookies = SimpleCookie()
    cookies.load(header.decode())
    item = cookies[name]
    assert item.value == value.get_secret_value()
    assert item["secure"] is True
    assert item["httponly"] is True
    assert item["samesite"] == "Lax"
    assert item["path"] == "/"
    assert item["domain"] == ""
    assert item["max-age"] == "300"
    assert value.get_secret_value() not in repr(value)
    request_cookie = f"other=public; {name}={item.value}".encode()
    assert read_opaque_cookie(request_cookie, name=name, maximum_bytes=4096) == value


@pytest.mark.parametrize("name", [SESSION_COOKIE, LOGIN_COOKIE])
def test_logout_cookie_clears_the_exact_same_cookie_scope(name):
    cookies = SimpleCookie()
    cookies.load(clear_opaque_cookie(name=name).decode())
    item = cookies[name]
    assert item.value == ""
    assert item["max-age"] == "0"
    assert item["expires"] == "Thu, 01 Jan 1970 00:00:00 GMT"
    assert item["path"] == "/"
    assert item["domain"] == ""
    assert item["secure"] is True
    assert item["httponly"] is True
    assert item["samesite"] == "Lax"


@pytest.mark.parametrize("cookie", [
    (f"{SESSION_COOKIE}={STATE}; {SESSION_COOKIE}={STATE}").encode(),
    (f"{SESSION_COOKIE}=short").encode(),
    (f'{SESSION_COOKIE}="{STATE}"').encode(),
    (f"{SESSION_COOKIE} ={STATE}").encode(),
    (f"{SESSION_COOKIE}={STATE}\r\nX-Test: injected").encode(),
    (f"{SESSION_COOKIE}={STATE}, {SESSION_COOKIE}={STATE}").encode(),
    SESSION_COOKIE.encode(),
    b"\xff",
    b"a" * 4097,
])
def test_ambiguous_or_malformed_protected_cookie_is_rejected(cookie):
    with pytest.raises(AuthInputRejected):
        read_opaque_cookie(cookie, name=SESSION_COOKIE, maximum_bytes=4096)


def test_unknown_or_absent_cookie_never_supplies_identity():
    for cookie in (None, b"", b"role=owner; user_id=attacker", b"X-P6-Bridge=public"):
        assert read_opaque_cookie(cookie, name=SESSION_COOKIE, maximum_bytes=4096) is None


@pytest.mark.parametrize("max_age", [0, -1, True, 604801])
def test_cookie_writer_rejects_unbounded_or_invalid_lifetime(max_age):
    with pytest.raises(ValueError):
        set_opaque_cookie(name=SESSION_COOKIE, value=SecretStr(STATE), max_age=max_age)


def test_cookie_writer_cannot_inject_headers_or_change_cookie_name():
    with pytest.raises(ValueError):
        set_opaque_cookie(name=SESSION_COOKIE, value=SecretStr("x\r\nInjected: true"), max_age=300)
    with pytest.raises(ValueError):
        set_opaque_cookie(name="session", value=SecretStr(STATE), max_age=300)
    with pytest.raises(ValueError):
        clear_opaque_cookie(name="session")
