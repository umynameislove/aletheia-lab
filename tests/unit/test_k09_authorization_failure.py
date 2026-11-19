"""Post-claim authorization failures retain their identity at worker settlement."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from test_k09_execution import Jobs

from aletheia_lab.hosted import upload_job_worker
from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.postgres_jobs import JobClaim
from aletheia_lab.hosted.project_operations import CanonicalProjectOperations


@pytest.mark.parametrize("execution", [False, True], ids=["legacy", "execution"])
@pytest.mark.parametrize("stage", ["prepare", "publication"])
@pytest.mark.parametrize("operation", ["preview", "confirm", "analyze", "follow_up", "refresh"])
def test_authorization_failure_after_claim_is_terminal_and_classified(
    monkeypatch, execution, stage, operation,
):
    jobs = Jobs()
    jobs.execution = execution
    claim = JobClaim("job_auth", "ws_auth", operation,
                     {"source_id": "source_auth", "mapping": {}} if operation == "confirm"
                     else {"source_id": "source_auth"}, SecretStr("a" * 64))
    events = []

    async def acquire(**kwargs):
        assert operation in kwargs["operations"]
        events.append("claim")
        return claim

    async def prepare(*args, **kwargs):
        assert events == ["claim"]
        events.append("prepare")
        if stage == "prepare":
            raise AuthorizationDenied()
        return SimpleNamespace(snapshot=object(), model_dump=lambda **kwargs: {})

    async def publish(**kwargs):
        assert events == ["claim", "prepare"]
        events.append("publication")
        raise AuthorizationDenied()

    operations = object.__new__(CanonicalProjectOperations)
    monkeypatch.setattr(jobs, "claim", acquire)
    for name in ("preview", "prepare_confirm", "prepare_initial_analysis", "prepare_follow_up", "prepare_refresh"):
        monkeypatch.setattr(operations, name, prepare)
    for name in ("complete", "complete_snapshot", "complete_result", "complete_refresh"):
        monkeypatch.setattr(jobs, name, publish, raising=False)

    assert asyncio.run(upload_job_worker.run_one_upload_job(jobs, operations, lease_seconds=30))
    assert events == (["claim", "prepare"] if stage == "prepare"
                      else ["claim", "prepare", "publication"])
    failure, = jobs.failed
    assert failure["claim"] == claim
    assert set(failure) == ({"claim", "lease_digest", "failure_kind"} if execution
                            else {"claim", "lease_digest"})
    assert failure.get("failure_kind") == ("authorization" if execution else None)
    assert not jobs.acknowledged


def test_filesystem_permission_error_keeps_permanent_failure_classification(monkeypatch):
    async def compute(*args):
        raise PermissionError("private filesystem detail")

    monkeypatch.setattr(upload_job_worker, "_run_claim", compute)
    jobs = Jobs()
    assert asyncio.run(upload_job_worker.run_one_upload_job(jobs, None, lease_seconds=30))
    failure, = jobs.failed
    assert set(failure) == {"claim", "lease_digest"}
    assert not jobs.acknowledged


def test_authorization_failure_during_heartbeat_reaps_compute_before_settlement(monkeypatch):
    async def scenario():
        cleaned = asyncio.Event()

        class ReapCheckedJobs(Jobs):
            async def heartbeat(self, **kwargs):
                raise AuthorizationDenied()

            async def fail(self, **kwargs):
                assert cleaned.is_set()
                return await super().fail(**kwargs)

        async def compute(*args):
            try:
                await asyncio.Future()
            finally:
                cleaned.set()

        monkeypatch.setattr(upload_job_worker, "_run_claim", compute)
        jobs = ReapCheckedJobs()
        assert await upload_job_worker.run_one_upload_job(jobs, None, lease_seconds=.03)
        failure, = jobs.failed
        assert failure["failure_kind"] == "authorization"
        assert set(failure) == {"claim", "lease_digest", "failure_kind"}
        assert not jobs.acknowledged
    asyncio.run(scenario())
