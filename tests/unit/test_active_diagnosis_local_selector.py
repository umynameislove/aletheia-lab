"""Local transport failures are not diagnosis wins or authority to execute tools."""

from __future__ import annotations

import json

import pytest

from aletheia_lab.diagnosis.active_local import LocalActionSelector, _parse_proposals


def _provider(actions, **changes):
    value = {
        "system_fingerprint": "pinned",
        "choices": [
            {"finish_reason": "stop", "message": {"content": json.dumps({"actions": actions})}}
        ],
    }
    return value | changes


def test_valid_bounded_proposals_preserve_model_order():
    assert _parse_proposals(
        _provider(["read_model", "read_input"]), "pinned", ["read_input", "read_model"]
    ) == ["read_model", "read_input"]


@pytest.mark.parametrize(
    "actions",
    [
        ["shell"],
        ["../secret"],
        ["read_model"] * 2,
        ["stop", "read_model"],
        ["a"] * 4,
        "read_model",
        [None],
    ],
)
def test_invalid_actions_never_become_executable_tools(actions):
    with pytest.raises(ValueError):
        _parse_proposals(_provider(actions), "pinned", ["read_model"])


def test_runtime_identity_and_finish_boundary_are_checked():
    with pytest.raises(ValueError):
        _parse_proposals(_provider(["stop"], system_fingerprint="other"), "pinned", [])
    value = _provider(["stop"])
    value["choices"][0]["finish_reason"] = "length"
    with pytest.raises(ValueError):
        _parse_proposals(value, "pinned", [])


@pytest.mark.parametrize("port", [0, 80, 65536, True, "8783"])
def test_endpoint_is_owned_loopback_port_not_model_selected_destination(port):
    with pytest.raises(ValueError):
        LocalActionSelector(port=port, expected_fingerprint="pinned")


def test_unexpected_private_fields_are_rejected_before_http():
    selector = LocalActionSelector(port=8783, expected_fingerprint="pinned")
    with pytest.raises(ValueError):
        selector({"reference": "hidden"})
    assert not selector.calls


def test_request_cap_is_not_silently_retried():
    selector = LocalActionSelector(port=8783, expected_fingerprint="pinned", max_requests=1)
    selector.calls.append({"status": "failed"})
    with pytest.raises(RuntimeError):
        selector({})


class _Response:
    status = 200

    def __init__(self, provider):
        self.provider = provider

    def read(self, _):
        return json.dumps(self.provider).encode()


class _Connection:
    def __init__(self, provider):
        self.provider = provider
        self.requested = None
        self.closed = False

    def request(self, method, path, *, body, headers):
        self.requested = (method, path, json.loads(body), headers)

    def getresponse(self):
        return _Response(self.provider)

    def close(self):
        self.closed = True


@pytest.mark.parametrize("invalid", [False, True])
def test_direct_wire_projection_and_failure_are_retained_without_retry(monkeypatch, invalid):
    from aletheia_lab.diagnosis import active_local

    provider = _provider(["shell"] if invalid else ["read_model"])
    connection = _Connection(provider)
    destinations = []

    def factory(host, port, *, timeout):
        destinations.append((host, port, timeout))
        return connection

    monkeypatch.setattr(active_local.http.client, "HTTPConnection", factory)
    selector = LocalActionSelector(port=8783, expected_fingerprint="pinned")
    payload = {
        "catalogue_scope": "finite",
        "hypotheses": [],
        "observations": {},
        "available_actions": ["read_model"],
        "cost_estimates_seconds": {"read_model": 0.1},
        "remaining_seconds": 10,
    }
    if invalid:
        with pytest.raises(RuntimeError):
            selector(payload)
    else:
        assert selector(payload) == ["read_model"]
    assert destinations == [("127.0.0.1", 8783, 90)]
    assert connection.closed and len(selector.calls) == 1
    assert connection.requested[2]["messages"][1]["content"] == json.dumps(
        payload, sort_keys=True, allow_nan=False
    )
    assert selector.calls[0]["status"] == ("failed" if invalid else "completed")
