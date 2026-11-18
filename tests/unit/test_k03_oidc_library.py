"""Real Authlib/JOSE crypto with generated keys and mock HTTP, no real IdP.

Missing dependencies must fail collection, not silently skip qualification.
Transaction replay/browser binding are separate coordinator/store obligations.
"""

import asyncio
import base64
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from joserfc import jwt
from joserfc.jwk import OctKey, RSAKey
from pydantic import SecretStr

from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted_auth.boundary import CallbackInput
from aletheia_lab.hosted_auth.oidc import (
    AuthlibOidcClient,
    OidcRejected,
    OidcUnavailable,
    new_authorization_attempt,
)
from aletheia_lab.hosted_auth.settings import AuthConfigurationRejected, AuthPolicy

NOW = datetime(2030, 1, 1, tzinfo=UTC)
ISSUER = "https://identity.example.invalid/realm"
CLIENT_ID = "synthetic-client"
CALLBACK = "https://app.example.invalid/auth/callback"
POLICY = AuthPolicy(default_return_to="/app", return_to_paths=("/app",))


@pytest.fixture(scope="module")
def signing_key():
    # Ephemeral test key only; never exported to disk or printed.
    return RSAKey.generate_key(2048, {"kid": "synthetic-key"})


def _runtime(tmp_path, *, confidential=False):
    identity = {
        "issuer": ISSUER, "client_id": CLIENT_ID, "redirect_uri": CALLBACK,
        "client_kind": "confidential" if confidential else "public",
    }
    if confidential:
        identity["client_secret_env"] = "ALETHEIA_HOSTED_OIDC_CLIENT_SECRET"
    return RuntimeSettings.model_validate({
        "environment": "staging", "public_origin": "https://app.example.invalid",
        "identity": identity,
        "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)},
    })


def _claims(attempt, **changes):
    return {
        "iss": ISSUER, "sub": "synthetic-subject", "aud": CLIENT_ID,
        "iat": int(NOW.timestamp()), "exp": int(NOW.timestamp()) + 120,
        "nonce": attempt.nonce.get_secret_value(),
        "email": "synthetic@example.invalid", "email_verified": True,
        **changes,
    }


class SyntheticProvider:
    """Only an in-memory HTTP transport, never a socket or external service."""

    def __init__(self, key, attempt):
        self.key = key
        self.attempt = attempt
        self.claims = _claims(attempt)
        self.header = {"alg": "RS256", "kid": "synthetic-key"}
        self.status = 200
        self.headers = {}
        self.requests = []
        self.issued_tokens = []
        self.raw_body = None
        self.token_changes = {}

    def handle(self, request):
        self.requests.append(request)
        assert request.method == "POST"
        assert str(request.url) == ISSUER + "/token"
        form = parse_qs(request.content.decode())
        assert form["grant_type"] == ["authorization_code"]
        assert form["redirect_uri"] == [CALLBACK]
        assert form["code_verifier"] == [self.attempt.code_verifier.get_secret_value()]
        assert form["code"] == ["synthetic-authorization-code"]
        assert request.headers["accept-encoding"] == "identity"
        if self.raw_body is not None:
            return httpx2.Response(self.status, content=self.raw_body, headers={"content-type": "application/json", **self.headers})
        value = jwt.encode(self.header, self.claims, self.key)
        self.issued_tokens.append(value)
        payload = {"access_token": "synthetic-access-token", "token_type": "Bearer", "id_token": value, **self.token_changes}
        return httpx2.Response(self.status, json=payload, headers=self.headers)


def _system(tmp_path, key, *, reauthenticate=False, confidential=False, policy=POLICY, **changes):
    attempt = new_authorization_attempt(policy, now=NOW, reauthenticate=reauthenticate)
    provider = SyntheticProvider(key, attempt)
    options = {
        "authorization_endpoint": ISSUER + "/authorize",
        "token_endpoint": ISSUER + "/token",
        "public_jwks": {"keys": [key.as_dict(private=False)]},
        "material_expires_at": NOW + timedelta(seconds=300),
        "client_secret": SecretStr("synthetic-client-secret") if confidential else None,
        "transport_factory": lambda: httpx2.MockTransport(provider.handle),
        "clock": lambda: NOW,
        **changes,
    }
    client = AuthlibOidcClient(_runtime(tmp_path, confidential=confidential), policy, **options)
    callback = CallbackInput(attempt.state, SecretStr("synthetic-authorization-code"), False)
    return client, provider, attempt, callback


def test_authlib_builds_code_request_with_s256_nonce_state_and_exact_redirect(tmp_path, signing_key):
    async def scenario():
        client, provider, attempt, _ = _system(tmp_path, signing_key)
        url = await client.authorization_url(attempt)
        parsed = urlsplit(url.get_secret_value())
        query = parse_qs(parsed.query)
        verifier = attempt.code_verifier.get_secret_value()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        assert query["response_type"] == ["code"]
        assert query["response_mode"] == ["query"]
        assert query["redirect_uri"] == [CALLBACK]
        assert query["client_id"] == [CLIENT_ID]
        assert query["state"] == [attempt.state.get_secret_value()]
        assert query["nonce"] == [attempt.nonce.get_secret_value()]
        assert query["code_challenge_method"] == ["S256"]
        assert query["code_challenge"] == [challenge]
        assert verifier not in url.get_secret_value()
        assert attempt.state.get_secret_value() not in repr(url)
        assert verifier not in repr(attempt)
        assert provider.requests == []
    asyncio.run(scenario())


@pytest.mark.parametrize("confidential", [False, True])
def test_authlib_exchange_and_jose_signature_validation_return_only_identity(tmp_path, signing_key, confidential):
    async def scenario():
        client, provider, attempt, callback = _system(tmp_path, signing_key, confidential=confidential)
        identity = await client.exchange(callback, attempt)
        assert identity.issuer == ISSUER
        assert identity.subject.get_secret_value() == "synthetic-subject"
        assert identity.email_verified is True
        assert len(provider.requests) == 1
        request = provider.requests[0]
        if confidential:
            expected = base64.b64encode((CLIENT_ID + ":synthetic-client-secret").encode()).decode()
            assert request.headers["authorization"] == "Basic " + expected
            assert b"synthetic-client-secret" not in request.content
        else:
            assert "authorization" not in request.headers
            assert parse_qs(request.content.decode())["client_id"] == [CLIENT_ID]
        assert "synthetic-access-token" not in repr(identity)
        assert "synthetic@example.invalid" not in repr(identity)
    asyncio.run(scenario())


@pytest.mark.parametrize("changes", [
    {"iss": "https://evil.invalid"}, {"aud": "wrong-client"},
    {"aud": [CLIENT_ID, "other"], "azp": "wrong-client"},
    {"nonce": "wrong-nonce"}, {"nonce": None, "nonce_supported": False},
    {"exp": int(NOW.timestamp()) - 31}, {"iat": int(NOW.timestamp()) + 31},
    {"nbf": int(NOW.timestamp()) + 31}, {"exp": True}, {"sub": 123},
    {"email_verified": "true"}, {"at_hash": "invalid-hash"},
])
def test_invalid_signed_claims_fail_without_returning_identity(tmp_path, signing_key, changes):
    async def scenario():
        client, provider, attempt, callback = _system(tmp_path, signing_key)
        provider.claims.update(changes)
        with pytest.raises(OidcRejected) as caught:
            await client.exchange(callback, attempt)
        assert len(provider.requests) == 1
        assert len(provider.issued_tokens) == 1
        assert str(caught.value) == "The sign-in response could not be verified."
        assert "synthetic-access-token" not in repr(caught.value)
        assert caught.value.__suppress_context__ is True
    asyncio.run(scenario())


@pytest.mark.parametrize("missing", ["iss", "sub", "aud", "exp", "iat", "nonce"])
def test_missing_essential_claim_is_rejected(tmp_path, signing_key, missing):
    async def scenario():
        client, provider, attempt, callback = _system(tmp_path, signing_key)
        provider.claims.pop(missing)
        with pytest.raises(OidcRejected):
            await client.exchange(callback, attempt)
        assert len(provider.issued_tokens) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("changes", [{"exp": int(NOW.timestamp()) - 29}, {"iat": int(NOW.timestamp()) + 29}])
def test_configured_clock_skew_is_applied_by_library(tmp_path, signing_key, changes):
    async def scenario():
        client, provider, attempt, callback = _system(tmp_path, signing_key)
        provider.claims.update(changes)
        assert (await client.exchange(callback, attempt)).issuer == ISSUER
    asyncio.run(scenario())


def test_wrong_signature_key_fails(tmp_path, signing_key):
    async def scenario():
        client, provider, attempt, callback = _system(tmp_path, signing_key)
        provider.key = RSAKey.generate_key(2048, {"kid": "synthetic-key"})
        with pytest.raises(OidcRejected):
            await client.exchange(callback, attempt)
    asyncio.run(scenario())


@pytest.mark.parametrize("algorithm", ["none", "HS256"])
def test_unsigned_and_symmetric_tokens_are_never_accepted(tmp_path, signing_key, algorithm):
    async def scenario():
        client, provider, attempt, callback = _system(tmp_path, signing_key)
        if algorithm == "none":
            def encode(value):
                return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

            value = encode({"alg": "none", "kid": "synthetic-key"}) + "." + encode(_claims(attempt)) + "."
        else:
            key = OctKey.import_key("synthetic-symmetric-key-with-enough-entropy")
            value = jwt.encode({"alg": "HS256", "kid": "synthetic-key"}, _claims(attempt), key)
        provider.token_changes["id_token"] = value
        with pytest.raises(OidcRejected):
            await client.exchange(callback, attempt)
    asyncio.run(scenario())


@pytest.mark.parametrize("changes", [
    {"kid": "unknown"}, {"typ": "at+jwt"}, {"jku": "https://evil.invalid/jwks"},
    {"jwk": {"kty": "oct", "k": "attacker"}}, {"x5u": "https://evil.invalid/cert"},
])
def test_token_cannot_select_its_key_source_or_another_token_profile(tmp_path, signing_key, changes):
    async def scenario():
        client, provider, attempt, callback = _system(tmp_path, signing_key)
        provider.header.update(changes)
        with pytest.raises(OidcRejected):
            await client.exchange(callback, attempt)
        assert len(provider.requests) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("bad_callback", ["wrong_state", "denied", "missing_code"])
def test_bad_callback_has_zero_token_requests(tmp_path, signing_key, bad_callback):
    async def scenario():
        client, provider, attempt, callback = _system(tmp_path, signing_key)
        if bad_callback == "wrong_state":
            callback = replace(callback, state=SecretStr("x" * 43))
        elif bad_callback == "denied":
            callback = replace(callback, provider_denied=True)
        else:
            callback = replace(callback, code=None)
        with pytest.raises(OidcRejected):
            await client.exchange(callback, attempt)
        assert provider.requests == []
    asyncio.run(scenario())


def test_expired_attempt_and_expired_key_material_fail_before_network(tmp_path, signing_key):
    async def scenario():
        current = [NOW]
        client, provider, attempt, callback = _system(tmp_path, signing_key, clock=lambda: current[0])
        expired = replace(attempt, expires_at=NOW)
        with pytest.raises(OidcRejected):
            await client.exchange(callback, expired)
        current[0] = NOW + timedelta(seconds=300)
        with pytest.raises(OidcUnavailable):
            await client.exchange(callback, attempt)
        assert provider.requests == []
    asyncio.run(scenario())


@pytest.mark.parametrize("status,error", [(302, OidcRejected), (400, OidcRejected), (429, OidcUnavailable), (503, OidcUnavailable)])
def test_redirect_and_outage_are_bounded_single_attempts(tmp_path, signing_key, status, error):
    async def scenario():
        client, provider, attempt, callback = _system(tmp_path, signing_key)
        provider.status = status
        provider.headers = {"location": "https://evil.invalid"}
        with pytest.raises(error):
            await client.exchange(callback, attempt)
        assert len(provider.requests) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("body,headers", [
    (b"x" * 65537, {}),
    (b"[]", {}),
    (b'{"id_token":"x","id_token":"y"}', {}),
    (b'{"value":NaN}', {}),
    (b"{}", {"content-type": "text/html"}),
    (b"compressed", {"content-encoding": "gzip"}),
])
def test_bad_or_unbounded_provider_body_is_rejected(tmp_path, signing_key, body, headers):
    async def scenario():
        client, provider, attempt, callback = _system(tmp_path, signing_key)
        provider.raw_body = body
        provider.headers = headers
        with pytest.raises(OidcRejected):
            await client.exchange(callback, attempt)
        assert len(provider.requests) == 1
    asyncio.run(scenario())


def test_reauthentication_requests_fresh_login_and_checks_auth_time(tmp_path, signing_key):
    async def scenario():
        client, provider, attempt, callback = _system(tmp_path, signing_key, reauthenticate=True)
        query = parse_qs(urlsplit((await client.authorization_url(attempt)).get_secret_value()).query)
        assert query["prompt"] == ["login"]
        assert query["max_age"] == ["0"]
        with pytest.raises(OidcRejected):
            await client.exchange(callback, attempt)
        provider.claims["auth_time"] = int(NOW.timestamp()) - 60
        with pytest.raises(OidcRejected):
            await client.exchange(callback, attempt)
        provider.claims["auth_time"] = int(NOW.timestamp())
        assert (await client.exchange(callback, attempt)).issuer == ISSUER
    asyncio.run(scenario())


@pytest.mark.parametrize("auth_time", [True, str(int(NOW.timestamp())), None, int(NOW.timestamp()) + 31])
def test_reauthentication_rejects_non_numeric_or_future_auth_time(tmp_path, signing_key, auth_time):
    async def scenario():
        client, provider, attempt, callback = _system(tmp_path, signing_key, reauthenticate=True)
        provider.claims["auth_time"] = auth_time
        with pytest.raises(OidcRejected):
            await client.exchange(callback, attempt)
        assert len(provider.issued_tokens) == 1
    asyncio.run(scenario())


def test_invalid_public_key_material_or_endpoint_cannot_enable_client(tmp_path, signing_key):
    with pytest.raises(AuthConfigurationRejected):
        _system(tmp_path, signing_key, token_endpoint="https://evil.invalid/token")
    with pytest.raises(AuthConfigurationRejected):
        _system(tmp_path, signing_key, public_jwks={"keys": [signing_key.as_dict(private=True)]})
    with pytest.raises(AuthConfigurationRejected):
        _system(tmp_path, signing_key, material_expires_at=NOW + timedelta(days=1))
    with pytest.raises(AuthConfigurationRejected):
        _system(tmp_path, signing_key, public_jwks={"keys": [{"kty": "oct", "kid": "sym", "k": "secret"}]})


def test_hung_provider_has_one_bounded_attempt(tmp_path, signing_key):
    async def scenario():
        calls = []

        async def hung(request):
            calls.append(1)
            await asyncio.Event().wait()

        client, _, attempt, callback = _system(
            tmp_path, signing_key,
            policy=POLICY.model_copy(update={"idp_timeout_seconds": 0.01}),
            transport_factory=lambda: httpx2.MockTransport(hung),
        )
        with pytest.raises(OidcUnavailable):
            await client.exchange(callback, attempt)
        assert calls == [1]
    asyncio.run(scenario())


def test_external_cancellation_propagates(tmp_path, signing_key):
    async def scenario():
        async def hung(request):
            await asyncio.Event().wait()

        client, _, attempt, callback = _system(
            tmp_path, signing_key, transport_factory=lambda: httpx2.MockTransport(hung),
        )
        # External cancellation is not converted into an auth denial or success.
        task = asyncio.create_task(client.exchange(callback, attempt))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())


def test_streamed_provider_body_is_capped_and_closed_on_rejection(tmp_path, signing_key):
    class OversizedStream(httpx2.AsyncByteStream):
        def __init__(self):
            self.chunks = 0
            self.closed = False

        async def __aiter__(self):
            for _ in range(10):
                self.chunks += 1
                yield b"x" * 32768

        async def aclose(self):
            self.closed = True

    async def scenario():
        stream = OversizedStream()

        async def handle(request):
            return httpx2.Response(200, headers={"content-type": "application/json"}, stream=stream)

        client, _, attempt, callback = _system(
            tmp_path, signing_key, transport_factory=lambda: httpx2.MockTransport(handle),
        )
        with pytest.raises(OidcRejected):
            await client.exchange(callback, attempt)
        assert stream.chunks == 3
        assert stream.closed is True
    asyncio.run(scenario())
