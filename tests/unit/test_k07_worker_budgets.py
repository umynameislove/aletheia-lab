import asyncio
import sys
from pathlib import Path

import pytest

from aletheia_lab.hosted.worker_limits import MAX_OUTPUT_BYTES, MAX_UPLOAD_OUTPUT_BYTES
from aletheia_lab.hosted.worker_supervisor import (
    ReportWorkerSupervisor,
    UploadWorkerSupervisor,
    WorkerRejected,
)


def test_upload_output_budget_is_separate_and_both_supervisors_fail_closed(tmp_path, monkeypatch):
    async def run():
        output = b"four"

        class Input:
            def write(self, payload):
                pass

            async def drain(self):
                pass

            def close(self):
                pass

        class Process:
            returncode = 0

            def __init__(self):
                self.stdin = Input()
                self.stdout = asyncio.StreamReader()
                self.stdout.feed_data(output)
                self.stdout.feed_eof()
                self.stderr = asyncio.StreamReader()
                self.stderr.feed_eof()

            async def wait(self):
                return self.returncode

            async def communicate(self, payload=None):
                return await self.stdout.read(), await self.stderr.read()

        async def spawn(*args, **kwargs):
            return Process()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        arguments = dict(python=Path(sys.executable).absolute(), source_root=tmp_path, slots=1, timeout_seconds=5)
        report = ReportWorkerSupervisor(**arguments)
        upload = UploadWorkerSupervisor(**arguments, private_root=tmp_path)
        assert report._output_limit_for(b"{}") == MAX_OUTPUT_BYTES == 32 * 1024 * 1024
        assert upload._output_limit_for(b'{"operation":"confirm"}') == MAX_UPLOAD_OUTPUT_BYTES == 64 * 1024 * 1024
        for operation in ("preview", "analyze", "follow_up", "refresh"):
            assert upload._output_limit_for(f'{{"operation":"{operation}"}}'.encode()) == MAX_OUTPUT_BYTES
        import aletheia_lab.hosted.worker_supervisor as supervisor
        monkeypatch.setattr(supervisor, "MAX_OUTPUT_BYTES", 3)
        monkeypatch.setattr(supervisor, "MAX_UPLOAD_OUTPUT_BYTES", 5)
        try:
            with pytest.raises(WorkerRejected, match="worker_failed"):
                await report.render("json", b"{}")
            assert await upload.run_upload(b'{"operation":"confirm"}') == b"four"
            with pytest.raises(WorkerRejected, match="worker_failed"):
                await upload.run_upload(b'{"operation":"preview"}')
            output = b"sixsix"
            with pytest.raises(WorkerRejected, match="worker_failed"):
                await upload.run_upload(b'{"operation":"confirm"}')
        finally:
            await report.aclose()
            await upload.aclose()

    asyncio.run(run())
