"""A signed continuation is bounded and bound to the exact authorized query."""
import pytest
from itsdangerous.timed import TimestampSigner
from pydantic import SecretStr

from aletheia_lab.hosted.cursors import CursorExpired, CursorInvalid, ScopedCursors

SECRET = SecretStr("synthetic-cursor-key-" + "s" * 43)
BINDING = {"user_id": "owner", "context_id": "ctx", "workspace_id": "workspace",
           "membership_revision": 1, "query": {"limit": 50}}


def test_shared_key_instances_roundtrip_without_process_local_state():
    first, second = ScopedCursors(SECRET), ScopedCursors(SECRET)
    token = first.issue(purpose="workspace_members", binding=BINDING, value={"after": "member_0001"})
    assert second.read(purpose="workspace_members", binding=BINDING, cursor=token) == {"after": "member_0001"}


@pytest.mark.parametrize("key,value", [("user_id", "other"), ("context_id", "other"),
    ("workspace_id", "other"), ("membership_revision", 2), ("membership_revision", True), ("query", {"limit": 200})])
def test_scope_and_native_type_substitution_are_rejected(key, value):
    cursors = ScopedCursors(SECRET)
    token = cursors.issue(purpose="workspace_members", binding=BINDING, value={"after": "member"})
    with pytest.raises(CursorInvalid):
        cursors.read(purpose="workspace_members", binding={**BINDING, key: value}, cursor=token)


def test_purpose_signature_and_key_cannot_be_substituted():
    cursors = ScopedCursors(SECRET)
    token = cursors.issue(purpose="workspace_members", binding=BINDING, value={"after": "member"})
    with pytest.raises(CursorInvalid):
        cursors.read(purpose="catalog_records", binding=BINDING, cursor=token)
    with pytest.raises(CursorInvalid):
        ScopedCursors(SecretStr("other-key-" + "x" * 43)).read(purpose="workspace_members", binding=BINDING, cursor=token)
    with pytest.raises(CursorInvalid):
        cursors.read(purpose="workspace_members", binding=BINDING, cursor="x" + token[1:])


def test_expiry_is_reported_only_after_scope_verification(monkeypatch):
    clock = [1000]
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: clock[0])
    cursors = ScopedCursors(SECRET)
    token = cursors.issue(purpose="workspace_members", binding=BINDING, value={"after": "member"})
    clock[0] = 1301
    with pytest.raises(CursorExpired):
        cursors.read(purpose="workspace_members", binding=BINDING, cursor=token)
    with pytest.raises(CursorInvalid):
        cursors.read(purpose="workspace_members", binding={**BINDING, "workspace_id": "foreign"}, cursor=token)


@pytest.mark.parametrize("value", [{"after": "x" * 513}, {"after": float("nan")}, {"after": ("tuple",)},
    {"after": 2 ** 63}, {"query": {1: "key"}}, {"query": list(range(33))}, {"after": "control\n"}])
def test_unbounded_or_non_json_native_payloads_cannot_be_issued(value):
    with pytest.raises(CursorInvalid):
        ScopedCursors(SECRET).issue(purpose="workspace_members", binding=BINDING, value=value)


@pytest.mark.parametrize("patch", [{"v": True}, {"extra": "field"}, {"value": []}])
def test_valid_signature_does_not_bypass_payload_shape(patch):
    cursors = ScopedCursors(SECRET)
    payload = {"v": 1, "purpose": "workspace_members", "binding": BINDING, "value": {"after": "member"}, **patch}
    token = cursors._serializer.dumps(payload, salt="aletheia-cursor/v1:workspace_members")
    with pytest.raises(CursorInvalid):
        cursors.read(purpose="workspace_members", binding=BINDING, cursor=token)


@pytest.mark.parametrize("cursor", ["", "x" * 4097, "token with space", "\n"])
def test_malformed_cursor_fails_safely(cursor):
    with pytest.raises(CursorInvalid):
        ScopedCursors(SECRET).read(purpose="workspace_members", binding=BINDING, cursor=cursor)
