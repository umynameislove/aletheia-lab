"""Prospective local native workload with a physically delayed live collector.

One previously unused implementation; nested fresh process repeats. No provider,
historical protected run, natural incident prevalence or one-attempt registration.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import sqlite3
import subprocess
import threading
import time
from collections import Counter
from collections.abc import Coroutine
from contextlib import suppress
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.litserve_evidence_analysis import analyze, forecasts
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

PROTOCOL = "configs/evaluation/litserve_evidence_validation_protocol.json"
FILES = (
    PROTOCOL,
    "scripts/litserve_evidence_validation.py",
    "src/aletheia_lab/evaluation/litserve_evidence_source.py",
    "src/aletheia_lab/evaluation/litserve_evidence_analysis.py",
    "src/aletheia_lab/evaluation/litserve_evidence_study.py",
    "src/aletheia_lab/evaluation/litserve_evidence_provenance.py",
    "src/aletheia_lab/evaluation/model_load_provenance.py",
    "src/aletheia_lab/evaluation/model_load_retention.py",
    "src/aletheia_lab/filesystem.py",
    "src/aletheia_lab/project/identity.py",
)
PACKAGES = (
    "litserve",
    "pyzmq",
    "fastapi",
    "pydantic",
    "uvicorn",
    "httpx",
    "in-toto",
    "securesystemslib",
)
SCHEDULE = (
    ("lawful-0", "/a", 7.0),
    ("lawful-1", "/a", 11.0),
    ("lawful-2", "/b", 7.0),
    ("lawful-3", "/b", 11.0),
    ("lawful-4", "/a", 0.0),
    ("lawful-5", "/b", 0.0),
    ("queue-blocker", "/a", 13.0),
    ("queue-wait-0", "/a", 17.0),
    ("queue-wait-1", "/a", 19.0),
    ("abandoned-success", "/b", 23.0),
    ("abandoned-error", "/b", 23.0),
)


def producer_events(directory: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in sorted(directory.glob("producer-*.jsonl")):
        # A concurrent final append can be partial; keep it for the next read.
        raw = path.read_bytes()
        complete = raw[: raw.rfind(b"\n") + 1]
        events.extend(json.loads(line) for line in complete.splitlines())
    return events


class Collector:
    """Actual asynchronous journal transport/persistence, not post-hoc masking."""

    def __init__(self, directory: Path, delay: float) -> None:
        self.directory, self.delay = directory, delay
        self.path = directory / "collector.sqlite"
        self.stop = threading.Event()
        self.receipts: list[dict[str, Any]] = []
        self.failure: str | None = None
        self.persistence_ns = 0
        self.scan_ns = 0
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            self._collect()
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.failure = type(exc).__name__

    def _collect(self) -> None:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                "CREATE TABLE events(pid INTEGER, seq INTEGER, payload TEXT, PRIMARY KEY(pid,seq))"
            )
            connection.commit()
            pending: dict[tuple[int, int], tuple[int, dict[str, Any]]] = {}
            seen: set[tuple[int, int]] = set()
            while True:
                start = time.monotonic_ns()
                values = producer_events(self.directory)
                self.scan_ns += time.monotonic_ns() - start
                for event in values:
                    key = event["pid"], event["sequence"]
                    if key not in seen and key not in pending:
                        pending[key] = (time.monotonic_ns() + int(self.delay * 1e9), event)
                for key, (due, event) in list(pending.items()):
                    if time.monotonic_ns() < due:
                        continue
                    start = time.monotonic_ns()
                    connection.execute("INSERT INTO events VALUES(?,?,?)", (*key, encode(event)))
                    connection.commit()
                    self.persistence_ns += time.monotonic_ns() - start
                    self.receipts.append({"event": event, "received_ns": time.monotonic_ns()})
                    seen.add(key)
                    del pending[key]
                if self.stop.is_set() and not pending:
                    break
                self.stop.wait(0.002) if not self.stop.is_set() else time.sleep(0.002)
        finally:
            connection.close()

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("collector failed to drain within its bound")


def environment(root: Path, native_site: Path) -> dict[str, str]:
    # No credentials, inherited Python path, proxy or cloud settings cross child boundary.
    allowed = ("PATH", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "WINDIR", "LOCALAPPDATA")
    result = {key: os.environ[key] for key in allowed if key in os.environ}
    result.update(
        PYTHONPATH=os.pathsep.join((str(root / "src"), str(native_site))),
        PYTHONUNBUFFERED="1",
        OMP_NUM_THREADS="1",
        OPENBLAS_NUM_THREADS="1",
        MKL_NUM_THREADS="1",
        LITSERVE_DISABLE_TELEMETRY="1",
        OTEL_SDK_DISABLED="true",
    )
    return result


def runtime_identity(root: Path, executable: Path, native_site: Path) -> dict[str, Any]:
    # Metadata/source only: no native import, model fit or validation outcome.
    code = (
        "import importlib.metadata as m,json,hashlib,sys; "
        "from pathlib import Path; p=m.distribution('litserve'); "
        f"names={PACKAGES!r}; "
        "files=('server.py','api.py','loops/base.py','loops/simple_loops.py','callbacks/base.py','transport/process_transport.py'); "
        "print(json.dumps({'python':sys.version.split()[0], 'packages':{n:m.version(n) for n in names}, "
        "'native_source_bindings':{f:hashlib.sha256(Path(p.locate_file('litserve/'+f)).read_bytes()).hexdigest() for f in files}}))"
    )
    result = subprocess.run(
        [str(executable), "-c", code],
        env=environment(root, native_site),
        capture_output=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        raise ValueError("explicit native environment metadata unavailable")
    identity: dict[str, Any] = json.loads(result.stdout)
    if identity["packages"]["litserve"] != "0.2.19":
        raise ValueError("native version differs from prospective protocol")
    return identity


def _cells(protocol: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"arm": arm, "collector_delay": delay, "replicate": replicate}
        for replicate in range(protocol["process_replicates"])
        for delay in protocol["collector_delays_seconds"]
        for arm in protocol["arms"]
    ]


def design(root: Path, executable: Path, native_site: Path) -> dict[str, Any]:
    protocol = json.loads((root / PROTOCOL).read_bytes())
    cells = _cells(protocol)
    return {
        "schema": "litserve-evidence-transfer-plan/v1",
        "protocol": protocol,
        "cells": cells,
        "requests_per_cell": 11,
        "planned_request_count": 11 * len(cells),
        "runtime_identity": runtime_identity(root, executable, native_site),
        "bindings": {name: content_sha256((root / name).read_bytes()) for name in FILES},
        "selection_boundary": "unused framework chosen for CPU/queue architecture before fault outcomes; authored controlled workloads, not independent field incidents",
    }


async def _ready(port: int, process: subprocess.Popen[Any], timeout: float) -> None:
    import httpx

    end = time.monotonic() + timeout
    async with httpx.AsyncClient(trust_env=False) as client:
        while time.monotonic() < end:
            if process.poll() is not None:
                raise RuntimeError("native server exited before readiness")
            try:
                response = await client.get(f"http://127.0.0.1:{port}/health", timeout=1)
                if response.status_code == 200:
                    return
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.05)
    raise TimeoutError("native startup deadline exceeded")


async def _request(
    client: Any,
    port: int,
    endpoint: str,
    token: str,
    family: str,
    x: float,
    rows: list[dict[str, Any]],
    delay: float = 0,
    fail: bool = False,
    read_timeout: float = 2.0,
) -> None:
    import httpx

    start = time.monotonic_ns()
    payload = {
        "token": token,
        "x": x,
        "delay": delay,
        "fail": fail,
        "deadline_ns": start + 150_000_000,
    }
    row: dict[str, Any] = {
        "token": token,
        "endpoint": endpoint,
        "family": family,
        "x": x,
        "payload": payload,
        "start_ns": start,
        "status": None,
        "body": None,
        "raw_response_hex": "",
        "error": None,
    }
    rows.append(row)  # Offered census exists before HTTP; never drop a failed call.
    try:
        response = await client.post(
            f"http://127.0.0.1:{port}{endpoint}", json=payload, timeout=httpx.Timeout(read_timeout)
        )
        row.update(status=response.status_code, raw_response_hex=response.content.hex())
        try:
            row["body"] = response.json()
        except ValueError:
            row["error"] = "JSONDecodeError"
    except httpx.TransportError as exc:
        row["error"] = type(exc).__name__
    finally:
        row["end_ns"] = time.monotonic_ns()
        row["latency_ns"] = row["end_ns"] - start


async def _entered(directory: Path, token: str) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if any(
            e["kind"] == "predict_enter" and e.get("token") == token
            for e in producer_events(directory)
        ):
            return
        await asyncio.sleep(0.002)
    raise TimeoutError("actual prediction-entry barrier was not observed")


async def workload(port: int, directory: Path, rows: list[dict[str, Any]]) -> None:
    import httpx

    async with httpx.AsyncClient(trust_env=False) as client:
        for group in (
            (("/a", 7.0), ("/a", 11.0)),
            (("/b", 7.0), ("/b", 11.0)),
            (("/a", 0.0), ("/b", 0.0)),
        ):
            tasks = [
                _request(
                    client,
                    port,
                    endpoint,
                    f"lawful-{len(rows) + index}",
                    "ordered_endpoint_batches",
                    x,
                    rows,
                )
                for index, (endpoint, x) in enumerate(group)
            ]
            await asyncio.gather(*tasks)
        blocker = asyncio.create_task(
            _request(client, port, "/a", "queue-blocker", "queue_age_expiry", 13.0, rows, delay=0.6)
        )
        await _entered(directory, "queue-blocker")
        await asyncio.gather(
            *[
                _request(client, port, "/a", f"queue-wait-{index}", "queue_age_expiry", x, rows)
                for index, x in enumerate((17.0, 19.0))
            ],
            blocker,
        )
        for token, fail in (("abandoned-success", False), ("abandoned-error", True)):
            task = asyncio.create_task(
                _request(
                    client,
                    port,
                    "/b",
                    token,
                    "started_client_abandonment",
                    23.0,
                    rows,
                    delay=0.6,
                    fail=fail,
                    read_timeout=0.3,
                )
            )
            await _entered(directory, token)
            await task
            # Drain after each offered abandonment so the next starts in a new attempt.
            await asyncio.sleep(0.75)


def _terminate(process: subprocess.Popen[Any]) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGINT)
        except ProcessLookupError:
            return
    else:
        if process.poll() is None:
            process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=5)
    if os.name == "posix":
        # The group belongs to our new session, even if its leader already exited.
        for _ in range(100):
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.01)
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)


def _finish(process: subprocess.Popen[Any] | None, collector: Collector) -> str | None:
    failure = None
    try:
        if process is not None:
            _terminate(process)
    except (OSError, RuntimeError, TimeoutError) as exc:
        failure = f"cleanup:{type(exc).__name__}"
    try:
        if collector.thread.ident is not None:
            collector.close()
    except (OSError, RuntimeError, TimeoutError) as exc:
        failure = failure or f"collector:{type(exc).__name__}"
    return failure


def final_events(directory: Path) -> list[dict[str, Any]]:
    from aletheia_lab.evaluation.litserve_evidence_source import read_events

    return read_events(directory.glob("producer-*.jsonl"))


def execute_cell(
    root: Path, directory: Path, executable: Path, native_site: Path, cell: dict[str, Any]
) -> dict[str, Any]:
    directory.mkdir()
    collector = Collector(directory, cell["collector_delay"])
    rows: list[dict[str, Any]] = []
    error = None
    process = None
    start = time.monotonic_ns()
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    command = [
        str(executable),
        str(root / "scripts/litserve_evidence_validation.py"),
        "serve",
        "--study-dir",
        str(directory),
        "--port",
        str(port),
        "--arm",
        cell["arm"],
    ]
    with (
        (directory / "stdout.log").open("wb") as stdout,
        (directory / "stderr.log").open("wb") as stderr,
    ):
        try:
            collector.thread.start()
            process = subprocess.Popen(
                command,
                cwd=root,
                env=environment(root, native_site),
                stdout=stdout,
                stderr=stderr,
                start_new_session=os.name == "posix",
            )

            async def drive() -> None:
                assert process is not None
                await _ready(port, process, 60)
                await workload(port, directory, rows)
                await asyncio.sleep(1)

            asyncio.run(_bounded(drive()))
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            error = type(exc).__name__
        finally:
            cleanup_error = _finish(process, collector)
            error = error or cleanup_error
    read_start = time.monotonic_ns()
    write_new_file(directory / "client-rows.json", encode(rows).encode())
    write_new_file(directory / "collector-receipts.json", encode(collector.receipts).encode())
    try:
        events = final_events(directory)
    except (OSError, ValueError) as exc:
        events = producer_events(directory)
        error = error or f"final_journal:{type(exc).__name__}"
    read_ns = time.monotonic_ns() - read_start
    capture = [json.loads(p.read_bytes()) for p in directory.glob("capture-cost-*.json")]
    costs = {
        "capture_recorded_ns": sum(c["capture_write_ns"] for c in capture),
        "unmeasured_final_stats_writes": len(capture),
        "capture_payload_bytes": sum(c["event_bytes"] for c in capture),
        "collector_scan_ns": collector.scan_ns,
        "collector_persistence_ns": collector.persistence_ns,
        "raw_read_ns": read_ns,
        "sqlite_physical_bytes": collector.path.stat().st_size if collector.path.exists() else 0,
        "raw_and_log_physical_bytes": sum(
            p.stat().st_size for p in directory.iterdir() if p.is_file() and p != collector.path
        ),
        "request_latency_ns": [r["latency_ns"] for r in rows],
        "scope": "instrumented local runtime; physical raw files exclude derived source/results and dependency environment; no uninstrumented overhead estimate",
    }
    source = {
        **cell,
        "rows": rows,
        "events": events,
        "receipts": collector.receipts,
        "failure": error or collector.failure,
        "server_returncode": process.returncode if process else None,
        "total_cell_ns": time.monotonic_ns() - start,
        "collector_scan_ns": collector.scan_ns,
        "collector_persistence_ns": collector.persistence_ns,
        "costs": costs,
    }
    write_new_file(directory / "source.json", encode(source).encode())
    return source


async def _bounded(coroutine: Coroutine[Any, Any, None]) -> None:
    await asyncio.wait_for(coroutine, timeout=120)


def _cell_analysis(source: dict[str, Any]) -> dict[str, Any]:
    try:
        expected = SCHEDULE[: len(source["rows"])]
        if [(r["token"], r["endpoint"], r["x"]) for r in source["rows"]] != list(expected):
            raise ValueError("offered case identity differs from fixed schedule")
        for row in source["rows"]:
            raw = bytes.fromhex(row["raw_response_hex"])
            if (
                row["status"] is not None
                and row["error"] != "JSONDecodeError"
                and json.loads(raw) != row["body"]
            ):
                raise ValueError("client operand differs from raw HTTP bytes")
        result = analyze(source["rows"], source["events"], source["receipts"])
    except (ValueError, KeyError, TypeError) as exc:
        result = {"reference_failure": type(exc).__name__, "rows": []}
    return {
        **{key: source[key] for key in ("arm", "collector_delay", "replicate", "failure")},
        "planned": 11,
        "offered": len(source["rows"]),
        "missing": 11 - len(source["rows"]),
        "analysis": result,
        "forecasts": forecasts(source["arm"], source["collector_delay"], result),
        "costs": source.get("costs", {}),
        "signed_baseline": source.get("signed_baseline", {"status": "not_executed"}),
    }


def _signed_bundle(directory: Path, source: dict[str, Any]) -> dict[str, Any]:
    from aletheia_lab.evaluation.litserve_evidence_provenance import sign_bundle

    events = sorted(
        (r["event"] for r in source["receipts"]), key=lambda e: (e["pid"], e["sequence"])
    )
    artifact = content_sha256(encode(events).encode())
    started = time.monotonic_ns()
    # Same represented facts and resolver. A signature supplies integrity, not a new witness.
    status = sign_bundle(artifact, directory / "provenance")
    return {
        "status": status,
        "bundle_sha256": artifact,
        "verification_ns": time.monotonic_ns() - started,
        "link_bindings": {
            p.name: content_sha256(p.read_bytes()) for p in (directory / "provenance").iterdir()
        },
        "link_physical_bytes": sum(p.stat().st_size for p in (directory / "provenance").iterdir()),
        "meaning": "same trusted capture plus ordinary join; not independent attestation or an accuracy advantage",
    }


def _execute_or_failure(
    root: Path, folder: Path, executable: Path, native_site: Path, cell: dict[str, Any]
) -> dict[str, Any]:
    try:
        return execute_cell(root, folder, executable, native_site, cell)
    except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
        # A launch/cleanup failure does not delete this or subsequent planned cells.
        folder.mkdir(exist_ok=True)
        path = folder / "source.json"
        if path.is_file():
            retained: dict[str, Any] = json.loads(path.read_bytes())
            return retained
        try:
            events = final_events(folder)
        except (OSError, ValueError):
            events = []
        row_path, receipt_path = folder / "client-rows.json", folder / "collector-receipts.json"
        source: dict[str, Any] = {
            **cell,
            "rows": json.loads(row_path.read_bytes()) if row_path.is_file() else [],
            "events": events,
            "receipts": json.loads(receipt_path.read_bytes()) if receipt_path.is_file() else [],
            "failure": type(exc).__name__,
            "costs": {},
        }
        write_new_file(path, encode(source).encode())
        return source


def aggregate(cells: list[dict[str, Any]]) -> dict[str, Any]:
    complete = all(
        c["missing"] == 0
        and not c["failure"]
        and "reference_failure" not in c["analysis"]
        and c["analysis"]["collector_complete"]
        and c["signed_baseline"]["status"] == "pass"
        for c in cells
    )
    groups: dict[str, Any] = {}
    for arm in ("native", "cooperative"):
        own = [cell for cell in cells if cell["arm"] == arm]
        rows = [row for cell in own for row in cell["analysis"]["rows"]]
        groups[arm] = {
            "offered": sum(c["offered"] for c in own),
            "status_counts": dict(Counter(str(r["status"]) for r in rows)),
            "closure_at_client_cut": dict(
                Counter(r["truth"]["closure_at_client_cut"] for r in rows)
            ),
            "computed": sum(c["analysis"].get("computed", 0) for c in own),
        }
    evidence = {}
    for delay in (0.0, 0.5):
        own = [cell for cell in cells if cell["collector_delay"] == delay]
        scores = [
            cell["analysis"].get("comparators", {}).get("first_query", {}).get("native_uid", {})
            for cell in own
        ]
        evidence[str(delay)] = {
            endpoint: {
                metric: sum(score.get(endpoint, {}).get(metric, 0) for score in scores)
                for metric in (
                    "denominator",
                    "correct",
                    "false_conclusive",
                    "unknown",
                    "unavailable",
                )
            }
            for endpoint in ("origin", "closure")
        }
    return {
        "cells": len(cells),
        "planned_requests": 11 * len(cells),
        "offered_requests": sum(c["offered"] for c in cells),
        "complete": complete,
        "by_arm": groups,
        "first_query_by_collector_delay": evidence,
        "forecast_supported": sum(c["forecasts"].get("supported", 0) for c in cells),
        "forecast_contradicted": sum(c["forecasts"].get("contradicted", 0) for c in cells),
        "source_cluster_count": 1,
        "mechanism_families": 3,
        "disposition": "bounded_source_conditioned_transfer"
        if complete
        else "incomplete_census_no_transfer_closeout",
        "provider_calls": 0,
        "protected_runs": 0,
        "limits": "Controlled CPU scalar models; one previously unused implementation; nested repeats; no new checker/algorithm or natural prevalence/production performance claim.",
    }


def run(root: Path, directory: Path, executable: Path, native_site: Path) -> dict[str, Any]:
    if directory.exists() or directory.is_symlink():
        raise ValueError(
            "fresh owned study directory required; historical outcomes never overwritten"
        )
    plan = design(root, executable, native_site)
    directory.mkdir(parents=True)
    write_new_file(directory / "plan.json", encode(plan).encode())
    cells, bindings = [], {}
    for index, cell in enumerate(plan["cells"]):
        path = directory / f"cell-{index:02d}"
        source = _execute_or_failure(root, path, executable, native_site, cell)
        analysis_start = time.monotonic_ns()
        _cell_analysis(source)
        source["costs"]["analysis_ns"] = time.monotonic_ns() - analysis_start
        try:
            source["signed_baseline"] = _signed_bundle(path, source)
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            source["signed_baseline"] = {"status": "failure", "error_type": type(exc).__name__}
        # Preserve original native source.json; analysis/signature additions have their own file.
        write_new_file(path / "assessed-source.json", encode(source).encode())
        cells.append(_cell_analysis(source))
        bindings[path.name] = {
            "native": content_sha256((path / "source.json").read_bytes()),
            "assessed": content_sha256((path / "assessed-source.json").read_bytes()),
        }
        print(
            json.dumps(
                {
                    "status": "litserve_evidence_progress",
                    "completed_cells": index + 1,
                    "maximum_cells": len(plan["cells"]),
                }
            ),
            flush=True,
        )
    report: dict[str, Any] = {
        "schema": "litserve-evidence-transfer-result/v1",
        "plan_sha256": content_sha256((directory / "plan.json").read_bytes()),
        "source_bindings": bindings,
        "cells": cells,
        "summary": aggregate(cells),
    }
    report["results_sha256"] = content_sha256(encode(report).encode())
    write_new_file(directory / "results.json", encode(report).encode())
    return {
        "status": "litserve_evidence_validation_complete",
        **report["summary"],
        "plan_sha256": report["plan_sha256"],
        "results_sha256": report["results_sha256"],
    }


def _rebuild_cell(folder: Path, cell: dict[str, Any], binding: dict[str, str]) -> dict[str, Any]:
    source_raw = (folder / "source.json").read_bytes()
    assessed_raw = (folder / "assessed-source.json").read_bytes()
    if {"native": content_sha256(source_raw), "assessed": content_sha256(assessed_raw)} != binding:
        raise ValueError("raw source report changed")
    source = json.loads(assessed_raw)
    original = json.loads(source_raw)
    if any(source[key] != value for key, value in original.items() if key != "costs"):
        raise ValueError("assessed source changed native observations")
    if any(source["costs"][key] != value for key, value in original["costs"].items()):
        raise ValueError("assessed source changed recorded cost")
    if any(source[key] != cell[key] for key in cell) or source["events"] != final_events(folder):
        raise ValueError("native producer journal differs")
    stored = _database_events(folder)
    received = {(r["event"]["pid"], r["event"]["sequence"]): r["event"] for r in source["receipts"]}
    if stored != received:
        raise ValueError("physical collector differs from receipt census")
    if source["signed_baseline"]["status"] == "pass":
        for name, expected in source["signed_baseline"]["link_bindings"].items():
            if content_sha256((folder / "provenance" / name).read_bytes()) != expected:
                raise ValueError("actual signed provenance link changed")
        from aletheia_lab.evaluation.litserve_evidence_provenance import verify_bundle

        verify_bundle(folder / "provenance")
    return _cell_analysis(source)


def _database_events(folder: Path) -> dict[tuple[int, int], Any]:
    path = folder / "collector.sqlite"
    if not path.is_file():
        return {}
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        return {
            (pid, seq): json.loads(payload)
            for pid, seq, payload in connection.execute("SELECT pid,seq,payload FROM events")
        }
    finally:
        connection.close()


def verify(root: Path, directory: Path) -> dict[str, Any]:
    plan_raw, result_raw = (
        (directory / "plan.json").read_bytes(),
        (directory / "results.json").read_bytes(),
    )
    plan, result = json.loads(plan_raw), json.loads(result_raw)
    unsigned = dict(result)
    claimed = unsigned.pop("results_sha256")
    if content_sha256(encode(unsigned).encode()) != claimed or result[
        "plan_sha256"
    ] != content_sha256(plan_raw):
        raise ValueError("self-hash or plan identity differs")
    protocol = json.loads((root / PROTOCOL).read_bytes())
    if (
        plan["protocol"] != protocol
        or plan["cells"] != _cells(protocol)
        or set(plan["bindings"]) != set(FILES)
    ):
        raise ValueError("declared plan differs from fixed protocol/census")
    for path, expected in plan["bindings"].items():
        if content_sha256((root / path).read_bytes()) != expected:
            raise ValueError("execution code changed; retain original code for replay")
    cells = [
        _rebuild_cell(
            directory / f"cell-{index:02d}", cell, result["source_bindings"][f"cell-{index:02d}"]
        )
        for index, cell in enumerate(plan["cells"])
    ]
    if cells != result["cells"] or aggregate(cells) != result["summary"]:
        raise ValueError("independent read-only rebuild differs")
    return {
        "verification": "pass",
        "plan_sha256": content_sha256(plan_raw),
        "results_sha256": claimed,
        **result["summary"],
    }
