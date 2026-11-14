"""Scheduler bounds concurrency and propagates storage errors."""
import asyncio

import pytest

from aletheia_lab.hosted import upload_job_worker


def test_scheduler_bounds_slots_and_stops(monkeypatch):
    async def scenario():
        stop = asyncio.Event()
        active = 0
        peak = 0
        completed = 0
        async def fake(jobs, operations, *, lease_seconds):
            nonlocal active, peak, completed
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            completed += 1
            if completed >= 4:
                stop.set()
            return True
        monkeypatch.setattr(upload_job_worker, 'run_one_upload_job', fake)
        await asyncio.wait_for(upload_job_worker.run_upload_scheduler(None, None, stop=stop,
            slots=2, lease_seconds=10, idle_seconds=0.05),timeout=2)
        assert peak == 2 and completed >= 4 and active == 0
    asyncio.run(scenario())


def test_scheduler_fails_if_storage_unavailable(monkeypatch):
    async def broken(jobs, operations, *, lease_seconds):
        raise RuntimeError('database unavailable')
    monkeypatch.setattr(upload_job_worker, 'run_one_upload_job', broken)
    async def scenario():
        with pytest.raises(ExceptionGroup):
            await upload_job_worker.run_upload_scheduler(None,None,stop=asyncio.Event(),
                slots=1,lease_seconds=10,idle_seconds=0.05)
    asyncio.run(scenario())


def test_analysis_claim_is_dispatched_and_published():
    from pydantic import SecretStr

    from aletheia_lab.hosted.postgres_jobs import JobClaim

    class Result:
        def model_dump(self, *, mode):
            return {"schema_version": "p6-product-view/v1"}

    class Jobs:
        async def claim(self, *, lease_digest, lease_seconds, operations):
            assert "analyze" in operations
            return JobClaim("job1", "ws1", "analyze", {"snapshot_id": "snap1", "question": "What changed?"}, SecretStr("a" * 64))
        async def complete(self, *, claim, lease_digest, result):
            assert result["schema_version"] == "p6-product-view/v1"
            return True

    class Operations:
        async def analyze(self, *, credential_digest, workspace_id, snapshot_id, question):
            assert (workspace_id, snapshot_id, question) == ("ws1", "snap1", "What changed?")
            return Result()

    assert asyncio.run(upload_job_worker.run_one_upload_job(Jobs(), Operations(), lease_seconds=10))


def test_lifespan_starts_and_stops_scheduler(tmp_path):
    from contextlib import asynccontextmanager

    from test_k02_runtime_lifecycle import ProbeBackend, _settings

    from aletheia_lab.hosted.composition import ProcessApplication

    events = []
    @asynccontextmanager
    async def resources():
        yield ProbeBackend()

    async def scheduler(stop):
        events.append("started")
        try:
            await stop.wait()
        finally:
            events.append("stopped")

    async def scenario():
        app = ProcessApplication(_settings(tmp_path), resources, scheduler=scheduler)
        incoming = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
        messages = []
        async def receive():
            return next(incoming)
        async def send(message):
            messages.append(message["type"])
        await app({"type": "lifespan"}, receive, send)
        assert messages == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    asyncio.run(scenario())
    assert events == ["started", "stopped"]


def test_analysis_request_requires_snapshot_and_question():
    from pydantic import ValidationError

    from aletheia_lab.hosted.job_http import Submission

    request = Submission.model_validate({"operation": "analyze", "snapshot_id": "snapshot1", "question": "What changed?"})
    assert request.source_id is None
    for value in (
        {"operation": "analyze", "snapshot_id": "snapshot1"},
        {"operation": "analyze", "snapshot_id": "snapshot1", "question": "What changed?", "source_id": "src1"},
        {"operation": "analyze", "snapshot_id": "snapshot1", "question": ""},
    ):
        with pytest.raises(ValidationError):
            Submission.model_validate(value)


def test_clock_failure_after_analysis_never_publishes():
    from pydantic import SecretStr

    from aletheia_lab.hosted.postgres_jobs import JobClaim

    class Jobs:
        async def claim(self, **kwargs):
            return JobClaim("job1", "ws1", "analyze", {"snapshot_id": "snap1", "question": "What changed?"}, SecretStr("a" * 64))
        async def complete(self, **kwargs):
            raise AssertionError("invalid clock must prevent publication")

    class Operations:
        async def analyze(self, **kwargs):
            return object()

    async def scenario():
        checks = iter([True, False])
        async def clock():
            return next(checks)
        with pytest.raises(TimeoutError):
            await upload_job_worker.run_one_upload_job(Jobs(), Operations(), lease_seconds=10, clock_check=clock)
    asyncio.run(scenario())
