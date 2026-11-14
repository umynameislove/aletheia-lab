"""Maintained-library OIDC client over explicitly pinned trusted provider material.

Not an HTTP route or login transaction store. Callers must atomically claim a
browser-bound one-use server transaction BEFORE exchange, then commit the local
session with current account/epoch checks. No browser-supplied JWKS/endpoints.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import re
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import import_module
from types import TracebackType
from typing import Any, Protocol, cast
from urllib.parse import urlencode, urlsplit

import httpx2
from joserfc import jwt
from joserfc.jwk import KeySet
from pydantic import SecretStr

from aletheia_lab.hosted.settings import RuntimeSettings

from .boundary import CallbackInput, new_opaque_secret
from .repositories import ValidatedIdentity
from .settings import AuthConfigurationRejected, AuthPolicy

_UNSAFE_URL = re.compile(r"[\x00-\x20\x7f\\%]")
_PKCE = re.compile(r"[A-Za-z0-9._~-]{43,128}")
_RANDOM = re.compile(r"[A-Za-z0-9_-]{43,128}")
_MAX_RESPONSE = 65536
_MAX_TOKEN = 16384


class OidcRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("The sign-in response could not be verified.")


class OidcUnavailable(TimeoutError):
    def __init__(self) -> None:
        super().__init__("The identity service is temporarily unavailable.")


@dataclass(frozen=True)
class AuthorizationAttempt:
    """Trusted server transaction data, not proof that the transaction was claimed."""

    state: SecretStr
    nonce: SecretStr
    code_verifier: SecretStr
    created_at: datetime
    expires_at: datetime
    reauthenticate: bool = False


def new_authorization_attempt(policy: AuthPolicy, *, now: datetime, reauthenticate: bool = False) -> AuthorizationAttempt:
    if now.utcoffset() is None:
        raise AuthConfigurationRejected()
    return AuthorizationAttempt(
        new_opaque_secret(), new_opaque_secret(), SecretStr(secrets.token_urlsafe(64)),
        now, now + timedelta(seconds=policy.login_transaction_seconds), reauthenticate,
    )


class _OAuthClient(Protocol):
    async def __aenter__(self) -> _OAuthClient: ...

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def create_authorization_url(self, url: str, **kwargs: object) -> tuple[str, str]: ...

    async def fetch_token(self, url: str, **kwargs: object) -> Mapping[str, object]: ...


class _OAuthFactory(Protocol):
    def __call__(self, **kwargs: object) -> _OAuthClient: ...


class _Claims(Protocol):
    def validate(self, *, now: int, leeway: int) -> None: ...


class _ClaimsFactory(Protocol):
    def __call__(
        self, claims: dict[str, Any], header: dict[str, Any], *,
        options: dict[str, object], params: dict[str, object],
    ) -> _Claims: ...


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise OidcRejected()
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise OidcRejected()


class _StrictDecoder(json.JSONDecoder):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.update(object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
        super().__init__(*args, **kwargs)


def _origin(value: str) -> tuple[str, str, int]:
    if _UNSAFE_URL.search(value):
        raise AuthConfigurationRejected()
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
        or parsed.password is not None or parsed.query or parsed.fragment or parsed.port == 0
    ):
        raise AuthConfigurationRejected()
    return parsed.scheme, parsed.hostname.lower(), parsed.port or 443


class _BoundedTransport(httpx2.AsyncBaseTransport):
    """Bound raw provider responses before Authlib buffers/parses them."""

    def __init__(self, inner: httpx2.AsyncBaseTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        response = await self._inner.handle_async_request(request)
        try:
            if response.status_code == 429 or response.status_code >= 500:
                raise OidcUnavailable()
            if response.status_code != 200:
                raise OidcRejected()
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise OidcRejected()
            media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media != "application/json":
                raise OidcRejected()
            body = await self._body(response)
            # Prevalidate JSON shape/duplicates/numbers before the library sees it.
            parsed = json.loads(body, cls=_StrictDecoder)
            if not isinstance(parsed, dict):
                raise OidcRejected()
            return httpx2.Response(200, headers=response.headers, content=body, request=request)
        finally:
            await response.aclose()

    @staticmethod
    async def _body(response: httpx2.Response) -> bytes:
        if response.is_stream_consumed:
            content = response.content
            if len(content) > _MAX_RESPONSE:
                raise OidcRejected()
            return content
        body = bytearray()
        async for chunk in response.aiter_raw():
            if len(body) + len(chunk) > _MAX_RESPONSE:
                raise OidcRejected()
            body.extend(chunk)
        return bytes(body)

    async def aclose(self) -> None:
        await self._inner.aclose()


class AuthlibOidcClient:
    """Authorization Code + PKCE S256 and ID Token verification, no token storage.

    Provider material must come from approved configuration or a separately
    bounded, issuer-pinned discovery/JWKS loader. No loader is invented here.
    Expired material fails closed; production refresh/lifecycle remains pending.
    """

    def __init__(
        self, runtime: RuntimeSettings, policy: AuthPolicy, *,
        authorization_endpoint: str, token_endpoint: str, public_jwks: dict[str, object],
        material_expires_at: datetime, client_secret: SecretStr | None = None,
        allowed_endpoint_origins: tuple[str, ...] = (),
        transport_factory: Callable[[], httpx2.AsyncBaseTransport] = httpx2.AsyncHTTPTransport,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._policy = AuthPolicy.model_validate(policy.model_dump(warnings=False))
        self._runtime = RuntimeSettings.model_validate(runtime.model_dump(warnings=False))
        self._policy.require_runtime(self._runtime)
        identity = self._runtime.identity
        if (client_secret is not None) != (identity.client_kind == "confidential"):
            raise AuthConfigurationRejected()
        if client_secret is not None and not client_secret.get_secret_value():
            raise AuthConfigurationRejected()
        try:
            origins = {_origin(identity.issuer), *(_origin(item) for item in allowed_endpoint_origins)}
            if _origin(authorization_endpoint) not in origins or _origin(token_endpoint) not in origins:
                raise AuthConfigurationRejected()
            if material_expires_at.utcoffset() is None:
                raise AuthConfigurationRejected()
            material_now = clock()
            if material_now.utcoffset() is None or not material_now < material_expires_at <= material_now + timedelta(seconds=300):
                raise AuthConfigurationRejected()
            self._keys = self._public_keys(public_jwks)
            self._oauth = cast(_OAuthFactory, import_module("authlib.integrations.httpx_client").AsyncOAuth2Client)
            self._claims = cast(_ClaimsFactory, import_module("authlib.oidc.core").CodeIDToken)
        except Exception:
            raise AuthConfigurationRejected() from None
        self._authorization_endpoint = authorization_endpoint
        self._token_endpoint = token_endpoint
        self._material_expires_at = material_expires_at
        self._secret = client_secret
        self._transport_factory = transport_factory
        self._clock = clock

    def _public_keys(self, data: dict[str, object]) -> KeySet:
        raw = json.dumps(data, allow_nan=False).encode("utf-8")
        if len(raw) > _MAX_RESPONSE:
            raise AuthConfigurationRejected()
        copied = json.loads(raw, cls=_StrictDecoder)
        keys = copied.get("keys")
        if not isinstance(keys, list) or not 1 <= len(keys) <= 16:
            raise AuthConfigurationRejected()
        ids: set[str] = set()
        for key in keys:
            if not isinstance(key, dict) or key.get("kty") not in ("RSA", "EC"):
                raise AuthConfigurationRejected()
            kid = key.get("kid")
            if not isinstance(kid, str) or not 1 <= len(kid) <= 128 or kid in ids:
                raise AuthConfigurationRejected()
            if any(item in key for item in ("d", "p", "q", "dp", "dq", "qi", "oth", "k")):
                raise AuthConfigurationRejected()
            if key.get("use", "sig") != "sig" or key.get("key_ops", ["verify"]) != ["verify"]:
                raise AuthConfigurationRejected()
            ids.add(kid)
        return KeySet.import_key_set(copied)

    def _attempt(self, attempt: AuthorizationAttempt) -> None:
        now = self._clock()
        if now.utcoffset() is None:
            raise OidcRejected()
        if self._material_expires_at <= now:
            raise OidcUnavailable()
        if (
            now.utcoffset() is None or attempt.created_at.utcoffset() is None
            or attempt.expires_at.utcoffset() is None
            or not attempt.created_at <= now < attempt.expires_at
            or attempt.expires_at - attempt.created_at > timedelta(seconds=self._policy.login_transaction_seconds)
            or not _RANDOM.fullmatch(attempt.state.get_secret_value())
            or not _RANDOM.fullmatch(attempt.nonce.get_secret_value())
            or not _PKCE.fullmatch(attempt.code_verifier.get_secret_value())
        ):
            raise OidcRejected()

    def _client(self, attempt: AuthorizationAttempt) -> _OAuthClient:
        identity = self._runtime.identity
        return self._oauth(
            client_id=identity.client_id,
            client_secret=self._secret.get_secret_value() if self._secret is not None else None,
            token_endpoint_auth_method="client_secret_basic" if identity.client_kind == "confidential" else "none",
            redirect_uri=identity.redirect_uri, scope="openid email profile",
            state=attempt.state.get_secret_value(), code_challenge_method="S256",
            timeout=self._policy.idp_timeout_seconds, follow_redirects=False, trust_env=False,
            headers={"Accept-Encoding": "identity"},
            transport=_BoundedTransport(self._transport_factory()),
        )

    async def authorization_url(self, attempt: AuthorizationAttempt) -> SecretStr:
        self._attempt(attempt)
        try:
            async with self._client(attempt) as client:
                extra: dict[str, object] = {}
                if self._policy.required_acr is not None:
                    extra["acr_values"] = self._policy.required_acr
                if attempt.reauthenticate:
                    extra.update(prompt="login", max_age=0)
                url, state = client.create_authorization_url(
                    self._authorization_endpoint, state=attempt.state.get_secret_value(),
                    nonce=attempt.nonce.get_secret_value(),
                    code_verifier=attempt.code_verifier.get_secret_value(), response_mode="query", **extra,
                )
        except Exception:
            raise OidcRejected() from None
        if not hmac.compare_digest(state, attempt.state.get_secret_value()):
            raise OidcRejected()
        return SecretStr(url)

    async def exchange(self, callback: CallbackInput, attempt: AuthorizationAttempt) -> ValidatedIdentity:
        """Only after atomic browser-bound transaction claim; never auto-retry."""
        self._attempt(attempt)
        if callback.provider_denied or callback.code is None:
            raise OidcRejected()
        if not hmac.compare_digest(callback.state.get_secret_value(), attempt.state.get_secret_value()):
            raise OidcRejected()
        # Reconstruct solely from the configured redirect URI, never Host/return-to.
        response_url = self._runtime.identity.redirect_uri + "?" + urlencode({
            "code": callback.code.get_secret_value(), "state": callback.state.get_secret_value(),
        })
        try:
            async with asyncio.timeout(self._policy.idp_timeout_seconds):
                async with self._client(attempt) as client:
                    token = await client.fetch_token(
                        self._token_endpoint, authorization_response=response_url,
                        code_verifier=attempt.code_verifier.get_secret_value(),
                    )
                return self._verified_identity(token, attempt)
        except (TimeoutError, httpx2.TransportError):
            raise OidcUnavailable() from None
        except Exception:
            raise OidcRejected() from None

    def _verified_identity(self, response: Mapping[str, object], attempt: AuthorizationAttempt) -> ValidatedIdentity:
        value, access = response.get("id_token"), response.get("access_token")
        token_type = response.get("token_type")
        if (
            not isinstance(value, str) or not 1 <= len(value) <= _MAX_TOKEN
            or not isinstance(access, str) or not 1 <= len(access) <= _MAX_TOKEN
            or not isinstance(token_type, str) or token_type.lower() != "bearer"
        ):
            raise OidcRejected()
        self._header(value)
        token = jwt.decode(value, self._keys, algorithms=self._policy.id_token_algorithms, decoder_cls=_StrictDecoder)
        options: dict[str, object] = {
            "iss": {"essential": True, "value": self._runtime.identity.issuer},
            "aud": {"essential": True, "value": self._runtime.identity.client_id},
            "nonce": {"essential": True, "value": attempt.nonce.get_secret_value()},
        }
        if attempt.reauthenticate:
            options["auth_time"] = {"essential": True}
        claims = self._claims(token.claims, token.header, options=options, params={
            "client_id": self._runtime.identity.client_id,
            "nonce": attempt.nonce.get_secret_value(), "access_token": access,
        })
        claims.validate(now=int(self._clock().timestamp()), leeway=self._policy.clock_skew_seconds)
        return self._profile(token.claims, attempt)

    def _header(self, value: str) -> None:
        parts = value.split(".")
        if len(parts) != 3 or any(not part for part in parts) or len(parts[0]) > 4096:
            raise OidcRejected()
        encoded = parts[0].encode("ascii")
        raw = base64.b64decode(encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        header = json.loads(raw, cls=_StrictDecoder)
        if not isinstance(header, dict):
            raise OidcRejected()
        if header.get("alg") not in self._policy.id_token_algorithms or header.get("typ", "JWT") != "JWT":
            raise OidcRejected()
        if not isinstance(header.get("kid"), str) or not 1 <= len(header["kid"]) <= 128:
            raise OidcRejected()
        if any(name in header for name in ("jku", "jwk", "x5u", "x5c", "crit")):
            raise OidcRejected()

    def _profile(self, claims: dict[str, Any], attempt: AuthorizationAttempt) -> ValidatedIdentity:
        if self._policy.required_acr is not None and claims.get("acr") != self._policy.required_acr:
            raise OidcRejected()
        if not isinstance(claims.get("sub"), str):
            raise OidcRejected()
        email, verified = claims.get("email"), claims.get("email_verified", False)
        if (email is not None and not isinstance(email, str)) or type(verified) is not bool:
            raise OidcRejected()
        if attempt.reauthenticate:
            auth_time = claims.get("auth_time")
            skew = self._policy.clock_skew_seconds
            if isinstance(auth_time, bool) or not isinstance(auth_time, (int, float)):
                raise OidcRejected()
            if not attempt.created_at.timestamp() - skew <= auth_time <= self._clock().timestamp() + skew:
                raise OidcRejected()
        return ValidatedIdentity(
            issuer=claims["iss"], subject=SecretStr(claims["sub"]),
            email=SecretStr(email) if isinstance(email, str) else None, email_verified=verified,
        )
