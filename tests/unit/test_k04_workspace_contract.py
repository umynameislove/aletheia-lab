"""Strict request/response models for K-04, including parent/actor trust boundaries."""
import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from aletheia_lab.hosted_contract.workspaces import (
    AcceptInvitation,
    ChangeGrant,
    ChangeMember,
    CreateInvitation,
    CreateWorkspace,
    Empty,
    Invitation,
    Member,
    MemberPage,
    RevokedInvitation,
    SourceGrant,
    TransferOwner,
    Workspace,
)

VERSION = "product-api/v1-draft"
MEMBER = {"schema_version": VERSION, "workspace_id": "ws1", "user_id": "u1",
          "role": "owner", "active": True, "revision": 0}


@pytest.mark.parametrize("model,value", [
    (CreateWorkspace, {"name": "Synthetic workspace"}),
    (ChangeMember, {"role": "reviewer"}), (ChangeMember, {"active": False}),
    (TransferOwner, {"user_id": "u2"}),
    (CreateInvitation, {"user_id": "u2", "role": "viewer", "expires_in_seconds": 60}),
    (AcceptInvitation, {"token": "a" * 43}), (ChangeGrant, {"actions": ["read", "export"]}),
    (ChangeGrant, {"actions": []}), (Empty, {}),
])
def test_requests_accept_only_explicit_shapes(model, value):
    assert model.model_validate_json(json.dumps(value)) is not None
    assert model.model_json_schema()["additionalProperties"] is False


@pytest.mark.parametrize("model,value", [
    (CreateWorkspace, {"name": "Synthetic", "user_id": "u2"}),
    (CreateWorkspace, {"name": "Synthetic", "role": "owner"}),
    (CreateWorkspace, {"name": "Synthetic", "workspace_id": "ws2"}),
    (CreateWorkspace, {"name": ""}), (ChangeMember, {}),
    (ChangeMember, {"role": None, "active": None}), (ChangeMember, {"active": 1}),
    (ChangeMember, {"role": "superadmin"}),
    (TransferOwner, {"user_id": "u2", "actor_id": "u1"}),
    (CreateInvitation, {"user_id": "u2", "role": "owner"}),
    (CreateInvitation, {"user_id": "u2", "role": "viewer", "expires_in_seconds": 59}),
    (CreateInvitation, {"user_id": "u2", "role": "viewer", "expires_in_seconds": 604801}),
    (CreateInvitation, {"user_id": "u2", "role": "viewer", "expires_in_seconds": True}),
    (AcceptInvitation, {"token": "a" * 42}), (AcceptInvitation, {"token": "a" * 129}),
    (ChangeGrant, {"actions": ["owner"]}), (ChangeGrant, {"actions": ["read"] * 6}),
    (ChangeGrant, {"actions": ["write"]}), (ChangeGrant, {"actions": ["read", "read"]}),
    (Empty, {"force": True}),
])
def test_requests_reject_escalation_implicit_coercion_and_unbounded_values(model, value):
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(value))


def test_invitation_request_secret_is_redacted_in_repr():
    synthetic_token = "synthetic-invite-" + "t" * 43
    value = AcceptInvitation.model_validate_json(json.dumps({"token": synthetic_token}))
    assert value.token.get_secret_value() == synthetic_token
    assert synthetic_token not in repr(value)


@pytest.mark.parametrize("model,value", [
    (Workspace, {"schema_version": VERSION, "workspace_id": "ws1", "workspace_name": "Synthetic",
                 "membership": {"role": "owner", "revision": 0}}),
    (Member, MEMBER),
    (MemberPage, {"schema_version": VERSION, "workspace_id": "ws1", "items": [MEMBER]}),
    (Invitation, {"schema_version": VERSION, "workspace_id": "ws1", "invitation_id": "inv1",
                  "user_id": "u2", "role": "viewer", "expires_at": datetime.now(UTC) + timedelta(hours=1),
                  "token": "a" * 43}),
    (RevokedInvitation, {"schema_version": VERSION, "workspace_id": "ws1", "invitation_id": "inv1", "revoked": True}),
    (SourceGrant, {"schema_version": VERSION, "workspace_id": "ws1", "source_id": "src1",
                   "user_id": "u2", "actions": ["read"], "revision": 2}),
])
def test_producer_response_roundtrip_preserves_scope_and_types(model, value):
    original = model.model_validate(value)
    restored = model.model_validate_json(original.model_dump_json())
    assert restored == original
    with pytest.raises(ValidationError):
        model.model_validate({**value, "client_selected_authority": True})


def test_member_page_rejects_cross_workspace_child_even_with_valid_ids():
    with pytest.raises(ValidationError):
        MemberPage.model_validate({"schema_version": VERSION, "workspace_id": "ws1",
                                   "items": [{**MEMBER, "workspace_id": "ws2"}]})


@pytest.mark.parametrize("change", [{"revision": -1}, {"revision": True}, {"active": 1},
                                    {"schema_version": "product-api/v2-proposal"}, {"role": "root"}])
def test_member_response_rejects_invalid_revision_role_status_or_version(change):
    with pytest.raises(ValidationError):
        Member.model_validate({**MEMBER, **change})


def test_member_page_is_bounded():
    with pytest.raises(ValidationError):
        MemberPage.model_validate({"schema_version": VERSION, "workspace_id": "ws1", "items": [MEMBER] * 201})


def test_invitation_response_expiry_requires_timezone():
    value = {"schema_version": VERSION, "workspace_id": "ws1", "invitation_id": "inv1",
             "user_id": "u2", "role": "viewer", "expires_at": "2026-11-11T12:00:00", "token": "a" * 43}
    with pytest.raises(ValidationError):
        Invitation.model_validate_json(json.dumps(value))
