"""Missing control schema and probe failures never become job mutations."""
import asyncio
from contextlib import asynccontextmanager

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted.authorization import Authorization
from aletheia_lab.hosted.postgres_jobs import PostgresJobs


class OldSchema:
    autocommit = False

    def __init__(self, fail=False):
        self.fail, self.queries = fail, []

    @asynccontextmanager
    async def connection(self):
        yield self

    async def execute(self, query, params=()):
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("private_driver_diagnostic")
        return self

    async def fetchone(self):
        return None


class Access:
    async def _access(self, conn, digest, workspace_id, *, action):
        return Authorization("synthetic", "owner", 1, workspace_id)


@pytest.mark.parametrize("fail", [False, True])
def test_control_readiness_does_not_claim_legacy_or_failed_schema_is_usable(fail):
    assert asyncio.run(PostgresJobs(OldSchema(fail), Access()).controls_ready()) is False


@pytest.mark.parametrize("operation", ["cancel", "retry"])
def test_legacy_control_request_fails_safe_before_job_lookup_or_mutation(operation):
    async def run():
        pool = OldSchema()
        jobs = PostgresJobs(pool, Access())
        kwargs = {"credential_digest": SecretStr("a" * 64), "workspace_id": "ws", "job_id": "job"}
        if operation == "retry":
            kwargs["request_key"] = SecretStr("synthetic-retry-valid-key")
        with pytest.raises(TimeoutError, match="temporarily unavailable"):
            await getattr(jobs, operation)(**kwargs)
        assert not any("INSERT" in query or "UPDATE j01_jobs" in query or "creator_id,operation" in query for query in pool.queries)

    asyncio.run(run())
