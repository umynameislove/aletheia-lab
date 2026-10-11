"""Three binary SQLite representations with one durable audit service contract.

Static amortizes generation records; compact additionally deduplicates operand
blobs; full keeps each captured generation with the complete request. No arm
duplicates the model or has free recovery unavailable to another arm.
"""

from __future__ import annotations

import copy
import json
import os
import sqlite3
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import fsync_directory_tree, write_new_file
from aletheia_lab.project.identity import content_sha256

ARMS = ("static", "compact", "full")
Record = tuple[dict[str, Any], dict[str, Any], bytes, bytes]


def _check_vectors(record: dict[str, Any], operand: bytes, output: bytes) -> None:
    if (
        content_sha256(operand) != record["input_sha256"]
        or content_sha256(output) != record["output_sha256"]
    ):
        raise ValueError("binary evidence digest mismatch")


class Store:
    def __init__(self, directory: Path, arm: str) -> None:
        if arm not in ARMS or directory.is_symlink():
            raise ValueError("unknown arm or unsafe store directory")
        directory.mkdir(parents=True, exist_ok=False)
        self.directory, self.arm = directory, arm
        self.db = sqlite3.connect(directory / "receipts.sqlite3")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE receipts (request_id TEXT PRIMARY KEY, generation_id TEXT NOT NULL, generation_payload TEXT, record_payload TEXT NOT NULL, input_payload BLOB, output_payload BLOB)"
        )
        self.db.execute("CREATE INDEX generation_lookup ON receipts(generation_id)")
        self.db.execute(
            "CREATE TABLE generations (generation_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        self.db.execute("CREATE TABLE blobs (digest TEXT PRIMARY KEY, payload BLOB NOT NULL)")
        self.db.commit()
        write_new_file(directory / "representation.json", encode({"arm": arm}).encode())
        fsync_directory_tree(directory)
        self.pending: list[Record] = []

    def add(
        self, generation: dict[str, Any], record: dict[str, Any], operand: bytes, output: bytes
    ) -> None:
        if generation["generation_id"] != record["generation_id"]:
            raise ValueError("generation/request association inconsistent")
        _check_vectors(record, operand, output)
        self.pending.append((copy.deepcopy(generation), copy.deepcopy(record), operand, output))

    def _generation(self, generation: dict[str, Any]) -> str | None:
        key, payload = generation["generation_id"], encode(generation)
        if self.arm == "full":
            previous = self.db.execute(
                "SELECT generation_payload FROM receipts WHERE generation_id=? LIMIT 1", (key,)
            ).fetchone()
            if previous is not None and previous[0] != payload:
                raise ValueError("conflicting immutable generation payload")
            return payload
        previous = self.db.execute(
            "SELECT payload FROM generations WHERE generation_id=?", (key,)
        ).fetchone()
        if previous is not None and previous[0] != payload:
            raise ValueError("conflicting immutable generation payload")
        self.db.execute("INSERT OR IGNORE INTO generations VALUES (?, ?)", (key, payload))
        return None

    def _blob(self, key: str, raw: bytes) -> None:
        previous = self.db.execute("SELECT payload FROM blobs WHERE digest=?", (key,)).fetchone()
        if previous is not None and previous[0] != raw:
            raise ValueError("conflicting immutable content digest")
        self.db.execute("INSERT OR IGNORE INTO blobs VALUES (?, ?)", (key, raw))

    def _insert(self, captured: Record) -> None:
        generation, record, operand, output = captured
        gen_json = self._generation(generation)
        if self.arm == "compact":
            self._blob(record["input_sha256"], operand)
            self._blob(record["output_sha256"], output)
            input_payload: bytes | None = None
            output_payload: bytes | None = None
        else:
            input_payload, output_payload = operand, output
        self.db.execute(
            "INSERT INTO receipts VALUES (?, ?, ?, ?, ?, ?)",
            (
                record["request_id"],
                generation["generation_id"],
                gen_json,
                encode(record),
                input_payload,
                output_payload,
            ),
        )

    def flush(self, *, fail_after_commit: bool = False) -> dict[str, int]:
        if not self.pending:
            return {}
        pending, self.pending = self.pending, []
        try:
            for captured in pending:
                self._insert(captured)
            self.db.commit()
        except (sqlite3.Error, ValueError):
            self.db.rollback()
            raise
        commit_ns = perf_counter_ns()
        if fail_after_commit:
            raise RuntimeError("injected interruption after commit before ACK")
        request_ids: list[str] = [item[1]["request_id"] for item in pending]
        row = {
            "request_ids": request_ids,
            "database_commit_return_ns": commit_ns,
            "timestamp_semantics": "commit returned before journal write, not journal fsync time",
        }
        with (self.directory / "acks.jsonl").open("ab") as handle:
            handle.write((encode(row) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())
        fsync_directory_tree(self.directory)
        return dict.fromkeys(request_ids, commit_ns)

    def close(self) -> None:
        if self.pending:
            self.pending.clear()  # Unacknowledged captures are never persisted as successes.
        self.db.close()


def read_ack(directory: Path) -> dict[str, int]:
    path = directory / "acks.jsonl"
    if not path.is_file():
        return {}
    result: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if (
            set(row) != {"request_ids", "database_commit_return_ns", "timestamp_semantics"}
            or type(row["database_commit_return_ns"]) is not int
            or row["database_commit_return_ns"] <= 0
            or type(row["request_ids"]) is not list
            or not row["request_ids"]
            or any(not isinstance(key, str) or not key for key in row["request_ids"])
        ):
            raise ValueError("invalid ACK schema")
        for request_id in row["request_ids"]:
            if request_id in result:
                raise ValueError("duplicate ACK association")
            result[request_id] = row["database_commit_return_ns"]
    return result


def _read_row(db: sqlite3.Connection, arm: str, raw: tuple[Any, ...]) -> Record:
    request_id, generation_id, gen_json, row_json, operand, output = raw
    record = json.loads(row_json)
    if request_id != record["request_id"] or generation_id != record["generation_id"]:
        raise ValueError("receipt index/record association inconsistent")
    if arm != "full":
        found = db.execute(
            "SELECT payload FROM generations WHERE generation_id=?", (generation_id,)
        ).fetchone()
        if found is None:
            raise ValueError("missing generation association")
        gen_json = found[0]
    if gen_json is None:
        raise ValueError("missing generation payload")
    generation = json.loads(gen_json)
    if generation["generation_id"] != generation_id:
        raise ValueError("generation association inconsistent")
    if arm == "compact":
        found_input = db.execute(
            "SELECT payload FROM blobs WHERE digest=?", (record["input_sha256"],)
        ).fetchone()
        found_output = db.execute(
            "SELECT payload FROM blobs WHERE digest=?", (record["output_sha256"],)
        ).fetchone()
        if found_input is None or found_output is None:
            raise ValueError("missing binary evidence blob")
        operand, output = found_input[0], found_output[0]
    if not isinstance(operand, bytes) or not isinstance(output, bytes):
        raise ValueError("binary evidence absent")
    _check_vectors(record, operand, output)
    return generation, record, operand, output


def read_records(
    directory: Path, arm: str
) -> list[tuple[dict[str, Any], dict[str, Any], bytes, bytes, int | None]]:
    path = directory / "receipts.sqlite3"
    if arm not in ARMS or directory.is_symlink() or path.is_symlink():
        raise ValueError("unknown arm or unsafe store")
    if json.loads((directory / "representation.json").read_bytes()) != {"arm": arm}:
        raise ValueError("store representation differs")
    if Path(str(path) + "-wal").exists():
        raise ValueError("closed checkpointed store required")
    acks = read_ack(directory)
    db = sqlite3.connect(path.absolute().as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("SQLite integrity failure")
        rows = [
            _read_row(db, arm, row) for row in db.execute("SELECT * FROM receipts ORDER BY rowid")
        ]
        generations: dict[str, dict[str, Any]] = {}
        for generation, _, _, _ in rows:
            key = generation["generation_id"]
            if key in generations and generations[key] != generation:
                raise ValueError("conflicting retained generation payloads")
            generations[key] = generation
        return [(*row, acks.get(row[1]["request_id"])) for row in rows]
    finally:
        db.close()
