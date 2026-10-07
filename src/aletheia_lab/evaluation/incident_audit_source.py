"""CPU reproduction of an upstream session-initializer workflow.

Legal SessionOptions overrides do not change the graph file. The surrounding
request census and incident timetable are study-authored development controls,
not natural operator incidents. Native source/history is an adequate comparator.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_audit import digest, resolve
from aletheia_lab.project.identity import content_sha256

SOURCE_URL = (
    "https://github.com/microsoft/onnxruntime/blob/v1.23.2/onnxruntime/test/python/"
    "onnxruntime_test_python.py#L1162-L1185"
)
GRAPH_SHA256 = "71f431c4e9321ec6fbeb158d02ed240459a7dcc98673fa79a4f439ce42efaf10"


def identity() -> dict[str, Any]:
    """Inspect only package/asset identity, never run an inference."""
    distribution = importlib.metadata.distribution("onnxruntime")
    graph = Path(str(distribution.locate_file("onnxruntime/datasets/mul_1.onnx")))
    if distribution.version != "1.23.2" or graph.is_symlink():
        raise ValueError("explicit native version/regular upstream asset required")
    if content_sha256(graph.read_bytes()) != GRAPH_SHA256:
        raise ValueError("upstream graph asset differs")
    return {
        "package": "onnxruntime",
        "version": distribution.version,
        "graph_sha256": GRAPH_SHA256,
        "graph_bytes": graph.stat().st_size,
        "source_url": SOURCE_URL,
        "source_class": "exposed_upstream_workflow",
        "provider": "CPUExecutionProvider",
        "license": "MIT",
        "binding_source_sha256": content_sha256(
            Path(
                str(
                    distribution.locate_file("onnxruntime/capi/onnxruntime_inference_collection.py")
                )
            ).read_bytes()
        ),
    }


class InitializerWorkload:
    """Explicit runtime arguments and return values, not hidden engine attestation."""

    def __init__(self) -> None:
        np = importlib.import_module("numpy")
        ort = importlib.import_module("onnxruntime")

        self.identity = identity()
        graph = Path(
            str(
                importlib.metadata.distribution("onnxruntime").locate_file(
                    "onnxruntime/datasets/mul_1.onnx"
                )
            )
        )
        self.weights = {
            "aaa": np.array([[1, 2], [3, 4], [5, 6]], dtype=np.float32),
            "bbb": np.array([[2, 1], [4, 3], [6, 5]], dtype=np.float32),
        }
        self.sessions: dict[str, Any] = {}
        self.owners: list[Any] = []
        self.loads: dict[str, dict[str, str]] = {}
        self.load_ns: dict[str, int] = {}
        for model, weights in self.weights.items():
            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            if model == "bbb":
                initializer = ort.OrtValue.ortvalue_from_numpy(weights)
                self.owners.append(initializer)  # SDK requires this lifetime.
                options.add_initializer("W", initializer)
            started = perf_counter_ns()
            self.sessions[model] = ort.InferenceSession(
                str(graph), sess_options=options, providers=["CPUExecutionProvider"]
            )
            self.load_ns[model] = perf_counter_ns() - started
            self.loads[f"session-{model}"] = {
                "model": model,
                "artifact": GRAPH_SHA256,
                "fingerprint": digest(
                    {
                        "graph": GRAPH_SHA256,
                        "W": weights.tolist(),
                        "dtype": "float32",
                        "shape": [3, 2],
                        "provider": "CPUExecutionProvider",
                    }
                ),
            }

    def call(
        self, ordinal: int, *, captured: bool = True, route_repair: bool = False
    ) -> dict[str, Any]:
        """All native operations are offered, including a genuine invalid-shape call."""
        np = importlib.import_module("numpy")

        if type(ordinal) is not int or not 0 <= ordinal < 256:
            raise ValueError("bounded offered request census required")
        model = "aaa" if ordinal % 2 == 0 else "bbb"
        requested = "aaa" if ordinal % 8 == 7 else model
        if route_repair:
            model = requested
        token, generation = f"request-{ordinal:03d}", f"session-{model}"
        values = np.array([[1, 2], [3, 4], [5, 6]], dtype=np.float32)
        if ordinal % 8 == 3 or ordinal == 15:
            values = np.zeros((3, 2), dtype=np.float32)
        if ordinal == 11:
            values = np.ones((2, 2), dtype=np.float32)
        raw_input = values.tolist()
        started = perf_counter_ns()
        output, error = None, None
        try:
            output = self.sessions[model].run(["Y"], {"X": values})[0].tolist()
        except Exception as exc:
            error = type(exc).__name__  # Native errors remain in the census.
        native_ns = perf_counter_ns() - started
        expected = None if error else (values * self.weights[model]).tolist()
        if output != expected:
            raise ValueError("native output differs from independent matrix arithmetic")
        capture_started = perf_counter_ns()
        input_hash, output_hash = digest(raw_input), digest(output) if output is not None else None
        frame: dict[str, Any] = {
            "token": token,
            "requested": requested,
            "kind": "non_batched",
            "input": input_hash,
            "output": output_hash,
            "closed": True,
            "failed": error is not None,
            "loads": {},
            "uses": [],
        }
        if captured and output is not None:
            frame["loads"] = {generation: self.loads[generation]}
            frame["uses"] = [
                {
                    "token": token,
                    "batch": token,
                    "index": 0,
                    "generation": generation,
                    "input": input_hash,
                    "output": output_hash,
                    "fingerprint": self.loads[generation]["fingerprint"],
                }
            ]
        truth = "unknown" if error else "compliant" if requested == model else "violation"
        visible = resolve(frame)
        capture_ns = perf_counter_ns() - capture_started
        return {
            "ordinal": ordinal,
            "frame": frame,
            "reference": truth,
            "native_error": error,
            "native_ns": native_ns,
            "capture_and_live_resolve_ns": capture_ns,
            "strong_source_history": truth,
            "graph_only_effective_model": "unknown",  # Same graph admits both legal sessions.
            "capture_answer": visible,
            "input": raw_input,
            "output": output,
            "actual_session": model,
            "expected_matrix_output": expected,
            "caller_receipt_sha256": digest({"token": token, "output": output, "error": error}),
        }

    def native_floor(self, ordinal: int) -> dict[str, Any]:
        """Same SDK call without request hashing, certificates or archive operations."""
        np = importlib.import_module("numpy")
        model = "aaa" if ordinal % 2 == 0 else "bbb"
        values = np.array([[1, 2], [3, 4], [5, 6]], dtype=np.float32)
        if ordinal % 8 == 3 or ordinal == 15:
            values = np.zeros((3, 2), dtype=np.float32)
        if ordinal == 11:
            values = np.ones((2, 2), dtype=np.float32)
        error, output = None, None
        started = perf_counter_ns()
        try:
            output = self.sessions[model].run(["Y"], {"X": values})[0].tolist()
        except Exception as exc:
            error = type(exc).__name__
        elapsed = perf_counter_ns() - started
        expected = None if error else (values * self.weights[model]).tolist()
        if expected != output:
            raise ValueError("native control output differs")
        return {
            "ordinal": ordinal,
            "native_ns": elapsed,
            "native_error": error,
            "output": output,
            "reference_arithmetic_checked": True,
        }


def source_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    conclusive = [row for row in rows if row["reference"] != "unknown"]
    return {
        "offered_native_calls": len(rows),
        "native_failures": len(rows) - len(conclusive),
        "strong_source_history_correct": sum(
            row["strong_source_history"] == row["reference"] for row in conclusive
        ),
        "captured_correct": sum(row["capture_answer"] == row["reference"] for row in conclusive),
        "captured_unknown": sum(row["capture_answer"] == "unknown" for row in conclusive),
        "captured_false": sum(
            row["capture_answer"] not in {row["reference"], "unknown"} for row in conclusive
        ),
        "controlled_contract_violations": sum(row["reference"] == "violation" for row in rows),
        "reference_arithmetic_checked": True,
        "independent_operator_incident": False,
        "source_cluster_count": 1,
        "request_schedule": "authored adaptation of legal upstream initializer workflow",
        "frame_bytes": sum(len(encode(row["frame"]).encode()) for row in rows),
    }
