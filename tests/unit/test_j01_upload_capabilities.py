"""The composed slice advertises implemented operations with current role checks."""
import asyncio

from test_k02_read_backend import _backend

from aletheia_lab.hosted.upload_runtime import UploadReadBackend
from aletheia_lab.hosted_contract.session_capabilities import Membership


def test_upload_actions_follow_current_membership_and_keep_unimplemented_actions_off():
    async def scenario():
        base, sessions, workspaces = _backend()
        backend = UploadReadBackend(sessions, workspaces, limits=base._limits, clock=base._clock)
        session = await backend.load_session(b"session=synthetic")
        for role in ("owner", "admin", "analyst", "reviewer", "viewer"):
            workspaces.membership = Membership(role=role, revision=2)
            value = await backend.capabilities(session, "ws_synthetic_001")
            writer = role in ("owner", "admin", "analyst")
            assert value.allowed_actions == (["import", "analyze"] if writer else [])
            assert value.supported_source_types == (["zip_bundle"] if writer else [])
            assert value.analysis_backends == (["local_evidence"] if writer else [])
            assert value.supported_export_formats == []
            assert value.features == {"mapping.class_unit_v2": True}
    asyncio.run(scenario())
