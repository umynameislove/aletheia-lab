"""Native HTTP cost floor: no observer, reference journal or realization scans.

The separate parent HTTP ledger remains common. Native operation counts/timers
and resident labels are modest control instrumentation, not origin certification.
"""

from __future__ import annotations

import importlib
import json
import math
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from time import perf_counter_ns
from typing import Any
from unittest.mock import patch

from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.module_realization_source import OWNED_MODULES
from aletheia_lab.evaluation.module_realization_source import _sdk as _sdk
from aletheia_lab.filesystem import write_new_file


class NativePlain:
    """One serialized SDK operation per offer, without binding inspection."""

    def __init__(self, artifacts: dict[str, Any], variant: str, repair: str) -> None:
        if variant not in ("collision", "unique") or repair not in ("none", "evict"):
            raise ValueError("fixed owned realization variant/repair required")
        self.artifacts, self.variant, self.repair = artifacts, variant, repair
        self.sdk = _sdk()
        self.lock = threading.RLock()
        self.resident: Any = None
        self.resident_label: str | None = None
        self.resident_load_id: str | None = None
        self.native_load_count = 0
        self.prediction_calls = 0
        self.measurements = {"native_load_ns": 0, "native_predict_ns": 0}

    def load(self, label: str) -> dict[str, Any]:
        with self.lock:
            if label not in ("A", "B", "C"):
                raise ValueError("owned labels A/B/C only")
            if self.repair == "evict":
                for name in OWNED_MODULES:
                    sys.modules.pop(name, None)
                importlib.invalidate_caches()
            path = (
                self.artifacts["invalid_path"]
                if label == "C"
                else self.artifacts["models"][self.variant][label]["path"]
            )
            identity = f"load-{self.native_load_count}"
            self.native_load_count += 1
            started = perf_counter_ns()
            try:
                candidate = self.sdk.load_model(path, suppress_warnings=True)
            except Exception:
                return {"status": 400, "body": {"error": "invalid artifact"}}
            finally:
                self.measurements["native_load_ns"] += perf_counter_ns() - started
            self.resident = candidate
            self.resident_label, self.resident_load_id = label, identity
            return {"status": 200, "body": {"loaded": label}}

    def predict(self, request_id: str, x: float) -> dict[str, Any]:
        with self.lock:
            if not request_id or type(x) not in (int, float) or not math.isfinite(x):
                raise ValueError("owned request identity and finite scalar required")
            if self.resident is None:
                return {"status": 503, "body": {"error": "no resident"}}
            self.prediction_calls += 1
            started = perf_counter_ns()
            try:
                values = self.resident.predict([float(x)])
            finally:
                self.measurements["native_predict_ns"] += perf_counter_ns() - started
            if len(values) != 1 or not math.isfinite(float(values[0])):
                raise ValueError("one finite native response required")
            return {"status": 200, "body": {"y": float(values[0])}}

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "variant": self.variant,
                "repair": self.repair,
                "resident_load_id": self.resident_load_id,
                "resident_label": self.resident_label,
                "native_load_count": self.native_load_count,
                "actual_native_loads": self.native_load_count,
                "loads_attempted": self.native_load_count,
                "prediction_calls": self.prediction_calls,
                "predictions_attempted": self.prediction_calls,
                "measurements": dict(self.measurements),
                "instrumentation": "native-call counts/timers only; no actual-use certification",
            }


def _collector_status() -> dict[str, Any]:
    """Protocol compatibility only; no collector is constructed or offered events."""
    return {
        "offered_events": 0,
        "durable_ack_sequences": [],
        "durable_ack_digests": {},
        "pending_events": 0,
        "dropped_events": 0,
        "duplicate_deliveries": 0,
        "payload_bytes": 0,
        "write_ns": 0,
        "projection_ns": 0,
    }


def _deny(*args: Any, **kwargs: Any) -> None:
    raise PermissionError("owned native cost worker cannot make outbound connections")


def serve(config: dict[str, Any], directory: Path, artifacts: Path) -> None:
    """Same parent-driven loopback workload, no child evidence IO beyond readiness."""
    directory.mkdir()
    with (
        patch.dict(
            os.environ, {"MLFLOW_DISABLE_TELEMETRY": "true", "MLFLOW_ENABLE_ASYNC_LOGGING": "false"}
        ),
        patch.object(socket.socket, "connect", _deny),
        patch.object(socket.socket, "connect_ex", _deny),
        patch.object(socket, "create_connection", _deny),
    ):
        app = NativePlain(
            json.loads((artifacts / "manifest.json").read_bytes()),
            config["variant"],
            config["repair"],
        )

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass

            def do_POST(self) -> None:
                value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/load":
                    response = app.load(value["label"])
                elif self.path == "/predict":
                    response = app.predict(value["request_id"], value["x"])
                elif self.path == "/closure":
                    response = {"status": "closed"}
                elif self.path == "/flush":
                    response = {"status": "flushed"}
                elif self.path in {"/status", "/stop"}:
                    response = {"native": app.snapshot()}
                    if self.path == "/stop":
                        threading.Thread(target=server.shutdown, daemon=True).start()
                else:
                    raise ValueError("unsupported owned native cost route")
                raw = encode({**response, "collector": _collector_status()}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        server = HTTPServer(("127.0.0.1", 0), Handler)
        try:
            write_new_file(directory / "ready.json", encode({"port": server.server_port}).encode())
            server.serve_forever(poll_interval=0.02)
        finally:
            server.server_close()
