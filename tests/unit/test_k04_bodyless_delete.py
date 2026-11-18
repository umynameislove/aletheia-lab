"""Bodyless invite revocation still validates actual bytes and current CSRF proof."""
import pytest
from test_k04_workspace_http import BASE, scenario


@pytest.mark.parametrize("declared", [None, b"0"])
def test_empty_delete_without_content_type_reads_actual_body_before_effect(tmp_path, declared):
    messages, calls, reads = scenario(tmp_path, method="DELETE", path=BASE + "/invitations/inv1",
        raw=b"", omit_media=True, declared_length=declared)
    assert messages[0]["status"] == 200 and calls[0][0] == "revoke_invite" and reads


@pytest.mark.parametrize("body,declared,status", [(b"{}", None, 415), (b"x", b"0", 400),
    (b"x" * 4097, None, 413)])
def test_missing_media_cannot_hide_nonempty_or_oversized_delete_body(tmp_path, body, declared, status):
    messages, calls, reads = scenario(tmp_path, method="DELETE", path=BASE + "/invitations/inv1",
        raw=body, omit_media=True, declared_length=declared)
    assert messages[0]["status"] == status and not calls


@pytest.mark.parametrize("events,status", [
    ([{"type": "http.request", "body": b"", "more_body": True},
      {"type": "http.request", "body": b"{}", "more_body": False}], 415),
    ([{"type": "http.request", "body": b"", "more_body": True}] * 11, 413),
    ([{"type": "http.disconnect"}], 400),
    ([{"type": "http.request", "body": "invalid", "more_body": False}], 400),
])
def test_bodyless_delete_stream_is_bounded_and_validated(tmp_path, events, status):
    messages, calls, reads = scenario(tmp_path, method="DELETE", path=BASE + "/invitations/inv1",
        raw=b"", omit_media=True, body_events=events)
    assert messages[0]["status"] == status and not calls and reads


@pytest.mark.parametrize("extra", [((b"If-Match", b'"1"'),), ((b"content-length", b"0"),)])
def test_duplicate_cas_or_body_length_never_reaches_invite_effect(tmp_path, extra):
    messages, calls, _ = scenario(tmp_path, method="DELETE", path=BASE + "/invitations/inv1",
        raw=b"", revision=b'"1"', extra_headers=extra)
    assert messages[0]["status"] in (400, 403) and not calls


@pytest.mark.parametrize("values", [{"valid_csrf": False}, {"origin": b"https://foreign.example.invalid"}])
def test_bodyless_delete_keeps_origin_and_csrf_proof_checks(tmp_path, values):
    messages, calls, reads = scenario(tmp_path, method="DELETE", path=BASE + "/invitations/inv1",
        raw=b"", omit_media=True, **values)
    assert messages[0]["status"] == 403 and not calls and not reads
