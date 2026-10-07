"""Opt-in owned aiohttp serving lifecycle with native cachetools memoization.

Hot reload and request-entry selection are application contracts. Cachetools
does not promise model-generation freshness. Raw reference capture exists in
every arm; ``none`` disables the additional audit service, not observation.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import math
import os
import socket
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from time import perf_counter_ns, process_time_ns
from typing import Any
from unittest.mock import patch

from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

ARMS = ("input_key", "clear", "generation_key", "isolated")
ORDERS = ("old_first", "new_first")
EVIDENCE = ("none", "sufficient", "full")
SUFFICIENT = frozenset(
    {
        "load_return",
        "compute_return",
        "wrapper_return",
        "handler_terminal",
        "response",
        "publish",
        "load_failure",
    }
)


def _encode(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True)
class Resident:
    """Immutable owned model selected before the cached call."""

    generation: str
    coefficient: float
    intercept: float
    digest: str


class Lifecycle:
    """One fresh process/cell; instrumented reference and optional audit service."""

    def __init__(self, config: dict[str, Any], directory: Path) -> None:
        self.config, self.directory = config, directory
        self.cachetools = importlib.import_module("cachetools")
        self.web = importlib.import_module("aiohttp.web")
        self.lock, self.journal_lock = threading.RLock(), threading.RLock()
        self.token: ContextVar[str] = ContextVar("owned_lifecycle_token", default="")
        self.entered, self.release = threading.Event(), threading.Event()
        self.events: list[dict[str, Any]] = []
        self.audit_records: list[dict[str, Any]] = []
        self.rows: list[dict[str, Any]] = []
        self.selected: dict[str, str] = {}
        self.returned: dict[str, dict[str, Any]] = {}
        self.loads: dict[str, dict[str, Any]] = {}
        self.measurements: dict[str, Any] = {
            "capture_ns": 0,
            "raw_capture_ns": 0,
            "persist_ns": 0,
            "query_ns": 0,
            "hash_ns": 0,
            "offered_elapsed_ns": [],
        }
        self.connection: sqlite3.Connection | None = None
        self.audit_payloads: list[bytes] = []
        self.commit_batches = 0
        if config["evidence"] != "none":
            started = perf_counter_ns()
            self.connection = sqlite3.connect(directory / "audit.sqlite", check_same_thread=False)
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute(
                "CREATE TABLE audit (sequence INTEGER PRIMARY KEY, payload BLOB NOT NULL)"
            )
            self.connection.commit()
            self.measurements["persist_ns"] += perf_counter_ns() - started
        self.artifacts = self._artifacts()
        self.wrappers: dict[str, Any] = {}
        self.shared_cache = self.cachetools.LRUCache(maxsize=8)
        self.resident = self._load("A")
        self.shared_wrapper = self._wrapper(self.shared_cache)
        self.wrappers["A"] = self._wrapper(self.cachetools.LRUCache(maxsize=8))

    def event(self, kind: str, **facts: Any) -> dict[str, Any]:
        with self.journal_lock:
            started = perf_counter_ns()
            event = {
                "sequence": len(self.events),
                "time_ns": started,
                "token": self.token.get(),
                "kind": kind,
                **facts,
            }
            self.events.append(event)
            self.measurements["raw_capture_ns"] += perf_counter_ns() - started
            started = perf_counter_ns()
            evidence = self.config["evidence"]
            if evidence == "full" or (evidence == "sufficient" and kind in SUFFICIENT):
                self.audit_records.append(dict(event))
                raw = _encode(event)
            else:
                raw = None
            self.measurements["capture_ns"] += perf_counter_ns() - started
            if raw is not None:
                started = perf_counter_ns()
                if self.connection is None:
                    raise RuntimeError("audit connection unavailable")
                self.connection.execute("INSERT INTO audit VALUES (?, ?)", (event["sequence"], raw))
                self.connection.commit()
                self.commit_batches += 1
                self.audit_payloads.append(raw)
                self.measurements["persist_ns"] += perf_counter_ns() - started
            return event

    def _artifacts(self) -> dict[str, dict[str, Any]]:
        artifacts = {}
        for label, coefficient in (("A", 1.0), ("B", 2.0)):
            raw = _encode({"coefficient": coefficient, "intercept": 0.0, "padding": " " * 8192})
            write_new_file(self.directory / f"{label}.json", raw)
            artifacts[label] = {
                "bytes": len(raw),
                "raw_hex": raw.hex(),
                "coefficient": coefficient,
                "intercept": 0.0,
            }
        write_new_file(self.directory / "C.json", b'{"coefficient":')
        artifacts["C"] = {
            "bytes": len(b'{"coefficient":'),
            "raw_hex": b'{"coefficient":'.hex(),
            "invalid_json": True,
        }
        return artifacts

    def _load(self, label: str) -> Resident:
        self.event("load_enter", artifact=label)
        raw = (self.directory / f"{label}.json").read_bytes()
        if not 0 < len(raw) <= 16384:
            raise ValueError("owned JSON byte bound")
        started = perf_counter_ns()
        digest = content_sha256(raw)
        self.measurements["hash_ns"] += perf_counter_ns() - started
        self.artifacts[label]["sha256"] = digest
        data = json.loads(raw)
        if set(data) != {"coefficient", "intercept", "padding"}:
            raise ValueError("owned affine JSON schema")
        if not isinstance(data["padding"], str) or len(data["padding"]) > 8192:
            raise ValueError("owned padding bound")
        values = [data["coefficient"], data["intercept"]]
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
            raise ValueError("finite affine parameters required")
        model = Resident(label, float(values[0]), float(values[1]), digest)
        observed = {
            "generation": label,
            "digest": digest,
            "artifact": label,
            "coefficient": model.coefficient,
            "intercept": model.intercept,
            "object_id": id(model),
            "pid": os.getpid(),
            "bytes": len(raw),
        }
        self.loads[label] = observed
        self.event("load_return", **observed)
        return model

    def _wrapper(self, cache: Any) -> Any:
        def key(model: Resident, x: float) -> Any:
            return (model.generation, x) if self.config["arm"] == "generation_key" else x

        def compute(model: Resident, x: float) -> dict[str, Any]:
            entry = self.event(
                "compute_enter",
                generation=model.generation,
                object_id=id(model),
                digest=model.digest,
                x=x,
            )
            identifier = f"compute-{entry['sequence']}"
            if self.token.get() == "old":
                self.entered.set()
                if not self.release.wait(timeout=5):
                    raise TimeoutError("old compute release deadline")
            result = {
                "cid": identifier,
                "generation": model.generation,
                "x": x,
                "y": model.coefficient * x + model.intercept,
                "digest": model.digest,
            }
            self.event("compute_return", **result, object_id=id(model))
            return result

        return self.cachetools.cached(cache, key=key, lock=self.lock, info=True)(compute)

    def _predict(self, model: Resident, x: float) -> dict[str, Any]:
        wrapper = (
            self.wrappers[model.generation]
            if self.config["arm"] == "isolated"
            else self.shared_wrapper
        )
        result: dict[str, Any] = dict(wrapper(model, x))
        # Observe the actual wrapper return: setdefault may prefer another producer.
        self.returned[self.token.get()] = dict(result)
        self.event(
            "wrapper_return",
            selected_generation=model.generation,
            selected_object_id=id(model),
            returned=dict(result),
        )
        return result

    async def infer(self, request: Any) -> Any:
        token_handle = self.token.set(request.headers["X-Owned-Token"])
        try:
            body = await request.json()
            x = float(body["x"])
            if not math.isfinite(x):
                raise ValueError("finite input required")
            with self.lock:
                selected = self.resident
                self.selected[self.token.get()] = selected.generation
            self.event("select", generation=selected.generation, object_id=id(selected), x=x)
            result = await asyncio.to_thread(self._predict, selected, x)
            response = self.web.json_response({"y": result["y"]})
            self.event(
                "handler_terminal",
                selected_generation=selected.generation,
                selected_object_id=id(selected),
                returned_producer=result,
                status=response.status,
                raw_response=response.body.decode(),
            )
            return response
        finally:
            self.token.reset(token_handle)

    async def reload(self, request: Any) -> Any:
        token_handle = self.token.set(request.headers["X-Owned-Token"])
        try:
            label = (await request.json())["artifact"]
            if label not in ("B", "C"):
                raise ValueError("owned reload labels only")
            try:
                candidate = self._load(label)
            except (ValueError, KeyError, TypeError) as exc:
                self.event(
                    "load_failure",
                    artifact=label,
                    error_type=type(exc).__name__,
                    preserved_generation=self.resident.generation,
                )
                return self.web.json_response({"error": "invalid artifact"}, status=400)
            candidate_wrapper = self._wrapper(self.cachetools.LRUCache(maxsize=8))
            with self.lock:
                previous = self.resident.generation
                self.wrappers[label] = candidate_wrapper
                self.resident = candidate
                if self.config["arm"] == "clear":
                    self.shared_wrapper.cache_clear()
                self.event(
                    "publish",
                    previous_generation=previous,
                    generation=label,
                    object_id=id(candidate),
                    digest=candidate.digest,
                    clear=self.config["arm"] == "clear",
                )
            return self.web.json_response({"loaded_generation": label})
        finally:
            self.token.reset(token_handle)

    async def operation(
        self, client: Any, url: str, token: str, phase: str, route: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "token": token,
            "phase": phase,
            "route": route,
            "body": body,
            "event_start": len(self.events),
        }
        if route == "/infer":
            row["offered_index"] = sum(item["route"] == "/infer" for item in self.rows)
        self.rows.append(row)
        started = perf_counter_ns()
        try:
            async with client.post(
                url + route, json=body, headers={"X-Owned-Token": token}
            ) as reply:
                raw = await reply.read()
                row.update(status=reply.status, raw_response=raw.decode(), response=json.loads(raw))
        except (OSError, TimeoutError, ValueError) as exc:
            row.update(status=None, raw_response=None, response=None, error_type=type(exc).__name__)
        row.update(
            completion_ns=perf_counter_ns(),
            selected_generation=self.selected.get(token),
            returned_producer=self.returned.get(token),
            event_end=len(self.events),
        )
        row["elapsed_ns"] = row["completion_ns"] - started
        if route == "/infer":
            self.measurements["offered_elapsed_ns"].append(row["elapsed_ns"])
        handle = self.token.set(token)
        try:
            self.event("response", **row)
        finally:
            self.token.reset(handle)
        return row

    async def execute(self) -> None:
        aiohttp = importlib.import_module("aiohttp")
        app = self.web.Application(client_max_size=4096)
        app.router.add_post("/infer", self.infer)
        app.router.add_post("/reload", self.reload)
        runner = self.web.AppRunner(app)
        old = None
        await runner.setup()
        try:
            site = self.web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            url = f"http://127.0.0.1:{port}"
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as client:

                async def infer(token: str, x: int, phase: str) -> dict[str, Any]:
                    return await self.operation(client, url, token, phase, "/infer", {"x": x})

                await infer("warm0", 0, "warm")
                await infer("repeat0", 0, "warm")
                old = asyncio.create_task(infer("old", 1, "overlap"))
                if not await asyncio.to_thread(self.entered.wait, 5):
                    raise TimeoutError("old compute entry deadline")
                await self.operation(client, url, "reload", "reload", "/reload", {"artifact": "B"})
                await infer("after_reload0", 0, "after_reload")
                if self.config["order"] == "old_first":
                    self.release.set()
                    await old
                    await infer("new1", 1, "after_reload")
                else:
                    await infer("new1", 1, "after_reload")
                    self.release.set()
                    await old
                await infer("repeat1", 1, "after_reload")
                await self.operation(
                    client, url, "invalid_reload", "failed_reload", "/reload", {"artifact": "C"}
                )
                await infer("after_failed2", 2, "after_failed_reload")
                await infer("repeat2", 2, "after_failed_reload")
                for index in range(self.config["steady_requests"]):
                    await infer(f"steady-{index}", 2, "steady")
        finally:
            self.release.set()
            if old is not None and not old.done():
                await asyncio.gather(old, return_exceptions=True)
            await runner.cleanup()

    def persist(self) -> dict[str, Any]:
        if self.config["evidence"] == "none":
            return {
                "record_count": 0,
                "logical_bytes": 0,
                "physical_bytes": 0,
                "main_bytes": 0,
                "wal_bytes": 0,
                "shm_bytes": 0,
                "records": [],
                "commit_batches": 0,
            }
        path = self.directory / "audit.sqlite"
        connection = self.connection
        if connection is None:
            raise RuntimeError("audit connection unavailable")
        try:
            started = perf_counter_ns()
            returned = [
                bytes(row[0])
                for row in connection.execute("SELECT payload FROM audit ORDER BY sequence")
            ]
            self.measurements["query_ns"] = perf_counter_ns() - started
            if returned != self.audit_payloads:
                raise ValueError("audit persisted bytes differ")
            sizes = {
                "main_bytes": path.stat().st_size,
                "wal_bytes": Path(str(path) + "-wal").stat().st_size,
                "shm_bytes": Path(str(path) + "-shm").stat().st_size,
            }
            return {
                "record_count": len(returned),
                "logical_bytes": sum(map(len, returned)),
                "physical_bytes": sum(sizes.values()),
                **sizes,
                "records": [json.loads(raw) for raw in returned],
                "commit_batches": self.commit_batches,
                "commit_policy": "one synchronous FULL commit per admitted event, inline throughout execution",
                "physical_measurement": "after committed drain/query, before close/checkpoint",
            }
        finally:
            connection.close()

    def cache_state(self) -> dict[str, Any]:
        """CPython object sizes of retained native caches, excluding service/RSS."""
        seen: set[int] = set()

        def size(value: Any) -> int:
            if id(value) in seen:
                return 0
            seen.add(id(value))
            result = sys.getsizeof(value)
            if isinstance(value, dict):
                result += sum(size(key) + size(item) for key, item in value.items())
            elif isinstance(value, (list, tuple, set, frozenset)):
                result += sum(size(item) for item in value)
            return result

        caches = {
            "shared": self.shared_cache,
            **{f"generation-{label}": wrapper.cache for label, wrapper in self.wrappers.items()},
        }
        rows = []
        for owner, cache in caches.items():
            rows.append(
                {
                    "owner": owner,
                    "cache_id": id(cache),
                    "entry_count": len(cache),
                    "maxsize": cache.maxsize,
                    "shallow_bytes": sys.getsizeof(cache),
                    "deduplicated_owned_bytes": size(cache) + size(cache.__dict__),
                }
            )
        return {
            "cache_count": len({id(cache) for cache in caches.values()}),
            "rows": rows,
            "owned_bytes": sum(row["deduplicated_owned_bytes"] for row in rows),
            "scope": "sys.getsizeof native cache objects/internal mappings/retained values, identity-deduplicated; excludes wrappers, aiohttp, thread resources and RSS",
        }


async def _execute_guarded(lifecycle: Lifecycle) -> None:
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=4) as executor:
        loop.set_default_executor(executor)
        original_connect = socket.socket.connect
        original_connect_ex = socket.socket.connect_ex
        original_bind = socket.socket.bind
        original_resolve = socket.getaddrinfo

        def allowed(sock: Any, address: Any) -> None:
            if sock.family != socket.AF_UNIX and (
                not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1")
            ):
                raise PermissionError("owned loopback/Unix network only")

        def connect(sock: Any, address: Any) -> Any:
            allowed(sock, address)
            return original_connect(sock, address)

        def connect_ex(sock: Any, address: Any) -> Any:
            allowed(sock, address)
            return original_connect_ex(sock, address)

        def bind(sock: Any, address: Any) -> Any:
            allowed(sock, address)
            return original_bind(sock, address)

        def resolve(host: Any, *args: Any, **kwargs: Any) -> Any:
            if host not in ("127.0.0.1", "::1"):
                raise PermissionError("owned numeric loopback resolution only")
            return original_resolve(host, *args, **kwargs)

        with (
            patch.object(socket.socket, "connect", connect),
            patch.object(socket.socket, "connect_ex", connect_ex),
            patch.object(socket.socket, "bind", bind),
            patch.object(socket, "getaddrinfo", resolve),
        ):
            await lifecycle.execute()


def _persist_and_sign(lifecycle: Lifecycle, directory: Path) -> dict[str, Any]:
    audit_query = lifecycle.persist()
    cache_state = lifecycle.cache_state()
    signed_status, signed_digest, signature_failure = "unavailable", None, None
    lifecycle.measurements.update(sign_verify_ns=0, audit_hash_ns=0)
    if lifecycle.config["evidence"] != "none":
        path = directory / "audit.sqlite"
        sizes = {"final_main_bytes": path.stat().st_size}
        for suffix in ("wal", "shm"):
            sidecar = Path(str(path) + f"-{suffix}")
            sizes[f"final_{suffix}_bytes"] = sidecar.stat().st_size if sidecar.exists() else 0
        audit_query.update(**sizes, final_physical_bytes=sum(sizes.values()))
        started = perf_counter_ns()
        signed_digest = content_sha256(path.read_bytes())
        lifecycle.measurements["audit_hash_ns"] = perf_counter_ns() - started
        started = perf_counter_ns()
        try:
            signed_status = importlib.import_module(
                "aletheia_lab.evaluation.litserve_evidence_provenance"
            ).sign_bundle(signed_digest, directory / "provenance")
        except Exception as exc:
            signed_status, signature_failure = "failed", type(exc).__name__
        lifecycle.measurements["sign_verify_ns"] = perf_counter_ns() - started
    return {
        "audit_query": audit_query,
        "cache_state": cache_state,
        "signed_status": signed_status,
        "signed_digest": signed_digest,
        "signature_failure": signature_failure,
    }


def _build_source(
    lifecycle: Lifecycle,
    packages: dict[str, str],
    terminal: str,
    failure: str | None,
    service: dict[str, Any],
) -> dict[str, Any]:
    lifecycle.measurements["latencies"] = [
        {
            "offered_index": row["offered_index"],
            "phase": row["phase"],
            "elapsed_ns": row["elapsed_ns"],
        }
        for row in lifecycle.rows
        if row["route"] == "/infer"
    ]
    return {
        "schema": "owned-cache-lifecycle/v1",
        "config": lifecycle.config,
        "environment": {"python": sys.version.split()[0], "packages": packages, "pid": os.getpid()},
        "artifacts": lifecycle.artifacts,
        "loads": lifecycle.loads,
        "rows": lifecycle.rows,
        "events": lifecycle.events,
        "measurements": lifecycle.measurements,
        **service,
        "terminal": terminal,
        "failure": failure,
        "scope": "application-owned affine reload, native cachetools memoization",
        "cost_boundary": "additional audit service over always-instrumented raw reference; disjoint timing buckets; not total observer overhead",
        "planned_inferences": 72,
        "planned_reloads": 2,
    }


def run_cell(config: dict[str, Any], directory: Path) -> dict[str, Any]:
    """Execute one fixed, fresh, opt-in CPU-local cell and preserve its raw census."""
    if (
        config.get("arm") not in ARMS
        or config.get("order") not in ORDERS
        or config.get("evidence") not in EVIDENCE
        or config.get("steady_requests") != 64
    ):
        raise ValueError("fixed owned lifecycle configuration required")
    if directory.exists() or directory.is_symlink():
        raise ValueError("fresh owned cell directory required")
    directory.mkdir(parents=True)
    packages = {name: version(name) for name in ("aiohttp", "cachetools")}
    if packages["cachetools"] != "6.2.6":
        raise ValueError("native cachetools version differs")
    wall_started, cpu_started = perf_counter_ns(), process_time_ns()
    lifecycle = Lifecycle(config, directory)
    terminal, failure = "complete", None
    try:
        asyncio.run(_execute_guarded(lifecycle))
    except Exception as exc:
        terminal, failure = "failed", type(exc).__name__
    service = _persist_and_sign(lifecycle, directory)
    lifecycle.measurements.update(
        cpu_wall_ns=perf_counter_ns() - wall_started, process_cpu_ns=process_time_ns() - cpu_started
    )
    source = _build_source(lifecycle, packages, terminal, failure, service)
    write_new_file(directory / "source.json", _encode(source))
    return source
