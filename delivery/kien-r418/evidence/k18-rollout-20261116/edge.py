"""TLS passthrough with a readiness check before admitting a replica to the test pool."""

import asyncio
import json
import socket
import ssl
import time
from collections import Counter
from contextlib import suppress
from pathlib import Path

from coordination import read_json, write_json

ROOT = Path("/evidence")
CONFIG = json.loads(Path("/run/config/stage.json").read_text())
TLS = ssl.create_default_context(cafile="/run/config/ca.pem")
HOST = f"localhost:{CONFIG['app_port']}"
READY = ()
ACTIVE = Counter()
ROUTED = Counter()
DOWNLOAD = ""


async def canary(target):
    writer = None
    try:
        async with asyncio.timeout(1.2):
            reader, writer = await asyncio.open_connection(
                target, 18766, ssl=TLS, server_hostname="localhost"
            )
            writer.write(
                f"GET /health/ready HTTP/1.1\r\nHost: {HOST}\r\nConnection: close\r\n\r\n".encode()
            )
            await writer.drain()
            head = await reader.readuntil(b"\r\n\r\n")
            return head.startswith(b"HTTP/1.1 200 ")
    except (OSError, TimeoutError, asyncio.IncompleteReadError):
        return False
    finally:
        if writer:
            writer.close()


async def control():
    global READY, DOWNLOAD
    last = None
    while True:
        plan = read_json(ROOT / "edge-plan.json")
        replicas = plan["replicas"]
        assert 1 <= len(replicas) <= 3 and len(replicas) == len(set(replicas))
        assert set(replicas) <= {"api0", "api1", "api2", "api3"}
        statuses = await asyncio.gather(*(canary(target) for target in replicas))
        READY = tuple(
            target for target, ready in zip(replicas, statuses, strict=True) if ready
        )
        DOWNLOAD = plan["download"]
        current = {
            "serial": plan["serial"],
            "ready": READY,
            "checks": dict(zip(replicas, statuses, strict=True)),
            "active": dict(ACTIVE),
            "routed": dict(ROUTED),
        }
        if current != last:
            write_json(ROOT / "edge-state.json", current)
            if (
                last is None
                or current["serial"] != last["serial"]
                or current["checks"] != last["checks"]
            ):
                with (ROOT / "edge-canaries.jsonl").open("a") as log:
                    log.write(json.dumps({**current, "epoch": time.time()}) + "\n")
        last = current
        await asyncio.sleep(0.25)


async def pipe(reader, writer, slow=False):
    while data := await reader.read(4096 if slow else 65536):
        writer.write(data)
        await writer.drain()
        if slow:
            await asyncio.sleep(0.12)


async def connection(reader, writer, slow=False):
    if not READY or sum(ACTIVE.values()) >= 32 or (slow and DOWNLOAD not in READY):
        writer.close()
        return
    target = DOWNLOAD if slow else READY[sum(ROUTED.values()) % len(READY)]
    ACTIVE[target] += 1
    ROUTED[target] += 1
    upstream = None
    try:
        remote, upstream = await asyncio.open_connection(
            target, 18766, limit=8192 if slow else 65536
        )
        if slow:
            upstream.get_extra_info("socket").setsockopt(
                socket.SOL_SOCKET, socket.SO_RCVBUF, 8192
            )
        transfers = (
            asyncio.create_task(pipe(reader, upstream)),
            asyncio.create_task(pipe(remote, writer, slow)),
        )
        try:
            await asyncio.wait(transfers, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for transfer in transfers:
                transfer.cancel()
            await asyncio.gather(*transfers, return_exceptions=True)
    finally:
        ACTIVE[target] -= 1
        for stream in (upstream, writer):
            if stream:
                stream.close()
                with suppress(OSError):
                    await stream.wait_closed()


async def main():
    normal = await asyncio.start_server(connection, "0.0.0.0", CONFIG["app_port"])
    download = await asyncio.start_server(
        lambda r, w: connection(r, w, True), "0.0.0.0", CONFIG["app_port"] + 1000
    )
    async with normal, download:
        await control()


asyncio.run(main())
