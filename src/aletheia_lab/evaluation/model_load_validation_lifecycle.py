"""Frozen local lifecycle execution; reference and observer are separate sinks."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.model_load_contract import LoadContract, Observation, Record, Scope
from aletheia_lab.evaluation.model_load_provenance import document_digest
from aletheia_lab.project.identity import content_sha256


class BoundedCapture:
    """Drain an OS pipe without unbounded disk or memory, retaining overflow as failure."""

    def __init__(self, limit: int) -> None:
        self.reader, self.writer = os.pipe()
        self.limit = limit
        self.data = bytearray()
        self.exceeded = threading.Event()
        self.failed = threading.Event()
        self.writer_open = True
        self.thread = threading.Thread(target=self._drain, daemon=True)
        self.thread.start()

    def _drain(self) -> None:
        try:
            while raw := os.read(self.reader, 32768):
                remaining = self.limit - len(self.data)
                self.data.extend(raw[:remaining])
                if len(raw) > remaining:
                    self.exceeded.set()
        except OSError:
            self.failed.set()
        finally:
            os.close(self.reader)

    def close_writer(self) -> None:
        if self.writer_open:
            os.close(self.writer)
            self.writer_open = False

    def finish(self) -> None:
        self.close_writer()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise ValueError("output pipe did not close within its bounded shutdown")
        if self.failed.is_set():
            raise ValueError("output capture failed before complete drain")


def finish_captures(*captures: BoundedCapture) -> None:
    failure: ValueError | None = None
    for capture in captures:
        try:
            capture.finish()
        except ValueError as exc:
            failure = exc
    if failure is not None:
        raise failure


@contextmanager
def native_output(directory: Path) -> Iterator[dict[str, str]]:
    """Capture OS descriptors as well as Python output in this isolated worker."""
    output: dict[str, str] = {}
    saved = [os.dup(fd) for fd in (1, 2)]
    captures = [(name, BoundedCapture(262144)) for name in ("stdout", "stderr")]
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        for fd, (_, capture) in zip((1, 2), captures, strict=True):
            os.dup2(capture.writer, fd)
            capture.close_writer()
        try:
            yield output
        finally:
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            finally:
                for fd, original in zip((1, 2), saved, strict=True):
                    os.dup2(original, fd)
                finish_captures(*(capture for _, capture in captures))
            if any(capture.exceeded.is_set() for _, capture in captures):
                raise ValueError("native output exceeds the per-slot result allowance")
            for name, capture in captures:
                output[name] = capture.data.decode("utf-8")
    finally:
        for _, capture in captures:
            capture.close_writer()
        for original in saved:
            os.close(original)


class PreventionBlocked(RuntimeError):
    """A rejected invocation has not crossed the native loader boundary."""


class Observer:
    """Actual SQLite delivery/expiry/misrouting, never a mask of reference truth."""

    def __init__(self, path: Path, target: Scope, fault: str) -> None:
        self.db = sqlite3.connect(path)
        self.target, self.fault = target, fault
        self.db.execute("CREATE TABLE spool(mailbox TEXT, delayed INTEGER, payload TEXT)")
        self.counts = {name: 0 for name in ("submitted", "delayed", "foreign", "expired")}

    def submit(self, record: Record) -> None:
        self.counts["submitted"] += 1
        mailbox = record.scope.request
        delayed = int(
            self.fault == "delay" and record.scope == self.target and record.kind == "load"
        )
        if self.fault == "foreign" and record.scope == self.target and record.kind == "load":
            mailbox = "foreign-mailbox"
            self.counts["foreign"] += 1
        self.counts["delayed"] += delayed
        self.db.execute(
            "INSERT INTO spool VALUES (?, ?, ?)",
            (mailbox, delayed, json.dumps(asdict(record), sort_keys=True)),
        )
        self.db.commit()

    def expire_root(self) -> None:
        rows = self.db.execute("SELECT rowid, payload FROM spool").fetchall()
        for key, payload in rows:
            value = json.loads(payload)
            if value["scope"] == asdict(Scope(self.target.request, 0)):
                self.db.execute("DELETE FROM spool WHERE rowid=?", (key,))
                self.counts["expired"] += 1
        self.db.commit()

    def read(self, contract: LoadContract, *, release: bool = False) -> dict[str, Any]:
        if release:
            self.db.execute("UPDATE spool SET delayed=0")
            self.db.commit()
        values = self.db.execute(
            "SELECT payload FROM spool WHERE mailbox=? AND delayed=0 ORDER BY rowid",
            (self.target.request,),
        ).fetchall()
        records = tuple(decode_record(json.loads(value[0])) for value in values)
        return asdict(Observation(contract, self.target, records))


def decode_record(value: dict[str, Any]) -> Record:
    fields = dict(value)
    scope = Scope(**fields.pop("scope"))
    parent = fields.pop("parent_scope")
    return Record(scope=scope, parent_scope=Scope(**parent) if parent else None, **fields)


def observation(value: dict[str, Any]) -> Observation:
    fields = {**value["contract"], "artifact_domain": tuple(value["contract"]["artifact_domain"])}
    return Observation(
        LoadContract(**fields), Scope(**value["scope"]), tuple(map(decode_record, value["records"]))
    )


class Guard:
    """Application binding plus an atomic one-use permit, not a reference query."""

    def __init__(self, scope: Scope, selected: Record, parent: Record) -> None:
        self.scope, self.selected, self.parent = scope, selected, parent
        self.used = False
        self.lock = threading.Lock()

    def permit(self, payload: bytes) -> None:
        expected = self.selected.digest
        if self.selected.phase == "inherit":
            if (
                self.selected.parent_scope != self.parent.scope
                or self.selected.parent_selection != self.parent.selection
                or self.parent.scope != Scope(self.scope.request, 0)
            ):
                raise PreventionBlocked("unavailable_root_binding")
            expected = self.parent.digest
        if self.selected.scope != self.scope or content_sha256(payload) != expected:
            raise PreventionBlocked("wrong_bound_buffer")
        with self.lock:
            if self.used:
                raise PreventionBlocked("one_invocation_token_spent")
            self.used = True


def selection(
    scope: Scope, digest: str, revision: int, phase: str, parent: Record | None
) -> Record:
    return Record(
        "selection:" + str(scope.attempt),
        scope,
        "selection",
        digest,
        f"{scope.request}:{scope.attempt}:{revision}",
        revision,
        phase,
        parent_scope=parent.scope if parent else None,
        parent_selection=parent.selection if parent else None,
    )


class EntryCapture:
    """Snapshot each actual entry before the observer can route or lose it."""

    def __init__(
        self,
        ledger: dict[str, Any],
        slot: dict[str, Any],
        schedule: dict[str, Any],
        observer: Observer,
        digests: dict[str, str],
        selected: Record,
        parent: Record,
    ) -> None:
        self.ledger, self.slot, self.schedule = ledger, slot, schedule
        self.observer, self.digests = observer, digests
        self.selected, self.parent = selected, parent
        self.scope = selected.scope
        self.target = False
        self.guard = Guard(self.scope, selected, parent)
        self.pending: list[dict[str, Any]] = []

    def _permit(self, raw: bytes) -> None:
        try:
            self.guard.permit(raw)
        except PreventionBlocked as exc:
            self.ledger["blocked"].append(
                {
                    "reason": str(exc),
                    "raw_hex": raw.hex(),
                    "scope": asdict(self.scope),
                    "prior_entries": len(self.ledger["target_entries"]),
                }
            )
            raise

    def before(self, raw: bytes) -> None:
        if type(raw) is not bytes or content_sha256(raw) not in self.digests.values():
            raise ValueError("native entry outside sealed buffer domain")
        if self.target and self.slot["branch"] == "prevention":
            self._permit(raw)
        key = "target_entries" if self.target else "auxiliary_entries"
        budget = (
            self.schedule["target_entries"] if self.target else self.schedule["auxiliary_entries"]
        )
        if len(self.ledger[key]) >= budget:
            raise ValueError("native entry exceeds frozen slot budget")
        scope = self.scope if self.target else self.parent.scope
        entry: dict[str, Any] = {
            "occurrence": f"native:{scope.attempt}:{len(self.ledger[key])}",
            "scope": asdict(scope),
            "raw_hex": raw.hex(),
            "sha256": content_sha256(raw),
            "completed": False,
        }
        self.ledger[key].append(entry)
        self.pending.append(entry)
        binding = self.selected if self.target else self.parent
        record = Record(entry["occurrence"], scope, "load", entry["sha256"], binding.selection)
        self.observer.submit(record)
        if self.target and self.schedule["id"] == "delivery-duplicate-only":
            self.observer.submit(record)

    def after(self, completed: bool) -> None:
        if not self.pending:
            raise ValueError("native completion without an entry")
        self.pending.pop()["completed"] = completed


def _target_load(
    adapter: Any,
    spool: Path,
    artifacts: dict[str, bytes],
    schedule: dict[str, Any],
    selected_name: str,
    ledger: dict[str, Any],
) -> None:
    loaded = selected_name
    if schedule["id"] in {
        "handoff-pinned-drift",
        "retry-inherited-foreign-mailbox",
        "retry-root-expired",
    }:
        loaded = "B"
    if schedule["id"] == "cache-reload-stale-buffer":
        loaded = "A"
    spool.write_bytes(artifacts[loaded])
    payload = spool.read_bytes()
    if schedule["id"] == "handoff-pinned-drift":
        spool.write_bytes(artifacts["A"])
    try:
        model = adapter.load(payload)
        ledger["sdk_completed_loads"] += 1
        if schedule["target_entries"] == 2:
            adapter.reenter(model, payload)
            ledger["sdk_completed_loads"] += 1
    except PreventionBlocked:
        pass


def run_slot(
    directory: Path,
    slot: dict[str, Any],
    schedule: dict[str, Any],
    artifacts: dict[str, bytes],
    backend_version: str,
    adapter_class: Any,
) -> dict[str, Any]:
    """Run one frozen schedule; adapter callbacks intercept actual native entries."""
    directory.mkdir(exist_ok=False)
    digests = {name: content_sha256(raw) for name, raw in artifacts.items()}
    scope = Scope("request-" + uuid.uuid4().hex, int(schedule["auxiliary_entries"] > 0))
    parent = selection(Scope(scope.request, 0), digests["A"], 1, schedule["policy"], None)
    contract = LoadContract(schedule["policy"], tuple(digests.values()), schedule["retry"])
    selected_name = "B" if schedule["policy"] == "resolve_at_load" else "A"
    if scope.attempt and schedule["retry"] == "inherit":
        selected_name = "A"
    selected = selection(
        scope,
        digests[selected_name],
        1 if selected_name == "A" else 2,
        schedule["retry"] if scope.attempt else schedule["policy"],
        parent if scope.attempt and schedule["retry"] == "inherit" else None,
    )
    fault = {
        "retry-inherited-foreign-mailbox": "foreign",
        "retry-reselect-legitimate": "delay",
    }.get(schedule["id"], "complete")
    observer = Observer(directory / "observer.sqlite", scope, fault)
    ledger: dict[str, Any] = {
        "contract": asdict(contract),
        "scope": asdict(scope),
        "root_binding": asdict(parent),
        "selection": asdict(selected),
        "target_entries": [],
        "auxiliary_entries": [],
        "blocked": [],
        "closed": False,
        "sdk_completed_loads": 0,
        "sdk_completed_auxiliary_loads": 0,
    }
    capture = EntryCapture(ledger, slot, schedule, observer, digests, selected, parent)
    adapter = adapter_class(slot["backend"], capture.before, capture.after)
    spool = directory / "model.buffer"
    spool.write_bytes(artifacts["A"])
    alias = directory / "alias.json"
    alias.write_text(json.dumps({"name": "A", "sha256": digests["A"]}), encoding="utf-8")
    row: dict[str, Any] = {"slot": slot, "status": "completed", "ledger": ledger}
    messages: dict[str, str] = {}
    handler = logging.StreamHandler(sys.stderr)
    logger = logging.getLogger(slot["backend"])
    logger.addHandler(handler)
    try:
        with native_output(directory) as messages:
            if schedule["auxiliary_entries"]:
                observer.submit(parent)
                cached_model = adapter.load(artifacts["A"])
                ledger["sdk_completed_auxiliary_loads"] += 1
                observer.submit(Record("parent-close", parent.scope, "closure", load_count=1))
            pre_raw = spool.read_bytes()
            pre = content_sha256(pre_raw)
            if schedule["policy"] == "resolve_at_load":
                alias.write_text(
                    json.dumps({"name": "B", "sha256": digests["B"]}), encoding="utf-8"
                )
                spool.write_bytes(artifacts["B"])
                resolved = json.loads(alias.read_text(encoding="utf-8"))
                if resolved["sha256"] != content_sha256(artifacts[resolved["name"]]):
                    raise ValueError("application alias binding mismatch")
                selected = selection(
                    scope,
                    resolved["sha256"],
                    2,
                    schedule["retry"] if scope.attempt else schedule["policy"],
                    None,
                )
                ledger["selection"] = asdict(selected)
                capture.selected = selected
                capture.guard = Guard(scope, selected, parent)
            manifest = json.dumps(
                {
                    "schema": "model-load-manifest/v1",
                    "selected_sha256": selected.digest,
                    "backend": slot["backend"],
                    "format": slot["api"],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            capture.target = True
            observer.submit(selected)
            if schedule["id"] == "retry-root-expired":
                observer.expire_root()
            if schedule["target_entries"]:
                _target_load(adapter, spool, artifacts, schedule, selected_name, ledger)
            else:
                if cached_model is None:
                    raise ValueError("cache-only child lacks warmed application object")
                observer.submit(Record("cache", scope, "cache_hit", digest=digests["A"]))
            observer.submit(
                Record("close", scope, "closure", load_count=len(ledger["target_entries"]))
            )
            ledger["closed"] = True
            row["observations"] = {
                "before": observer.read(contract),
                "after": observer.read(contract, release=True),
            }
            row["path_frame"] = {
                "backend": slot["backend"],
                "backend_version": backend_version,
                "policy": contract.policy,
                "retry_policy": contract.retry,
                "public_request_id": scope.request,
                "public_root_id": scope.request,
                "public_attempt": scope.attempt,
                "requested_uri": f"spool://{scope.request}/model",
                "selected_version": None,
                "registered_metadata": None,
                "manifest": {"text": manifest.decode(), "sha256": content_sha256(manifest)},
                "pre_path_sha256": pre,
                "post_path_sha256": content_sha256(spool.read_bytes()),
            }
            ledger["path_snapshots"] = {
                "pre_raw_hex": pre_raw.hex(),
                "post_raw_hex": spool.read_bytes().hex(),
            }
    except Exception as exc:
        row["status"] = "technical_failure"
        row["error_type"] = type(exc).__name__
    finally:
        logger.removeHandler(handler)
        observer.db.close()
    if "path_frame" in row:
        row["path_frame"]["native_messages"] = messages
        ledger["native_messages"] = dict(messages)
    row["transport"] = observer.counts
    row["row_sha256"] = document_digest(row)
    return row
