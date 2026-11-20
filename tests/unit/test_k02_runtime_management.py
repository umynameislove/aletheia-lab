"""Existing-table metadata/policy transactions with a synthetic SQL connection."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic import SecretStr, ValidationError

import aletheia_lab.hosted.runtime_management as module
from aletheia_lab.hosted.authorization import Authorization, AuthorizationDenied
from aletheia_lab.hosted.runtime_management import PolicyRejected, RuntimeManagement
from aletheia_lab.hosted.workspace_management import WorkspaceConflict, WorkspaceDenied
from aletheia_lab.hosted_contract.runtime_management import (
    ChangeProject,
    ChangeWorkspaceSettings,
    WorkspacePolicyLimits,
)

VERSION = "product-api/v1-draft"
PROJECT = "p3-project-" + "a" * 64
DIGEST = SecretStr("d" * 64)


class Cursor:
    def __init__(self, row=None): self.row = row
    async def fetchone(self): return self.row


class Connection:
    autocommit = False

    def __init__(self):
        self.revision, self.name, self.deleted = 3, None, False
        self.settings_revision, self.upload, self.workspace = 0, None, None
        self.queries = []
        self.snapshot = "upload-snapshot-" + "b" * 64

    async def execute(self, query, params=()):
        self.queries.append((query, params))
        if query.startswith("SELECT p.revision,p.display_name"):
            return Cursor((self.revision, self.name, self.deleted, self.snapshot, None))
        if query.startswith("SELECT revision FROM k05_projects"):
            return Cursor((self.revision,))
        if query.startswith("UPDATE k05_projects"):
            self.name, self.revision = params[0], self.revision + 1
            return Cursor((self.revision,))
        if query.startswith("SELECT settings_revision") and "LIMIT 0" not in query:
            return Cursor((self.settings_revision, self.upload, self.workspace))
        if query.startswith("UPDATE j01_workspaces"):
            self.upload, self.workspace = params[:2]
            self.settings_revision += 1
            return Cursor((self.settings_revision,))
        return Cursor()


class Policy:
    def __init__(self, *, role="owner", denied=False):
        self.role, self.denied, self.calls = role, denied, []

    async def authorize(self, conn, digest, workspace, action):
        self.calls.append(("workspace", action))
        if workspace != "ws1" or self.denied:
            raise AuthorizationDenied()
        return Authorization("u1", self.role, 1, workspace)

    async def authorize_project(self, conn, auth, workspace, project, action, *, allow_deleted=False):
        self.calls.append(("project", action, allow_deleted))
        if project != PROJECT or conn.deleted and not allow_deleted or self.denied:
            raise AuthorizationDenied()


@pytest.fixture
def system(monkeypatch):
    conn, policy = Connection(), Policy()

    class Pool:
        @asynccontextmanager
        async def connection(self):
            before = conn.revision, conn.name, conn.settings_revision, conn.upload, conn.workspace
            try:
                yield conn
            except BaseException:
                conn.revision, conn.name, conn.settings_revision, conn.upload, conn.workspace = before
                raise

    async def session(connection, digest):
        connection.queries.append(("CURRENT SESSION", ()))
        return "u1"

    monkeypatch.setattr(module, "current_session", session)
    repo = RuntimeManagement(Pool(), platform_limits=WorkspacePolicyLimits(maximum_upload_bytes=100,
        maximum_workspace_bytes=1000), audit=True)
    repo._policy = policy
    return SimpleNamespace(repo=repo, conn=conn, policy=policy)


@pytest.mark.parametrize("deleted,state", [(False, "active"), (True, "deleting")])
def test_compact_project_read_preserves_current_refs_and_deleted_read_policy(system, deleted, state):
    system.conn.deleted = deleted
    value = asyncio.run(system.repo.project(credential_digest=DIGEST, workspace_id="ws1", project_id=PROJECT))
    assert value.revision == 3 and value.state == state and value.current_snapshot_id == system.conn.snapshot
    assert value.current_result_id is None and value.display_name is None
    assert system.policy.calls == [("workspace", "read"), ("project", "read", True)]
    assert not any(query.startswith("UPDATE") for query, _ in system.conn.queries)


def test_project_metadata_cas_changes_no_immutable_record_and_audit_has_no_name(system):
    request = ChangeProject(schema_version=VERSION, display_name="Renamed project")
    value = asyncio.run(system.repo.update_project(credential_digest=DIGEST, workspace_id="ws1",
        project_id=PROJECT, expected_revision=3, request=request))
    assert value.revision == 4 and value.display_name == "Renamed project"
    writes = [(query, params) for query, params in system.conn.queries if query.startswith(("UPDATE", "INSERT"))]
    assert len(writes) == 2 and writes[0][0].startswith("UPDATE k05_projects SET display_name=")
    assert writes[1][0].startswith("INSERT INTO k06_action_audit")
    assert request.display_name not in writes[1][1]
    assert ("project", "update", False) in system.policy.calls


@pytest.mark.parametrize("options,error", [({"revision": 2}, WorkspaceConflict),
    ({"denied": True}, AuthorizationDenied), ({"deleted": True}, AuthorizationDenied)])
def test_stale_or_revoked_or_deleted_project_has_no_metadata_effect(system, options, error):
    system.policy.denied = options.get("denied", False)
    system.conn.deleted = options.get("deleted", False)
    with pytest.raises(error):
        asyncio.run(system.repo.update_project(credential_digest=DIGEST, workspace_id="ws1", project_id=PROJECT,
            expected_revision=options.get("revision", 3), request=ChangeProject(schema_version=VERSION, display_name="New")))
    assert system.conn.revision == 3 and system.conn.name is None
    assert not any(query.startswith(("UPDATE", "INSERT")) for query, _ in system.conn.queries)


def test_settings_expose_effective_platform_policy_and_reduced_override(system):
    before = asyncio.run(system.repo.settings(credential_digest=DIGEST, workspace_id="ws1"))
    assert (before.maximum_upload_bytes, before.maximum_workspace_bytes, before.provenance) == (100, 1000, "platform_default")
    assert before.retention_policy == "retained_no_purge_schedule" and before.source_policy == "zip_bundle_only"
    value = asyncio.run(system.repo.update_settings(credential_digest=DIGEST, workspace_id="ws1", expected_revision=0,
        request=ChangeWorkspaceSettings(schema_version=VERSION, maximum_upload_bytes=50)))
    assert (value.revision, value.maximum_upload_bytes, value.maximum_workspace_bytes) == (1, 50, 1000)
    assert value.provenance == "workspace_override"
    queries = [query for query, _ in system.conn.queries]
    assert queries.index("CURRENT SESSION") < next(i for i, query in enumerate(queries) if query.endswith("FOR UPDATE"))


@pytest.mark.parametrize("change,error", [
    ({"maximum_upload_bytes": 101}, PolicyRejected), ({"maximum_workspace_bytes": 1001}, PolicyRejected),
    ({"maximum_workspace_bytes": 20}, PolicyRejected), ({"expected_revision": 1}, WorkspaceConflict),
])
def test_settings_cannot_exceed_platform_or_lose_revision(system, change, error):
    change = dict(change)
    revision = change.pop("expected_revision", 0)
    change = change or {"maximum_upload_bytes": 50}
    with pytest.raises(error):
        asyncio.run(system.repo.update_settings(credential_digest=DIGEST, workspace_id="ws1", expected_revision=revision,
            request=ChangeWorkspaceSettings(schema_version=VERSION, **change)))
    assert system.conn.settings_revision == 0 and system.conn.upload is None
    assert not any(query.startswith(("UPDATE", "INSERT")) for query, _ in system.conn.queries)


@pytest.mark.parametrize("role", ["analyst", "reviewer", "viewer"])
def test_settings_read_requires_current_settings_manager_permission(system, role):
    system.policy.role = role
    with pytest.raises(WorkspaceDenied):
        asyncio.run(system.repo.settings(credential_digest=DIGEST, workspace_id="ws1"))


def test_invalid_stored_project_metadata_fails_closed(system):
    system.conn.name = "password=private_marker_do_not_disclose"
    with pytest.raises(ValidationError):
        asyncio.run(system.repo.project(credential_digest=DIGEST, workspace_id="ws1", project_id=PROJECT))
