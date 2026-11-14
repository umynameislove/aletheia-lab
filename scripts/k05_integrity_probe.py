"""Bounded real-P3 local integrity scaling self-check; synthetic data only.

Fixture setup writes real object files/index rows directly, without per-file
fsync. Runtime durability/integrity settings remain unchanged. No SQL/binds are
emitted. First reads use uncontrolled OS/SQLite caches, never a claimed cold run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from aletheia_lab.project.lineage import build_lineage_graph, build_lineage_node
from aletheia_lab.project.persistence import ProjectStore, ProjectStoreError


class BudgetExpired(RuntimeError):
    pass


class MeasuredStore(ProjectStore):
    def __init__(self, root: Path, deadline: float):
        self.object_reads = self.bytes_verified = self.query_count = 0
        self.deadline = deadline
        super().__init__(root)
        self._connection.set_trace_callback(self._count_query)

    def _count_query(self, _statement: str) -> None:
        self.query_count += 1

    def read_object(self, digest: str) -> bytes:
        if time.monotonic() >= self.deadline:
            raise BudgetExpired("Local integrity probe duration exhausted")
        self.object_reads += 1
        payload = super().read_object(digest)
        self.bytes_verified += len(payload)
        return payload

    def reset(self) -> None:
        self.object_reads = self.bytes_verified = self.query_count = 0

    def counters(self) -> dict[str, int]:
        return {"objects_read": self.object_reads, "bytes_verified": self.bytes_verified,
                "sqlite_statements": self.query_count}


def _cluster_bytes(path: Path) -> int:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        sectors, bytes_per_sector = wintypes.DWORD(), wintypes.DWORD()
        free, total = wintypes.DWORD(), wintypes.DWORD()
        succeeded = ctypes.windll.kernel32.GetDiskFreeSpaceW(
            str(path.anchor), ctypes.byref(sectors), ctypes.byref(bytes_per_sector),
            ctypes.byref(free), ctypes.byref(total))
        if not succeeded:
            raise OSError("Cannot establish local fixture allocation bound")
        return sectors.value * bytes_per_sector.value
    return os.statvfs(path).f_frsize


def _footprint(root: Path, cluster: int) -> dict[str, int]:
    # Fixture writes already established each file's exact size. Count the real
    # index and fixed metadata files instead of walking100k names for telemetry.
    with closing(sqlite3.connect((root / "project-store.sqlite3").as_uri() + "?mode=ro", uri=True)) as conn:
        files, logical = conn.execute("SELECT count(*),sum(byte_size) FROM objects").fetchone()
    allocated = files * cluster
    allocated += (len(tuple((root / "objects" / "sha256").iterdir())) + 2) * cluster
    for candidate in root.iterdir():
        if candidate.is_file():
            size = candidate.stat().st_size
            logical += size
            allocated += ((size + cluster - 1) // cluster) * cluster
            files += 1
    return {"files": files, "logical_bytes": logical,
            "allocation_upper_estimate_bytes": allocated, "allocation_unit_bytes": cluster}


def _graph():
    project = "p3-project-" + "1" * 64
    node = build_lineage_node(project_id=project, kind="project", source_id=project,
                              source_sha256="1" * 64, visibility="public")
    return build_lineage_graph(project, (node,), ())


def _sample(root: Path, graph, unrelated: int, deadline: float) -> dict[str, object]:
    opened_at = time.perf_counter()
    with MeasuredStore(root, deadline) as store:
        assert store.object_reads == 0, "Opening the store hashed an unrelated object"
        output: dict[str, object] = {"unrelated_indexed_objects": unrelated,
                                    "open_ms": (time.perf_counter() - opened_at) * 1000}
        reference = None
        for cache in ("uncontrolled_first", "warm"):
            store.reset()
            started = time.perf_counter()
            assert store.load(graph.graph_id) == graph
            counters = store.counters()
            assert counters["objects_read"] == 1
            if reference is None:
                reference = counters
            else:
                assert counters == reference
            output[cache] = {**counters, "elapsed_ms": (time.perf_counter() - started) * 1000}
        if unrelated:
            # Corrupt the first unrelated digest so detection does not require
            # a second full scan; clean scrub above proved complete coverage.
            digest = str(store._connection.execute(
                "SELECT sha256 FROM objects WHERE sha256 NOT IN "
                "(SELECT object_sha256 FROM records) ORDER BY sha256 LIMIT 1").fetchone()[0])
            candidate = store._object_path(digest)
            original = candidate.read_bytes()
            try:
                candidate.write_bytes(b"synthetic corruption")
                store.reset()
                assert store.load(graph.graph_id) == graph
                assert store.counters() == reference
                output["healthy_scope_after_unrelated_corruption"] = store.counters()
                store.reset()
                try:
                    store.verify_integrity()
                except ProjectStoreError:
                    output["corruption_detected_by_scrub"] = True
                    output["corrupt_scrub_until_detection"] = store.counters()
                else:
                    raise AssertionError("Full scrub did not detect unrelated corruption")
            finally:
                candidate.write_bytes(original)
        store.reset()
        started = time.perf_counter()
        try:
            store.verify_integrity()
            assert store.object_reads == unrelated + 2
            scrub_complete = True
        except BudgetExpired:
            scrub_complete = False
        output["clean_full_scrub"] = {**store.counters(), "completed": scrub_complete,
                                      "elapsed_ms": (time.perf_counter() - started) * 1000}
        return output


def execute(args: argparse.Namespace) -> dict[str, object]:
    started = time.monotonic()
    deadline = started + args.max_seconds
    parent = args.work_dir.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    output: dict[str, object] = {"format": "k05-local-integrity-probe/v1",
        "requested_max_unrelated": args.max_unrelated, "cache": "uncontrolled-first and warm; no eviction",
        "fixture": "real indexed object files;8 bounded setup threads,no per-file fsync",
        "scope": "local P3 only; not hosted PostgreSQL, restore, provider or capacity evidence"}
    samples = []
    completed = True
    with tempfile.TemporaryDirectory(prefix="k05-integrity-", dir=parent) as temporary:
        root = Path(temporary).resolve()
        assert root.parent == parent and root.name.startswith("k05-integrity-")
        cluster = _cluster_bytes(root)
        # Reserve up to64MiB for SQLite/index/WAL/directory overhead in addition
        # to one allocation unit per tiny unrelated file.
        if args.max_unrelated * cluster + 64 * 1024 * 1024 > args.max_bytes:
            raise ValueError("Requested object count exceeds fixture allocation budget")
        graph = _graph()
        with ProjectStore(root) as store:
            store.persist((graph,))
        count = 0
        targets = sorted({0, *(number for number in (100, 1000, args.max_unrelated)
                              if number <= args.max_unrelated)})
        def fixture(number: int) -> tuple[str, int, str, str]:
            payload = f"k05-synthetic-unrelated-object/v1:{number:06d}\n".encode("ascii")
            digest = hashlib.sha256(payload).hexdigest()
            path = root / "objects" / "sha256" / digest[:2] / digest[2:]
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(payload)
            return digest, len(payload), "application/octet-stream", path.relative_to(root).as_posix()

        for target in targets:
            with ProjectStore(root) as setup, ThreadPoolExecutor(max_workers=8) as writers:
                while count < target:
                    if time.monotonic() >= deadline - min(30, args.max_seconds / 4):
                        completed = False
                        break
                    batch = list(writers.map(fixture, range(count, min(target, count + 1000))))
                    setup._connection.executemany("INSERT INTO objects VALUES(?,?,?,?)", batch)
                    setup._connection.commit()
                    count += len(batch)
            try:
                sample = _sample(root, graph, count, deadline)
                sample["requested_tier"] = target
                samples.append(sample)
                if not sample["clean_full_scrub"]["completed"]:
                    completed = False
            except BudgetExpired:
                completed = False
                output["incomplete_tier"] = count
                break
            if not completed:
                break
        footprint = _footprint(root, cluster)
        assert footprint["allocation_upper_estimate_bytes"] <= args.max_bytes
        output.update({"samples": samples, "achieved_unrelated": count,
                       "completed_requested_counts": completed and count == args.max_unrelated,
                       "footprint_before_cleanup": footprint,
                       "elapsed_seconds_before_cleanup": time.monotonic() - started,
                       "completed_hotpath_counts": count == args.max_unrelated,
                       "owned_fixture_removed": False})
        if args.output:
            args.output.write_text(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                                   encoding="utf-8")
    output["owned_fixture_removed"] = not root.exists()
    output["elapsed_seconds_including_cleanup"] = time.monotonic() - started
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-unrelated", type=int, default=1000)
    parser.add_argument("--max-seconds", type=int, default=120)
    parser.add_argument("--max-bytes", type=int, default=500 * 1024 * 1024)
    parser.add_argument("--work-dir", type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not 0 <= args.max_unrelated <= 100000 or not 10 <= args.max_seconds <= 1800:
        parser.error("Use 0..100000 objects and 10..1800 seconds")
    result = execute(args)
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["completed_requested_counts"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
