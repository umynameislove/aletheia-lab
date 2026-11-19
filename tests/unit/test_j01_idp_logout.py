import asyncio
import json

import pytest
from test_k03_csrf_boundary import _runtime
from test_k03_session_adapter import TOKEN_A, _cookie
from test_k03_session_http import _request, _system

from aletheia_lab.hosted.application import AccessDenied
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted_auth.logout_http import LogoutApplication


def test_local_logout_survives_idp_outage(tmp_path):
    class Sessions:
        calls = 0
        async def logout(self, cookie):
            self.calls += 1
    async def fallback(*args):
        raise AssertionError('unexpected fallback')
    async def clock():
        return True
    async def unavailable():
        raise TimeoutError()
    async def scenario():
        sessions = Sessions()
        # Exercise the route directly; CSRF is covered by the integration suite.
        app = object.__new__(LogoutApplication)
        app._sessions, app._runtime = sessions, _runtime(tmp_path)
        app._limits, app._clock, app._idp_logout_url = BodyLimits(1000, 1, 2, 10), clock, unavailable
        incoming = iter([{'type':'http.request','body':b'{}','more_body':False}])
        output = []
        async def receive(): return next(incoming)
        async def send(message): output.append(message)
        await app._logout({'type':'http','method':'POST','headers':[(b'content-type',b'application/json')]}, receive, send)
        assert sessions.calls == 1
        assert output[0]['status'] == 200 and output[1]['body'] == b'{"logout_url":null}'
        assert len([h for h in output[0]['headers'] if h[0] == b'set-cookie']) == 2
    asyncio.run(scenario())


def test_logout_guards_csrf_and_revokes_before_returning_idp_url(tmp_path):
    async def scenario():
        _, sessions, _, tokens, headers = _system(tmp_path)
        async def fallback(*args):
            raise AssertionError('unexpected fallback')
        async def clock():
            return True
        async def idp_url():
            return 'https://identity.example.invalid/logout'
        app = LogoutApplication(fallback, sessions, tokens, _runtime(tmp_path),
                                body_limits=BodyLimits(1024, 1, 1, 16), clock_check=clock,
                                idp_logout_url=idp_url)
        invalid = [(name, b'bad' if name == b'x-csrf-token' else value) for name, value in headers]
        denied, reads = await _request(app, invalid, path='/v1/auth/logout')
        assert denied[0]['status'] == 403 and not reads
        assert await sessions.resolve_cookie(_cookie(TOKEN_A)) is not None
        result, _ = await _request(app, headers, path='/v1/auth/logout')
        assert result[0]['status'] == 200
        assert json.loads(result[1]['body']) == {'logout_url':'https://identity.example.invalid/logout'}
        with pytest.raises(AccessDenied):
            await sessions.resolve_cookie(_cookie(TOKEN_A))
    asyncio.run(scenario())
