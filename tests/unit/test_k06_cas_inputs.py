"""Grant validation applies to the same values eventually written after an await."""
import asyncio
from contextlib import asynccontextmanager

import pytest
from pydantic import SecretStr
from test_k04_project_policy import Connection, WorkspaceFacts

from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.workspace_management import WorkspaceManagement


@pytest.mark.parametrize("kind", ["source", "project"])
def test_grant_mutation_freezes_validated_actions_before_pool_wait(kind):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        rows = [("source",), None, None] if kind == "source" else [("creator", 0), None, None, None]
        conn = Connection([*rows, (False,)])  # Legacy profile: no catalog revision extension.

        class Pool:
            @asynccontextmanager
            async def connection(self):
                entered.set()
                await release.wait()
                yield conn

        class Sources(WorkspaceFacts, WorkspaceManagement):
            pass

        actions = ["read", "export"]
        arguments = dict(credential_digest=SecretStr("a" * 64), workspace_id="wa", user_id="viewer",
                         actions=actions, expected_revision=0)
        if kind == "source":
            call = Sources(Pool()).set_source_grant(**arguments, source_id="source")
        else:
            manager = PostgresProjectAccess(Pool())
            manager._workspaces = WorkspaceFacts()
            call = manager.set_grant(**arguments, project_id="project")
        pending = asyncio.create_task(call)
        await entered.wait()
        actions[:] = ["delete"]
        release.set()
        value = await pending
        assert value["actions"] == ["read", "export"]
        grant_writes = [params for query, params in conn.queries if query.startswith('INSERT INTO j01_')]
        assert len(grant_writes) == 1 and grant_writes[0][3] == ["read", "export"]
    asyncio.run(run())
