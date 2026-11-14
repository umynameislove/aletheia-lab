import asyncio
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import SecretStr

from aletheia_lab.hosted_auth.login import LoginStart
from aletheia_lab.hosted_auth.start_http import LoginStartApplication


def test_login_start_requires_same_origin_and_sets_bound_cookie():
    class Coordinator:
        calls = 0
        switch = False
        async def begin(self, cookie, *, return_to=None, reauthenticate=False):
            self.calls += 1
            self.switch = reauthenticate
            assert return_to == '/app'
            return LoginStart(SecretStr('https://id.example/authorize?state=synthetic'),
                              SecretStr('x' * 43), datetime.now(UTC) + timedelta(minutes=2))

    async def scenario():
        coordinator = Coordinator()
        async def fallback(scope, receive, send):
            raise AssertionError('unexpected route')
        async def clock():
            return True
        app = LoginStartApplication(fallback, coordinator, maximum_cookie_bytes=4096,
                                    timeout_seconds=3, clock_check=clock)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://app.example') as client:
            denied = await client.get('/v1/auth/start?return_to=%2Fapp', headers={'sec-fetch-site': 'cross-site'})
            assert denied.status_code == 400 and coordinator.calls == 0
            good = await client.get('/v1/auth/start?return_to=%2Fapp', headers={'sec-fetch-site': 'same-origin'})
            assert good.status_code == 303 and good.headers['location'].startswith('https://id.example/')
            assert '__Host-' in good.headers['set-cookie'] and 'Secure' in good.headers['set-cookie']
            assert good.headers['cache-control'] == 'no-store' and coordinator.calls == 1
            invalid = await client.get('/v1/auth/start?return_to=%2Fapp&return_to=%2Fevil',
                                       headers={'sec-fetch-site': 'same-origin'})
            assert invalid.status_code == 400 and coordinator.calls == 1
            switching = await client.get('/v1/auth/start?return_to=%2Fapp&mode=switch',
                                         headers={'sec-fetch-site': 'same-origin'})
            assert switching.status_code == 303 and coordinator.switch is True
    asyncio.run(scenario())


def test_direct_switch_can_be_disabled_until_idp_logout(tmp_path):
    class Coordinator:
        async def begin(self, *args, **kwargs):
            raise AssertionError('direct switch must not begin')
    async def fallback(*args):
        raise AssertionError('unexpected fallback')
    async def clock():
        return True
    async def scenario():
        app = LoginStartApplication(fallback, Coordinator(), maximum_cookie_bytes=4096,
                                    timeout_seconds=3, clock_check=clock, allow_direct_switch=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://app.example') as client:
            response = await client.get('/v1/auth/start?mode=switch', headers={'sec-fetch-site': 'same-origin'})
            assert response.status_code == 400
    asyncio.run(scenario())
