import asyncio
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from test_k03_login_coordinator import ISSUER, POLICY, _runtime

from aletheia_lab.hosted_auth import oidc_discovery
from aletheia_lab.hosted_auth.oidc import OidcUnavailable


def test_discovery_pins_issuer_caches_and_refreshes(tmp_path, monkeypatch):
    calls = []
    metadata = {'issuer': ISSUER, 'authorization_endpoint': ISSUER + '/authorize',
                'token_endpoint': ISSUER + '/token', 'jwks_uri': ISSUER + '/keys',
                'end_session_endpoint': ISSUER + '/logout'}
    async def handler(request):
        calls.append(str(request.url))
        return httpx2.Response(200, json={'keys': [{'use':'sig', 'kty':'RSA'}]} if request.url.path.endswith('/keys') else metadata)
    class Client:
        def __init__(self, *args, **kwargs):
            pass
    monkeypatch.setattr(oidc_discovery, 'AuthlibOidcClient', Client)

    async def scenario():
        loader = oidc_discovery.RefreshingOidc(_runtime(tmp_path), POLICY,
                                               transport_factory=lambda:httpx2.MockTransport(handler))
        first, second = await asyncio.gather(loader._current(), loader._current())
        assert first is second and len(calls) == 2
        from urllib.parse import parse_qs, urlsplit
        result = urlsplit(await loader.end_session_url())
        query = parse_qs(result.query)
        assert result.path.endswith('/logout')
        assert query['post_logout_redirect_uri'] == ['https://app.example.invalid/#/signed-out']
        loader._expires = datetime.now(UTC) - timedelta(seconds=1)
        assert await loader._current() is not first and len(calls) == 4
        loader._expires = datetime.now(UTC) - timedelta(seconds=1)
        metadata['jwks_uri'] = 'https://evil.invalid/keys'
        with pytest.raises(OidcUnavailable):
            await loader._current()
        assert len(calls) == 5
        metadata['jwks_uri'] = ISSUER + '/keys'
        metadata['end_session_endpoint'] = 'https://evil.invalid/logout'
        with pytest.raises(OidcUnavailable):
            await loader.end_session_url()
    asyncio.run(scenario())
