"""Member page parsing and agreed invitation/onboarding public errors."""
import json

import pytest
from test_k04_workspace_http import BASE, scenario

from aletheia_lab.hosted.cursors import CursorExpired, CursorInvalid
from aletheia_lab.hosted.workspace_management import WorkspaceInvitationError


def test_member_page_passes_only_bounded_continuation_query(tmp_path):
    messages, calls, reads = scenario(tmp_path, method="GET", path=BASE + "/members",
                                      query=b"limit=75&cursor=synthetic.signed.cursor")
    assert messages[0]["status"] == 200 and not reads
    assert calls[0][1]["limit"] == 75 and calls[0][1]["cursor"] == "synthetic.signed.cursor"
    value = json.loads(messages[1]["body"])
    assert value["next_cursor"] is None and value["total"] is None and value["total_kind"] == "unknown"


@pytest.mark.parametrize("query", [b"limit=0", b"limit=201", b"limit=01", b"limit=true", b"limit=1&limit=2",
    b"cursor=", b"cursor=one&cursor=two", b"cursor=%zz", b"cursor=%0a", b"workspace_id=foreign", b"limit=1&extra=x",
    b"limit=1&cursor=x&extra=y", b"cursor=" + b"x" * 4097])
def test_member_page_rejects_ambiguous_or_foreign_query_before_repository(tmp_path, query):
    messages, calls, reads = scenario(tmp_path, method="GET", path=BASE + "/members", query=query)
    assert messages[0]["status"] == 400 and not calls and not reads


@pytest.mark.parametrize("error,status,code", [(CursorInvalid(), 400, "cursor_invalid"),
    (CursorExpired(), 410, "cursor_expired"),
    (WorkspaceInvitationError(404, "invitation_unavailable"), 404, "invitation_unavailable"),
    (WorkspaceInvitationError(403, "invitation_wrong_account"), 403, "invitation_wrong_account"),
    (WorkspaceInvitationError(410, "invitation_expired"), 410, "invitation_expired")])
def test_agreed_public_errors_preserve_safe_problem_shape(tmp_path, error, status, code):
    messages, _, _ = scenario(tmp_path, error=error)
    value = json.loads(messages[1]["body"])
    assert messages[0]["status"] == status and value["code"] == code
    assert not {"user_id", "email", "workspace_id", "token"} & set(value)


def test_head_member_page_authorizes_and_retains_no_store_without_body(tmp_path):
    messages, calls, reads = scenario(tmp_path, method="HEAD", path=BASE + "/members", query=b"limit=25")
    assert messages[0]["status"] == 200 and len(calls) == 1 and not reads
    assert messages[1]["body"] == b"" and (b"cache-control", b"no-store") in messages[0]["headers"]


