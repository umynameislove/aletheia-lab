import asyncio
import sys
from pathlib import Path

from aletheia_lab.hosted.worker_supervisor import ReportWorkerSupervisor


def test_outer_cancellation_waits_until_the_child_has_been_reaped():
    async def run():
        entered = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()

        class DelayedReap(ReportWorkerSupervisor):
            async def _execute(self, report_format, payload):
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cleaning.set()
                    await release.wait()
                    raise

        worker = DelayedReap(python=Path(sys.executable).resolve(), source_root=Path(__file__).resolve().parent,
                             slots=1, timeout_seconds=5)
        caller = asyncio.create_task(worker.render("json", b"{}"))
        try:
            await entered.wait()
            caller.cancel()
            await cleaning.wait()
            await asyncio.sleep(0)
            # Returning earlier lets UploadOperations unlink an input still held by the child.
            assert not caller.done()
        finally:
            release.set()
            await asyncio.gather(caller, return_exceptions=True)
            await worker.aclose()
    asyncio.run(run())
