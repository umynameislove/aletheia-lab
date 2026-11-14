"""Slow downloads have finite retained buffers and do not occupy metadata admission."""
from __future__ import annotations

import asyncio
import json

from test_k02_durable_exports import BASE, RESULT, Harness


def test_four_held_downloads_reject_fifth_keep_reads_live_and_release_on_cancel(tmp_path):
    async def run():
        harness = Harness(tmp_path, b"x" * 131072)
        descriptor = await harness.create()
        entered, hold = asyncio.Event(), asyncio.Event()
        held = 0

        async def blocked(message):
            nonlocal held
            if message.get("type") == "http.response.body" and message.get("body"):
                held += 1
                if held == 4:
                    entered.set()
                await hold.wait()

        pending = [asyncio.create_task(harness.request(descriptor["download_url"], hook=blocked)) for _ in range(4)]
        try:
            await asyncio.wait_for(entered.wait(), 2)
            busy, error = await harness.request(descriptor["download_url"])
            assert error is None and busy[0]["status"] == 503
            assert json.loads(busy[1]["body"])["code"] == "service_unavailable"
            assert harness.app._download_slots.locked()
            metadata, error = await harness.request(BASE + "/results/" + RESULT)
            assert error is None and metadata[0]["status"] == 200
        finally:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        assert harness.app._download_slots._value == 4
        retry, error = await harness.request(descriptor["download_url"])
        assert error is None and retry[0]["status"] == 200
        assert b"".join(message.get("body", b"") for message in retry[1:]) == harness.renderer.payload

    asyncio.run(run())


def test_denial_and_slow_send_deadline_release_download_permit(tmp_path):
    async def run():
        harness = Harness(tmp_path, b"x" * 131072)
        descriptor = await harness.create()
        harness.state.allowed = False
        denied, error = await harness.request(descriptor["download_url"])
        assert error is None and denied[0]["status"] == 404
        assert harness.app._download_slots._value == 4
        harness.state.allowed = True

        async def blocked(message):
            if message.get("type") == "http.response.body":
                await asyncio.Event().wait()

        messages, error = await harness.request(descriptor["download_url"], hook=blocked)
        assert isinstance(error, TimeoutError)
        assert len([m for m in messages if m["type"] == "http.response.start"]) == 1
        assert messages[-1]["more_body"] is True
        assert harness.app._download_slots._value == 4

    asyncio.run(run())
