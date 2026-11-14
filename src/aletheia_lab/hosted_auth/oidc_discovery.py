"""Bounded, issuer-pinned OIDC discovery and signing-key refresh."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from urllib.parse import urlencode

import httpx2
from pydantic import SecretStr

from aletheia_lab.hosted.settings import RuntimeSettings

from .boundary import CallbackInput
from .oidc import (
    AuthlibOidcClient,
    AuthorizationAttempt,
    OidcUnavailable,
    _BoundedTransport,
    _origin,
    _StrictDecoder,
)
from .repositories import ValidatedIdentity
from .settings import AuthPolicy


class RefreshingOidc:
    """Fetch only from the configured issuer; never use stale signing keys."""

    def __init__(self, runtime: RuntimeSettings, policy: AuthPolicy, *,
                 client_secret: SecretStr | None = None,
                 transport_factory: Callable[[], httpx2.AsyncBaseTransport] = httpx2.AsyncHTTPTransport) -> None:
        self._runtime, self._policy = runtime, policy
        self._secret, self._transport_factory = client_secret, transport_factory
        self._client: AuthlibOidcClient | None = None
        self._expires = datetime.min.replace(tzinfo=UTC)
        self._end_session_endpoint: str | None = None
        self._lock = asyncio.Lock()

    async def _current(self) -> AuthlibOidcClient:
        async with self._lock:
            now = datetime.now(UTC)
            if self._client is not None and now < self._expires:
                return self._client
            try:
                issuer = self._runtime.identity.issuer
                origin = _origin(issuer)
                async with httpx2.AsyncClient(transport=_BoundedTransport(self._transport_factory()),
                        timeout=self._policy.idp_timeout_seconds, trust_env=False, follow_redirects=False,
                        headers={'Accept-Encoding':'identity'}) as client:
                    response = await client.get(issuer.rstrip('/') + '/.well-known/openid-configuration')
                    metadata = json.loads(response.content, cls=_StrictDecoder)
                    if not isinstance(metadata, dict) or metadata.get('issuer') != issuer:
                        raise ValueError('issuer mismatch')
                    authorization, token, jwks = (metadata.get(name) for name in
                        ('authorization_endpoint', 'token_endpoint', 'jwks_uri'))
                    if not all(isinstance(value, str) and _origin(value) == origin
                               for value in (authorization, token, jwks)):
                        raise ValueError('provider endpoint mismatch')
                    logout = metadata.get('end_session_endpoint')
                    if logout is not None and (not isinstance(logout, str) or _origin(logout) != origin):
                        raise ValueError('provider logout endpoint mismatch')
                    keys = json.loads((await client.get(cast(str, jwks))).content, cls=_StrictDecoder)
                    if not isinstance(keys, dict):
                        raise ValueError('invalid keys')
                    entries = keys.get('keys')
                    if not isinstance(entries, list) or len(entries) > 32 or any(not isinstance(key, dict) for key in entries):
                        raise ValueError('invalid keys')
                    keys = {'keys': [key for key in entries if key.get('use', 'sig') == 'sig']}
                expiry = datetime.now(UTC) + timedelta(seconds=120)
                result = AuthlibOidcClient(self._runtime, self._policy,
                    authorization_endpoint=cast(str, authorization), token_endpoint=cast(str, token),
                    public_jwks=keys, material_expires_at=expiry, client_secret=self._secret,
                    transport_factory=self._transport_factory)
                self._client, self._expires = result, expiry
                self._end_session_endpoint = logout
                return result
            except Exception:
                self._client = None
                self._end_session_endpoint = None
                raise OidcUnavailable() from None

    async def end_session_url(self) -> str:
        """Use only configured client and fixed app destination, never request input."""
        await self._current()
        endpoint = self._end_session_endpoint
        if endpoint is None:
            raise OidcUnavailable()
        destination = self._runtime.public_origin.rstrip('/') + '/#/signed-out'
        return endpoint + '?' + urlencode({'client_id': self._runtime.identity.client_id,
                                           'post_logout_redirect_uri': destination})

    async def authorization_url(self, attempt: AuthorizationAttempt) -> SecretStr:
        return await (await self._current()).authorization_url(attempt)

    async def exchange(self, callback: CallbackInput, attempt: AuthorizationAttempt) -> ValidatedIdentity:
        return await (await self._current()).exchange(callback, attempt)
