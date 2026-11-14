"""Persisted lifecycle metadata and typed refresh output never expose private job fields."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr
from test_k02_job_boundary import PATH, PRIVATE, _request, _system
from test_k05_native_upload_records import upload_case

from aletheia_lab.hosted.authorization import Authorization
from aletheia_lab.hosted.job_lifecycle import public_job
from aletheia_lab.hosted.postgres_catalog import CatalogIntegrityError
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import SourceNotFound

NOW = datetime(2026, 11, 11, tzinfo=UTC)


def _metadata(**patch):
    return {"state": "running", "operation": "refresh", "created_at": NOW.isoformat(),
        "deadline_at": (NOW + timedelta(minutes=15)).isoformat(), "attempt_count": 1, "max_attempts": 3,
        "terminal_at": None, "creator_id": "creator", "request": {"private": PRIVATE},
        "credential_digest": PRIVATE, **patch}


def test_public_lifecycle_retains_actual_dates_budget_and_scoped_output_without_private_metadata():
    terminal = NOW + timedelta(minutes=2)
    job = public_job("ws", "job", _metadata(state="succeeded", terminal_at=terminal.isoformat(), attempt_count=3),
                     retryable=False)
    assert (job.operation, job.created_at, job.deadline_at, job.terminal_at, job.attempt, job.max_attempts) == (
        "refresh", NOW, NOW + timedelta(minutes=15), terminal, 3, 3)
    assert job.output_url == "/v1/workspaces/ws/jobs/job/output" and job.retryable is False
    assert PRIVATE not in job.model_dump_json() and "credential_digest" not in job.model_dump_json()


def test_unstarted_and_legacy_metadata_do_not_invent_an_attempt_or_deadline():
    queued = public_job("ws", "job", _metadata(state="queued", attempt_count=0), retryable=False)
    assert queued.attempt is None and queued.max_attempts == 3 and queued.output_url is None
    legacy = public_job("ws", "job", {"state": "running", "operation": "preview"}, retryable=None)
    assert all(getattr(legacy, field) is None for field in (
        "created_at", "deadline_at", "terminal_at", "attempt", "max_attempts", "retryable", "output_url"))


@pytest.mark.parametrize("patch", [{"attempt_count": True}, {"attempt_count": -1}, {"attempt_count": 4},
    {"max_attempts": 0}, {"max_attempts": "3"}, {"deadline_at": (NOW - timedelta(seconds=1)).isoformat()},
    {"terminal_at": NOW.isoformat()}])
def test_corrupt_persisted_lifecycle_cannot_be_serialized_as_valid_status(patch):
    with pytest.raises(ValueError):
        public_job("ws", "job", _metadata(**patch), retryable=False)


class MetadataConnection:
    def __init__(self, value):
        self.value = value

    async def execute(self, query, params=()):
        return self

    async def fetchone(self):
        return (self.value,)


@pytest.mark.parametrize("actor,denied,expected", [("owner", False, True), ("owner", True, False),
                                                 ("viewer", False, False)])
def test_status_retry_hint_uses_current_permission_without_hiding_readable_terminal_truth(actor, denied, expected):
    async def run():
        jobs = PostgresJobs(object(), object())
        jobs._authorize = AsyncMock(side_effect=SourceNotFound() if denied else None)
        metadata = _metadata(state="failed", terminal_at=(NOW + timedelta(seconds=5)).isoformat())
        auth = Authorization(actor, "owner" if actor == "owner" else "viewer", 1, "ws")
        job = await jobs._status_response(MetadataConnection(metadata), "a" * 64, "ws", "job", auth)
        assert job.status == "failed" and job.retryable is expected and job.terminal_at is not None
        assert jobs._authorize.await_count == (0 if actor == "viewer" else 1)
    asyncio.run(run())


def _refresh_result(snapshot, *, changed):
    parent = "upload-snapshot-" + "b" * 64 if changed else snapshot.snapshot_id
    return {"project_id": snapshot.mapping.project_id, "snapshot_id": snapshot.snapshot_id,
            "previous_snapshot_id": parent, "status": "new_snapshot" if changed else "unchanged", "revision": 4 if changed else 3}


@pytest.mark.parametrize("changed", [False, True])
def test_refresh_http_output_returns_only_typed_canonical_metadata(tmp_path, changed):
    snapshot, _ = upload_case(tmp_path)
    app, jobs, headers = _system(tmp_path)
    jobs.result.update(operation="refresh", result=_refresh_result(snapshot, changed=changed))
    status, response_headers, body = _request(app, headers, path=PATH + "/job_boundary/output")
    assert status == 200 and json.loads(body) == jobs.result and response_headers[b"cache-control"] == b"no-store"


@pytest.mark.parametrize("patch", [{"private": PRIVATE}, {"revision": True}, {"project_id": "invalid"},
    {"snapshot_id": "../../private"}, {"previous_snapshot_id": "invalid"}, {"status": "unknown"}])
def test_refresh_http_rejects_corrupt_metadata_without_reflecting_it(tmp_path, patch):
    snapshot, _ = upload_case(tmp_path)
    app, jobs, headers = _system(tmp_path)
    jobs.result.update(operation="refresh", result={**_refresh_result(snapshot, changed=True), **patch})
    status, _, body = _request(app, headers, path=PATH + "/job_boundary/output")
    assert status == 500 and PRIVATE.encode() not in body


class RefreshPool:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.row = None

    @asynccontextmanager
    async def connection(self):
        yield self

    async def execute(self, query, params=()):
        self.row = ((self.snapshot.snapshot_id, self.snapshot.model_dump_json()) if "canonical_record_id" in query
                    else (self.snapshot.source_sha256,))
        return self

    async def fetchone(self):
        return self.row


@pytest.mark.parametrize("patch", [{}, {"project_id": "p3-project-" + "f" * 64},
                                  {"revision": 99}, {"previous_snapshot_id": "upload-snapshot-" + "f" * 64}])
def test_refresh_repository_output_verifies_canonical_snapshot_revision_and_parent(tmp_path, patch):
    async def run():
        snapshot, _ = upload_case(tmp_path)
        pool = RefreshPool(snapshot)
        jobs = PostgresJobs(pool, object())
        catalog = type("Catalog", (), {"read_record": AsyncMock(return_value=snapshot)})()
        jobs._read_catalog = catalog
        jobs._authorize = AsyncMock()
        result = _refresh_result(snapshot, changed=True)
        request = {"project_id": snapshot.mapping.project_id, "parent_snapshot_id": result["previous_snapshot_id"],
                   "expected_revision": 3, "source_id": snapshot.source_id}
        args = dict(credential_digest=SecretStr("a" * 64), workspace_id=snapshot.workspace_id,
                    request=request, result={**result, **patch})
        if patch:
            with pytest.raises(CatalogIntegrityError):
                await jobs._refresh_output(**args)
        else:
            assert await jobs._refresh_output(**args) == result
            jobs._authorize.assert_awaited_once()
    asyncio.run(run())
