"""Owned HTTP serving and parent census, independent of lossy audit transport."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from time import monotonic, perf_counter_ns, sleep
from typing import Any
from unittest.mock import patch
from urllib.request import ProxyHandler, Request, build_opener

from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.module_realization_store import Collector, audit
from aletheia_lab.filesystem import write_new_file


def append(path: Path, value: dict[str, Any]) -> None:
    """Owned private journal, fsynced before the parent advances the workload."""
    with path.open("ab") as stream:
        stream.write(encode(value).encode() + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _deny(*args: Any, **kwargs: Any) -> None:
    raise PermissionError("owned serving worker cannot make outbound connections")


def _control(
    path: str, app: Any, collector: Collector, requests: list[str], server: HTTPServer
) -> dict[str, Any]:
    if path == "/audit":
        started = perf_counter_ns()
        records = (
            []
            if collector.db is None
            else [
                json.loads(row[0])
                for row in collector.db.execute("SELECT payload FROM events ORDER BY seq")
            ]
        )
        return {"audit": audit(records, requests), "query_ns": perf_counter_ns() - started}
    if path == "/stop":
        threading.Thread(target=server.shutdown, daemon=True).start()
    return {"native": app.snapshot()}


def _application(arguments: tuple[Any, ...], binding_mode: str | None) -> Any:
    from aletheia_lab.evaluation.module_realization_source import NativeApplication

    if binding_mode:
        from aletheia_lab.evaluation.module_realization_binding_cache import BindingCacheApplication

        return BindingCacheApplication(*arguments, binding_mode=binding_mode)
    return NativeApplication(*arguments)


def serve(config: dict[str, Any], directory: Path, artifacts: Path) -> None:
    directory.mkdir()
    collector = Collector(directory, config)

    def reference(event: dict[str, Any]) -> None:
        if config.get("binding_mode") and event["kind"] in {"load", "request_entry", "predict"}:
            # Qualification oracle bypasses the candidate cache; not production evidence.
            binding, diagnostics = app.reference_binding()
            event = {**event, "binding": binding, "diagnostics": diagnostics}
        append(directory / "reference.jsonl", event)

    with (
        patch.object(socket.socket, "connect", _deny),
        patch.object(socket.socket, "connect_ex", _deny),
        patch.object(socket, "create_connection", _deny),
    ):
        arguments = (
            json.loads((artifacts / "manifest.json").read_bytes()),
            config["variant"],
            config["repair"],
            collector.emit,
            reference,
        )
        app = _application(arguments, config.get("binding_mode"))
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/load":
                response = app.load(value["label"])
            elif self.path == "/predict":
                requests.append(value["request_id"])
                response = app.predict(value["request_id"], value["x"])
            elif self.path == "/closure":
                event = {"kind": "closure", "requests": list(requests)}
                reference(event)
                collector.emit(event)
                response = {"status": "closed"}
            elif self.path == "/flush":
                collector.flush()
                response = {"status": "flushed"}
            elif self.path in {"/status", "/stop", "/audit"}:
                response = _control(self.path, app, collector, requests, server)
            else:
                raise ValueError("unsupported owned route")
            raw = encode({**response, "collector": collector.status()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    # External connect is blocked; a loopback app server is explicitly in scope.
    with (
        patch.object(socket.socket, "connect", _deny),
        patch.object(socket.socket, "connect_ex", _deny),
        patch.object(socket, "create_connection", _deny),
    ):
        server = HTTPServer(("127.0.0.1", 0), Handler)
        write_new_file(directory / "ready.json", encode({"port": server.server_port}).encode())
        try:
            server.serve_forever(poll_interval=0.02)
        finally:
            server.server_close()
            collector.close()


def _request(port: int, route: str, value: dict[str, Any]) -> tuple[dict[str, Any], int]:
    request = Request(
        f"http://127.0.0.1:{port}/{route}",
        data=encode(value).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = perf_counter_ns()
    with build_opener(ProxyHandler({})).open(request, timeout=15) as response:
        result = json.loads(response.read())
    return result, perf_counter_ns() - started


def _ready(process: subprocess.Popen[bytes], directory: Path) -> int:
    deadline = monotonic() + 45
    while monotonic() < deadline and process.poll() is None:
        path = directory / "ready.json"
        if path.exists():
            return int(json.loads(path.read_bytes())["port"])
        sleep(0.025)
    raise TimeoutError("owned service did not become ready")


def drive(
    command: list[str],
    environment: dict[str, str],
    directory: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Fresh process, persistent collector, serial real HTTP requests; no throughput claim."""
    parent = directory.parent / f"{directory.name}-parent"
    parent.mkdir()
    journal = parent / "client.jsonl"
    rows: list[dict[str, Any]] = []
    process = subprocess.Popen(
        command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    failure = None
    last: dict[str, Any] = {}
    try:
        port = _ready(process, directory)

        def offer(route: str, value: dict[str, Any]) -> None:
            nonlocal last
            append(journal, {"kind": "offer", "route": route, "value": value})
            last, elapsed = _request(port, route, value)
            row = {
                "kind": "response",
                "route": route,
                "value": value,
                "response": last,
                "elapsed_ns": elapsed,
            }
            append(journal, row)
            rows.append(row)

        for label in config["order"]:
            offer("load", {"label": label})
            for index in range(config["requests_per_stage"]):
                ordinal = len([row for row in rows if row["route"] == "predict"])
                offer("predict", {"request_id": f"r-{ordinal:03}", "x": index % 3})
        offer("load", {"label": "C"})
        ordinal = len([row for row in rows if row["route"] == "predict"])
        offer("predict", {"request_id": f"r-{ordinal:03}", "x": 7})
        if config.get("audit_queries"):
            offer("audit", {})
        # Before-flush crash deliberately leaves no terminal/flush acknowledgement.
        if config.get("crash") == "before_flush":
            offer("status", {})
            process.kill()
        else:
            offer("closure", {})
            offer("flush", {})
            if config.get("audit_queries"):
                offer("audit", {})
            offer("status", {})
            if config.get("crash") == "after_ack":
                process.kill()
            else:
                offer("stop", {})
    except (OSError, ValueError, TimeoutError) as exc:
        failure = type(exc).__name__
    finally:
        failure = _finish(process, parent, failure)
    result = {
        "config": config,
        "failure": failure,
        "returncode": process.returncode,
        "last_response": last,
        "response_count": len(rows),
    }
    append(journal, {"kind": "process_exit", "returncode": process.returncode, "failure": failure})
    return result


def _finish(process: subprocess.Popen[bytes], parent: Path, failure: str | None) -> str | None:
    if failure is not None and process.poll() is None:
        process.kill()
    try:
        stdout, stderr = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=10)
        failure = failure or "shutdown_timeout"
    write_new_file(parent / "stdout.log", stdout)
    write_new_file(parent / "stderr.log", stderr)
    return failure
