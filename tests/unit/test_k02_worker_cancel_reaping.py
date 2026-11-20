"""Caller cancellation must not outlive child cleanup or free its worker slot."""

import asyncio
import sys
from pathlib import Path

import pytest

from aletheia_lab.hosted.worker_supervisor import ReportWorkerSupervisor, WorkerRejected


@pytest.mark.parametrize("repeated_cancel", [False, True])
def test_cancelled_render_waits_for_reaping_even_after_second_cancel(tmp_path, repeated_cancel):
    async def run():
        started, cleanup_started, release_cleanup, cleanup_finished = (
            asyncio.Event(), asyncio.Event(), asyncio.Event(), asyncio.Event())

        class SlowCleanupSupervisor(ReportWorkerSupervisor):
            async def _execute(self, report_format, payload):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleanup_started.set()
                    await release_cleanup.wait()
                    cleanup_finished.set()

        supervisor = SlowCleanupSupervisor(python=Path(sys.executable).resolve(), source_root=tmp_path,
                                            slots=1, timeout_seconds=30)
        caller = asyncio.create_task(supervisor.render("json", b"synthetic-payload"))
        try:
            await asyncio.wait_for(started.wait(), 1)
            caller.cancel()
            await asyncio.wait_for(cleanup_started.wait(), 1)
            if repeated_cancel:
                caller.cancel()
            await asyncio.sleep(0)
            assert not caller.done(), "Caller returned before child cleanup completed"
            assert not cleanup_finished.is_set()
            with pytest.raises(WorkerRejected, match="worker_busy"):
                await supervisor.render("json", b"second-payload")
            release_cleanup.set()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert cleanup_finished.is_set()
        finally:
            release_cleanup.set()
            await asyncio.gather(caller, return_exceptions=True)
            await supervisor.aclose()

    asyncio.run(run())
