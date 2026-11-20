"""Bounded TLS passthrough for controlled K15 client transport faults."""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from contextlib import suppress


class Edge:
    def __init__(self, replicas, *, idle=20.0):
        self.replicas = replicas
        self.idle = idle
        self.counts = Counter()
        self.active = {}
        self.tasks = set()
        self.hold = asyncio.Event()
        self.held = asyncio.Event()
        self.release = asyncio.Event()
        self.offline = False

    async def __aenter__(self):
        self.server = await asyncio.start_server(self.forward, "127.0.0.1", 0, backlog=128)
        self.port = self.server.sockets[0].getsockname()[1]
        self.url = f"https://127.0.0.1:{self.port}"
        return self

    async def __aexit__(self, *_):
        self.server.close()
        await self.server.wait_closed()
        self.abort()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)

    def abort(self):
        for writer in list(self.active.values()):
            self.counts["forced_abort"] += 1
            writer.transport.abort()

    async def forward(self, reader, writer):
        self.counts["accepted"] += 1
        if self.offline or len(self.active) >= 144:
            self.counts["fault_refused" if self.offline else "limit_refused"] += 1
            writer.transport.abort()
            return
        token = self.counts["accepted"]
        replica = (token - 1) % len(self.replicas)
        self.counts[f"replica_{replica}"] += 1
        self.active[token] = writer
        self.counts["peak_active"] = max(self.counts["peak_active"], len(self.active))
        here = asyncio.current_task()
        self.tasks.add(here)
        upstream = None
        last = time.monotonic()

        async def pipe(source, target, outbound):
            nonlocal last
            while data := await source.read(65536):
                last = time.monotonic()
                if outbound and self.hold.is_set():
                    self.counts["held_tls_bytes"] += len(data)
                    self.held.set()
                    await self.release.wait()
                self.counts["to_client_bytes" if outbound else "to_api_bytes"] += len(data)
                target.write(data)
                await target.drain()

        async def expire():
            while True:
                await asyncio.sleep(min(self.idle, 0.1))
                if time.monotonic() - last >= self.idle:
                    self.counts["idle_timeout"] += 1
                    return

        try:
            remote, upstream = await asyncio.open_connection("127.0.0.1", self.replicas[replica])
            tasks = [
                asyncio.create_task(pipe(reader, upstream, False)),
                asyncio.create_task(pipe(remote, writer, True)),
                asyncio.create_task(expire()),
            ]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            del self.active[token]
            self.tasks.remove(here)
            for stream in (upstream, writer):
                if stream is not None:
                    stream.close()
                    with suppress(OSError):
                        await stream.wait_closed()
