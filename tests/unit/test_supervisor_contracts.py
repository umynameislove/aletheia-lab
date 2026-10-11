"""Synthetic process/HTTP boundaries; journal and census remain real."""

from __future__ import annotations

import io
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aletheia_lab.evaluation import module_realization_runtime as runtime
from aletheia_lab.evaluation.module_realization_analysis import _census
from aletheia_lab.evaluation.module_realization_store import read_records


class Process:
    def __init__(self):
        self.returncode = None
        self.kill = Mock(side_effect=self.killed)
        self.communicate = Mock(side_effect=self.completed)

    def killed(self):
        self.returncode = -9

    def completed(self, timeout):
        self.returncode = self.returncode or 0
        return b"owned stdout", b"owned stderr"

    def poll(self):
        return self.returncode


@pytest.mark.parametrize("crash", ["none", "before_flush", "after_ack"])
@pytest.mark.parametrize("queries", [False, True])
def test_supervisor_has_exact_offer_response_census_and_crash_frontier(
    tmp_path, monkeypatch, crash, queries
):
    process = Process()
    starts = []

    def spawn(command, **kwargs):
        assert command == ["synthetic-child"] and kwargs["env"] == {"OWNED": "true"}
        starts.append(command)
        return process

    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    monkeypatch.setattr(runtime, "_ready", lambda child, directory: 1234)
    calls = []

    def request(port, route, value):
        assert port == 1234
        calls.append((route, value))
        return {"status": 200, "collector": {"durable_ack_digests": {}}, "body": {"y": 1}}, 7

    monkeypatch.setattr(runtime, "_request", request)
    config = {
        "order": ["A", "B", "A"],
        "requests_per_stage": 2,
        "crash": crash,
        "audit_queries": queries,
    }
    directory = tmp_path / "cell"
    result = runtime.drive(["synthetic-child"], {"OWNED": "true"}, directory, config)
    rows = read_records(tmp_path / "cell-parent/client.jsonl")
    responses, request_ids = _census(rows, config)
    assert starts == [["synthetic-child"]]
    assert request_ids == [f"r-{index:03}" for index in range(7)]
    assert result["failure"] is None and result["response_count"] == len(responses) == len(calls)
    assert [row["route"] for row in responses].count("load") == 4
    assert all(row["elapsed_ns"] == 7 for row in responses)
    assert (tmp_path / "cell-parent/stdout.log").read_bytes() == b"owned stdout"
    assert rows[-1] == {
        "kind": "process_exit",
        "returncode": -9 if crash != "none" else 0,
        "failure": None,
    }
    routes = [route for route, _ in calls]
    assert ("closure" in routes) is (crash != "before_flush")
    assert ("flush" in routes) is (crash != "before_flush")
    assert ("stop" in routes) is (crash == "none")
    assert routes.count("audit") == (0 if not queries else 1 if crash == "before_flush" else 2)
    assert process.kill.call_count == int(crash != "none")


@pytest.mark.parametrize("failure", [OSError, ValueError, TimeoutError])
def test_failed_response_retains_offer_and_marks_unserved_census(tmp_path, monkeypatch, failure):
    process = Process()
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(runtime, "_ready", lambda *args: 1234)

    def rejected(*args):
        raise failure("synthetic transport failure")

    monkeypatch.setattr(runtime, "_request", rejected)
    config = {"order": ["A", "B", "A"], "requests_per_stage": 2}
    result = runtime.drive(["fixture"], {}, tmp_path / "cell", config)
    rows = read_records(tmp_path / "cell-parent/client.jsonl")
    assert result["failure"] == failure.__name__ and result["response_count"] == 0
    assert rows[0] == {"kind": "offer", "route": "load", "value": {"label": "A"}}
    assert rows[-1]["kind"] == "process_exit"
    with pytest.raises(ValueError, match="unserved"):
        _census(rows, config)
    process.kill.assert_called_once()


@pytest.mark.parametrize("earlier", [None, "earlier_failure"])
def test_shutdown_timeout_keeps_logs_and_existing_failure(tmp_path, earlier):
    process = Process()
    process.communicate.side_effect = [
        subprocess.TimeoutExpired("fixture", 10),
        (b"partial", b"timeout"),
    ]
    assert runtime._finish(process, tmp_path, earlier) == (earlier or "shutdown_timeout")
    assert (tmp_path / "stdout.log").read_bytes() == b"partial"
    assert process.kill.call_count == (2 if earlier else 1)


def test_ready_requires_live_child_and_owned_ready_receipt(tmp_path):
    process = Process()
    (tmp_path / "ready.json").write_bytes(b'{"port":1234}')
    assert runtime._ready(process, tmp_path) == 1234
    process.returncode = 2
    with pytest.raises(TimeoutError, match="did not become ready"):
        runtime._ready(process, tmp_path)


def test_request_encoding_disables_proxies_and_keeps_response_boundary(monkeypatch):
    response = SimpleNamespace(read=lambda: b'{"status":200}')

    class Opened:
        def __enter__(self):
            return response

        def __exit__(self, *args):
            return False

    opened = Mock(return_value=Opened())
    handlers = []
    monkeypatch.setattr(
        runtime,
        "build_opener",
        lambda handler: handlers.append(handler) or SimpleNamespace(open=opened),
    )
    result, elapsed = runtime._request(1234, "predict", {"request_id": "owned", "x": 3})
    request = opened.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:1234/predict"
    assert json.loads(request.data) == {"request_id": "owned", "x": 3}
    assert request.get_method() == "POST" and opened.call_args.kwargs["timeout"] == 15
    assert handlers[0].proxies == {} and result == {"status": 200} and elapsed >= 0


def test_server_dispatch_keeps_reference_closure_and_closes_owned_resources(tmp_path, monkeypatch):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "manifest.json").write_bytes(b"{}")
    collector = SimpleNamespace(
        emit=Mock(), flush=Mock(), close=Mock(), status=lambda: {"durable_ack_digests": {}}
    )
    monkeypatch.setattr(runtime, "Collector", lambda directory, config: collector)
    app = SimpleNamespace(
        load=Mock(return_value={"status": "loaded"}),
        predict=Mock(return_value={"y": 6}),
        snapshot=lambda: {"loads": 1},
    )
    monkeypatch.setattr(runtime, "_application", lambda args, binding_mode: app)
    replies = []
    server = SimpleNamespace(server_port=1234, server_close=Mock())
    controls = []

    def control(path, selected, store, requests, owned_server):
        assert selected is app and store is collector and owned_server is server
        controls.append((path, list(requests)))
        return {"native": app.snapshot()}

    monkeypatch.setattr(runtime, "_control", control)

    def construct(address, handler):
        assert address == ("127.0.0.1", 0)

        def dispatch(**kwargs):
            for route, value in (
                ("/load", {"label": "A"}),
                ("/predict", {"request_id": "r0", "x": 3}),
                ("/closure", {}),
                ("/flush", {}),
                ("/status", {}),
                ("/audit", {}),
                ("/stop", {}),
            ):
                request = object.__new__(handler)
                raw = json.dumps(value).encode()
                request.headers, request.path = {"Content-Length": str(len(raw))}, route
                request.rfile, request.wfile = io.BytesIO(raw), io.BytesIO()
                request.send_response, request.send_header, request.end_headers = (
                    Mock(),
                    Mock(),
                    Mock(),
                )
                request.do_POST()
                request.send_response.assert_called_once_with(200)
                replies.append(json.loads(request.wfile.getvalue()))
            request.path = "/outside-owned-contract"
            request.rfile = io.BytesIO(b"{}")
            request.headers = {"Content-Length": "2"}
            with pytest.raises(ValueError, match="unsupported owned route"):
                request.do_POST()

        server.serve_forever = dispatch
        return server

    monkeypatch.setattr(runtime, "HTTPServer", construct)
    directory = tmp_path / "owned-server"
    runtime.serve({"variant": "fixture", "repair": "none"}, directory, artifacts)
    assert json.loads((directory / "ready.json").read_bytes()) == {"port": 1234}
    assert read_records(directory / "reference.jsonl") == [{"kind": "closure", "requests": ["r0"]}]
    app.load.assert_called_once_with("A")
    app.predict.assert_called_once_with("r0", 3)
    collector.flush.assert_called_once()
    collector.close.assert_called_once()
    server.server_close.assert_called_once()
    assert controls == [(route, ["r0"]) for route in ("/status", "/audit", "/stop")]
    assert replies[1]["y"] == 6 and all("collector" in reply for reply in replies)
