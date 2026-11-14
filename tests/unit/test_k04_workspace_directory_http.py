"""Actual read-route shapes keep directory authority in the existing session."""
import json

import pytest
from pydantic import ValidationError
from test_k04_workspace_http import BASE, GRANT, scenario

from aletheia_lab.hosted_contract.workspaces import (
    InvitationPage,
    InvitationTargetPage,
    LookupInvitationTargets,
    Member,
    Workspace,
    WorkspacePage,
)


@pytest.mark.parametrize("operation,path,model", [
    ("list_workspaces", "/v1/workspaces?limit=25", WorkspacePage),
    ("read_workspace", BASE, Workspace),
    ("list_invitations", BASE + "/invitations?limit=25", InvitationPage),
])
def test_directory_gets_use_existing_cookie_authority_and_safe_pages(tmp_path, operation, path, model):
    route, _, query = path.partition("?")
    messages, calls, reads = scenario(tmp_path, method="GET", path=route, query=query.encode())
    assert messages[0]["status"] == 200 and calls[0][0] == operation and not reads
    model.model_validate_json(messages[1]["body"])
    assert (b"cache-control", b"no-store") in messages[0]["headers"]


def test_source_grant_read_can_return_absent_explicit_grant_without_inventing_authority(tmp_path):
    messages, calls, reads = scenario(tmp_path, method="GET", path=BASE + "/sources/src1/grants/u2",
                                       output={**GRANT, "actions": [], "revision": 0})
    assert messages[0]["status"] == 200 and calls[0][0] == "read_source_grant" and not reads
    value = json.loads(messages[1]["body"])
    assert value["actions"] == [] and value["revision"] == 0


def test_exact_email_target_lookup_requires_explicit_selection_of_returned_identity(tmp_path):
    messages, calls, _ = scenario(tmp_path, method="POST", path=BASE + "/invitation-targets",
                                   body={"email": "same@example.invalid"})
    assert messages[0]["status"] == 200 and calls[0][0] == "lookup_invitation_targets"
    values = calls[0][1]
    assert values["email"] == "same@example.invalid" and values["limit"] == 20
    value = InvitationTargetPage.model_validate_json(messages[1]["body"])
    assert value.workspace_id == "ws1" and value.items[0].user_id == "u2"
    assert not {"role", "token", "email", "subject"} & set(value.items[0].model_dump())


@pytest.mark.parametrize("path", ["/v1/workspaces", BASE, BASE + "/invitations", BASE + "/sources/src1/grants/u2"])
def test_head_directory_reads_keep_authorization_and_headers_without_body(tmp_path, path):
    messages, calls, reads = scenario(tmp_path, method="HEAD", path=path)
    assert messages[0]["status"] == 200 and len(calls) == 1 and not reads and messages[1]["body"] == b""
    assert int(dict(messages[0]["headers"])[b"content-length"]) > 0


@pytest.mark.parametrize("body", [{"email": " same@example.invalid"}, {"email": "domain.example.invalid"},
    {"email": "same@example.invalid", "role": "owner"}, {"email": "same@example.invalid", "limit": 21},
    {"email": "same@example.invalid", "limit": True}])
def test_target_lookup_rejects_implicit_identity_or_authority_fields(tmp_path, body):
    messages, calls, _ = scenario(tmp_path, method="POST", path=BASE + "/invitation-targets", body=body)
    assert messages[0]["status"] == 422 and not calls


def test_pending_invitation_dto_cannot_serialize_raw_token_or_digest(tmp_path):
    messages, _, _ = scenario(tmp_path, method="GET", path=BASE + "/invitations")
    value = json.loads(messages[1]["body"])
    item = value["items"][0]
    for key in ("token", "token_digest"):
        with pytest.raises(ValidationError):
            InvitationPage.model_validate({**value, "items": [{**item, key: "synthetic"}]})


def test_member_name_is_bounded_and_optional_only_for_old_fixtures():
    member = {"schema_version": "product-api/v1-draft", "workspace_id": "ws", "user_id": "u",
              "role": "viewer", "active": True, "revision": 1}
    assert Member.model_validate(member).display_name is None
    assert Member.model_validate({**member, "display_name": "Synthetic"}).display_name == "Synthetic"
    with pytest.raises(ValidationError):
        Member.model_validate({**member, "display_name": "x" * 257})


def test_target_request_cursor_is_bounded_before_issuer_work():
    with pytest.raises(ValidationError):
        LookupInvitationTargets.model_validate({"email": "same@example.invalid", "cursor": "x" * 4097})
