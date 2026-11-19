"""Injected routes are active only during the owning runtime lifespan."""
import asyncio
from contextlib import asynccontextmanager

from aletheia_lab.hosted.composition import ProcessApplication


def test_routes_are_available_only_during_lifespan(tmp_path):
    from test_k02_runtime_lifecycle import ProbeBackend, _http, _settings

    @asynccontextmanager
    async def resources():
        yield ProbeBackend()

    def routes(fallback):
        async def app(scope, receive, send):
            if scope.get('path') == '/experimental':
                await send({'type':'http.response.start','status':204,'headers':[]})
                await send({'type':'http.response.body','body':b''})
            else:
                await fallback(scope, receive, send)
        return app

    async def run():
        app=ProcessApplication(_settings(tmp_path),resources,routes=routes)
        assert await _http(app,'/experimental')==404
        events=iter([{'type':'lifespan.startup'},{'type':'lifespan.shutdown'}])
        async def receive(): return next(events)
        async def send(message):
            if message['type']=='lifespan.startup.complete':
                assert await _http(app,'/experimental')==204
                assert await _http(app,'/health/ready')==200
        await app({'type':'lifespan'},receive,send)
        assert await _http(app,'/experimental')==404
    asyncio.run(run())
