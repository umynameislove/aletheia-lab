"""Offline concurrency checks for the ASGI body reader, without an HTTP server."""

from __future__ import annotations

import asyncio

import pytest

from aletheia_lab.hosted.request_boundary import BodyLimits, read_json_body

HEADERS = [(b"content-type", b"application/json")]
LIMITS = BodyLimits(1024, 1.0, 2.0, 8)


def test_slow_body_does_not_block_another_body_on_the_same_event_loop():
    async def run():
        slow_waiting = asyncio.Event()
        finish_slow = asyncio.Event()
        calls = 0

        async def slow_receive():
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"type": "http.request", "body": b'{"slow":', "more_body": True}
            slow_waiting.set()
            await finish_slow.wait()
            return {"type": "http.request", "body": b"true}", "more_body": False}

        async def fast_receive():
            return {"type": "http.request", "body": b'{"fast":true}', "more_body": False}

        slow = asyncio.create_task(read_json_body(slow_receive, HEADERS, LIMITS))
        try:
            await asyncio.wait_for(slow_waiting.wait(), 1.0)
            fast = await asyncio.wait_for(read_json_body(fast_receive, HEADERS, LIMITS), 1.0)
            assert fast == b'{"fast":true}'
            assert not slow.done()
            finish_slow.set()
            assert await slow == b'{"slow":true}'
        finally:
            if not slow.done():
                slow.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await slow

    asyncio.run(run())


def test_cancelling_a_waiting_body_does_not_cancel_another_reader():
    async def run():
        waiting = asyncio.Event()

        async def slow_receive():
            waiting.set()
            await asyncio.Event().wait()

        async def fast_receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        slow = asyncio.create_task(read_json_body(slow_receive, HEADERS, LIMITS))
        await asyncio.wait_for(waiting.wait(), 1.0)
        slow.cancel()
        with pytest.raises(asyncio.CancelledError):
            await slow
        assert await asyncio.wait_for(read_json_body(fast_receive, HEADERS, LIMITS), 1.0) == b"{}"

    asyncio.run(run())
