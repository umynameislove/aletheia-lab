"""Cancelling an upload never admits a second parser while its thread still runs."""
import asyncio
import threading

import pytest

from aletheia_lab.hosted import postgres_sources
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.source_bundle import SourceEntry


@pytest.mark.parametrize("parser_fails", [False, True])
def test_cancelled_parser_retains_slot_until_actual_thread_completion(monkeypatch, parser_fails):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def parse(payload, **budgets):
        calls.append(payload)
        if payload == b"first":
            entered.set()
            assert release.wait(5)
            if parser_fails:
                raise ValueError("synthetic rejected bundle")
        return (SourceEntry("dataset.csv", 1, "a" * 64),)

    monkeypatch.setattr(postgres_sources, "inspect_source_bundle", parse)
    sources = PostgresSources(object(), maximum_upload_bytes=100, maximum_expanded_bytes=200, maximum_entries=2)

    async def run():
        first = asyncio.create_task(sources._inspect(b"first"))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            owned = sources._parse_future
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert owned is not None and not owned.done()
            for _ in range(3):
                with pytest.raises(TimeoutError, match="temporarily busy"):
                    await sources._inspect(b"blocked")
            assert calls == [b"first"]
            assert sources._parse_future is owned
            release.set()
            await asyncio.gather(asyncio.shield(owned), return_exceptions=True)
            accepted = await sources._inspect(b"next")
            assert accepted[0].path == "dataset.csv"
            assert calls == [b"first", b"next"]
        finally:
            release.set()
            await asyncio.gather(first, return_exceptions=True)

    asyncio.run(run())


def test_parser_validation_error_is_preserved_and_releases_slot(monkeypatch):
    def reject(payload, **budgets):
        raise ValueError("synthetic rejected bundle")

    monkeypatch.setattr(postgres_sources, "inspect_source_bundle", reject)
    sources = PostgresSources(object(), maximum_upload_bytes=100, maximum_expanded_bytes=200, maximum_entries=2)

    async def run():
        for _ in range(2):
            with pytest.raises(ValueError, match="synthetic rejected"):
                await sources._inspect(b"bad")

    asyncio.run(run())
