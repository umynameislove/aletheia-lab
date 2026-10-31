"""New MLServer continuation sequence; never imports or invokes an old driver."""

import argparse
import hashlib
import http.client
import json
import math
import os
import shutil
import signal
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path

import psutil
from native_guard import NativeGuard, reserve_ports

SOURCE_ROOT = Path(__file__).resolve().parent
MODEL_NAME = "serving-continuation"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")


class Sequence:
    def __init__(self, root, mode, attempt=None, qualified_by="qualification"):
        global ROOT
        ROOT = root.resolve()
        if os.sys.platform != "darwin":
            raise RuntimeError("Native guard is qualified only on macOS")
        self.mode = mode
        self.qualified_by = qualified_by
        if any(
            not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in name)
            for name in (attempt or mode, qualified_by)
        ):
            raise ValueError("Simple owned attempt names required")
        self.prepared = json.loads((ROOT / "prepared.json").read_text())
        if (
            sha(SOURCE_ROOT.parents[1] / "docs/serving-recovery-study.md")
            != self.prepared["method_sha256"]
        ):
            raise RuntimeError("Prepared public method differs")
        for filename, expected in self.prepared["sourcepins"].items():
            if sha(SOURCE_ROOT / filename) != expected:
                raise RuntimeError("Fixed source differs: " + filename)
        for row in (
            self.prepared["metadata"]["mlserver_sources"]
            + self.prepared["metadata"]["dependency_sources"]
        ):
            if sha(row["path"]) != row["sha256"]:
                raise RuntimeError("Installed MLServer source differs")
        for edition in self.prepared["editions"]:
            self.check_edition(edition)
        self.output = ROOT / "results" / (attempt or mode)
        self.output.mkdir(parents=True, exist_ok=False)
        self.repository = self.output / "repository"
        self.repository.mkdir()
        (self.output / "tmp").mkdir()
        (self.output / "barriers").mkdir()
        self.events = self.output / "events.jsonl"
        self.guard = None
        self.owner = 0
        self.requests = []
        self.guards = []
        self.started = time.monotonic()
        self.port = None
        self.publish("A")
        self.emit(
            "sequence_prepared",
            mode=mode,
            prepared_sha256=sha(ROOT / "prepared.json"),
            design_sha256=self.prepared["method_sha256"],
            intended_native_calls=True,
        )

    def emit(self, kind, **fields):
        row = {"kind": kind, "parent_ns": time.monotonic_ns(), **fields}
        with self.events.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return row

    def check_edition(self, edition):
        names = {
            "runtime": "continuation_runtime.py",
            "helper": "continuation_helper.py",
            "training": "training.json",
        }
        for component, filename in names.items():
            if (
                sha(ROOT / "editions" / edition / filename)
                != self.prepared["editions"][edition]["sourcehashes"][component]
            ):
                raise RuntimeError("Fixed authored edition differs")

    def publish(self, edition):
        self.check_edition(edition)
        for path in (ROOT / "editions" / edition).iterdir():
            if path.is_file():
                shutil.copyfile(path, self.repository / path.name)
        self.emit(
            "authored_publication",
            edition=edition,
            files={
                path.name: sha(path) for path in sorted(self.repository.iterdir()) if path.is_file()
            },
        )

    def request(self, method, route, payload=None, seconds=5):
        if time.monotonic() - self.started > 300:
            raise TimeoutError("Fixed whole-sequence ceiling exceeded")
        try:
            receipt = self.guard.request(self.port, method, route, payload, seconds=seconds)
        except BaseException as exc:
            self.emit(
                "http_failure",
                method=method,
                route=route,
                failure_type=type(exc).__name__,
                failure=str(exc),
            )
            raise
        self.emit("http", receipt=receipt)
        return receipt

    def start(self):
        self.owner += 1
        owner_output = self.output / ("owner-" + str(self.owner))
        owner_output.mkdir()
        ports = reserve_ports(2)
        self.declared_ports = ports
        self.port = ports[0]
        settings = {
            "host": "localhost",
            "http_port": ports[0],
            "grpc_port": ports[1],
            "parallel_workers": 0,
            "metrics_endpoint": None,
            "debug": False,
            "cache_enabled": False,
            "kafka_enabled": False,
            "load_models_at_startup": True,
        }
        (self.repository / "settings.json").write_text(json.dumps(settings, sort_keys=True) + "\n")
        model_settings = {
            "name": MODEL_NAME,
            "implementation": "continuation_runtime.ContinuationRuntime",
            "cache_enabled": False,
            "max_batch_size": 0,
        }
        (self.repository / "model-settings.json").write_text(
            json.dumps(model_settings, sort_keys=True) + "\n"
        )
        environment = {
            "PATH": "/usr/bin:/bin",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "OTEL_SDK_DISABLED": "true",
            "DO_NOT_TRACK": "1",
            "BENTOML_DO_NOT_TRACK": "1",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "VECLIB_MAXIMUM_THREADS": "1",
            "TMPDIR": str(self.output / "tmp"),
            "CONTINUATION_REPOSITORY": str(self.repository),
            "CONTINUATION_BARRIERS": str(self.output / "barriers"),
            "CONTINUATION_NATIVE_EVENTS": str(self.output / "native-lifecycle.jsonl"),
        }
        argv = [
            self.prepared["service_python"],
            "-B",
            str(Path(self.prepared["service_python"]).parent / "mlserver"),
            "start",
            str(self.repository),
        ]
        self.emit(
            "owner_start_requested",
            owner=self.owner,
            argv=argv,
            ports=ports,
            environment_keys=sorted(environment),
            settings=settings,
        )
        self.guard = NativeGuard(
            argv,
            environment,
            self.repository,
            owner_output,
            self.output,
            seconds=120,
            rss=768 * 1024**2,
            disk=100 * 1024**2,
        )
        ceiling = time.monotonic() + 30
        while time.monotonic() < ceiling:
            self.guard.alive()
            try:
                receipt = self.request("GET", "/v2/models/" + MODEL_NAME + "/ready", seconds=1)
                if receipt["status"] == 200:
                    self.emit("owner_ready", owner=self.owner, pid=self.guard.child.pid)
                    return
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.15)
        raise TimeoutError("Fixed native readiness deadline exceeded")

    def stop(self, hard=False):
        if self.guard is None:
            return
        guard = self.guard
        # Complete inspection before deliberately stopping a drained owner.
        deadline = time.monotonic() + 3
        while not all((guard.child.pid, port) in guard.proofs for port in self.declared_ports):
            guard.alive()
            if time.monotonic() > deadline:
                raise RuntimeError("Declared listener proof incomplete before owned shutdown")
            time.sleep(0.02)
        guard.stop.set()
        guard.thread.join(timeout=5)
        if guard.thread.is_alive():
            raise RuntimeError("Monitor inspection did not finish before owned shutdown")
        guard.alive()
        if not all(proof["pass"] for proof in guard.proofs.values()):
            raise RuntimeError("Listener inspection failed before owned shutdown")
        self.emit(
            "monitor_shutdown_handoff",
            owner=self.owner,
            declared_ports=self.declared_ports,
            admitted_requests_drained=True,
            listener_proofs_complete=True,
        )
        if hard and guard.child.poll() is None:
            guard.alive()
            observed = guard.observed.get(guard.child.pid)
            proc = psutil.Process(guard.child.pid)
            if (
                not observed
                or proc.create_time() != observed["created"]
                or os.getpgid(proc.pid) != proc.pid
            ):
                raise RuntimeError(
                    "Exact owned PID/create-time/group proof unavailable for final serving-owner loss"
                )
            self.emit(
                "serving_owner_sigkill",
                pid=proc.pid,
                created=proc.create_time(),
                admitted_requests_drained=True,
            )
            os.killpg(proc.pid, signal.SIGKILL)
            guard.child.wait(timeout=5)
        result = guard.close()
        self.guards.append(result)
        self.emit("owner_closed", owner=self.owner, guard=result)
        self.guard = None
        if (
            result["inspection_failures"]
            or result["failures"]
            or not result["all_observed_listeners_qualified"]
            or not result["cleanup_complete"]
        ):
            raise RuntimeError(
                "Owned native process/resource/listener/cleanup qualification failed"
            )

    def management(self, action):
        receipt = self.request("POST", "/v2/repository/models/" + MODEL_NAME + "/" + action, {})
        if receipt["status"] != 200:
            raise RuntimeError("Public repository operation failed: " + action)
        return receipt

    def infer(self, request_id, value, allowed, requested=None, hold=False):
        began = time.monotonic_ns()
        row = {
            "request_id": request_id,
            "input": value,
            "targetC": {
                "allowed_edition": allowed,
                "requested_upgrade": requested,
                "authority_origin": "experiment-author",
                "policy": "entry-cutover-with-drained-maintenance",
            },
            "acquisition_timeline": {"offered_ns": began},
            "status": "attempted-awaiting-response",
        }
        self.requests.append(row)
        self.emit("inference_attempted", request_id=request_id, input=value)
        try:
            receipt = self.request(
                "POST",
                "/v2/models/" + MODEL_NAME + "/infer",
                {
                    "id": request_id,
                    "parameters": {"hold": hold},
                    "inputs": [{"name": "x", "shape": [1], "datatype": "FP64", "data": [value]}],
                },
                seconds=14 if hold else 5,
            )
        except BaseException as exc:
            row.update(
                status="transport-failed", failure={"type": type(exc).__name__, "message": str(exc)}
            )
            raise
        row.update(http=receipt, status="http-failed")
        row["acquisition_timeline"]["received_ns"] = receipt["parent_end_ns"]
        if receipt["status"] == 200:
            record = receipt["body"].get("parameters", {}).get("record")
            if (
                not isinstance(record, dict)
                or record["request_id"] != request_id
                or record["pid"] != self.guard.child.pid
            ):
                raise RuntimeError("Actual request/native-owner association failed")
            output = receipt["body"].get("outputs", [])
            if (
                record["input"] != float(value)
                or len(output) != 1
                or output[0]["data"] != [record["output"]]
            ):
                raise RuntimeError("Actual raw input/output consumer binding differs")
            row.update(record)
            row["status"] = "completed"
            row["reference"] = self.score(record, allowed, requested)
        self.emit("request_acquired", request=row)
        if (
            row["status"] != "completed"
            or not row["source_qualified"]
            or not row["reference"]["actual_arithmetic_correct"]
        ):
            raise RuntimeError("Native consumer/reference qualification failed")
        return row

    def score(self, record, allowed, requested):
        c = record["actualcomponents"]
        reference = record["_reference"]
        before = reference["fitted_before"]
        raw_coef = bytes.fromhex(reference["coef_bytes_hex"])
        if (
            reference["coef_dtype"] != "float64"
            or reference["coef_shape"] != [1]
            or len(raw_coef) != 8
            or struct.unpack("=d", raw_coef)[0] != before["coef"][0]
            or float.fromhex(reference["intercept_hex"]) != before["intercept"]
            or before != reference["fitted_after"]
            or before["coef"] != c["coef"]
            or before["intercept"] != c["intercept"]
            or before["n_features_in"] != c["n_features_in"]
            or c["helper"] != reference["helper_value"]
            or c["runtime_offset"] != reference["runtime_offset"]
            or not reference["selected_model_consumed"]
        ):
            raise RuntimeError(
                "Independent raw fitted bytes/components/reference qualification differs"
            )
        actual = (
            Fraction.from_float(float(reference["fitted_before"]["coef"][0]))
            * Fraction(record["input"])
            + Fraction.from_float(float(reference["fitted_before"]["intercept"]))
            + Fraction.from_float(float(reference["helper_value"]))
            + Fraction.from_float(float(reference["runtime_offset"]))
        )
        target = self.prepared["editions"][allowed]
        expected = (
            target["weight"] * record["input"]
            + target["intercept"]
            + target["helper"]
            + target["runtime_offset"]
        )

        def matches(edition):
            spec = self.prepared["editions"][edition]
            return (
                record["sourcehashes"] == spec["sourcehashes"]
                and math.isclose(c["coef"][0], spec["weight"], abs_tol=1e-10, rel_tol=0)
                and math.isclose(c["intercept"], spec["intercept"], abs_tol=1e-10, rel_tol=0)
                and c["helper"] == spec["helper"]
                and c["runtime_offset"] == spec["runtime_offset"]
            )

        return {
            "actual_arithmetic_correct": math.isclose(
                record["output"], float(actual), abs_tol=1e-10, rel_tol=0
            ),
            "actual_arithmetic_fraction": str(actual),
            "target_expected_output": expected,
            "authorized_state": "compliant" if matches(allowed) else "violation",
            "target_numerics": "compliant"
            if math.isclose(record["output"], expected, abs_tol=1e-10, rel_tol=0)
            else "violation",
            "requested_upgrade_reached": None if requested is None else matches(requested),
            "without_authority": "needs-contract",
            "reference_limit": "separate Fraction/raw-state computation, same trusted host/source/hook; no independent sensor",
        }

    def overlap_load(self, prefix):
        request_id = prefix + "-held-old-x3"
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.infer, request_id, 3, "A", None, True)
            path = self.output / "barriers" / (request_id + ".entered.json")
            ceiling = time.monotonic() + 3
            while not path.exists():
                self.guard.alive()
                if future.done():
                    future.result()
                    raise RuntimeError("Held request returned without entry witness")
                if time.monotonic() > ceiling:
                    raise TimeoutError("Held selection entry not observed")
                time.sleep(0.01)
            entered = json.loads(path.read_text())
            ack = self.management("load")
            (self.output / "barriers" / (request_id + ".release")).write_text("release\n")
            row = future.result(timeout=15)
            qualified = (
                entered["generation"] == row["generation"]
                and row["selected_ns"] < ack["parent_end_ns"] < row["consumed_ns"]
            )
            self.emit(
                "held_load_interval",
                request_id=request_id,
                qualified=qualified,
                selected_generation=row["generation"],
                ack_ns=ack["parent_end_ns"],
            )
            if not qualified:
                raise RuntimeError("Actual held selection did not span public load ACK")
            return row

    def qualify(self):
        self.start()
        self.infer("Q-initial-x3", 3, "A")
        held = self.overlap_load("Q")
        post = self.infer("Q-noop-x3", 3, "A")
        if held["generation"] == post["generation"] or held["pid"] != post["pid"]:
            raise RuntimeError("No actual new native generation in same owner")
        self.management("unload")
        unavailable = self.request("GET", "/v2/models/" + MODEL_NAME + "/ready")
        if unavailable["status"] == 200:
            raise RuntimeError("Official unload did not make model unavailable")
        self.management("load")
        self.infer("Q-unload-load-x3", 3, "A")
        self.stop()

    def main_sequence(self):
        qualification = json.loads(
            (ROOT / "results" / self.qualified_by / "terminal.json").read_text()
        )
        if qualification["status"] != "complete-qualified" or qualification[
            "prepared_sha256"
        ] != sha(ROOT / "prepared.json"):
            raise RuntimeError("Exact current prepared qualification unavailable")
        self.start()
        self.infer("A-x0", 0, "A")
        self.infer("A-x3", 3, "A")
        self.publish("B")
        self.infer("B-published-x0", 0, "A", "B")
        held = self.overlap_load("B")
        post = self.infer("B-active-x0", 0, "B")
        if held["generation"] == post["generation"]:
            raise RuntimeError("Public load did not replace actual selected instance")
        self.infer("B-active-x3", 3, "B")
        self.publish("C")
        self.management("load")
        self.infer("C-load-x0", 0, "C")
        self.infer("C-load-x3", 3, "C")
        self.stop()
        self.start()
        self.infer("C-repair-x0", 0, "C")
        self.infer("C-repair-x3", 3, "C")
        self.management("load")
        self.infer("C-noop-x3", 3, "C")
        self.management("unload")
        unavailable = self.request("GET", "/v2/models/" + MODEL_NAME + "/ready")
        if unavailable["status"] == 200:
            raise RuntimeError("Official unload did not make continuation unavailable")
        self.management("load")
        self.infer("C-reload-x3", 3, "C")
        self.publish("D")
        self.management("load")
        self.infer("D-load-x0", 0, "D")
        self.infer("D-load-x3", 3, "D")
        self.stop()
        self.start()
        self.infer("D-repair-x0", 0, "D")
        self.infer("D-repair-x3", 3, "D")
        self.stop(hard=True)
        if [row["request_id"] for row in self.requests] != self.prepared["planned_main_ids"]:
            raise RuntimeError("Planned main inference census differs")

    def run(self):
        status, failure = "failed", None
        try:
            if self.mode == "qualification":
                self.qualify()
            else:
                self.main_sequence()
            status = "complete-qualified"
        except BaseException as exc:
            failure = {"type": type(exc).__name__, "message": str(exc)}
            self.emit("sequence_failure", failure=failure)
        finally:
            if self.guard is not None:
                try:
                    self.stop()
                except BaseException as cleanup:
                    self.emit(
                        "cleanup_failure", failure_type=type(cleanup).__name__, failure=str(cleanup)
                    )
                    if failure is None:
                        failure = {"type": type(cleanup).__name__, "message": str(cleanup)}
                    status = "failed"
            for source in (
                self.prepared["metadata"]["mlserver_sources"]
                + self.prepared["metadata"]["dependency_sources"]
            ):
                if sha(source["path"]) != source["sha256"]:
                    failure = {"type": "SourceIdentityFailure", "message": source["path"]}
                    status = "failed"
            write_new(self.output / "requests.json", self.requests)
            ordinary_keys = (
                "request_id",
                "input",
                "output",
                "fitted_output",
                "actualcomponents",
                "sourcehashes",
                "generation",
                "pid",
                "instance_id",
                "fitted_id",
                "selected_ns",
                "consumed_ns",
                "completed_ns",
                "source_qualified",
                "evidence_origin",
                "status",
                "acquisition_timeline",
            )
            write_new(
                self.output / "ordinary-evidence.json",
                [{key: row[key] for key in ordinary_keys if key in row} for row in self.requests],
            )
            write_new(
                self.output / "author-reference.json",
                [
                    {
                        key: row[key]
                        for key in ("request_id", "targetC", "reference", "_reference")
                        if key in row
                    }
                    for row in self.requests
                ],
            )
            attempted = {row["request_id"] for row in self.requests}
            planned = (
                self.prepared["planned_main_ids"]
                if self.mode == "main"
                else ["Q-initial-x3", "Q-held-old-x3", "Q-noop-x3", "Q-unload-load-x3"]
            )
            result = {
                "status": status,
                "mode": self.mode,
                "failure": failure,
                "prepared_sha256": sha(ROOT / "prepared.json"),
                "requests": len(self.requests),
                "planned_ids": planned,
                "unattempted_ids": [rid for rid in planned if rid not in attempted],
                "native_owner_count": self.owner,
                "guard_count": len(self.guards),
                "all_cleanup_complete": all(row["cleanup_complete"] for row in self.guards),
                "elapsed_seconds": time.monotonic() - self.started,
                "requests_sha256": sha(self.output / "requests.json"),
                "events_sha256": sha(self.events),
                "limits": "finite controlled source-informed development; no deployment prevalence/novelty/general atomicity or host-crash guarantee",
            }
            write_new(self.output / "terminal.json", result)
            print(json.dumps(result, sort_keys=True))
        return 0 if status == "complete-qualified" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("mode", choices=("qualification", "main"))
    parser.add_argument("--attempt")
    parser.add_argument("--qualified-by", default="qualification")
    args = parser.parse_args()
    raise SystemExit(Sequence(args.root, args.mode, args.attempt, args.qualified_by).run())
