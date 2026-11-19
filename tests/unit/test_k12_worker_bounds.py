"""An oversized report child is terminated before its pipes can grow unbounded."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from aletheia_lab.hosted.worker_supervisor import ReportWorkerSupervisor, WorkerRejected


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_actual_noisy_child_is_killed_reaped_and_slot_recovers(monkeypatch, stream):
    async def run():
        spawn = asyncio.create_subprocess_exec
        children = []

        async def noisy(*args, **kwargs):
            program = f"import sys,time; sys.{stream}.buffer.write(b'x'*200000); sys.{stream}.flush(); time.sleep(10)"
            child = await spawn(sys.executable, "-B", "-c", program, **kwargs)
            children.append(child)
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", noisy)
        root = Path(__file__).resolve().parents[2]
        worker = ReportWorkerSupervisor(python=Path(sys.executable).absolute(), source_root=root / "src",
                                        slots=1, timeout_seconds=5)
        monkeypatch.setattr(worker, "_output_limit_for", lambda _: 65536)
        try:
            with pytest.raises(WorkerRejected, match="worker_failed"):
                await asyncio.wait_for(worker.render("pdf", b"{}"), 3)
            assert len(children) == 1 and children[0].returncode is not None
            await asyncio.sleep(0)
            assert not worker._tasks and not worker._slots.locked()
            monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
            fixture = (root / "src/aletheia_lab/product/synthetic_p6_view.json").read_bytes()
            assert await worker.render("json", fixture)
        finally:
            await worker.aclose()

    asyncio.run(run())
