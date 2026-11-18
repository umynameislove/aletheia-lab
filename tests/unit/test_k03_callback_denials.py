"""Callback error classes over synthetic authority; no real IdP or database."""

import asyncio
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from pydantic import SecretStr
from test_k03_login_coordinator import (
    ISSUER,
    POLICY,
    SyntheticLoginAuthority,
    SyntheticOidc,
    _cookie,
    _flow,
    _query,
    _system,
)

from aletheia_lab.hosted_auth.abuse import BoundedOidc
from aletheia_lab.hosted_auth.boundary import CallbackInput
from aletheia_lab.hosted_auth.callback_http import CallbackApplication
from aletheia_lab.hosted_auth.login import LoginRejected
from aletheia_lab.hosted_auth.oidc import OidcRejected, OidcUnavailable
from aletheia_lab.hosted_auth.repositories import RepositoryDataRejected, RepositoryUnavailable


async def _invoke(flow, start, *, query=None, clock_check=None, timeout_seconds=5,
                  method="GET", scheme="https"):
    messages = []

    async def fallback(scope, receive, send):
        raise AssertionError("callback must not reach another route")

    async def receive():
        raise AssertionError("callback must not consume a body")

    async def send(message):
        messages.append(message)

    app = CallbackApplication(fallback, flow, POLICY, maximum_cookie_bytes=4096,
                              timeout_seconds=timeout_seconds, clock_check=clock_check)
    await app({"type": "http", "path": "/v1/auth/callback", "method": method, "scheme": scheme,
               "query_string": _query(start) if query is None else query,
               "headers": [(b"cookie", _cookie(start))]}, receive, send)
    return messages


def _denied(messages, reason):
    assert len(messages) == 2 and messages[0]["status"] == 303
    headers = messages[0]["headers"]
    assert (b"location", ("/#/signed-out?reason=" + reason).encode("ascii")) in headers
    assert (b"cache-control", b"no-store") in headers
    assert (b"referrer-policy", b"no-referrer") in headers
    assert (b"x-content-type-options", b"nosniff") in headers
    assert (b"content-length", b"0") in headers
    assert not any(name == b"set-cookie" for name, _ in headers)
    assert messages[1]["body"] == b""


@pytest.mark.parametrize("condition,reason", [
    ("disabled", "account_disabled"), ("unprovisioned", "login_failed"), ("idp_unverified", "login_failed"),
    ("binding_unverified", "login_failed"), ("wrong_issuer", "login_failed"),
])
def test_expected_identity_denials_are_safe_and_callback_stays_spent(tmp_path, condition, reason):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None)
        account = store.accounts["subject-a"]
        if condition == "disabled":
            store.accounts["subject-a"] = account.model_copy(update={"enabled": False})
        elif condition == "unprovisioned":
            store.accounts.pop("subject-a")
        elif condition == "idp_unverified":
            oidc.identity = oidc.identity.model_copy(update={"email_verified": False})
        elif condition == "binding_unverified":
            actor = account.actor.model_copy(update={"email_verified": False})
            store.accounts["subject-a"] = account.model_copy(update={"actor": actor})
        else:
            oidc.identity = oidc.identity.model_copy(update={"issuer": "https://wrong.example.invalid"})
        _denied(await _invoke(flow, start), reason)
        assert store.claim_effects == oidc.calls == 1
        assert store.commit_calls == store.commit_effects == len(store.sessions) == 0
        _denied(await _invoke(flow, start), "login_failed")
        assert store.claim_effects == oidc.calls == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("error,reason", [
    (OidcRejected(), "login_failed"), (OidcUnavailable(), "idp_unavailable"),
    (RepositoryUnavailable(), "idp_unavailable"), (TimeoutError("synthetic-private-timeout"), "idp_unavailable"),
    (RepositoryDataRejected(), "login_failed"), (RuntimeError("synthetic-private-provider-value"), "login_failed"),
])
def test_callback_distinguishes_invalid_auth_outage_and_internal_defects(tmp_path, error, reason):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None)
        oidc.error = error
        _denied(await _invoke(flow, start), reason)
        assert store.claim_effects == oidc.calls == 1
        assert store.commit_calls == store.commit_effects == 0
        oidc.error = None
        _denied(await _invoke(flow, start), "login_failed")
        assert oidc.calls == 1
    asyncio.run(scenario())


def test_callback_external_cancellation_propagates_without_sending_response(tmp_path):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None)
        oidc.error = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await _invoke(flow, start)
        assert store.claim_effects == 1 and store.commit_effects == 0
    asyncio.run(scenario())


def _provider_query(start, error, **changes):
    state = parse_qs(urlsplit(start.authorization_url.get_secret_value()).query)["state"][0]
    return urlencode({"state": state, "iss": ISSUER, "error": error,
                      "error_description": "synthetic-private-provider-value",
                      "error_uri": "https://untrusted.example.invalid/recovery", **changes}).encode("ascii")


@pytest.mark.parametrize("error,kind,reason", [
    ("access_denied", LoginRejected, "provider_cancelled"),
    ("server_error", OidcUnavailable, None),
    ("temporarily_unavailable", OidcUnavailable, None),
    ("synthetic-private-provider-error", LoginRejected, "invalid_attempt"),
])
def test_claimed_provider_response_has_typed_outcome_without_exchange_or_partial_session(tmp_path, error, kind, reason):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None)
        query = _provider_query(start, error)
        with pytest.raises(kind) as caught:
            await flow.complete(_cookie(start), query)
        if isinstance(caught.value, LoginRejected):
            assert caught.value.reason == reason
        assert "synthetic-private" not in str(caught.value) + repr(caught.value)
        assert store.claim_effects == 1 and oidc.calls == 0
        assert store.lookup_calls == store.commit_calls == store.commit_effects == len(store.sessions) == 0
        with pytest.raises(LoginRejected) as replay:
            await flow.complete(_cookie(start), query)
        assert replay.value.reason == "invalid_attempt"
        assert store.claim_effects == 1 and oidc.calls == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("error,kind", [("access_denied", LoginRejected), ("server_error", OidcUnavailable)])
@pytest.mark.parametrize("forgery", ["state", "issuer"])
def test_provider_outcome_is_not_classified_until_browser_state_and_issuer_are_valid(tmp_path, error, kind, forgery):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None)
        changes = {"state": "z" * 43} if forgery == "state" else {"iss": "https://forged.example.invalid"}
        with pytest.raises(LoginRejected) as caught:
            await flow.complete(_cookie(start), _provider_query(start, error, **changes))
        assert caught.value.reason == "invalid_attempt"
        assert store.claim_effects == oidc.calls == store.lookup_calls == store.commit_calls == 0
        assert len(store.sessions) == 0
        # The rightful callback remains claimable after the forged input.
        with pytest.raises(kind):
            await flow.complete(_cookie(start), _provider_query(start, error))
        assert store.claim_effects == 1 and oidc.calls == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("error,kind", [("access_denied", LoginRejected), ("server_error", OidcUnavailable),
                                      ("temporarily_unavailable", OidcUnavailable), ("unknown", LoginRejected)])
def test_browser_reported_provider_outcome_never_starts_global_idp_backoff(tmp_path, error, kind):
    async def scenario():
        store, provider = SyntheticLoginAuthority(), SyntheticOidc()
        guard = BoundedOidc(provider, timeout_seconds=1.0, maximum_inflight=1,
                            initial_backoff_seconds=1.0, maximum_backoff_seconds=1.0)
        flow = _flow(tmp_path, store, guard)
        start = await flow.begin(None)
        with pytest.raises(kind):
            await flow.complete(_cookie(start), _provider_query(start, error))
        assert provider.calls == store.commit_effects == 0
        # A new explicit start still works immediately; browser input is not an outage probe.
        assert await flow.begin(None)
    asyncio.run(scenario())


def test_callback_constructor_and_default_login_rejection_remain_compatible():
    callback = CallbackInput(SecretStr("s" * 43), None, True)
    assert callback.provider_denied is True and callback.provider_disposition is None
    ordinary, cancelled = LoginRejected(), LoginRejected("provider_cancelled")
    assert ordinary.reason == "invalid_attempt" and cancelled.reason == "provider_cancelled"
    assert ordinary.args == cancelled.args == ("The sign-in attempt is invalid or no longer current.",)


@pytest.mark.parametrize("error,reason", [
    ("access_denied", "login_cancelled"),
    ("server_error", "idp_unavailable"),
    ("temporarily_unavailable", "idp_unavailable"),
    ("synthetic-private-provider-error", "login_failed"),
])
def test_provider_failure_redirects_after_claim_and_replay_is_generic(tmp_path, error, reason):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None)
        query = _provider_query(start, error)
        _denied(await _invoke(flow, start, query=query), reason)
        assert store.claim_effects == 1 and oidc.calls == 0
        assert store.lookup_calls == store.commit_calls == store.commit_effects == len(store.sessions) == 0
        _denied(await _invoke(flow, start, query=query), "login_failed")
        assert store.claim_effects == 1 and oidc.calls == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("method,scheme,status", [("POST", "https", 405), ("GET", "http", 400)])
def test_wrong_method_or_scheme_stays_a_nonredirect_without_claim(tmp_path, method, scheme, status):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None)
        messages = await _invoke(flow, start, method=method, scheme=scheme)
        assert messages[0]["status"] == status
        assert not any(name in (b"location", b"set-cookie") for name, _ in messages[0]["headers"])
        assert messages[1]["body"] == b"Authentication request could not be completed."
        assert store.claim_effects == oidc.calls == store.commit_effects == 0
    asyncio.run(scenario())


def test_callback_clock_denial_redirects_without_claim_or_cookie(tmp_path):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None)

        async def unavailable_clock():
            return False

        _denied(await _invoke(flow, start, clock_check=unavailable_clock), "idp_unavailable")
        assert store.claim_effects == oidc.calls == store.commit_effects == 0
    asyncio.run(scenario())


def test_callback_deadline_redirects_after_claim_without_session_and_replay_is_generic(tmp_path):
    async def scenario():
        flow, store, oidc = _system(tmp_path)
        start = await flow.begin(None)
        oidc.release.clear()
        _denied(await _invoke(flow, start, timeout_seconds=0.01), "idp_unavailable")
        assert store.claim_effects == oidc.calls == 1
        assert store.lookup_calls == store.commit_calls == store.commit_effects == len(store.sessions) == 0
        _denied(await _invoke(flow, start), "login_failed")
        assert store.claim_effects == oidc.calls == 1
    asyncio.run(scenario())
