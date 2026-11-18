"""Reject malformed management commands before allocating database work."""

import asyncio

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted.workspace_management import (
    WorkspaceDenied,
    WorkspaceManagement,
    provision_identity,
)
from aletheia_lab.hosted.workspace_migration import WorkspaceMigrationRejected, migrate
from aletheia_lab.hosted_auth.repositories import ValidatedIdentity


class NoDatabase:
    autocommit = False

    def connection(self):
        raise AssertionError("Rejected command must not acquire a database connection")

    async def execute(self, *args):
        raise AssertionError("Rejected command must not execute SQL")


@pytest.mark.parametrize("name", ["", " ", "x" * 257, None])
def test_invalid_workspace_name_has_no_side_effect(name):
    with pytest.raises(ValueError):
        asyncio.run(WorkspaceManagement(NoDatabase()).create(credential_digest=SecretStr("a" * 64), name=name))


@pytest.mark.parametrize("role,expiry", [("owner", 600), ("viewer", 0), ("admin", True), ("reviewer", 604801)])
def test_invitation_cannot_mint_owner_or_unbounded_authority(role, expiry):
    with pytest.raises(ValueError):
        asyncio.run(WorkspaceManagement(NoDatabase()).invite(credential_digest=SecretStr("a" * 64),
            workspace_id="ws", user_id="user", role=role, expires_in_seconds=expiry))


@pytest.mark.parametrize("schema", ["public", "pg_catalog", "bad;DROP TABLE x", "x.y", "", "information_schema"])
def test_migration_rejects_unsafe_or_shared_namespace(schema):
    with pytest.raises(WorkspaceMigrationRejected):
        asyncio.run(migrate(NoDatabase(), schema))


@pytest.mark.parametrize("verified,issuer", [(False, "https://issuer.example.invalid"), (True, "https://other.example.invalid")])
def test_operator_provisioning_requires_verified_exact_issuer(verified, issuer):
    identity = ValidatedIdentity(issuer=issuer, subject=SecretStr("synthetic-subject"),
                                 email=SecretStr("synthetic@example.invalid"), email_verified=verified)
    with pytest.raises(WorkspaceDenied):
        asyncio.run(provision_identity(NoDatabase(), identity, expected_issuer="https://issuer.example.invalid",
                                        display_name="Synthetic"))


@pytest.mark.parametrize("actions,revision", [(["owner"], 0), (["delete"], 0), (["read", "read"], 0),
                                             (["read"], True), (["read"], -1), (["read"], 9223372036854775807)])
def test_grant_input_cannot_escalate_or_bypass_revision(actions, revision):
    with pytest.raises(ValueError):
        asyncio.run(WorkspaceManagement(NoDatabase()).set_source_grant(credential_digest=SecretStr("a" * 64),
            workspace_id="ws", source_id="src", user_id="user", actions=actions, expected_revision=revision))
