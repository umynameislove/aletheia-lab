"""Fresh real MLServer serial REST workload with one collector per process.

Native request timing has no observer/profiler. Post-request reference checks
and fixture replacements are excluded and separately timed, but can perturb
subsequent caches. This is local ASGI latency, not a socket/production SLA.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import sys
from contextlib import nullcontext
from pathlib import Path
from time import perf_counter_ns, process_time_ns
from typing import Any, cast
from unittest.mock import patch

from aletheia_lab.evaluation.model_load_application import _application, atomic_artifact
from aletheia_lab.evaluation.model_load_contract import (
    LoadContract,
    Observation,
    Record,
    Scope,
    receipt_checker,
)
from aletheia_lab.evaluation.model_load_serving_capture import ServingCapture, tree_fingerprint
from aletheia_lab.evaluation.model_load_serving_store import ServingStore
from aletheia_lab.project.identity import content_sha256


def prepare_models(directory: Path) -> dict[str, Any]:
    """Six genuine fresh estimators; B mirrors labels, not padded serialized data."""
    np = importlib.import_module("numpy")
    joblib = importlib.import_module("joblib")
    classifier = importlib.import_module("sklearn.tree").DecisionTreeClassifier
    rng = np.random.default_rng(106)
    features = rng.normal(size=(8192, 8))
    score = (
        features[:, 0]
        + 0.7 * features[:, 1]
        - 0.6 * features[:, 2]
        + 0.9 * np.sin(2 * features[:, 3])
        + 0.6 * features[:, 4] * features[:, 5]
        + rng.normal(scale=0.45, size=8192)
    )
    labels = (score > 0).astype(np.int64)
    probe = rng.normal(size=(12, 8))
    directory.mkdir(exist_ok=False)
    bands: dict[str, Any] = {}
    for depth in (2, 6, 10):
        band: dict[str, Any] = {}
        for name, targets in (("A", labels), ("B", 1 - labels)):
            model = classifier(max_depth=depth, random_state=106).fit(features, targets)
            path = directory / f"{depth}-{name}.joblib"
            if joblib.dump(model, path, compress=0) != [str(path)]:
                raise ValueError("owned estimator must be uncompressed single-file")
            payload = path.read_bytes()
            if not 0 < len(payload) <= 262_144:
                raise ValueError("estimator outside supported artifact bound")
            band[name] = {
                "path": path.name,
                "digest": content_sha256(payload),
                "bytes": len(payload),
                "nodes": int(model.tree_.node_count),
                "fingerprint": tree_fingerprint(model),
                "predictions": model.predict(probe).tolist(),
            }
        if (
            band["A"]["predictions"] == band["B"]["predictions"]
            or band["A"]["fingerprint"] == band["B"]["fingerprint"]
        ):
            raise ValueError("control partners are not prediction-discriminating")
        bands[str(depth)] = band
    return {"bands": bands, "probe": probe.tolist(), "fits": 6}


def decide(frame: dict[str, Any], parent: dict[str, Any] | None = None) -> dict[str, Any]:
    """S uses only admitted durable evidence, not the independent reference."""
    scope = Scope(frame["scope"], 0)
    contract = LoadContract("pin_at_acceptance", tuple(frame["domain"]))
    records = (
        [Record(f"{scope.request}-close", scope, "closure", load_count=frame["count"])]
        if frame.get("closed", True)
        else []
    )
    if frame.get("expected") is not None:
        records.append(
            Record(
                f"{scope.request}-selection",
                scope,
                "selection",
                digest=frame["expected"],
                selection=scope.request,
                revision=frame["step"],
                phase="pin_at_acceptance",
            )
        )
    for index, digest in enumerate(frame.get("observed", [])):
        records.append(
            Record(
                f"{scope.request}-load-{index}",
                scope,
                "load",
                digest=digest,
                selection=scope.request,
            )
        )
    result = receipt_checker(Observation(contract, scope, tuple(records)))
    resident = None
    if frame["kind"] == "infer":
        if parent is None or frame.get("generation") != parent["scope"]:
            return {"verdict": "unknown", "eligibility": "undetermined", "resident": None}
        resident = decide(parent)["verdict"]
    return {"verdict": result.verdict, "eligibility": result.eligibility, "resident": resident}


class Workload:
    def __init__(
        self, config: dict[str, Any], directory: Path, artifacts: Path, models: dict[str, Any]
    ) -> None:
        self.config, self.directory = config, directory
        self.band = models["bands"][str(config["depth"])]
        self.payloads = {
            name: (artifacts / self.band[name]["path"]).read_bytes() for name in ("A", "B")
        }
        if any(
            content_sha256(self.payloads[name]) != self.band[name]["digest"] for name in ("A", "B")
        ):
            raise ValueError("owned artifact drift")
        self.domain = [self.band[name]["digest"] for name in ("A", "B")]
        self.probe = models["probe"]
        self.model_dir = directory / "repository/probe"
        self.model_dir.mkdir(parents=True, exist_ok=False)
        self.path = self.model_dir / "model.joblib"
        settings = {
            "name": "probe",
            "implementation": "mlserver_sklearn.SKLearnModel",
            "parameters": {"version": "1", "uri": "model.joblib"},
            "cache_enabled": True,
        }
        (self.model_dir / "model-settings.json").write_text(json.dumps(settings), encoding="utf-8")
        self.app, self.registry = _application(directory / "repository")
        self.capture = ServingCapture(self.model_dir)
        collector_start = perf_counter_ns()
        self.store = (
            ServingStore(directory / "receipts.sqlite", config["arm"], config["horizon"])
            if config["arm"] in ("static", "full")
            else None
        )
        self.collector_init_ns = perf_counter_ns() - collector_start if self.store else 0
        self.rows: list[dict[str, Any]] = []
        self.audits: list[dict[str, Any]] = []
        self.targets: list[dict[str, Any]] = []
        self.generation: str | None = None
        self.resident: str | None = None
        self.resident_object: Any = None
        self.client: Any = None
        self.setup_ns = self.reference_ns = self.retire_ns = self.monitor_ns = 0

    async def operation(
        self,
        scope: str,
        step: int,
        kind: str,
        expected: str | None = None,
        delivered: str | None = None,
    ) -> None:
        body = None
        if kind == "infer":
            body = {
                "id": scope,
                "inputs": [
                    {
                        "name": "x",
                        "shape": [12, 8],
                        "datatype": "FP64",
                        "data": [value for row in self.probe for value in row],
                    }
                ],
                "outputs": [{"name": "predict"}],
            }
        self.capture.scope = scope
        before = len(self.capture.events)
        start = perf_counter_ns()
        error = None
        try:
            response = await asyncio.wait_for(
                self.client.post(
                    "/v2/repository/models/probe/load"
                    if kind == "load"
                    else "/v2/models/probe/infer",
                    json=body,
                ),
                timeout=10,
            )
            status = response.status_code
            result = response.json() if getattr(response, "content", True) else {}
        except (TimeoutError, ValueError, OSError) as exc:
            status, result, error = None, {}, type(exc).__name__
        rest_ns = perf_counter_ns() - start
        events = self.capture.events[before:]
        successful = status == 200
        # Independent object/sink checks occur AFTER the measured request.
        check_start = perf_counter_ns()
        await self.check_reference(scope, kind, successful, delivered, result)
        truth = {
            "verdict": ("violation" if expected != delivered else "compliant")
            if kind == "load" and successful
            else None,
            "eligibility": "load" if kind == "load" and successful else "no_new_load",
            "resident": self._resident_verdict() if kind == "infer" else None,
        }
        check_ns = perf_counter_ns() - check_start
        self.reference_ns += check_ns
        frame = {
            "scope": scope,
            "kind": kind,
            "step": step,
            "domain": self.domain,
            "expected": self.band[expected]["digest"] if expected else None,
            "observed": [event["digest"] for event in events],
            "count": len(events),
            "closed": True,
            "status": status,
            "generation": self.generation,
        }
        write_start = perf_counter_ns()
        if self.store:
            self.store.append(
                scope,
                step,
                frame,
                self.generation if kind == "infer" else None,
                {"request": body, "response": result, "events": events},
            )
            self.store.keep_parent(self.generation)
        write_ns = perf_counter_ns() - write_start if self.store else 0
        row = {
            "scope": scope,
            "step": step,
            "kind": kind,
            "status": status,
            "error": error,
            "frame": frame,
            "truth": truth,
            "rest_ns": rest_ns,
            "write_ns": write_ns,
            "end_to_end_ns": perf_counter_ns() - start - check_ns,
            "events": events,
            "completed_at_ns": perf_counter_ns(),
        }
        self.rows.append(row)
        if kind == "load" or scope.endswith("infer-15"):
            self.targets.append(row)

    async def check_reference(
        self, scope: str, kind: str, successful: bool, delivered: str | None, result: dict[str, Any]
    ) -> None:
        model = await self.registry.get_model("probe")
        if kind == "load" and successful:
            if (
                delivered is None
                or tree_fingerprint(model._model) != self.band[delivered]["fingerprint"]
            ):
                raise ValueError("independent reconstructed-object reference mismatch")
            if self.resident_object is model._model:
                raise ValueError("explicit reload did not create a new object")
            self.resident, self.generation, self.resident_object = delivered, scope, model._model
        elif model._model is not self.resident_object:
            raise ValueError("resident object changed without a successful reload")
        if kind == "infer" and successful:
            predictions = result.get("outputs", [{}])[0].get("data")
            if self.resident is None or predictions != self.band[self.resident]["predictions"]:
                raise ValueError("independent inference sink mismatch")

    def _resident_verdict(self) -> str | None:
        if self.generation is None:
            return None
        return cast(
            str | None,
            next(row["truth"]["verdict"] for row in self.rows if row["scope"] == self.generation),
        )

    def audits_at(self, step: int) -> None:
        for target in self.targets:
            age = step - target["step"]
            if age not in (0, 2, 8):
                continue
            start = perf_counter_ns()
            snapshot = self.store.query(target["scope"]) if self.store else None
            queried = perf_counter_ns()
            decision = (
                decide(snapshot["frame"], snapshot["parent_frame"])
                if snapshot
                else {"verdict": "unknown", "eligibility": "undetermined", "resident": None}
            )
            checked = perf_counter_ns()
            available = snapshot is not None
            self.audits.append(
                {
                    "scope": target["scope"],
                    "age": age,
                    "kind": target["kind"],
                    "truth": target["truth"],
                    "decision": decision,
                    "available": available,
                    "snapshot": snapshot,
                    "query_ns": queried - start,
                    "verify_ns": checked - queried,
                    "elapsed_since_operation_ns": start - target["completed_at_ns"],
                }
            )

    async def run(self) -> dict[str, Any]:
        httpx = importlib.import_module("httpx")
        transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
        context = self.capture.installed() if self.config["arm"] != "native" else nullcontext()
        async with httpx.AsyncClient(
            transport=transport, base_url="http://int06.invalid", trust_env=False
        ) as client:
            self.client = client
            with context:
                for step in range(12):
                    setup = perf_counter_ns()
                    failed = step in (3, 7, 11)
                    expected = "A" if step % 2 == 0 else "B"
                    delivered = "B" if step in (2, 6, 10) else expected
                    if failed:
                        if self.path.exists():
                            self.path.unlink()  # Only this fresh owned worker model path.
                    else:
                        atomic_artifact(self.path, self.payloads[delivered])
                    self.setup_ns += perf_counter_ns() - setup
                    await self.operation(
                        f"load-{step}", step, "load", expected, None if failed else delivered
                    )
                    for index in range(self.config["inferences"]):
                        await self.operation(f"slot-{step}-infer-{index}", step, "infer")
                    self.sample()
                    self.audits_at(step)
                    self.retire(step)
                    self.sample()
        stats = self.store.stats() if self.store else {}
        close_ns = self.store.close() if self.store else 0
        return {
            "config": self.config,
            "rows": self.rows,
            "audits": self.audits,
            "store": stats,
            "setup_ns": self.setup_ns,
            "reference_ns": self.reference_ns,
            "retire_ns": self.retire_ns,
            "close_ns": close_ns,
            "collector_init_ns": self.collector_init_ns,
            "monitor_ns": self.monitor_ns,
            "captured_reconstructions": len(self.capture.events),
            "source_cluster_count": 1,
        }

    def retire(self, step: int) -> None:
        if self.store:
            start = perf_counter_ns()
            self.store.retire(step)
            self.retire_ns += perf_counter_ns() - start

    def sample(self) -> None:
        if self.store:
            start = perf_counter_ns()
            self.store.stats()
            self.monitor_ns += perf_counter_ns() - start


def worker(
    config: dict[str, Any], directory: Path, artifacts: Path, models: dict[str, Any]
) -> dict[str, Any]:
    """Private subprocess entry; never loads historical or user-supplied models."""
    if directory.exists() or artifacts.is_symlink():
        raise ValueError("workload requires a fresh owned directory")
    for key in tuple(os.environ):
        if key.startswith(("MLSERVER_", "OTEL_", "PROMETHEUS_")):
            os.environ.pop(key)
    os.environ["OTEL_SDK_DISABLED"] = "true"

    def deny(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("serving experiment cannot use sockets")

    cpu = process_time_ns()
    with (
        patch.object(socket.socket, "connect", deny),
        patch.object(socket.socket, "connect_ex", deny),
        patch.object(socket, "create_connection", deny),
    ):
        workload = Workload(config, directory, artifacts, models)
        try:
            result = asyncio.run(workload.run())
        except (ValueError, OSError, RuntimeError, TimeoutError) as exc:
            result = {
                "config": config,
                "status": "runtime_worker_failure",
                "error_type": type(exc).__name__,
                "rows": workload.rows,
                "audits": workload.audits,
                "partial_captured_reconstructions": len(workload.capture.events),
            }
            if workload.store:
                workload.store.close()
    result["cpu_ns"] = process_time_ns() - cpu
    try:
        resource = importlib.import_module("resource")
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result["peak_process_rss_bytes"] = rss if sys.platform == "darwin" else rss * 1024
    except ImportError:
        result["peak_process_rss_bytes"] = None
    return result
