"""Two controlled local lifecycle producers, with actual loaders and observer IO.

Only artifacts created inside a fresh temporary workspace are deserialized.
These are authored schedules on SQLite/Joblib, not incidents from MLflow or a
deployed registry. Control truth never travels through the tested observer.
"""

from __future__ import annotations

import io
import json
import os
import queue
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import joblib  # type: ignore[import-untyped]
from sklearn.tree import DecisionTreeClassifier  # type: ignore[import-untyped]

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.model_load_contract import (
    LoadContract,
    Observation,
    Record,
    Scope,
)
from aletheia_lab.project.identity import content_sha256

Workflow = Literal["sqlite_queue", "file_cache"]
Fault = Literal["complete", "delay_selection", "drop_load", "expire_selection", "misjoin_load"]


@dataclass(frozen=True)
class Artifact:
    path: Path
    digest: str


def create_artifacts(workspace: Path) -> dict[str, Artifact]:
    """Fit tiny authored models, no historical dataset/model or external pickle."""
    artifacts = {}
    for name, labels in (("A", [0, 0, 1, 1]), ("B", [1, 1, 0, 0])):
        model = DecisionTreeClassifier(max_depth=1, random_state=1729).fit(
            [[0], [1], [2], [3]], labels
        )
        path = workspace / f"model-{name}.joblib"
        if path.exists() or path.is_symlink():
            raise FileExistsError("refusing to reuse a model artifact")
        written = joblib.dump(model, path, compress=3)
        if len(written) != 1 or Path(written[0]) != path:
            raise ValueError("artifact is not the supported single-file representation")
        artifacts[name] = Artifact(path, file_sha256(path))
    return artifacts


class Registry:
    """Actual SQLite snapshot selection or atomic persisted file-alias selection."""

    def __init__(self, workspace: Path, workflow: Workflow) -> None:
        self.workflow = workflow
        self.path = workspace / ("registry.sqlite" if workflow == "sqlite_queue" else "alias.json")
        self.native: list[str] = []
        self._lock = threading.Lock()
        if workflow == "sqlite_queue":
            connection = sqlite3.connect(self.path)
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute(
                    "CREATE TABLE registry(alias TEXT PRIMARY KEY, version TEXT, revision INTEGER)"
                )
                connection.execute("INSERT INTO registry VALUES('production', 'A', 0)")
                connection.commit()
            finally:
                connection.close()
        else:
            self.path.write_text(json.dumps({"version": "A", "revision": 0}), encoding="utf-8")

    def select(self, snapshot_hook: Any = None) -> tuple[str, int]:
        if self.workflow == "sqlite_queue":
            connection = sqlite3.connect(self.path)
            try:
                connection.execute("BEGIN DEFERRED")
                connection.set_trace_callback(self.native.append)
                row = connection.execute(
                    "SELECT version, revision FROM registry WHERE alias='production'"
                ).fetchone()
                connection.set_trace_callback(None)
                if snapshot_hook is not None:
                    snapshot_hook()
                connection.commit()
            finally:
                connection.close()
            if row is None:
                raise ValueError("registry did not select a version")
            return str(row[0]), int(row[1])
        with self._lock:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        self.native.append("read current production alias")
        if snapshot_hook is not None:
            snapshot_hook()
        return str(value["version"]), int(value["revision"])

    def update(self) -> None:
        if self.workflow == "sqlite_queue":
            connection = sqlite3.connect(self.path)
            try:
                connection.execute("UPDATE registry SET version='B', revision=revision+1")
                connection.commit()
            finally:
                connection.close()
        else:
            with self._lock:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                replacement = self.path.with_suffix(".next.json")
                replacement.write_text(
                    json.dumps({"version": "B", "revision": value["revision"] + 1}),
                    encoding="utf-8",
                )
                os.replace(replacement, self.path)
        self.native.append("production alias updated")


class Transport:
    """Persistent observer spool; faults act at submission/delivery/retention.

    A paused delivery queue is released explicitly, expiry uses monotonic time,
    and a misjoin changes the mailbox, NOT the record's true attempt identity.
    Evidence is never produced by deleting fields from a completed truth ledger.
    """

    def __init__(self, path: Path, fault: Fault, target: Scope) -> None:
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.fault, self.target = fault, target
        self.counts = {"submitted": 0, "dropped": 0, "delayed": 0, "expired": 0, "misjoined": 0}
        self.connection.execute(
            "CREATE TABLE spool(route INTEGER, blocked INTEGER, expiry INTEGER, payload TEXT)"
        )

    def submit(self, record: Record) -> None:
        with self.lock:
            self.counts["submitted"] += 1
            affected = record.scope == self.target
            if affected and self.fault == "drop_load" and record.kind == "load":
                self.counts["dropped"] += 1
                return
            blocked = int(self.fault == "delay_selection" and record.kind == "selection")
            expiry = (
                time.monotonic_ns()
                if self.fault == "expire_selection" and record.kind == "selection"
                else None
            )
            route = record.scope.attempt
            if affected and self.fault == "misjoin_load" and record.kind == "load":
                route = max(0, route - 1)
                self.counts["misjoined"] += 1
            self.counts["delayed"] += blocked
            self.connection.execute(
                "INSERT INTO spool VALUES (?, ?, ?, ?)",
                (route, blocked, expiry, json.dumps(asdict(record), sort_keys=True)),
            )
            self.connection.commit()

    def read(self, scope: Scope, *, release: bool = False) -> tuple[Record, ...]:
        with self.lock:
            expired = self.connection.execute(
                "SELECT count(*) FROM spool WHERE expiry <= ?", (time.monotonic_ns(),)
            ).fetchone()
            if expired is None:
                raise RuntimeError("observer retention count is unavailable")
            self.counts["expired"] += int(expired[0])
            self.connection.execute("DELETE FROM spool WHERE expiry <= ?", (time.monotonic_ns(),))
            if release:
                self.connection.execute("UPDATE spool SET blocked=0")
            rows = self.connection.execute(
                "SELECT payload FROM spool WHERE route=? AND blocked=0", (scope.attempt,)
            ).fetchall()
            parents = (
                self.connection.execute(
                    "SELECT payload FROM spool WHERE route=0 AND blocked=0"
                ).fetchall()
                if scope.attempt
                else []
            )
            self.connection.commit()
        records = [_decode_record(json.loads(row[0])) for row in rows]
        records.extend(
            record
            for row in parents
            if (record := _decode_record(json.loads(row[0]))).kind == "selection"
        )
        return tuple(records)

    def close(self) -> None:
        self.connection.close()


def _decode_record(value: dict[str, Any]) -> Record:
    scope = Scope(**value.pop("scope"))
    parent = value.pop("parent_scope")
    return Record(scope=scope, parent_scope=Scope(**parent) if parent else None, **value)


@dataclass(frozen=True)
class Episode:
    name: str
    policy: Literal["pin_at_acceptance", "resolve_at_load"]
    retry: Literal["inherit", "reselect"] = "inherit"
    fault: Fault = "complete"
    overlap: bool = False
    cached: bool = False
    use_current_instead_of_pin: bool = False
    second_attempt: bool = False


def schedules(workflow: Workflow) -> tuple[Episode, ...]:
    retry_case = (
        Episode(
            "retry_misjoin",
            "pin_at_acceptance",
            second_attempt=True,
            use_current_instead_of_pin=True,
            fault="misjoin_load",
        )
        if workflow == "sqlite_queue"
        else Episode("retry_reselect", "pin_at_acceptance", retry="reselect", second_attempt=True)
    )
    missing_case = Episode(
        "retry_lost_witness" if workflow == "sqlite_queue" else "retry_expired_selection",
        "pin_at_acceptance",
        second_attempt=True,
        use_current_instead_of_pin=True,
        fault="drop_load" if workflow == "sqlite_queue" else "expire_selection",
    )
    return (
        Episode("queued_pin_violation", "pin_at_acceptance", use_current_instead_of_pin=True),
        Episode("queued_late_legal", "resolve_at_load"),
        Episode("overlap_delayed", "resolve_at_load", overlap=True, fault="delay_selection"),
        Episode("cached_reuse", "resolve_at_load", cached=True),
        missing_case,
        retry_case,
    )


def run_episode(
    workspace: Path, workflow: Workflow, episode: Episode, artifacts: dict[str, Artifact]
) -> dict[str, Any]:
    """Execute authored schedule through a queue; no outcomes select the schedule."""
    scope = Scope(f"{workflow}:{episode.name}", int(episode.second_attempt))
    registry = Registry(workspace, workflow)
    transport = Transport(workspace / "observer.sqlite", episode.fault, scope)
    contract = LoadContract(
        episode.policy, tuple(item.digest for item in artifacts.values()), episode.retry
    )
    accepted = registry.select()
    initial_scope = Scope(scope.request, 0)
    parent = _selection_record(initial_scope, accepted, artifacts, episode.policy)
    if episode.policy == "pin_at_acceptance":
        transport.submit(parent)
    truth: dict[str, Any] = {
        "accepted_snapshot": list(accepted),
        "policy": asdict(contract),
        "selection_snapshot": None,
        "raw_loader_buffers": [],
        "closed": False,
        "deserializer_invocations": 0,
        "cache_origin_digest": None,
    }
    jobs: queue.Queue[Scope] = queue.Queue(maxsize=1)
    start = threading.Event()
    snapshot_taken, writer_done, first_finished = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    failures: list[BaseException] = []
    jobs.put(initial_scope)
    cache = _warm_cache(artifacts["A"]) if episode.cached else None
    if cache is not None:
        truth["cache_origin_digest"] = artifacts["A"].digest

    def target() -> None:
        _worker(
            jobs,
            start,
            registry,
            transport,
            artifacts,
            episode,
            accepted,
            parent,
            truth,
            cache,
            snapshot_taken,
            writer_done,
            first_finished,
            failures,
        )

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    try:
        if episode.second_attempt:
            start.set()
            if not first_finished.wait(5):
                raise TimeoutError("initial attempt did not reach retry boundary")
            registry.update()
            start.set()
        elif episode.overlap:
            start.set()
            if not snapshot_taken.wait(5):
                raise TimeoutError("snapshot boundary was not reached")
            registry.update()
            writer_done.set()
        else:
            registry.update()
            start.set()
        thread.join(5)
        if thread.is_alive():
            raise TimeoutError("local worker failed to terminate")
        if failures:
            raise RuntimeError("local worker failed") from failures[0]
        before = Observation(contract, scope, transport.read(scope))
        after = Observation(contract, scope, transport.read(scope, release=True))
        current = registry.select()
        native = Observation(
            contract,
            scope,
            (
                Record(
                    "native-registry",
                    scope,
                    "registry",
                    digest=artifacts[current[0]].digest,
                    revision=current[1],
                ),
            ),
        )
        return {
            "workflow": workflow,
            "episode": episode.name,
            "scope": asdict(scope),
            "native_messages": list(registry.native),
            "truth": truth,
            "observations": {"native": native, "before": before, "after": after},
            "transport": dict(transport.counts),
        }
    finally:
        start.set()
        writer_done.set()
        thread.join(5)
        transport.close()


def _selection_record(
    scope: Scope,
    snapshot: tuple[str, int],
    artifacts: dict[str, Artifact],
    phase: str,
    parent: Record | None = None,
) -> Record:
    return Record(
        f"{scope.attempt}:selection",
        scope,
        "selection",
        digest=artifacts[snapshot[0]].digest,
        selection=f"{scope.request}:{scope.attempt}:selection",
        revision=snapshot[1],
        phase=phase,
        parent_scope=parent.scope if parent else None,
        parent_selection=parent.selection if parent else None,
    )


def _warm_cache(artifact: Artifact) -> Any:
    buffer = io.BytesIO(artifact.path.read_bytes())
    if content_sha256(buffer.getvalue()) != artifact.digest:
        raise ValueError("trusted cache warmup artifact changed")
    return joblib.load(buffer, mmap_mode=None)


def _worker(
    jobs: queue.Queue[Scope],
    start: threading.Event,
    registry: Registry,
    transport: Transport,
    artifacts: dict[str, Artifact],
    episode: Episode,
    accepted: tuple[str, int],
    parent: Record,
    truth: dict[str, Any],
    cache: Any,
    snapshot_taken: threading.Event,
    writer_done: threading.Event,
    first_finished: threading.Event,
    failures: list[BaseException],
) -> None:
    try:
        for index in range(2 if episode.second_attempt else 1):
            scope = jobs.get(timeout=5)
            if not start.wait(5):
                raise TimeoutError("worker start gate failed")
            first = episode.second_attempt and index == 0
            attempt_truth: dict[str, Any] = (
                {"raw_loader_buffers": [], "deserializer_invocations": 0} if first else truth
            )
            _execute_job(
                scope,
                registry,
                transport,
                artifacts,
                episode,
                accepted,
                parent,
                attempt_truth,
                cache,
                snapshot_taken,
                writer_done,
            )
            attempt_truth["closed"] = True
            transport.submit(
                Record(
                    f"{scope.attempt}:closed",
                    scope,
                    "closure",
                    load_count=attempt_truth["deserializer_invocations"],
                )
            )
            jobs.task_done()
            if first:
                attempt_truth["terminal"] = "injected_post_load_retry"
                truth["predecessor_attempt"] = attempt_truth
                start.clear()
                jobs.put(Scope(scope.request, 1))
                registry.native.append("retry queued after transient post-load failure")
                first_finished.set()
    except BaseException as exc:
        failures.append(exc)


def _execute_job(
    scope: Scope,
    registry: Registry,
    transport: Transport,
    artifacts: dict[str, Artifact],
    episode: Episode,
    accepted: tuple[str, int],
    parent: Record,
    truth: dict[str, Any],
    cache: Any,
    snapshot_taken: threading.Event,
    writer_done: threading.Event,
) -> None:
    if cache is not None:
        transport.submit(Record("cache", scope, "cache_hit", digest=artifacts["A"].digest))
        truth["cache_prediction"] = cache.predict([[0], [3]]).tolist()
        return
    snapshot = registry.select(
        lambda: _snapshot_gate(snapshot_taken, writer_done) if episode.overlap else None
    )
    truth["selection_snapshot"] = list(snapshot)
    selected = _selected_version(scope, episode, accepted, snapshot)
    phase = episode.retry if scope.attempt else episode.policy
    inherited = parent if scope.attempt and episode.retry == "inherit" else None
    selection = _selection_record(scope, selected, artifacts, phase, inherited)
    transport.submit(selection)
    consumed = snapshot[0] if episode.use_current_instead_of_pin else selected[0]
    _deserialize(scope, consumed, selection, artifacts, transport, truth)


def _snapshot_gate(snapshot_taken: threading.Event, writer_done: threading.Event) -> None:
    snapshot_taken.set()
    if not writer_done.wait(5):
        raise TimeoutError("overlap writer gate failed")


def _selected_version(
    scope: Scope, episode: Episode, accepted: tuple[str, int], snapshot: tuple[str, int]
) -> tuple[str, int]:
    if scope.attempt:
        return accepted if episode.retry == "inherit" else snapshot
    return accepted if episode.policy == "pin_at_acceptance" else snapshot


def _deserialize(
    scope: Scope,
    consumed: str,
    selection: Record,
    artifacts: dict[str, Artifact],
    transport: Transport,
    truth: dict[str, Any],
) -> None:
    raw = artifacts[consumed].path.read_bytes()
    if content_sha256(raw) != artifacts[consumed].digest:
        raise ValueError("local trusted artifact changed outside the schedule")
    buffer = io.BytesIO(raw)
    # Private reference retains independently rehashable actual input bytes.
    truth["raw_loader_buffers"].append(buffer.getvalue().hex())
    truth["deserializer_invocations"] += 1
    transport.submit(
        Record(
            f"{scope.attempt}:actual-load",
            scope,
            "load",
            digest=content_sha256(buffer.getvalue()),
            selection=selection.selection,
        )
    )
    loaded = joblib.load(buffer, mmap_mode=None)
    truth["prediction"] = loaded.predict([[0], [3]]).tolist()
