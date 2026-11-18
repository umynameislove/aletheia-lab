"""Current creation hints and runtime capability advertisements, not object grants."""
import asyncio
import hashlib

import pytest
from pydantic import SecretStr
from test_k02_read_backend import _backend

from aletheia_lab.hosted.application import AccessDenied
from aletheia_lab.hosted.upload_runtime import UploadReadBackend
from aletheia_lab.hosted.workspace_management import WorkspaceDenied
from aletheia_lab.hosted_contract.session_capabilities import Membership


@pytest.mark.parametrize("allowed", [False, True])
def test_creation_hint_uses_current_cookie_digest_and_refreshes_instead_of_inferring_onboarding(allowed):
    async def run():
        base, sessions, workspaces = _backend()
        cookie = b"__Host-aletheia_session=" + b"s" * 43
        async def resolve(value):
            return sessions.record if value == cookie else None
        sessions.resolve_cookie = resolve
        seen = []
        class Management:
            async def can_create_workspace(self, *, credential_digest):
                seen.append(credential_digest)
                return allowed if len(seen) == 1 else not allowed
        backend = UploadReadBackend(sessions, workspaces, limits=base._limits, clock=base._clock,
                                    membership_management=Management())
        assert (await backend.load_session(cookie)).can_create_workspace is allowed
        assert (await backend.load_session(cookie)).can_create_workspace is not allowed
        assert seen == [SecretStr(hashlib.sha256(b"aletheia-session-v1\x00" + b"s" * 43).hexdigest())] * 2
    asyncio.run(run())


def test_creation_hint_current_authorization_failure_never_returns_old_session():
    async def run():
        base, sessions, workspaces = _backend()
        async def resolve(value):
            return sessions.record
        sessions.resolve_cookie = resolve
        class Management:
            async def can_create_workspace(self, **kwargs):
                raise WorkspaceDenied()
        backend = UploadReadBackend(sessions, workspaces, limits=base._limits, clock=base._clock,
                                    membership_management=Management())
        with pytest.raises(AccessDenied) as caught:
            await backend.load_session(b"__Host-aletheia_session=" + b"s" * 43)
        assert caught.value.code == "session_expired"
    asyncio.run(run())


@pytest.mark.parametrize("role", ["owner", "admin", "analyst", "reviewer", "viewer"])
def test_catalog_capabilities_match_implemented_role_actions_and_hide_pdf(role):
    async def run():
        base, sessions, workspaces = _backend()
        workspaces.membership = Membership(role=role, revision=9)
        backend = UploadReadBackend(sessions, workspaces, limits=base._limits, clock=base._clock,
                                    catalog_available=True)
        session = await backend.load_session(b"session=synthetic")
        assert session.can_create_workspace is False
        caps = await backend.capabilities(session, "ws_synthetic_001")
        assert "read" in caps.allowed_actions and "export" in caps.allowed_actions
        assert ("review" in caps.allowed_actions) is (role != "viewer")
        assert ("delete" in caps.allowed_actions) is (role in ("owner", "admin", "analyst"))
        assert caps.supported_export_formats == ["json", "markdown"]
        assert caps.features["events.polling"] is True
    asyncio.run(run())
