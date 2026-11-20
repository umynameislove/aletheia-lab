import asyncio
import http.client
import ssl
import sys
from contextlib import suppress
from ipaddress import IPv4Address
from pathlib import Path

import uvicorn
from aletheia_lab.hosted.edge import EdgeGuard
from aletheia_lab.hosted.static_assets import StaticAssets

if len(sys.argv) != 3:
    raise SystemExit("Usage: python serve-lan.py <Windows-LAN-IPv4> <MacBook-LAN-IPv4>")
try:
    local, peer = map(IPv4Address, sys.argv[1:])
except ValueError as error:
    raise SystemExit(str(error)) from error
if local.is_unspecified or peer.is_unspecified or not (local.is_private and peer.is_private):
    raise SystemExit("Use specific private IPv4 addresses.")
HOST = str(local)
PEERS = {HOST, str(peer)}
ORIGIN = "https://localhost:18776"
PRIVATE = Path("C:/t/aletheia-k14-staging-da2093353a73")
DIST = Path("C:/t/j02-final-20261116/ui/dashboard/web/dist")
CERTS = ssl.create_default_context(cafile=str(PRIVATE / "ca.pem"))
SLOTS = asyncio.Semaphore(16)
HOP = {b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
       b"te", b"trailer", b"transfer-encoding", b"upgrade"}
LIMIT = 1024 * 1024

def request(scope, data):
    target = scope["raw_path"].decode("ascii")
    query = scope["query_string"].decode("ascii")
    if query:
        target += "?" + query
    conn = http.client.HTTPSConnection("127.0.0.1", 19676, context=CERTS, timeout=12)
    try:
        conn.putrequest(scope["method"], target, skip_host=True, skip_accept_encoding=True)
        for name, value in scope["headers"]:
            if name not in HOP | {b"content-length"}:
                conn.putheader(name, value)
        if data or scope["method"] == "POST":
            conn.putheader("Content-Length", str(len(data)))
        conn.putheader("Connection", "close")
        conn.endheaders(data)
        response = conn.getresponse()
        content = response.read(8 * LIMIT + 1)
        if len(content) > 8 * LIMIT:
            raise ValueError("response exceeds smoke test limit")
        headers = [(name.lower().encode("ascii"), value.encode("latin-1"))
                   for name, value in response.getheaders()
                   if name.lower().encode("ascii") not in HOP]
        return response.status, headers, content
    finally:
        conn.close()

async def proxy(scope, receive, send):
    data = bytearray()
    try:
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            data.extend(event.get("body", b""))
            if len(data) > LIMIT:
                break
            if not event.get("more_body"):
                break
        if len(data) > LIMIT:
            status, headers, body = 413, [(b"cache-control", b"no-store")], b""
        else:
            status, headers, body = await asyncio.to_thread(request, scope, bytes(data))
    except (OSError, http.client.HTTPException, ValueError):
        status, headers, body = 502, [(b"cache-control", b"no-store")], b""
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})

assets = StaticAssets(EdgeGuard(ORIGIN, proxy), DIST, ORIGIN)
async def application(scope, receive, send):
    if scope["type"] == "http" and scope["client"][0] in PEERS:
        await assets(scope, receive, send)
    else:
        await send({"type": "http.response.start", "status": 403, "headers": []})
        await send({"type": "http.response.body", "body": b""})

async def pipe(source, target):
    while data := await asyncio.wait_for(source.read(65536), 45):
        target.write(data)
        await asyncio.wait_for(target.drain(), 10)
    if target.can_write_eof():
        target.write_eof()

async def idp(reader, writer):
    upstream = None
    try:
        if writer.get_extra_info("peername")[0] not in PEERS or SLOTS.locked():
            return
        async with SLOTS:
            remote, upstream = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", 18877), 5)
            async with asyncio.TaskGroup() as group:
                group.create_task(pipe(reader, upstream))
                group.create_task(pipe(remote, writer))
    except (OSError, TimeoutError, ExceptionGroup):
        pass
    finally:
        writer.close()
        if upstream is not None:
            upstream.close()

async def main():
    relay = await asyncio.start_server(idp, HOST, 19777)
    server = uvicorn.Server(uvicorn.Config(
        application, host=HOST, port=19776, ssl_certfile=str(PRIVATE / "api.crt"),
        ssl_keyfile=str(PRIVATE / "api.key"), proxy_headers=False, access_log=False,
        lifespan="off", limit_concurrency=24, log_level="warning",
    ))
    print("Smoke UI and IdP on LAN 19776/19777, 60 minutes", flush=True)
    async with relay:
        with suppress(TimeoutError):
            async with asyncio.timeout(3600):
                await asyncio.gather(server.serve(), relay.serve_forever())

with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
    runner.run(main())


