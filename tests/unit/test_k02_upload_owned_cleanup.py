"""Owned input/cache cleanup waits for writers and preserves unrelated paths."""

import asyncio
import json
import threading
from pathlib import Path

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted.upload_operations import UploadOperations

ARGS = {"credential_digest": SecretStr("a" * 64), "workspace_id": "ws1", "source_id": "src1"}


class Sources:
    async def read(self, **kwargs):
        return b"synthetic-input"


def test_repeated_cancel_waits_for_input_writer_before_unlink(tmp_path):
    async def run():
        started, release = threading.Event(), threading.Event()

        class Operations(UploadOperations):
            def _write_input(self, data, path):
                super()._write_input(data, path)
                started.set()
                assert release.wait(5), "Test writer was not released"

        class Worker:
            async def run_upload(self, payload):
                raise AssertionError("Cancelled writer must not invoke worker")

        operations = Operations(Sources(), Worker(), private_root=tmp_path)
        task = asyncio.create_task(operations.preview(**ARGS))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), 3)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert len(list((tmp_path / "inputs").glob("*.zip"))) == 1
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert list((tmp_path / "inputs").iterdir()) == []
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_failed_worker_removes_only_its_owned_input_and_materialization(tmp_path):
    retained = tmp_path / "materializations" / "retained"
    retained.mkdir(parents=True)
    (retained / "sentinel").write_text("keep", encoding="utf-8")

    class Worker:
        async def run_upload(self, payload):
            path = Path(json.loads(payload)["source_path"])
            scratch = tmp_path / "materializations" / path.stem
            scratch.mkdir()
            (scratch / "partial").write_bytes(b"partial-work")
            raise RuntimeError("synthetic worker failure")

    operations = UploadOperations(Sources(), Worker(), private_root=tmp_path)
    with pytest.raises(RuntimeError, match="synthetic worker failure"):
        asyncio.run(operations.preview(**ARGS))
    assert list((tmp_path / "inputs").iterdir()) == []
    assert list((tmp_path / "materializations").iterdir()) == [retained]
    assert (retained / "sentinel").read_text(encoding="utf-8") == "keep"
    with pytest.raises(ValueError, match="server-owned input path"):
        operations._cleanup_input(retained / "sentinel")
    assert (retained / "sentinel").read_text(encoding="utf-8") == "keep"
